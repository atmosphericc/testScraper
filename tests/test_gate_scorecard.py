#!/usr/bin/env python3
"""Offline wiring test for tools/analysis/gate_scorecard.py.

Hermetic: no event store, no network, no subprocess to q.py (subprocess.run is
faked). Proves the scorecard is wired to real queries and that its output
framing (tail, prefix, defensive paths) is correct.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "analysis"))
import gate_scorecard as gs  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


class _Fake:
    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


def _with_fake_run(fake, fn):
    orig = gs.subprocess.run
    gs.subprocess.run = lambda *a, **k: fake
    try:
        return fn()
    finally:
        gs.subprocess.run = orig


def test_gates_cover_the_five_gate_model():
    headings = " ".join(h for h, _, _, _ in gs.GATES)
    for tok in ("DETECTION", "EDGE LIMITER", "SHAPE BANK", "FAST_SELLING"):
        check(f"scorecard names {tok}", tok in headings)
    check("every gate row is a 4-tuple", all(len(g) == 4 for g in gs.GATES))


def test_every_query_name_has_a_sql_file():
    qdir = os.path.join(REPO, "tools", "events", "queries")
    for _, q, _, _ in gs.GATES:
        check(f"query '{q}' has a .sql (no typo/drift)", os.path.isfile(os.path.join(qdir, q + ".sql")))


def test_run_query_tails_and_prefixes():
    fake = _Fake(stdout="\n".join(f"line{i}" for i in range(10)))
    out = _with_fake_run(fake, lambda: gs.run_query("admits", None, 3))
    lines = out.splitlines()
    check("tail=3 returns 3 lines", len(lines) == 3)
    check("lines are indented", all(ln.startswith("    ") for ln in lines))
    check("tail keeps the LAST lines", "line9" in out and "line0" not in out)


def test_run_query_defensive_on_empty():
    out = _with_fake_run(_Fake(stdout="", stderr=""), lambda: gs.run_query("admits", None, 5))
    check("empty output -> defensive 'no output' hint", "no output" in out)


def test_run_query_reports_stderr():
    out = _with_fake_run(_Fake(stdout="", stderr="Traceback\nboom happened"), lambda: gs.run_query("admits", None, 5))
    check("stderr with no stdout -> 'query error'", "query error" in out and "boom happened" in out)


def test_run_query_passes_run_filter():
    captured = {}

    def spy(cmd, **kw):
        captured["cmd"] = cmd
        return _Fake(stdout="x")
    orig = gs.subprocess.run
    gs.subprocess.run = spy
    try:
        gs.run_query("first_po_age", "20261116", 5)
    finally:
        gs.subprocess.run = orig
    check("run id is forwarded as --run", "--run" in captured["cmd"] and "20261116" in captured["cmd"])


def main():
    for t in (test_gates_cover_the_five_gate_model, test_every_query_name_has_a_sql_file,
              test_run_query_tails_and_prefixes, test_run_query_defensive_on_empty,
              test_run_query_reports_stderr, test_run_query_passes_run_filter):
        t()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
