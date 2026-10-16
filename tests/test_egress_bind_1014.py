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

GOOD_ROUTES = [("Ethernet 2", 25), ("Wi-Fi 2", 125), ("Wi-Fi", 125)]


def _routes(rows):
    old = bpm_mod._default_routes
    bpm_mod._default_routes = lambda timeout_s=10.0: rows
    return old


def test_bind_map():
    old = _routes(GOOD_ROUTES)
    try:
        _bind_map()
    finally:
        bpm_mod._default_routes = old


def test_route_guard():
    """Verifier E4: the egress adapter needs its own default route ranking strictly
    behind the home route, else the whole PC could move onto the hotspot."""
    ad = {"Wi-Fi": ["172.20.10.3"]}
    cases = [
        ("route_ok", [("Ethernet 2", 25), ("Wi-Fi", 125)], {"alt-1": "172.20.10.3"}, None),
        ("route_tie_refused", [("Ethernet 2", 25), ("Wi-Fi", 25)], {}, "ranks with or ahead"),
        ("route_ahead_refused", [("Ethernet 2", 25), ("Wi-Fi", 10)], {}, "ranks with or ahead"),
        ("route_missing_refused", [("Ethernet 2", 25)], {}, "no default route"),
        ("route_only_refused", [("Wi-Fi", 125)], {}, "ONLY default route"),
        ("route_unreadable_refused", None, {}, "could not read"),
    ]
    for name, rows, want, msg in cases:
        old = _routes(rows)
        try:
            with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=Wi-Fi"):
                got, out = quiet(_with_psutil, ad, bpm_mod._egress_bind_map)
        finally:
            bpm_mod._default_routes = old
        check(name, got == want and (msg is None or (msg in out and "hotspot_egress_setup.ps1" in out)),
              (got, out))


def test_direct_connect_timeout_param():
    fwd = ForwarderPool()
    up = fwd.add_direct(_free_port(), "127.0.0.1", connect_s=0.1)
    check("connect_s_floor", up.connect_s == 0.5, up.connect_s)
    up2 = fwd.add_direct(_free_port(), "127.0.0.1")
    check("connect_s_default_4", up2.connect_s == 4.0, up2.connect_s)


def _bind_map():
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


# ── 4. S1-EGRESS-READY: age-relaunch exemption (TARGET_ACCOUNT_EGRESS_AGE_EXEMPT) ──
# Drives the REAL _setup_purchase_forwarders -> Worker.build_components ->
# SessionManager.ensure_logged_in chain; only the browser, the network and the
# forwarder's failure points are stubbed.

import contextlib  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import tempfile  # noqa: E402

import src.purchasing.worker as worker_mod  # noqa: E402
from src.purchasing.worker import Worker, WorkerConfig  # noqa: E402
from src.session import session_manager as smmod  # noqa: E402

_QUIET = logging.getLogger("egress_age_test")
_QUIET.addHandler(logging.NullHandler())
_QUIET.propagate = False
_AGE_ENV = dict(TARGET_CHROME_MAX_AGE_S="2100", TARGET_CHROME_MAX_AGE_OFFSETS=None,
                TARGET_CHROME_RELAUNCH_DESYNC_S=None)
AGES = (1900, 2200, 5000)
HEAD_TABLE = {"primary": (0, 0, 0),      # home: never age-relaunched
              "business": (0, 1, 1),     # proxied: 2,100 s base
              "alt-1": (0, 1, 1)}        # egress account, flag off: proxied like HEAD


class _KA:
    def __init__(self, sm, status_callback=None):
        self.sm = sm


class _PX:
    def __init__(self, sm, status_callback=None):
        self.sm = sm
        self._page_lock = threading.Lock()


def _cfg(tmp, wid, acct, proxy=None):
    return WorkerConfig(worker_id=wid, account_id=acct, session_path=str(Path(tmp) / f"s{wid}.json"),
                        profile_dir=str(Path(tmp) / f"p{wid}"), proxy_url=proxy)


def _arm(sm):
    """No browser: count relaunches, mint nothing."""
    sm._relaunch_calls = 0

    async def _rl():
        sm._relaunch_calls += 1
        sm._browser_launched_at = time.time()
        return True

    async def _ok(*a, **k):
        return True
    sm._relaunch_browser = _rl
    sm.ensure_fresh_access_token = _ok
    sm.save_session_state = _ok
    sm.logger = _QUIET


