#!/usr/bin/env python3
"""Offline test for the EXP-LIMKEY guest experiments (2026-10-16): the probe's classify() /
verdict() / time window / port helpers, and the in-window follower's log parsing (Live),
log tailing (LogFollower), config read and pre-registered readout. Nothing launches: no
Chrome, no network, no bot.

    python tests/test_guest_follow_1016.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "experiments"))
import limkey_guest_probe as P  # noqa: E402
import limkey_guest_follow as F  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


def row(**k):
    base = dict(post_seen=True, cdp=1, n=0, hop=False, key="", body="")
    base.update(k)
    return base


# ── probe: classify / verdict ────────────────────────────────────────────────
check("cls_limited_key", P.classify(row(status=429, key=P.ERR_A2C)) == "limited")
check("cls_limited_keyless_empty", P.classify(row(status=429)) == "limited")
check("cls_429_body_passed", P.classify(row(status=429, n=60, body="x")) == "passed")
check("cls_429_hop_passed", P.classify(row(status=429, hop=True)) == "passed")
check("cls_429_unreadable_unread", P.classify(row(status=429, n=-1)) == "unread")
check("cls_nocdp_unread", P.classify(row(status=429, cdp=0)) == "unread")
check("cls_403_wall", P.classify(row(status=403)) == "wall")
check("cls_px_body_wall", P.classify(row(status=200, body="<div id=px-captcha>")) == "wall")
check("cls_5xx_unclassified", P.classify(row(status=503)) == "unclassified")
check("cls_no_post", P.classify(dict(post_seen=False, status=429)) == "no_post")
check("cls_424_passed", P.classify(row(status=424, key="INVENTORY_UNAVAILABLE")) == "passed")
check("cls_401_passed", P.classify(row(status=401)) == "passed")
ctrl = [dict(row(status=424), tcin=P.DECOY)] * 10
lim = [dict(row(status=429, key=P.ERR_A2C), tcin="1")] * 30
pas = [dict(row(status=401), tcin="2")] * 30
check("verdict_testbed", P.verdict(["1"], lim + ctrl, 30)[0] == "TESTBED")
check("verdict_demand", P.verdict(["2"], pas + ctrl, 30)[0] == "DEMAND-SWITCHED")
check("verdict_split", P.verdict(["1", "2"], lim + pas + ctrl, 30)[0] == "SPLIT")
check("verdict_control_short", P.verdict(["1"], lim + ctrl[:9], 30)[0] == "INCONCLUSIVE")
check("verdict_control_limited",
      P.verdict(["1"], lim + ctrl + [dict(row(status=429, key=P.ERR_A2C), tcin=P.DECOY)], 30)[0] == "INCONCLUSIVE")
check("verdict_wall_inconclusive", P.verdict(["1"], lim + ctrl + [dict(row(status=403), tcin="1")], 30)[0] == "INCONCLUSIVE")
check("verdict_under_n", P.verdict(["1"], lim[:29] + ctrl, 30)[0] == "INCONCLUSIVE")
check("verdict_mid_share", P.verdict(["1"], lim[:10] + [dict(row(status=401), tcin="1")] * 20 + ctrl, 30)[0] == "INCONCLUSIVE")
check("verdict_preflight_not_answered",
      P.verdict(["2"], pas + ctrl + [dict(post_seen=False, status=429, tcin="2")] * 5, 30)[2]["2"]["answered"] == 30)


def at(hh, mm):
    return time.mktime((2026, 10, 16, hh, mm, 0, 0, 0, -1))


for hh, mm, exp in [(5, 59, False), (6, 0, True), (13, 0, True), (23, 4, True), (23, 5, False), (0, 30, False)]:
    check(f"window_{hh:02d}{mm:02d}", P.in_allowed_window(at(hh, mm)) == exp)
ports = [P.probe_free_port() for _ in range(40)]
check("probe_ports_in_range", all(P.PROBE_PORT_LO <= p < P.PROBE_PORT_HI for p in ports), str(ports[:5]))
check("probe_main_empty_tcins", P.main(["--tcins", ","]) == 2)

# ── follower: Live ───────────────────────────────────────────────────────────
HOT = ["1010892076", "1012644665"]
lv = F.Live(HOT)
t0 = 1_000_000.0
lv.feed("[DISPATCH] 1010892076 → W2/business2026-10-16 02:25:34,158 INFO x: [PURCHASE_LOCK] started", t0)
check("live_dispatch_alone_not_live", lv.live(t0, 35) == [])
lv.feed("[STATE_CARRY] cleared2026-10-16 02:25:34,159 INFO s: [STOCK][FLIP] tcin=1010892076 #1 read_ms=1", t0)
check("live_flip_glued_line", lv.live(t0, 35) == ["1010892076"])
check("live_grace_expiry", lv.live(t0 + 36, 35) == [])
lv.feed("2026-10-16 02:26:02,848 INFO s: [GROUND-TRUTH] pool cache-bust ok: in_stock=['1010892076'] (25 TCINs)", t0 + 30)
check("live_ground_truth", lv.live(t0 + 60, 35) == ["1010892076"])
lv.feed("2026-10-16 02:35:47,376 INFO s: [STOCK WATCH] 1010892076: in_stock=False avail=OUT_OF_STOCK", t0 + 61)
check("live_watch_false_ends", lv.live(t0 + 61, 35) == [])
lv.feed("2026-10-16 02:25:34,196 INFO s: [STOCK STATUS] tcin=1012644665 #3 old=IN_STOCK new=OUT_OF_STOCK in_stock=0 services=0", t0 + 62)
check("live_status0_ignored_not_live", lv.live(t0 + 62, 35) == [])
lv.feed("[02:25:34] [API_CYCLE] IN STOCK: ['1012644665', '99999999']", t0 + 63)
check("live_api_cycle_hot_only", lv.live(t0 + 63, 35) == ["1012644665"])
lv.feed("2026-10-16 02:28:17,322 INFO s: [STOCK WATCH] 81926151: in_stock=True avail=IN_STOCK", t0 + 64)
check("live_never_decoy", "81926151" not in lv.live(t0 + 64, 35))

# ── follower: LogFollower ────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    a = d / "run_20260101_000000.log"
    a.write_text("old line 1\nold line 2\n", encoding="utf-8")
    lf = F.LogFollower(d)
    check("tail_starts_at_end", lf.poll(time.time()) == [])
    with a.open("a", encoding="utf-8") as f:
        f.write("new 1\npartial")
    check("tail_new_full_lines", lf.poll(time.time()) == ["new 1"])
    with a.open("a", encoding="utf-8") as f:
        f.write(" rest\n")
    check("tail_partial_joined", lf.poll(time.time()) == ["partial rest"])
    b = d / "run_20260102_000000.log"
    b.write_text("pre-existing in new run\n", encoding="utf-8")
    os.utime(b, (time.time() + 5, time.time() + 5))
    lf.next_check = 0
    check("tail_switches_to_new_run_at_end", lf.poll(time.time()) == [] and lf.path == b)
    with b.open("a", encoding="utf-8") as f:
        f.write("after switch\n")
    check("tail_reads_new_run", lf.poll(time.time()) == ["after switch"])
    lf.f.close()

# ── follower: config + readout ───────────────────────────────────────────────
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    cfgp = d / "product_config.json"
    cfgp.write_text(json.dumps({"products": [{"tcin": "1010892076", "enabled": True},
                                             {"tcin": "1012644665", "enabled": False},
                                             {"tcin": "81926151", "enabled": True},
                                             {"tcin": "1010892076", "enabled": True}]}), encoding="utf-8")
    check("config_enabled_dedup_no_decoy", F.hot_from_config(cfgp) == ["1010892076"])
    db = d / "events.sqlite"
    con = sqlite3.connect(str(db))
    con.execute("create table shots (tcin TEXT, ts_ms INTEGER, status INTEGER, err_key TEXT, gate TEXT)")
    base = 1_700_000_000_000
    wins, shots = [], []
    for i in range(6):
        s0 = base + i * 600_000
        wins.append({"tcin": "1010892076", "start_ms": s0, "end_ms": s0 + 60_000})
        for k in range(4):
            con.execute("insert into shots values (?,?,?,?,?)", ("1010892076", s0 + k * 5000, 429, "ERR_A2C_TCIN_RATE_LIMITED", "wall1_limited"))
        # never-POSTed / keyless rows (gate other / unknown) must NOT score as members passing
        con.execute("insert into shots values (?,?,?,?,?)", ("1010892076", s0 + 1000, 0, None, "other"))
        con.execute("insert into shots values (?,?,?,?,?)", ("1010892076", s0 + 2000, 429, None, "unknown"))
    # a flicker split of window 0, 20 s after it ends: merged, not a 7th window
    wins.append({"tcin": "1010892076", "start_ms": base + 80_000, "end_ms": base + 90_000})
    con.commit()
    con.close()
    for cls, exp in (("passed", "GUEST-DIFFERS"), ("limited", "SAME-FATE")):
        sh = [{"tcin": w["tcin"], "window_start_ms": w["start_ms"], "cls": cls} for w in wins for _ in range(3)]
        p = d / f"f_{cls}.json"
        p.write_text(json.dumps({"windows": wins, "shots": sh}), encoding="utf-8")
        r = F.readout(str(p), db)
        check(f"readout_{cls}", r["verdict"] == exp and r["windows"] == 6, json.dumps({k: r[k] for k in ('verdict', 'windows')}))
    sh = [{"tcin": w["tcin"], "window_start_ms": w["start_ms"], "cls": "passed"} for w in wins[:4] for _ in range(3)]
    p = d / "f_few.json"
    p.write_text(json.dumps({"windows": wins[:4], "shots": sh}), encoding="utf-8")
    check("readout_needs_5", F.readout(str(p), db)["verdict"].startswith("INCONCLUSIVE"))
    sh = [{"tcin": w["tcin"], "window_start_ms": w["start_ms"], "cls": "passed"} for w in wins[1:6] for _ in range(2)]
    p = d / "f_thin.json"
    p.write_text(json.dumps({"windows": wins[1:6], "shots": sh}), encoding="utf-8")
    check("readout_needs_3_shots", F.readout(str(p), db)["windows"] == 0)

    # no member shots in a window -> not scored; a member shot past the limiter -> M
    con = sqlite3.connect(str(db))
    con.execute("insert into shots values (?,?,?,?,?)", ("1010892076", base + 5 * 600_000 + 3000, 401, None, "wall2_denied"))
    con.commit()
    con.close()
    wx = wins[1:6] + [{"tcin": "1012644665", "start_ms": base + 9_000_000, "end_ms": base + 9_060_000}]
    sh = [{"tcin": w["tcin"], "window_start_ms": w["start_ms"], "cls": "passed"} for w in wx for _ in range(3)]
    p1, p2 = d / "pool_a.json", d / "pool_b.json"
    p1.write_text(json.dumps({"windows": wx[:3], "shots": [x for x in sh if x["window_start_ms"] in {w["start_ms"] for w in wx[:3]}]}), encoding="utf-8")
    p2.write_text(json.dumps({"windows": wx[3:], "shots": [x for x in sh if x["window_start_ms"] in {w["start_ms"] for w in wx[3:]}]}), encoding="utf-8")
    r = F.readout([str(p1), str(p2)], db)
    check("readout_pooled_two_files", r["windows"] == 5 and r["no_member_data"] == 1, json.dumps({k: r[k] for k in ("windows", "no_member_data")}))
    check("readout_gate_other_unknown_not_passed", sum(x["members_unscored"] for x in r["rows"]) == 10)
    check("readout_wall2_denied_is_M", sum(1 for x in r["rows"] if x["M"]) == 1 and r["members_only"] == 0 and r["guest_only"] == 4)


# ── readout boundaries (verifier 10-16 round 2: thresholds, gates, slack, filters) ─────────
def scenario(windows, member_rows, guest_rows):
    """windows: [(tcin, start_ms, end_ms)]; member_rows: [(tcin, ts_ms, gate)];
    guest_rows: {(tcin, start_ms): [cls, ...]} -> readout dict."""
    with tempfile.TemporaryDirectory() as dd:
        dd = Path(dd)
        dbp = dd / "e.sqlite"
        c = sqlite3.connect(str(dbp))
        c.execute("create table shots (tcin TEXT, ts_ms INTEGER, status INTEGER, err_key TEXT, gate TEXT)")
        for tc, ts, g in member_rows:
            c.execute("insert into shots values (?,?,?,?,?)", (tc, ts, 429 if g == "wall1_limited" else 401, None, g))
        c.commit()
        c.close()
        ws = [{"tcin": tc, "start_ms": a, "end_ms": b} for tc, a, b in windows]
        sh = [{"tcin": tc, "window_start_ms": a, "cls": cl} for (tc, a), cls in guest_rows.items() for cl in cls]
        jp = dd / "f.json"
        jp.write_text(json.dumps({"windows": ws, "shots": sh}), encoding="utf-8")
        return F.readout(str(jp), dbp)


T = "1010892076"
B = 1_800_000_000_000


def W(i):
    return (T, B + i * 600_000, B + i * 600_000 + 60_000)


def mrow(i, gate, dt=5_000, tc=T):
    return (tc, B + i * 600_000 + dt, gate)


def run_mix(g_list, m_list):
    """per window i: guest passes if g_list[i], members pass if m_list[i]"""
    wins = [W(i) for i in range(len(g_list))]
    mem = []
    for i, mp in enumerate(m_list):
        mem.append(mrow(i, "wall2_denied" if mp else "wall1_limited"))
        mem.append(mrow(i, "wall1_limited", 6_000))
    gr = {(T, W(i)[1]): (["passed"] + ["limited"] * 2) if g else ["limited"] * 3 for i, g in enumerate(g_list)}
    return scenario(wins, mem, gr)


check("rb_n5_guest_differs", run_mix([1] * 5, [0] * 5)["verdict"] == "GUEST-DIFFERS")
check("rb_n4_inconclusive", run_mix([1] * 4, [0] * 4)["verdict"].startswith("INCONCLUSIVE"))
check("rb_gm3_mg2_inconclusive", run_mix([1, 1, 1, 0, 0], [0, 0, 0, 1, 1])["verdict"] == "INCONCLUSIVE")
check("rb_gm4_mg2_differs", run_mix([1, 1, 1, 1, 0, 0], [0, 0, 0, 0, 1, 1])["verdict"] == "GUEST-DIFFERS")
check("rb_gm2_not_differs", run_mix([1, 1, 0, 0, 0], [0, 0, 0, 0, 0])["verdict"] != "GUEST-DIFFERS")
check("rb_same4_gm1_samefate", run_mix([1, 0, 0, 0, 0], [0, 0, 0, 0, 0])["verdict"] == "SAME-FATE")
check("rb_same3_inconclusive", run_mix([1, 0, 0, 0, 0], [0, 1, 0, 0, 0])["verdict"] == "INCONCLUSIVE")
check("rb_all_pass_samefate", run_mix([1] * 5, [1] * 5)["verdict"] == "SAME-FATE")
for g in ("cart", "admitted_fs", "inventory", "cart_limit", "wall2_denied"):
    r = scenario([W(0)], [mrow(0, g)], {(T, W(0)[1]): ["limited"] * 3})
    check(f"rb_gate_{g}_is_M", r["rows"] and r["rows"][0]["M"] is True)
r = scenario([W(0)], [mrow(0, "wall1_limited")], {(T, W(0)[1]): ["limited"] * 3})
check("rb_wall1_not_M", r["rows"] and r["rows"][0]["M"] is False)
for dt, exp in ((-1_500, True), (-2_500, None), (69_000, True), (71_000, None)):
    r = scenario([W(0)], [mrow(0, "wall2_denied", dt)], {(T, W(0)[1]): ["limited"] * 3})
    got = r["rows"][0]["M"] if r["rows"] else None
    check(f"rb_slack_{dt}", got is exp, str(r["rows"]))
r = scenario([W(0)], [mrow(0, "wall2_denied", tc="1012644665"), mrow(0, "wall1_limited")], {(T, W(0)[1]): ["limited"] * 3})
check("rb_tcin_filter", r["rows"] and r["rows"][0]["M"] is False)
r = scenario([W(0)], [mrow(0, "wall1_limited")], {(T, W(0)[1]): ["passed", "passed", "no_post", "unread", "wall"]})
check("rb_answered_excludes_noncounted", r["windows"] == 0)
r = scenario([W(0)], [mrow(0, "wall1_limited")], {(T, W(0)[1]): ["passed", "limited", "limited", "limited"]})
check("rb_G_any_pass", r["rows"] and r["rows"][0]["G"] is True)
a0 = B
r = scenario([(T, a0, a0 + 30_000), (T, a0 + 90_000, a0 + 120_000)], [mrow(0, "wall1_limited")],
             {(T, a0): ["limited"] * 2, (T, a0 + 90_000): ["limited"] * 2})
check("rb_merge_60s", r["windows"] == 1 and r["no_member_data"] == 0)
r = scenario([(T, a0, a0 + 30_000), (T, a0 + 91_000, a0 + 120_000)], [mrow(0, "wall1_limited")],
             {(T, a0): ["limited"] * 3, (T, a0 + 91_000): ["limited"] * 3})
check("rb_no_merge_61s", r["windows"] == 1 and r["no_member_data"] == 1, json.dumps({k: r[k] for k in ("windows", "no_member_data")}))
r = scenario([W(0)], [mrow(0, "unknown"), mrow(0, "other", 6_000)], {(T, W(0)[1]): ["passed"] * 3})
check("rb_all_unscored_is_no_member", r["windows"] == 0 and r["no_member_data"] == 1)
r = scenario([(T, B, None)], [mrow(0, "wall1_limited")], {(T, B): ["passed"] * 3})
check("rb_unclosed_not_scored", r["windows"] == 0 and r["unclosed_windows"] == 1)

# ── Live: the transitions the verifier listed ────────────────────────────────
lv2 = F.Live(["1010892076"])
t1 = 5_000_000.0
lv2.feed("x [STOCK STATUS] tcin=1010892076 #2 old=OUT new=IN in_stock=1 services=1", t1)
check("live_status1_live", lv2.live(t1, 35) == ["1010892076"])
lv2.feed("x [STOCK STATUS] tcin=1010892076 #3 old=IN new=OUT in_stock=0 services=0", t1 + 1)
check("live_status0_does_not_end", lv2.live(t1 + 1, 35) == ["1010892076"])
lv2.feed("x [STOCK WATCH] 1010892076: in_stock=False avail=OUT_OF_STOCK", t1 + 2)
check("live_watch_false_ends_2", lv2.live(t1 + 2, 35) == [])
lv2.feed("x [STOCK][FLIP] tcin=1010892076 #2 read_ms=1", t1 + 3)
check("live_relive_after_oos", lv2.live(t1 + 3, 35) == ["1010892076"])
lv3 = F.Live(["1010892076"])
lv3.feed("x [STOCK WATCH] 1010892076: in_stock=True avail=IN_STOCK", t1)
check("live_watch_true_live", lv3.live(t1, 35) == ["1010892076"])

print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
sys.exit(1 if FAILED else 0)
