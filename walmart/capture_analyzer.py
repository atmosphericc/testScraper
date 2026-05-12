"""Auto-extract GraphQL operation hashes + payload shapes from checkout
capture JSONL files.

Scans every `walmart/logs/checkout_capture_*.jsonl` and emits a Python
dict of `{operation_name: {hash, sample_post_body, sample_response_body,
seen_count, last_seen}}`. Used to populate `walmart/checkout_api.py`
with new mutation hashes as we discover them.

Usage:
    python -m walmart.capture_analyzer                    # scan all + print
    python -m walmart.capture_analyzer --json out.json    # write JSON
    python -m walmart.capture_analyzer --emit-py          # emit Python
                                                          # snippet for
                                                          # checkout_api.py
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


_OP_HASH_RE = re.compile(r"/graphql/([A-Za-z][A-Za-z0-9]*)/([a-f0-9]{40,})")


def scan_capture(path: str) -> dict[str, dict[str, Any]]:
    """Parse one JSONL and return {op_name: aggregated_info}."""
    ops: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "hashes": defaultdict(int),       # hash → count
        "request_count": 0,
        "response_count": 0,
        "sample_post_body": None,
        "sample_response_body": None,
        "last_seen_ts": 0.0,
        "methods": defaultdict(int),
        "endpoints": defaultdict(int),    # cartxo vs home
    })
    try:
        for line in open(path):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            url = rec.get("url", "")
            m = _OP_HASH_RE.search(url)
            if not m:
                continue
            op_name, op_hash = m.group(1), m.group(2)
            info = ops[op_name]
            info["hashes"][op_hash] = info["hashes"].get(op_hash, 0) + 1
            ts = rec.get("ts") or 0.0
            if ts > info["last_seen_ts"]:
                info["last_seen_ts"] = ts
            ep_match = re.search(r"/orchestra/([^/]+)/graphql/", url)
            if ep_match:
                ns = ep_match.group(1)
                info["endpoints"][ns] = info["endpoints"].get(ns, 0) + 1

            event = rec.get("event")
            if event == "request":
                info["request_count"] += 1
                info["methods"][rec.get("method", "?")] = (
                    info["methods"].get(rec.get("method", "?"), 0) + 1
                )
                if info["sample_post_body"] is None:
                    pb = rec.get("post_data") or rec.get("postData") or rec.get("body") or ""
                    if pb and isinstance(pb, str) and pb.startswith("{"):
                        info["sample_post_body"] = pb[:60000]  # cap for sanity
            elif event == "response_body":
                info["response_count"] += 1
                if info["sample_response_body"] is None:
                    body = rec.get("body") or rec.get("body_text") or ""
                    if body and isinstance(body, str):
                        info["sample_response_body"] = body[:60000]
    except FileNotFoundError:
        return {}
    return dict(ops)


def merge_op_data(
    aggregate: dict[str, dict[str, Any]],
    new: dict[str, dict[str, Any]],
) -> None:
    for op, info in new.items():
        if op not in aggregate:
            aggregate[op] = {
                "hashes": defaultdict(int),
                "request_count": 0,
                "response_count": 0,
                "sample_post_body": None,
                "sample_response_body": None,
                "last_seen_ts": 0.0,
                "methods": defaultdict(int),
                "endpoints": defaultdict(int),
            }
        agg = aggregate[op]
        for h, c in info["hashes"].items():
            agg["hashes"][h] = agg["hashes"].get(h, 0) + c
        agg["request_count"] += info["request_count"]
        agg["response_count"] += info["response_count"]
        if agg["sample_post_body"] is None and info.get("sample_post_body"):
            agg["sample_post_body"] = info["sample_post_body"]
        if agg["sample_response_body"] is None and info.get("sample_response_body"):
            agg["sample_response_body"] = info["sample_response_body"]
        if info["last_seen_ts"] > agg["last_seen_ts"]:
            agg["last_seen_ts"] = info["last_seen_ts"]
        for m, c in info["methods"].items():
            agg["methods"][m] = agg["methods"].get(m, 0) + c
        for e, c in info["endpoints"].items():
            agg["endpoints"][e] = agg["endpoints"].get(e, 0) + c


def find_captures(root: str = "walmart/logs") -> list[str]:
    return sorted(glob.glob(os.path.join(root, "checkout_capture_*.jsonl")))


def summarize(ops: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten + sort: most recent first, then by request count."""
    out = []
    for op, info in ops.items():
        # Latest hash = the one with most occurrences (a rotated hash will
        # appear in older files; current one dominates)
        hashes_sorted = sorted(info["hashes"].items(), key=lambda x: -x[1])
        primary_hash = hashes_sorted[0][0] if hashes_sorted else None
        endpoint = (
            max(info["endpoints"].items(), key=lambda x: x[1])[0]
            if info["endpoints"] else "?"
        )
        sample_method = (
            max(info["methods"].items(), key=lambda x: x[1])[0]
            if info["methods"] else "?"
        )
        out.append({
            "op": op,
            "hash": primary_hash,
            "endpoint": endpoint,
            "method": sample_method,
            "request_count": info["request_count"],
            "response_count": info["response_count"],
            "has_post_sample": bool(info["sample_post_body"]),
            "has_response_sample": bool(info["sample_response_body"]),
            "all_hashes": list(info["hashes"].keys()),
            "last_seen_ts": info["last_seen_ts"],
        })
    out.sort(key=lambda x: (-x["request_count"], x["op"]))
    return out