def _fleet(tmp, flag, bind=None, patch=None, adapters=None, stdout=None):
    """primary (home) + business (a plain proxy_url: BD-style) + alt-1 (egress-bound).
    Runs the real forwarder setup, then the real Worker.build_components."""
    ws = [Worker(_cfg(tmp, 1, "primary")),
          Worker(_cfg(tmp, 2, "business", proxy="http://127.0.0.1:23999")),
          Worker(_cfg(tmp, 3, "alt-1"))]
    m = _mgr(ws, _free_port() - 5)
    old_map, old_probe = bpm_mod._egress_bind_map, bpm_mod._egress_public_ip_probe
    if bind is not False:
        bpm_mod._egress_bind_map = bind or (lambda flog=None: {"alt-1": "127.0.0.1"})
    bpm_mod._egress_public_ip_probe = lambda pairs, flog=None: None
    old_parts = (worker_mod.SessionKeepAlive, worker_mod.PurchaseExecutor)
    try:
        with env(TARGET_ACCOUNT_EGRESS_AGE_EXEMPT=flag, TARGET_ACCOUNT_EGRESS_FALLBACK_HOME=None,
                 TARGET_ACCOUNT_EGRESS_DNS="0", **_AGE_ENV):
            with (patch() if patch else contextlib.nullcontext()):
                if stdout is not None:
                    with contextlib.redirect_stdout(stdout):
                        _with_psutil(adapters or _FLEET_ADAPTERS, m._setup_purchase_forwarders)
                    out = stdout.getvalue()
                else:
                    _, out = quiet(_with_psutil, adapters or _FLEET_ADAPTERS, m._setup_purchase_forwarders)
        worker_mod.SessionKeepAlive, worker_mod.PurchaseExecutor = _KA, _PX
        for w in ws:
            w.build_components()
    finally:
        worker_mod.SessionKeepAlive, worker_mod.PurchaseExecutor = old_parts
        bpm_mod._egress_bind_map, bpm_mod._egress_public_ip_probe = old_map, old_probe
    for w in ws:
        _arm(w.session_manager)
    return m, ws, out


_FLEET_ADAPTERS = {"Ethernet 2": ["192.168.1.20"], "Wi-Fi": ["172.20.10.3"]}


def _age_run(sm, age, reset_note=True):
    sm._browser_launched_at = time.time() - age
    sm._proactive_relaunch_at = 0.0
    sm._age_relaunch_deferred_at = 0.0
    sm._relaunch_calls = 0
    if reset_note:
        sm._egress_exempt_noted_at = 0.0
    with env(**_AGE_ENV):
        ok, out = quiet(lambda: asyncio.run(sm.ensure_logged_in()))
    return sm._relaunch_calls, out, ok


def _decisions(ws):
    return {w.cfg.account_id: tuple(_age_run(w.session_manager, a)[0] for a in AGES) for w in ws}


def test_exempt_default_off_is_head_behaviour():
    """Flag unset / 0 / junk: nothing is marked and the relaunch decision at 1,900 / 2,200 /
    5,000 s is HEAD's for a home, a BD-style proxied and an egress account."""
    for flag in (None, "0", "", " ", "true", "yes", "2", "on"):
        with tempfile.TemporaryDirectory() as d:
            m, ws, out = _fleet(d, flag)
            try:
                got = _decisions(ws)
                check(f"off_decisions_are_head[{flag!r}]", got == HEAD_TABLE, got)
                check(f"off_no_mark[{flag!r}]",
                      not any(getattr(w.cfg, "egress_age_exempt", False) for w in ws)
                      and not any(w.session_manager.egress_age_exempt for w in ws))
                check(f"off_egress_still_bound[{flag!r}]",
                      ws[2].cfg.proxy_url is not None and ws[2].session_manager.proxy_url == ws[2].cfg.proxy_url)
                check(f"off_no_exempt_log[{flag!r}]", "EXEMPT" not in out, out)
                _n, sout, _ = _age_run(ws[2].session_manager, 5000)
                check(f"off_no_egress_age_line[{flag!r}]", "[EGRESS-AGE]" not in sout
                      and "[CHROME-AGE] alt-1" in sout, sout)
            finally:
                quiet(m._shutdown_purchase_forwarders)


