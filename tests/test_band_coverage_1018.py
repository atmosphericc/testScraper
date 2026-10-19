#!/usr/bin/env python3
"""Offline test for tools/events/queries/band_coverage.sql (F4, readiness audit 2026-10-18).

A synthetic event store in a temp dir (runs / monitor_stats / windows only), the query run through the real
tools/events/q.py as a subprocess in read-only mode. No bot, no browser, no network, no build.py rebuild.
When the main tree's gitignored logs/events/events.sqlite is reachable, the pre-registered weekday table is also
checked against it (read-only); otherwise that part SKIPs.
Run: python tests/test_band_coverage_1018.py
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Q = ROOT / "tools" / "events" / "q.py"
OFFSET = -300                                   # CDT: local = utc + offset

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") + ",000"


def local_to_ms(dt: datetime) -> int:
    return int((dt.replace(tzinfo=timezone.utc) - timedelta(minutes=OFFSET)).timestamp() * 1000)


def build_store(path: Path):
    con = sqlite3.connect(str(path))
    con.executescript("""
CREATE TABLE runs (run_id TEXT PRIMARY KEY, file TEXT, first_ts TEXT, last_ts TEXT, lines INTEGER, parser_version INTEGER, utc_offset_min INTEGER, tz_src TEXT, atc_resp_form TEXT, flip_log INTEGER);
CREATE TABLE monitor_stats (run_id TEXT, line INTEGER, ts_ms INTEGER, ts TEXT, t_s REAL, sweeps INTEGER, s200 INTEGER, s403 INTEGER, s429 INTEGER, other INTEGER, beh INTEGER, outstanding INTEGER);
CREATE TABLE windows (run_id TEXT, window_id INTEGER, tcin TEXT, first_read_ms INTEGER, last_read_ms INTEGER, end_ms INTEGER, reads INTEGER, flips INTEGER, races INTEGER, shots INTEGER, trunc INTEGER, line INTEGER, src TEXT);
""")

    def run(run_id, segments):
        """segments: [(start_dt, end_dt)] -> one monitor_stats row per 30 s (ts = row end), the run's first/last stamps."""
        n = 0
        for a, b in segments:
            t = a
            while t <= b:
                n += 1
                con.execute("INSERT INTO monitor_stats(run_id,line,ts_ms,ts,sweeps,s200) VALUES (?,?,?,?,?,?)",
                            (run_id, n, local_to_ms(t), ts(t), n, n))
                t += timedelta(seconds=30)
        first, last = segments[0][0], segments[-1][1]
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", (run_id, run_id + ".log", ts(first), ts(last + timedelta(seconds=5)), n, 7, OFFSET, "t", "x", 1))

    def window(run_id, wid, tcin, local_dt):
        con.execute("INSERT INTO windows(run_id,window_id,tcin,first_read_ms,src) VALUES (?,?,?,?,?)", (run_id, wid, tcin, local_to_ms(local_dt), "flip"))

    D = datetime
    # run A: Thu 10-08 23:00 -> Fri 10-09 06:00, continuous. Fri 10-09 band fully covered; 3 windows (2 in band, 1 after it).
    run("run_A", [(D(2026, 10, 8, 23, 0, 0), D(2026, 10, 9, 6, 0, 0))])
    window("run_A", 1, "1010892001", D(2026, 10, 9, 3, 10))
    window("run_A", 2, "1010892002", D(2026, 10, 9, 4, 0))
    window("run_A", 3, "1010892001", D(2026, 10, 9, 6, 0, 1))          # outside the band (06:00)
    # run B: Sun 10-11 01:00 -> 03:30 (the band is covered only 02:00-03:30 = 90 min), no windows
    run("run_B", [(D(2026, 10, 11, 1, 0, 0), D(2026, 10, 11, 3, 30, 0))])
    # run C: Wed 10-14 01:00 -> 03:00, a 5.5 min hole, 03:05:30 -> 05:40  => 60 + 145 = 205 min in the band; 1 window at 02:00 sharp
    run("run_C", [(D(2026, 10, 14, 1, 0, 0), D(2026, 10, 14, 3, 0, 0)), (D(2026, 10, 14, 3, 5, 30), D(2026, 10, 14, 5, 40, 0))])
    window("run_C", 1, "1010892003", D(2026, 10, 14, 2, 0, 0))
    con.commit()
    con.close()


