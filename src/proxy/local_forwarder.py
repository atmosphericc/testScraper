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
                   connect_s: float = 4.0) -> Upstream:
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
        )
        self.upstreams[port] = up
        return up

    async def _open_direct(self, up: Upstream, host: str, port: int):
        """Dial host:port bound to up.bind_ip; on a bind/connect failure fall
        back to the home route when allowed. Raises when nothing connects."""
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(host, port, local_addr=(up.bind_ip, 0)),
                timeout=up.connect_s,
            )
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
        """Bidirectional byte pipe with stats updates."""

        async def pipe(src, dst, counter_attr):
            try:
                while True:
                    chunk = await src.read(BUFFER_SIZE)
                    if not chunk:
                        break
                    dst.write(chunk)
                    await dst.drain()
                    setattr(up.stats, counter_attr,
                            getattr(up.stats, counter_attr) + len(chunk))
                    up.stats.last_activity = time.time()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
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