def test_exempt_flag_on():
    """Flag on: the egress account gets 0 relaunches at 2,200 and 5,000 s; the BD-style
    account still gets 1 at 2,200 s; home stays 0."""
    for flag in ("1", " 1 "):
        with tempfile.TemporaryDirectory() as d:
            m, ws, out = _fleet(d, flag)
            try:
                got = _decisions(ws)
                check(f"on_decisions[{flag!r}]",
                      got == {"primary": (0, 0, 0), "business": (0, 1, 1), "alt-1": (0, 0, 0)}, got)
                alt = ws[2]
                check(f"on_marked[{flag!r}]", alt.cfg.egress_age_exempt is True
                      and alt.session_manager.egress_age_exempt is True)
                check(f"on_others_unmarked[{flag!r}]",
                      not ws[0].session_manager.egress_age_exempt and not ws[1].session_manager.egress_age_exempt
                      and not ws[0].cfg.egress_age_exempt and not ws[1].cfg.egress_age_exempt)
                check(f"on_setup_logged[{flag!r}]", "[EGRESS] W3/alt-1: age relaunch EXEMPT" in out, out)
                n, sout, ok = _age_run(alt.session_manager, 2200)
                check(f"on_note_line[{flag!r}]", n == 0 and ok is True
                      and "[EGRESS-AGE] alt-1:" in sout and "[CHROME-AGE]" not in sout, (n, sout))
                n, sout, _ = _age_run(ws[1].session_manager, 2200)
                check(f"on_bd_still_relaunches[{flag!r}]", n == 1 and "[CHROME-AGE] business:" in sout, (n, sout))
                # purchase in flight / young Chrome: nothing changes for the exempt account
                alt.session_manager.purchase_in_progress = True
                check(f"on_in_flight_quiet[{flag!r}]", _age_run(alt.session_manager, 5000)[0] == 0)
                alt.session_manager.purchase_in_progress = False
                check(f"on_young_quiet[{flag!r}]", _age_run(alt.session_manager, 100)[0] == 0)
            finally:
                quiet(m._shutdown_purchase_forwarders)


def test_exempt_kill_switch_age_zero_still_zero():
    with tempfile.TemporaryDirectory() as d:
        m, ws, _ = _fleet(d, "1")
        try:
            alt = ws[2].session_manager
            alt._browser_launched_at = time.time() - 99999
            with env(TARGET_CHROME_MAX_AGE_S="0"):
                _, sout = quiet(lambda: asyncio.run(alt.ensure_logged_in()))
            check("kill_age_zero_no_relaunch_no_note", alt._relaunch_calls == 0 and "[EGRESS-AGE]" not in sout, sout)
        finally:
            quiet(m._shutdown_purchase_forwarders)


class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


def test_exempt_five_hour_sentinel_sim():
    """60 sentinel ticks (300 s) on a fake clock: the exempt egress Chrome is never
    age-relaunched and logs [EGRESS-AGE] only about once per max age; the BD-style
    Chrome beside it keeps relaunching."""
    with tempfile.TemporaryDirectory() as d:
        m, ws, _ = _fleet(d, "1")
        T0 = 1_800_000_000.0
        clk = _Clock(T0)
        saved = smmod.time
        buf = io.StringIO()
        counts = {"business": 0, "alt-1": 0}
        try:
            for w in (ws[1], ws[2]):
                sm, nm = w.session_manager, w.cfg.account_id

                async def _rl(_sm=sm, _n=nm):
                    counts[_n] += 1
                    _sm._browser_launched_at = clk.t
                    return True
                sm._relaunch_browser = _rl
                sm._browser_launched_at = T0
                sm._proactive_relaunch_at = 0.0
                sm._egress_exempt_noted_at = 0.0
            smmod.time = clk
            with env(**_AGE_ENV):
                for k in range(1, 61):
                    clk.t = T0 + 300.0 * k
                    for w in (ws[1], ws[2]):
                        with contextlib.redirect_stdout(buf):
                            asyncio.run(w.session_manager.ensure_logged_in())
        finally:
            smmod.time = saved
            quiet(m._shutdown_purchase_forwarders)
        out = buf.getvalue()
        notes = [ln for ln in out.splitlines() if ln.startswith("[EGRESS-AGE] alt-1")]
        check("sim_exempt_zero_relaunches", counts["alt-1"] == 0, counts)
        check("sim_bd_keeps_relaunching", counts["business"] >= 6, counts)
        check("sim_no_chrome_age_line_for_alt1", "[CHROME-AGE] alt-1" not in out, out[-300:])
        check("sim_note_rate_limited", 1 <= len(notes) <= 10, len(notes))


