#!/usr/bin/env python3
"""PX recovery trend (FX-1118): watch the home IP's PerimeterX flag decay over time.

Reads the existing harvest_px_probe_*.json readouts (NO new network/load) and prints the
wall rate per probe run, oldest first, labelled home vs resi. The home IP got self-flagged
(see .claude/state/CURRENT_STATE.md); after removing the cause (park fix + quiet window +
disabled guest-follow re-burn) it should decay over DAYS toward the residential baseline
(~33% on a fresh resi IP). Recovery = the HOME-line wall rate falling from ~89-100% toward ~33%.

    venv\\Scripts\\python.exe tools\\analysis\\px_recovery_trend.py

To add a fresh data point (adds a little load -- do at most ~once/day while recovering):
    venv\\Scripts\\python.exe tools\\experiments\\harvest_px_probe.py --urls "" --loads 2
then re-run this.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "logs" / "experiments"


def _line_label(stem: Path) -> str:
    """home vs resi, read from the matching .log head (the JSON does not store it)."""
    log = stem.with_suffix(".log")
    try:
        head = log.read_text(encoding="utf-8", errors="replace")[:4000]
    except Exception:
        return "?"
    if "SECOND-LINE" in head or "exit_ip=" in head:
        return "resi"
    if "line=home" in head or "during_drop=" in head:
        return "home"
    return "home"          # the probe defaults to the home line


def main() -> int:
    files = sorted(EXP.glob("harvest_px_probe_*.json"))
    if not files:
        print("no harvest_px_probe_*.json yet -- run tools/experiments/harvest_px_probe.py first")
        return 0
    rows = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        s = d.get("summary") or {}
        rate = s.get("overall_wall_rate")
        try:
            when = datetime.strptime(str(d.get("started") or ""), "%Y%m%d_%H%M%S")
            when_s = when.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            when, when_s = None, str(d.get("started") or "?")
        surf = d.get("surface_summary") or {}
        rows.append({
            "when": when_s, "dt": when, "line": _line_label(f),
            "pdp_wall": None if rate is None else round(rate * 100, 1),
            "answered": s.get("answered"), "verdict": s.get("verdict"),
            "nonpdp_wall": surf.get("non_pdp_wall_rate") if surf else None,
        })
    rows.sort(key=lambda r: r["when"])
    print(f"{'when':16}  {'line':5}  {'pdp_wall%':>9}  {'nonpdp%':>7}  {'n':>3}  verdict")
    print("-" * 64)
    home_rates = []
    for r in rows:
        npw = "" if r["nonpdp_wall"] is None else f"{round(r['nonpdp_wall'] * 100, 1)}"
        pw = "" if r["pdp_wall"] is None else f"{r['pdp_wall']}"
        print(f"{r['when']:16}  {r['line']:5}  {pw:>9}  {npw:>7}  {str(r['answered'] or ''):>3}  {r['verdict'] or ''}")
        if r["line"] == "home" and r["pdp_wall"] is not None:
            home_rates.append((r["when"], r["pdp_wall"]))
    print()
    # Pre-burn clean reference: an old home reading at ~0% (before we self-flagged the IP).
    clean_ref = [r for r in rows if r["line"] == "home" and (r["pdp_wall"] or 0) <= 10]
    if clean_ref:
        print(f"pre-burn clean reference: {clean_ref[0]['pdp_wall']}% home on {clean_ref[0]['when']} "
              f"(the IP WAS clean before sustained botting flagged it).")
    # Recovery trend = home readings from the last 2 days only (exclude the stale pre-burn point, so
    # this tracks the CURRENT flag decaying, not the original burn).
    latest = max((r["dt"] for r in rows if r["dt"]), default=None)
    recent = [r for r in rows if r["line"] == "home" and r["dt"] and latest
              and (latest - r["dt"]).days < 2 and r["pdp_wall"] is not None]
    if len(recent) >= 2:
        first, last = recent[0], recent[-1]
        delta = last["pdp_wall"] - first["pdp_wall"]
        trend = ("FALLING — recovering" if delta <= -10 else
                 "rising — still being flagged" if delta >= 10 else
                 "flat — needs more idle time (decay takes days)")
        print(f"recovery trend (last 2d, home): {first['pdp_wall']}% ({first['when']}) -> "
              f"{last['pdp_wall']}% ({last['when']})  =>  {trend}")
    print("Target: home-line wall% falling toward the ~33% residential baseline = flag clearing.")
    print("PX reputation decays over DAYS of zero bot load, not hours; re-probe ~once/day.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
