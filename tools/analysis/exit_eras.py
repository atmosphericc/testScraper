#!/usr/bin/env python3
"""How far does each exit IP get through the gates, and did it ever work?

For every run log: which exit IPs the accounts were bound to (from the
`[FORWARDER] Wn/acct -> 127.0.0.1:PORT ... exit_ip=` lines, else the
`[FORWARDER] ... bind OK` lines), then the gate progression for that night:

  shots            every main-tab add-to-cart POST
  past G1          NOT an empty-body edge 429 and not a 401
                   (= Target's per-TCIN edge limiter let it reach the cart service)
  carts            ATC 201
  orders           order confirmations in that log

Grouped into the three proxy eras so "did Bright Data ever work" can be answered
with numbers instead of an impression.

Usage:  python tools/analysis/exit_eras.py
Read-only.
"""
from __future__ import annotations

import glob
import re
from collections import Counter, defaultdict

from shots import parse_shots

FWD = re.compile(r"\[FORWARDER\].*?(?:exit_ip=|bind OK.*?)(\d+\.\d+\.\d+\.\d+)")
PORTMAP = re.compile(r"\[FORWARDER\] (W\d)/([\w-]+) → 127\.0\.0\.1:(\d+)")
ORDER = re.compile(r"(\*\*\* ORDER PLACED \*\*\*|\[API_PLACE_ORDER\] Order placed)")

# Which exit IPs were in service when. From the forwarder/run logs.
ERAS = (
    ("A 07-13..08-06  all 3 accts on BD (RCN 31.x + Latitude 92.x)", "20260713", "20260807"),
    ("B 08-07..09-04  all 3 accts on BD, rotated onto Interworks 168.158.x", "20260807", "20260905"),
    ("C 09-11..09-18  primary HOME IP, business+alt-1 still on BD", "20260905", "20260999"),
)


def era_of(path: str):
    m = re.search(r"run_(\d{8})_", path)
    if not m:
        return None
    d = m.group(1)
    for name, lo, hi in ERAS:
        if lo <= d < hi:
            return name
    return None


def main() -> None:
    per_era = defaultdict(Counter)
    ips_seen = defaultdict(Counter)
    for path in sorted(glob.glob("logs/runs/run_2026*.log")):
        era = era_of(path)
        if era is None:
            continue
        shots = parse_shots(path)
        orders = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line[:4] == "2026":
                    continue
                if ORDER.search(line):
                    orders += 1
                f = FWD.search(line)
                if f:
                    ips_seen[era][f.group(1)] += 1
        c = per_era[era]
        c["logs"] += 1
        c["orders"] += orders
        for s in shots:
            c["shots"] += 1
            if s.cls == "401":
                c["401"] += 1
                continue
            c["non401"] += 1
            if s.cls != "edge429":
                c["past_G1"] += 1
            if s.cls == "201":
                c["carts"] += 1

    print("Gate progression by proxy era (every main-tab add-to-cart POST)\n")
    for name, _, _ in ERAS:
        c = per_era[name]
        if not c["shots"]:
            continue
        n1 = c["non401"]
        print(f"-- {name}")
        print(f"   logs={c['logs']}  shots={c['shots']}  (401 auth-denied {c['401']})")
        print(f"   past G1 edge limiter : {c['past_G1']}/{n1} = "
              f"{100.0 * c['past_G1'] / n1 if n1 else 0:.2f}% of non-401 shots")
        print(f"   became a CART        : {c['carts']}/{c['past_G1']} of those = "
              f"{100.0 * c['carts'] / c['past_G1'] if c['past_G1'] else 0:.1f}%   "
              f"({c['carts']}/{n1} = {100.0 * c['carts'] / n1 if n1 else 0:.3f}% of all non-401 shots)")
        print(f"   ORDERS               : {c['orders']}")
        top = ", ".join(f"{ip}" for ip, _ in ips_seen[name].most_common(6))
        print(f"   exit IPs seen        : {top or '-'}\n")


if __name__ == "__main__":
    main()