def test_exempt_bind_refused_by_route_guard_leaves_home():
    """The REAL _egress_bind_map: a refusal from _egress_route_problem leaves alt-1 on
    home with the exemption unset (no attribute left set anywhere)."""
    ad = {"Ethernet 2": ["192.168.1.20"], "Wi-Fi": ["172.20.10.3"]}
    for rows in ([("Ethernet 2", 25), ("Wi-Fi", 25)],       # tie (the 10-14 host)
                 [("Ethernet 2", 25)],                       # no Wi-Fi default route yet
                 None):                                      # routes unreadable
        old = _routes(rows)
        try:
            with tempfile.TemporaryDirectory() as d, env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=Wi-Fi"):
                m, ws, out = _fleet(d, "1", bind=bpm_mod._egress_bind_map, adapters=ad)
                try:
                    alt = ws[2]
                    check(f"refused_stays_home[{rows}]", alt.cfg.proxy_url is None
                          and alt.session_manager.proxy_url is None and "stays on HOME" in out, out)
                    check(f"refused_no_mark[{rows}]", not getattr(alt.cfg, "egress_age_exempt", False)
                          and alt.session_manager.egress_age_exempt is False)
                    check(f"refused_behaves_as_home[{rows}]",
                          tuple(_age_run(alt.session_manager, a)[0] for a in AGES) == (0, 0, 0))
                    check(f"refused_no_pool[{rows}]", m._forwarder_pool is None)
                finally:
                    quiet(m._shutdown_purchase_forwarders)
        finally:
            bpm_mod._default_routes = old


def test_exempt_mark_cleared_when_start_all_fails():
    """The mark is set next to proxy_url and cleared where proxy_url is reverted: the
    forwarder pool failing to start reverts the egress worker AND its mark."""
    from src.proxy import local_forwarder as lf
    orig = lf.ForwarderPool.start_all

    async def _boom(self):
        raise RuntimeError("start_all boom")

    @contextlib.contextmanager
    def _patch():
        lf.ForwarderPool.start_all = _boom
        try:
            yield
        finally:
            lf.ForwarderPool.start_all = orig
    with tempfile.TemporaryDirectory() as d:
        m, ws, out = _fleet(d, "1", patch=_patch)
        try:
            alt = ws[2]
            check("startfail_reverted_home", alt.cfg.proxy_url is None and alt.session_manager.proxy_url is None, out)
            check("startfail_mark_cleared", alt.cfg.egress_age_exempt is False
                  and alt.session_manager.egress_age_exempt is False)
            check("startfail_behaves_as_home", tuple(_age_run(alt.session_manager, a)[0] for a in AGES) == (0, 0, 0))
            check("startfail_bd_style_untouched", ws[1].cfg.proxy_url == "http://127.0.0.1:23999"
                  and tuple(_age_run(ws[1].session_manager, a)[0] for a in AGES) == (0, 1, 1))
        finally:
            quiet(m._shutdown_purchase_forwarders)


class _TripOut(io.StringIO):
    """stdout that fails on the print that follows the mark (a console write error)."""
    def write(self, s):
        if "DIRECT, bound to" in s:
            raise RuntimeError("console write failed")
        return super().write(s)


