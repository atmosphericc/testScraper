#!/usr/bin/env python3
"""Keep the bot up and logged in for every overnight drop window, with no hand steps.

Why (C-1014-10, 2026-10-14): 09-20 -> 10-14 the bot was buy-ready for 51.6% of the
00:00-06:00 CT hours -- 10 nights nothing ran (the previous run had been stopped and
nothing restarted it) and on 10-14 every launch exited 87 because accounts were GUEST
(relogin_one.py's validate-first pass cannot see a guest, C-1014-02).

    python tools/ops/ensure_armed.py                 # evening mode (the scheduled task)
    python tools/ops/ensure_armed.py --mode exit87   # from the wrapper's exit-87 branch
    python tools/ops/ensure_armed.py --dry-run [--json]

evening  (Windows Task Scheduler, 23:15 and 01:30 CT):
         bot up, younger than --max-age-h and the newest run's boot_check GREEN -> nothing.
         Otherwise: stop it (bot_ctl), give every GUEST / signed-out account ONE scripted
         forced login (the env that went 2/2 on 10-14), start it (bot_ctl), follow
         boot_check to GREEN / RED. A member account is never force-logged-in.
exit87   (run_bot_with_nightly_restart.bat, exit 87 branch; app.py has exited, the wrapper
         is still alive): the same rate-limited forced login for GUEST accounts only --
         never stops or starts anything.

Rate limit: one forced login per account per --login-gap-h (6 h) -- typed logins are
Shape-scored and a storm burns the account (~0/25 historically; 2/2 on 10-14).
Opt out for a night: create state/ensure_armed.disable (any content).
Log: logs/ensure_armed.log; last result: state/ensure_armed_last.json.
Exit codes: 0 ok / nothing to do, 1 the boot ended RED or a step failed, 2 disabled.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

STATE = ROOT / "state" / "ensure_armed.json"
LAST = ROOT / "state" / "ensure_armed_last.json"
DISABLE = ROOT / "state" / "ensure_armed.disable"
LOG = ROOT / "logs" / "ensure_armed.log"
RUNS = ROOT / "logs" / "runs"
# The scripted forced login that worked 2/2 on 2026-10-14 (CURRENT_STATE, AGENTIC section).
LOGIN_ENV = {"RELOGIN_SKIP_PROXY": "1", "TARGET_UA_MODE": "engine",
             "TARGET_FP_CHROMIUM": "0", "TARGET_JAR_GUEST_GUARD": "1"}
MEMBER_SUT = ("R", "M", "MEMBER")


def python_exe(root: Path | None = None) -> str:
    """The interpreter the wrapper uses (.venv, then venv), else this one."""
    root = ROOT if root is None else root
    for d in (".venv", "venv"):
        p = root / d / "Scripts" / "python.exe"
        if p.exists():
            return str(p)
    return sys.executable


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_json(p: Path, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(p: Path, data) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, p)
    except OSError:
        pass


# ── account state (zero browser; the same jar facts check_session_readiness reads) ──

def accounts(root: Path | None = None) -> list:
    """[(account_id, jar path)] for enabled accounts."""
    root = ROOT if root is None else root
    cfg = load_json(root / "config" / "target_accounts.json", {})
    out = [(a["account_id"], root / a["session_path"]) for a in cfg.get("accounts", [])
           if a.get("enabled", True) and a.get("account_id") and a.get("session_path")]
    return out or [("primary", root / "target.json"), ("business", root / "target-2.json"),
                   ("alt-1", root / "target-3.json")]


def _jwt_sut(value) -> str:
    from src.jar_guard import _claims
    c = _claims(value)
    return str(c.get("sut") or c.get("user_type") or "").upper()


def jar_state(path: Path, now: float | None = None) -> tuple:
    """(needs_login, why). needs_login only for a jar that cannot start a member session:
    missing / unreadable, no or expired login-session, or signed out (guest idToken and no
    member accessToken). A member whose 4 h token merely expired is NOT a login case --
    the wrapper's start pass re-mints it (CURRENT_STATE)."""
    now = time.time() if now is None else now
    data = load_json(path, None)
    if not isinstance(data, dict):
        return True, "jar missing or unreadable"
    cks = {c.get("name"): c for c in data.get("cookies", []) if isinstance(c, dict)}
    ls = cks.get("login-session")
    if not ls:
        return True, "login-session missing"
    try:
        exp = float(ls.get("expires") or 0)
    except (TypeError, ValueError):
        exp = 0.0
    if 0 < exp < now:
        return True, "login-session expired"
    at, idt = cks.get("accessToken"), cks.get("idToken")
    at_sut = _jwt_sut(at.get("value")) if at else ""
    id_sut = _jwt_sut(idt.get("value")) if idt else ""
    if at_sut in MEMBER_SUT or id_sut in MEMBER_SUT:
        return False, f"member (accessToken sut={at_sut or '-'}, idToken sut={id_sut or '-'})"
    if id_sut == "G" or at_sut == "G":
        return True, f"signed out: guest token (accessToken sut={at_sut or '-'}, idToken sut={id_sut or '-'})"
    return False, f"no member/guest token saved (sut {at_sut or '-'}/{id_sut or '-'}) -- start pass re-mints"


