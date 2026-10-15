#!/usr/bin/env python3
"""Offline tests for the 2026-10-14 late backlog items (stubs + loopback only):

FX-1014-KICK        TARGET_REARM_KICK=1 -- a racer that frees its account wakes the
                    level re-arm loop at once (no 0-3 s idle tick).
FX-1014-SENTSTAGGER TARGET_SENTINEL_STAGGER_S -- accounts' sentinel checks (which
                    hold the page lock ~2 s) are spread out instead of simultaneous.
GUESTSEEN recovery  REVERTED (verifier: the write probe cannot see a guest) -- the
                    test pins that ensure_armed does not force-login from the live log.
EGRESS DNS          the egress forwarder resolves through the hotspot's DNS server,
                    queried from the bound address (DNS-steered Fastly name).

Nothing launches, nothing logs in, no Target traffic.
Run: python tests/test_kick_stagger_guest_dns_1014.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "ops"))

import test_identity_rest as H  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402
import src.proxy.local_forwarder as lf_mod  # noqa: E402
import ensure_armed as EA  # noqa: E402

check = H.check
env = H.env
quiet = H.quiet


# ── FX-1014-KICK ─────────────────────────────────────────────────────────────

def test_kick_event():
    with env(TARGET_REARM_KICK=None):
        m = H.bare_mgr()
        check("kick_off_none", m.rearm_kick_event() is None)
        m._rearm_kick_set()          # no-op, no raise
    with env(TARGET_REARM_KICK="1"):
        m = H.bare_mgr()
        ev = m.rearm_kick_event()
        check("kick_on_event", isinstance(ev, threading.Event) and not ev.is_set())
        check("kick_same_event", m.rearm_kick_event() is ev)
        m._rearm_kick_set()
        check("kick_set", ev.is_set())
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    i = src.index("self._release_worker_reservation(pinned_worker.label())")
    j = src.index("self._rearm_kick_set()", i)
    k = src.index("del self._active_purchases[state_key]")
    check("kick_after_release_and_removal", k < i < j and j - i < 400)
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    check("app_loop_waits_on_kick", "_kick.wait(max(0.0, _next_full - time.time()))" in app
          and "_kick.clear()" in app and "time.sleep(interval_s)" in app)


def test_kick_wakes_a_waiting_loop():
    """The app loop pattern: wait(interval) returns early on a kick."""
    with env(TARGET_REARM_KICK="1"):
        m = H.bare_mgr()
        ev = m.rearm_kick_event()
        woke = {}

        def _loop():
            t0 = time.time()
            ev.wait(3.0)
            woke["dt"] = time.time() - t0
            ev.clear()
        th = threading.Thread(target=_loop)
        th.start()
        time.sleep(0.1)
        m._rearm_kick_set()
        th.join(5)
        check("kick_wakes_fast", woke.get("dt", 9) < 1.0, woke)
        check("kick_cleared", not ev.is_set())


# ── FX-1014-SENTSTAGGER ──────────────────────────────────────────────────────

def _sentinel_mgr():
    m = H.bare_mgr()
    ws = [SimpleNamespace(label=(lambda i=i: f"W{i}/a{i}")) for i in (1, 2, 3)]
    m.worker_pool = SimpleNamespace(workers=ws)
    calls = []
    m._run_session_sentinel_for = lambda workers: calls.append(([w.label() for w in workers], time.time()))
    return m, calls


def test_sentinel_stagger():
    with env(TARGET_SENTINEL_STAGGER_S=None):
        m, calls = _sentinel_mgr()
        m._run_session_sentinel_once()
        check("stagger_off_all_at_once", [c[0] for c in calls] == [["W1/a1", "W2/a2", "W3/a3"]], calls)
    with env(TARGET_SENTINEL_STAGGER_S="0.15"):
        m, calls = _sentinel_mgr()
        t0 = time.time()
        m._run_session_sentinel_once()
        check("stagger_first_now", [c[0] for c in calls] == [["W1/a1"]], calls)
        time.sleep(0.6)
        order = [c[0] for c in calls]
        check("stagger_order", order == [["W1/a1"], ["W2/a2"], ["W3/a3"]], order)
        dts = [round(c[1] - t0, 2) for c in calls]
        check("stagger_spacing", dts[1] >= 0.12 and dts[2] >= 0.27, dts)
    with env(TARGET_SENTINEL_STAGGER_S="999"):
        check("stagger_clamped", bpm_mod._sentinel_stagger_s() == 90.0)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    check("bat_stagger_armed", "\nset TARGET_SENTINEL_STAGGER_S=30" in bat)
    # KICK v2 verifier (opus, 10-14 23:5x): DO NOT ARM -- yields come in bursts (all 3
    # accounts within 44-131 ms on 10-09) and a kick hands the fresh flip to the first
    # exiter alone; a kicked publish also stamps the debounce clock (full ticks dropped).
    check("bat_kick_not_armed", "\nset TARGET_REARM_KICK=1" not in bat)


class _LoopPM:
    def __init__(self, kick_on=True, miss=("M",)):
        self._ev = threading.Event() if kick_on else None
        self.miss = set(miss)

    def rearm_kick_event(self):
        return self._ev

    def rebuy_active(self):
        return False

    def get_all_states(self):
        return {"M": {"status": "ready", "rearm_hint_ts": time.time()}, "F": {"status": "failed"}}

    def kick_offer_ok(self, t):
        return t in self.miss


class _Bus:
    def __init__(self):
        self.pub = []

    def publish(self, kind, data):
        self.pub.append((time.time(), data["source"], sorted(data["stock_data"])))


def _run_loop(pm, interval, until_s, kicks=()):
    from app import StockMonitorThread
    bus = _Bus()
    obj = SimpleNamespace(running=True, _purchase_manager=pm, event_bus=bus,
                          stock_monitor=SimpleNamespace(_resilient_checker=SimpleNamespace(
                              _tcin_status={"M": 1, "F": 1})),
                          _build_level_rearm_map=lambda snap, st, now, rebuy_open=None: {"M": {}, "F": {}})
    th = threading.Thread(target=StockMonitorThread._level_rearm_loop, args=(obj, interval), daemon=True)
    t0 = time.time()
    th.start()
    for at in kicks:
        time.sleep(max(0.0, t0 + at - time.time()))
        pm._ev.set()
    time.sleep(max(0.0, t0 + until_s - time.time()))
    obj.running = False
    th.join(3)
    return t0, bus.pub


def test_kick_v2_loop():
    """Verifier K2: kicked ticks offer only MISS TCINs as 'level_rearm_kick', >= 0.6 s
    apart, and never move the regular full tick (which still carries failed rows)."""
    pm = _LoopPM(kick_on=True)
    t0, pub = _run_loop(pm, 1.0, 2.55, kicks=(0.2, 0.3, 0.45))
    full = [p for p in pub if p[1] == "level_rearm"]
    kicked = [p for p in pub if p[1] == "level_rearm_kick"]
    check("kv2_kicked_only_miss", kicked and all(p[2] == ["M"] for p in kicked), pub)
    check("kv2_full_carries_failed", full and all(p[2] == ["F", "M"] for p in full), pub)
    check("kv2_first_kick_fast", kicked and kicked[0][0] - t0 < 0.45, [round(p[0] - t0, 2) for p in kicked])
    ks = [p[0] for p in kicked]
    check("kv2_kicks_spaced", all(b - a >= 0.58 for a, b in zip(ks, ks[1:])), [round(k - t0, 2) for k in ks])
    fs = [round(p[0] - t0, 1) for p in full]
    check("kv2_full_schedule_fixed", fs[:2] == [1.0, 2.0], fs)
    pm_off = _LoopPM(kick_on=False)
    t0, pub = _run_loop(pm_off, 0.5, 1.3)
    check("kv2_off_plain_full_ticks", pub and all(p[1] == "level_rearm" and p[2] == ["F", "M"] for p in pub)
          and len(pub) == 2, pub)


def test_kick_offer_ok_and_debounce_exemption():
    with env(TARGET_REARM_KICK="1", TARGET_REBUY_NONBUYERS=None):
        m = H.bare_mgr()
        m._fx1006_miss_pending = {"1010000001": {"window_start": time.time()}}
        m.get_all_states = lambda: {"1010000002": {"status": "failed"}}
        check("offer_ok_miss", m.kick_offer_ok("1010000001") is True)
        check("offer_not_failed", m.kick_offer_ok("1010000002") is False)
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    check("debounce_exempts_kick", "not in ('proxy', 'level_rearm_kick')" in app)


def test_sentinel_for_stops_after_shutdown():
    m = H.bare_mgr()
    m._sentinel_stop = threading.Event()
    m._sentinel_stop.set()
    hit = []
    w = SimpleNamespace(session_manager=None, label=lambda: "W1/x")
    m._run_session_sentinel_for = bpm_mod.BulletproofPurchaseManager._run_session_sentinel_for.__get__(m)
    orig_getattr = getattr
    m._run_session_sentinel_for([SimpleNamespace(label=lambda: "W1/x",
                                                 session_manager=SimpleNamespace(purchase_in_progress=False,
                                                                                 ensure_logged_in=lambda: hit.append(1)))])
    check("sentinel_noop_after_stop", hit == [], hit)


# ── GUESTSEEN recovery in ensure_armed: REVERTED 10-14 23:4x ─────────────────
# Verifier (opus): a guest cart ACCEPTS writes (10-14: 69/69 warmup writes alive on two
# guests), so "3 GUEST + WRITE-AUTH DEAD" never fires on a real guest, while members sit
# in short DEAD windows (160 in October) and a stale log could force-login a member.

def test_no_live_guest_login_in_ensure_armed():
    src = (ROOT / "tools" / "ops" / "ensure_armed.py").read_text(encoding="utf-8")
    check("ea_no_live_guest_login", "live_guests" not in src)


# ── EGRESS DNS ───────────────────────────────────────────────────────────────

def _fake_dns(answers, ttl=60, truncate=False):
    """A loopback UDP DNS server answering every A query with `answers` (after a
    CNAME record, as Fastly does). Returns (port, stop, seen_sources)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.2)
    stop = threading.Event()
    seen = []

    def _serve():
        while not stop.is_set():
            try:
                q, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            seen.append(addr[0])
            tid = q[:2]
            qend = 12
            while q[qend] != 0:
                qend += 1 + q[qend]
            question = q[12:qend + 5]
            resp = tid + struct.pack(">HHHHH", 0x8180, 1, 1 + len(answers), 0, 0) + question
            cname = b"\x06target\x03map\x00"
            resp += b"\xc0\x0c" + struct.pack(">HHIH", 5, 1, ttl, len(cname)) + cname
            for ip in answers:
                resp += b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, ttl, 4) + bytes(int(x) for x in ip.split("."))
            if truncate:
                resp = resp[:-2]
            sock.sendto(resp, addr)
    threading.Thread(target=_serve, daemon=True).start()
    return sock.getsockname()[1], stop, seen, sock


