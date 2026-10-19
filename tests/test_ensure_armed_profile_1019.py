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


def raw_relogin(root, *lines):
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / "relogin.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def stamps():
    try:
        return json.loads(EA.STATE.read_text(encoding="utf-8")).get("last_forced_login", {})
    except Exception:
        return None


class RaceFake(T.Fake):
    """A Fake that, when the bot is stopped, plays 'another process stamped a forced login meanwhile'."""

    def __init__(self, *a, on_stop=None, **kw):
        super().__init__(*a, **kw)
        self.on_stop = on_stop

    def __call__(self, cmd, env_extra=None, timeout=900):
        r = super().__call__(cmd, env_extra, timeout)
        if "bot_ctl.py stop" in " ".join(map(str, cmd)) and self.on_stop:
            self.on_stop()
        return r


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
        check("off_skip_cleared_line_not_account", EA.needs_login(EVENING) == [])   # app.py: "not an account problem"
        run_log(t, EVENING - 25, "[SYSTEM] [BOOT_SKIP] business (Worker 1) recorded")
        check("on_other_account_in_run_log", ids(EA.needs_login(EVENING)) == ["business"])
        run_log(t, EVENING - 20, "[BOOT_SKIP] alt-10 recorded", "xalt-1 failed the boot login probe")
        check("on_run_log_name_boundaries", EA.needs_login(EVENING) == [])

    with tempfile.TemporaryDirectory() as d:     # a stale run log, and an unparseable name
        t = Path(d); T.setup(t, three)
        run_log(t, EVENING - 25 * 60, "[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded")
        check("on_stale_run_log_no", EA.needs_login(EVENING) == [])
        (t / "logs" / "runs" / "run_garbage.log").write_text("[BOOT_SKIP] primary (Worker 1) recorded", encoding="utf-8")
        check("on_unparseable_run_name_ignored", EA.needs_login(EVENING) == [])
        # only the newest run log is read: a fresh boot without the line clears an older fresh one
        run_log(t, EVENING - 300, "[BOOT_SKIP] primary (Worker 1) recorded x")
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
        run_log(t, EVENING - 60, "[BOOT_SKIP] business (Worker 1) recorded")
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

    with tempfile.TemporaryDirectory() as d:     # outcome label of a profile-signal login follows rc, not the member jar
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 60, "business", GUEST_TAIL))
        need = EA.needs_login(EVENING)
        res_bad = EA.force_logins(lambda cmd, env_extra=None, timeout=0: (1, ""), need, {}, 6, False, EVENING)
        check("label_profile_login_rc1_not_logged_in", res_bad["business"].startswith("STILL NOT LOGGED IN"), res_bad)
        EA.STATE.unlink()       # 1019 hardening: the cap now re-reads the state file, so forget the first call's stamp
        res_ok = EA.force_logins(lambda cmd, env_extra=None, timeout=0: (0, ""), need, {}, 6, False, EVENING)
        check("label_profile_login_rc0_logged_in", res_ok["business"].startswith("LOGGED IN"), res_ok)

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

    _hardening_1019(three)
    _fix_round_1019(three)

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


