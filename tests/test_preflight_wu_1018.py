#!/usr/bin/env python3
"""Offline tests for tools/predrop/preflight.py additions of 2026-10-18 (readiness audit).

  FS-A3  the Windows Update pause is compared with the DROP (+12 h), not with now; the live active hours replace the
         hard-coded text; a missing registry key gives a WARN row, never a crash (fake registry rows).
  F7     every `bat:NNNN` cite in the live sections of CURRENT_STATE.md must resolve to a `set` line of the wrapper.

Fake registry rows / synthetic state text / a temp repo root only: no PowerShell, no registry, no browser, no network, no bot.
Run: python tests/test_preflight_wu_1018.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "predrop"))

import preflight as PF   # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"[PASS] {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"[FAIL] {name}  {detail}")


def L(*a):
    return datetime(*a).timestamp()          # local epoch


def utc_str(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = L(2026, 10, 18, 21, 0)
DROP = L(2026, 10, 23, 2, 30)                # Fri 02:30 local


def reg(pause_until=None, start=9, end=3, **extra):
    r = {"PauseUpdatesExpiryTime": utc_str(pause_until) if pause_until else "", "ActiveHoursStart": start, "ActiveHoursEnd": end,
         "SmartActiveHoursState": None, "UserChoiceActiveHoursStart": None, "UserChoiceActiveHoursEnd": None}
    r.update(extra)
    return r


def rows_for(r, drop_ts=DROP, now=NOW):
    R = PF.Report()
    info = PF.wu_assess(r, drop_ts, now)
    PF.wu_rows(R, info, drop_ts)
    return info, {x["id"]: x for x in R.rows}


def test_active_hours_math():
    f = PF._in_active_hours
    check("ah_9_3_wraps", f(2, 9, 3) is True and f(23, 9, 3) is True and f(9, 9, 3) is True and f(3, 9, 3) is False and f(8, 9, 3) is False)
    check("ah_19_13_wraps", f(2, 19, 13) is True and f(14, 19, 13) is False and f(18, 19, 13) is False and f(19, 19, 13) is True and f(12, 19, 13) is True)
    check("ah_plain_range", f(10, 8, 22) is True and f(22, 8, 22) is False and f(7, 8, 22) is False)
    check("ah_unknown", f(2, None, 3) is None and f(2, 5, 5) is None)
    check("hour_parse", PF._hour_or_none("9") == 9 and PF._hour_or_none(None) is None and PF._hour_or_none(24) is None and PF._hour_or_none("x") is None)
    check("iso_parse", PF._parse_iso_ts("2026-10-22T05:01:59Z") == datetime(2026, 10, 22, 5, 1, 59, tzinfo=timezone.utc).timestamp()
          and PF._parse_iso_ts("2026-10-22T05:01:59.1234567Z") is not None and PF._parse_iso_ts("") is None and PF._parse_iso_ts("junk") is None)


def test_pause_vs_drop():
    # pause expiring BEFORE the drop (the real 10-18 situation: ends 10-22 00:01, drop Fri 10-23 02:30)
    info, st = rows_for(reg(pause_until=L(2026, 10, 22, 0, 1)))
    row = st.get("host.wu_pause_vs_drop", {})
    check("pause_before_drop_warn", row.get("status") == "WARN" and "BEFORE" in row.get("detail", "") and "26.5 h" in row.get("detail", ""), row)
    check("pause_before_drop_not_covering", info["covers_drop"] is False and info["paused"] is True)
    # pause ends after the drop but inside drop + 12 h -> still WARN (restart right after the window)
    info, st = rows_for(reg(pause_until=DROP + 5 * 3600))
    row = st.get("host.wu_pause_vs_drop", {})
    check("pause_inside_12h_warn", row.get("status") == "WARN" and "after" in row.get("detail", "") and not info["covers_drop"], row)
    # exactly drop + 12 h counts as covering; a week out certainly does
    info, st = rows_for(reg(pause_until=DROP + 12 * 3600))
    check("pause_exact_12h_ok", st["host.wu_pause_vs_drop"]["status"] == "OK" and info["covers_drop"] is True, st.get("host.wu_pause_vs_drop"))
    info, st = rows_for(reg(pause_until=DROP + 7 * 86400))
    check("pause_covering_ok", st["host.wu_pause_vs_drop"]["status"] == "OK" and info["covers_drop"] is True)
    # not paused at all (empty expiry / expired): the existing wu_pause row owns that WARN; no duplicate vs_drop row
    info, st = rows_for(reg(pause_until=None))
    check("not_paused_no_vs_drop_row", "host.wu_pause_vs_drop" not in st and info["paused"] is False)
    info, st = rows_for(reg(pause_until=NOW - 3600))
    check("expired_pause_no_vs_drop_row", "host.wu_pause_vs_drop" not in st and info["paused"] is False and info["covers_drop"] is False)


def test_active_hours_rows():
    pause_ok = DROP + 7 * 86400
    # 9-3 (the live value after the 10-16 auto-adjust): the 02:30 drop is inside the ACTIVE hours
    info, st = rows_for(reg(pause_until=L(2026, 10, 22, 0, 1), start=9, end=3))
    row = st["host.wu_active_hours"]
    check("ah_live_values_in_text", "09:00-03:00" in row["detail"] and "02:00" in row["detail"], row)
    check("ah_9_3_unpinned_warns", row["status"] == "WARN" and "auto-adjust" in row["detail"] and "no pause covers" in row["detail"], row)
    # 19-13: the 02:30 drop is also inside active hours; the 14:00 drop is OUTSIDE (allowed restart) -> WARN without a pause
    info, st = rows_for(reg(pause_until=None, start=19, end=13), drop_ts=L(2026, 10, 23, 14, 0))
    row = st["host.wu_active_hours"]
    check("ah_19_13_outside_warn", row["status"] == "WARN" and "19:00-13:00" in row["detail"] and "OUTSIDE" in row["detail"], row)
    # the same outside-hours drop with a covering pause: OK
    info, st = rows_for(reg(pause_until=L(2026, 10, 23, 14, 0) + 7 * 86400, start=19, end=13), drop_ts=L(2026, 10, 23, 14, 0))
    row = st["host.wu_active_hours"]
    check("ah_outside_but_paused_ok", row["status"] == "OK" and "pause covers" in row["detail"], row)
    # 9-3 with a covering pause and the 02:30 drop
    info, st = rows_for(reg(pause_until=pause_ok, start=9, end=3))
    check("ah_9_3_paused_ok", st["host.wu_active_hours"]["status"] == "OK", st["host.wu_active_hours"])
    # drop hour inside active hours AND something is present in the registry: OK (hours may still be auto-adjusted, noted)
    info, st = rows_for(reg(pause_until=None, start=9, end=3, SmartActiveHoursState=1))
    row = st["host.wu_active_hours"]
    check("ah_pinned_value_reported", row["status"] == "OK" and "SmartActiveHoursState=1" in row["detail"], row)
    # active hours unreadable
    info, st = rows_for(reg(pause_until=pause_ok, start=None, end=None))
    check("ah_missing_warn", st["host.wu_active_hours"]["status"] == "WARN" and "not readable" in st["host.wu_active_hours"]["detail"], st["host.wu_active_hours"])
    # no drop given: no rows at all (default output unchanged)
    R = PF.Report()
    PF.wu_rows(R, PF.wu_assess(reg(pause_until=pause_ok), None, NOW), None)
    check("no_drop_no_rows", R.rows == [], R.rows)


def test_missing_registry_never_crashes():
    info = PF.wu_assess({}, DROP, NOW)
    R = PF.Report()
    PF.wu_rows(R, info, DROP)
    st = {x["id"]: x for x in R.rows}
    check("empty_reg_warn_row", st["host.wu_registry"]["status"] == "WARN" and st["host.wu_active_hours"]["status"] == "WARN", st)
    check("none_reg_ok", PF.wu_assess(None, DROP, NOW)["have_reg"] is False)


def test_check_drop_overlap():
    def drop_rows(hour_dt, wu, paused_ok):
        R = PF.Report()
        PF.check_drop(R, hour_dt, paused_ok, wu)
        return {x["id"]: x for x in R.rows}
    wu_9_3 = PF.wu_assess(reg(start=9, end=3), None, NOW)
    wu_19_13 = PF.wu_assess(reg(start=19, end=13), None, NOW)
    # the old code warned for every 02:00-11:00 drop; with live 9-3 the 02:30 drop is inside active hours -> no overlap row
    check("overlap_9_3_0230_quiet", "drop.wu_overlap" not in drop_rows(DROP, wu_9_3, False))
    # 05:00 is outside 9-3 -> WARN naming the LIVE hours, not "end 02:00"
    st = drop_rows(L(2026, 10, 23, 5, 0), wu_9_3, False)
    check("overlap_9_3_0500_warn", st["drop.wu_overlap"]["status"] == "WARN" and "09:00-03:00" in st["drop.wu_overlap"]["detail"]
          and "end 02:00" not in st["drop.wu_overlap"]["detail"], st.get("drop.wu_overlap"))
    # 19-13: 14:00 outside -> WARN; 02:30 inside -> quiet (the old rule warned here)
    check("overlap_19_13_1400_warn", "drop.wu_overlap" in drop_rows(L(2026, 10, 23, 14, 0), wu_19_13, False))
    check("overlap_19_13_0230_quiet", "drop.wu_overlap" not in drop_rows(DROP, wu_19_13, False))
    # a covering pause silences it
    check("overlap_paused_quiet", "drop.wu_overlap" not in drop_rows(L(2026, 10, 23, 5, 0), wu_9_3, True))
    # no live hours (host checks skipped): the old 02:00-11:00 rule, with honest text
    st = drop_rows(DROP, None, False)
    check("overlap_fallback_rule", st["drop.wu_overlap"]["status"] == "WARN" and "not read" in st["drop.wu_overlap"]["detail"], st.get("drop.wu_overlap"))
    check("overlap_fallback_outside_quiet", "drop.wu_overlap" not in drop_rows(L(2026, 10, 23, 15, 0), None, False))


def test_check_host_with_fake_registry():
    """check_host end to end with _ps / _ps_json faked: a pause expiring before the drop reports WARN; an unreadable key never raises."""
    real_ps, real_psj = PF._ps, PF._ps_json

    def make(rows):
        def fake_psj(script, timeout=30):
            return rows if "UX\\Settings" in script else []

        def fake_ps(script, timeout=30):
            return "false" if "RebootRequired" in script else ""
        return fake_ps, fake_psj
    try:
        for label, rows, want in (
            ("fake_host_pause_expires_before_drop", [reg(pause_until=L(2026, 10, 22, 0, 1))], "WARN"),
            ("fake_host_pause_covers_drop", [reg(pause_until=DROP + 7 * 86400)], "OK"),
        ):
            PF._ps, PF._ps_json = make(rows)
            R = PF.Report()
            PF.check_host(R, DROP)
            st = {x["id"]: x for x in R.rows}
            # wu_assess inside check_host uses the real clock: keep the pause relative to the drop, so only compare vs_drop where it is clock-proof
            got = st.get("host.wu_pause_vs_drop", {}).get("status")
            if label.endswith("covers_drop"):
                check(label, got in ("OK", None) and st["host.wu_active_hours"]["status"] in ("OK", "WARN"), (got, st.get("host.wu_active_hours")))
            else:
                check(label, got in ("WARN", None) and "host.wu_active_hours" in st, (got, sorted(st)))
        # key missing entirely -> rows [] -> a WARN row, no exception, every other host check still ran
        PF._ps, PF._ps_json = make([])
        R = PF.Report()
        PF.check_host(R, DROP)
        st = {x["id"]: x for x in R.rows}
        check("fake_host_missing_key_warns", st.get("host.wu_registry", {}).get("status") == "WARN" and not any(x["status"] == "UNKNOWN" and x["id"] == "host.windows_update" for x in R.rows), sorted(st))
        # without a drop, none of the new rows exist
        R = PF.Report()
        PF.check_host(R, None)
        ids = {x["id"] for x in R.rows}
        check("fake_host_no_drop_no_new_rows", not ({"host.wu_registry", "host.wu_pause_vs_drop", "host.wu_active_hours"} & ids), sorted(ids))
        # a crashing ps call is caught by the guard, never propagates
        def boom(script, timeout=30):
            raise RuntimeError("registry blew up")
        PF._ps, PF._ps_json = boom, boom
        R = PF.Report()
        PF.check_host(R, DROP)
        check("fake_host_exception_guarded", any(x["id"] == "host.windows_update" and x["status"] == "UNKNOWN" for x in R.rows))
    finally:
        PF._ps, PF._ps_json = real_ps, real_psj


# ── F7 ─────────────────────────────────────────────────────────────────────────

BAT_SETS = [("TARGET_A_FLAG", "1", 10), ("TARGET_B_FLAG", "0", 12), ("TARGET_C_FLAG", "1", 20), ("TARGET_D_FLAG", "2", 30)]
BAT_N = 40

STATE = """# CURRENT STATE
**As of: 2026-10-16 ~16:45**
Header cite: `set TARGET_A_FLAG=1`, bat:10 ok; stale bat:11 here.

