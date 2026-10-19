#!/usr/bin/env python3
"""Offline test for the 2026-10-19 ensure_armed money-safety flags (build lane easafe):

  ENSURE_ARMED_STATUS_FAILCLOSED (M-1019-01): "bot_ctl status" gives no usable answer (rc 124 timeout,
      non-JSON) -> today that reads as "bot not running" -> forced logins -> bot_ctl start, i.e. a RUNNING
      bot can be double-started / its accounts force-logged-in. Flag on: log "[STATUS] ...", action
      "status-unknown", return 1, nothing else runs.
  ENSURE_ARMED_ROWLOG: a non-GREEN boot_verdict logs one "[BOOTROWS] ..." line naming the FAIL rows.

Every subprocess is faked (the Fake runner of test_ensure_armed_1014): nothing launches, nothing logs in.
The flag-off differential loads HEAD's module (3278ce7f, the base of this lane) via `git show` and compares the
recorded command sequences over a scenario matrix.

    python tests/test_ensure_armed_money_1019.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "ops"))
import test_ensure_armed_1014 as T   # noqa: E402  (Fake, setup, A, jar, MEMBER, GUEST; no side effects on import)
import ensure_armed as EA            # noqa: E402

EVENING, IN_BAND, MEMBER, GUEST = T.EVENING, T.IN_BAND, T.MEMBER, T.GUEST
BASE_COMMIT = "3278ce7f"
FLAG_VARS = ("ENSURE_ARMED_STATUS_FAILCLOSED", "ENSURE_ARMED_ROWLOG", "ENSURE_ARMED_PROFILE_SIGNALS")
NL = chr(10)
RUN_N = re.compile(r"run_\d+\.log")      # the Fake names a new run log by its clock
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def set_env(**kw):
    """Set / clear the three flag variables (None clears). Unlisted ones are cleared."""
    for v in FLAG_VARS:
        os.environ.pop(v, None)
    for k, v in kw.items():
        if v is not None:
            os.environ[k] = v


class StatusFake(T.Fake):
    """Fake whose `bot_ctl status` can be broken: mode 'ok' | 'garbage' | 'timeout' | 'empty'.
    stop_rc != 0 makes `bot_ctl stop` fail. boot_check prints `boot_out` (a JSON string) when given."""

    def __init__(self, root, status="ok", stop_rc=0, boot_out=None, **kw):
        super().__init__(root, **kw)
        self.status, self.stop_rc, self.boot_out = status, stop_rc, boot_out

    def __call__(self, cmd, env_extra=None, timeout=900):
        s = " ".join(map(str, cmd))
        if "bot_ctl.py status" in s:
            self.cmds.append((s, dict(env_extra or {})))
            if self.status == "garbage":
                return 0, "garbage"
            if self.status == "timeout":
                return 124, "timeout after 60s: Command '[...]' timed out after 60 seconds"
            if self.status == "empty":
                return 0, ""
            return 0, json.dumps({"running": self.running, "age_h": self.age_h if self.running else None})
        if "bot_ctl.py stop" in s and self.stop_rc != 0:
            self.cmds.append((s, dict(env_extra or {})))
            return self.stop_rc, "stop failed"
        if "boot_check.py" in s and self.boot_out is not None:
            self.cmds.append((s, dict(env_extra or {})))
            v = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
            return {"GREEN": 0, "RED": 1}.get(v, 2), self.boot_out
        return super().__call__(cmd, env_extra, timeout)


def norm(f, tmp):
    """The recorded commands with the interpreter and the temp root removed (so two runs compare)."""
    py = EA.python_exe()
    out = []
    for c, e in f.cmds:
        c = RUN_N.sub("run_N.log", c.replace(py, "PY").replace(str(tmp), "TMP").replace(str(ROOT), "REPO"))
        out.append((c, tuple(sorted(e.items()))))
    return out


def short(f, tmp):
    return [c.replace(EA.python_exe(), "PY").replace(str(tmp), "TMP") for c, _ in norm(f, tmp)]


def log_text():
    try:
        return EA.LOG.read_text(encoding="utf-8")
    except OSError:
        return ""


def last():
    return json.loads(EA.LAST.read_text(encoding="utf-8"))


def main():
    saved = {v: os.environ.get(v) for v in FLAG_VARS}
    try:
        _main()
    finally:
        for v, x in saved.items():
            if x is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = x
    print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for n in FAILED:
        print(f"  FAIL: {n}")
    return 1 if FAILED else 0


def _main():
    # ---- the flags parse defensively: only exactly "1" is on ----
    for v, want in ((None, False), ("0", False), ("", False), ("true", False), (" 1", False), ("1 ", False),
                    ("11", False), ("yes", False), ("1", True)):
        set_env(ENSURE_ARMED_STATUS_FAILCLOSED=v, ENSURE_ARMED_ROWLOG=v)
        check(f"flag_parse_failclosed_{v!r}", EA.status_failclosed_on() is want)
        check(f"flag_parse_rowlog_{v!r}", EA.rowlog_on() is want)

    # ---- [FLAGS] lines: the original first line is untouched, one new one follows it ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, {"primary": MEMBER})
        set_env(ENSURE_ARMED_STATUS_FAILCLOSED="1", ENSURE_ARMED_PROFILE_SIGNALS="1")
        EA._FLAGS_LOGGED = False
        EA.log_flags()
        lines = [ln.split("] ", 1)[1] for ln in log_text().splitlines() if "[FLAGS]" in ln]
        check("flags_first_line_unchanged",
              lines and lines[0] == "[FLAGS] ENSURE_ARMED_PROFILE_SIGNALS=1 -> profile_signals=on", lines)
        check("flags_second_line_failclosed",
              len(lines) >= 2 and lines[1] == "[FLAGS] ENSURE_ARMED_STATUS_FAILCLOSED=1 -> status_failclosed=on", lines)
        check("flags_exactly_two_lines", len(lines) == 2, lines)
        set_env()
        EA._FLAGS_LOGGED = False
        EA.LOG.unlink()
        EA.log_flags()
        lines = [ln.split("] ", 1)[1] for ln in log_text().splitlines() if "[FLAGS]" in ln]
        check("flags_all_unset", lines == [
            "[FLAGS] ENSURE_ARMED_PROFILE_SIGNALS=unset -> profile_signals=off",
            "[FLAGS] ENSURE_ARMED_STATUS_FAILCLOSED=unset -> status_failclosed=off"], lines)

    # ---- (a) status unusable + an eligible GUEST + bot "not running" (really unknown) ----
    for mode in ("garbage", "timeout", "empty"):
        for flag in (None, "1"):
            with tempfile.TemporaryDirectory() as d:
                t = Path(d); T.setup(t, {"primary": MEMBER, "business": GUEST})
                set_env(ENSURE_ARMED_STATUS_FAILCLOSED=flag)
                f = StatusFake(t, status=mode, running=False)
                rc = EA.evening(T.A(), run=f, now=EVENING)
                txt = log_text()
                tag = f"status_{mode}_flag_{flag}"
                if flag is None:
                    # today: unknown status -> "not running" -> forced login -> start -> follow
                    seq = short(f, t)
                    check(f"{tag}_today_sequence",
                          len(seq) == 4 and seq[0].endswith("bot_ctl.py status --json")
                          and seq[1].endswith("relogin_one.py business --force")
                          and seq[2].endswith("bot_ctl.py start") and "boot_check.py" in seq[3], seq)
                    check(f"{tag}_no_status_line", "[STATUS]" not in txt, txt)
                    check(f"{tag}_rc0_started", rc == 0 and last().get("action") == "started", (rc, last()))
                else:
                    seq = short(f, t)
                    check(f"{tag}_only_status_ran", len(seq) == 1 and seq[0].endswith("bot_ctl.py status --json"), seq)
                    check(f"{tag}_zero_relogin_zero_start", not f.has("relogin_one.py") and not f.has("bot_ctl.py start")
                          and not f.has("bot_ctl.py stop") and not f.has("boot_check"), f.cmds)
                    check(f"{tag}_status_line",
                          "[STATUS] bot_ctl status failed -- not touching the bot or any login this run:" in txt, txt)
                    check(f"{tag}_rc1_last_action", rc == 1 and last().get("action") == "status-unknown", (rc, last()))
                    check(f"{tag}_no_stamp_written", not EA.STATE.exists(), EA.STATE.read_text() if EA.STATE.exists() else "")

    # the timeout text is the tail of what bot_status saw, bounded to 200 chars
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, {"primary": MEMBER, "business": GUEST})
        set_env(ENSURE_ARMED_STATUS_FAILCLOSED="1")
        f = StatusFake(t, status="timeout")
        EA.evening(T.A(), run=f, now=EVENING)
        line = next(ln for ln in log_text().splitlines() if "[STATUS]" in ln)
        tail = line.split("this run: ", 1)[1]
        check("status_line_tail_has_timeout_text", "timeout after 60s" in tail, line)
        check("status_line_tail_le_200", len(tail) <= 200, len(tail))

    # a flag on with a USABLE status (running false / true) changes nothing
    for running in (False, True):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d); T.setup(t, {"primary": MEMBER, "business": GUEST})
            set_env()
            f0 = StatusFake(t, status="ok", running=running)
            rc0 = EA.evening(T.A(), run=f0, now=EVENING)
            seq0 = norm(f0, t)
            T.setup(t, {"primary": MEMBER, "business": GUEST})     # reset jars + state
            for p in (EA.STATE, EA.LAST):
                p.unlink(missing_ok=True)
            set_env(ENSURE_ARMED_STATUS_FAILCLOSED="1")
            f1 = StatusFake(t, status="ok", running=running)
            rc1 = EA.evening(T.A(), run=f1, now=EVENING)
            check(f"flag_on_usable_status_running_{running}_same", rc0 == rc1 and seq0 == norm(f1, t), (seq0, norm(f1, t)))
            check(f"flag_on_usable_status_running_{running}_no_status_line", "[STATUS]" not in log_text())

    # ---- (b) 01:30 local (inside the band), bot running, newest run RED -> stop then start (today) ----
    for flag in (None, "1"):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d); T.setup(t, {"primary": MEMBER, "business": MEMBER})
            set_env(ENSURE_ARMED_STATUS_FAILCLOSED=flag)
            f = StatusFake(t, status="ok", running=True, age_h=2.0, verdicts=("RED", "GREEN"))
            rc = EA.evening(T.A(), run=f, now=IN_BAND)
            seq = short(f, t)
            check(f"in_band_red_restarts_today_flag_{flag}",
                  rc == 0 and len(seq) == 5 and seq[0].endswith("bot_ctl.py status --json")
                  and "boot_check.py" in seq[1] and "--follow" not in seq[1]
                  and seq[2].endswith("bot_ctl.py stop") and seq[3].endswith("bot_ctl.py start")
                  and "boot_check.py" in seq[4] and "--follow" in seq[4] and not f.has("relogin_one.py"), seq)

    # ---- (c) bot running, `bot_ctl stop` rc != 0 -> no relogin, no start, rc 1 (today's guard) ----
    for flag in (None, "1"):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d); T.setup(t, {"primary": MEMBER, "business": GUEST})
            set_env(ENSURE_ARMED_STATUS_FAILCLOSED=flag)
            f = StatusFake(t, status="ok", running=True, age_h=2.0, verdicts=("RED",), stop_rc=1)
            rc = EA.evening(T.A(), run=f, now=EVENING)
            check(f"stop_failed_guard_flag_{flag}",
                  rc == 1 and not f.has("relogin_one.py") and not f.has("bot_ctl.py start")
                  and f.has("bot_ctl.py stop") and last().get("action") == "stop-failed", (rc, f.cmds, last()))

    # ---- (d) ROWLOG ----
    boot_json = json.dumps({"verdict": "RED", "log": "x.log", "boot": "t", "age_s": 5, "lines": 9, "rows": [
        {"id": "wrapper_login_pass", "status": "OK", "detail": "d", "line": None, "t": None},
        {"id": "worker_pool", "status": "OK", "detail": "d", "line": 3, "t": 1.0},
        {"id": "login_check", "status": "FAIL", "detail": "d", "line": 4, "t": 2.0},
        {"id": "pool_ready", "status": "WARN", "detail": "d", "line": 5, "t": 3.0},
        {"id": "first_stats", "status": "FAIL", "detail": "d", "line": 6, "t": 4.0},
        {"id": "relogin:primary", "status": "FAIL", "detail": "d", "line": None, "t": None},
        {"id": "dashboard", "status": "WARN", "detail": "d", "line": None, "t": None}]}, indent=1)
    n_markers = len(EA._boot_marker_ids())
    check("marker_ids_read_from_boot_check", n_markers == 9, n_markers)
    for flag in (None, "1", "0", "true"):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d); T.setup(t, {"primary": MEMBER})
            set_env(ENSURE_ARMED_ROWLOG=flag)
            f = StatusFake(t, status="ok", running=False, verdicts=("RED",), boot_out=boot_json)
            rc = EA.evening(T.A(), run=f, now=EVENING)
            txt = log_text()
            if flag == "1":
                check("rowlog_on_line_exact",
                      f"[BOOTROWS] RED fails=['login_check', 'first_stats', 'relogin:primary'] warns=2 buy_markers=1/{n_markers}" in txt, txt)
                check("rowlog_on_once", txt.count("[BOOTROWS]") == 1, txt)
            else:
                check(f"rowlog_off_{flag!r}_no_line", "[BOOTROWS]" not in txt, txt)
            check(f"rowlog_{flag!r}_verdict_red_rc1", rc == 1 and last().get("verdict") == "RED", (rc, last()))
    # the command line + timeout of boot_check are unchanged by the flag
    seqs = []
    for flag in (None, "1"):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d); T.setup(t, {"primary": MEMBER})
            set_env(ENSURE_ARMED_ROWLOG=flag)
            tos = []
            f = StatusFake(t, status="ok", running=False, verdicts=("RED",), boot_out=boot_json)

            def spy(cmd, env_extra=None, timeout=900, _f=f, _tos=tos):
                _tos.append(timeout)
                return _f.__class__.__call__(_f, cmd, env_extra, timeout)
            EA.evening(T.A(), run=spy, now=EVENING)
            seqs.append((norm(f, t), tos))
    check("rowlog_flag_does_not_change_commands_or_timeouts", seqs[0] == seqs[1], seqs)
    # `boot_check --follow --json` prints one object per poll: the LAST one names the rows
    pending_json = json.dumps({"verdict": "PENDING", "rows": [{"id": "pool_ready", "status": "PENDING"}]}, indent=1)
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, {"primary": MEMBER})
        set_env(ENSURE_ARMED_ROWLOG="1")
        v = EA.boot_verdict(lambda cmd, env_extra=None, timeout=0: (1, pending_json + NL + pending_json + NL + boot_json + NL),
                            t / "x.log", follow=True)
        check("rowlog_follow_multi_object_uses_last",
              v == "RED" and f"[BOOTROWS] RED fails=['login_check', 'first_stats', 'relogin:primary'] warns=2 buy_markers=1/{n_markers}" in log_text(),
              log_text())
    # a GREEN verdict never logs rows, even with the flag on
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, {"primary": MEMBER})
        set_env(ENSURE_ARMED_ROWLOG="1")
        f = StatusFake(t, status="ok", running=False, verdicts=("GREEN",), boot_out=boot_json)
        rc = EA.evening(T.A(), run=f, now=EVENING)
        check("rowlog_green_no_line", rc == 0 and "[BOOTROWS]" not in log_text(), log_text())
    # unparsable output (and a JSON that is not a boot_check result) -> fallback line, same verdict
    for name, out in (("text", "boot_check blew up"), ("empty", ""), ("json_list", "[1, 2]"),
                      ("json_norows", '{"verdict": "RED"}'), ("rows_not_list", '{"rows": 5}')):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d); T.setup(t, {"primary": MEMBER})
            set_env(ENSURE_ARMED_ROWLOG="1")
            f = StatusFake(t, status="ok", running=False, verdicts=("RED",), boot_out=out)
            rc = EA.evening(T.A(), run=f, now=EVENING)
            check(f"rowlog_unparsable_{name}",
                  "[BOOTROWS] RED (boot_check output unparsable)" in log_text() and rc == 1 and last().get("verdict") == "RED",
                  log_text())
    # a PENDING verdict is logged as PENDING, not RED, and is still returned as PENDING
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, {"primary": MEMBER})
        set_env(ENSURE_ARMED_ROWLOG="1")
        v = EA.boot_verdict(lambda cmd, env_extra=None, timeout=0: (2, boot_json), t / "x.log")
        check("rowlog_pending_verdict_returned", v == "PENDING" and "[BOOTROWS] PENDING fails=" in log_text(), (v, log_text()))
    # a log() that explodes can never change the verdict
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); T.setup(t, {"primary": MEMBER})
        set_env(ENSURE_ARMED_ROWLOG="1")
        real = EA.log
        EA.log = lambda m: (_ for _ in ()).throw(RuntimeError("log down"))
        try:
            v = EA.boot_verdict(lambda cmd, env_extra=None, timeout=0: (1, boot_json), t / "x.log")
        finally:
            EA.log = real
        check("rowlog_log_failure_swallowed", v == "RED", v)

    # ---- (e) flag-off differential against HEAD's module ----
    _differential()


def _load_head():
    """HEAD's (BASE_COMMIT) ensure_armed.py as a throw-away module, or None when git cannot produce it."""
    try:
        src = subprocess.run(["git", "show", f"{BASE_COMMIT}:tools/ops/ensure_armed.py"], cwd=str(ROOT),
                             capture_output=True, timeout=60, check=True).stdout
    except Exception as e:
        print(f"[SKIP] flag-off differential: cannot read {BASE_COMMIT}:tools/ops/ensure_armed.py ({e!r})")
        return None
    tmpd = tempfile.mkdtemp(prefix="ea_head_")
    p = Path(tmpd) / "tools" / "ops" / "ensure_armed_head.py"
    p.parent.mkdir(parents=True)
    p.write_bytes(src)
    spec = importlib.util.spec_from_file_location("ensure_armed_head", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _point(mod, tmp):
    mod.ROOT = tmp
    mod.STATE, mod.LAST, mod.DISABLE = tmp / "state" / "ea.json", tmp / "state" / "ea_last.json", tmp / "state" / "ea.disable"
    mod.LOG, mod.RUNS = tmp / "logs" / "ensure_armed.log", tmp / "logs" / "runs"


def _differential():
    head = _load_head()
    if head is None:
        return
    check("head_module_lacks_new_flags", not hasattr(head, "status_failclosed_on") and not hasattr(head, "rowlog_on"))
    cells = 0
    mismatches = []
    for status in ("ok", "garbage", "timeout"):
        for running in (True, False):
            for verdict in ("GREEN", "RED"):
                for need in (True, False):
                    for when_name, when in (("evening", EVENING), ("band", IN_BAND)):
                        results = []
                        for mod in (head, EA):
                            with tempfile.TemporaryDirectory() as d:
                                t = Path(d)
                                T.setup(t, {"primary": MEMBER, "business": GUEST if need else MEMBER})
                                _point(mod, t)
                                set_env()
                                f = StatusFake(t, status=status, running=running, age_h=3.0,
                                               verdicts=(verdict, "GREEN"))
                                try:
                                    rc = mod.evening(T.A(), run=f, now=when)
                                except Exception as e:     # an exception must also be identical
                                    rc = f"raise {type(e).__name__}"
                                try:
                                    lj = json.loads(mod.LAST.read_text(encoding="utf-8"))
                                    lj.pop("at", None)
                                    lj.pop("run_log", None)     # named by the Fake's clock
                                except OSError:
                                    lj = None
                                stamp = json.loads(mod.STATE.read_text()).get("last_forced_login", {}).keys() \
                                    if mod.STATE.exists() else None
                                # the interpreter path is module-independent (same ROOT-less lookup)
                                py = mod.python_exe()
                                cmds = [(RUN_N.sub("run_N.log", c.replace(py, "PY").replace(str(t), "TMP")), tuple(sorted(e.items())))
                                        for c, e in f.cmds]
                                results.append((rc, cmds, lj, sorted(stamp) if stamp is not None else None))
                        cells += 1
                        if results[0] != results[1]:
                            mismatches.append((status, running, verdict, need, when_name, results))
    check(f"differential_{cells}_cells_identical", not mismatches, mismatches[:1])
    # exit87 mode too
    for need in (True, False):
        results = []
        for mod in (head, EA):
            with tempfile.TemporaryDirectory() as d:
                t = Path(d)
                T.setup(t, {"primary": MEMBER, "business": GUEST if need else MEMBER})
                _point(mod, t)
                set_env()
                f = StatusFake(t, status="garbage", running=True)
                rc = mod.exit87(T.A(mode="exit87"), run=f, now=EVENING)
                results.append((rc, [RUN_N.sub("run_N.log", c.replace(mod.python_exe(), "PY").replace(str(t), "TMP")) for c, _ in f.cmds]))
        check(f"differential_exit87_need_{need}", results[0] == results[1], results)
    # restore EA's own paths for anything running after us in the same process
    _point(EA, ROOT)
    EA.LOG, EA.RUNS = ROOT / "logs" / "ensure_armed.log", ROOT / "logs" / "runs"


if __name__ == "__main__":
    sys.exit(main())
