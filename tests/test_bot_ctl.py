#!/usr/bin/env python3
"""Offline tests: tools/ops/bot_ctl.py, the start / stop / restart helper /pre-drop boots with (2026-10-14).

Pins:
  - classify: the wrapper cmd.exe (any spelling of the .bat path), app.py pythons (venv launcher and
    interpreter), bot-profile Chromes; NOT relogin_one.py, a personal Chrome, a grep that mentions app.py
  - start: refuses (3) while a bot runs and never launches; launches `cmd.exe /c <bat>` in a NEW console
    (10-23: the explorer.exe open vanished three times, C-1022-08), without CREATE_NEW_PROCESS_GROUP (stop's
    Ctrl+C must still reach the wrapper), console output to a log file, retrying without
    CREATE_BREAKAWAY_FROM_JOB when the job refuses it; and
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
import tempfile
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
# 10-19: main() appends to logs/bot_ctl.log and send_ctrl_c stamps state/last_bot_ctl_stop.json - keep both
# off the repo's real files, or every suite run writes real-looking `restart` lines into the audit log
_tmp_ops = Path(tempfile.mkdtemp())
bc.CMD_LOG = _tmp_ops / "bot_ctl.log"
bc.STAMP = _tmp_ops / "last_bot_ctl_stop.json"


class FakePopen:
    def __init__(self, args, **kw):
        calls.append(("popen", args, kw))
        if state.get("_refuse_breakaway") and (kw.get("creationflags", 0) & _BREAKAWAY):
            raise OSError(5, "Access is denied")     # a job that forbids breakaway
        if state.get("_appear_on_start"):
            state["wrapper"].append(P(900))


_NEW_CONSOLE = getattr(bc.subprocess, "CREATE_NEW_CONSOLE", 0x10)
_NEW_GROUP = getattr(bc.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
_BREAKAWAY = getattr(bc.subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)


bc.subprocess.Popen = FakePopen
bc._enable_ctrl_c = lambda: calls.append(("enable_ctrl_c",))      # the real one clears CONSOLE_IGNORE_CTRL_C (kernel32)

# ── start ───────────────────────────────────────────────────────────────────────
state.clear(); state.update(wrapper=[P(10)], app=[P(11), P(12)], chrome=[]); calls.clear()
check("start_refuses_running", bc.start(wait_s=0.01) == 3)
check("start_refuses_no_popen", not any(c[0] == "popen" for c in calls), calls)

state.clear(); state.update(wrapper=[], app=[], chrome=[], _appear_on_start=True); calls.clear()
rc = bc.start(wait_s=5)
check("start_ok", rc == 0, rc)
_pop = next((c for c in calls if c[0] == "popen"), None)
check("start_via_cmd_new_console", _pop is not None and _pop[1][:2] == ["cmd.exe", "/c"]
      and _pop[1][2].endswith("run_bot_with_nightly_restart.bat") and len(_pop[1]) == 3, calls)
check("start_ctrl_c_enabled_before_launch", [c[0] for c in calls][:2] == ["enable_ctrl_c", "popen"], [c[0] for c in calls])
_kw = _pop[2] if _pop else {}
_flags = _kw.get("creationflags", 0)
check("start_new_console_flag", bool(_flags & _NEW_CONSOLE), hex(_flags))
check("start_breakaway_first", bool(_flags & _BREAKAWAY), hex(_flags))
check("start_no_new_process_group", not (_flags & _NEW_GROUP), hex(_flags))   # Ctrl+C from `stop` must still land
check("start_console_minimized", getattr(_kw.get("startupinfo"), "wShowWindow", None) == bc.SW_SHOWMINNOACTIVE, _kw.get("startupinfo"))
check("start_console_log_captured", _kw.get("stdout") is not None and _kw.get("stderr") == bc.subprocess.STDOUT
      and getattr(_kw.get("stdout"), "closed", False), (_kw.get("stdout"), _kw.get("stderr")))
check("start_cwd_repo_root", _kw.get("cwd") == str(bc.BAT.parent), _kw.get("cwd"))
_path = next((v for k, v in (_kw.get("env") or {}).items() if k.upper() == "PATH"), "")
check("start_env_system32_first", _path.lower().split(bc.os.pathsep)[0].endswith("system32"), _path[:120])
_sep = bc.os.pathsep
_sys32 = bc.os.path.join("C:/Win", "System32")
_e = bc.wrapper_env({"SystemRoot": "C:/Win", "Path": _sep.join(["C:/Git/usr/bin", _sys32, "C:/tools"])})
check("wrapper_env_reorders_path", _e["Path"].split(_sep)[0] == _sys32 and _e["Path"].endswith(_sep.join(["C:/Git/usr/bin", "C:/tools"]))
      and _e["Path"].lower().count(_sys32.lower() + _sep) == 1, _e["Path"])
check("wrapper_env_sets_pythonioencoding", _e.get("PYTHONIOENCODING") == "utf-8"
      and bc.wrapper_env({"PYTHONIOENCODING": "latin-1", "Path": ""})["PYTHONIOENCODING"] == "latin-1", _e.get("PYTHONIOENCODING"))
check("start_only_one_launch", sum(1 for c in calls if c[0] == "popen") == 1, calls)
state.pop("_appear_on_start")

# a job that forbids breakaway: the first Popen raises, the retry drops the flag and keeps the new console
state.clear(); state.update(wrapper=[], app=[], chrome=[], _appear_on_start=True, _refuse_breakaway=True); calls.clear()
rc = bc.start(wait_s=5)
_pops = [c for c in calls if c[0] == "popen"]
check("start_retry_without_breakaway", rc == 0 and len(_pops) == 2 and (_pops[0][2]["creationflags"] & _BREAKAWAY)
      and not (_pops[1][2]["creationflags"] & _BREAKAWAY) and (_pops[1][2]["creationflags"] & _NEW_CONSOLE), (rc, [hex(c[2].get("creationflags", 0)) for c in _pops]))
state.pop("_appear_on_start"); state.pop("_refuse_breakaway")

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
check("restart_audit_line_goes_to_temp_log", bc.CMD_LOG.exists() and bc.CMD_LOG.parent == _tmp_ops
      and bc.CMD_LOG != ROOT / "logs" / "bot_ctl.log")

# ── the Ctrl+C helper ───────────────────────────────────────────────────────────
try:
    compile(bc.CTRL_C_HELPER, "<helper>", "exec")
    check("helper_compiles", True)
except SyntaxError as e:
    check("helper_compiles", False, e)
check("helper_refuses_shared_console", "protect & set(" in bc.CTRL_C_HELPER and "sys.exit(5)" in bc.CTRL_C_HELPER)

print(f"===== {PASS} passed, {FAIL} failed =====")
sys.exit(1 if FAIL else 0)