def q(db: Path):
    r = subprocess.run([sys.executable, str(Q), "band_coverage", "--db", str(db), "--json"], capture_output=True, text=True,
                       timeout=120, cwd=str(ROOT), encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None, r.stderr[-600:]
    return json.loads(r.stdout), r.stderr


def test_synthetic():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "events.sqlite"
        build_store(db)
        before = db.stat().st_mtime_ns
        out, err = q(db)
        check("bc_runs_rc0", out is not None, err)
        if out is None:
            return
        weekday, days = out
        by_day = {r["date"]: r for r in days}
        by_wd = {r["weekday"]: r for r in weekday}
        fri, sun, wed = by_day["2026-10-09"], by_day["2026-10-11"], by_day["2026-10-14"]
        check("bc_fri_full_band", fri["covered_min"] == 210.0 and fri["wd"] == "Fri", fri)
        check("bc_fri_windows_in_band", fri["win_in_band"] == 2 and fri["tcins"] == 2, fri)
        check("bc_fri_prev_run", fri["prev_run"] is None or fri["prev_run"], fri)
        check("bc_sun_partial_cover", sun["covered_min"] == 90.0 and sun["win_in_band"] == 0 and sun["wd"] == "Sun", sun)
        check("bc_sun_prev_marker", sun["prev_run"] == "run_A" and sun["prev_mon_end"] == "2026-10-09 06:00:00" and sun["gap_h"] == 44.0, sun)
        check("bc_sat_not_monitored", by_day["2026-10-10"]["covered_min"] == 0.0 and by_day["2026-10-10"]["prev_run"] == "run_A", by_day["2026-10-10"])
        check("bc_gap_over_120s_breaks", wed["covered_min"] == 205.0, wed)
        check("bc_boundary_window_counted", wed["win_in_band"] == 1 and wed["wd"] == "Wed", wed)
        check("bc_mon_prev_run_b", by_day["2026-10-12"]["prev_run"] == "run_B" and by_day["2026-10-12"]["covered_min"] == 0.0, by_day["2026-10-12"])
        # last date = the last monitor_stats date; dates before the data are 'not monitored'
        check("bc_range", days[0]["date"] == "2026-09-01" and days[-1]["date"] == "2026-10-14", (days[0]["date"], days[-1]["date"]))
        check("bc_weekday_table", by_wd["Fri"]["covered_bands"] == 1 and by_wd["Fri"]["with_window"] == 1
              and by_wd["Wed"]["covered_bands"] == 1 and by_wd["Wed"]["with_window"] == 1
              and by_wd["Sun"]["covered_bands"] == 1 and by_wd["Sun"]["with_window"] == 0 and by_wd["Sun"]["covered_empty"] == 1
              and by_wd["Mon"]["covered_bands"] == 0 and by_wd["Mon"]["with_window"] == 0, weekday)
        check("bc_weekday_sums", sum(r["covered_bands"] for r in weekday) == 3 and sum(r["with_window"] for r in weekday) == 2, weekday)
        check("bc_read_only", db.stat().st_mtime_ns == before)


def test_real_store():
    """The pre-registered table (since 2026-09-01, covered bands only): Fri 7 covered / 6 with a window, Wed 4/3, Thu 2/1, Sat-Tue 13/0."""
    cands = [ROOT / "logs" / "events" / "events.sqlite"] + [p / "logs" / "events" / "events.sqlite" for p in ROOT.parents[:3]]
    db = next((c for c in cands if c.exists()), None)
    if db is None:
        print("[SKIP] no event store reachable (gitignored); synthetic test only")
        return
    out, err = q(db)
    check("bc_real_store_runs", out is not None, err)
    if out is None:
        return
    weekday, days = out
    # the store keeps growing: pin only the dates up to the audit (2026-10-16) by re-aggregating the per-date rows
    agg = {}
    for r in days:
        if r["date"] <= "2026-10-16" and r["covered_min"] > 0:
            a = agg.setdefault(r["wd"], [0, 0])
            a[0] += 1
            a[1] += 1 if r["win_in_band"] > 0 else 0
    check("bc_real_covered_counts", agg.get("Fri", [0])[0] == 7 and agg.get("Wed", [0])[0] == 4 and agg.get("Thu", [0])[0] == 2
          and sum(agg.get(k, [0])[0] for k in ("Sat", "Sun", "Mon", "Tue")) == 13, agg)
    # window counts depend on the parser version of the stored runs (a rebuild at parser v7 may add windows): report, do not pin
    print(f"[INFO] real-store with_window (covered, <= 10-16): " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(agg.items())))


def main() -> int:
    for t in (test_synthetic, test_real_store):
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for f in FAILED:
        print("  FAILED:", f)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