## STATE CARD - read this first (2026-10-04)
| Wall | `TARGET_B_FLAG` armed, bat:12 | and a range bat:5-15 and a block bat:13-19 and past the end bat:35-90 |

## POST-RUN 2026-10-14 (recent, so live)
Cites: bat:20, bat:21, list bat:10,12,13 and TARGET_C_FLAG at bat:30

## POST-RUN 2026-09-01 (old, so history)
Old cites that are certainly stale: bat:2, bat:3, bat:99999

## Undated evergreen section
Always live: bat:30 ok, bat:31 stale, bat:41 beyond.
"""


def test_stale_bat_cites_pure():
    asof = datetime(2026, 10, 16).date()
    r = PF.stale_bat_cites(STATE, BAT_SETS, BAT_N, asof)
    stale = {(x["state_line"], x["cite"]) for x in r["stale"]}
    cites = {c for _, c in stale}
    check("f7_single_stale_found", (3, "bat:11") in stale and (3, "bat:10") not in stale, stale)
    check("f7_range_with_set_line_ok", "bat:5-15" not in cites, stale)
    check("f7_range_without_set_line_stale", (6, "bat:13-19") in stale, stale)
    check("f7_range_past_end_stale", (6, "bat:35-90") in stale and any(x["why"].startswith("beyond") for x in r["stale"] if x["cite"] == "bat:35-90"), r["stale"])
    check("f7_comma_list_per_part", (9, "bat:13") in stale and (9, "bat:10") not in stale and (9, "bat:12") not in stale, stale)
    check("f7_recent_dated_is_live", (9, "bat:21") in stale and (9, "bat:20") not in stale, stale)
    check("f7_old_dated_is_history", not any(sl == 12 for sl, _ in stale) and r["history_cites"] == 3, (r["history_cites"], stale))
    check("f7_undated_is_live", (15, "bat:31") in stale and (15, "bat:41") in stale and (15, "bat:30") not in stale, stale)
    check("f7_state_card_live_by_name", (6, "bat:35-90") in stale)
    check("f7_counts", r["live_cites"] == 13 and r["history_cites"] == 3 and len(r["stale"]) == 7, (r["live_cites"], r["history_cites"], len(r["stale"])))
    # the state card stays live even when its dated heading is far older than the as-of
    r2 = PF.stale_bat_cites(STATE, BAT_SETS, BAT_N, datetime(2026, 12, 1).date())
    s2 = {(x["state_line"], x["cite"]) for x in r2["stale"]}
    check("f7_state_card_live_when_old", (6, "bat:35-90") in s2 and (9, "bat:21") not in s2 and r2["history_cites"] == 7, (r2["history_cites"], s2))
    # no as-of date -> every section counts as live
    r3 = PF.stale_bat_cites(STATE, BAT_SETS, BAT_N, None)
    s3 = {(x["state_line"], x["cite"]) for x in r3["stale"]}
    check("f7_no_asof_all_live", r3["history_cites"] == 0 and (12, "bat:2") in s3 and (12, "bat:99999") in s3, s3)


def test_moved_cite_detected():
    # bat:12 sits on TARGET_B_FLAG; the sentence names TARGET_A_FLAG (which is at 10) -> resolves to a set line but the wrong flag
    txt = "**As of: 2026-10-16**\n\nThe flag `set TARGET_A_FLAG=1` is armed, bat:12 and fine `set TARGET_B_FLAG=0` bat:12\n"
    r = PF.stale_bat_cites(txt, BAT_SETS, BAT_N, datetime(2026, 10, 16).date())
    check("f7_moved_flagged", len(r["moved"]) == 1 and r["moved"][0]["state_line"] == 3 and "TARGET_A_FLAG" in r["moved"][0]["now_at"] and not r["stale"], r)
    # a cite that lands on the named flag is not "moved"; a name that is not a wrapper variable is ignored
    txt2 = "As of: 2026-10-16\n`set TARGET_A_FLAG=1` bat:10 and NOT_IN_BAT_AT_ALL bat:12\n"
    r = PF.stale_bat_cites(txt2, BAT_SETS, BAT_N, datetime(2026, 10, 16).date())
    check("f7_no_false_moved", r["moved"] == [] and r["stale"] == [], r)


def _tiny_root(tmp: Path, state_text: str, bat_lines):
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "src").mkdir(exist_ok=True)
    (tmp / "config" / "product_config.json").write_text("{}", encoding="utf-8")
    (tmp / "run_bot_with_nightly_restart.bat").write_bytes(("\r\n".join(bat_lines) + "\r\n").encode("ascii"))
    (tmp / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp / ".claude" / "state" / "CURRENT_STATE.md").write_text(state_text, encoding="utf-8")


def test_repo_row():
    bat = ["@echo off", "REM a comment", "set TARGET_A_FLAG=1", "set TARGET_B_FLAG=0", "REM tail"]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _tiny_root(root, "**As of: 2026-10-16**\n`TARGET_A_FLAG` is armed (bat:3). `TARGET_B_FLAG` bat:4. Stale: bat:2 and bat:99.\n", bat)
        R = PF.Report()
        PF.check_repo(R, root, False, None)
        st = {x["id"]: x for x in R.rows}
        row = st.get("repo.state_bat_cites", {})
        check("f7_row_warns_with_stale", row.get("status") == "WARN" and "2 do not resolve" in row.get("detail", "") and "bat:2" in row["detail"] and "bat:99" in row["detail"]
              and "bat:3" not in row["detail"].replace("bat:3)", ""), row)
        check("f7_row_fix_names_flag", "flag NAME" in row.get("fix", ""), row)
        _tiny_root(root, "**As of: 2026-10-16**\n`TARGET_A_FLAG` bat:3, `TARGET_B_FLAG` bat:4, block bat:3-4.\n", bat)
        R = PF.Report()
        PF.check_repo(R, root, False, None)
        row = {x["id"]: x for x in R.rows}["repo.state_bat_cites"]
        check("f7_row_ok_when_all_resolve", row["status"] == "OK" and "3 `bat:NNNN`" in row["detail"], row)
        _tiny_root(root, "**As of: 2026-10-16**\nno cites at all\n", bat)
        R = PF.Report()
        PF.check_repo(R, root, False, None)
        row = {x["id"]: x for x in R.rows}["repo.state_bat_cites"]
        check("f7_row_ok_no_cites", row["status"] == "OK" and row["detail"].startswith("0 "), row)
        # a missing state file -> UNKNOWN row from the guard, not a crash
        (root / ".claude" / "state" / "CURRENT_STATE.md").unlink()
        R = PF.Report()
        PF.check_repo(R, root, False, None)
        rows = {x["id"]: x for x in R.rows}
        check("f7_row_guarded_when_state_missing", rows["repo.state_bat_cites"]["status"] == "UNKNOWN", rows.get("repo.state_bat_cites"))


def test_real_tree_smoke():
    """The real CURRENT_STATE.md against the real wrapper of THIS checkout: the row exists and has a verdict (no pin on the count)."""
    st_p = ROOT / ".claude" / "state" / "CURRENT_STATE.md"
    bat_p = ROOT / "run_bot_with_nightly_restart.bat"
    if not (st_p.exists() and bat_p.exists()):
        print("[SKIP] real tree files missing")
        return
    R = PF.Report()
    PF.check_repo(R, ROOT, False, None)
    row = {x["id"]: x for x in R.rows}.get("repo.state_bat_cites")
    check("f7_real_tree_row", row is not None and row["status"] in ("OK", "WARN"), row)


def main() -> int:
    for t in (test_active_hours_math, test_pause_vs_drop, test_active_hours_rows, test_missing_registry_never_crashes,
              test_check_drop_overlap, test_check_host_with_fake_registry, test_stale_bat_cites_pure,
              test_moved_cite_detected, test_repo_row, test_real_tree_smoke):
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
