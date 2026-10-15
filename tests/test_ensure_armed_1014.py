#!/usr/bin/env python3
"""Offline test for tools/ops/ensure_armed.py (2026-10-14, C-1014-10: the bot was buy-ready
for 51.6% of the drop hours). Every subprocess is faked: nothing launches, nothing logs in.

    python tests/test_ensure_armed_1014.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "ops"))
import ensure_armed as EA  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def jwt(sut):
    body = base64.urlsafe_b64encode(json.dumps({"sut": sut, "sub": "x", "eid": "e"}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def jar(path, ls="live", at=None, idt=None):
    cks = []
    if ls == "live":
        cks.append({"name": "login-session", "value": "v", "domain": ".target.com", "expires": time.time() + 86400 * 20})
    elif ls == "expired":
        cks.append({"name": "login-session", "value": "v", "domain": ".target.com", "expires": time.time() - 86400 * 365})  # 10-14: was -60, a time bomb vs the fixed EVENING clock once real time passed 23:16
    elif ls == "session":
        cks.append({"name": "login-session", "value": "v", "domain": ".target.com", "expires": -1})
    if at:
        cks.append({"name": "accessToken", "value": jwt(at), "domain": ".target.com"})
    if idt:
        cks.append({"name": "idToken", "value": jwt(idt), "domain": ".target.com"})
    Path(path).write_text(json.dumps({"cookies": cks, "saved_at": time.time()}), encoding="utf-8")


class Fake:
    """Scripted subprocess runner: records every command, never launches anything."""

    def __init__(self, root, running=False, age_h=3.0, verdicts=("GREEN",), login_fixes=True):
        self.root, self.cmds = root, []
        self.running, self.age_h = running, age_h
        self.verdicts = list(verdicts)
        self.login_fixes = login_fixes

    def __call__(self, cmd, env_extra=None, timeout=900):
        s = " ".join(map(str, cmd))
        self.cmds.append((s, dict(env_extra or {})))
        if "bot_ctl.py status" in s:
            return 0, json.dumps({"running": self.running, "age_h": self.age_h if self.running else None})
        if "bot_ctl.py stop" in s:
            self.running = False
            return 0, "stopped"
        if "bot_ctl.py start" in s:
            self.running = True
            log = self.root / "logs" / "runs" / f"run_{int(time.time() * 1000)}.log"
            log.write_text("boot", encoding="utf-8")
            return 0, "started"
        if "boot_check.py" in s:
            v = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
            return {"GREEN": 0, "RED": 1}.get(v, 2), v
        if "relogin_one.py" in s:
            acct = cmd[2]
            if self.login_fixes:
                for a, p in EA.accounts():
                    if a == acct:
                        jar(p, at="R", idt="R")
            return 0, "LOGGED IN"
        return 0, ""

    def has(self, frag):
        return [c for c, _ in self.cmds if frag in c]


def setup(tmp: Path, jars: dict):
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "logs" / "runs").mkdir(parents=True, exist_ok=True)
    accts = []
    for i, (a, spec) in enumerate(jars.items()):
        fn = f"target-{i}.json"
        accts.append({"account_id": a, "enabled": True, "session_path": fn})
        if spec is not None:
            jar(tmp / fn, **spec)
    (tmp / "config" / "target_accounts.json").write_text(json.dumps({"accounts": accts}), encoding="utf-8")
    (tmp / "logs" / "runs" / "run_old.log").write_text("old", encoding="utf-8")
    EA.ROOT = tmp
    EA.STATE, EA.LAST, EA.DISABLE = tmp / "state" / "ea.json", tmp / "state" / "ea_last.json", tmp / "state" / "ea.disable"
    EA.LOG, EA.RUNS = tmp / "logs" / "ensure_armed.log", tmp / "logs" / "runs"


class A:
    def __init__(self, **kw):
        self.mode, self.max_age_h, self.login_gap_h, self.dry_run = "evening", 20.0, 6.0, False
        self.__dict__.update(kw)


EVENING = datetime(2026, 10, 14, 23, 15).timestamp()   # outside the 00-06 drop band
IN_BAND = datetime(2026, 10, 15, 1, 30).timestamp()
MEMBER, GUEST = dict(at="R", idt="R"), dict(at=None, idt="G")


def main():
    # ---- jar classification ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        cases = {"member": (MEMBER, False), "guest": (GUEST, True), "no_ls": (dict(ls=None, at="R", idt="R"), True),
                 "ls_expired": (dict(ls="expired", at="R", idt="R"), True),
                 "ls_session_typed": (dict(ls="session", at="R", idt="R"), False),
                 "no_tokens": (dict(), False), "guest_at": (dict(at="G", idt="G"), True),
                 "member_id_only": (dict(at=None, idt="R"), False)}
        for name, (spec, want) in cases.items():
            jar(t / f"{name}.json", **spec)
            got, why = EA.jar_state(t / f"{name}.json")
            check(f"jar_{name}", got is want, why)
        check("jar_missing", EA.jar_state(t / "nope.json")[0] is True)

    # ---- evening: running GREEN, all member -> nothing ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER, "business": MEMBER})
        f = Fake(t, running=True, age_h=3.0)
        rc = EA.evening(A(), run=f, now=EVENING)
        check("ev_green_noop", rc == 0 and not f.has("stop") and not f.has("start") and not f.has("relogin"), f.cmds)

    # ---- evening: not running, one guest -> forced login with the proven env, start, follow ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER, "business": GUEST})
        f = Fake(t, running=False)
        rc = EA.evening(A(), run=f, now=EVENING)
        logins = [(c, e) for c, e in f.cmds if "relogin_one.py" in c]
        check("ev_down_logs_in_guest_only", len(logins) == 1 and "relogin_one.py business --force" in logins[0][0]
              and logins[0][1] == EA.LOGIN_ENV, logins)
        check("ev_down_starts_and_follows", rc == 0 and f.has("bot_ctl.py start") and f.has("--follow")
              and not f.has("bot_ctl.py stop"), f.cmds)
        last = json.loads(EA.LAST.read_text())
        check("ev_last_written", last.get("verdict") == "GREEN" and "business" in last.get("need_login", {}), last)
        check("ev_rate_stamp", "business" in json.loads(EA.STATE.read_text()).get("last_forced_login", {}))

    # ---- evening: never force-logs a member account ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER})
        f = Fake(t, running=False)
        EA.evening(A(), run=f, now=EVENING)
        check("ev_member_never_forced", not f.has("relogin_one.py"), f.cmds)

    # ---- evening: running but the newest run is RED (e.g. an exit-87 loop) -> restart ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": GUEST})
        f = Fake(t, running=True, verdicts=("RED", "GREEN"))
        rc = EA.evening(A(), run=f, now=IN_BAND)
        order = [c.split()[-1] if "bot_ctl" in c else "login" for c, _ in f.cmds if "bot_ctl.py s" in c or "relogin" in c]
        check("ev_red_restarts_even_in_band", f.has("bot_ctl.py stop") and f.has("relogin_one.py primary")
              and f.has("bot_ctl.py start") and rc == 0, f.cmds)
        i_stop = next(i for i, (c, _) in enumerate(f.cmds) if "bot_ctl.py stop" in c)
        i_login = next(i for i, (c, _) in enumerate(f.cmds) if "relogin_one.py" in c)
        i_start = next(i for i, (c, _) in enumerate(f.cmds) if "bot_ctl.py start" in c)
        check("ev_order_stop_login_start", i_stop < i_login < i_start, f.cmds)

    # ---- in the drop band a GREEN bot is never restarted for a guest or for age ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER, "business": GUEST})
        f = Fake(t, running=True, age_h=23.0)
        rc = EA.evening(A(), run=f, now=IN_BAND)
        check("ev_band_green_untouched", rc == 0 and not f.has("stop") and not f.has("relogin"), f.cmds)

    # ---- out of band: GREEN but a guest that may log in now -> restart with the login ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER, "business": GUEST})
        f = Fake(t, running=True, age_h=3.0)
        EA.evening(A(), run=f, now=EVENING)
        check("ev_green_guest_restart_out_of_band", f.has("bot_ctl.py stop") and f.has("relogin_one.py business"), f.cmds)
        # ... but if that account was force-logged-in 1 h ago, leave the GREEN bot alone
        f2 = Fake(t, running=True, age_h=3.0)
        jar(EA.accounts()[1][1], **GUEST)
        EA.save_json(EA.STATE, {"last_forced_login": {"business": EVENING - 3600}})
        EA.evening(A(), run=f2, now=EVENING)
        check("ev_rate_limited_no_restart", not f2.has("stop") and not f2.has("relogin"), f2.cmds)

    # ---- out of band: GREEN but older than 20 h -> restart (the <24 h rule) ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER})
        f = Fake(t, running=True, age_h=22.0)
        EA.evening(A(), run=f, now=EVENING)
        check("ev_age_restart", f.has("bot_ctl.py stop") and f.has("bot_ctl.py start") and not f.has("relogin"), f.cmds)

    # ---- a boot in progress (PENDING) is followed, never restarted ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER})
        f = Fake(t, running=True, age_h=0.05, verdicts=("PENDING", "GREEN"))
        rc = EA.evening(A(), run=f, now=EVENING)
        check("ev_pending_followed", rc == 0 and f.has("--follow") and not f.has("stop"), f.cmds)

    # ---- a boot that ends RED returns 1 ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER})
        f = Fake(t, running=False, verdicts=("RED",))
        check("ev_red_boot_rc1", EA.evening(A(), run=f, now=EVENING) == 1)

    # ---- dry run touches nothing ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": GUEST})
        f = Fake(t, running=False)
        rc = EA.evening(A(dry_run=True), run=f, now=EVENING)
        check("ev_dry_run", rc == 0 and not f.has("start") and not f.has("relogin"), f.cmds)

    # ---- exit87 mode: logs in guests only, never stops / starts, rate-limited ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": GUEST, "business": MEMBER, "alt-1": dict(ls="expired")})
        f = Fake(t, running=True)
        rc = EA.exit87(A(mode="exit87"), run=f, now=EVENING)
        check("x87_logs_in_needy", rc == 0 and f.has("relogin_one.py primary") and f.has("relogin_one.py alt-1")
              and not f.has("relogin_one.py business"), f.cmds)
        check("x87_never_bot_ctl", not f.has("bot_ctl"), f.cmds)
        f2 = Fake(t, running=True, login_fixes=False)
        jar(EA.accounts()[0][1], **GUEST)
        EA.exit87(A(mode="exit87"), run=f2, now=EVENING + 600)
        check("x87_rate_limited", not f2.has("relogin_one.py"), f2.cmds)
        EA.exit87(A(mode="exit87"), run=f2, now=EVENING + 7 * 3600)
        check("x87_after_gap_retries", f2.has("relogin_one.py primary"), f2.cmds)

    # ---- disable file ----
    with tempfile.TemporaryDirectory() as d:
        t = Path(d); setup(t, {"primary": MEMBER})
        EA.DISABLE.write_text("x")
        check("disabled_rc2", EA.main([]) == 2)

    # ---- the wrapper calls exit87 mode in its exit-87 branch, once, before relogin_one all ----
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("utf-8", "replace").split("\r\n")
    calls = [i for i, l in enumerate(bat) if "tools\\ops\\ensure_armed.py --mode exit87" in l]
    rel = [i for i, l in enumerate(bat) if l.strip().startswith('"%PYTHON%" relogin_one.py all')]
    x87 = next(i for i, l in enumerate(bat) if l.startswith('if "%EXITCODE%"=="87" ('))
    check("bat_exit87_hook", len(calls) == 1 and rel and x87 < calls[0] < rel[-1], (calls, rel, x87))

    print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for n in FAILED:
        print(f"  FAIL: {n}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