# ── the steps; `run` is injectable so the offline test never launches anything ──

def default_run(cmd: list, env_extra: dict | None = None, timeout: float = 900) -> tuple:
    env = dict(os.environ)
    env.update(env_extra or {})
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # The scheduled task runs under pythonw: give each helper a hidden console instead of
    # flashing a window (bot_ctl start still opens the wrapper's own console via explorer).
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        return 124, f"timeout after {timeout:.0f}s: {e}"
    except Exception as e:   # never let one step kill the night
        return 1, f"{type(e).__name__}: {e}"


def bot_status(run) -> dict:
    rc, out = run([python_exe(), "tools/ops/bot_ctl.py", "status", "--json"], timeout=60)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception:
        return {"running": None, "error": out[-300:]}


def newest_run_log(after: float = 0.0):
    try:
        logs = [p for p in RUNS.glob("run_*.log") if p.stat().st_mtime >= after]
    except OSError:
        return None
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def boot_verdict(run, log_path, follow: bool = False, deadline: int = 720) -> str:
    cmd = [python_exe(), "tools/predrop/boot_check.py", "--json", "--log", str(log_path)]
    if follow:
        cmd += ["--follow", "--deadline", str(deadline)]
    rc, out = run(cmd, timeout=deadline + 120 if follow else 120)
    return {0: "GREEN", 1: "RED"}.get(rc, "PENDING")


def force_logins(run, need: list, state: dict, gap_h: float, dry: bool, now: float) -> dict:
    """One scripted forced login per account that needs one, rate-limited. Returns
    {acct: outcome}. Re-reads the jar after each login."""
    res = {}
    last = state.setdefault("last_forced_login", {})
    for acct, path, why in need:
        prev = float(last.get(acct) or 0)
        if now - prev < gap_h * 3600:
            res[acct] = f"skipped: forced login {((now - prev) / 3600):.1f} h ago (< {gap_h:g} h)"
            log(f"[LOGIN] {acct}: {why} -- {res[acct]}")
            continue
        if dry:
            res[acct] = "dry-run: would force-login"
            log(f"[LOGIN] {acct}: {why} -- dry run, not logging in")
            continue
        log(f"[LOGIN] {acct}: {why} -- scripted forced login (home IP, engine UA)")
        last[acct] = now
        save_json(STATE, state)
        rc, out = run([python_exe(), "relogin_one.py", acct, "--force"], env_extra=LOGIN_ENV, timeout=600)
        ok, after = jar_state(path)
        res[acct] = ("LOGGED IN" if not ok else "STILL NOT LOGGED IN") + f" (rc={rc}; jar: {after})"
        log(f"[LOGIN] {acct}: {res[acct]}")
    return res


def in_drop_band(now: float) -> bool:
    """00:00-06:00 host time (CT): never restart a GREEN bot in here for age or a login."""
    return 0 <= datetime.fromtimestamp(now).hour < 6


