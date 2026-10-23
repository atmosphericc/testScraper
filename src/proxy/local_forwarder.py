"""
Local HTTP CONNECT proxy forwarder with upstream Basic-Auth.

Solves the Bright Data auth problem cleanly: Chrome / curl_cffi connect to
this local forwarder (no auth needed for 127.0.0.1), and the forwarder
opens upstream connections to BD's superproxy with the proper
`Proxy-Authorization: Basic <b64>` header injected into the CONNECT.

Architecture:
    [Chrome / curl_cffi] --(plain CONNECT)--> [127.0.0.1:PORT]
                                              |
                                              v
                                  [BD superproxy w/ basic auth]
                                              |
                                              v
                                       [target.com]

Multi-port mode: each upstream BD IP gets its own listening port. Workers
pick a port to determine which BD IP they exit from. Simple, no dynamic
routing logic, easy to debug.

Usage as a library:
    from src.proxy.local_forwarder import ForwarderPool
    pool = ForwarderPool()
    await pool.add_upstream("brd...zone-...-ip-X.X.X.X:pass", port=8080)
    await pool.start_all()
    # Chrome: --proxy-server=127.0.0.1:8080
    # curl_cffi: proxies={"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"}
"""

import asyncio
import os
import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

UPSTREAM_HOST_DEFAULT = "brd.superproxy.io"
UPSTREAM_PORT_DEFAULT = 33335
BUFFER_SIZE = 65536


def _tunnel_idle_s() -> float:
    """FWD_TUNNEL_IDLE_S seconds (0 / unset / bad = off, the pre-10-23 behaviour)."""
    try:
        v = float(os.environ.get("FWD_TUNNEL_IDLE_S", "0") or 0)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


@dataclass
class UpstreamStats:
    """Per-upstream connection stats for diagnostics."""
    pinned_ip: str
    port: int
    connections_total: int = 0
    connections_active: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    errors: int = 0
    last_error: Optional[str] = None
    last_activity: float = 0.0
    idle_closes: int = 0     # 2026-10-23: tunnels closed by FWD_TUNNEL_IDLE_S (a silently dropped upstream)


@dataclass
class Upstream:
    """One BD upstream IP + the local port that fronts it."""
    proxy_url: str       # http://user:pass@brd.superproxy.io:33335
    port: int            # local port (e.g. 8080)
    pinned_ip: str       # the BD-pinned exit IP, parsed from username
    upstream_host: str
    upstream_port: int
    user: str
    password: str
    auth_b64: str
    server: Optional[asyncio.AbstractServer] = None
    stats: UpstreamStats = field(default_factory=lambda: UpstreamStats("", 0))
    # 2026-10-14 FX-1014-EGRESS: a DIRECT listener (no upstream proxy) whose
    # outbound sockets bind to bind_ip -- one account's traffic leaves through a
    # second network adapter (a phone hotspot, EXP-1002-NET). fallback_home:
    # when the bind fails (adapter gone), dial unbound (home route) instead of
    # failing every request of that account; each fallback is counted.
    direct: bool = False
    bind_ip: str = ""
    fallback_home: bool = True
    fallbacks: int = 0
    connect_s: float = 4.0
    # FX-1014-EGRESS DNS: resolve through this server, queried FROM bind_ip, so
    # the account reaches the CDN edge its own network maps to (carts.target.com
    # is a DNS-steered Fastly name). '' = the OS resolver (the home line's).
    dns_server: str = ""
    dns_fallbacks: int = 0
    dns_cache: dict = field(default_factory=dict)