def test_dns_query_and_resolve():
    port, stop, seen, sock = _fake_dns(["151.101.2.10", "151.101.66.10"], ttl=45)
    try:
        ips, ttl = lf_mod.dns_query_a("carts.target.com", "127.0.0.1", "127.0.0.1", 2.0, port)
        check("dns_a_records", ips == ["151.101.2.10", "151.101.66.10"] and ttl == 45, (ips, ttl))
        check("dns_from_bound_address", seen and seen[-1] == "127.0.0.1", seen)
        fwd = lf_mod.ForwarderPool()
        up = fwd.add_direct(0, "127.0.0.1", dns_server=f"127.0.0.1:{port}")
        got = asyncio.run(fwd._resolve_direct(up, "carts.target.com"))
        check("resolve_uses_server", got == ["151.101.2.10", "151.101.66.10"], got)
        n = len(seen)
        got2 = asyncio.run(fwd._resolve_direct(up, "Carts.Target.com"))
        check("resolve_cached_case_insensitive", got2 == got and len(seen) == n, (got2, len(seen), n))
        check("resolve_ip_literal_untouched", asyncio.run(fwd._resolve_direct(up, "10.1.2.3")) == ["10.1.2.3"])
    finally:
        stop.set()
        sock.close()
    fwd = lf_mod.ForwarderPool()
    dead_port = _free_udp_port()
    up = fwd.add_direct(0, "127.0.0.1", dns_server=f"127.0.0.1:{dead_port}")
    t0 = time.time()
    got = asyncio.run(fwd._resolve_direct(up, "carts.target.com"))
    check("resolve_failure_falls_back_to_os", got == ["carts.target.com"] and up.dns_fallbacks == 1, (got, up.dns_fallbacks))
    t1 = time.time()
    got = asyncio.run(fwd._resolve_direct(up, "carts.target.com"))
    check("resolve_failure_negative_cached", got == ["carts.target.com"] and up.dns_fallbacks == 1
          and time.time() - t1 < 0.5, (got, up.dns_fallbacks, time.time() - t1))
    up0 = fwd.add_direct(0, "127.0.0.1")
    check("no_server_os_resolver", asyncio.run(fwd._resolve_direct(up0, "carts.target.com")) == ["carts.target.com"])