def test_exempt_mark_cleared_when_setup_raises_after_the_mark():
    """An exception AFTER the mark is set (the bind print) takes the except path, which
    reverts proxy_url and must clear the mark in the same breath."""
    with tempfile.TemporaryDirectory() as d:
        m, ws, out = _fleet(d, "1", stdout=_TripOut())
        try:
            alt = ws[2]
            check("raise_after_mark_reverted_home", alt.cfg.proxy_url is None
                  and "egress setup failed (console write failed)" in out, out)
            check("raise_after_mark_mark_cleared", alt.cfg.egress_age_exempt is False
                  and alt.session_manager.egress_age_exempt is False)
            check("raise_after_mark_behaves_as_home", tuple(_age_run(alt.session_manager, a)[0] for a in AGES) == (0, 0, 0))
        finally:
            quiet(m._shutdown_purchase_forwarders)


def test_exempt_mark_never_set_when_add_direct_fails():
    from src.proxy import local_forwarder as lf
    orig = lf.ForwarderPool.add_direct

    def _boom(self, *a, **k):
        raise RuntimeError("add_direct boom")

    @contextlib.contextmanager
    def _patch():
        lf.ForwarderPool.add_direct = _boom
        try:
            yield
        finally:
            lf.ForwarderPool.add_direct = orig
    with tempfile.TemporaryDirectory() as d:
        m, ws, out = _fleet(d, "1", patch=_patch)
        try:
            alt = ws[2]
            check("adddirect_fail_home", alt.cfg.proxy_url is None and "egress setup failed" in out, out)
            check("adddirect_fail_no_mark", alt.cfg.egress_age_exempt is False
                  and alt.session_manager.egress_age_exempt is False)
            check("adddirect_fail_behaves_as_home", tuple(_age_run(alt.session_manager, a)[0] for a in AGES) == (0, 0, 0))
        finally:
            quiet(m._shutdown_purchase_forwarders)


def test_exempt_unmark_and_worker_guards():
    ns = SimpleNamespace(cfg=SimpleNamespace(egress_age_exempt=True))
    bpm_mod._egress_unmark(ns)
    check("unmark_clears", ns.cfg.egress_age_exempt is False)
    bare = SimpleNamespace(cfg=SimpleNamespace())
    bpm_mod._egress_unmark(bare)
    check("unmark_noop_does_not_create_attr", not hasattr(bare.cfg, "egress_age_exempt"))
    bpm_mod._egress_unmark(SimpleNamespace())          # no cfg at all: never raises
    check("unmark_no_cfg_never_raises", True)
    check("workerconfig_default_false", WorkerConfig().egress_age_exempt is False)
    # an inconsistent cfg (mark set, proxy_url None) never exempts the SessionManager
    old_parts = (worker_mod.SessionKeepAlive, worker_mod.PurchaseExecutor)
    worker_mod.SessionKeepAlive, worker_mod.PurchaseExecutor = _KA, _PX
    try:
        with tempfile.TemporaryDirectory() as d:
            w = Worker(_cfg(d, 3, "alt-1"))
            w.cfg.egress_age_exempt = True
            w.build_components()
            check("worker_guard_no_proxy_no_exemption", w.session_manager.proxy_url is None
                  and w.session_manager.egress_age_exempt is False)
            w2 = Worker(_cfg(d, 4, "x", proxy="127.0.0.1:23999"))
            w2.build_components()
            check("worker_default_not_exempt", w2.session_manager.egress_age_exempt is False)
    finally:
        worker_mod.SessionKeepAlive, worker_mod.PurchaseExecutor = old_parts


# ── 5. tools/ops/egress_check.py (read-only readiness check) ──────────────────

