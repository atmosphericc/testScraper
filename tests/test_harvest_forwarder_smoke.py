#!/usr/bin/env python3
"""Smoke test: harvester-forwarder + purchase-tab fingerprint gating.

Closes the two coherence gaps from the architecture review:
  - harvest logs in through the SAME per-account BD IP the purchase path uses
    (_setup_harvest_forwarders), so cookies are minted on the IP they're used from
  - the purchase SessionManager re-applies the per-account fingerprint only for
    file-driven (multi-account) configs, never for legacy single-account

Binds loopback ports in the 24000 band briefly; no outbound connection. No network.
Run: python tests/test_harvest_forwarder_smoke.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import harvest_accounts as H  # noqa: E402
from src.session.session_manager import SessionManager  # noqa: E402


def _acc(aid, proxy="", tz=""):
    return {"account_id": aid, "proxy_url": proxy, "timezone": tz,
            "session_path": f"{aid}.json", "profile_dir": f"prof-{aid}",
            "username": "", "password": ""}


def test_bd_accounts_get_forwarders():
    accts = [
        _acc("primary", "http://brd-c:pw@brd.superproxy.io:33335"),
        _acc("alt-1", ""),                       # home IP — no forwarder
        _acc("alt-2", "http://brd-c:pw@brd.superproxy.io:33335"),
    ]

    # Setup AND teardown must share one event loop (servers are bound to it) —
    # exactly how cmd_harvest runs it under a single asyncio.run.
    async def _scenario():
        pool, addr_map = await H._setup_harvest_forwarders(accts)
        try:
            assert pool is not None
            assert set(addr_map.keys()) == {"primary", "alt-2"}, addr_map
            assert "alt-1" not in addr_map
            assert all(v.startswith("127.0.0.1:240") for v in addr_map.values()), addr_map
            assert len(set(addr_map.values())) == 2
        finally:
            if pool is not None:
                await pool.stop_all()

    asyncio.run(_scenario())


def test_no_bd_accounts_no_forwarder():
    accts = [_acc("primary", ""), _acc("alt-1", "127.0.0.1:9999")]  # plain proxy isn't BD
    pool, addr_map = asyncio.run(H._setup_harvest_forwarders(accts))
    assert pool is None and addr_map == {}, (pool, addr_map)


def test_needs_forwarder_classification():
    assert H._needs_forwarder("http://u:p@brd.superproxy.io:33335")
    assert H._needs_forwarder("http://x@host")
    assert not H._needs_forwarder("127.0.0.1:9999")
    assert not H._needs_forwarder("http://plainhost:8080")


def test_sessionmanager_fingerprint_gating():
    # File-driven multi-account: apply_fingerprint honored when account_id present.
    sm = SessionManager(session_path="t.json", account_id="alt-1",
                        timezone="America/Chicago", apply_fingerprint=True)
    assert sm.apply_fingerprint is True
    assert sm.account_timezone == "America/Chicago"
    # Legacy single-account: no account_id => fingerprint application disabled even
    # if the flag is somehow set (never alter the established primary profile).
    sm2 = SessionManager(session_path="t.json", apply_fingerprint=True)
    assert sm2.apply_fingerprint is False, sm2.apply_fingerprint
    # Default: off.
    sm3 = SessionManager(session_path="t.json")
    assert sm3.apply_fingerprint is False


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  FAIL {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} smoke tests passed.")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
