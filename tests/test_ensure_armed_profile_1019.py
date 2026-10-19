#!/usr/bin/env python3
"""Offline test for the 2026-10-19 profile-guest signals (C-1018-20): ENSURE_ARMED_PROFILE_SIGNALS in
tools/ops/ensure_armed.py and the read-only preflight row accounts.profile_guest.

The 10-19 01:30 auto-arm loop: business + primary Chrome PROFILES were guest while the jar FILES read
member (TARGET_JAR_GUEST_GUARD), so jar_state() said "nothing to force" and the exit-87 branch never fixed it.

Every subprocess is faked (the Fake runner of test_ensure_armed_1014): nothing launches, nothing logs in.
Temp dirs only; logs are synthetic.

    python tests/test_ensure_armed_profile_1019.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "ops"))
sys.path.insert(0, str(ROOT / "tools" / "predrop"))
import test_ensure_armed_1014 as T   # noqa: E402  (Fake, setup, A, jar, MEMBER, GUEST; no side effects on import)
import ensure_armed as EA            # noqa: E402
import preflight as PF               # noqa: E402

EVENING, IN_BAND, MEMBER, GUEST = T.EVENING, T.IN_BAND, T.MEMBER, T.GUEST
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


GUEST_TAIL = ("already logged in ✅ (cookies refreshed, 55 cookies, auth=['accessToken', 'idToken', "
              "'refreshToken', 'login-session']) ⚠ GUEST (sut=G) — the profile is signed out")
OK_TAIL = "already logged in ✅ (cookies refreshed, 62 cookies, auth=['accessToken', 'idToken', 'refreshToken', 'login-session'])"


def relogin_log(root, *rows):
    """rows: (ts, acct, tail). Written in file order."""
    lines = [f"{iso(ts)} [RESULT] {acct}: {tail}" for ts, acct, tail in rows]
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "relogin.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_log(root, ts, *lines):
    name = "run_" + datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S") + ".log"
    (root / "logs" / "runs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "runs" / name).write_text("\n".join(("[LOG] run output",) + lines) + "\n", encoding="utf-8")
    return name


def set_flag(v):
    if v is None:
        os.environ.pop("ENSURE_ARMED_PROFILE_SIGNALS", None)
    else:
        os.environ["ENSURE_ARMED_PROFILE_SIGNALS"] = v


def ids(need):
    return [a for a, _, _ in need]


def main():
    saved = os.environ.get("ENSURE_ARMED_PROFILE_SIGNALS")
    try:
        _main()
    finally:
        set_flag(saved)
    print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for n in FAILED:
        print(f"  FAIL: {n}")
    return 1 if FAILED else 0


def _main():
    three = {"primary": MEMBER, "business": MEMBER, "alt-1": MEMBER}

    # ---- the flag parses defensively: only exactly "1" is on ----
    for v, want in ((None, False), ("0", False), ("", False), ("true", False), (" 1", False), ("1 ", False),
                    ("11", False), ("yes", False), ("1", True)):
        set_flag(v)
        check(f"flag_parse_{v!r}", EA.profile_signals_on() is want)

    # ---- flag OFF: decisions identical to today even with a fresh GUEST label + fresh boot-probe line ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        run_log(t, EVENING - 120, "[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded")
        for v in (None, "0", "true", " 1"):
            set_flag(v)
            check(f"off_{v!r}_no_need", EA.needs_login(EVENING) == [])
        set_flag(None)
        f = T.Fake(t, running=False)
        EA.evening(T.A(), run=f, now=EVENING)
        check("off_evening_no_login_but_starts", not f.has("relogin_one.py") and f.has("bot_ctl.py start"), f.cmds)
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("off_exit87_nothing_to_force", not f.has("relogin_one.py"), f.cmds)
        last = json.loads(EA.LAST.read_text())
        check("off_exit87_need_empty", last.get("need_login") == {}, last)
        # the jar-only rule is unchanged with the flag off
        T.setup(t, {"primary": GUEST, "business": MEMBER})
        check("off_jar_guest_still_needs", ids(EA.needs_login(EVENING)) == ["primary"])

    # ---- flag ON ----
    set_flag("1")
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 300, "business", GUEST_TAIL), (EVENING - 280, "alt-1", OK_TAIL))
        need = EA.needs_login(EVENING)
        check("on_fresh_label_only_that_account", ids(need) == ["business"], need)
        check("on_why_text", need and need[0][2].startswith("profile guest per relogin-label (jar: member"), need)
        txt = EA.LOG.read_text(encoding="utf-8")
        check("on_log_line", "[LOGIN] business: profile guest per relogin-label (jar: member" in txt, txt)
        check("on_log_line_once", txt.count("[LOGIN] business: profile guest per") == 1, txt)

    with tempfile.TemporaryDirectory() as d:     # stale label
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 25 * 60, "business", GUEST_TAIL))
        check("on_stale_label_25min_no", EA.needs_login(EVENING) == [])
        relogin_log(t, (EVENING - 19 * 60, "business", GUEST_TAIL))
        check("on_label_19min_yes", ids(EA.needs_login(EVENING)) == ["business"])
        relogin_log(t, (EVENING + 3600, "business", GUEST_TAIL))     # future-dated line: not "recent"
        check("on_future_label_no", EA.needs_login(EVENING) == [])

    with tempfile.TemporaryDirectory() as d:     # label for another account / no such account
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 60, "primary", GUEST_TAIL))
        check("on_other_account_label", ids(EA.needs_login(EVENING)) == ["primary"])
        relogin_log(t, (EVENING - 60, "someone-else", GUEST_TAIL))
        check("on_unknown_account_label_no", EA.needs_login(EVENING) == [])
        relogin_log(t, (EVENING - 60, "alt-10", GUEST_TAIL))
        check("on_prefix_account_label_no", EA.needs_login(EVENING) == [])

    with tempfile.TemporaryDirectory() as d:     # only the NEWEST [RESULT] line of the account counts
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 600, "business", GUEST_TAIL), (EVENING - 60, "business", OK_TAIL))
        check("on_newer_ok_line_clears", EA.needs_login(EVENING) == [])
        relogin_log(t, (EVENING - 600, "business", OK_TAIL), (EVENING - 60, "business", GUEST_TAIL))
        check("on_newer_guest_line_triggers", ids(EA.needs_login(EVENING)) == ["business"])

    with tempfile.TemporaryDirectory() as d:     # boot-probe lines in a fresh run log
        t = Path(d); T.setup(t, three)
        run_log(t, EVENING - 120, "[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded — the relaunch races WITHOUT it")
        need = EA.needs_login(EVENING)
        check("on_boot_skip_primary", ids(need) == ["primary"] and "profile guest per boot-probe" in need[0][2], need)
        run_log(t, EVENING - 60, "primary failed the boot login probe (no greeting)")
        check("on_failed_probe_phrase", ids(EA.needs_login(EVENING)) == ["primary"])
        run_log(t, EVENING - 30, "[SYSTEM] [BOOT_SKIP] business failed too while a skip was in force")
        check("on_other_account_in_run_log", ids(EA.needs_login(EVENING)) == ["business"])
        run_log(t, EVENING - 20, "[BOOT_SKIP] alt-10 recorded", "xalt-1 failed the boot login probe")
        check("on_run_log_name_boundaries", EA.needs_login(EVENING) == [])

    with tempfile.TemporaryDirectory() as d:     # a stale run log, and an unparseable name
        t = Path(d); T.setup(t, three)
        run_log(t, EVENING - 25 * 60, "[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded")
        check("on_stale_run_log_no", EA.needs_login(EVENING) == [])
        (t / "logs" / "runs" / "run_garbage.log").write_text("[BOOT_SKIP] primary", encoding="utf-8")
        check("on_unparseable_run_name_ignored", EA.needs_login(EVENING) == [])
        # only the newest run log is read: a fresh boot without the line clears an older fresh one
        run_log(t, EVENING - 300, "[BOOT_SKIP] primary x")
        run_log(t, EVENING - 30, "[SYSTEM] boot fine")
        check("on_newest_run_log_only", EA.needs_login(EVENING) == [])

    with tempfile.TemporaryDirectory() as d:     # missing / garbled / unreadable logs never raise
        t = Path(d); T.setup(t, three)
        try:
            r = EA.needs_login(EVENING)
            check("on_missing_logs_no_exception", r == [], r)
        except Exception as e:
            check("on_missing_logs_no_exception", False, repr(e))
        (t / "logs" / "relogin.log").write_bytes(b"\xff\xfe\x00garbage [RESULT] business: GUEST (sut=G)\n\x80\x81")
        try:
            r = EA.needs_login(EVENING)
            check("on_garbled_relogin_no_exception_no_trigger", r == [], r)   # no parseable timestamp -> not recent
        except Exception as e:
            check("on_garbled_relogin_no_exception_no_trigger", False, repr(e))
        (t / "logs" / "relogin.log").unlink()
        (t / "logs" / "relogin.log").mkdir()                 # exists() true, open() raises
        try:
            r = EA.needs_login(EVENING)
            txt = EA.LOG.read_text(encoding="utf-8")
            check("on_unreadable_relogin_falls_back", r == [] and "[LOGIN] profile-signal read failed:" in txt, (r, txt))
        except Exception as e:
            check("on_unreadable_relogin_falls_back", False, repr(e))
        # the other source still works when one read fails
        run_log(t, EVENING - 60, "[BOOT_SKIP] business recorded")
        try:
            r = EA.needs_login(EVENING)
            check("on_one_source_fails_other_works", ids(r) == ["business"], r)
        except Exception as e:
            check("on_one_source_fails_other_works", False, repr(e))
        # a jar-guest account is still found when a signal source fails
        T.setup(t, {"primary": GUEST, "business": MEMBER})
        for old_log in (t / "logs" / "runs").glob("run_2*.log"):
            old_log.unlink()                     # leave only the unreadable relogin.log (a directory)
        try:
            r = EA.needs_login(EVENING)
            check("on_read_failure_keeps_jar_answer", ids(r) == ["primary"] and r[0][2].startswith("signed out"), r)
        except Exception as e:
            check("on_read_failure_keeps_jar_answer", False, repr(e))

    with tempfile.TemporaryDirectory() as d:     # jar already guest + label: one entry, jar's reason
        t = Path(d); T.setup(t, {"primary": GUEST, "business": MEMBER})
        relogin_log(t, (EVENING - 60, "primary", GUEST_TAIL))
        need = EA.needs_login(EVENING)
        check("on_jar_guest_and_label_single_entry", ids(need) == ["primary"] and need[0][2].startswith("signed out"), need)

    # ---- evening + exit87 end to end through force_logins() ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        f = T.Fake(t, running=False)
        rc = EA.evening(T.A(), run=f, now=EVENING)
        logins = [(c, e) for c, e in f.cmds if "relogin_one.py" in c]
        check("ev_on_logs_in_label_account_only", rc == 0 and len(logins) == 1
              and "relogin_one.py business --force" in logins[0][0] and logins[0][1] == EA.LOGIN_ENV, logins)
        check("ev_on_rate_stamp", "business" in json.loads(EA.STATE.read_text()).get("last_forced_login", {}))
        check("ev_on_last_need_login_recorded", "business" in json.loads(EA.LAST.read_text()).get("need_login", {}))

    with tempfile.TemporaryDirectory() as d:     # exit87: the case of 10-19 01:30
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 90, "business", GUEST_TAIL), (EVENING - 70, "primary", OK_TAIL))
        f = T.Fake(t, running=True)
        rc = EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        logins = [(c, e) for c, e in f.cmds if "relogin_one.py" in c]
        check("x87_on_forces_label_account_only", rc == 0 and len(logins) == 1
              and "relogin_one.py business --force" in logins[0][0] and logins[0][1] == EA.LOGIN_ENV, logins)
        check("x87_on_never_bot_ctl", not f.has("bot_ctl"), f.cmds)
        # 6 h cap: the next exit-87 pass 10 min later (label still fresh) must NOT log in again
        relogin_log(t, (EVENING + 500, "business", GUEST_TAIL))
        f2 = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f2, now=EVENING + 600)
        check("x87_on_6h_cap_blocks_second_login", not f2.has("relogin_one.py"), f2.cmds)
        res = json.loads(EA.LAST.read_text())["logins"]["business"]
        check("x87_on_cap_reason", res.startswith("skipped: forced login"), res)
        relogin_log(t, (EVENING + 7 * 3600 - 60, "business", GUEST_TAIL))
        f3 = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f3, now=EVENING + 7 * 3600)
        check("x87_on_after_gap_logs_in_again", bool(f3.has("relogin_one.py business --force")), f3.cmds)

    with tempfile.TemporaryDirectory() as d:     # exit87 with only a boot-probe signal
        t = Path(d); T.setup(t, three)
        run_log(t, EVENING - 100, "[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded")
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("x87_on_boot_probe_forces_primary", bool(f.has("relogin_one.py primary --force"))
              and not f.has("relogin_one.py business") and not f.has("relogin_one.py alt-1"), f.cmds)

    with tempfile.TemporaryDirectory() as d:     # evening cap, evening restart rules unchanged
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 60, "business", GUEST_TAIL))
        EA.save_json(EA.STATE, {"last_forced_login": {"business": EVENING - 3600}})
        f = T.Fake(t, running=True, age_h=3.0)
        EA.evening(T.A(), run=f, now=EVENING)
        check("ev_on_cap_no_restart", not f.has("stop") and not f.has("relogin"), f.cmds)
        EA.save_json(EA.STATE, {})
        f = T.Fake(t, running=True, age_h=3.0)
        EA.evening(T.A(), run=f, now=EVENING)
        check("ev_on_green_label_restarts_out_of_band", f.has("bot_ctl.py stop") and f.has("relogin_one.py business"), f.cmds)
        EA.save_json(EA.STATE, {})
        f = T.Fake(t, running=True, age_h=3.0)
        relogin_log(t, (IN_BAND - 60, "business", GUEST_TAIL))
        EA.evening(T.A(), run=f, now=IN_BAND)
        check("ev_on_band_green_untouched", not f.has("stop") and not f.has("relogin"), f.cmds)
        f = T.Fake(t, running=False)
        EA.evening(T.A(dry_run=True), run=f, now=IN_BAND)
        check("ev_on_dry_run_touches_nothing", not f.has("start") and not f.has("relogin"), f.cmds)

    # ---- preflight: accounts.profile_guest is WARN only ----
    set_flag(None)
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        T.setup(t, {"primary": MEMBER, "business": MEMBER, "alt-1": GUEST})

        def row():
            R = PF.Report()
            PF.check_accounts(R, t, False, None)
            rs = [r for r in R.rows if r["id"] == "accounts.profile_guest"]
            return rs[0] if len(rs) == 1 else rs

        now = time.time()
        r = row()
        check("pf_no_relogin_log_ok", isinstance(r, dict) and r["status"] == "OK", r)
        relogin_log(t, (now - 3 * 86400, "business", GUEST_TAIL), (now - 60, "primary", OK_TAIL), (now - 60, "alt-1", GUEST_TAIL))
        r = row()
        check("pf_warn_member_jar_guest_label_any_age",
              isinstance(r, dict) and r["status"] == "WARN" and "business (72.0 h ago)" in r["detail"], r)
        check("pf_not_listed_ok_or_jar_guest", isinstance(r, dict) and "primary" not in r["detail"]
              and "alt-1" not in r["detail"], r)
        check("pf_items_machine_readable", isinstance(r, dict) and [i["account"] for i in r["items"]] == ["business"], r)
        relogin_log(t, (now - 3 * 86400, "business", GUEST_TAIL), (now - 60, "business", OK_TAIL))
        r = row()
        check("pf_newest_line_wins", isinstance(r, dict) and r["status"] == "OK", r)
        (t / "logs" / "relogin.log").unlink()
        (t / "logs" / "relogin.log").mkdir()
        r = row()
        check("pf_unreadable_log_guarded_not_fail", isinstance(r, dict) and r["status"] in ("UNKNOWN", "OK"), r)
        (t / "logs" / "relogin.log").rmdir()
        relogin_log(t, (now - 60, "business", GUEST_TAIL))
        R = PF.Report()
        PF.check_accounts(R, t, False, None)
        check("pf_never_fail", all(r["status"] != "FAIL" for r in R.rows if r["id"] == "accounts.profile_guest"))


if __name__ == "__main__":
    sys.exit(main())