def evening(args, run=default_run, now=None) -> int:
    now = time.time() if now is None else now
    state = load_json(STATE, {})
    result = {"mode": "evening", "at": datetime.fromtimestamp(now).isoformat(timespec="seconds")}
    st = bot_status(run)
    result["status_before"] = st
    need = [(a, p, why) for a, p in accounts() for nl, why in [jar_state(p, now)] if nl]
    result["need_login"] = {a: why for a, _, why in need}
    last = state.get("last_forced_login", {})
    eligible = [n for n in need if now - float(last.get(n[0]) or 0) >= args.login_gap_h * 3600]
    band = in_drop_band(now)
    running = bool(st.get("running"))
    age = st.get("age_h")
    if running:
        lg = newest_run_log()
        verdict = boot_verdict(run, lg) if lg else "PENDING"
        if verdict == "PENDING" and lg:      # a boot in progress: let it finish, never restart it
            verdict = boot_verdict(run, lg, follow=True)
        result["verdict_before"] = verdict
        reasons = []
        if verdict != "GREEN":
            reasons.append(f"newest run is {verdict}")
        if not band and (age or 0) >= args.max_age_h:
            reasons.append(f"up {age} h (>= {args.max_age_h:g} h; the <24 h rule)")
        if not band and eligible:
            reasons.append(f"accounts need a login: {[a for a, _, _ in eligible]}")
        if not reasons:
            note = (f"; NOT restarting for {sorted(result['need_login'])} "
                    f"({'inside the 00-06 drop band' if band else 'forced login rate-limited'})"
                    if need else "")
            log(f"[OK] bot up {age} h, newest run GREEN{note}")
            result["action"] = "none"
            save_json(LAST, result)
            return 0
        log(f"[RESTART] bot up {age} h: " + "; ".join(reasons))
    else:
        log(f"[START] bot not running (need login: {sorted(result['need_login']) or 'none'})")
    if args.dry_run:
        log("[DRY] would stop (if running), force-login the accounts above, start, follow boot_check")
        result["action"] = "dry-run"
        save_json(LAST, result)
        return 0
    if running:
        rc, out = run([python_exe(), "tools/ops/bot_ctl.py", "stop"], timeout=180)
        log(f"[STOP] bot_ctl stop rc={rc}")
        if rc != 0:
            result["action"] = "stop-failed"
            save_json(LAST, result)
            return 1
    if need:
        result["logins"] = force_logins(run, need, state, args.login_gap_h, False, now)
        save_json(STATE, state)
    t_start = time.time()
    rc, out = run([python_exe(), "tools/ops/bot_ctl.py", "start"], timeout=180)
    log(f"[START] bot_ctl start rc={rc}")
    if rc != 0:
        result["action"] = "start-failed"
        save_json(LAST, result)
        return 1
    lg = None
    for _ in range(40):                      # the run log opens after the wrapper's ~45 s login pass
        lg = newest_run_log(after=t_start - 5)
        if lg:
            break
        time.sleep(15)
    if not lg:
        log("[BOOT] no new run log within 10 min -- RED")
        result.update(action="started", verdict="RED (no run log)")
        save_json(LAST, result)
        return 1
    verdict = boot_verdict(run, lg, follow=True)
    log(f"[BOOT] {lg.name}: boot_check {verdict}")
    result.update(action="started", run_log=lg.name, verdict=verdict)
    save_json(LAST, result)
    return 0 if verdict == "GREEN" else 1


def exit87(args, run=default_run, now=None) -> int:
    now = time.time() if now is None else now
    state = load_json(STATE, {})
    need = [(a, p, why) for a, p in accounts() for nl, why in [jar_state(p, now)] if nl]
    result = {"mode": "exit87", "at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
              "need_login": {a: why for a, _, why in need}}
    if not need:
        log("[EXIT87] every jar holds a member or re-mintable session -- nothing to force "
            "(the wrapper's relogin pass runs next)")
    else:
        result["logins"] = force_logins(run, need, state, args.login_gap_h, args.dry_run, now)
        save_json(STATE, state)
    save_json(LAST, result)
    return 0


def disabled_now(now=None) -> tuple:
    """(disabled, note). The opt-out file state/ensure_armed.disable keeps the bot down:
    empty / any text = until the file is removed (as before); a first line
    'until YYYY-MM-DDTHH:MM' (local time) = only until then -- after that the file is
    deleted and the run proceeds, so a one-night opt-out cannot outlive the night it
    was meant for (2026-10-15: bot stopped overnight, the 23:15 run must still start it)."""
    now = time.time() if now is None else now
    if not DISABLE.exists():
        return False, ""
    try:
        txt = DISABLE.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        txt = ""
    m = re.match(r"until\s+(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})", txt)
    if not m:
        return True, "disable file present (no expiry)"
    try:
        until = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}").timestamp()
    except ValueError:
        return True, f"disable file present (unreadable expiry {txt[:40]!r})"
    if now >= until:
        try:
            DISABLE.unlink()
        except Exception:
            pass
        return False, f"disable file expired ({m.group(1)} {m.group(2)}:{m.group(3)}) -- removed"
    return True, f"disabled until {m.group(1)} {m.group(2)}:{m.group(3)}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("evening", "exit87"), default="evening")
    ap.add_argument("--max-age-h", type=float, default=20.0)
    ap.add_argument("--login-gap-h", type=float, default=6.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    _dis, _note = disabled_now()
    if _dis:
        log(f"[DISABLED] {DISABLE}: {_note} -- not touching the bot")
        return 2
    if _note:
        log(f"[ENABLED] {_note}")
    try:
        return exit87(a) if a.mode == "exit87" else evening(a)
    except Exception as e:   # a scheduled task must leave a trace, never a silent death
        log(f"[ERROR] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    # 2026-10-16 (C-1016-06): record the home public IP on every scheduled run (full IP only in
    # the gitignored state/home_ip_history.log; masked here). Script runs only - tests import main().
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import home_ip as _home_ip
        log(_home_ip.record("ensure_armed"))
    except Exception as _e:
        try:
            log(f"[HOME_IP] skipped ({type(_e).__name__})")
        except Exception:
            pass
    sys.exit(main())
