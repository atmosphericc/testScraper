#!/usr/bin/env python3
"""Offline tests for FX-1014-EGRESS (TARGET_ACCOUNT_EGRESS_BIND): one named
home-IP account's purchase Chrome exits through a second network adapter (a
phone hotspot, EXP-1002-NET) via a DIRECT local forwarder bound to that
adapter's IPv4. Unset = the exact prior behaviour.

Loopback sockets only (127.0.0.1): no browser, no Target, no internet.
Run: python tests/test_egress_bind_1014.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
from src.proxy.local_forwarder import ForwarderPool  # noqa: E402

check = H.check
env = H.env
quiet = H.quiet


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _FakePsutil(types.ModuleType):
    def __init__(self, adapters):
        super().__init__("psutil")
        self._a = adapters

    def net_if_addrs(self):
        return {name: [SimpleNamespace(family=socket.AF_INET, address=ip) for ip in ips]
                for name, ips in self._a.items()}


def _with_psutil(adapters, fn):
    old = sys.modules.get("psutil")
    sys.modules["psutil"] = _FakePsutil(adapters)
    try:
        return fn()
    finally:
        if old is not None:
            sys.modules["psutil"] = old
        else:
            sys.modules.pop("psutil", None)


# ── 1. the env map ───────────────────────────────────────────────────────────

def test_bind_map():
    ad = {"Wi-Fi": ["192.168.1.20"], "Wi-Fi 2": ["172.20.10.3"], "Ethernet 2": []}
    with env(TARGET_ACCOUNT_EGRESS_BIND=None):
        check("unset_empty", bpm_mod._egress_bind_map() == {})
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=Wi-Fi 2"):
        got, _ = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        check("adapter_name_resolved", got == {"alt-1": "172.20.10.3"}, got)
    with env(TARGET_ACCOUNT_EGRESS_BIND="ALT-1=wi-fi 2"):
        got, _ = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        check("case_insensitive", got == {"alt-1": "172.20.10.3"}, got)
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=172.20.10.3;business=192.168.1.20"):
        got, _ = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        check("two_ips", got == {"alt-1": "172.20.10.3", "business": "192.168.1.20"}, got)
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=Hotspot"):
        got, out = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        check("missing_adapter_dropped_loudly", got == {} and "stays on HOME" in out, (got, out))
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=Ethernet 2"):
        got, out = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        check("adapter_without_ipv4_dropped", got == {} and "no IPv4" in out, (got, out))
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=10.9.9.9"):
        got, out = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        check("ip_not_local_dropped", got == {} and "on no local adapter" in out, (got, out))
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=127.0.0.1"):
        got, out = quiet(_with_psutil, {"lo": ["127.0.0.1"]}, bpm_mod._egress_bind_map)
        check("loopback_rejected", got == {}, got)
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=Wi-Fi"):
        got, out = quiet(_with_psutil, {"Wi-Fi": ["169.254.7.7"]}, bpm_mod._egress_bind_map)
        check("apipa_only_adapter_dropped", got == {} and "stays on HOME" in out, (got, out))
        got, _ = quiet(_with_psutil, {"Wi-Fi": ["169.254.7.7", "172.20.10.4"]}, bpm_mod._egress_bind_map)
        check("apipa_skipped_real_ip_used", got == {"alt-1": "172.20.10.4"}, got)
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=169.254.7.7"):
        got, _ = quiet(_with_psutil, {"Wi-Fi": ["169.254.7.7"]}, bpm_mod._egress_bind_map)
        check("apipa_ip_rejected", got == {}, got)
    with env(TARGET_ACCOUNT_EGRESS_BIND="garbage;=x;alt-1="):
        got, _ = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        check("garbage_ignored", got == {}, got)


# ── 2. the direct forwarder (loopback round trips) ───────────────────────────

class _Loop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.t = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.t.start()

    def run(self, coro, timeout=10):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


def _echo_server():
    """A loopback TCP server that records what it got and echoes it back."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    got = []

    def _serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            try:
                c.settimeout(3)
                data = c.recv(65536)
                if data.startswith((b"GET ", b"POST ")):
                    while b"\r\n\r\n" not in data:
                        more = c.recv(65536)
                        if not more:
                            break
                        data += more
                got.append(data)
                c.sendall(b"ECHO:" + data)
            except Exception:
                pass
            finally:
                c.close()
    threading.Thread(target=_serve, daemon=True).start()
    return srv, srv.getsockname()[1], got


