#!/usr/bin/env python3
"""
analyze_queue_capture.py — turn one real Walmart drop into actionable diffs.

Reads the JSONL artifacts produced by walmart/queue_events.py:
  - logs/queue_events_<ts>.jsonl   (timeline: always on)
  - logs/queue_capture_<ts>.jsonl  (raw bodies: WALMART_QUEUE_CAPTURE=1)

and prints:
  1. TIMELINE — the drop reconstructed (entered → admitted → ATC → order),
     with the admission latency, which = the real post-admission CHECKOUT
     WINDOW you have on drop night (a number nobody has measured yet).
  2. TICKET SHAPE — the real api.waiting-room.walmart.com response vs. the
     fields walmart/queue_handler.py currently assumes. Flags missing/extra
     keys so parse_ticket_response can be corrected against reality.
  3. GRAPHQL HASHES — the live persisted-query hashes seen in checkout POSTs
     vs. the hardcoded constants in walmart/checkout_api.py. THIS is the payoff:
     a mismatch is exactly why CreateContract 400s on drop night, and the
     captured value is the fix (paste it into checkout_api.py + APQ bodies).
  4. FAILURES — every 456 / PX-block / non-2xx status, tallied.

Usage:
    python analyze_queue_capture.py                       # newest capture+events
    python analyze_queue_capture.py logs/queue_capture_20260817_231502.jsonl
    python analyze_queue_capture.py --events logs/queue_events_20260817_231502.jsonl

Read-only. stdlib only.
"""

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"

# Current hardcoded hashes in walmart/checkout_api.py — the analyzer diffs the
# captured hashes against these. Kept as a literal map (not imported) so the
# analyzer runs even if checkout_api's import chain is unavailable; if these
# drift, update here too. The URL carries them as .../graphql/<Op>/<hash>.
CURRENT_HASHES = {
    "updateItems":         "8f04790148c52a6bd70449c7c6c56d57f74fec0301878d6ffc50acc059343180",
    "CreateContract":      "cc8455e5a9158dc86b9b96656595396c110f231e148863107287c46ec5aa9144",
    "getSlots":            "284fc996d255acb14e392a7b83f3d8dc43caade8d921a518349fc20f44a11aa0",
    "reserveSlotMutation": "d004e26443acf233d4b4d7df47c79252b79ca0cf9d7e07f7f1ab42fbee5da87f",
    "getBookSlotPage":     "5d4c1134a0da62a2647db0144076f4186c6fffc70bba62beccd2412a56c566a7",
}

# Fields walmart/queue_handler.parse_ticket_response reads out of a ticket.
KNOWN_TICKET_FIELDS = {"queue", "ticket", "status", "state", "custom",
                       "admissionLikelihood", "nextRefreshRelativeTime",
                       "nextRefreshUnixTimestamp", "tickets"}

# Hex is matched case-insensitively so a captured hash is NEVER silently
# skipped over a case difference — a dropped hash would hide the exact
# mismatch this tool exists to surface. Compared case-insensitively below.
_HASH_URL_RE = re.compile(r"/graphql/([A-Za-z]+)/([0-9a-fA-F]{64})")


def _load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def _newest(pattern):
    hits = glob.glob(str(LOG_DIR / pattern))
    return max(hits, key=os.path.getmtime) if hits else None


def _fmt_ms(ms):
    if ms is None:
        return "  ? "
    return f"{ms/1000:6.1f}s"


def _section(title):
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def analyze_timeline(events):
    _section("1. TIMELINE")
    if not events:
        print("  (no event log — pass one with --events, or none was written)")
        return
    entered = {}      # session_id -> t_ms of queue_entered
    admitted = {}     # session_id -> t_ms of admitted
    for e in events:
        k = e.get("kind")
        sid = e.get("session_id")
        line = f"  {_fmt_ms(e.get('t_ms'))}  {k:<17}"
        extras = {kk: vv for kk, vv in e.items()
                  if kk not in ("t_ms", "ts", "kind")}
        if extras:
            line += "  " + " ".join(f"{kk}={vv}" for kk, vv in extras.items())
        print(line)
        if k == "queue_entered" and sid is not None and sid not in entered:
            entered[sid] = e.get("t_ms")
        if k == "admitted" and sid is not None and sid not in admitted:
            admitted[sid] = e.get("t_ms")

    # Admission latency per session = time spent in queue.
    print("\n  Admission latency (queue_entered → admitted):")
    any_pair = False
    for sid, t_in in entered.items():
        t_out = admitted.get(sid)
        if t_in is not None and t_out is not None:
            any_pair = True
            print(f"    {sid}: {(t_out - t_in)/1000:.1f}s in queue")
    if not any_pair:
        print("    (no entered→admitted pair captured this run)")

    # The checkout window = admitted → purchase_result.
    adm_ts = next((e.get("t_ms") for e in events if e.get("kind") == "admitted"), None)
    res_ts = next((e.get("t_ms") for e in events if e.get("kind") == "purchase_result"), None)
    if adm_ts is not None and res_ts is not None:
        print(f"\n  >> Admitted → order attempt completed: {(res_ts - adm_ts)/1000:.1f}s")
        print("     (this is how long the post-admission checkout actually took —")
        print("      compare to the finite window Walmart gives before the spot lapses)")


