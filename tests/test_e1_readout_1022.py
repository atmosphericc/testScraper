#!/usr/bin/env python3
"""Offline test for tools/experiments/e1_readout.py (2026-10-22). No browser, no network, no bot: it exercises the
pure helpers (run span, follower-file selection by stamp, the R-CAD-1022 rule) on temp files and checks --help.
"""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "experiments" / "e1_readout.py"
FAILS = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


spec = importlib.util.spec_from_file_location("e1_readout", TOOL)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

# --help runs (argparse) and names the four verdicts
rc = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True, text=True, cwd=str(ROOT))
check("help_runs", rc.returncode == 0 and "R-CAD-1022" in rc.stdout and "LINE-DIFFERS" in rc.stdout, rc.stdout[-300:])

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    log = td / "run_20261023_013100.log"
    log.write_text("junk line\n2026-10-23 01:31:00,123 INFO start\n[RACE] no stamp\n2026-10-23 06:40:12,000 INFO end\n", encoding="utf-8")
    first, last = R.run_span(log)
    check("run_span_first_last", (first, last) == ("20261023_013100", "20261023_064012"), (first, last))

    # follower files: only stamps inside the span, every tag
    R.EXPS = td
    for name in ("guest_follow_20261023_234003.json", "guest_follow_bd0_20261023_234101.json", "guest_follow_resi_20261023_060000.json",
                 "guest_follow_e2a_20261022_094416.json", "guest_follow_bd1_20261023_070000.json", "limkey_guest_20261023_020000.json"):
        (td / name).write_text("{}", encoding="utf-8")
    picked = sorted(p.name for p in R.follower_files("20261023_013100", "20261023_064012"))
    check("follower_files_in_span_all_tags", picked == ["guest_follow_resi_20261023_060000.json"], picked)
    picked2 = sorted(p.name for p in R.follower_files("20261023_233000", "20261024_063000"))
    check("follower_files_night_span", picked2 == ["guest_follow_20261023_234003.json", "guest_follow_bd0_20261023_234101.json"], picked2)

    # grep counts
    log2 = td / "run_x.log"
    log2.write_text("[FAST_CADENCE] W1\n[FAST_CADENCE] W2\nxx cold re-entry in 61 s\n[AMBIGUOUS_COMMIT] y\nORDER PLACED z\n", encoding="utf-8")
    c = R.grep_counts(log2)
    check("grep_counts", c["fast_cadence"] == 2 and c["cold_reentry"] == 1 and c["ambiguous_commit"] == 1 and c["order_placed"] == 1 and c["rebuy"] == 0, c)

# R-CAD-1022 rule on a synthetic first_shot_era table
era = ("run_id first_shots past limited unclass shot_pct volleys volleys_past volleys_classified volley_pct note\n"
       "run_20261015_231603 21 0 21 0 0.0 7 0 7 0.0\n"
       "run_20261023_013100 18 3 15 0 16.7 6 3 6 50.0\n")
v = R.cadence_rule(era, "run_20261023_013100", {"fast_cadence": 0, "cold_reentry": 12})
check("rule_worked", v["verdict"].startswith("WORKED") and v["volleys_past"] == 3 and v["volleys"] == 6, v)
v = R.cadence_rule(era, "run_20261015_231603", {"fast_cadence": 0, "cold_reentry": 0})
check("rule_no_signal", v["verdict"].startswith("NO-SIGNAL") and v["volleys_past"] == 0 and v["volleys"] == 7, v)
v = R.cadence_rule(era, "run_20261023_013100", {"fast_cadence": 57, "cold_reentry": 0})
check("rule_invalid_when_fast_cadence_live", v["verdict"].startswith("INVALID"), v)
v = R.cadence_rule(era, "run_20261099_000000", {"fast_cadence": 0, "cold_reentry": 0})
check("rule_no_data", v["verdict"].startswith("NO-DATA"), v)

n_pass = 10 - len(FAILS)
print(f"\n=== {n_pass}/10 passed ===")
if FAILS:
    print("  FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
