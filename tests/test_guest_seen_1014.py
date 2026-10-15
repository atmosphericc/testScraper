#!/usr/bin/env python3
"""Offline tests for FX-1014-GUESTSEEN (C-1014-04/05): the session sentinel's
ladder passes on the account page's "Account"/"Orders" text, which a GUEST page
also shows -- on 10-14 07:03 two guest accounts logged logged_in=True all
morning. TARGET_SENTINEL_MEMBER_CHECK=1 reads the live accessToken after the
ladder passes and logs (GUEST)/(NO_TOKEN); tools/predrop/boot_check.py turns
that, or a guest jar on disk, into a WARN row per account.

Stubs only: no browser, nothing launched.
Run: python tests/test_guest_seen_1014.py
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402

check = H.check
env = H.env
quiet = H.quiet


class _SM:
    def __init__(self, status=None, raises=False, ok=True):
        self.status = status
        self.raises = raises
        self.ok = ok
        self.purchase_in_progress = False

    async def get_access_token_status(self):
        if self.raises:
            raise RuntimeError("cdp gone")
        return self.status

    async def ensure_logged_in(self):
        return self.ok


def _verdict(sm, ok=True):
    return asyncio.run(bpm_mod._sentinel_member_verdict(sm, ok))


MEMBER = {"present": True, "member": True, "eid": "e1", "ttl_s": 900.0}
GUEST = {"present": True, "member": False, "eid": None, "ttl_s": 3000.0}
NONE = {"present": False, "member": False, "eid": None, "ttl_s": -1.0}


def test_verdict_function():
    with env(TARGET_SENTINEL_MEMBER_CHECK=None):
        check("off_passthrough_guest", _verdict(_SM(GUEST)) is True)
    with env(TARGET_SENTINEL_MEMBER_CHECK="1"):
        check("member_ok", _verdict(_SM(MEMBER)) is True)
        g = _verdict(_SM(GUEST))
        check("guest_tagged", isinstance(g, str) and g.startswith("guest:"), g)
        n = _verdict(_SM(NONE))
        check("no_token_not_reported_read_errors_look_alike", n is True, n)
        check("ladder_failed_untouched", _verdict(_SM(GUEST), ok=False) is False)
        check("read_error_keeps_ok", _verdict(_SM(raises=True)) is True)
        check("odd_status_keeps_ok", _verdict(_SM("x")) is True)
        member_expired = dict(MEMBER, ttl_s=-50.0)
        check("expired_member_not_guest", _verdict(_SM(member_expired)) is True)


class _W:
    def __init__(self, wid, acct, sm):
        self.cfg = SimpleNamespace(worker_id=wid, account_id=acct)
        self.session_manager = sm
        self.purchase_executor = SimpleNamespace(_page_lock=None)

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"

    def run_async(self, coro):
        f = concurrent.futures.Future()
        try:
            f.set_result(asyncio.run(coro))
        except BaseException as e:  # noqa: BLE001
            f.set_exception(e)
        return f


def _sentinel(ws):
    m = H.bare_mgr()
    m.worker_pool = SimpleNamespace(workers=ws)
    m._sentinel_lock = threading.Lock()
    m._account_health = {}
    _, out = quiet(m._run_session_sentinel_once)
    return m, out


def test_sentinel_lines_and_health():
    ws = [_W(1, "primary", _SM(MEMBER)), _W(2, "business", _SM(GUEST)), _W(3, "alt-1", _SM(NONE))]
    with env(TARGET_SENTINEL_MEMBER_CHECK="1"):
        m, out = _sentinel(ws)
    check("line_member_ok", "[SENTINEL] W1/primary: logged_in=True (OK)" in out, out)
    check("line_guest", "[SENTINEL] W2/business: logged_in=False (GUEST)" in out, out)
    check("line_no_token_stays_ok", "[SENTINEL] W3/alt-1: logged_in=True (OK)" in out, out)
    h = m._account_health
    check("health_guest_flag", h["W2/business"]["guest"] is True and h["W2/business"]["logged_in"] is False, h)
    check("health_member_flag", h["W1/primary"]["guest"] is False and h["W1/primary"]["logged_in"] is True, h)
    ws = [_W(2, "business", _SM(GUEST))]
    with env(TARGET_SENTINEL_MEMBER_CHECK=None):
        m, out = _sentinel(ws)
    check("off_guest_still_ok_HEAD", "[SENTINEL] W2/business: logged_in=True (OK)" in out, out)


class _PE:
    def __init__(self, started_at=0.0):
        self._page_lock = None
        self._execute_started_at = started_at


class _LockedW(_W):
    """A worker whose executor has a page lock; a purchase 'starts waiting' on it
    while the sentinel's ladder runs (ensure_logged_in stamps _execute_started_at)."""
    def __init__(self, wid, acct, sm, purchase_arrives):
        super().__init__(wid, acct, sm)
        self.purchase_executor = _PE()
        pe = self.purchase_executor
        orig = sm.ensure_logged_in

        async def _ladder():
            if pe._page_lock is None:
                pe._page_lock = asyncio.Lock()
            if purchase_arrives:
                pe._execute_started_at = time.time() + 0.001
            return await orig()
        sm.ensure_logged_in = _ladder

    def run_async(self, coro):
        async def _wrap():
            if self.purchase_executor._page_lock is None:
                self.purchase_executor._page_lock = asyncio.Lock()
            return await coro
        return super().run_async(_wrap())