def analyze_tickets(capture):
    _section("2. TICKET SHAPE  (real api.waiting-room.walmart.com response)")
    tickets = [r for r in capture if r.get("channel") == "ticket_api"]
    if not tickets:
        print("  (no ticket_api bodies captured — run the drop with WALMART_QUEUE_CAPTURE=1)")
        return
    print(f"  {len(tickets)} ticket response(s) captured. First body, parsed:")
    first = tickets[0].get("body")
    parsed = None
    if isinstance(first, str):
        try:
            parsed = json.loads(first)
        except ValueError:
            print("  (first ticket body was not JSON — raw head:)")
            print("   " + first[:300])
    elif isinstance(first, (dict, list)):
        parsed = first
    if parsed is None:
        return
    # Unwrap {"tickets":[...]} or a bare list
    body = parsed
    if isinstance(body, dict) and isinstance(body.get("tickets"), list) and body["tickets"]:
        body = body["tickets"][0]
        print("  (unwrapped from {\"tickets\": [...]})")
    elif isinstance(body, list) and body:
        body = body[0]
        print("  (unwrapped from a top-level array)")
    if not isinstance(body, dict):
        print(f"  ticket body is a {type(body).__name__}, not an object — inspect raw.")
        return
    top_keys = set(body.keys())
    print(f"  top-level keys: {sorted(top_keys)}")
    custom = body.get("custom") if isinstance(body.get("custom"), dict) else {}
    if custom:
        print(f"  custom.* keys:  {sorted(custom.keys())}")
    # Diff vs what the parser knows
    seen = top_keys | set(custom.keys())
    unknown = seen - KNOWN_TICKET_FIELDS
    missing = {"queue", "ticket", "state"} - (top_keys | {k for k in ("state",) if isinstance(body.get("status"), dict) and "state" in body["status"]})
    if unknown:
        print(f"\n  ⚠ UNKNOWN fields the parser ignores (may carry signal): {sorted(unknown)}")
    if missing:
        print(f"  ⚠ EXPECTED fields NOT present as assumed: {sorted(missing)}")
    if not unknown and not missing:
        print("\n  ✓ shape matches what queue_handler.parse_ticket_response assumes.")


def analyze_hashes(capture):
    _section("3. GRAPHQL HASHES  (live vs. hardcoded in checkout_api.py)")
    posts = [r for r in capture if r.get("channel") in ("checkout", "atc")]
    seen = {}   # op -> hash
    for r in posts:
        m = _HASH_URL_RE.search(r.get("url", "") or "")
        if m:
            seen[m.group(1)] = m.group(2)
    if not seen:
        print("  (no checkout GraphQL POSTs captured — no hashes to compare)")
        return
    for op, live in seen.items():
        cur = CURRENT_HASHES.get(op)
        if cur is None:
            print(f"  {op}: {live}  (NEW op — not in checkout_api.py)")
        elif live.lower() == cur.lower():
            print(f"  {op}: MATCH ✓")
        else:
            print(f"  {op}: MISMATCH ✗")
            print(f"      hardcoded: {cur}")
            print(f"      LIVE:      {live}   ← update checkout_api.py to this")
    stale = [op for op, live in seen.items()
             if op in CURRENT_HASHES and live.lower() != CURRENT_HASHES[op].lower()]
    if stale:
        print(f"\n  >> {len(stale)} stale hash(es): {stale}. Paste the LIVE values into")
        print("     walmart/checkout_api.py constants AND the matching APQ bodies.")


def analyze_failures(capture, events):
    _section("4. FAILURES  (456 / PerimeterX / non-2xx)")
    px = [e for e in events if e.get("kind") == "px_blocked"]
    if px:
        print(f"  PerimeterX /blocked hits: {len(px)}")
        for e in px[:5]:
            print(f"    {_fmt_ms(e.get('t_ms'))}  session={e.get('session_id')}  {e.get('url')}")
    bad = []
    for r in capture:
        st = r.get("status")
        if isinstance(st, int) and st and (st == 456 or st >= 400):
            bad.append(r)
    if bad:
        print(f"\n  non-2xx checkout responses: {len(bad)}")
        for r in bad[:10]:
            print(f"    status={r.get('status')}  op={r.get('op')}  {str(r.get('body'))[:80]}")
    if not px and not bad:
        print("  none recorded. (If the drop went 0-for anyway, the loss was")
        print("   upstream of these hooks — check the event timeline for where it stopped.)")


def main():
    ap = argparse.ArgumentParser(description="Analyze a Walmart queue/checkout capture.")
    ap.add_argument("capture", nargs="?", help="path to queue_capture_*.jsonl (default: newest)")
    ap.add_argument("--events", help="path to queue_events_*.jsonl (default: newest)")
    args = ap.parse_args()

    cap_path = args.capture or _newest("queue_capture_*.jsonl")
    ev_path = args.events or _newest("queue_events_*.jsonl")

    if not cap_path and not ev_path:
        print("No capture or event files found in logs/. Run a drop first "
              "(WALMART_QUEUE_CAPTURE=1 for raw bodies).")
        return 1

    print("Walmart drop analysis")
    print(f"  events : {ev_path or '(none)'}")
    print(f"  capture: {cap_path or '(none)'}")

    events = _load_jsonl(ev_path) if ev_path and os.path.exists(ev_path) else []
    capture = _load_jsonl(cap_path) if cap_path and os.path.exists(cap_path) else []

    analyze_timeline(events)
    analyze_tickets(capture)
    analyze_hashes(capture)
    analyze_failures(capture, events)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
