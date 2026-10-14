#!/usr/bin/env python3
"""Offline tests: the PreToolUse hook .claude/hooks/block_bot_launch.py (2026-10-14).

Until 10-14 the hook blocked every bot / login launch and every tests/ file outside OFFLINE,
and any command holding a for-loop, the word "tests" and python ANYWHERE (6 false positives
10-02..10-05; `ls tests/*.py` next to an unrelated python heredoc on 10-14). Operator 10-14:
"Remove the hook's block on live tests outside the OFFLINE list and its block on launching a
second bot". What still blocks is a BLANKET run -- tests/ files the command does not spell
out (the 09-14 incident).

Pins:
  - ALLOW: every bot / login / smoke launch (app.py, -m app, test_app.py, relogin_one.py,
    test_resilient_stack.py, the wrapper .bat, hand_login*.bat, bot_ctl), named live tests
    (python and pytest, alone or in a for-loop over literal names), loops that run no tests/
    file, `python -c` over a file list, a tests/ glob in a statement without python, heredoc /
    commit-message mentions, the offline runner
  - BLOCK: the 09-14 incident (`for t in tests/test_*.py`), $(...) / $VAR / glob items,
    an unexpanded tests/$T.py, nested loops, while-read, xargs, find -exec, ForEach-Object,
    bash -c loops, `python tests/test_*.py`, `python tests/`, bare / directory / -k-only pytest
  - main(): exit 2 with a reason on stderr when blocked, exit 0 otherwise

No browser, no network. Run: venv/Scripts/python.exe tests/test_block_bot_launch_hook.py
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "block_bot_launch.py"
spec = importlib.util.spec_from_file_location("block_bot_launch", HOOK)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}  {str(detail)[:300]}")


ALLOW = {
    # the 10-02..10-05 false positives (offline names in a loop, loops that run no test)
    "loop_offline_1002": 'for t in test_held_line_strike test_fast_lane_golden test_fast_lane_checkout; do echo "=== $t"; python -u tests/$t.py 2>&1 | tail -8; echo "exit=${PIPESTATUS[0]}"; done',
    "loop_offline_timeout_1003": 'for f in test_fast_lane_golden test_fast_lane_checkout test_won_cart_direct_smoke; do echo "== $f"; timeout 900 python -u tests/$f.py 2>&1 | grep -E "passed|FAIL" | tail -5; done',
    "loop_offline_subshell_1003": 'S=/tmp/s; for t in test_fast_lane_golden test_held_line_strike; do (timeout 300 python -u tests/$t.py > "$S/$t.out" 2>&1; echo "rc=$?" >> "$S/$t.out"); done; tail -3 "$S"/*.out',
    "loop_not_tests_1002": 'python tests/test_events_parser.py > out.txt 2>&1; echo "exit=$?"; for q in po_by_age late_carts; do echo "## $q"; python tools/events/q.py $q --run run_x; done',
    "loop_echo_only_1003": 'for v in a b; do echo "$v $(diff x y | wc -l)"; done; python -u tests/test_won_cart_direct_smoke.py > o.txt',
    "loop_python_c_1005": "for f in src/a.py app.py relogin_one.py tests/run_offline_suite.py; do python -c \"import sys; b=open('$f','rb').read(); print(b.count(b'x'))\"; done",
    "glob_without_python_1014": "cd /repo && python - <<'EOF'\nprint(1)\nEOF\nls tests/ | wc -l; ls tests/*.py | wc -l",
    "loop_dynamic_no_tests": 'for f in logs/runs/*.log; do python tools/events/build.py "$f"; done',
    "loop_newline_offline": "for t in test_fast_lane_golden\ndo\n  python tests/$t.py\ndone",
    "commit_message_mention": "git commit -q -F - <<'EOF'\nfor t in tests/test_*.py; do python $t; done is what we block; run pytest and the suite\nEOF",
    "grep_mention": "grep -n 'python app.py' tests/test_events_parser.py docs/FLOW.md",
    "offline_runner": "python -u tests/run_offline_suite.py > suite.txt 2>&1; tail -3 suite.txt",
    "offline_single": "python tests/test_events_parser.py",
    # operator 2026-10-14: named live tests run
    "live_test": "python tests/test_qty2_atc_smoke.py",
    "live_test_windows_path": "venv\\Scripts\\python.exe tests\\test_idle_then_detect_then_purchase.py",
    "live_pytest": "pytest tests/test_qty2_atc_smoke.py -x",
    "live_pytest_node": "python -m pytest tests/test_qty2_atc_smoke.py::test_one",
    "loop_live_names": "for t in test_fast_lane_golden test_qty2_atc_smoke; do python -u tests/$t.py; done",
    "loop_live_newline": "for t in test_qty2_atc_smoke\ndo\n  python tests/$t.py\ndone",
    "loop_pytest_live": "for t in test_qty2_atc_smoke; do pytest tests/$t.py; done",
    "loop_then_live": "for t in test_fast_lane_golden; do python tests/$t.py; done; python tests/test_qty2_atc_smoke.py",
    # operator 2026-10-14: bot / login / smoke launches, a second bot included
    "app": "python app.py",
    "app_module": "python -m app",
    "test_app": "venv/Scripts/python.exe test_app.py",
    "relogin": "venv/Scripts/python.exe relogin_one.py all",
    "smoke": "RESILIENT_TEST_DURATION_S=120 python test_resilient_stack.py",
    "wrapper_bat": "run_bot_with_nightly_restart.bat",
    "wrapper_start": "cmd /c start run_bot_with_nightly_restart.bat",
    "hand_login": "cmd /c hand_login_primary.bat",
    "bot_ctl": "venv/Scripts/python.exe tools/ops/bot_ctl.py restart",
}

BLOCK = {
    "incident_0914": "for t in tests/test_*.py; do python $t; done",
    "loop_cmd_subst": "for t in $(ls tests); do python tests/$t; done",
    "loop_var_items": 'FILES=$(ls tests/*.py); for f in $FILES; do python "$f"; done',
    "loop_nested": "for a in x; do for t in test_qty2_atc_smoke; do python tests/$t.py; done; done",
    "unexpanded_var": "T=test_qty2_atc_smoke; python tests/$T.py",
    "bash_c_loop": "bash -c 'for t in tests/test_*.py; do python $t; done'",
    "xargs": "ls tests/*.py | xargs -n1 python",
    "find_exec": "find tests -name 'test_*.py' -exec python {} \\;",
    "while_read": 'ls tests/*.py | while read f; do python "$f"; done',
    "foreach_object": "Get-ChildItem tests\\*.py | ForEach-Object { python $_.FullName }",
    "python_glob": "python tests/test_*.py",
    "python_dir": "python tests/",
    "bare_pytest": "pytest",
    "pytest_dir": "pytest tests/ -x",
    "pytest_k_only": "python -m pytest -k atc",
    "pytest_glob": "pytest tests/test_*.py",
}

for name, cmd in ALLOW.items():
    r = h.check_command(cmd)
    check(f"allow:{name}", r is None, r)

for name, cmd in BLOCK.items():
    r = h.check_command(cmd)
    check(f"block:{name}", bool(r), "not blocked")

# main(): exit code + stderr, through a real subprocess with the hook's JSON on stdin.
env = dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT))
for cmd, want in (("pytest tests/", 2), ("echo hi", 0), ("python app.py", 0),
                  ("for t in tests/test_*.py; do python $t; done", 2)):
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}),
                       capture_output=True, text=True, env=env, cwd=str(ROOT / "logs"), timeout=30)
    check(f"main:{cmd!r}", p.returncode == want, f"rc={p.returncode} err={p.stderr.strip()}")
    if want == 2:
        check(f"main_reason:{cmd!r}", "[block_bot_launch] BLOCKED" in p.stderr, p.stderr)

print(f"===== {PASS} passed, {FAIL} failed =====")
sys.exit(1 if FAIL else 0)
