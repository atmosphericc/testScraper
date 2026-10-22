#!/usr/bin/env python3
"""E1 / R-CAD-1022 one-command post-drop readout (2026-10-22). READ-ONLY. Never launches anything.

    python tools/experiments/e1_readout.py                 # newest run log > 1 MB, every guest_follow*.json of its night
    python tools/experiments/e1_readout.py --run run_20261023_0131xx [--no-build] [--json]

What it does, in order (each step is a tool that already exists; this file only sequences and prints them):
  1. tools/events/build.py --only <run>            (idempotent; the store must hold the run before the follower
                                                    readout can score members)
  2. tools/experiments/limkey_guest_follow.py --readout <every logs/experiments/guest_follow*_<stamp>.json whose
     stamp lies inside the run span, ALL tags in one call>   -> E1: per-tag rows, cross_line verdicts, wall2 per tag
  3. tools/events/q.py first_shot_era                 -> R-CAD-1022 / R-ERA: the home line's first-contact volleys, the
                                                        era table and the WATCH/BREAK flag (read as printed)
  4. tools/events/q.py walls --run <run>              -> wall-1 / wall-2 counts per account x first/later
  5. greps on the run log that the store does not carry: [FAST_CADENCE] (must be 0 under the 10-22 revert),
     "cold re-entry" (the wave-first re-entry the revert restores), [AMBIGUOUS_COMMIT], ORDER PLACED, [REBUY]

Pre-registered rules it prints (it never decides for you):
  E1 (per non-home tag vs home, tools/experiments/limkey_guest_follow.py docstring):
      LINE-DIFFERS         -> the edge limiter keys on the network line; fund LINES, not accounts
      SAME-FATE-ALL-LINES  -> concordant with passes on >= 3 windows: the lines share a fate
      INCONCLUSIVE (concordant zero / near-zero) -> underpowered; repeat
      wall2 per tag: keyless_401 (Shape refused) vs hop_424_201 (Shape admitted) among shots past wall 1
  R-CAD-1022 (fast cadence reverted 10-22, alternating-night arm):
      WORKED       home first-contact volleys past wall 1 >= 3 of the night's volleys (10-16 under fast cadence: 0/7)
      NO-SIGNAL    0-2 of n volleys (one night cannot separate it from 10-16; keep the arm for the next drop)
      INVALID      [FAST_CADENCE] > 0 in the run log (the revert was not live)
      plus the keyless-401 share of past-limiter shots (10-01: 80/105 under slow cadence; nothing since passed wall 1)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "logs" / "runs"
EXPS = ROOT / "logs" / "experiments"
PY = sys.executable


def newest_run(min_bytes: int = 1_000_000) -> Path | None:
    cands = [p for p in RUNS.glob("run_*.log") if p.stat().st_size >= min_bytes]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def run_span(run_log: Path) -> tuple[str, str]:
    """(first, last) 'YYYYMMDD_HHMMSS' stamps of the run from its first and last timestamped lines."""
    first = last = None
    pat = re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})")
    with run_log.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat.match(line)
            if m:
                stamp = "".join(m.groups()[:3]) + "_" + "".join(m.groups()[3:])
                first = first or stamp
                last = stamp
    return first or "", last or ""


def follower_files(first: str, last: str) -> list[Path]:
    """Every guest_follow*_<stamp>.json whose stamp lies in [first, last] (all tags; file name carries the tag)."""
    out = []
    for p in sorted(EXPS.glob("guest_follow*_????????_??????.json")):
        m = re.search(r"_(\d{8}_\d{6})\.json$", p.name)
        if m and first <= m.group(1) <= last:
            out.append(p)
    return out


def sh(args: list[str], timeout: int = 900) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT), encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
    except Exception as e:  # noqa: BLE001
        return 99, f"{type(e).__name__}: {e}"


def grep_counts(run_log: Path) -> dict:
    keys = {"fast_cadence": "[FAST_CADENCE]", "cold_reentry": "cold re-entry", "ambiguous_commit": "[AMBIGUOUS_COMMIT]",
            "order_placed": "ORDER PLACED", "rebuy": "[REBUY]", "wave_first": "[WAVE_FIRST]"}
    counts = {k: 0 for k in keys}
    with run_log.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            for k, needle in keys.items():
                if needle in line:
                    counts[k] += 1
    return counts


def cadence_rule(first_shot_era_text: str, run_id: str, counts: dict) -> dict:
    """R-CAD-1022 from the printed first_shot_era table: the run's row gives volleys_past / volleys_classified."""
    verdict, n, past = "NO-DATA", None, None
    for line in first_shot_era_text.splitlines():
        if line.startswith(run_id):
            cols = line.split()
            # columns: run_id first_shots past limited unclass shot_pct volleys volleys_past volleys_classified volley_pct ...
            try:
                past, n = int(cols[7]), int(cols[8])
            except (IndexError, ValueError):
                pass
    if counts.get("fast_cadence", 0) > 0:
        verdict = "INVALID ([FAST_CADENCE] > 0: the revert was not live)"
    elif n is None:
        verdict = "NO-DATA (no home first-contact volley for this run in first_shot_era)"
    elif past >= 3:
        verdict = f"WORKED ({past}/{n} home volleys past wall 1 vs 0/7 on 10-16)"
    else:
        verdict = f"NO-SIGNAL ({past}/{n} home volleys; one night cannot separate it from 10-16's 0/7)"
    return {"verdict": verdict, "volleys_past": past, "volleys": n, "fast_cadence_lines": counts.get("fast_cadence", 0),
            "cold_reentry_lines": counts.get("cold_reentry", 0)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="", help="run id (run_YYYYMMDD_HHMMSS); default = newest run log > 1 MB")
    ap.add_argument("--no-build", action="store_true", help="skip tools/events/build.py --only <run>")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of sections")
    a = ap.parse_args(argv)

    run_log = (RUNS / f"{a.run}.log") if a.run else newest_run()
    if not run_log or not run_log.exists():
        print("no run log found"); return 2
    run_id = run_log.stem
    first, last = run_span(run_log)
    out: dict = {"run": run_id, "span": [first, last], "steps": {}}

    if not a.no_build:
        rc, txt = sh([PY, "tools/events/build.py", "--only", run_id])
        out["steps"]["build"] = {"rc": rc, "tail": txt[-600:]}

    files = follower_files(first, last)
    out["follower_files"] = [p.name for p in files]
    if files:
        rc, txt = sh([PY, "tools/experiments/limkey_guest_follow.py", "--readout", *[str(p) for p in files]])
        try:
            out["e1"] = json.loads(txt)
        except Exception:  # noqa: BLE001
            out["e1"] = {"rc": rc, "raw": txt[-3000:]}
    else:
        out["e1"] = "no follower file for this run (no guest ran, or stamps outside the run span)"

    rc, era = sh([PY, "tools/events/q.py", "first_shot_era"])
    out["steps"]["first_shot_era"] = {"rc": rc, "text": era[-4000:]}
    rc, walls = sh([PY, "tools/events/q.py", "walls", "--run", run_id])
    out["steps"]["walls"] = {"rc": rc, "text": walls[-4000:]}
    counts = grep_counts(run_log)
    out["greps"] = counts
    out["r_cad_1022"] = cadence_rule(era, run_id, counts)

    if a.json:
        print(json.dumps(out, indent=1, ensure_ascii=False)); return 0
    print(f"=== E1 / R-CAD-1022 readout: {run_id}  span {first} .. {last} ===")
    print(f"follower files ({len(files)}): {', '.join(p.name for p in files) or '-'}")
    e1 = out["e1"]
    if isinstance(e1, dict):
        print(f"home verdict: {e1.get('verdict')}  windows={e1.get('windows')} any_pass={e1.get('any_pass')}")
        for tag, cl in (e1.get("cross_line") or {}).items():
            print(f"  cross_line[{tag}]: {cl.get('verdict')}  windows={cl.get('windows')} bd_only_pass={cl.get('bd_only_pass')} "
                  f"home_only_pass={cl.get('home_only_pass')} reverse={cl.get('reverse')} same={cl.get('same')} any_pass={cl.get('any_pass')}")
        for tag, w2 in (e1.get("wall2") or {}).items():
            print(f"  wall2[{tag}]: past_wall1={w2.get('past_wall1')} keyless_401={w2.get('keyless_401')} "
                  f"hop_424_201={w2.get('hop_424_201')} other={w2.get('other_passed')}")
    else:
        print(f"E1: {e1}")
    print(f"R-CAD-1022: {out['r_cad_1022']['verdict']}  (fast_cadence lines {counts['fast_cadence']}, cold re-entry {counts['cold_reentry']})")
    print(f"money greps: ambiguous_commit={counts['ambiguous_commit']} order_placed={counts['order_placed']} rebuy={counts['rebuy']}")
    print("--- first_shot_era ---"); print(era[-2500:])
    print("--- walls ---"); print(walls[-2500:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
