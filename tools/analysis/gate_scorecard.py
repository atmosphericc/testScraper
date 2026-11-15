#!/usr/bin/env python3
"""Gate scorecard: one command that prints the per-gate proof board for the
current (2026-11) 5-gate hot-SKU funnel, tied to the fixes made this session.

It does NOT re-implement any verdict logic. Each gate delegates to the existing
pre-registered event-store query (tools/events/q.py ...), captures its output,
and frames it under the gate it proves with a plain-language PASS criterion. The
queries are the source of truth; this is a guided runner so the operator (or a
post-drop review) gets the whole board from a single call.

    python tools/analysis/gate_scorecard.py [--run 20261116]

Read-only. Build the store first: python tools/events/build.py

The gates (see docs/CLAIMS.md C-1114-xx and the harvest/FS memory):
  G0 Detection   see the restock without a rate-limit ban (home-IP apps_raw sweep).
  G1 Edge+disp   get a shot past the ATC edge limiter, and dispatch fast (R-LAG).
  G2/A Shape bank the admitted shot carries a LIVE banked_replay credential, not
                 the page_signed fallback (the 11-06 Wall A drain is the failure).
  G3 FAST_SELLING land the FIRST place-order inside the <~3s window the lottery allows.

Final proof of any gate is a live drop; this board is what makes that drop legible.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
Q = os.path.join(REPO, "tools", "events", "q.py")

# gate -> (heading, query name, what PASS looks like at the next drop, tail lines)
GATES = [
    ("G0  DETECTION",
     "coverage",
     "sweep covered the drop window with 0 rate-limit bans (home-IP apps_raw <=0.5/s). "
     "True proof is the boot sweep clean + a flip detected; coverage is the store-side proxy.",
     8),
    ("G1  EDGE LIMITER + DISPATCH SPEED",
     "dispatch_lag",
     "R-LAG SKIP (busy-window first-shot median <=1000ms, p90 <=2000ms). "
     "BUILD C2 means dispatch is too slow -> implement MISS-PHASE-TICK-1015.",
     6),
    ("G1b EDGE ADMISSIONS (did shots get past, early?)",
     "admits",
     "shots reach the cart service (past>0) and cluster at window OPEN (open10 high, first_age_ms small).",
     10),
    ("G2 / WALL A  SHAPE BANK (the 11-06 drain fix)",
     "cred_wall2",
     "banked_replay past_limiter >=20 AND reached_pct in the ~29-43% band. "
     "If >90% of admitted shots are page_signed -> CONFOUNDED = the bank was walled empty (Wall A failed).",
     8),
    ("G3  FAST_SELLING (first place-order window-age)",
     "first_po_age",
     "the run's reading is EVERY_WIN_LE_3S / LE_4_4S (a win landed young); NO_WIN means Gate 3 held.",
     14),
]


def run_query(name: str, run: str | None, tail: int) -> str:
    cmd = [sys.executable, Q, name]
    if run:
        cmd += ["--run", run]
    try:
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=120)
    except Exception as e:  # pragma: no cover - defensive
        return f"    (query failed to launch: {type(e).__name__}: {e})"
    out = (p.stdout or "").rstrip("\n")
    err = (p.stderr or "").strip()
    if not out and err:
        return f"    (query error: {err.splitlines()[-1][:160]})"
    if not out:
        return "    (no output -- is the event store built? run: python tools/events/build.py)"
    lines = out.splitlines()
    shown = lines[-tail:] if tail and len(lines) > tail else lines
    return "\n".join("    " + ln for ln in shown)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=None, help="restrict to one run id, e.g. 20261116")
    a = ap.parse_args()

    scope = f"run {a.run}" if a.run else "ALL runs in the store (pass --run <id> for one drop)"
    print("=" * 72)
    print(f"  GATE SCORECARD  --  {scope}")
    print("  final proof of any gate is a live drop; this is the legible board for it")
    print("=" * 72)
    for heading, query, pass_when, tail in GATES:
        print(f"\n#### {heading}   [proof query: q.py {query}]")
        print(f"  PASS WHEN: {pass_when}")
        print(run_query(query, a.run, tail))
    print("\n" + "=" * 72)
    print("  Re-run this after the next boot's drop to prove each gate from fresh data.")
    print("=" * 72)


if __name__ == "__main__":
    main()
