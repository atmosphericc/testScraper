#!/usr/bin/env python
"""Launch the pre-registered cross-ASN guest-follower lines for a drop, in one command.

Why: the decisive next-drop measurement (is a p-collapsed window GLOBAL, or just our
home ASN throttled?) needs guest shots fired from >= 2 distinct egress ASNs IN THE SAME
windows. The home line auto-runs via its nightly task; the BD and IPRoyal lines + the
BUY NOW arm are a MANUAL main-session launch at ~01:35 CT (docs/NEXT_DROP_CHECKLIST.md).
Firing five commands by hand at 01:35 is exactly how the 10-23 measurement got fumbled.
This launches them all, logs each PID + output path, and prints the readout commands.

MONEY-FREE: every line is a guest follower (no account, no checkout). BD lines are
flat-rate; the two IPRoyal lines cost per-GB, so this launches resiC/resiD only (two,
not four) and each carries --proactive-relaunch-s 720 to survive the IPRoyal CDP freeze.

Usage:
  python tools/experiments/launch_drop_followers.py --dry-run     # print, launch nothing
  python tools/experiments/launch_drop_followers.py               # launch all lines
  python tools/experiments/launch_drop_followers.py --no-resi     # BD + buynow only (0 resi GB)
  python tools/experiments/launch_drop_followers.py --no-buynow   # skip the BUY NOW arm
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLLOWER = ROOT / "tools" / "experiments" / "limkey_guest_follow.py"

# (tag, lock-name, [extra args]) — the home line is intentionally absent: it auto-runs
# via its own nightly task. These are the lines that must be added by hand.
LINES = [
    ("bd0",    "guest_follow_bd0",    ["--proxy", "reserve:0", "--proactive-relaunch-s", "720"]),
    ("bd1",    "guest_follow_bd1",    ["--proxy", "reserve:1", "--proactive-relaunch-s", "720"]),
    ("resiC",  "guest_follow_resiC",  ["--proxy", "resi:2",    "--proactive-relaunch-s", "720"], "resi"),
    ("resiD",  "guest_follow_resiD",  ["--proxy", "resi:3",    "--proactive-relaunch-s", "720"], "resi"),
    ("buynow", "guest_follow_buynow", ["--buynow"]),
]


def _cmd(tag, lock, extra):
    return [sys.executable, str(FOLLOWER), "--tag", tag, "--lock-name", lock, *extra]


def main():
    ap = argparse.ArgumentParser(description="Launch the cross-ASN drop followers in one shot.")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, launch nothing")
    ap.add_argument("--no-resi", action="store_true", help="skip the per-GB IPRoyal lines")
    ap.add_argument("--no-buynow", action="store_true", help="skip the BUY NOW arm")
    a = ap.parse_args()

    if not FOLLOWER.exists():
        print(f"ERROR: follower not found at {FOLLOWER}", file=sys.stderr)
        return 2

    selected = []
    for row in LINES:
        tag, lock, extra = row[0], row[1], row[2]
        kind = row[3] if len(row) > 3 else "bd"
        if a.no_resi and kind == "resi":
            continue
        if a.no_buynow and tag == "buynow":
            continue
        selected.append((tag, lock, extra, kind))

    print(f"Drop-follower launcher  (follower: {FOLLOWER.relative_to(ROOT)})")
    print(f"  home line is NOT launched here (it auto-runs via its nightly task).")
    print(f"  launching {len(selected)} line(s){' [DRY RUN]' if a.dry_run else ''}:\n")

    launched = []
    for tag, lock, extra, kind in selected:
        cmd = _cmd(tag, lock, extra)
        gb = " (per-GB)" if kind == "resi" else ""
        if a.dry_run:
            print(f"  [{tag:6}]{gb} {' '.join(cmd)}")
            continue
        # Detached background process; the follower writes its own guest_follow_<tag>_<stamp>.{log,json}.
        p = subprocess.Popen(cmd, cwd=str(ROOT),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        launched.append((tag, p.pid))
        print(f"  [{tag:6}]{gb} pid={p.pid}  {' '.join(cmd[2:])}")
        time.sleep(1.0)  # stagger Chrome launches so they don't contend on boot

    if a.dry_run:
        print("\n(dry run — nothing launched)")
        return 0

    print("\nLaunched. Each line writes logs/experiments/guest_follow_<tag>_<stamp>.{log,json}.")
    print("Verdict after the drop (per docs/NEXT_DROP_CHECKLIST.md VERDICT RULE):")
    print("  python tools/experiments/limkey_guest_follow.py --readout logs/experiments/guest_follow_*_<stamp>.json")
    print("Stop a line early: taskkill /PID <pid> /F  (or close the console).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
