#!/usr/bin/env python3
"""FX-1118 px-recovery-watch: track the home IP's PerimeterX flag decaying -- hands-off.

WHY THIS EXISTS
    The home line got self-flagged by the harvest hammering the cross-origin Press & Hold
    wall ~6944x/night (see .claude/state/CURRENT_STATE.md, FX-1118). The CAUSE is already
    removed (park-on-filled-wall + quiet window + the guest-follow re-burn tasks disabled),
    so the flag now DECAYS on its own over DAYS of near-zero bot load. Nothing has to be run
    for the flag to clear -- this watcher only MEASURES the decay and flips the bot back on
    automatically once the line reads clean, so the operator never has to run a command,
    reboot the router, or babysit a trend script.

WHAT IT DOES (once/day, from the TargetBot\\px-recovery-watch scheduled task)
    1. If state/px_recovered.flag already exists -> clean already confirmed; do nothing, no load.
    2. Otherwise run the MONEY-SAFE guest probe (tools/experiments/harvest_px_probe.py
       --urls "" --loads 2): 6 PDP DOM-reads total, NO login / cart / checkout EVER, a fresh
       temp profile, os._exit before the library's hooks. 6 loads/day is negligible next to the
       ~6944/night that caused the burn -- it does not keep the flag alive.
    3. Read the fresh harvest_px_probe_*.json summary.overall_wall_rate, append it to
       state/px_recovery.json (the ledger) and a human line to logs/px_recovery_watch.log.
    4. RECOVERED when the two most recent valid readings are BOTH <= RECOVERED_WALL (0.45):
       comfortably below the flagged band (~0.83-1.00) and clear of the ~0.33 residential
       noise floor. On recovery: remove state/ensure_armed.disable so the SANCTIONED nightly
       auto-arm (23:15) brings the bot back on its own that night, and write state/px_recovered.flag
       so this never fires twice. It never CREATES a disable -- it only lifts the one we set.

    venv\\Scripts\\python.exe tools\\ops\\px_recovery_watch.py            # one real cycle
    venv\\Scripts\\python.exe tools\\ops\\px_recovery_watch.py --selftest  # offline logic check, no network
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
PROBE = ROOT / "tools" / "experiments" / "harvest_px_probe.py"
EXP_DIR = ROOT / "logs" / "experiments"
STATE = ROOT / "state"
LEDGER = STATE / "px_recovery.json"
WATCH_LOG = ROOT / "logs" / "px_recovery_watch.log"
DISABLE = STATE / "ensure_armed.disable"
RECOVERED_FLAG = STATE / "px_recovered.flag"

RECOVERED_WALL = 0.45        # <= this on the last 2 readings == flag cleared (baseline ~0.33, flagged ~0.83-1.0)
MIN_ANSWERED = 4             # a reading with fewer answered loads is inconclusive, not counted
PROBE_LOADS = 2              # per decoy; 3 decoys -> 6 PDP reads total
PROBE_TIMEOUT_S = 900        # hard cap on the probe subprocess


def _log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    try:
        WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with WATCH_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _read_ledger() -> list[dict]:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_ledger(rows: list[dict]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def decide_recovered(rows: list[dict]) -> bool:
    """Pure: True iff the two most recent valid (answered>=MIN) readings are both <= RECOVERED_WALL."""
    valid = [r for r in rows
             if isinstance(r.get("wall_rate"), (int, float))
             and (r.get("answered") or 0) >= MIN_ANSWERED]
    if len(valid) < 2:
        return False
    last2 = valid[-2:]
    return all(r["wall_rate"] <= RECOVERED_WALL for r in last2)


def _run_probe() -> dict | None:
    """Run the money-safe guest probe; return the fresh readout dict, or None if it produced none."""
    start = time.time()
    cmd = [str(VENV_PY), str(PROBE), "--urls", "", "--loads", str(PROBE_LOADS)]
    _log(f"probe: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=str(ROOT), timeout=PROBE_TIMEOUT_S,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        _log("probe: TIMEOUT -- inconclusive this cycle")
        return None
    except Exception as e:                                   # noqa: BLE001
        _log(f"probe: launch error {e!r} -- inconclusive this cycle")
        return None
    fresh = sorted((f for f in EXP_DIR.glob("harvest_px_probe_*.json")
                    if f.stat().st_mtime >= start - 5),
                   key=lambda f: f.stat().st_mtime)
    if not fresh:
        _log("probe: no fresh readout produced (launch guard tripped) -- inconclusive this cycle")
        return None
    try:
        return json.loads(fresh[-1].read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        _log(f"probe: unreadable readout {e!r} -- inconclusive this cycle")
        return None


def _recover_now(rows: list[dict]) -> None:
    """Lift the dark-hold so the nightly auto-arm resumes; mark recovered so this fires once."""
    lifted = False
    if DISABLE.exists():
        try:
            DISABLE.unlink()
            lifted = True
        except Exception as e:                               # noqa: BLE001
            _log(f"RECOVERED but could NOT remove {DISABLE.name}: {e!r} -- remove it by hand to resume")
    RECOVERED_FLAG.write_text(json.dumps({
        "recovered_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "readings": rows[-2:],
    }, indent=2), encoding="utf-8")
    tail = "nightly auto-arm (23:15) will bring the bot back tonight" if lifted \
        else "nightly auto-arm was already enabled"
    _log(f"RECOVERED: home wall% back at baseline on the last 2 readings -> {tail}. "
         f"Flag cleared; px-recovery-watch is now idle.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="offline logic check, no probe/network")
    a = ap.parse_args()

    if a.selftest:
        cases = [
            ([], False),
            ([{"wall_rate": 0.4, "answered": 6}], False),                                  # need 2
            ([{"wall_rate": 0.9, "answered": 6}, {"wall_rate": 0.4, "answered": 6}], False),  # 1 high
            ([{"wall_rate": 0.4, "answered": 6}, {"wall_rate": 0.44, "answered": 6}], True),   # both clean
            ([{"wall_rate": 0.4, "answered": 6}, {"wall_rate": 0.46, "answered": 6}], False),  # 2nd over thresh
            ([{"wall_rate": 0.3, "answered": 2}, {"wall_rate": 0.3, "answered": 6}], False),   # one low-n ignored
            ([{"wall_rate": 0.83, "answered": 6}, {"wall_rate": 0.9, "answered": 6}], False),  # flagged
        ]
        ok = True
        for rows, want in cases:
            got = decide_recovered(rows)
            flag = "OK " if got == want else "FAIL"
            if got != want:
                ok = False
            print(f"{flag} decide_recovered -> {got} (want {want})  {rows}")
        print("SELFTEST", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    if RECOVERED_FLAG.exists():
        _log("already recovered (state/px_recovered.flag present) -- idle, no probe.")
        return 0

    d = _run_probe()
    rows = _read_ledger()
    if d is not None:
        s = d.get("summary") or {}
        rate = s.get("overall_wall_rate")
        answered = s.get("answered")
        rows.append({
            "date": f"{datetime.now():%Y-%m-%d}",
            "wall_rate": rate,
            "answered": answered,
            "started": d.get("started"),
        })
        _write_ledger(rows)
        pct = "?" if not isinstance(rate, (int, float)) else f"{rate * 100:.0f}%"
        _log(f"reading: home PDP wall {pct} (answered={answered}); baseline clean ~33%, flagged ~83-100%.")

    if decide_recovered(rows):
        _recover_now(rows)
    else:
        last = next((r for r in reversed(rows) if isinstance(r.get("wall_rate"), (int, float))), None)
        where = "" if not last else f" (latest {last['wall_rate'] * 100:.0f}%)"
        _log(f"still flagged{where} -- staying dark, re-checking tomorrow. Decay takes days of low load.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
