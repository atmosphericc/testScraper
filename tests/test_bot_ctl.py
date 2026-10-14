#!/usr/bin/env python3
"""Offline tests: tools/ops/bot_ctl.py, the start / stop / restart helper /pre-drop boots with (2026-10-14).

Pins:
  - classify: the wrapper cmd.exe (any spelling of the .bat path), app.py pythons (venv launcher and
    interpreter), bot-profile Chromes; NOT relogin_one.py, a personal Chrome, a grep that mentions app.py
  - start: refuses (3) while a bot runs and never calls explorer; launches through explorer.exe and
    returns 0 once a wrapper appears; returns 1 when none appears
  - stop: nothing running -> 0, no Ctrl+C; Ctrl+C goes to the WRAPPER pid, app.py gets the grace
    wait, then wrapper / app / bot Chrome trees are killed; 1 when something survives; a bot without a
    wrapper is killed without a Ctrl+C
  - restart: aborts without starting when stop fails
  - CTRL_C_HELPER compiles and refuses a console a protected PID shares (exit 5 branch present)

No browser, no network, no process is launched or signalled (every OS call is stubbed).
Run: venv/Scripts/python.exe tests/test_bot_ctl.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bot_ctl", ROOT / "tools" / "ops" / "bot_ctl.py")
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}  {str(detail)[:300]}")


# ── classify ────────────────────────────────────────────────────────────────────
C = bc.classify
check("wrapper", C("cmd.exe", ["C:\\Windows\\system32\\cmd.exe", "/c", '""C:\\Users\\elric\\Desktop\\testScraper\\run_bot_with_nightly_restart.bat" "']) == "wrapper")
check("wrapper_upper", C("CMD.EXE", ["cmd", "/c", "RUN_BOT_WITH_NIGHTLY_RESTART.BAT"]) == "wrapper")
check("app_launcher", C("python.exe", ["C:\\repo\\venv\\Scripts\\python.exe", "app.py"]) == "app")
check("app_interp", C("python.exe", ["C:\\Python312\\python.exe", "C:\\repo\\app.py"]) == "app")
check("relogin_not_app", C("python.exe", ["python", "relogin_one.py", "all"]) is None)
check("grep_not_app", C("grep.exe", ["grep", "app.py"]) is None)
check("app_pyc_not_app", C("python.exe", ["python", "app.pyc"]) is None)
check("bot_chrome", C("chrome.exe", ["chrome.exe", "--user-data-dir=C:\\repo\\state\\session_profiles\\alt-1"]) == "chrome")
check("bot_chrome_nodriver", C("chrome.exe", ["chrome.exe", "--user-data-dir=C:\\repo\\nodriver-profile-2"]) == "chrome")
check("personal_chrome", C("chrome.exe", ["chrome.exe", "--profile-directory=Default"]) is None)
check("none", C(None, None) is None)

# ── stubs ───────────────────────────────────────────────────────────────────────


class P:
    def __init__(self, pid, kids=()):
        self.pid, self.info, self.kids, self.killed = pid, {"create_time": 1000.0}, list(kids), False

    def children(self, recursive=False):
        return self.kids

    def kill(self):
        self.killed = True


calls = []
state = {}


def fake_find():
    return {k: [p for p in state.get(k, []) if not p.killed] for k in ("wrapper", "app", "chrome")}


bc.find = fake_find
bc.port_open = lambda port=5001: False
bc.time.sleep = lambda s: None
bc.send_ctrl_c = lambda pid: calls.append(("ctrl_c", pid)) or 0
bc.wait_gone = lambda procs, timeout: calls.append(("wait", [p.pid for p in procs], timeout)) or []


class FakePopen:
    def __init__(self, args, **kw):
        calls.append(("popen", args))
        if state.get("_appear_on_start"):
            state["wrapper"].append(P(900))


bc.subprocess.Popen = FakePopen

# ── start ───────────────────────────────────────────────────────────────────────
state.clear(); state.update(wrapper=[P(10)], app=[P(11), P(12)], chrome=[]); calls.clear()
check("start_refuses_running", bc.start(wait_s=0.01) == 3)
check("start_refuses_no_popen", not any(c[0] == "popen" for c in calls), calls)

state.clear(); state.update(wrapper=[], app=[], chrome=[], _appear_on_start=True); calls.clear()
rc = bc.start(wait_s=5)
check("start_ok", rc == 0, rc)
check("start_via_explorer", calls and calls[0][0] == "popen" and calls[0][1][0] == "explorer.exe"
      and calls[0][1][1].endswith("run_bot_with_nightly_restart.bat"), calls)
state.pop("_appear_on_start")

state.clear(); state.update(wrapper=[], app=[], chrome=[]); calls.clear()
check("start_no_wrapper_fails", bc.start(wait_s=0.01) == 1)

# ── stop ────────────────────────────────────────────────────────────────────────
state.clear(); state.update(wrapper=[], app=[], chrome=[]); calls.clear()
check("stop_nothing", bc.stop(1) == 0 and not calls, calls)

chrome_kid = P(31)
w, a1, a2, orphan = P(10), P(11, kids=[chrome_kid]), P(12), P(40)
state.clear(); state.update(wrapper=[w], app=[a1, a2], chrome=[chrome_kid, orphan]); calls.clear()
rc = bc.stop(7)
check("stop_ok", rc == 0, rc)
check("stop_ctrl_c_to_wrapper", ("ctrl_c", 10) in calls, calls)
check("stop_grace_wait_on_app", ("wait", [11, 12], 7) in calls, calls)
check("stop_killed_all", all(p.killed for p in (w, a1, a2, chrome_kid, orphan)))

w2 = P(10)
w2.kill = lambda: None                      # survives
state.clear(); state.update(wrapper=[w2], app=[], chrome=[]); calls.clear()
check("stop_survivor_fails", bc.stop(1) == 1)

a3 = P(11)
state.clear(); state.update(wrapper=[], app=[a3], chrome=[]); calls.clear()
check("stop_no_wrapper_kills", bc.stop(1) == 0 and a3.killed and not any(c[0] == "ctrl_c" for c in calls), calls)

# ── restart ─────────────────────────────────────────────────────────────────────
w3 = P(10)
w3.kill = lambda: None
state.clear(); state.update(wrapper=[w3], app=[], chrome=[]); calls.clear()
check("restart_aborts_on_failed_stop", bc.main(["restart", "--grace", "1"]) == 1
      and not any(c[0] == "popen" for c in calls), calls)

state.clear(); state.update(wrapper=[P(10)], app=[P(11)], chrome=[], _appear_on_start=True); calls.clear()
check("restart_ok", bc.main(["restart", "--grace", "1"]) == 0 and any(c[0] == "popen" for c in calls), calls)

# ── the Ctrl+C helper ───────────────────────────────────────────────────────────
try:
    compile(bc.CTRL_C_HELPER, "<helper>", "exec")
    check("helper_compiles", True)
except SyntaxError as e:
    check("helper_compiles", False, e)
check("helper_refuses_shared_console", "protect & set(" in bc.CTRL_C_HELPER and "sys.exit(5)" in bc.CTRL_C_HELPER)

print(f"===== {PASS} passed, {FAIL} failed =====")
sys.exit(1 if FAIL else 0)
