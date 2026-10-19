#!/usr/bin/env python3
"""Offline replay + string-coupling pins for ENSURE_ARMED_PROFILE_SIGNALS (build lane easafe, 2026-10-19).

The profile-guest signals read two TEXT contracts that live in other files: the "GUEST (sut=G)" label that
relogin_one.py prints and the two BOOT_SKIP strings app.py prints. If either emitter is reworded the
signal silently dies and the 10-19 01:30 exit-87 loop comes back, so:

  (a) string coupling -- the label occurs verbatim in relogin_one.py; the two app.py emitter strings (read out
      of app.py itself, not retyped) are matched by ensure_armed._boot_probe_re(acct); the "failed too while a
      skip was in force" line is NOT.
  (b) frozen replay -- the real logs/relogin.log lines 2026-10-19T01:30:00..01:35:00 and the BOOT_SKIP lines of
      logs/runs/run_20261019_013059.log (tests/fixtures/ensure_armed_1019/, e-mails masked) replayed at the
      01:30:58 decision time: flag ON -> primary boot-probe, business relogin-label, alt-1 none; flag OFF -> no
      profile-signal account.
      The relogin.log is cut at the decision time (a replay must see only what existed then; the later pass
      lines are used for the separate 01:33:40 check). The run log's file name is 01:30:59 -- one second AFTER
      the decision -- and still counts through _recent()'s 60 s clock-skew allowance; that is the data as it
      was, not a test convenience.
  (c) mutation guard -- one altered character in either emitter string (or in the label) kills the match,
      proving the pins above bite.

    python tests/test_ensure_armed_replay_1019.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "ops"))
import test_ensure_armed_1014 as T   # noqa: E402
import ensure_armed as EA            # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "ensure_armed_1019"
RUN_FIX = FIX / "runs" / "run_20261019_013059.log"
DECISION = datetime(2026, 10, 19, 1, 30, 58).timestamp()
LATE = datetime(2026, 10, 19, 1, 33, 40).timestamp()
FLAG = "ENSURE_ARMED_PROFILE_SIGNALS"
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def set_flag(v):
    if v is None:
        os.environ.pop(FLAG, None)
    else:
        os.environ[FLAG] = v


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def emitters(app_text: str) -> dict:
    """The three app.py BOOT_SKIP format strings, with the account placeholder {_w1} left in."""
    pats = {
        "recorded": r'print\(f"(\[SYSTEM\] \[BOOT_SKIP\] \{_w1\} \(Worker 1\) recorded[^"]*)"',
        "probe": r'add_activity_log\(f"(\{_w1\} failed the boot login probe[^"]*)"',
        "cleared": r'print\(f"(\[SYSTEM\] \[BOOT_SKIP\] \{_w1\} failed too while a skip was in force[^"]*)"',
    }
    out = {}
    for k, rx in pats.items():
        m = re.search(rx, app_text)
        out[k] = m.group(1) if m else None
    return out


def build_root(tmp: Path, *, relogin_text=None, run_text=None, accounts=("primary", "business", "alt-1")):
    T.setup(tmp, {a: T.MEMBER for a in accounts})
    (tmp / "logs" / "runs").mkdir(parents=True, exist_ok=True)
    for p in (tmp / "logs" / "runs").glob("run_*.log"):
        p.unlink()
    (tmp / "logs" / "relogin.log").write_text(relogin_text if relogin_text is not None else read(FIX / "relogin.log"),
                                              encoding="utf-8")
    (tmp / "logs" / "runs" / RUN_FIX.name).write_text(run_text if run_text is not None else read(RUN_FIX),
                                                      encoding="utf-8")


def cut_at(text: str, ts: float) -> str:
    """The relogin.log lines that existed at `ts` (a replay must not see the future)."""
    keep = []
    for ln in text.splitlines():
        t = EA._parse_ts(ln)
        if t is None or t <= ts:
            keep.append(ln)
    return "\n".join(keep) + "\n"


def sources(now, accounts=("primary", "business", "alt-1")):
    return {a: EA.profile_guest_source(a, now) for a in accounts}


def mutate(s: str, needle: str) -> str:
    """Change exactly one character of `needle` inside s."""
    assert s.count(needle) == 1, (needle, s.count(needle))
    alt = needle[:-1] + ("X" if needle[-1] != "X" else "Y")
    return s.replace(needle, alt)


def main():
    saved = os.environ.get(FLAG)
    try:
        _main()
    finally:
        set_flag(saved)
    print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for n in FAILED:
        print(f"  FAIL: {n}")
    return 1 if FAILED else 0


def _main():
    # ---- (a) string coupling ----
    relogin_src = read(ROOT / "relogin_one.py")
    check("label_verbatim_in_relogin_one", EA.GUEST_LABEL in relogin_src, EA.GUEST_LABEL)
    app_text = read(ROOT / "app.py")
    em = emitters(app_text)
    check("app_emitters_found", all(em.values()), em)
    for acct in ("primary", "business", "alt-1"):
        rx = EA._boot_probe_re(acct)
        rec = em["recorded"].replace("{_w1}", acct)
        probe = em["probe"].replace("{_w1}", acct)
        cleared = em["cleared"].replace("{_w1}", acct)
        check(f"coupling_recorded_matches_{acct}", rx.search(rec) is not None, rec)
        check(f"coupling_probe_matches_{acct}", rx.search(probe) is not None, probe)
        check(f"coupling_cleared_NOT_matched_{acct}", rx.search(cleared) is None, cleared)
        # with the run-log time prefix the probe line is written with
        check(f"coupling_probe_with_time_prefix_{acct}", rx.search(f"[01:31:36] [SYSTEM] {probe}") is not None)
        # another account's lines never match this account
        other = "alt-10" if acct == "alt-1" else acct + "x"
        check(f"coupling_other_account_not_matched_{acct}",
              rx.search(em["recorded"].replace("{_w1}", other)) is None
              and rx.search(em["probe"].replace("{_w1}", other)) is None)
    # the fixture lines ARE the emitters' output (not a hand-written near copy)
    fx = read(RUN_FIX).splitlines()
    check("fixture_run_lines_match_emitters",
          EA._boot_probe_re("primary").search(fx[0]) is not None and EA._boot_probe_re("primary").search(fx[1]) is not None, fx)
    check("fixture_first_line_is_recorded_emitter",
          fx[0].startswith("[SYSTEM] [BOOT_SKIP] primary (Worker 1) recorded"), fx[0])

    # ---- fixtures hold no e-mail / secret-looking text ----
    blob = read(FIX / "relogin.log") + read(RUN_FIX)
    check("fixtures_no_email", re.search(r"[\w.*-]+@[\w-]+\.\w+", blob) is None)
    check("fixtures_masked_marker_present", "<masked>" in blob)
    check("fixtures_no_token_cookie_values",
          re.search(r"(?i)(password|passwd|cvv|secret)\s*[:=]|eyJ[\w-]{10,}|Bearer\s+\S{10,}", blob) is None)
    check("fixture_relogin_window", all(ln.startswith("2026-10-19T01:3") for ln in read(FIX / "relogin.log").splitlines()))

    # ---- (b) frozen replay at 01:30:58 ----
    rl_full = read(FIX / "relogin.log")
    rl_cut = cut_at(rl_full, DECISION)
    check("replay_cut_keeps_decision_window", "01:30:54 [RESULT] alt-1" in rl_cut and "01:31:46" not in rl_cut)
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        build_root(t, relogin_text=rl_cut)
        set_flag("1")
        s = sources(DECISION)
        check("replay_on_primary_boot_probe", s["primary"] == "boot-probe", s)
        check("replay_on_business_relogin_label", s["business"] == "relogin-label", s)
        check("replay_on_alt1_none", s["alt-1"] is None, s)
        need = EA.needs_login(DECISION)
        check("replay_on_needs_login_two_accounts",
              [(a, why.split(" (")[0]) for a, _, why in need] ==
              [("primary", "profile guest per boot-probe"), ("business", "profile guest per relogin-label")], need)
        # and the decision the scheduled task would have taken: bot down -> forced logins for exactly those two
        f = T.Fake(t, running=False)
        rc = EA.evening(T.A(), run=f, now=DECISION)
        logins = [c.split("relogin_one.py ")[1] for c in f.has("relogin_one.py")]
        check("replay_on_evening_forces_primary_and_business", logins == ["primary --force", "business --force"], (logins, rc))
        set_flag(None)
        check("replay_off_needs_login_empty", EA.needs_login(DECISION) == [], EA.needs_login(DECISION))
        set_flag("0")
        check("replay_flag0_needs_login_empty", EA.needs_login(DECISION) == [])
        set_flag(None)
        t2 = Path(tempfile.mkdtemp(prefix="ea_replay_off_"))
        try:
            build_root(t2, relogin_text=rl_cut)
            f = T.Fake(t2, running=False)
            EA.evening(T.A(), run=f, now=DECISION)
            check("replay_off_evening_no_login_but_starts", not f.has("relogin_one.py") and f.has("bot_ctl.py start"), f.cmds)
        finally:
            shutil.rmtree(t2, ignore_errors=True)

    # the later passes of the same wrapper loop (01:33:40): both profiles still read guest
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        build_root(t, relogin_text=cut_at(rl_full, LATE))
        set_flag("1")
        s = sources(LATE)
        check("late_on_primary_business_label_alt1_none",
              s == {"primary": "relogin-label", "business": "relogin-label", "alt-1": None}, s)
        set_flag(None)
        check("late_off_empty", EA.needs_login(LATE) == [])

    # ---- (c) mutation guard: one altered character kills the match ----
    set_flag("1")
    run_lines = read(RUN_FIX).splitlines()
    # each boot-probe line is independently sufficient ...
    for i, name in enumerate(("recorded", "probe")):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            build_root(t, relogin_text=rl_cut, run_text=run_lines[i] + "\n")
            check(f"mut_{name}_line_alone_matches", EA.profile_guest_source("primary", DECISION) == "boot-probe")
    # ... and altered by one character each no longer matches
    for i, (name, needle) in enumerate((("recorded", "(Worker 1) recorded"), ("probe", "failed the boot login probe"))):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            build_root(t, relogin_text=rl_cut, run_text=mutate(run_lines[i], needle) + "\n")
            check(f"mut_{name}_one_char_kills_match", EA.profile_guest_source("primary", DECISION) is None,
                  EA.profile_guest_source("primary", DECISION))
    # both altered -> primary falls back to no signal (its relogin.log line is a plain success)
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        build_root(t, relogin_text=rl_cut, run_text="\n".join([mutate(run_lines[0], "(Worker 1) recorded"),
                                                               mutate(run_lines[1], "failed the boot login probe")]) + "\n")
        check("mut_both_emitters_primary_none", sources(DECISION)["primary"] is None)
    # the label altered by one char: business loses its relogin-label signal
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        mutated = rl_cut.replace(EA.GUEST_LABEL, EA.GUEST_LABEL[:-1] + "X")
        check("mut_label_text_changed", mutated != rl_cut)
        build_root(t, relogin_text=mutated)
        check("mut_label_one_char_kills_business", EA.profile_guest_source("business", DECISION) is None)
    # the matcher side: altered sample strings from the real emitters do not match
    rx = EA._boot_probe_re("primary")
    check("mut_matcher_recorded", rx.search(mutate(em["recorded"].replace("{_w1}", "primary"), "(Worker 1) recorded")) is None)
    check("mut_matcher_probe", rx.search(mutate(em["probe"].replace("{_w1}", "primary"), "failed the boot login probe")) is None)
    # the label coupling itself: a relogin_one.py that dropped the label would fail the verbatim pin
    check("mut_label_pin_bites", EA.GUEST_LABEL[:-1] + "X" not in relogin_src)


if __name__ == "__main__":
    sys.exit(main())