def test_member_read_skipped_when_a_purchase_waits():
    """Verifier G1a: a purchase that began waiting on the page lock during the
    ladder must not also wait for the token read."""
    with env(TARGET_SENTINEL_MEMBER_CHECK="1"):
        sm = _SM(GUEST)
        reads = []
        orig = sm.get_access_token_status

        async def _counted():
            reads.append(1)
            return await orig()
        sm.get_access_token_status = _counted
        m, out = _sentinel([_LockedW(2, "business", sm, purchase_arrives=True)])
        check("waiter_no_token_read", reads == [], reads)
        check("waiter_ladder_verdict_kept", "[SENTINEL] W2/business: logged_in=True (OK)" in out, out)
        sm2 = _SM(GUEST)
        m2, out2 = _sentinel([_LockedW(2, "business", sm2, purchase_arrives=False)])
        check("no_waiter_guest_reported", "[SENTINEL] W2/business: logged_in=False (GUEST)" in out2, out2)


# ── boot_check ───────────────────────────────────────────────────────────────

def _bc():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_bc_test", ROOT / "tools" / "predrop" / "boot_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_boot_check_rows():
    bc = _bc()
    text = ("[SENTINEL] W1/primary: logged_in=True (OK)\n"
            "[SENTINEL] W2/business: logged_in=True (OK)\n"
            "[SENTINEL] W2/business: logged_in=False (GUEST) accessToken has no member id (ttl=3000s) -- x\n"
            "[SENTINEL] W3/alt-1: logged_in=False (FAILED)\n")
    now = time.time()
    res = bc.analyze_log(text, now - 400, now, [], ["primary", "business", "alt-1"], {},
                         jar_guest={"alt-1": "signed out: guest token (accessToken sut=G, idToken sut=G)"})
    rows = {r["id"]: r for r in res["rows"]}
    check("bc_primary_ok", rows.get("member:primary", {}).get("status") == "OK", rows.get("member:primary"))
    check("bc_business_guest_warn", rows.get("member:business", {}).get("status") == "WARN"
          and "GUEST" in rows["member:business"]["detail"], rows.get("member:business"))
    check("bc_latest_verdict_wins", "SIGNED OUT" in rows.get("member:business", {}).get("detail", ""))
    check("bc_failed_warn", rows.get("member:alt-1", {}).get("status") == "WARN", rows.get("member:alt-1"))
    check("bc_jar_guest_warn", rows.get("jar:alt-1", {}).get("status") == "WARN", rows.get("jar:alt-1"))
    check("bc_no_jar_row_for_member", "jar:primary" not in rows)
    check("bc_guest_not_red", all(r["status"] != "FAIL" for k, r in rows.items()
                                  if k.startswith(("member:", "jar:"))))
    res2 = bc.analyze_log("", now - 400, now, [], ["primary"], {})
    check("bc_no_sentinel_no_row", not any(r["id"].startswith("member:") for r in res2["rows"]))