def print_table(rows: list[dict[str, Any]]) -> None:
    """Pretty-print to stdout."""
    print(f"{'Operation':<32} {'Method':<7} {'NS':<8} {'Req':>5} {'Resp':>5} "
          f"{'PostBody':>9} {'RespBody':>9}  Hash")
    print("-" * 130)
    for r in rows:
        h = (r["hash"] or "?")[:16] + "…" if r["hash"] else "?"
        print(
            f"{r['op']:<32} {r['method']:<7} {r['endpoint']:<8} "
            f"{r['request_count']:>5} {r['response_count']:>5} "
            f"{'yes' if r['has_post_sample'] else '—':>9} "
            f"{'yes' if r['has_response_sample'] else '—':>9}  {h}"
        )


def emit_py_snippet(ops: dict[str, dict[str, Any]]) -> str:
    """Emit a Python snippet of `OP_NAME_HASH = "..."` constants suitable
    for pasting into checkout_api.py."""
    lines = ["# Auto-extracted from checkout_capture_*.jsonl",
             f"# Generated {time.strftime('%Y-%m-%d %H:%M:%S')}",
             ""]
    rows = summarize(ops)
    for r in rows:
        if not r["hash"]:
            continue
        const_name = re.sub(r"([A-Z])", r"_\1", r["op"]).upper().strip("_") + "_HASH"
        lines.append(f"{const_name} = \"{r['hash']}\"  # {r['op']} ({r['method']} /orchestra/{r['endpoint']}/...)")
    return "\n".join(lines)


def find_interesting_ops(ops: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Heuristic — flag ops likely involved in tip/slot/fulfillment so we
    know which post bodies to read for the full-API flow."""
    keywords = {
        "tip": ["tip", "tipping"],
        "slot": ["slot", "reserveSlot", "BookSlot", "bookslot"],
        "fulfillment": ["fulfillment", "intent"],
        "cart": ["cart", "addItem", "addToCart"],
        "order": ["order", "Contract", "placeOrder"],
        "payment": ["payment", "PIE", "card"],
    }
    out = {}
    for category, kws in keywords.items():
        matches = []
        for op_name, info in ops.items():
            lc = op_name.lower()
            for kw in kws:
                if kw.lower() in lc:
                    matches.append(op_name)
                    break
        out[category] = matches
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", help="Write summary to JSON file")
    parser.add_argument(
        "--emit-py", action="store_true",
        help="Emit Python constants snippet for checkout_api.py"
    )
    parser.add_argument(
        "--root", default="walmart/logs",
        help="Capture log directory (default: walmart/logs)"
    )
    parser.add_argument(
        "--show-payload", metavar="OP_NAME",
        help="Print the captured POST body + response body for a specific op"
    )
    args = parser.parse_args(argv)

    captures = find_captures(args.root)
    if not captures:
        print(f"No captures found in {args.root}/", file=sys.stderr)
        return 1

    print(f"Scanning {len(captures)} capture file(s):", file=sys.stderr)
    for c in captures:
        print(f"  - {c}", file=sys.stderr)

    aggregate: dict[str, dict[str, Any]] = {}
    for c in captures:
        scan = scan_capture(c)
        merge_op_data(aggregate, scan)

    if args.show_payload:
        op = args.show_payload
        info = aggregate.get(op)
        if not info:
            print(f"Operation {op!r} not found. Available:", file=sys.stderr)
            for k in sorted(aggregate.keys()):
                print(f"  {k}", file=sys.stderr)
            return 1
        print(f"== {op} ==")
        print(f"Hashes seen: {list(info['hashes'].keys())}")
        if info.get("sample_post_body"):
            print("\n--- POST body ---")
            try:
                pretty = json.dumps(json.loads(info["sample_post_body"]), indent=2)
                print(pretty[:8000])
            except Exception:
                print(info["sample_post_body"][:8000])
        else:
            print("\n(no POST body captured — likely a GET query)")
        if info.get("sample_response_body"):
            print("\n--- response body ---")
            try:
                pretty = json.dumps(json.loads(info["sample_response_body"]), indent=2)
                print(pretty[:8000])
            except Exception:
                print(info["sample_response_body"][:8000])
        else:
            print("\n(no response body captured — Walmart didn't send one OR our "
                  "capture missed it; both happen)")
        return 0

    rows = summarize(aggregate)
    print_table(rows)

    print("\n--- Category groupings ---")
    interesting = find_interesting_ops(aggregate)
    for cat, ops in interesting.items():
        if ops:
            print(f"  {cat:<15} {ops}")
        else:
            print(f"  {cat:<15} (none captured yet)")

    if args.emit_py:
        print("\n--- Python snippet for checkout_api.py ---")
        print(emit_py_snippet(aggregate))

    if args.json:
        serializable = {
            op: {
                k: (dict(v) if hasattr(v, "items") else v)
                for k, v in info.items()
            }
            for op, info in aggregate.items()
        }
        Path(args.json).write_text(json.dumps(serializable, indent=2, default=str))
        print(f"\nWrote {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
