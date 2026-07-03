#!/usr/bin/env python3
"""Smoke test: per-account Session Sentinel + credential-relogin loading.

Covers the "keep every account logged in 100%" hardening without a browser:
  - _load_account_credentials picks the right account, rejects placeholder/missing
  - the sentinel records per-account health from each worker's check
  - workers mid-purchase are skipped (don't disturb an in-flight ATC)
  - TARGET_SESSION_SENTINEL=0 disables it

No browser, no network. Run: python tests/test_session_sentinel_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import Future
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.session.session_manager import SessionManager  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager as B  # noqa: E402


# ---- credential loading --------------------------------------------------- #
def _cfg(tmp, accounts):
    p = Path(tmp) / "target_accounts.json"
    p.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return p


def test_credentials_match_and_placeholder():
    with tempfile.TemporaryDirectory() as d:
        cfg = _cfg(d, [
            {"account_id": "primary", "username": "a@x.com", "password": "secret"},
            {"account_id": "biz", "username": "b@x.com", "password": "REPLACE_ME"},
        ])
        sm = SessionManager(session_path="t.json", account_id="primary")
        assert sm._load_account_credentials(cfg) == ("a@x.com", "secret")
        # placeholder password -> None
        sm2 = SessionManager(session_path="t.json", account_id="biz")
        assert sm2._load_account_credentials(cfg) is None
        # unknown account -> None
        sm3 = SessionManager(session_path="t.json", account_id="ghost")
        assert sm3._load_account_credentials(cfg) is None
        # no account_id -> None (legacy single-account never auto-relogins by creds)
        sm4 = SessionManager(session_path="t.json")
        assert sm4._load_account_credentials(cfg) is None


# ---- sentinel ------------------------------------------------------------- #
class _SM:
    def __init__(self, logged_in=True, in_progress=False):
        self._logged_in = logged_in
        self.purchase_in_progress = in_progress
        self.calls = 0

    async def ensure_logged_in(self):
        return self._logged_in


class _W:
    def __init__(self, wid, sm):
        self.cfg = type("C", (), {"worker_id": wid, "account_id": f"a{wid}"})()
        self.session_manager = sm

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"

    def run_async(self, coro):
        # Resolve the coroutine synchronously into a completed Future.
        import asyncio
        fut = Future()
        try:
            fut.set_result(asyncio.new_event_loop().run_until_complete(coro))
        except Exception as e:  # pragma: no cover
            fut.set_exception(e)
        return fut


class _Pool:
    def __init__(self, ws):
        self._ws = ws

    @property
    def workers(self):
        return list(self._ws)


def _mgr(ws):
    import threading
    m = B.__new__(B)
    m.worker_pool = _Pool(ws)
    m._sentinel_cycle_counter = 0
    m._account_health = {}
    m._sentinel_lock = threading.Lock()
    return m


def test_sentinel_records_health():
    os.environ.pop("TARGET_SESSION_SENTINEL", None)
    ws = [_W(1, _SM(logged_in=True)), _W(2, _SM(logged_in=False))]
    m = _mgr(ws)
    # Force the run on the first eligible tick (counter hits 5).
    for _ in range(5):
        m._maybe_run_session_sentinel()
    health = m.get_account_health()
    assert health["W1/a1"]["logged_in"] is True, health
    assert health["W2/a2"]["logged_in"] is False, health


def test_sentinel_skips_in_progress():
    os.environ.pop("TARGET_SESSION_SENTINEL", None)
    ws = [_W(1, _SM(logged_in=True, in_progress=True))]
    m = _mgr(ws)
    for _ in range(5):
        m._maybe_run_session_sentinel()
    assert m.get_account_health() == {}, "mid-purchase worker must be skipped"


def test_sentinel_disabled_flag():
    os.environ["TARGET_SESSION_SENTINEL"] = "0"
    try:
        ws = [_W(1, _SM(logged_in=True))]
        m = _mgr(ws)
        for _ in range(10):
            m._maybe_run_session_sentinel()
        assert m.get_account_health() == {}, "disabled sentinel must do nothing"
    finally:
        os.environ.pop("TARGET_SESSION_SENTINEL", None)


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