def dns_query_a(host: str, server: str, bind_ip: str = "", timeout: float = 2.0, port: int = 53):
    """Minimal DNS A lookup: ([ipv4, ...], ttl_s) for `host` asked of `server`
    over UDP from `bind_ip`. Follows nothing itself (the server recurses);
    CNAME answers are skipped, A records collected. Raises on any failure."""
    import random
    import socket
    import struct
    tid = random.randint(0, 0xFFFF)
    q = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    for label in host.rstrip(".").split("."):
        b = label.encode("idna")
        if not 0 < len(b) < 64:
            raise ValueError(f"bad label in {host!r}")
        q += bytes([len(b)]) + b
    q += b"\x00" + struct.pack(">HH", 1, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        if bind_ip:
            sock.bind((bind_ip, 0))
        sock.sendto(q, (server, int(port)))
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    if len(data) < 12:
        raise ValueError("short DNS reply")
    rid, flags, qd, an, _ns, _ar = struct.unpack(">HHHHHH", data[:12])
    if rid != tid:
        raise ValueError("DNS id mismatch")
    if flags & 0x000F:
        raise ValueError(f"DNS rcode {flags & 0x000F}")

    def _skip_name(off):
        while True:
            if off >= len(data):
                raise ValueError("truncated name")
            ln = data[off]
            if ln == 0:
                return off + 1
            if ln & 0xC0 == 0xC0:
                return off + 2
            off += 1 + ln

    off = 12
    for _ in range(qd):
        off = _skip_name(off) + 4
    ips, ttls = [], []
    for _ in range(an):
        off = _skip_name(off)
        rtype, _rclass, ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
        off += 10
        if off + rdlen > len(data):
            raise ValueError("truncated rdata")
        if rtype == 1 and rdlen == 4:
            ips.append(".".join(str(x) for x in data[off:off + 4]))
            ttls.append(ttl)
        off += rdlen
    if not ips:
        raise ValueError(f"no A record for {host}")
    return ips, min(300, max(30, min(ttls)))


_DNS_POOL = None


def _dns_executor():
    """A small dedicated pool, so slow DNS never queues behind (or starves) the
    loop's default executor that getaddrinfo uses."""
    global _DNS_POOL
    if _DNS_POOL is None:
        import concurrent.futures
        _DNS_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="EgressDNS")
    return _DNS_POOL


def parse_bd_proxy_url(url: str):
    """Parse http://user:pass@host:port → (host, port, user, pass, pinned_ip)."""
    no_scheme = url.replace("http://", "", 1)
    creds, hostport = no_scheme.split("@", 1)
    user, password = creds.split(":", 1)
    host, port_str = hostport.split(":", 1)
    m = re.search(r"-ip-([\d\.]+)$", user)
    pinned_ip = m.group(1) if m else ""
    return host, int(port_str), user, password, pinned_ip


