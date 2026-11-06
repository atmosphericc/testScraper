#!/usr/bin/env python3
"""Offline test: limkey_guest_follow slot grid, /24 mask, ASN parse, block readout, permutation p (2026-11-04).
No browser, no network. Run: python tests/test_guest_follow_slots_1104.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import limkey_guest_follow as G  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --- slot scheduler -------------------------------------------------------------------------
check("slot: mid-grid", near(G.next_slot(100.3, 5.0, 40), 100.04 + 5.0))      # slots at 5k+0.04: 100.04, 105.04
check("slot: before this slot", near(G.next_slot(99.0, 5.0, 40), 100.04))
check("slot: strict next after 100.04", near(G.next_slot(100.04, 5.0, 40), 105.04))
check("slot: inclusive at 100.04", near(G.next_slot(100.04, 5.0, 40, inclusive=True), 100.04))
check("slot: wrap across the grid boundary", near(G.next_slot(104.99, 5.0, 40), 105.04))
check("slot: offset 0", near(G.next_slot(101.0, 5.0, 0), 105.0))
check("slot: offsets 40/80/120 land inside 250 ms",
      max(G.next_slot(999.9, 5.0, o) for o in (0, 40, 80, 120)) - min(G.next_slot(999.9, 5.0, o) for o in (0, 40, 80, 120)) < 0.25)
check("slot: result mod grid == offset", near((G.next_slot(1234.567, 5.0, 120) % 5.0), 0.12))
check("slot: offset beyond grid wraps", near(G.next_slot(100.3, 5.0, 5040), G.next_slot(100.3, 5.0, 40)))
check("slot: grid 0 returns now", G.next_slot(7.5, 0, 40) == 7.5)
# --- legacy interval when grid is 0 ------------------------------------------------------------
check("legacy: grid 0 -> now + cadence", G.next_shot_time(100.0, 5.0) == 105.0 and G.next_shot_time(100.0, 7.5, 0.0, 99) == 107.5)
check("grid on -> next slot", near(G.next_shot_time(100.3, 5.0, 5.0, 40), 105.04))
# --- /24 mask + ASN parse ----------------------------------------------------------------------
check("mask24", G.mask24("203.0.113.77") == "203.0.113.0/24")
check("mask24 invalid", G.mask24("ERR x") == "" and G.mask24("1.2.3") == "")
a = G.parse_asn('{"as":"AS15169 Google LLC","asname":"GOOGLE","isp":"Google LLC"}')
check("parse_asn", a == {"asn": "AS15169", "asname": "GOOGLE"}, str(a))
check("parse_asn bad", G.parse_asn("nope") == {"asn": "unknown", "asname": ""} and G.parse_asn("{}")["asn"] == "unknown")
import asyncio  # noqa: E402


async def _boom(url):
    raise TimeoutError()


async def _ok(url):
    assert "ip-api.com/json/203.0.113.9" in url
    return '{"as":"AS64500 X","asname":"EXAMPLE-NET"}'

check("lookup_asn failure non-fatal", asyncio.run(G.lookup_asn("203.0.113.9", _boom))["asn"] == "unknown")
check("lookup_asn ok", asyncio.run(G.lookup_asn("203.0.113.9", _ok)) == {"asn": "AS64500", "asname": "EXAMPLE-NET"})
check("lookup_asn bad ip", asyncio.run(G.lookup_asn("ERR", _ok))["asn"] == "unknown")
# --- block readout on a tiny synthetic set -------------------------------------------------------
T0 = 1_000_000_000_000


def shot(tag, off_ms, cls, w=T0, tcin="111111", role="hot"):
    return {"tag": tag, "tcin": tcin, "ts_ms": w + off_ms, "window_start_ms": w, "cls": cls, "role": role, "asn": "AS1"}


def mk(tag, w, pattern):
    # pattern: list of (block, [cls...])
    s = []
    for b, cl in pattern:
        for i, c in enumerate(cl):
            s.append(shot(tag, b * 30000 + 1000 * i, c, w))
    return {"tag": tag, "shots": s}

hi, lo = ["passed"] * 4, ["limited"] * 4
mid = ["passed", "limited", "passed", "limited"]
home = mk("home", T0, [(0, hi), (1, lo), (2, mid), (3, hi)])
bd = mk("bd0", T0 + 800, [(0, hi), (1, lo), (2, mid), (3, hi)])        # window start 800 ms apart: same cluster
bd["shots"].append(shot("bd0", 5000, "wall", T0 + 800))
bd["shots"].append(shot("bd0", 6000, "passed", T0 + 800, role="control"))
res = G.analyze_blocks([home, bd], perms=1000, seed=7)
c0 = [r for r in res["table"] if r["tag"] == "home" and r["block"] == 0]
check("blocks: cell counts", len(c0) == 1 and c0[0]["n"] == 4 and c0[0]["passed"] == 4 and c0[0]["limited"] == 0, str(c0))
check("blocks: shared cluster window", {r["window"] for r in res["table"]} == {T0}, str({r["window"] for r in res["table"]}))
check("blocks: remainder + control", res["remainder"] == {"wall": 1} and res["controls_excluded"] == 1, str(res["remainder"]))
check("level: pooled", res["level"]["home"]["passed"] == 10 and res["level"]["home"]["n"] == 16)
lo_w, hi_w = G.wilson(10, 16)
check("wilson 10/16 in [0.38,0.84]", 0.38 < lo_w < 0.40 and 0.80 < hi_w < 0.84, f"{lo_w:.3f} {hi_w:.3f}")
check("wilson n=0", G.wilson(0, 0) == (None, None))
pr = res["pairs"]["bd0|home"]
check("pair: identical series -> r = 1", near(pr["pearson"], 1.0) and pr["blocks"] == 4, str(pr))
check("pair: p small and deterministic", pr["perm_p"] <= 0.1 and
      G.analyze_blocks([home, bd], perms=1000, seed=7)["pairs"]["bd0|home"]["perm_p"] == pr["perm_p"], str(pr))
# exact p for 4 distinct values shuffled: only identity (and nothing else) reaches r=1 -> 1/24 ~ 0.0417
xs = [1.0, 0.0, 0.5, 1.0]
r, p = G.perm_pvalue([(xs, list(xs))], 2000, 3)
check("perm_p ~ 1/12 for one tied pair (identity + swap of the two 1.0s)", 0.05 < p < 0.12 and near(r, 1.0), f"r={r} p={p}")
anti = mk("anti", T0, [(0, lo), (1, hi), (2, mid), (3, lo)])
res2 = G.analyze_blocks([home, anti], perms=500, seed=1)
check("pair: anti-correlated r<0, p large", res2["pairs"]["anti|home"]["pearson"] < 0 and res2["pairs"]["anti|home"]["perm_p"] > 0.5)
flat = mk("flat", T0, [(0, hi), (1, hi), (2, hi)])
check("pair: zero variance -> None", G.analyze_blocks([home, flat], perms=50)["pairs"]["flat|home"]["pearson"] is None)
rep = G.format_block_report(res)
check("report prints sections", "co-movement" in rep and "Wilson" in rep and "remainder" in rep and "blk=0" in rep)
# --- CLI defaults reproduce today's -----------------------------------------------------------
a0 = G.build_parser().parse_args([])
check("cli defaults", a0.slot_grid_s == 0.0 and a0.slot_offset_ms == 0 and a0.per_window_cap is None and a0.nightly_cap is None
      and a0.max_per_window == 40 and a0.max_per_night == 1500)

print()
print("ALL PASS" if not FAILS else f"FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
