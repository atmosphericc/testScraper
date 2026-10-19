#!/usr/bin/env python3
"""Start / stop / restart the production bot the way the operator does, so an agent can
boot it (/pre-drop Step 9 at T-20; operator 2026-10-14: make /pre-drop boot the bot).

    python tools/ops/bot_ctl.py status [--json]
    python tools/ops/bot_ctl.py stop [--grace 30]
    python tools/ops/bot_ctl.py start
    python tools/ops/bot_ctl.py restart [--grace 30]

start    opens run_bot_with_nightly_restart.bat through explorer.exe -- what a double-click
         does: its own console window, the desktop's environment (not this shell's) and a
         process outside this shell, so it outlives the Claude session. It waits until the
         wrapper's cmd.exe appears and prints its PID. It does not wait for GREEN: that is
         `python tools/predrop/boot_check.py --follow`. A running bot makes start exit 3 (use
         restart); there is no second launch path, because two wrappers inside the ~45 s
         relogin pass would both get past the wrapper's single-instance guard.
stop     sends Ctrl+C to the wrapper's console (app.py's shutdown handler saves the session
         and closes Chrome; its deadman force-exits after 15 s), waits --grace seconds for
         app.py to go, then kills what is left: the wrapper, app.py and every Chrome on a bot
         profile (nodriver-profile / session_profiles / smoke_profiles -- an orphan holding
         the profile lock kills the next boot with 0xC0000005, 09-11). The Ctrl+C is never
         sent to a console this process or its parents share (that would hit Claude itself);
         a bot without its own console is killed instead.
restart  stop (when anything runs) + start.

Exit codes: 0 done, 1 failed, 3 start refused because a bot is running.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BAT = ROOT / "run_bot_with_nightly_restart.bat"
DASHBOARD_PORT = 5001
STAMP = ROOT / "state" / "last_bot_ctl_stop.json"
CMD_LOG = ROOT / "logs" / "bot_ctl.log"
BOT_PROFILE = re.compile(r"nodriver-profile|session_profiles|smoke_profiles", re.I)

# Runs as its own process: leave our console, join the target's, refuse if a protected
# PID (this tool or a parent) shares it, ignore the event ourselves, then raise CTRL_C_EVENT
# for every process on that console. Exit 0 sent, 3 attach failed, 4 send failed, 5 shared.
CTRL_C_HELPER = (
    "import ctypes, sys\n"
    "k = ctypes.windll.kernel32\n"
    "target = int(sys.argv[1])\n"
    "protect = {int(x) for x in sys.argv[2].split(',') if x}\n"
    "k.FreeConsole()\n"
    "if not k.AttachConsole(target):\n"
    "    sys.exit(3)\n"
    "buf = (ctypes.c_uint32 * 1024)()\n"
    "n = k.GetConsoleProcessList(buf, 1024)\n"
    "if protect & set(buf[:min(n, 1024)]):\n"
    "    k.FreeConsole()\n"
    "    sys.exit(5)\n"
    "k.SetConsoleCtrlHandler(None, True)\n"
    "sys.exit(0 if k.GenerateConsoleCtrlEvent(0, 0) else 4)\n"
)


def log(msg: str) -> None:
    print(f"[bot_ctl] {msg}", flush=True)


def classify(name, cmdline) -> "str | None":
    """'wrapper' / 'app' / 'chrome' for one process of the bot, else None."""
    n = (name or "").lower()
    parts = [str(x) for x in (cmdline or [])]
    joined = " ".join(parts)
    if n == "cmd.exe" and "run_bot_with_nightly_restart.bat" in joined.lower():
        return "wrapper"
    if n.startswith("python") and any(os.path.basename(x).lower() == "app.py" for x in parts):
        return "app"            # a venv bot is two of these: launcher + interpreter
    if n == "chrome.exe" and BOT_PROFILE.search(joined):
        return "chrome"
    return None


def find() -> dict:
    import psutil
    out = {"wrapper": [], "app": [], "chrome": []}
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            kind = classify(p.info.get("name"), p.info.get("cmdline"))
        except Exception:
            continue
        if kind:
            out[kind].append(p)
    return out


def port_open(port: int = DASHBOARD_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def status() -> dict:
    f = find()
    started = min((p.info.get("create_time") or 0 for p in f["app"] + f["wrapper"]), default=0)
    return {
        "running": bool(f["app"] or f["wrapper"]),
        "wrapper_pids": [p.pid for p in f["wrapper"]],
        "app_pids": [p.pid for p in f["app"]],
        "bot_chrome": len(f["chrome"]),
        "dashboard_up": port_open(),
        "started": datetime.fromtimestamp(started).isoformat(timespec="seconds") if started else None,
        "age_h": round((time.time() - started) / 3600, 2) if started else None,
    }


def _protected_pids() -> list:
    import psutil
    me = psutil.Process()
    return [me.pid] + [p.pid for p in me.parents()]


def send_ctrl_c(pid: int) -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    protect = ",".join(str(p) for p in _protected_pids())
    try:
        # 10-19: stamp the stop so app.py's TARGET_SIGINT_ATTRIB line can tell "bot_ctl stop" from an
        # unexplained console Ctrl+C. A failing write must never stop the signal.
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        tmp = STAMP.with_name(STAMP.name + ".tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "pid": pid, "argv": sys.argv, "by": "bot_ctl"}),
                       encoding="utf-8")
        os.replace(tmp, STAMP)
    except Exception:
        pass
    try:
        return subprocess.run([sys.executable, "-c", CTRL_C_HELPER, str(pid), protect],
                              creationflags=flags, timeout=15).returncode
    except Exception as e:
        log(f"Ctrl+C helper failed: {e}")
        return 4


def wait_gone(procs: list, timeout: float) -> list:
    import psutil
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    return alive


def kill_tree(p) -> None:
    try:
        kids = p.children(recursive=True)
    except Exception:
        kids = []
    for q in kids + [p]:
        try:
            q.kill()
        except Exception:
            pass


def stop(grace: float = 30.0) -> int:
    f = find()
    if not (f["wrapper"] or f["app"] or f["chrome"]):
        log("nothing running")
        return 0
    log(f"stopping: wrapper {[p.pid for p in f['wrapper']]}, app.py {[p.pid for p in f['app']]}, "
        f"bot Chrome x{len(f['chrome'])}")
    if f["wrapper"]:
        rc = send_ctrl_c(f["wrapper"][0].pid)
        log({0: "Ctrl+C sent to the wrapper console", 3: "could not attach to the wrapper console",
             4: "Ctrl+C send failed", 5: "the wrapper shares this console -- no Ctrl+C, killing instead"}
            .get(rc, f"Ctrl+C helper rc={rc}"))
        if rc == 0 and f["app"]:
            alive = wait_gone(f["app"], grace)
            log("app.py exited cleanly" if not alive else f"app.py still alive after {grace:.0f}s -- killing")
    for p in f["wrapper"] + f["app"]:
        kill_tree(p)
    time.sleep(1.0)
    for p in find()["chrome"]:
        kill_tree(p)
    time.sleep(1.0)
    left = find()
    if left["wrapper"] or left["app"] or left["chrome"]:
        log(f"still running after stop: wrapper {[p.pid for p in left['wrapper']]}, "
            f"app.py {[p.pid for p in left['app']]}, bot Chrome x{len(left['chrome'])}")
        return 1
    log("stopped")
    return 0


def start(wait_s: float = 30.0) -> int:
    s = status()
    if s["running"]:
        log(f"a bot is already running (wrapper {s['wrapper_pids']}, app.py {s['app_pids']}, "
            f"since {s['started']}) -- use restart")
        return 3
    if not BAT.exists():
        log(f"missing {BAT}")
        return 1
    subprocess.Popen(["explorer.exe", str(BAT)], close_fds=True)   # explorer exits 1 on success
    deadline = time.time() + wait_s
    while time.time() < deadline:
        w = find()["wrapper"]
        if w:
            log(f"wrapper started (pid {w[0].pid}) -- next: python tools/predrop/boot_check.py --follow")
            return 0
        time.sleep(1.0)
    log(f"the wrapper did not appear within {wait_s:.0f}s")
    return 1


def _ppid_chain(limit: int = 5) -> str:
    """'name(pid) < name(pid) ...' for this process and up to `limit` parents (psutil). Never raises."""
    try:
        import psutil
        me = psutil.Process()
        procs = [me] + me.parents()
        return " < ".join(f"{p.name()}({p.pid})" for p in procs[:limit])
    except Exception:
        return "?"


def _log_cmd(cmd: str, argv) -> None:
    """Append one audit line per start/stop/restart to logs/bot_ctl.log. Never raises."""
    try:
        CMD_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = (f"{datetime.now().isoformat(timespec='seconds')} {cmd} argv={list(argv)} "
                f"ppid_chain={_ppid_chain()}")
        with open(CMD_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    for name in ("stop", "restart"):
        sp = sub.add_parser(name)
        sp.add_argument("--grace", type=float, default=30.0)
    sub.add_parser("start")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        s = status()
        print(json.dumps(s) if a.json else
              (f"running since {s['started']} ({s['age_h']} h): wrapper {s['wrapper_pids']}, app.py {s['app_pids']}, "
               f"bot Chrome x{s['bot_chrome']}, dashboard {'up' if s['dashboard_up'] else 'down'}"
               if s["running"] else f"not running (bot Chrome x{s['bot_chrome']}, dashboard "
                                    f"{'up' if s['dashboard_up'] else 'down'})"))
        return 0
    _log_cmd(a.cmd, sys.argv if argv is None else ["bot_ctl.py"] + list(argv))
    if a.cmd == "stop":
        return stop(a.grace)
    if a.cmd == "start":
        return start()
    rc = stop(a.grace)
    if rc != 0:
        log("restart aborted: the old bot did not stop")
        return rc
    return start()


if __name__ == "__main__":
    sys.exit(main())