class ForwarderPool:
    """Holds N upstreams, each on its own local port. asyncio-native."""

    def __init__(self, listen_host: str = "127.0.0.1"):
        self.listen_host = listen_host
        self.upstreams: dict[int, Upstream] = {}   # port → Upstream
        self._started = False

    def add_upstream(self, proxy_url: str, port: int) -> Upstream:
        """Register a BD proxy URL on a local port. Does not start the server."""
        host, p, user, pwd, pinned = parse_bd_proxy_url(proxy_url)
        auth_b64 = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        up = Upstream(
            proxy_url=proxy_url,
            port=port,
            pinned_ip=pinned,
            upstream_host=host,
            upstream_port=p,
            user=user,
            password=pwd,
            auth_b64=auth_b64,
            stats=UpstreamStats(pinned_ip=pinned, port=port),
        )
        self.upstreams[port] = up
        return up

    def add_direct(self, port: int, bind_ip: str, fallback_home: bool = True,
                   connect_s: float = 4.0, dns_server: str = "") -> Upstream:
        """FX-1014-EGRESS: a direct CONNECT proxy on `port` whose outbound
        connections bind to `bind_ip` (no upstream proxy, no auth). connect_s
        bounds each bound dial (a dead-but-associated hotspot fails fast).
        Does not start the server."""
        up = Upstream(
            proxy_url="", port=port, pinned_ip=bind_ip, upstream_host="", upstream_port=0,
            user="", password="", auth_b64="",
            stats=UpstreamStats(pinned_ip=bind_ip, port=port),
            direct=True, bind_ip=bind_ip, fallback_home=fallback_home,
            connect_s=max(0.5, float(connect_s)),
            dns_server=str(dns_server or ""),
        )
        self.upstreams[port] = up
        return up

    async def _resolve_direct(self, up: Upstream, host: str) -> list:
        """FX-1014-EGRESS DNS: the IPv4s for `host` from up.dns_server (cached for
        the record TTL, 30-300 s), else [host] (the OS resolver, counted). A failed
        lookup is remembered for 20 s, so a dead DNS server costs one 2 s wait per
        host, not one per tunnel."""
        if not up.dns_server or re.fullmatch(r"[0-9.]+", host or "") or ":" in (host or ""):
            return [host]
        key = (host or "").lower()
        now = time.time()
        hit = up.dns_cache.get(key)
        if hit and hit[1] > now:
            return list(hit[0]) or [host]
        try:
            _srv, _, _p = up.dns_server.partition(":")
            ips, ttl = await asyncio.get_running_loop().run_in_executor(
                _dns_executor(), dns_query_a, host, _srv, up.bind_ip, 2.0, int(_p or 53))
            up.dns_cache[key] = (list(ips), now + ttl)
            return list(ips)
        except Exception as e:
            up.dns_fallbacks += 1
            up.dns_cache[key] = ([], now + 20.0)
            if up.dns_fallbacks in (1, 10) or up.dns_fallbacks % 100 == 0:
                logger.warning(f"[EGRESS:{up.port}] DNS via {up.dns_server} failed for {host} "
                               f"({type(e).__name__}: {e}) -- OS resolver used (#{up.dns_fallbacks})")
            return [host]

    async def _open_direct(self, up: Upstream, host: str, port: int):
        """Dial host:port bound to up.bind_ip -- DNS + connect inside one
        connect_s budget; an address that will not connect is dropped from the
        cache and the next one (then the OS resolver) is tried. On a bind /
        connect failure fall back to the home route only when allowed. Raises
        when nothing connects."""
        try:
            deadline = time.time() + up.connect_s
            addrs = await asyncio.wait_for(self._resolve_direct(up, host), timeout=up.connect_s)
            if addrs[0] != host:
                addrs = addrs[:2] + [host]
            last_err = None
            for addr in addrs:
                left = deadline - time.time()
                if left <= 0:
                    break
                try:
                    return await asyncio.wait_for(
                        asyncio.open_connection(addr, port, local_addr=(up.bind_ip, 0)),
                        timeout=left,
                    )
                except Exception as e:
                    last_err = e
                    if addr != host:
                        up.dns_cache.pop((host or "").lower(), None)
            raise last_err or asyncio.TimeoutError("egress connect budget spent")
        except Exception as e:
            if not up.fallback_home:
                raise
            up.fallbacks += 1
            up.stats.last_error = f"egress_bind_failed({up.bind_ip}): {type(e).__name__}: {e}"
            if up.fallbacks in (1, 10) or up.fallbacks % 100 == 0:
                logger.warning(f"[EGRESS:{up.port}] bind to {up.bind_ip} failed ({type(e).__name__}: {e}) "
                               f"-- HOME route used (fallback #{up.fallbacks})")
            return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10.0)

    @staticmethod
    def _split_hostport(target: str, default_port: int):
        """'host:443' / '[::1]:443' / 'host' -> (host, port)."""
        t = target.strip()
        if t.startswith("["):
            host, _, rest = t[1:].partition("]")
            port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else default_port
            return host, port
        host, sep, port_s = t.rpartition(":")
        if sep and port_s.isdigit():
            return host, int(port_s)
        return t, default_port

    async def start_all(self):
        """Launch a TCP server for each registered upstream."""
        for port, up in self.upstreams.items():
            await self._start_one(up)
        self._started = True
        logger.info(f"[FORWARDER] started {len(self.upstreams)} listeners")

    async def _start_one(self, up: Upstream):
        async def handler(reader, writer):
            await self._handle_client(up, reader, writer)
        up.server = await asyncio.start_server(handler, self.listen_host, up.port)
        if up.direct:
            logger.info(f"[FORWARDER] {self.listen_host}:{up.port} → DIRECT bound to {up.bind_ip}")
            return
        logger.info(f"[FORWARDER] {self.listen_host}:{up.port} → "
                    f"{up.upstream_host}:{up.upstream_port} (exit_ip={up.pinned_ip})")

    async def stop_all(self, per_server_timeout_s: float = 2.0):
        """Close every server. wait_closed() blocks until ALL open connections
        drain — and a CONNECT tunnel to a Chrome that just got killed mid-flight
        can persist until OS-level TCP cleanup (minutes). Bound each wait so
        shutdown is deterministic; sockets that don't close in time are
        abandoned for the OS to reap."""
        for up in self.upstreams.values():
            if up.server:
                up.server.close()
                try:
                    await asyncio.wait_for(up.server.wait_closed(),
                                           timeout=per_server_timeout_s)
                except (asyncio.TimeoutError, Exception):
                    pass
        self._started = False

    async def _handle_client(self, up: Upstream, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        up.stats.connections_total += 1
        up.stats.connections_active += 1
        up.stats.last_activity = time.time()

        upstream_writer = None
        try:
            # Parse the client's first request line (CONNECT or HTTP request)
            first_line = await asyncio.wait_for(reader.readline(), timeout=15.0)
            if not first_line:
                return

            if first_line.startswith(b"CONNECT "):
                await self._handle_connect(up, reader, writer, first_line)
            else:
                # Plain HTTP forward (rare for HTTPS-heavy traffic but keep it).
                await self._handle_http(up, reader, writer, first_line)
        except asyncio.TimeoutError:
            up.stats.errors += 1
            up.stats.last_error = "client_read_timeout"
        except Exception as e:
            up.stats.errors += 1
            up.stats.last_error = f"{type(e).__name__}: {e}"
            logger.debug(f"[FORWARDER:{up.port}] client {peer} error: {e}")
        finally:
            up.stats.connections_active -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_connect(self, up: Upstream, client_reader, client_writer,
                              first_line: bytes):
        """HTTP CONNECT (HTTPS tunnel) — pipe to BD upstream with auth."""
        # Drain remaining request headers
        try:
            while True:
                line = await asyncio.wait_for(client_reader.readline(), timeout=5.0)
                if line in (b"\r\n", b""):
                    break
        except asyncio.TimeoutError:
            return

        # Parse target host:port from CONNECT line
        parts = first_line.split()
        if len(parts) < 2:
            client_writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await client_writer.drain()
            return
        target = parts[1].decode("ascii", errors="ignore")

        if up.direct:
            await self._connect_direct(up, client_reader, client_writer, target)
            return

        # Open upstream
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(up.upstream_host, up.upstream_port),
                timeout=10.0,
            )
        except Exception as e:
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            up.stats.errors += 1
            up.stats.last_error = f"upstream_connect: {e}"
            return

        try:
            # Send CONNECT to BD upstream with Proxy-Authorization
            req = (
                f"CONNECT {target} HTTP/1.1\r\n"
                f"Host: {target}\r\n"
                f"Proxy-Authorization: Basic {up.auth_b64}\r\n"
                f"User-Agent: Mozilla/5.0\r\n"
                f"Proxy-Connection: Keep-Alive\r\n"
                f"\r\n"
            ).encode()
            up_writer.write(req)
            await up_writer.drain()

            # Read upstream response status
            status_line = await asyncio.wait_for(up_reader.readline(), timeout=15.0)
            if not status_line.startswith(b"HTTP/1.1 200") and not status_line.startswith(b"HTTP/1.0 200"):
                # Bubble the upstream error back to client
                client_writer.write(status_line)
                # Read & forward upstream headers + body until socket closes
                try:
                    while True:
                        chunk = await asyncio.wait_for(up_reader.read(BUFFER_SIZE), timeout=2.0)
                        if not chunk:
                            break
                        client_writer.write(chunk)
                except asyncio.TimeoutError:
                    pass
                await client_writer.drain()
                up.stats.errors += 1
                up.stats.last_error = f"upstream_status: {status_line.decode(errors='replace').strip()}"
                return

            # Drain remaining upstream response headers
            while True:
                line = await asyncio.wait_for(up_reader.readline(), timeout=5.0)
                if line in (b"\r\n", b""):
                    break

            # Tell client tunnel is established
            client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await client_writer.drain()

            # Bidirectional pipe
            await self._pipe_both(up, client_reader, client_writer, up_reader, up_writer)
        finally:
            try:
                up_writer.close()
                await up_writer.wait_closed()
            except Exception:
                pass

    async def _connect_direct(self, up: Upstream, client_reader, client_writer, target: str):
        """FX-1014-EGRESS CONNECT: dial the target itself from bind_ip."""
        try:
            host, port = self._split_hostport(target, 443)
            u_reader, u_writer = await self._open_direct(up, host, port)
        except Exception as e:
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            up.stats.errors += 1
            up.stats.last_error = f"direct_connect: {type(e).__name__}: {e}"
            return
        try:
            client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await client_writer.drain()
            await self._pipe_both(up, client_reader, client_writer, u_reader, u_writer)
        finally:
            try:
                u_writer.close()
                await u_writer.wait_closed()
            except Exception:
                pass

    async def _http_direct(self, up: Upstream, client_reader, client_writer, headers):
        """FX-1014-EGRESS plain HTTP: absolute-URI request line -> origin form,
        proxy-only headers dropped, sent from bind_ip."""
        try:
            method, uri, ver = headers[0].decode("latin-1").rstrip("\r\n").split(" ", 2)
            if "://" not in uri:
                raise ValueError("not an absolute URI")
            hostport, slash, path = uri.split("://", 1)[1].partition("/")
            host, port = self._split_hostport(hostport, 80)
            line = f"{method} /{path if slash else ''} {ver}\r\n".encode("latin-1")
            u_reader, u_writer = await self._open_direct(up, host, port)
        except Exception as e:
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            up.stats.errors += 1
            up.stats.last_error = f"direct_http: {type(e).__name__}: {e}"
            return
        try:
            u_writer.write(line + b"".join(
                h for h in headers[1:]
                if not h.lower().startswith((b"proxy-connection:", b"proxy-authorization:"))))
            await u_writer.drain()
            await self._pipe_both(up, client_reader, client_writer, u_reader, u_writer)
        finally:
            try:
                u_writer.close()
                await u_writer.wait_closed()
            except Exception:
                pass

    async def _handle_http(self, up: Upstream, client_reader, client_writer,
                           first_line: bytes):
        """Plain HTTP request — forward unchanged, with auth header injected."""
        # Read remaining headers
        headers = [first_line]
        try:
            while True:
                line = await asyncio.wait_for(client_reader.readline(), timeout=5.0)
                if not line:
                    break
                headers.append(line)
                if line == b"\r\n":
                    break
        except asyncio.TimeoutError:
            return

        if up.direct:
            await self._http_direct(up, client_reader, client_writer, headers)
            return

        # Inject Proxy-Authorization just before the terminating blank line
        injected = []
        seen_blank = False
        for h in headers:
            if h == b"\r\n" and not seen_blank:
                injected.append(f"Proxy-Authorization: Basic {up.auth_b64}\r\n".encode())
                injected.append(h)
                seen_blank = True
            else:
                injected.append(h)

        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(up.upstream_host, up.upstream_port),
                timeout=10.0,
            )
        except Exception as e:
            client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await client_writer.drain()
            up.stats.errors += 1
            up.stats.last_error = f"upstream_connect: {e}"
            return

        try:
            for h in injected:
                up_writer.write(h)
            await up_writer.drain()
            await self._pipe_both(up, client_reader, client_writer, up_reader, up_writer)
        finally:
            try:
                up_writer.close()
                await up_writer.wait_closed()
            except Exception:
                pass

    async def _pipe_both(self, up: Upstream, c_reader, c_writer, u_reader, u_writer):
        """Bidirectional byte pipe with stats updates.

        2026-10-23 FWD_TUNNEL_IDLE_S (default 0 = unchanged): on the IPRoyal residential line every tunnel of a
        session goes silent at once ~17-21 min in (both a Chrome through this forwarder and a Chrome talking to the
        vendor directly stalled in the same second, FS-W-B), and Chrome then hangs on its dead sockets and stops
        answering CDP. With the flag > 0, a tunnel that carries NO byte in EITHER direction for that many seconds
        is closed on both sides, so Chrome sees a closed socket and reconnects instead of waiting on a dead one.
        An idle keep-alive connection closed this way is harmless (Chrome opens a new one on the next request)."""
        idle_s = _tunnel_idle_s()
        last = [time.time()]            # shared by both directions: the tunnel's last byte in any direction
        counted = [False]               # idle_closes is counted once per tunnel, not once per direction

        async def pipe(src, dst, counter_attr):
            try:
                while True:
                    if idle_s > 0:
                        try:
                            chunk = await asyncio.wait_for(src.read(BUFFER_SIZE), timeout=idle_s)
                        except asyncio.TimeoutError:
                            if time.time() - last[0] >= idle_s:
                                if not counted[0]:
                                    counted[0] = True
                                    up.stats.idle_closes += 1
                                break               # the finally closes dst; the other direction sees EOF
                            continue                # the other direction moved recently: keep waiting
                    else:
                        chunk = await src.read(BUFFER_SIZE)
                    if not chunk:
                        break
                    dst.write(chunk)
                    await dst.drain()
                    setattr(up.stats, counter_attr,
                            getattr(up.stats, counter_attr) + len(chunk))
                    up.stats.last_activity = time.time()
                    last[0] = up.stats.last_activity
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass
                try:
                    src_w = u_writer if dst is c_writer else c_writer
                    src_w.close()                   # close our own side too, so the peer coroutine ends now
                except Exception:
                    pass

        await asyncio.gather(
            pipe(c_reader, u_writer, "bytes_out"),
            pipe(u_reader, c_writer, "bytes_in"),
            return_exceptions=True,
        )


# ─── Standalone CLI for testing ────────────────────────────────────────────────
async def _cli_main():
    import json
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print(f"Usage: python {Path(__file__).name} <proxy_url> [<port>=8080]")
        print(f"   or: python {Path(__file__).name} all  (binds first 10 enabled proxies to 8080-8089)")
        return 1

    pool = ForwarderPool()

    if sys.argv[1] == "all":
        proxy_file = Path(__file__).resolve().parent.parent.parent / "config" / "proxyIps.json"
        data = json.loads(proxy_file.read_text(encoding="utf-8"))
        proxies = data.get("proxies", [])[:10]
        for i, p in enumerate(proxies):
            pool.add_upstream(p, 8080 + i)
    else:
        proxy_url = sys.argv[1]
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
        pool.add_upstream(proxy_url, port)

    await pool.start_all()
    print("[FORWARDER] running — Ctrl+C to stop", flush=True)
    print("[FORWARDER] test it with:")
    for up in pool.upstreams.values():
        print(f"  curl -x http://127.0.0.1:{up.port} https://www.target.com/p/A-50270379 -I "
              f"  # exit_ip={up.pinned_ip}")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await pool.stop_all()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(_cli_main())