def _load_egress_check():
    spec = importlib.util.spec_from_file_location("egress_check_1015", ROOT / "tools" / "ops" / "egress_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EC = _load_egress_check()
HOME_PUB, PHONE_PUB = "98.76.54.32", "174.201.5.66"


def _good_deps(**over):
    """All-GREEN stubs around the REAL bot route guard (bpm._default_routes is patched
    by the caller). Real network / PowerShell functions are never reachable."""
    d = EC.default_deps()
    d.adapters = lambda: {"Ethernet 2": ["192.168.1.20"], "Wi-Fi": ["172.20.10.3"]}
    d.interface_metric = lambda alias: (100, False)
    d.dns_server = lambda alias: "172.20.10.1"
    d.http_ip = lambda bind=None, **k: PHONE_PUB if bind else HOME_PUB
    d.dns_query = lambda srv, bind, **k: ["93.184.216.34"]
    for k, v in over.items():
        setattr(d, k, v)
    return d


def _ec(deps, *argv):
    rc, out = quiet(EC.main, list(argv) or ["--alias", "Wi-Fi"], deps)
    return rc, out


def _status(out, name):
    """GREEN / RED / SKIP of check `name` in the rendered output (None = no such line)."""
    import re
    for ln in out.splitlines():
        m = re.match(r"\[(\w+)\s*\]\s+(\w+)\s", ln)
        if m and m.group(2) == name:
            return m.group(1)
    return None


def test_egress_check_all_green():
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        rc, out = _ec(_good_deps())
    finally:
        bpm_mod._default_routes = old
    stat = {n: _status(out, n) for n in ("metric", "wifi_up", "route", "public_ip", "dns")}
    check("ec_green_all", rc == 0 and set(stat.values()) == {"GREEN"} and "VERDICT: GREEN" in out, (rc, stat, out))
    check("ec_public_ips_masked", HOME_PUB not in out and PHONE_PUB not in out
          and "98.76.x.x" in out and "174.201.x.x" in out, out)
    check("ec_private_ips_in_full", "172.20.10.3" in out and "172.20.10.1" in out, out)
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        rc2, out2 = _ec(_good_deps(), "--alias", "Wi-Fi", "--show-ips")
    finally:
        bpm_mod._default_routes = old
    check("ec_show_ips", HOME_PUB in out2 and PHONE_PUB in out2, out2)


def test_egress_check_wifi_down():
    old = _routes([("Ethernet 2", 25)])
    try:
        d = _good_deps(adapters=lambda: {"Ethernet 2": ["192.168.1.20"], "Wi-Fi": ["169.254.7.7"]},
                       dns_server=lambda alias: "")
        rc, out = _ec(d)
    finally:
        bpm_mod._default_routes = old
    check("ec_wifi_down_red", rc == 1 and _status(out, "wifi_up") == "RED" and "169.254" in out
          and "VERDICT: RED" in out, out)
    check("ec_wifi_down_net_skipped", _status(out, "public_ip") == "SKIP", out)
    check("ec_wifi_down_names_join", "join" in out.split("VERDICT:")[1].lower()
          or "hotspot" in out.split("VERDICT:")[1].lower(), out)
    old = _routes([("Ethernet 2", 25)])
    try:
        rc, out = _ec(_good_deps(adapters=lambda: {"Ethernet 2": ["192.168.1.20"]}))
    finally:
        bpm_mod._default_routes = old
    check("ec_wifi_missing_red", rc == 1 and _status(out, "wifi_up") == "RED" and "no adapter named" in out, out)


def test_egress_check_metric_25_names_setup_script():
    # not joined yet: only the home route exists, the Wi-Fi interface metric is the automatic 25
    old = _routes([("Ethernet 2", 25)])
    try:
        rc, out = _ec(_good_deps(interface_metric=lambda alias: (25, True),
                                 adapters=lambda: {"Ethernet 2": ["192.168.1.20"]}))
    finally:
        bpm_mod._default_routes = old
    check("ec_metric25_red", rc == 1 and _status(out, "metric") == "RED", out)
    check("ec_metric25_names_script", "hotspot_egress_setup.ps1" in out and "BEFORE joining" in out, out)
    # joined with the equal metric: the bot's own guard refuses too, and the verdict step is the script
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 25)])
    try:
        rc, out = _ec(_good_deps(interface_metric=lambda alias: (25, True)))
    finally:
        bpm_mod._default_routes = old
    check("ec_route_tie_red", _status(out, "route") == "RED" and "ranks with or ahead" in out, out)
    check("ec_tie_next_is_script", "hotspot_egress_setup.ps1" in out.split("VERDICT:")[1], out)
    # a fixed higher metric is GREEN
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        rc, out = _ec(_good_deps(interface_metric=lambda alias: (100, False)))
    finally:
        bpm_mod._default_routes = old
    check("ec_metric100_green", _status(out, "metric") == "GREEN" and rc == 0, out)
    # unreadable routes / interface: RED, never a silent GREEN
    old = _routes(None)
    try:
        rc, out = _ec(_good_deps())
    finally:
        bpm_mod._default_routes = old
    check("ec_routes_unreadable_red", rc == 1 and _status(out, "metric") == "RED"
          and _status(out, "route") == "RED", out)
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        rc, out = _ec(_good_deps(interface_metric=lambda alias: None))
    finally:
        bpm_mod._default_routes = old
    check("ec_metric_unreadable_red", rc == 1 and _status(out, "metric") == "RED", out)


