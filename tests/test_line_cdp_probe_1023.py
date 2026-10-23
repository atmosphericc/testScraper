#!/usr/bin/env python3
"""Offline test for tools/experiments/line_cdp_probe.py (2026-10-23): the argument parser and the SUMMARY parser
on synthetic log lines. No browser, no network, no proxy (the module import touches nothing live)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import line_cdp_probe as M  # noqa: E402

FAILS: list = []
CHECKS: list = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    CHECKS.append(name)
    if not cond:
        FAILS.append(name)


rc = subprocess.run([sys.executable, str(ROOT / "tools" / "experiments" / "line_cdp_probe.py"), "--help"],
                    capture_output=True, text=True, timeout=120)
check("help_runs", rc.returncode == 0 and "--proxy" in rc.stdout and "--nav-every-s" in rc.stdout, rc.stdout[-300:] + rc.stderr[-300:])

a = M.build_parser().parse_args(["--proxy", "resi:2", "--tag", "resi", "--minutes", "5"])
check("args_parse", a.proxy == "resi:2" and a.tag == "resi" and a.minutes == 5.0 and a.every_s == 10.0 and a.nav_every_s == 240.0
      and a.pdp == M.P.DECOY, vars(a))

lines = [
    "2026-10-23 09:00:00 [CDP_PROBE] tag=t start minutes=30",
    "2026-10-23 09:00:10 [CDP_PROBE] tag=t t=+0s eval_ms=40 ok=1 events=3 fwd_out_kb=1 fwd_in_kb=2 fwd_err=0 fwd_last=-",
    "2026-10-23 09:00:20 [CDP_PROBE] tag=t t=+10s eval_ms=400 ok=1 events=0 fwd_out_kb=5 fwd_in_kb=9 fwd_err=1 fwd_last=z",
    "2026-10-23 09:00:30 [CDP_PROBE] tag=t t=+20s eval_ms=60 ok=1 events=1 fwd_out_kb=6 fwd_in_kb=10 fwd_err=1 fwd_last=z",
    "2026-10-23 09:00:50 [CDP_PROBE] tag=t t=+30s eval TIMEOUT (>20s) fwd_err=1 fwd_last=z",
    "2026-10-23 09:01:00 [CDP_PROBE] tag=t NAV ms=4210 px_wall=0 title='a'",
    "2026-10-23 09:02:00 [CDP_PROBE] tag=t NAV ms=TIMEOUT px_wall=1 title='b'",
    "2026-10-23 09:02:10 some unrelated line eval_ms=9999",
]
s = M.summarize(lines)
check("summary_evals", s["evals"] == 3 and s["eval_p50_ms"] == 60 and s["eval_max_ms"] == 400 and s["eval_timeouts"] == 1, s)
check("summary_navs", s["navs"] == 1 and s["nav_p50_ms"] == 4210 and s["nav_timeouts"] == 1 and s["px_walls"] == 1, s)
check("summary_fwd_last_sample", s["fwd_out_kb"] == 6 and s["fwd_in_kb"] == 10 and s["fwd_err"] == 1, s)
check("summary_empty", M.summarize([])["evals"] == 0 and M.summarize([])["eval_p50_ms"] is None, M.summarize([]))
check("pct_bounds", M.pct([5, 1, 3], 0.0) == 1 and M.pct([5, 1, 3], 1.0) == 5 and M.pct([], 0.5) is None)

print(f"\n=== {len(CHECKS) - len(FAILS)}/{len(CHECKS)} passed ===")
if FAILS:
    print("  FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
