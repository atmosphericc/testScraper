#!/usr/bin/env python3
"""Offline test for tools/experiments/e1_readout.py (2026-10-22). No browser, no network, no bot: it exercises the
pure helpers (run span, follower-file selection by stamp, the R-CAD-1022 rule) on temp files and checks --help.
"""
import importlib.util
import subprocess
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "experiments" / "e1_readout.py"
FAILS = []


CHECKS: list = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    CHECKS.append(name)
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

    # follower files: start stamps inside [first - 4 h, last], every tag (10-23: the followers of a night start
    # BEFORE the run log that carries the drop -- 23:40/23:49 followers vs a 00:10 relaunch -- and were skipped)
    R.EXPS = td
    for name in ("guest_follow_20261023_234003.json", "guest_follow_bd0_20261023_234101.json", "guest_follow_resi_20261023_060000.json",
                 "guest_follow_e2a_20261022_094416.json", "guest_follow_bd1_20261023_070000.json", "limkey_guest_20261023_020000.json",
                 "guest_follow_resi_20261022_234943.json", "guest_follow_20261022_234003.json", "guest_follow_e2x_20261022_193000.json"):
        (td / name).write_text("{}", encoding="utf-8")
    picked = sorted(p.name for p in R.follower_files("20261023_013100", "20261023_064012"))
    check("follower_files_in_span_all_tags", picked == ["guest_follow_20261022_234003.json", "guest_follow_resi_20261022_234943.json",
                                                        "guest_follow_resi_20261023_060000.json"], picked)
    picked2 = sorted(p.name for p in R.follower_files("20261023_233000", "20261024_063000"))
    check("follower_files_night_span", picked2 == ["guest_follow_20261023_234003.json", "guest_follow_bd0_20261023_234101.json"], picked2)
    # the 10-23 shape: run log opened 00:10, followers started 23:40 / 23:49 the evening before -> all picked;
    # a 19:30 daytime arm (4.7 h before) and the 09:44 E2 arm stay out
    picked3 = sorted(p.name for p in R.follower_files("20261023_001006", "20261023_064323"))
    check("follower_files_lookback_4h", picked3 == ["guest_follow_20261022_234003.json", "guest_follow_resi_20261022_234943.json",
                                                    "guest_follow_resi_20261023_060000.json"], picked3)
    picked4 = sorted(p.name for p in R.follower_files("20261023_001006", "20261023_064323", lookback_h=0))
    check("follower_files_no_lookback_old_rule", picked4 == ["guest_follow_resi_20261023_060000.json"], picked4)
    check("stamp_minus_hours", R._stamp_minus_hours("20261023_001006", 4.0) == "20261022_201006" and R._stamp_minus_hours("bad", 4.0) == "bad")

    # usage section: the follower's traffic line lives in the .log beside the .json (10-23: json-only read -> 'no egress/traffic line' x4)
    jf = td / "guest_follow_bd9_20261023_234000.json"
    lf = td / "guest_follow_bd9_20261023_234000.log"
    jf.write_text(json.dumps({"tag": "bd9", "log": str(lf)}), encoding="utf-8")
    lf.write_text(chr(10).join(["2026-10-23 23:40:10 [GUEST_FOLLOW] egress tag=bd9 ip=31.105.x.x home=67.184.x.x proxy=brd.superproxy.io:33335",
                             "2026-10-24 01:00:00 [GUEST_FOLLOW] egress tag=bd9 ip=31.105.x.x home=67.184.x.x proxy=brd.superproxy.io:33335",
                             "2026-10-24 05:25:39 [GUEST_FOLLOW] tag=bd9 traffic tag=bd9 proxy=brd.superproxy.io:33335 out_mb=52.81 in_mb=376.95 total_mb=429.76", ""]),
                  encoding="utf-8")
    srcs = [p.name for p in R.follower_text_sources(jf)]
    check("follower_text_sources_log_first", srcs[0] == lf.name and srcs[-1] == jf.name, srcs)
    usage = R.residential_usage(log, [jf], [])
    check("usage_reads_companion_log", any("total_mb=429.76" in u and "egress tag=bd9" in u for u in usage), usage)
    check("usage_last_egress_only", sum(u.count("egress tag=bd9") for u in usage) == 1, usage)   # 7 relaunches = 7 egress lines; show the last
    usage2 = R.residential_usage(log, [td / "guest_follow_zz_20261023_234000.json"], [])
    check("usage_missing_follower_no_crash", any("no egress/traffic line" in u for u in usage2), usage2)

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

n_pass = len(CHECKS) - len(FAILS)
print(f"\n=== {n_pass}/{len(CHECKS)} passed ===")
if FAILS:
    print("  FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