def test_egress_check_public_ip_same_as_home():
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        rc, out = _ec(_good_deps(http_ip=lambda bind=None, **k: HOME_PUB))
    finally:
        bpm_mod._default_routes = old
    check("ec_same_ip_red", rc == 1 and _status(out, "public_ip") == "RED" and "SAME as home" in out, out)

    def _dead(bind=None, **k):
        if bind:
            raise OSError("no path")
        return HOME_PUB
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        rc, out = _ec(_good_deps(http_ip=_dead))
    finally:
        bpm_mod._default_routes = old
    check("ec_bound_dead_red", rc == 1 and _status(out, "public_ip") == "RED" and "OSError" in out, out)


def test_egress_check_dns():
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        rc, out = _ec(_good_deps(dns_server=lambda alias: ""))
        check("ec_dns_unlisted_red", rc == 1 and _status(out, "dns") == "RED"
              and "no IPv4 DNS server" in out, out)

        def _fail(srv, bind, **k):
            raise TimeoutError("no answer")
        rc, out = _ec(_good_deps(dns_query=_fail))
        check("ec_dns_silent_red", rc == 1 and _status(out, "dns") == "RED" and "TimeoutError" in out, out)
        rc, out = _ec(_good_deps(), "--alias", "Wi-Fi", "--no-net")
        check("ec_no_net_skips_probes", _status(out, "public_ip") == "SKIP" and _status(out, "dns") == "SKIP"
              and rc == 0 and "without --no-net" in out, (rc, out))
    finally:
        bpm_mod._default_routes = old