def test_dns_hardening():
    port, stop, seen, sock = _fake_dns(["93.184.216.34"], truncate=True)
    try:
        try:
            lf_mod.dns_query_a("carts.target.com", "127.0.0.1", "127.0.0.1", 2.0, port)
            check("truncated_rdata_raises", False, "no raise")
        except ValueError as e:
            check("truncated_rdata_raises", "truncated" in str(e), str(e))
    finally:
        stop.set()
        sock.close()
    port, stop, seen, sock = _fake_dns(["93.184.216.34"], ttl=0x7FFFFFFF)
    try:
        ips, ttl = lf_mod.dns_query_a("carts.target.com", "127.0.0.1", "127.0.0.1", 2.0, port)
        check("ttl_ceiling_300", ttl == 300, ttl)
    finally:
        stop.set()
        sock.close()


def test_open_direct_tries_next_address():
    """An address that refuses is dropped from the cache and the next one dialed."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    tport = srv.getsockname()[1]
    port, stop, seen, sock = _fake_dns(["127.0.0.3", "127.0.0.1"])
    try:
        fwd = lf_mod.ForwarderPool()
        up = fwd.add_direct(0, "127.0.0.1", dns_server=f"127.0.0.1:{port}", fallback_home=False)

        async def _go():
            r, w = await fwd._open_direct(up, "edge.test", tport)
            peer = w.get_extra_info("peername")
            w.close()
            return peer
        peer = asyncio.run(_go())
        check("next_address_connected", peer and peer[0] == "127.0.0.1", peer)
        check("refusing_address_dropped_from_cache", "edge.test" not in up.dns_cache, up.dns_cache)
    finally:
        stop.set()
        sock.close()
        srv.close()


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_dns_server_lookup_guarded():
    check("dns_lookup_odd_alias_safe", bpm_mod._egress_dns_server("Eric\u2019s 'Pixel' $x`y") == "")
    check("dns_lookup_empty_alias", bpm_mod._egress_dns_server("") == "")
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    check("dns_lookup_alias_via_env", "$env:EGRESS_ALIAS" in src and "'EGRESS_ALIAS': alias" in src)


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
