#!/usr/bin/env python3
"""Offline tests for TARGET_SIGINT_ATTRIB (2026-10-19, lane sigint).

Three wrapper kills (10-18 19:05, 23:15:37, 10-19 08:02:11) were console Ctrl+C events with no
known sender. With TARGET_SIGINT_ATTRIB=1 (default off) app.py's shutdown_handler, app.py's boot
entry (reap / quick-edit calls) and relogin_one.py's entry print ONE attribution line:
  [SYSTEM] [SIGINT_ATTRIB] signum=<n> idle_ms=<..> fg="<title>" bot_ctl_stamp_age_s=<n|none> -> <bot_ctl stop|UNATTRIBUTED>
and tools/ops/bot_ctl.py stamps state/last_bot_ctl_stop.json before it raises its Ctrl+C and appends
every start/stop/restart to logs/bot_ctl.log.

Pins:
  - the helper builds a well-formed line (idle small/large, stamp fresh/stale/missing/boundary) and
    never raises when ctypes or the stamp file fail; a quote/newline in a window title is sanitised
  - shutdown_handler (lifted from app.py with ast, run against stubs): flag off prints no
    [SIGINT_ATTRIB]; flag on prints it BEFORE "Shutting down gracefully"; a failing emitter does not
    stop the shutdown; the second signal still hits os._exit(1) first and prints no attribution
  - AST differential vs HEAD (3278ce7f): with the flag branches removed, shutdown_handler and the
    boot entry statements are identical to HEAD's; the except body is ONE flag-guarded statement + a
    bare raise
  - bot_ctl: send_ctrl_c writes the stamp, a failing stamp write still signals and returns the same
    rc; main(["start"|"stop"|"restart"]) appends to the log, status does not; a failing log write
    never changes a return code (start / stop are patched, nothing real runs)
  - relogin_one.py (hermetic subprocess, stub imports, asyncio.run patched to raise
    KeyboardInterrupt): exit code and traceback identical to HEAD's file for flag off and on; the
    line appears on stderr and in the log only with the flag on

No browser, no network, no process launched except the python driver. Run: python tests/test_sigint_attrib_1019.py
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
BASE_COMMIT = "3278ce7f"
APP = (ROOT / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
HELPERS = ("_sa_idle_ms", "_sa_fg_title", "_sa_stamp_age_s", "_sa_stamp_pid", "_sa_pid_is_ancestor",
           "_sigint_attrib_line", "_sigint_attrib_emit")
FLAG = "TARGET_SIGINT_ATTRIB"
LINE_RE = re.compile(
    r'^\[SYSTEM\] \[SIGINT_ATTRIB\] signum=(?P<sig>\S+) idle_ms=(?P<idle>\d+|\?) fg="(?P<fg>[^"\n]*)" '
    r'bot_ctl_stamp_age_s=(?P<age>-?\d+|none) stamp_pid=(?P<spid>\d+|none) ppid=(?P<ppid>\d+|\?) '
    r'-> (?P<who>bot_ctl stop|UNATTRIBUTED)$')

_fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------- lifting code out of app.py
def _git_show(path):
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE_COMMIT}:{path}"], capture_output=True,
                       timeout=60)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8").replace("\r\n", "\n")


def _main_if(tree):
    for n in tree.body:
        if isinstance(n, ast.If) and "__main__" in ast.dump(n.test):
            return n
    raise AssertionError("no __main__ block in app.py")


def _find_func(node, name):
    for n in ast.walk(node):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} not found")


def _helper_ns(**extra):
    tree = ast.parse(APP)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in HELPERS]
    assert {n.name for n in nodes} == set(HELPERS), [n.name for n in nodes]
    ns = {"ctypes": __import__("ctypes"), "os": os, "time": time, "json": json,
          "__file__": str(ROOT / "app.py")}
    ns.update(extra)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), ns)
    ns.update(extra)        # after the exec, so a probe can be replaced by a stub
    return ns


# ---------------------------------------------------------------- 1. helper line
def _line_with(idle=1234, fg="Windows Terminal", age=None, signum=2, spid=4242, anc=True):
    def _idle():
        if isinstance(idle, Exception):
            raise idle
        return idle

    def _fg():
        if isinstance(fg, Exception):
            raise fg
        return fg

    def _age():
        if isinstance(age, Exception):
            raise age
        return age

    def _spid():
        if isinstance(spid, Exception):
            raise spid
        return spid

    def _anc(pid):
        if isinstance(anc, Exception):
            raise anc
        return anc

    ns = _helper_ns(_sa_idle_ms=_idle, _sa_fg_title=_fg, _sa_stamp_age_s=_age,
                    _sa_stamp_pid=_spid, _sa_pid_is_ancestor=_anc)
    return ns["_sigint_attrib_line"](signum)


def test_helper_format():
    cases = [
        ("fresh_stamp", dict(idle=120, age=3.2), "bot_ctl stop", "3", "120"),
        ("stale_stamp", dict(idle=120, age=400.0), "UNATTRIBUTED", "400", "120"),
        ("missing_stamp", dict(idle=45_000_000, age=FileNotFoundError("x")), "UNATTRIBUTED", "none", "45000000"),
        ("boundary_29", dict(idle=5, age=29.9), "bot_ctl stop", "29", "5"),
        ("boundary_30", dict(idle=5, age=30.0), "UNATTRIBUTED", "30", "5"),
        ("clock_skew_small", dict(idle=5, age=-2.0), "bot_ctl stop", "-2", "5"),
        ("clock_skew_big", dict(idle=5, age=-600.0), "UNATTRIBUTED", "-600", "5"),
        ("idle_unknown", dict(idle=None, age=1.0), "bot_ctl stop", "1", "?"),
    ]
    for name, kw, who, age, idle in cases:
        line = _line_with(**kw)
        m = LINE_RE.match(line)
        check(f"helper_{name}_wellformed", bool(m), line)
        if m:
            check(f"helper_{name}_values", m["who"] == who and m["age"] == age and m["idle"] == idle,
                  line)
    line = _line_with(signum=15, age=1.0)
    check("helper_signum_in_line", " signum=15 " in line, line)
    # 10-19 fix: a fresh stamp whose pid is NOT one of our ancestors (a restart / forced login that began
    # inside the 30 s window) must not inherit the label
    line = _line_with(idle=5, age=3.0, spid=4242, anc=False)
    m = LINE_RE.match(line)
    check("helper_fresh_stamp_wrong_pid_unattributed", bool(m) and m["who"] == "UNATTRIBUTED"
          and m["spid"] == "4242" and m["age"] == "3", line)
    line = _line_with(idle=5, age=3.0, spid=4242, anc=True)
    m = LINE_RE.match(line)
    check("helper_fresh_stamp_ancestor_pid_attributed", bool(m) and m["who"] == "bot_ctl stop"
          and m["spid"] == "4242" and m["ppid"] == str(os.getppid()), line)
    line = _line_with(idle=5, age=3.0, spid=RuntimeError("no pid"), anc=True)
    m = LINE_RE.match(line)
    check("helper_fresh_stamp_no_pid_unattributed", bool(m) and m["who"] == "UNATTRIBUTED"
          and m["spid"] == "none", line)
    line = _line_with(idle=5, age=3.0, spid=4242, anc=RuntimeError("psutil gone"))
    m = LINE_RE.match(line)
    check("helper_ancestor_probe_failure_unattributed", bool(m) and m["who"] == "UNATTRIBUTED", line)
    line = _line_with(idle=5, age=400.0, spid=4242, anc=True)
    m = LINE_RE.match(line)
    check("helper_stale_stamp_ancestor_pid_unattributed", bool(m) and m["who"] == "UNATTRIBUTED", line)
    # 10-19 fix round: the ~50 ms psutil probe must not run at all for a stale / missing / far-future stamp
    # (the stamp file is never deleted, so every later Ctrl+C would pay for it). The stub raises
    # AssertionError if called; an AssertionError swallowed by the helper's except would still hide, so the
    # stub also records the call.
    for name, age in (("stale", 400.0), ("boundary_30", 30.0), ("missing", FileNotFoundError("x")),
                      ("future_skew", -600.0)):
        probed = []

        def _no_probe(pid, _p=probed):
            _p.append(pid)
            raise AssertionError("ancestor probe ran for a non-fresh stamp")

        ns = _helper_ns(_sa_idle_ms=lambda: 5, _sa_fg_title=lambda: "t",
                        _sa_stamp_age_s=(lambda a=age: (_ for _ in ()).throw(a)) if isinstance(age, Exception)
                        else (lambda a=age: a),
                        _sa_stamp_pid=lambda: 4242, _sa_pid_is_ancestor=_no_probe)
        line = ns["_sigint_attrib_line"](2)
        m = LINE_RE.match(line)
        check(f"helper_{name}_stamp_skips_ancestor_probe", bool(m) and m["who"] == "UNATTRIBUTED"
              and not probed, (line, probed))
    probed = []
    ns = _helper_ns(_sa_idle_ms=lambda: 5, _sa_fg_title=lambda: "t", _sa_stamp_age_s=lambda: 3.0,
                    _sa_stamp_pid=lambda: 4242, _sa_pid_is_ancestor=lambda pid: (probed.append(pid), True)[1])
    m = LINE_RE.match(ns["_sigint_attrib_line"](2))
    check("helper_fresh_stamp_runs_ancestor_probe_once", bool(m) and m["who"] == "bot_ctl stop"
          and probed == [4242], (m and m["who"], probed))


def test_helper_never_raises():
    boom = RuntimeError("boom")
    line = _line_with(idle=boom, fg=boom, age=boom, spid=boom, anc=boom)
    m = LINE_RE.match(line)
    check("helper_all_probes_fail", bool(m) and m["idle"] == "?" and m["fg"] == "?" and m["age"] == "none"
          and m["spid"] == "none" and m["who"] == "UNATTRIBUTED", line)

    class _NoCtypes:
        def __getattr__(self, name):
            raise OSError("ctypes unavailable")

    ns = _helper_ns(ctypes=_NoCtypes(), __file__=str(Path(tempfile.gettempdir()) / "nowhere" / "app.py"))
    line = ns["_sigint_attrib_line"](2)
    m = LINE_RE.match(line)
    check("helper_ctypes_fails", bool(m) and m["idle"] == "?" and m["fg"] == "?", line)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ns["_sigint_attrib_emit"](2)
    check("emit_prints_one_line", out.getvalue().count("[SIGINT_ATTRIB]") == 1
          and out.getvalue().endswith("\n"), out.getvalue())

    # the emitter swallows even a print() failure
    class _BadOut:
        def write(self, *a):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    ok = True
    try:
        with contextlib.redirect_stdout(_BadOut()):
            ns["_sigint_attrib_emit"](2)
    except Exception:
        ok = False
    check("emit_swallows_print_failure", ok)

    # sanitising: a quote / newline / control char in a window title cannot break the one-line format
    line = _line_with(fg='evil" title\nwith\tcontrol' + "x" * 200, age=1.0)
    m = LINE_RE.match(line)
    check("helper_title_sanitised", bool(m) and "\n" not in line and len(m["fg"]) <= 80, line)


def test_helper_real_ctypes_and_stamp_file():
    # real ctypes on this host (Windows); only the shape is asserted, never a value
    ns = _helper_ns()
    line = ns["_sigint_attrib_line"](2)
    check("helper_real_ctypes_wellformed", bool(LINE_RE.match(line)), line)
    if sys.platform == "win32":
        m = LINE_RE.match(line)
        check("helper_real_idle_is_number", bool(m) and m["idle"] != "?", line)
    # real stamp file next to a fake app.py: fresh -> bot_ctl stop, then stale
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "state").mkdir()
        stamp = Path(td) / "state" / "last_bot_ctl_stop.json"
        ns = _helper_ns(__file__=str(Path(td) / "app.py"), _sa_idle_ms=lambda: 7, _sa_fg_title=lambda: "t")
        # real psutil: our parent process is an ancestor, a made-up pid is not
        stamp.write_text(json.dumps({"ts": time.time() - 2, "pid": os.getppid(), "argv": [], "by": "bot_ctl"}))
        m = LINE_RE.match(ns["_sigint_attrib_line"](2))
        check("stamp_file_fresh_attributed", bool(m) and m["who"] == "bot_ctl stop"
              and m["spid"] == str(os.getppid()), m and m.string)
        stamp.write_text(json.dumps({"ts": time.time() - 2, "pid": 4_000_000_001, "argv": [], "by": "bot_ctl"}))
        m = LINE_RE.match(ns["_sigint_attrib_line"](2))
        check("stamp_file_fresh_wrong_pid_unattributed", bool(m) and m["who"] == "UNATTRIBUTED", m and m.string)
        stamp.write_text(json.dumps({"ts": time.time() - 2}))      # no pid key
        m = LINE_RE.match(ns["_sigint_attrib_line"](2))
        check("stamp_file_fresh_no_pid_key_unattributed", bool(m) and m["who"] == "UNATTRIBUTED"
              and m["spid"] == "none", m and m.string)
        stamp.write_text(json.dumps({"ts": time.time() - 3600}))
        m = LINE_RE.match(ns["_sigint_attrib_line"](2))
        check("stamp_file_stale_unattributed", bool(m) and m["who"] == "UNATTRIBUTED", m and m.string)
        stamp.write_text("{not json")
        m = LINE_RE.match(ns["_sigint_attrib_line"](2))
        check("stamp_file_corrupt_unattributed", bool(m) and m["age"] == "none", m and m.string)


# ---------------------------------------------------------------- 2. shutdown_handler
class _Exit(BaseException):
    pass


def _run_handler(flag, emit=None, calls=1, order=None):
    """Run the REAL shutdown_handler source against stubs; returns (stdout, exit_codes).
    `order` (a list) records 'deadman' when the deadman thread starts and 'emit' when the emit stub runs."""
    tree = ast.parse(APP)
    fn = _find_func(_main_if(tree), "shutdown_handler")
    codes = []

    def _os_exit(code):
        codes.append(code)
        raise _Exit(code)

    fake_os = SimpleNamespace(environ={FLAG: flag} if flag is not None else {}, _exit=_os_exit,
                              write=lambda *a: 0)
    sd = {"flag": False}
    worker = SimpleNamespace(stop=lambda timeout=0: None)
    fake_threading = SimpleNamespace(Thread=lambda **kw: SimpleNamespace(
        start=lambda: order.append("deadman") if order is not None else None))
    base_emit = emit or (lambda signum=None: print(f"[SYSTEM] [SIGINT_ATTRIB] signum={signum} STUB"))

    def _emit(signum=None):
        if order is not None:
            order.append("emit")
        return base_emit(signum)
    ns = {"os": fake_os, "time": SimpleNamespace(time=time.time, sleep=lambda s: None),
          "threading": fake_threading, "asyncio": SimpleNamespace(),
          "_shutdown_started": sd, "add_activity_log": lambda *a, **k: None,
          "global_purchase_manager": None, "global_event_loop": None, "_kill_browser_now": lambda: None,
          "activity_log_persistence_worker": worker,
          "_sigint_attrib_emit": _emit}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "app.py", "exec"), ns)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for _ in range(calls):
            try:
                ns["shutdown_handler"](2, None)
            except _Exit:
                pass
    return out.getvalue(), codes


def test_shutdown_handler():
    out, codes = _run_handler(None)
    check("handler_flag_unset_no_line", "SIGINT_ATTRIB" not in out and "Shutting down gracefully" in out, out)
    check("handler_flag_unset_exit0", codes == [0], codes)
    for v in ("0", "", "true", "yes", "11"):
        out, codes = _run_handler(v)
        check(f"handler_flag_{v!r}_off", "SIGINT_ATTRIB" not in out and codes == [0], (out, codes))
    out, codes = _run_handler("1")
    i, j = out.find("[SIGINT_ATTRIB]"), out.find("Shutting down gracefully")
    check("handler_flag_on_line_before_shutdown_print", 0 <= i < j and out.count("[SIGINT_ATTRIB]") == 1, out)
    check("handler_flag_on_signum_passed", "signum=2" in out, out)
    check("handler_flag_on_exit0", codes == [0], codes)
    # the deadman (hard-exit timer) must be armed BEFORE the attribution emit, so a hung emit cannot
    # leave a Ctrl+C unable to exit
    order = []
    _run_handler("1", order=order)
    check("handler_flag_on_deadman_starts_before_emit", order == ["deadman", "emit"], order)
    order = []
    _run_handler(None, order=order)
    check("handler_flag_off_deadman_only_no_emit", order == ["deadman"], order)

    def _bad(signum=None):
        raise RuntimeError("emit exploded")

    out, codes = _run_handler("1", emit=_bad)
    check("handler_emit_failure_does_not_stop_shutdown", "Shutting down gracefully" in out and codes == [0],
          (out, codes))
    out, codes = _run_handler("1", calls=2)
    check("handler_second_signal_force_exits_first",
          codes == [0, 1] or codes == [1] or codes[-1:] == [1], codes)
    check("handler_second_signal_no_second_attrib", out.count("[SIGINT_ATTRIB]") == 1
          and "Force-exit on second Ctrl+C" in out, out)


# ---------------------------------------------------------------- 3. AST differential vs HEAD
class _Strip(ast.NodeTransformer):
    """Remove the TARGET_SIGINT_ATTRIB flag branches everywhere in a tree."""

    def _stmts(self, stmts):
        out = []
        for s in stmts:
            s = self.visit(s)
            if s is None:
                continue
            out.extend(s if isinstance(s, list) else [s])
        return out

    def visit_Try(self, node):
        if FLAG in ast.dump(node) and not node.finalbody:
            if any(isinstance(h.type, ast.Name) and h.type.id == "KeyboardInterrupt" for h in node.handlers):
                return self._stmts(node.body)       # boot entry: the wrapped calls stay, the handler goes
            if len(node.body) == 1 and isinstance(node.body[0], ast.If) and FLAG in ast.dump(node.body[0].test):
                return None                          # try/if-flag/except Exception: pass around the emit
        self.generic_visit(node)
        return node


def _strip_attrib(stmts):
    mod = ast.Module(body=list(stmts), type_ignores=[])
    mod = _Strip().visit(mod)
    return mod.body


def test_ast_differential():
    head = _git_show("app.py")
    if head is None:
        check("ast_head_available", False, f"git show {BASE_COMMIT}:app.py failed")
        return
    new_tree, head_tree = ast.parse(APP), ast.parse(head)
    nf, hf = _find_func(_main_if(new_tree), "shutdown_handler"), _find_func(_main_if(head_tree), "shutdown_handler")
    nb = ast.dump(ast.Module(body=_strip_attrib(nf.body), type_ignores=[]))
    hb = ast.dump(ast.Module(body=hf.body, type_ignores=[]))
    check("ast_shutdown_handler_flag_off_identical", nb == hb)
    added = [s for s in nf.body if isinstance(s, ast.Try) and FLAG in ast.dump(s)]
    check("ast_shutdown_handler_one_added_try", len(added) == 1)
    if added:
        t = added[0]
        check("ast_handler_try_shape", len(t.body) == 1 and isinstance(t.body[0], ast.If)
              and len(t.body[0].body) == 1 and len(t.handlers) == 1
              and "Exception" in ast.dump(t.handlers[0].type) and not t.orelse and not t.finalbody)

    nm, hm = _main_if(new_tree).body, _main_if(head_tree).body
    n_strip = ast.dump(ast.Module(body=_strip_attrib(nm), type_ignores=[]))
    h_dump = ast.dump(ast.Module(body=hm, type_ignores=[]))
    check("ast_boot_entry_flag_off_identical", n_strip == h_dump)
    kb = [s for s in nm if isinstance(s, ast.Try)
          and any(isinstance(h.type, ast.Name) and h.type.id == "KeyboardInterrupt" for h in s.handlers)]
    check("ast_boot_entry_one_keyboardinterrupt_try", len(kb) == 1)
    if kb:
        h = kb[0].handlers[0]
        check("ast_boot_except_is_one_guarded_stmt_then_bare_raise",
              len(h.body) == 2 and isinstance(h.body[0], ast.If) and FLAG in ast.dump(h.body[0].test)
              and len(h.body[0].body) == 1 and isinstance(h.body[1], ast.Raise) and h.body[1].exc is None
              and not h.body[0].orelse and not kb[0].orelse and not kb[0].finalbody)
        calls = [ast.unparse(s).split("(")[0] for s in kb[0].body if isinstance(s, ast.Expr)]
        check("ast_boot_try_wraps_reap_and_quickedit",
              calls == ["_reap_orphan_repo_chromes", "_disable_console_quickedit"], calls)

    # relogin_one: same shape on the module-level __main__ block
    rl_new = ast.parse((ROOT / "relogin_one.py").read_text(encoding="utf-8").replace("\r\n", "\n"))
    rl_head_src = _git_show("relogin_one.py")
    rl_head = ast.parse(rl_head_src) if rl_head_src else None
    nm = _main_if(rl_new).body
    kb = [s for s in nm if isinstance(s, ast.Try)]
    check("ast_relogin_entry_try", len(kb) == 1)
    if kb and rl_head is not None:
        t = kb[0]
        h = t.handlers[0]
        check("ast_relogin_except_shape", len(t.handlers) == 1 and "KeyboardInterrupt" in ast.dump(h.type)
              and len(h.body) == 2 and isinstance(h.body[0], ast.If) and FLAG in ast.dump(h.body[0].test)
              and isinstance(h.body[1], ast.Raise) and h.body[1].exc is None)
        flat = [s for s in nm if not isinstance(s, ast.Try)] + t.body
        check("ast_relogin_entry_flag_off_identical",
              ast.dump(ast.Module(body=flat, type_ignores=[])) ==
              ast.dump(ast.Module(body=_main_if(rl_head).body, type_ignores=[])))


# ---------------------------------------------------------------- 4. bot_ctl
def _load_bot_ctl():
    spec = importlib.util.spec_from_file_location("bot_ctl_sigint", ROOT / "tools" / "ops" / "bot_ctl.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    return bc


def test_bot_ctl():
    bc = _load_bot_ctl()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        runs = []

        def fake_run(cmd, **kw):
            runs.append(cmd)
            return SimpleNamespace(returncode=0)

        bc._protected_pids = lambda: [1, 2]
        bc.subprocess = SimpleNamespace(run=fake_run, CREATE_NO_WINDOW=0, CREATE_NEW_PROCESS_GROUP=0,
                                        Popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("Popen")))
        bc.STAMP = td / "state" / "last_bot_ctl_stop.json"      # dir missing -> created
        before = time.time()
        rc = bc.send_ctrl_c(4242)
        check("send_ctrl_c_rc_unchanged", rc == 0 and len(runs) == 1, (rc, runs))
        stamp = json.loads(bc.STAMP.read_text(encoding="utf-8")) if bc.STAMP.exists() else {}
        check("send_ctrl_c_writes_stamp", stamp.get("by") == "bot_ctl" and stamp.get("pid") == 4242
              and before - 1 <= stamp.get("ts", 0) <= time.time() + 1 and isinstance(stamp.get("argv"), list),
              stamp)
        check("send_ctrl_c_helper_cmd_unchanged", runs[0][0] == sys.executable and runs[0][1] == "-c"
              and runs[0][2] == bc.CTRL_C_HELPER and runs[0][3:] == ["4242", "1,2"], runs[0][3:])
        check("send_ctrl_c_no_leftover_tmp", not (td / "state" / "last_bot_ctl_stop.json.tmp").exists())

        # a failing stamp write must still signal and return the helper's rc
        blocker = td / "afile"
        blocker.write_text("x")
        bc.STAMP = blocker / "sub" / "stamp.json"          # parent is a file -> mkdir fails
        runs.clear()
        rc = bc.send_ctrl_c(7)
        check("send_ctrl_c_stamp_failure_still_signals", rc == 0 and len(runs) == 1, (rc, runs))

        # main(): start / stop / restart append to the log, status does not; rc untouched
        bc.CMD_LOG = td / "logs" / "bot_ctl.log"
        calls = []
        bc.start = lambda wait_s=30.0: (calls.append("start"), 0)[1]
        bc.stop = lambda grace=30.0: (calls.append("stop"), 0)[1]
        bc.status = lambda: {"running": False, "wrapper_pids": [], "app_pids": [], "bot_chrome": 0,
                             "dashboard_up": False, "started": None, "age_h": None}
        with contextlib.redirect_stdout(io.StringIO()):
            rc_status = bc.main(["status"])
        check("main_status_not_logged", rc_status == 0 and not bc.CMD_LOG.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            rc_start = bc.main(["start"])
        check("main_start_rc_and_call", rc_start == 0 and calls == ["start"], (rc_start, calls))
        lines = bc.CMD_LOG.read_text(encoding="utf-8").splitlines() if bc.CMD_LOG.exists() else []
        check("main_start_appends_log", len(lines) == 1 and re.match(
            r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d start argv=\[.*\] ppid_chain=.+", lines[0]), lines)
        with contextlib.redirect_stdout(io.StringIO()):
            bc.main(["stop"])
            bc.main(["restart"])
        lines = bc.CMD_LOG.read_text(encoding="utf-8").splitlines()
        cmds = [ln.split()[1] for ln in lines]
        check("main_stop_restart_appended", cmds == ["start", "stop", "restart"], cmds)
        check("main_restart_calls_stop_then_start", calls == ["start", "stop", "stop", "start"], calls)
        chain = lines[0].split("ppid_chain=", 1)[1]
        check("ppid_chain_max_5", 1 <= len(chain.split(" < ")) <= 5, chain)

        # a log failure never changes a return code
        bc.CMD_LOG = blocker / "sub" / "bot_ctl.log"
        with contextlib.redirect_stdout(io.StringIO()):
            r1, r2 = bc.main(["start"]), bc.main(["stop"])
        check("main_log_failure_keeps_rc", (r1, r2) == (0, 0), (r1, r2))
        bc.stop = lambda grace=30.0: 1
        with contextlib.redirect_stdout(io.StringIO()):
            r3 = bc.main(["restart"])
        check("main_log_failure_nonzero_rc_preserved", r3 == 1, r3)


# ---------------------------------------------------------------- 5. relogin_one exit-code subprocess
DRIVER = r'''
import asyncio, os, runpy, sys, types
for name in ("harvest_accounts", "src", "src.session", "src.session.account_identity"):
    m = types.ModuleType(name)
    m.__getattr__ = lambda n: None
    m.__path__ = []
    sys.modules[name] = m
def _fake_run(coro):
    coro.close()
    raise KeyboardInterrupt
asyncio.run = _fake_run
runpy.run_path(sys.argv[1], run_name="__main__")
'''


def _run_relogin(td, src_text, flag, stamp=None):
    d = Path(td)
    if stamp is not None:
        (d / "state").mkdir(parents=True, exist_ok=True)
        (d / "state" / "last_bot_ctl_stop.json").write_text(json.dumps(stamp), encoding="utf-8")
    script = d / "relogin_one.py"
    script.write_text(src_text, encoding="utf-8")
    drv = d / "_driver.py"
    drv.write_text(DRIVER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != FLAG}
    env.update({"RELOGIN_DEADMAN_S": "0", "PYTHONIOENCODING": "utf-8"})
    if flag is not None:
        env[FLAG] = flag
    r = subprocess.run([sys.executable, str(drv), str(script)], capture_output=True, env=env, cwd=td,
                       timeout=120)
    log = d / "logs" / "relogin.log"
    return (r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"),
            log.read_text(encoding="utf-8") if log.exists() else "")


def test_relogin_entry():
    head_src = _git_show("relogin_one.py")
    if head_src is None:
        check("relogin_head_available", False, f"git show {BASE_COMMIT}:relogin_one.py failed")
        return
    new_src = (ROOT / "relogin_one.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2, \
            tempfile.TemporaryDirectory() as t3, tempfile.TemporaryDirectory() as t4:
        h = _run_relogin(t1, head_src, None)
        n_off = _run_relogin(t2, new_src, None)
        n_zero = _run_relogin(t3, new_src, "0")
        n_on = _run_relogin(t4, new_src, "1")
    # relogin_one carries its own inline copy of the attribution logic: exercise its attributed branch.
    # This process is the driver's parent, hence an ancestor of the runpy'd relogin code.
    with tempfile.TemporaryDirectory() as t5, tempfile.TemporaryDirectory() as t6,             tempfile.TemporaryDirectory() as t7:
        n_anc = _run_relogin(t5, new_src, "1", stamp={"ts": time.time(), "pid": os.getpid()})
        n_bad = _run_relogin(t6, new_src, "1", stamp={"ts": time.time(), "pid": 4000000001})
        n_old = _run_relogin(t7, new_src, "1", stamp={"ts": time.time() - 400, "pid": os.getpid()})
    for tag, res, who in (("ancestor_fresh", n_anc, "bot_ctl stop"), ("foreign_pid", n_bad, "UNATTRIBUTED"),
                          ("stale_stamp", n_old, "UNATTRIBUTED")):
        ls = [ln for ln in res[2].splitlines() if "SIGINT_ATTRIB" in ln]
        mm = LINE_RE.match(ls[0]) if len(ls) == 1 else None
        check(f"relogin_flag_on_{tag}_{who.split()[0].lower()}", bool(mm) and mm["who"] == who
              and ls[0].endswith(f"-> {who}"), (ls, res[2][-300:]))
    check("relogin_head_interrupt_is_nonzero", h[0] != 0 and "KeyboardInterrupt" in h[2], (h[0], h[2][-300:]))
    check("relogin_exit_code_flag_unset_same_as_head", n_off[0] == h[0], (n_off[0], h[0]))
    check("relogin_exit_code_flag_0_same_as_head", n_zero[0] == h[0], (n_zero[0], h[0]))
    check("relogin_exit_code_flag_on_same_as_head", n_on[0] == h[0], (n_on[0], h[0]))
    check("relogin_traceback_kept_flag_on", "KeyboardInterrupt" in n_on[2], n_on[2][-300:])
    check("relogin_flag_off_no_attrib_anywhere", all("SIGINT_ATTRIB" not in x for x in (
        n_off[1], n_off[2], n_off[3], n_zero[1], n_zero[2], n_zero[3])), (n_off[2], n_off[3]))
    on_lines = [ln for ln in n_on[2].splitlines() if "SIGINT_ATTRIB" in ln]
    check("relogin_flag_on_one_stderr_line", len(on_lines) == 1 and bool(LINE_RE.match(on_lines[0])), on_lines)
    check("relogin_flag_on_log_line", sum("SIGINT_ATTRIB" in ln for ln in n_on[3].splitlines()) == 1, n_on[3])
    check("relogin_flag_on_stdout_clean", "SIGINT_ATTRIB" not in n_on[1], n_on[1])


def main():
    test_helper_format()
    test_helper_never_raises()
    test_helper_real_ctypes_and_stamp_file()
    test_shutdown_handler()
    test_ast_differential()
    test_bot_ctl()
    test_relogin_entry()
    print()
    if _fails:
        print(f"{len(_fails)} FAILED: {_fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