def test_egress_check_prints_no_account_values_and_reads_no_config():
    old = _routes([("Ethernet 2", 25), ("Wi-Fi", 125)])
    try:
        with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=Wi-Fi;business=172.20.10.3"):
            rc, out = _ec(_good_deps(), "--json")
            rc2, out2 = _ec(_good_deps())
    finally:
        bpm_mod._default_routes = old
    check("ec_alias_from_env_no_account_ids", rc == 0 and "alt-1" not in out + out2
          and "business" not in out + out2 and json.loads(out)["alias"] == "Wi-Fi", out)
    with env(TARGET_ACCOUNT_EGRESS_BIND="alt-1=172.20.10.3"):
        check("ec_alias_from_ip", EC.alias_from_env({"Wi-Fi": ["172.20.10.3"]}) == "Wi-Fi")
        check("ec_alias_from_unknown_ip", EC.alias_from_env({"Wi-Fi": ["10.1.1.1"]}) is None)
    with env(TARGET_ACCOUNT_EGRESS_BIND=None):
        check("ec_alias_unset_none", EC.alias_from_env({"Wi-Fi": ["10.1.1.1"]}) is None)
    src = (ROOT / "tools" / "ops" / "egress_check.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    check("ec_no_target_host", "target.com" not in src.lower() and "redsky" not in src.lower())
    check("ec_reads_no_config", "target_accounts" not in src and "product_config" not in src
          and "proxyIps" not in src and "config/" not in code)
    check("ec_ps_is_read_only", all(bad not in src for bad in
          ("Set-Net", "New-Net", "Remove-Net", "Set-DnsClient", "netsh", "Disable-", "Enable-NetAdapter", "route add",
           "os.system")), "a mutating command is in the tool")
    check("ec_probe_host_is_ipify", EC.PROBE_HOST == "api.ipify.org")


def test_egress_check_probes_real_code_on_loopback():
    """http_ip / dns_query / interface_metric against loopback fakes (no internet)."""
    # http_ip: a bound loopback socket reads a bare IPv4 body
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    mode = {"body": b"198.51.100.7"}

    def _serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            try:
                c.settimeout(2)
                c.recv(4096)
                body = mode["body"]
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            except Exception:
                pass
            finally:
                c.close()
    threading.Thread(target=_serve, daemon=True).start()
    try:
        check("ec_http_ip_bound", EC.http_ip("127.0.0.1", host="127.0.0.1", port=port, timeout=3) == "198.51.100.7")
        check("ec_http_ip_unbound", EC.http_ip(None, host="127.0.0.1", port=port, timeout=3) == "198.51.100.7")
        mode["body"] = b"<html>blocked</html>"
        try:
            EC.http_ip("127.0.0.1", host="127.0.0.1", port=port, timeout=3)
            check("ec_http_ip_rejects_non_ip", False, "no raise")
        except ValueError:
            check("ec_http_ip_rejects_non_ip", True)
    finally:
        srv.close()

    # dns_query: a fake UDP DNS server on loopback
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.bind(("127.0.0.1", 0))
    uport = u.getsockname()[1]
    dmode = {"kind": "ok"}

    def _dns():
        import struct as st
        while True:
            try:
                data, addr = u.recvfrom(512)
            except OSError:
                return
            if dmode["kind"] == "silent":
                continue
            qid = data[:2]
            question = data[12:]
            if dmode["kind"] == "nxdomain":
                u.sendto(qid + st.pack(">HHHHH", 0x8183, 1, 0, 0, 0) + question, addr)
            elif dmode["kind"] == "badid":
                u.sendto(b"\x00\x00" + st.pack(">HHHHH", 0x8180, 1, 1, 0, 0) + question, addr)
            else:
                ans = (b"\xc0\x0c" + st.pack(">HHIH", 1, 1, 60, 4) + bytes([93, 184, 216, 34])
                       + b"\xc0\x0c" + st.pack(">HHIH", 5, 1, 60, 2) + b"\xc0\x0c"      # a CNAME to skip
                       + b"\xc0\x0c" + st.pack(">HHIH", 1, 1, 60, 4) + bytes([93, 184, 216, 35]))
                u.sendto(qid + st.pack(">HHHHH", 0x8180, 1, 3, 0, 0) + question + ans, addr)
    threading.Thread(target=_dns, daemon=True).start()
    try:
        got = EC.dns_query("127.0.0.1", "127.0.0.1", port=uport, timeout=2)
        check("ec_dns_query_parses_a_records", got == ["93.184.216.34", "93.184.216.35"], got)
        for kind in ("nxdomain", "badid", "silent"):
            dmode["kind"] = kind
            try:
                EC.dns_query("127.0.0.1", "127.0.0.1", port=uport, timeout=0.4)
                check(f"ec_dns_query_rejects_{kind}", False, "no raise")
            except Exception:
                check(f"ec_dns_query_rejects_{kind}", True)
    finally:
        u.close()

    # interface_metric: the alias travels in the environment, never in the command text
    calls = []
    real_run = EC.subprocess.run

    def _fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return SimpleNamespace(stdout=_fake_run.out, returncode=_fake_run.rc)
    EC.subprocess.run = _fake_run
    try:
        _fake_run.out, _fake_run.rc = "100|Disabled\r\n", 0
        check("ec_metric_fixed", EC.interface_metric("Wi-Fi \"x") == (100, False))
        cmd, kw = calls[-1]
        check("ec_metric_alias_via_env", kw["env"]["EGRESS_ALIAS"] == "Wi-Fi \"x" and "Wi-Fi" not in " ".join(cmd))
        check("ec_metric_cmd_is_get_only", "Get-NetIPInterface" in " ".join(cmd) and "Set-" not in " ".join(cmd))
        _fake_run.out = "25|Enabled"
        check("ec_metric_auto", EC.interface_metric("Wi-Fi") == (25, True))
        _fake_run.out = "garbage"
        check("ec_metric_garbage_none", EC.interface_metric("Wi-Fi") is None)
        _fake_run.out, _fake_run.rc = "100|Disabled", 1
        check("ec_metric_nonzero_none", EC.interface_metric("Wi-Fi") is None)

        def _raise(*a, **k):
            raise FileNotFoundError("powershell.exe")
        EC.subprocess.run = _raise
        check("ec_metric_missing_powershell_none", EC.interface_metric("Wi-Fi") is None)
    finally:
        EC.subprocess.run = real_run


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