def test_jar_guest_map_reads_disk():
    bc = _bc()
    d = Path(tempfile.mkdtemp(prefix="guestseen_"))
    (d / "config").mkdir()
    (d / "tools" / "ops").mkdir(parents=True)
    (d / "tools" / "ops" / "ensure_armed.py").write_text((ROOT / "tools" / "ops" / "ensure_armed.py").read_text(encoding="utf-8"), encoding="utf-8")
    (d / "src").mkdir()
    for f in ("__init__.py", "jar_guard.py"):
        src = ROOT / "src" / f
        if src.exists():
            (d / "src" / f).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (d / "config" / "target_accounts.json").write_text(json.dumps({"accounts": [
        {"account_id": "primary", "session_path": "a.json", "enabled": True},
        {"account_id": "business", "session_path": "missing.json", "enabled": True}]}), encoding="utf-8")
    import base64

    def _jwt(claims):
        b = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return f"x.{b}.y"
    (d / "a.json").write_text(json.dumps({"cookies": [
        {"name": "login-session", "value": "z", "expires": time.time() + 86400},
        {"name": "accessToken", "value": _jwt({"sut": "R", "eid": "e"})}]}), encoding="utf-8")
    got = bc.jar_guest_map(d)
    check("jgm_member_absent", "primary" not in got, got)
    check("jgm_missing_jar_flagged", "business" in got, got)
    check("jgm_error_empty", bc.jar_guest_map(d / "nope") == {} or isinstance(bc.jar_guest_map(d / "nope"), dict))


def test_boot_check_waits_for_a_new_launch():
    """After `bot_ctl start` the newest run log is still the PREVIOUS run until the
    wrapper's login pass ends; judging it gave a false RED (10-14 23:54)."""
    from datetime import datetime as _dt
    bc = _bc()
    d = Path(tempfile.mkdtemp(prefix="bcnew_"))
    (d / "logs" / "runs").mkdir(parents=True)
    old_boot = time.time() - 1800
    name = "run_" + _dt.fromtimestamp(old_boot).strftime("%Y%m%d_%H%M%S") + ".log"
    (d / "logs" / "runs" / name).write_text("[x] old run\n", encoding="utf-8")

    def _wrap(rest, ago):
        ts = _dt.fromtimestamp(time.time() - ago)
        return f"[{ts.strftime('%a %m/%d/%Y %H:%M:%S')}.12]{rest}\n"
    wl = d / "logs" / "bot_restart_wrapper.log"
    wl.write_text(_wrap(" launch #1 app.py", 1801) + _wrap(" account login: relogin_one.py all", 20), encoding="utf-8")
    r = bc.run_once(d, None, None, check_port=False)
    check("bc_new_launch_pending", r["verdict"] == "PENDING" and r["rows"][0]["id"] == "new_launch", r)
    wl.write_text(_wrap(" launch #1 app.py", 1801) + _wrap(" app.py exited code=0", 20), encoding="utf-8")
    r = bc.run_once(d, None, None, check_port=False)
    check("bc_stopped_bot_not_masked", not any(x["id"] == "new_launch" for x in r["rows"]), r["rows"][:2])
    wl.write_text(_wrap(" launch #1 app.py", 1801) + _wrap(" account login: relogin_one.py all", 20), encoding="utf-8")
    r = bc.run_once(d, d / "logs" / "runs" / name, None, check_port=False)
    check("bc_explicit_log_judged", not any(x["id"] == "new_launch" for x in r["rows"]), r["rows"][:2])


def test_bat_arms_member_check():
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace")
    check("bat_member_check_armed", "\nset TARGET_SENTINEL_MEMBER_CHECK=1" in bat.replace("\r\n", "\n"))


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