def _client(port, payload_first, payload_then=None):
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        c.sendall(payload_first)
        buf = b""
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                chunk = c.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if payload_then is not None and b"\r\n\r\n" in buf:
                c.sendall(payload_then)
                payload_then = None
                buf += b"|"
        return buf
    finally:
        c.close()


def test_direct_connect_round_trip():
    L = _Loop()
    srv, tport, got = _echo_server()
    try:
        fwd = ForwarderPool()
        fport = _free_port()
        up = fwd.add_direct(fport, "127.0.0.1")
        L.run(fwd.start_all())
        out = _client(fport, f"CONNECT 127.0.0.1:{tport} HTTP/1.1\r\nHost: x\r\n\r\n".encode(), b"hello")
        check("connect_200", out.startswith(b"HTTP/1.1 200"), out[:80])
        check("connect_tunnel_echo", b"ECHO:hello" in out, out)
        check("connect_no_fallback", up.fallbacks == 0, up.fallbacks)
        check("direct_flags", up.direct is True and up.bind_ip == "127.0.0.1")
        L.run(fwd.stop_all())
    finally:
        srv.close()
        L.stop()


def test_bind_failure_fallback_and_strict():
    """A bind IP that is on no adapter: fallback on -> home route (counted);
    fallback off -> 502."""
    L = _Loop()
    srv, tport, got = _echo_server()
    try:
        fwd = ForwarderPool()
        p1, p2 = _free_port(), _free_port()
        up1 = fwd.add_direct(p1, "10.254.254.254", fallback_home=True)
        up2 = fwd.add_direct(p2, "10.254.254.254", fallback_home=False)
        L.run(fwd.start_all())
        out1 = _client(p1, f"CONNECT 127.0.0.1:{tport} HTTP/1.1\r\n\r\n".encode(), b"hi")
        check("fallback_connects", out1.startswith(b"HTTP/1.1 200") and b"ECHO:hi" in out1, out1[:80])
        check("fallback_counted", up1.fallbacks == 1, up1.fallbacks)
        out2 = _client(p2, f"CONNECT 127.0.0.1:{tport} HTTP/1.1\r\n\r\n".encode())
        check("strict_502", out2.startswith(b"HTTP/1.1 502"), out2[:80])
        check("strict_error_recorded", "direct_connect" in (up2.stats.last_error or ""), up2.stats.last_error)
        L.run(fwd.stop_all())
    finally:
        srv.close()
        L.stop()