def _hardening_1019(three):
    """guestprof-v2 (C-1019): A overlap race, B cap fails open, C signal outlives a success, D [FLAGS] line."""
    set_flag("1")

    # ---- A: evening holds a stale in-memory state; the exit-87 call stamps the same account meanwhile ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))

        def other_process_stamps():
            EA.save_json(EA.STATE, {"last_forced_login": {"business": EVENING - 30}, "other_key": 7})
        f = RaceFake(t, running=True, age_h=3.0, on_stop=other_process_stamps)
        rc = EA.evening(T.A(), run=f, now=EVENING)
        check("A_evening_stale_state_second_force_blocked", not f.has("relogin_one.py"), f.cmds)
        check("A_evening_stopped_then_started_anyway", f.has("bot_ctl.py stop") and f.has("bot_ctl.py start") and rc == 0, f.cmds)
        check("A_other_process_stamp_not_overwritten", stamps() == {"business": EVENING - 30}, stamps())
        check("A_other_keys_preserved", json.loads(EA.STATE.read_text()).get("other_key") == 7)
        check("A_skip_reason_logged", "skipped: forced login" in json.loads(EA.LAST.read_text())["logins"]["business"])

    with tempfile.TemporaryDirectory() as d:     # force_logins directly: stale {} memory, fresh stamp in the file
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL), (EVENING - 110, "primary", GUEST_TAIL))
        EA.save_json(EA.STATE, {"last_forced_login": {"business": EVENING - 600, "alt-1": EVENING - 99}})
        need = EA.needs_login(EVENING)
        calls = []
        res = EA.force_logins(lambda cmd, env_extra=None, timeout=0: calls.append(cmd) or (0, ""), need, {}, 6, False, EVENING)
        check("A_fresh_file_stamp_blocks_that_account", res["business"].startswith("skipped: forced login"), res)
        check("A_other_account_still_forced", len(calls) == 1 and calls[0][2] == "primary", calls)
        sm = stamps()
        check("A_merge_keeps_every_stamp", sm.get("business") == EVENING - 600 and sm.get("alt-1") == EVENING - 99
              and sm.get("primary") == EVENING, sm)
        # a stamp that appears between two accounts' decisions in the same call also blocks the second
        EA.save_json(EA.STATE, {})
        seen = []

        def run_then_stamp(cmd, env_extra=None, timeout=0):
            seen.append(cmd[2])
            if cmd[2] == "business":
                cur = json.loads(EA.STATE.read_text())
                cur["last_forced_login"]["primary"] = EVENING - 5
                EA.STATE.write_text(json.dumps(cur), encoding="utf-8")
            return 0, ""
        need2 = sorted([(a, p, "x") for a, p in EA.accounts() if a in ("business", "primary")], key=lambda n: n[0] != "business")
        res = EA.force_logins(run_then_stamp, need2, {}, 6, False, EVENING)
        check("A_stamp_between_decisions_blocks_later_account", seen == ["business"]
              and res["primary"].startswith("skipped: forced login"), (seen, res))

    with tempfile.TemporaryDirectory() as d:     # state file locked / unreadable at the decision
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        EA.STATE.write_text("{ not json", encoding="utf-8")
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        txt = EA.LOG.read_text(encoding="utf-8")
        check("A_unreadable_state_no_force", not f.has("relogin_one.py"), f.cmds)
        check("A_unreadable_state_log_line", "[LOGIN] business: skipped, state unreadable" in txt, txt)
        EA.STATE.unlink(); EA.STATE.mkdir()          # a directory: open() raises
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("A_state_is_directory_no_force", not f.has("relogin_one.py"), f.cmds)
        f = T.Fake(t, running=False)
        EA.evening(T.A(), run=f, now=EVENING)
        check("A_evening_unreadable_state_no_force_but_starts", not f.has("relogin_one.py") and f.has("bot_ctl.py start"), f.cmds)
        f = T.Fake(t, running=True, age_h=3.0)
        EA.evening(T.A(), run=f, now=EVENING)
        check("A_evening_unreadable_state_green_bot_left_alone", not f.has("stop") and not f.has("relogin"), f.cmds)

    # ---- B: the cap fails CLOSED when the stamp cannot be persisted ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        (t / "blocker").write_text("a file, so state/ cannot be created under it", encoding="utf-8")
        EA.STATE = t / "blocker" / "ea.json"
        forced = 0
        for i in range(4):                           # the old code forced a login on all 4 passes
            f = T.Fake(t, running=True)
            EA.exit87(T.A(mode="exit87"), run=f, now=EVENING + i * 600)
            forced += len(f.has("relogin_one.py"))
        txt = EA.LOG.read_text(encoding="utf-8")
        check("B_unwritable_state_4_passes_0_logins", forced == 0, forced)
        check("B_unwritable_state_logged", "[LOGIN] business: skipped, stamp not persisted" in txt, txt)
        f = T.Fake(t, running=True, age_h=3.0)
        EA.evening(T.A(), run=f, now=EVENING)
        check("B_evening_unwritable_state_no_login", not f.has("relogin_one.py"), f.cmds)
        check("B_evening_unwritable_dir_no_stop_no_restart",
              not f.has("bot_ctl.py stop") and not f.has("bot_ctl.py start"), f.cmds)
        check("B_evening_unwritable_dir_logged",
              "[LOGIN] state/ensure_armed.json not writable -- not restarting for a forced login"
              in EA.LOG.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as d:     # os.replace fails (AV lock / read-only): same
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        real_replace = os.replace

        def boom(*a, **k):
            raise PermissionError(13, "locked")
        os.replace = boom
        try:
            f = T.Fake(t, running=True)
            EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        finally:
            os.replace = real_replace
        check("B_os_replace_fails_no_login", not f.has("relogin_one.py"), f.cmds)
        os.replace = boom                            # read-only state file: running GREEN bot, EVENING, fresh GUEST label
        try:
            f = T.Fake(t, running=True, age_h=3.0)
            EA.evening(T.A(), run=f, now=EVENING)
        finally:
            os.replace = real_replace
        check("B_evening_readonly_file_no_login_no_stop",
              not f.has("relogin_one.py") and not f.has("bot_ctl.py stop") and not f.has("bot_ctl.py start"), f.cmds)
        EA.STATE.write_text("{}", encoding="utf-8")
        os.chmod(EA.STATE, 0o444)                    # a real read-only file (os.replace over it is refused on Windows)
        try:
            f = T.Fake(t, running=True, age_h=3.0)
            EA.evening(T.A(), run=f, now=EVENING)
        finally:
            os.chmod(EA.STATE, 0o666)
        ro_blocked = not f.has("bot_ctl.py stop")
        check("B_evening_real_readonly_file_no_stop_or_(replace_allowed)", ro_blocked or os.name != "nt", f.cmds)
        f = T.Fake(t, running=True, age_h=3.0)       # writable again: the same state DOES stop + login (the probe is not a blanket block)
        EA.evening(T.A(), run=f, now=EVENING)
        check("B_evening_writable_state_still_stops_for_login",
              f.has("bot_ctl.py stop") and f.has("relogin_one.py"), f.cmds)

    with tempfile.TemporaryDirectory() as d:     # a write that "succeeds" but is not there on read-back
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        real = EA.save_json_strict
        EA.save_json_strict = lambda p, data: True
        try:
            f = T.Fake(t, running=True)
            EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        finally:
            EA.save_json_strict = real
        check("B_readback_mismatch_no_login", not f.has("relogin_one.py"), f.cmds)
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("B_writable_again_logs_in_and_stamps", bool(f.has("relogin_one.py business")) and stamps().get("business") == EVENING, (f.cmds, stamps()))

    with tempfile.TemporaryDirectory() as d:     # a login that happened OUTSIDE ensure_armed counts toward the cap
        t = Path(d); T.setup(t, three)
        raw_relogin(t,
                    f"{iso(EVENING - 3600)} [ACCOUNT] business: attempt 1/3",
                    f"{iso(EVENING - 3500)} [RESULT] business: LOGGED IN ✅",
                    f"{iso(EVENING - 100)} [RESULT] business: " + GUEST_TAIL)
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("B_external_attempt_in_gap_counts", not f.has("relogin_one.py"), f.cmds)
        res = json.loads(EA.LAST.read_text())["logins"]["business"]
        check("B_external_attempt_reason", res.startswith("skipped: forced login 1.0 h ago") and "relogin.log attempt" in res, res)
        check("B_external_attempt_no_stamp_written", not (stamps() or {}).get("business"), stamps())
        f = T.Fake(t, running=True, age_h=3.0)
        EA.evening(T.A(), run=f, now=EVENING)
        check("B_external_attempt_evening_no_restart_for_it", not f.has("stop") and not f.has("relogin"), f.cmds)
        # older than the gap -> does not count
        raw_relogin(t, f"{iso(EVENING - 7 * 3600)} [ACCOUNT] business: attempt 1/3",
                    f"{iso(EVENING - 100)} [RESULT] business: " + GUEST_TAIL)
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("B_external_attempt_7h_old_does_not_count", bool(f.has("relogin_one.py business")), f.cmds)
        # other account / attempt 2/3 only / a prefix account name -> does not count
        EA.save_json(EA.STATE, {})
        raw_relogin(t, f"{iso(EVENING - 3600)} [ACCOUNT] business2: attempt 1/3",
                    f"{iso(EVENING - 3600)} [ACCOUNT] primary: attempt 1/3",
                    f"{iso(EVENING - 3000)} [ACCOUNT] business: attempt 2/3",
                    f"{iso(EVENING - 100)} [RESULT] business: " + GUEST_TAIL)
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("B_external_attempt_other_shapes_do_not_count", bool(f.has("relogin_one.py business")), f.cmds)
        # ... and a jar-guest account with the flag ON is capped by it too
        T.setup(t, {"primary": GUEST, "business": MEMBER})
        raw_relogin(t, f"{iso(EVENING - 1800)} [ACCOUNT] primary: attempt 1/3")
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("B_external_attempt_caps_jar_guest_flag_on", not f.has("relogin_one.py"), f.cmds)
        # flag OFF keeps the pre-1019 decision (the hardening is flag-gated, not silently armed)
        set_flag(None)
        EA.save_json(EA.STATE, {})
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("B_external_attempt_ignored_flag_off_head_behaviour", bool(f.has("relogin_one.py primary --force")), f.cmds)
        set_flag("1")

    # ---- C: a signal does not outlive a LATER success for that exact account ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, three)
        run_log(t, EVENING - 900, "[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded")
        check("C_baseline_boot_probe_15min_forces", ids(EA.needs_login(EVENING)) == ["primary"])
        raw_relogin(t, f"{iso(EVENING - 300)} [RESULT] primary: LOGGED IN ✅")
        check("C_newer_logged_in_voids_boot_probe", EA.needs_login(EVENING) == [])
        check("C_void_is_logged", "[LOGIN] primary: boot-probe signal void -- newer relogin.log success" in EA.LOG.read_text(encoding="utf-8"))
        raw_relogin(t, f"{iso(EVENING - 300)} [RESULT] primary: " + OK_TAIL)
        check("C_newer_already_logged_in_voids", EA.needs_login(EVENING) == [])
        raw_relogin(t, f"{iso(EVENING - 300)} [RESULT] primary: " + GUEST_TAIL)
        check("C_newer_guest_labelled_line_does_not_void", ids(EA.needs_login(EVENING)) == ["primary"])
        raw_relogin(t, f"{iso(EVENING - 1500)} [RESULT] primary: LOGGED IN ✅")
        check("C_older_success_does_not_void", ids(EA.needs_login(EVENING)) == ["primary"])
        raw_relogin(t, f"{iso(EVENING - 300)} [RESULT] business: LOGGED IN ✅",
                    f"{iso(EVENING - 300)} [RESULT] primary-2: LOGGED IN ✅")
        check("C_other_account_success_does_not_void", ids(EA.needs_login(EVENING)) == ["primary"])
        raw_relogin(t, f"{iso(EVENING - 300)} [RESULT] primary: NOT logged in ❌ (after 3 attempts)")
        check("C_failed_login_does_not_void", ids(EA.needs_login(EVENING)) == ["primary"])
        # exit87 end to end: the voided signal forces nothing
        raw_relogin(t, f"{iso(EVENING - 300)} [RESULT] primary: LOGGED IN ✅")
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("C_exit87_voided_signal_forces_nothing", not f.has("relogin_one.py"), f.cmds)
        # the label signal is voided by a success that is newer by TIMESTAMP even when it sits earlier in the file
        for q in (t / "logs" / "runs").glob("run_2026*.log"):
            q.unlink()
        raw_relogin(t, f"{iso(EVENING - 100)} [RESULT] primary: LOGGED IN ✅",
                    f"{iso(EVENING - 200)} [RESULT] primary: " + GUEST_TAIL)
        check("C_label_voided_by_newer_timestamp_out_of_file_order", EA.needs_login(EVENING) == [])
        # an unreadable relogin.log never raises and leaves the boot-probe signal standing
        run_log(t, EVENING - 900, "[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded")
        (t / "logs" / "relogin.log").unlink(); (t / "logs" / "relogin.log").mkdir()
        try:
            check("C_unreadable_relogin_keeps_boot_probe", ids(EA.needs_login(EVENING)) == ["primary"])
        except Exception as e:
            check("C_unreadable_relogin_keeps_boot_probe", False, repr(e))

    # ---- D: the [FLAGS] line is the first log line in both modes ----
    for mode in ("evening", "exit87"):
        for val, want in (("1", "=1 -> profile_signals=on"), (None, "=unset -> profile_signals=off"),
                          ("0", "=0 -> profile_signals=off"), ("true", "=true -> profile_signals=off"),
                          ("", "=(empty) -> profile_signals=off")):
            with tempfile.TemporaryDirectory() as d:
                t = Path(d); T.setup(t, three)
                set_flag(val)
                EA._FLAGS_LOGGED = False
                f = T.Fake(t, running=True, age_h=3.0)
                rc = EA.main(["--mode", mode], run=f)
                lines = EA.LOG.read_text(encoding="utf-8").splitlines()
                check(f"D_{mode}_{val!r}_flags_first", rc == 0 and bool(lines)
                      and lines[0].endswith("[FLAGS] ENSURE_ARMED_PROFILE_SIGNALS" + want), (rc, lines[:2]))
                check(f"D_{mode}_{val!r}_flags_once", sum("[FLAGS] ENSURE_ARMED_PROFILE_SIGNALS" in ln for ln in lines) == 1, lines)
    with tempfile.TemporaryDirectory() as d:     # even a disabled run starts with it
        t = Path(d); T.setup(t, three)
        set_flag("1"); EA._FLAGS_LOGGED = False
        EA.DISABLE.write_text("x", encoding="utf-8")
        rc = EA.main(["--mode", "exit87"], run=T.Fake(t))
        lines = EA.LOG.read_text(encoding="utf-8").splitlines()
        check("D_disabled_run_flags_first", rc == 2 and "[FLAGS]" in lines[0] and "[DISABLED]" in lines[-1], lines)   # 1019b: a second [FLAGS] line (STATUS_FAILCLOSED) now sits between
    EA._FLAGS_LOGGED = False
    set_flag("1")


def _fix_round_1019(three):
    """Fix round: (1) non-dict stamps never stop a bot it cannot restart, (2) flag-off decisions equal the
    pre-1019 ones, (3) persist_stamp refuses a younger stamp written between read and write, (4) evening
    decides from fresh facts after the boot_check follow."""
    jarg = {"primary": GUEST, "business": MEMBER, "alt-1": MEMBER}

    # ---- 1: list-valued stamps, a running GREEN bot, a jar-guest account ----
    for flag in (None, "1"):
        for bad in ('{"last_forced_login": ["primary"]}', '{"last_forced_login": "x"}', '{"last_forced_login": 5}'):
            with tempfile.TemporaryDirectory() as d:
                t = Path(d); T.setup(t, jarg)
                set_flag(flag)
                EA.STATE.write_text(bad, encoding="utf-8")
                f = T.Fake(t, running=True, age_h=3.0)
                rc = EA.main(["--mode", "evening"], run=f)
                stopped, started = bool(f.has("bot_ctl.py stop")), bool(f.has("bot_ctl.py start"))
                check(f"F1_{flag!r}_{bad[:30]}_never_stop_without_start", (not stopped) or started, f.cmds)
                if flag == "1":
                    check(f"F1_on_{bad[:30]}_green_bot_left_alone_no_login",
                          not stopped and not started and not f.has("relogin_one.py") and rc == 0, (rc, f.cmds))
                    f = T.Fake(t, running=True)
                    EA.main(["--mode", "exit87"], run=f)
                    check(f"F1_on_{bad[:30]}_exit87_no_login", not f.has("relogin_one.py"), f.cmds)
                    txt = EA.LOG.read_text(encoding="utf-8")
                    check(f"F1_on_{bad[:30]}_corrupt_logged",
                          "unreadable or corrupt" in txt and "skipped, state unreadable" in txt, txt)
    set_flag("1")
    check("F1_effective_prev_skips_non_dict",
          EA.effective_prev("primary", [["x"], None, "s", {"primary": 5}], 1000.0, 6)[0] == 5.0)
    with tempfile.TemporaryDirectory() as d:     # force_logins itself: a list `last` in the caller's state is replaced by {}
        t = Path(d); T.setup(t, jarg)
        calls = []
        need = [("primary", t / "target-0.json", "signed out: x")]
        res = EA.force_logins(lambda cmd, env_extra=None, timeout=0: calls.append(cmd) or (0, ""), need,
                              {"last_forced_login": ["primary"]}, 6, False, EVENING)
        check("F1_force_logins_list_last_no_crash", len(calls) == 1 and "primary" in res, (calls, res))

    # ---- 2: flag OFF == pre-1019 decisions (the hardening is flag-gated) ----
    set_flag(None)
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, jarg)
        EA.STATE.write_text("{ not json", encoding="utf-8")
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("F2_off_corrupt_state_still_forces", bool(f.has("relogin_one.py primary --force")), f.cmds)
        T.setup(t, jarg)
        (t / "blocker").write_text("x", encoding="utf-8")
        EA.STATE = t / "blocker" / "ea.json"
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("F2_off_unwritable_state_still_forces", bool(f.has("relogin_one.py primary --force")), f.cmds)
        T.setup(t, jarg)
        if EA.STATE.exists():
            EA.STATE.unlink()       # the corrupt-state case above re-stamped (flag off): start clean
        raw_relogin(t, f"{iso(EVENING - 7200)} [ACCOUNT] primary: attempt 1/3")
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING)
        check("F2_off_external_attempt_not_counted", bool(f.has("relogin_one.py primary --force")), f.cmds)
        # the own-stamp cap still works with the flag off
        f = T.Fake(t, running=True)
        EA.exit87(T.A(mode="exit87"), run=f, now=EVENING + 600)
        check("F2_off_own_stamp_cap_still_works", not f.has("relogin_one.py"), f.cmds)
        check("F2_off_no_lock_file_left", not Path(str(EA.STATE) + ".lock").exists())
    set_flag("1")

    # ---- 3: a stamp written between force_logins' strict read and persist_stamp blocks the login ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, jarg)
        real = EA.persist_stamp

        def stamp_then_persist(acct, ts, gap_h=None):
            EA.save_json(EA.STATE, {"last_forced_login": {acct: ts - 1}})
            return real(acct, ts, gap_h)
        EA.persist_stamp = stamp_then_persist
        try:
            calls = []
            need = [("primary", t / "target-0.json", "signed out: x")]
            res = EA.force_logins(lambda cmd, env_extra=None, timeout=0: calls.append(cmd) or (0, ""), need, {}, 6, False, EVENING)
        finally:
            EA.persist_stamp = real
        check("F3_stamp_between_read_and_persist_blocks_login", not calls and res["primary"].startswith("skipped"), (calls, res))
        check("F3_blocking_stamp_not_overwritten", stamps() == {"primary": EVENING - 1}, stamps())
        check("F3_persist_stamp_returns_blocked", real("primary", EVENING, 6) == "blocked")
        check("F3_persist_stamp_ok_after_gap", real("primary", EVENING + 7 * 3600, 6) == "ok")
        lock = Path(str(EA.STATE) + ".lock")
        check("F3_persist_stamp_no_lock_left", not lock.exists())
        # a lock held by another process -> fail closed; a stale lock is stolen
        lock.write_text("x", encoding="utf-8")
        orig = EA._StateLock.__init__.__defaults__
        EA._StateLock.__init__.__defaults__ = (None, 0.2)
        try:
            check("F3_held_lock_fails_closed", real("alt-1", EVENING, 6) == "fail")
            old = time.time() - 120
            os.utime(lock, (old, old))
            check("F3_stale_lock_is_stolen", real("alt-1", EVENING, 6) == "ok")
        finally:
            EA._StateLock.__init__.__defaults__ = orig

    # ---- 4: evening re-decides after the boot_check follow ----
    class FollowFake(T.Fake):
        def __init__(self, *a, on_follow=None, **kw):
            super().__init__(*a, **kw)
            self.on_follow = on_follow

        def __call__(self, cmd, env_extra=None, timeout=900):
            r = super().__call__(cmd, env_extra, timeout)
            if "--follow" in cmd and self.on_follow:
                self.on_follow()
            return r

    with tempfile.TemporaryDirectory() as d:     # another process stamps the account during the follow
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        f = FollowFake(t, running=True, age_h=3.0, verdicts=("PENDING", "GREEN"),
                       on_follow=lambda: EA.save_json(EA.STATE, {"last_forced_login": {"business": EVENING + 300}}))
        rc = EA.evening(T.A(), run=f, now=EVENING)
        check("F4_stamped_during_follow_green_bot_not_restarted",
              rc == 0 and not f.has("bot_ctl.py stop") and not f.has("bot_ctl.py start") and not f.has("relogin_one.py"), f.cmds)
        check("F4_result_records_no_action", json.loads(EA.LAST.read_text()).get("action") == "none")
    with tempfile.TemporaryDirectory() as d:     # the account is fixed (jar member again) during the follow
        t = Path(d); T.setup(t, {"primary": GUEST, "business": MEMBER, "alt-1": MEMBER})
        f = FollowFake(t, running=True, age_h=3.0, verdicts=("PENDING", "GREEN"),
                       on_follow=lambda: T.jar(t / "target-0.json", at="R", idt="R"))
        EA.evening(T.A(), run=f, now=EVENING)
        check("F4_fixed_during_follow_green_bot_not_restarted",
              not f.has("bot_ctl.py stop") and not f.has("relogin_one.py"), f.cmds)
    with tempfile.TemporaryDirectory() as d:     # control: nothing changes during the follow -> still restarts and logs in
        t = Path(d); T.setup(t, three)
        relogin_log(t, (EVENING - 120, "business", GUEST_TAIL))
        f = FollowFake(t, running=True, age_h=3.0, verdicts=("PENDING", "GREEN"))
        EA.evening(T.A(), run=f, now=EVENING)
        check("F4_control_no_change_still_restarts_and_logs_in",
              bool(f.has("bot_ctl.py stop")) and bool(f.has("relogin_one.py business")) and bool(f.has("bot_ctl.py start")), f.cmds)

    # ---- 4b: the forced login in evening uses a FRESH clock, not the start-of-run `now` ----
    # A boot-probe signal makes business need a login. During a followed boot (PENDING -> RED) the wrapper's
    # exit-87 branch runs relogin_one.py (an 'attempt 1/3' line written ~5 min into the follow). The restart must
    # count that attempt toward the 6 h cap: with the stale start-of-run `now` the line lies in the "future"
    # (> now + 60 s) of external_login_ts and a second typed login on the same account would be forced.
    from types import SimpleNamespace
    real_time, offset = EA.time, [0.0]
    EA.time = SimpleNamespace(time=lambda: real_time.time() + offset[0], sleep=lambda s: None)
    try:
        with tempfile.TemporaryDirectory() as d:
            t = Path(d); T.setup(t, three)
            run_log(t, EVENING - 120, "[SYSTEM] [BOOT_SKIP] business (Worker 1) recorded")

            def during_follow():
                with open(t / "logs" / "relogin.log", "a", encoding="utf-8") as fh:
                    fh.write(f"{iso(EVENING + 300)} [ACCOUNT] business: attempt 1/3\n")
                    fh.write(f"{iso(EVENING + 330)} [RESULT] business: NOT logged in (after 3 attempts)\n")
                offset[0] = 360.0

            class ClockFake(FollowFake):
                def __call__(self, cmd, env_extra=None, timeout=900):
                    r = super().__call__(cmd, env_extra, timeout)
                    if "bot_ctl.py start" in " ".join(map(str, cmd)):    # the restart's run log lives on the module clock
                        lg = max((t / "logs" / "runs").glob("run_*.log"), key=lambda q: q.stat().st_mtime)
                        os.utime(lg, (real_time.time() + offset[0], real_time.time() + offset[0]))
                    return r
            set_flag("1")
            f = ClockFake(t, running=True, age_h=3.0, verdicts=("PENDING", "RED"), on_follow=during_follow)
            rc = EA.evening(T.A(), run=f, now=EVENING)
            check("F4b_restarted_after_red_follow", bool(f.has("bot_ctl.py stop")) and bool(f.has("bot_ctl.py start")), f.cmds)
            check("F4b_attempt_during_follow_counts_no_second_login", not f.has("relogin_one.py"), f.cmds)
            check("F4b_login_logged_as_skipped", "skipped: forced login" in EA.LOG.read_text(encoding="utf-8")
                  and "relogin.log attempt" in EA.LOG.read_text(encoding="utf-8"),
                  EA.LOG.read_text(encoding="utf-8")[-600:])
    finally:
        EA.time = real_time


if __name__ == "__main__":
    sys.exit(main())