def test_direct_plain_http_rewrite():
    L = _Loop()
    srv, tport, got = _echo_server()
    try:
        fwd = ForwarderPool()
        fport = _free_port()
        fwd.add_direct(fport, "127.0.0.1")
        L.run(fwd.start_all())
        req = (f"GET http://127.0.0.1:{tport}/a/b?c=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
               f"Proxy-Connection: keep-alive\r\nX-K: v\r\n\r\n").encode()
        out = _client(fport, req)
        sent = got[-1] if got else b""
        check("http_origin_form", sent.startswith(b"GET /a/b?c=1 HTTP/1.1\r\n"), sent[:80])
        check("http_proxy_header_dropped", b"Proxy-Connection" not in sent and b"X-K: v" in sent, sent)
        check("http_echo_back", out.startswith(b"ECHO:GET /a/b"), out[:60])
        out2 = _client(fport, f"GET http://127.0.0.1:{tport} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        check("http_root_path", (got[-1] if got else b"").startswith(b"GET / HTTP/1.1"), got[-1:] )
        L.run(fwd.stop_all())
    finally:
        srv.close()
        L.stop()


def test_split_hostport():
    sp = ForwarderPool._split_hostport
    check("hp_plain", sp("www.target.com:443", 80) == ("www.target.com", 443))
    check("hp_default", sp("www.target.com", 443) == ("www.target.com", 443))
    check("hp_ipv6", sp("[::1]:8443", 443) == ("::1", 8443))
    check("hp_ipv6_default", sp("[::1]", 443) == ("::1", 443))


def test_bd_upstream_unchanged():
    """A BD upstream still parses and is not direct (prior behaviour)."""
    fwd = ForwarderPool()
    up = fwd.add_upstream("http://brd-customer-x-zone-y-ip-1.2.3.4:pw@brd.superproxy.io:33335", _free_port())
    check("bd_not_direct", up.direct is False and up.pinned_ip == "1.2.3.4" and up.fallbacks == 0)


# ── 3. the manager wiring ────────────────────────────────────────────────────

class _W:
    def __init__(self, wid, acct, proxy=None):
        self.cfg = SimpleNamespace(worker_id=wid, account_id=acct, proxy_url=proxy)

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"


def _mgr(workers, base):
    m = H.bare_mgr()
    m.worker_pool = SimpleNamespace(workers=workers)
    m._forwarder_pool = None
    m._forwarder_loop = None
    m._forwarder_thread = None
    m.PURCHASE_FORWARDER_PORT_BASE = base
    return m


def test_setup_wires_only_the_named_account():
    ws = [_W(1, "primary"), _W(2, "business"), _W(3, "alt-1")]
    base = _free_port() - 5
    m = _mgr(ws, base)
    old_map, old_probe = bpm_mod._egress_bind_map, bpm_mod._egress_public_ip_probe
    probed = []
    bpm_mod._egress_bind_map = lambda flog=None: {"alt-1": "127.0.0.1"}
    bpm_mod._egress_public_ip_probe = lambda pairs, flog=None: probed.append(pairs)
    try:
        _, out = quiet(m._setup_purchase_forwarders)
        check("named_account_rewired", ws[2].cfg.proxy_url == f"127.0.0.1:{base + 3}", ws[2].cfg.proxy_url)
        check("others_stay_home", ws[0].cfg.proxy_url is None and ws[1].cfg.proxy_url is None)
        check("egress_logged", "[EGRESS] W3/alt-1" in out, out)
        fwd = m._forwarder_pool
        check("pool_live_direct", fwd is not None and any(u.direct for u in fwd.upstreams.values()))
        time.sleep(0.2)
        check("probe_started", probed == [[("W3/alt-1", "127.0.0.1")]], probed)
        quiet(m._shutdown_purchase_forwarders)
    finally:
        bpm_mod._egress_bind_map, bpm_mod._egress_public_ip_probe = old_map, old_probe


def test_setup_unset_is_prior_behaviour():
    ws = [_W(1, "primary"), _W(2, "business"), _W(3, "alt-1")]
    m = _mgr(ws, _free_port())
    with env(TARGET_ACCOUNT_EGRESS_BIND=None):
        _, out = quiet(m._setup_purchase_forwarders)
    check("unset_no_pool", m._forwarder_pool is None, m._forwarder_pool)
    check("unset_all_home", all(w.cfg.proxy_url is None for w in ws))


def test_setup_proxied_account_not_rebound():
    ws = [_W(1, "primary"), _W(3, "alt-1", proxy="127.0.0.1:9999")]
    m = _mgr(ws, _free_port())
    old_map = bpm_mod._egress_bind_map
    bpm_mod._egress_bind_map = lambda flog=None: {"alt-1": "127.0.0.1"}
    try:
        _, out = quiet(m._setup_purchase_forwarders)
    finally:
        bpm_mod._egress_bind_map = old_map
    check("proxied_kept", ws[1].cfg.proxy_url == "127.0.0.1:9999", ws[1].cfg.proxy_url)
    check("proxied_warned", "bind ignored" in out, out)
    check("proxied_no_pool", m._forwarder_pool is None)


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
