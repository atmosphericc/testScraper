#!/usr/bin/env python3
"""Offline tests for the ATC-level DCO burst (2026-09-17 evening).

TARGET_ATC_DCO_BURST=1: after an ATC that came back 429 with a DCO body
("Request throttled due to high demand item", gate_kind 'dco'), the race thread
re-POSTs at TARGET_ATC_DCO_BURST_MIN/MAX_S up to TARGET_ATC_DCO_BURST_MAX times
instead of taking the wave-first cold re-entry. An edge-429 or a 401 during the
burst goes through the unchanged wave-first branch. Default 0 = prior behaviour.

Evidence (docs/HOT_SKU_FIX_2026_09_16.md section 13): across every run log the
next ATC by the same identity on the same TCIN within 5 s of a DCO 429 got past
the edge limiter 15/48 times (3 x 201); after 45-90 s (the cold re-entry) 0/7.

Runs the REAL manager race loop against stub workers (no browser, no network),
re-using the harness from tests/test_identity_rest.py. Mutation-checked: each
behavioural check fails when the burst block, the cadence override or the
counter is removed.

Run: python tests/test_dco_burst.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402  (harness: env, quiet, race_mgr, _Worker, check)
import src.purchasing.bulletproof_purchase_manager as bpm_mod  # noqa: E402

check = H.check
env = H.env
quiet = H.quiet

HOT_TCIN = H.HOT_TCIN
DCO = {"success": False, "reason": "rate_limited_429", "gate_kind": "dco",
       "error": "ATC rate-limited (429)"}
EDGE = {"success": False, "reason": "rate_limited_429", "gate_kind": "edge",
        "error": "ATC rate-limited (429)"}
A401 = {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401",
        "error": "ATC 401"}
WIN = {"success": True, "order_number": "DCO-1", "quantity": 1}

# Wave-first armed exactly like the bat, sleeps collapsed, a short retry budget so
# a cold re-entry can never fit (=> "ending the window" after ONE shot, which is
# the pre-burst behaviour on a DCO), and enough attempts for a burst.
BASE = dict(TARGET_ATC_RETRY_DELAY_MIN="0", TARGET_ATC_RETRY_DELAY_MAX="0",
            TARGET_RETRY_WARM="0", TARGET_WAVE_FIRST_ONLY="1", TARGET_WAVE_FIRST_EDGE="1",
            TARGET_SHOT_BANK_GATE="0", TARGET_401_PULSE="0", TARGET_RACE_ALL_WORKERS="1",
            TARGET_RETRY_WHILE_IN_STOCK="1", TARGET_RETRY_WHILE_IN_STOCK_MAX="20",
            TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="30", TARGET_ATC_DCO_AS_EDGE_CADENCE="0",
            TARGET_IDENTITY_REST="0", TARGET_PARK_ACCOUNT_TCINS=None)
# Mechanics tests: the stub executors have no harvest API, so the 2026-09-18 bank
# requirement is switched off here; section 3b tests it with a scripted harvest stub.
BURST_FAST = dict(TARGET_ATC_DCO_BURST="1", TARGET_ATC_DCO_BURST_MIN_S="1.0",
                  TARGET_ATC_DCO_BURST_MAX_S="1.0", TARGET_ATC_DCO_BURST_MAX="6",
                  TARGET_ATC_DCO_BURST_REQUIRE_BANK="0", TARGET_ATC_DCO_BURST_BANK_WAIT_S="0")
BURST_BANK = dict(TARGET_ATC_DCO_BURST="1", TARGET_ATC_DCO_BURST_MIN_S="1.0",
                  TARGET_ATC_DCO_BURST_MAX_S="1.0", TARGET_ATC_DCO_BURST_MAX="6",
                  TARGET_ATC_DCO_BURST_BANK_WAIT_S="1")          # REQUIRE_BANK default = 1


def _fleet(primary_script, others=(EDGE,)):
    """primary follows `primary_script`; business/alt-1 take one edge-429 and end."""
    return [H._Worker(1, "primary", primary_script), H._Worker(2, "business", list(others)),
            H._Worker(3, "alt-1", list(others))]


def _run(script, **extra):
    ws = _fleet(script)
    m = H.race_mgr(ws)
    with env(**{**BASE, **extra}):          # later keys override BASE
        ok, out = quiet(H._race, m, HOT_TCIN)
    return ok, out, ws, m


# ── 1. config parse ──────────────────────────────────────────────────────────

def test_cfg_defaults_and_clamps():
    c = bpm_mod._dco_burst_cfg({})
    BANK = {"require_bank": True, "bank_wait_s": 6.0, "bank_margin_s": 5.0}
    check("cfg_default_off", c == dict({"on": False, "max": 2, "lo": 1.0, "hi": 1.5}, **BANK), c)
    c = bpm_mod._dco_burst_cfg({"TARGET_ATC_DCO_BURST": "1", "TARGET_ATC_DCO_BURST_MAX": "99",
                                "TARGET_ATC_DCO_BURST_MIN_S": "0.1", "TARGET_ATC_DCO_BURST_MAX_S": "0.05"})
    check("cfg_clamps", c == dict({"on": True, "max": 12, "lo": 1.0, "hi": 1.0}, **BANK), c)
    c = bpm_mod._dco_burst_cfg({"TARGET_ATC_DCO_BURST": " 1 ", "TARGET_ATC_DCO_BURST_MAX": "x",
                                "TARGET_ATC_DCO_BURST_MIN_S": "nan", "TARGET_ATC_DCO_BURST_MAX_S": "inf"})
    check("cfg_garbage_falls_back", c == dict({"on": True, "max": 2, "lo": 1.0, "hi": 1.5}, **BANK), c)
    c = bpm_mod._dco_burst_cfg({"TARGET_ATC_DCO_BURST_REQUIRE_BANK": " 0 ", "TARGET_ATC_DCO_BURST_BANK_WAIT_S": "99",
                                "TARGET_ATC_DCO_BURST_BANK_MARGIN_S": "x"})
    check("cfg_bank_knobs", c["require_bank"] is False and c["bank_wait_s"] == 15.0 and c["bank_margin_s"] == 5.0, c)
    c = bpm_mod._dco_burst_cfg({"TARGET_ATC_DCO_BURST": "0"})
    check("cfg_zero_is_off", c["on"] is False, c)
    check("cfg_never_raises", bpm_mod._dco_burst_cfg({"TARGET_ATC_DCO_BURST": None})["on"] is False)


# ── 2. behaviour: flag off = prior behaviour ─────────────────────────────────

def test_flag_off_dco_takes_wave_first():
    # budget 5 s: the cold re-entry cannot fit, so wave-first ends the window
    ok, out, ws, m = _run([DCO, WIN], TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S="5")
    check("off_race_finished", ok, m.recorded)
    check("off_one_shot_only", ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("off_wave_first_ended_window",
          "[WAVE_FIRST] ATC-level dco on" in out and "ending the window" in out, out[-800:])
    check("off_no_burst_line", "[DCO_BURST]" not in out, out[-400:])
    check("off_final_failed", m._states.get(HOT_TCIN, {}).get("status") == "failed",
          m._states.get(HOT_TCIN))


# ── 3. behaviour: flag on ────────────────────────────────────────────────────

def test_burst_reshoots_after_dco_and_wins():
    ok, out, ws, m = _run([DCO, DCO, WIN], **BURST_FAST)
    check("on_race_finished", ok, m.recorded)
    check("on_three_shots", ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("on_burst_lines", out.count("[DCO_BURST] ATC-level FAST_SELLING on") == 2, out[-900:])
    check("on_burst_numbered", "re-POST 1/6 in 1.0-1.0s" in out and "re-POST 2/6 in 1.0-1.0s" in out
          and "bank=" in out, out[-900:])
    check("on_no_wave_first_on_dco", "[WAVE_FIRST] ATC-level dco on" not in out, out[-900:])
    check("on_purchased", m._states.get(HOT_TCIN, {}).get("status") == "purchased",
          m._states.get(HOT_TCIN))
    check("on_others_untouched", [w.purchase_executor.calls for w in ws[1:]] == [1, 1],
          [w.purchase_executor.calls for w in ws])


def test_burst_cap_then_wave_first():
    # budget 45 s + 15 s re-entry: 1 flip shot + 2 burst re-POSTs, the 3rd DCO is
    # capped -> ONE cold re-entry (15 s) -> a 4th DCO (capped again, no second cap
    # line) -> the window ends. Fits the harness's 20 s wait.
    ok, out, ws, m = _run([DCO, DCO, DCO, DCO, DCO, DCO],
                          **{**BURST_FAST, "TARGET_ATC_DCO_BURST_MAX": "2",
                             "TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "45",
                             "TARGET_WAVE_REENTRY_MIN_S": "15", "TARGET_WAVE_REENTRY_MAX_S": "15"})
    check("cap_race_finished", ok, m.recorded)
    check("cap_four_shots", ws[0].purchase_executor.calls == 4, ws[0].purchase_executor.calls)
    check("cap_line_once", out.count("[DCO_BURST] cap 2 spent on") == 1 and "back to wave-first" in out,
          out[-1200:])
    check("cap_two_burst_lines", out.count("[DCO_BURST] ATC-level FAST_SELLING on") == 2, out[-1200:])
    check("cap_then_wave_first", "[WAVE_FIRST] ATC-level dco on" in out and "ending the window" in out,
          out[-900:])
    check("cap_final_failed", m._states.get(HOT_TCIN, {}).get("status") == "failed",
          m._states.get(HOT_TCIN))


def test_edge_during_burst_ends_it():
    ok, out, ws, m = _run([DCO, EDGE, WIN], **BURST_FAST)
    check("edge_race_finished", ok, m.recorded)
    check("edge_two_shots", ws[0].purchase_executor.calls == 2, ws[0].purchase_executor.calls)
    check("edge_burst_then_wave_first",
          "[DCO_BURST] ATC-level FAST_SELLING on" in out and "[WAVE_FIRST] ATC-level edge on" in out,
          out[-900:])
    check("edge_final_failed", m._states.get(HOT_TCIN, {}).get("status") == "failed",
          m._states.get(HOT_TCIN))


def test_401_during_burst_ends_it():
    ok, out, ws, m = _run([DCO, A401, WIN], **BURST_FAST)
    check("a401_two_shots", ok and ws[0].purchase_executor.calls == 2, ws[0].purchase_executor.calls)
    check("a401_wave_first", "[WAVE_FIRST] ATC-level auth401 on" in out, out[-900:])


def test_edge_first_never_bursts():
    # The burst only follows a DCO: an edge-429 first shot behaves exactly as before.
    ok, out, ws, m = _run([EDGE, DCO, WIN], **{**BURST_FAST, "TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "5"})
    check("edge_first_one_shot", ok and ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("edge_first_no_burst", "[DCO_BURST]" not in out, out[-600:])


def test_burst_cadence_override_is_used():
    # With the burst sleeping 0.5 s per re-POST and the std cadence at 0, the loop
    # must visibly take the burst sleeps (2 x 0.5 s), not the 0 s std/edge range.
    import time
    ws = _fleet([DCO, DCO, WIN])
    m = H.race_mgr(ws)
    t0 = time.time()
    with env(**{**BASE, **BURST_FAST}):
        ok, out = quiet(H._race, m, HOT_TCIN)
    dt = time.time() - t0
    check("cadence_race_finished", ok, m.recorded)
    check("cadence_sleeps_taken", 1.9 <= dt < 8.0, dt)
    check("cadence_three_shots", ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)


def test_burst_counter_is_per_thread():
    # business also bursts independently of primary's counter.
    ws = [H._Worker(1, "primary", [DCO, DCO, WIN]), H._Worker(2, "business", [DCO, DCO, EDGE]),
          H._Worker(3, "alt-1", [EDGE])]
    m = H.race_mgr(ws)
    with env(**{**BASE, **BURST_FAST, "TARGET_ATC_DCO_BURST_MAX": "2"}):
        ok, out = quiet(H._race, m, HOT_TCIN)
    check("per_thread_finished", ok, m.recorded)
    check("per_thread_counts", [w.purchase_executor.calls for w in ws] == [3, 3, 1],
          [w.purchase_executor.calls for w in ws])


def test_bat_parity_dco_as_edge_cadence_does_not_slow_the_burst():
    # Review finding 2/8a: the bat sets TARGET_ATC_DCO_AS_EDGE_CADENCE=1, so a DCO
    # enters the edge-cadence block (2.0-3.0 s). The burst cadence must still win,
    # and the misleading [RETRY_CADENCE] line must not print for a burst re-POST.
    import time
    ws = _fleet([DCO, DCO, WIN])
    m = H.race_mgr(ws)
    t0 = time.time()
    with env(**{**BASE, **BURST_FAST, "TARGET_ATC_DCO_AS_EDGE_CADENCE": "1",
               "TARGET_ATC_EDGE429_RETRY_DELAY_MIN": "3.0", "TARGET_ATC_EDGE429_RETRY_DELAY_MAX": "3.0"}):
        ok, out = quiet(H._race, m, HOT_TCIN)
    dt = time.time() - t0
    check("parity_three_shots", ok and ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("parity_burst_cadence_wins", dt < 4.5, dt)          # 2 x 1.0 s, not 2 x 3.0 s
    check("parity_no_retry_cadence_line", "[RETRY_CADENCE]" not in out, out[-600:])


def test_burst_bounded_by_retry_max_and_budget():
    # Review finding 8c: the burst never outruns TARGET_RETRY_WHILE_IN_STOCK_MAX.
    ok, out, ws, m = _run([DCO] * 8, **{**BURST_FAST, "TARGET_RETRY_WHILE_IN_STOCK_MAX": "3"})
    check("retry_max_three_shots", ok and ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("retry_max_budget_line", "Retry-while-in-stock budget spent" in out, out[-600:])


def test_burst_needs_room_before_the_deadline():
    # Review finding 5: with < hi + 20 s of window left a DCO takes wave-first,
    # never a burst re-POST.
    ok, out, ws, m = _run([DCO, DCO, WIN], **{**BURST_FAST, "TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "30"})
    check("room_ok_bursts", ok and ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    ok2, out2, ws2, m2 = _run([DCO, DCO, WIN], **{**BURST_FAST, "TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "15"})
    check("no_room_no_burst", ok2 and ws2[0].purchase_executor.calls == 1, ws2[0].purchase_executor.calls)
    check("no_room_line", "no burst re-POST, wave-first decides" in out2, out2[-600:])


class _HarvestExec(H._Exec):
    """Stub executor with the harvest attributes the bank gate reads."""
    def __init__(self, script):
        super().__init__(script)
        self._harvest_cfg = {"enabled": True}
        self._harvest_replay_on = True
        self.waits = []

    def harvest_set_ready(self):
        return False

    async def harvest_wait_for_set(self, max_wait_s):
        self.waits.append(float(max_wait_s))
        return False


def test_burst_skips_the_bank_gate_wait():
    # Review finding 8b: the "no bank-gate wait" claim. With the gate ON, a burst
    # re-POST must not wait for a banked set; the later wave-first cold re-entry
    # (after the cap) still does — exactly once.
    # Others end at once (terminal reason) so the harness wait covers primary's
    # 15 s cold re-entry; budget 38 s = exactly one cold re-entry fits after the cap.
    OOS = {"success": False, "reason": "oos", "error": "out of stock"}
    ws = _fleet([DCO, DCO, DCO], others=(OOS,))
    ws[0].purchase_executor = _HarvestExec([DCO, DCO, DCO])
    m = H.race_mgr(ws)
    # (BURST_FAST has REQUIRE_BANK=0: this pins that the burst does not use the WAVE-FIRST
    # bank gate; the burst's own bank requirement is tested in section 3b.)
    with env(**{**BASE, **BURST_FAST, "TARGET_SHOT_BANK_GATE": "1", "TARGET_SHOT_BANK_WAIT_S": "0.2",
               "TARGET_BANK_GATE_ADAPTIVE": "0", "TARGET_ATC_DCO_BURST_MAX": "1",
               "TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S": "38", "TARGET_WAVE_REENTRY_MIN_S": "15",
               "TARGET_WAVE_REENTRY_MAX_S": "15"}):
        ok, out = quiet(H._race, m, HOT_TCIN)
    ex = ws[0].purchase_executor
    check("gate_race_finished", ok, m.recorded)
    # shot 1 DCO -> burst re-POST (no wait) -> shot 2 DCO (cap spent) -> wave-first cold
    # re-entry with ONE bank-gate wait -> shot 3 DCO -> window ends.
    check("gate_three_shots", ex.calls == 3, ex.calls)
    check("gate_waited_once_on_cold_reentry", ex.waits == [0.2], ex.waits)
    check("gate_bank_flag_logged", "bank=False" in out, out[-900:])


# ── 3b. the bank requirement (2026-09-18 first live night) ────────────────────
# 6 of 7 live burst re-POSTs went out page-signed ("bank STALE at shot time") and
# all 6 drew an edge 429; the one that carried a banked set passed. A burst
# re-POST now fires only with a replayable banked set.

class _BankExec(H._Exec):
    """Stub executor with the public harvest API. `ready` is a list consumed by
    harvest_set_ready() (the last value repeats); a wait flips readiness when
    `ready_after_wait` is set."""
    def __init__(self, script, ready, ready_after_wait=False):
        super().__init__(script)
        self._harvest_cfg = {"enabled": True}
        self._harvest_replay_on = True
        self.ready = list(ready)
        self.ready_after_wait = ready_after_wait
        self.ready_calls = []
        self.waits = []

    def harvest_set_ready(self, margin_s=0.0):
        self.ready_calls.append(float(margin_s))
        return self.ready.pop(0) if len(self.ready) > 1 else self.ready[0]

    async def harvest_wait_for_set(self, max_wait_s):
        self.waits.append(float(max_wait_s))
        if self.ready_after_wait:
            self.ready = [True]
        return bool(self.ready[0])


def _bank_run(script, ready, ready_after_wait=False, **extra):
    ws = _fleet(script)
    ws[0].purchase_executor = _BankExec(script, ready, ready_after_wait)
    m = H.race_mgr(ws)
    with env(**{**BASE, **BURST_BANK, **extra}):
        ok, out = quiet(H._race, m, HOT_TCIN)
    return ok, out, ws[0].purchase_executor, m


def test_bank_ready_bursts_with_margin():
    ok, out, ex, m = _bank_run([DCO, DCO, WIN], [True])
    check("bank_ready_three_shots", ok and ex.calls == 3, ex.calls)
    check("bank_ready_line", out.count("bank=True waited=0.0s") == 2, out[-900:])
    check("bank_ready_no_wait", ex.waits == [], ex.waits)
    check("bank_ready_margin_passed", ex.ready_calls and all(mg == 5.0 for mg in ex.ready_calls), ex.ready_calls)


def test_bank_arrives_during_the_wait():
    import time
    t0 = time.time()
    ok, out, ex, m = _bank_run([DCO, WIN], [False], ready_after_wait=True)
    dt = time.time() - t0
    check("bank_wait_two_shots", ok and ex.calls == 2, ex.calls)
    check("bank_wait_called_once", ex.waits == [1.0], ex.waits)
    check("bank_wait_line", "bank=True waited=" in out and "no page-signed re-POST" not in out, out[-900:])
    check("bank_wait_purchased", m._states.get(HOT_TCIN, {}).get("status") == "purchased", m._states.get(HOT_TCIN))
    check("bank_wait_total_time_sane", dt < 6.0, dt)


def test_no_banked_set_no_repost():
    # Never ready: the burst must NOT fire a page-signed re-POST; wave-first decides
    # (budget 30 s cannot fit a cold re-entry, so the window ends after ONE shot).
    ok, out, ex, m = _bank_run([DCO, WIN], [False])
    check("no_bank_one_shot", ok and ex.calls == 1, ex.calls)
    check("no_bank_line", "[DCO_BURST] no replayable banked set within 1s on" in out
          and "no page-signed re-POST, wave-first decides" in out, out[-900:])
    check("no_bank_wave_first_took_it", "[WAVE_FIRST] ATC-level dco on" in out, out[-900:])
    check("no_bank_waited_once", ex.waits == [1.0], ex.waits)
    check("no_bank_not_counted", "re-POST 1/" not in out, out[-900:])


def test_executor_without_harvest_api_never_bursts():
    # The plain stub has no harvest_set_ready: with the requirement on, no re-POST.
    ws = _fleet([DCO, WIN])
    m = H.race_mgr(ws)
    with env(**{**BASE, **BURST_BANK}):
        ok, out = quiet(H._race, m, HOT_TCIN)
    check("no_api_one_shot", ok and ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("no_api_line", "no replayable banked set" in out, out[-600:])


def test_require_bank_off_restores_0917():
    ok, out, ex, m = _bank_run([DCO, WIN], [False], TARGET_ATC_DCO_BURST_REQUIRE_BANK="0")
    check("req_off_two_shots", ok and ex.calls == 2, ex.calls)
    check("req_off_line", "bank=False waited=" in out, out[-900:])


def test_bank_helper_never_raises():
    f = bpm_mod._dco_burst_bank_ready
    class Boom:
        def harvest_set_ready(self, margin_s=0.0):
            raise RuntimeError("x")
    class Old:                                    # pre-margin signature
        def harvest_set_ready(self):
            return True
    check("helper_no_api", f(object(), None, None, 1.0, 5.0) == (False, 0.0))
    r = f(Boom(), None, None, 1.0, 5.0)
    check("helper_swallows", r[0] is False, r)
    check("helper_old_signature", f(Old(), None, None, 1.0, 5.0) == (True, 0.0))


def test_executor_margin_semantics():
    # Real executor method against a stub bank: the margin shrinks the cap.
    import src.session.purchase_executor as pe
    class _Bank:
        def __init__(self, age): self._age = age
        def newest_age(self): return self._age
        @staticmethod
        def max_replay_age(): return 100.0
    class _E:
        _harvest_cfg = {"enabled": True}
        _harvest_replay_on = True
    e = _E()
    rdy = pe.PurchaseExecutor.harvest_set_ready
    e._shape_bank = _Bank(99.0)
    check("margin_0_ready_at_99", rdy(e) is True and rdy(e, 0.0) is True)
    check("margin_5_not_ready_at_99", rdy(e, 5.0) is False)
    e._shape_bank = _Bank(94.0)
    check("margin_5_ready_at_94", rdy(e, 5.0) is True)
    e._shape_bank = _Bank(None)
    check("margin_empty_bank", rdy(e, 5.0) is False)
    check("margin_garbage", rdy(_E(), "x") is False)   # no _shape_bank attr -> False, never raises


# ── 3c. review 2026-09-20: F1 (margin-aware wait) and F2 (body gate) ─────────

DCO_EDGE_BODY = dict(DCO, gate_body='{"message":"Rate Limited","code":"ERR_A2C_TCIN_RATE_LIMITED"}')
DCO_REAL_BODY = dict(DCO, gate_body='{"message":"Rate Limited","code":"DCO_RATE_LIMITED",'
                                    '"alerts":[{"message":"Request throttled due to high demand item"}]}')


def test_f1_wait_polls_with_the_callers_margin():
    """F1: the bank wait must poll with the SAME margin the caller re-checks
    with. A 97 s-old set under a 100 s cap is 'ready' at margin 0 but NOT at
    margin 5; before the fix the wait returned True at t=0 and the caller then
    rejected the set, so the whole 95-100 s dead band skipped its wait."""
    import asyncio as _aio
    import time as _t
    import src.session.purchase_executor as pe
    from src.session.shape_harvest import ShapeBank

    def _arun(c_):
        return _aio.run(c_)

    class _E:
        _harvest_cfg = {"enabled": True}
        _harvest_replay_on = True
        # the real method, so harvest_wait_for_set polls the real readiness test
        harvest_set_ready = pe.PurchaseExecutor.harvest_set_ready
    e = _E()
    e._shape_bank = ShapeBank(size=3)
    HD = {"X-GyJwza5Z-a": "tok", "X-GyJwza5Z-b": "tok2"}
    now = _t.time()
    assert e._shape_bank.push(dict(HD), now=now - 97.0), "fixture: push must bank a set"
    rdy = pe.PurchaseExecutor.harvest_set_ready
    check("f1_ready_margin0_at_97s", rdy(e) is True and rdy(e, 0.0) is True)
    check("f1_not_ready_margin5_at_97s", rdy(e, 5.0) is False)
    wait = pe.PurchaseExecutor.harvest_wait_for_set
    t0 = _t.time()
    got = _arun(wait(e, 0.4))                      # margin 0 -> instant True (unchanged)
    check("f1_wait_margin0_instant", got is True and (_t.time() - t0) < 0.3, (got, _t.time() - t0))
    t0 = _t.time()
    got = _arun(wait(e, 0.4, 5.0))                 # margin 5 -> must actually wait, then fail
    dt = _t.time() - t0
    check("f1_wait_margin5_waits_and_fails", got is False and dt >= 0.35, (got, dt))
    # a fresh set banked mid-wait satisfies the margin
    e._shape_bank.push(dict(HD), now=_t.time())
    check("f1_wait_margin5_true_after_refill", _arun(wait(e, 0.4, 5.0)) is True)
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    check("f1_caller_passes_margin", "wait_fn(wait_s, margin_s)" in src
          and "except TypeError:" in src)


def test_f2_burst_needs_the_demand_throttle_body():
    """F2: gate_kind 'dco' is set for ANY non-empty ATC 429 body, so the burst
    additionally requires the demand-throttle body. A 429 carrying the per-TCIN
    EDGE key must take the unchanged wave-first branch."""
    ok, out, ws, m = _run([DCO_EDGE_BODY, WIN], **BURST_FAST)
    check("f2_edge_body_no_burst", ok and ws[0].purchase_executor.calls == 1, ws[0].purchase_executor.calls)
    check("f2_edge_body_no_burst_line", "[DCO_BURST]" not in out, out[-500:])
    check("f2_edge_body_wave_first", "[WAVE_FIRST] ATC-level dco on" in out, out[-500:])
    ok, out, ws, m = _run([DCO_REAL_BODY, DCO_REAL_BODY, WIN], **BURST_FAST)
    check("f2_real_body_bursts", ok and ws[0].purchase_executor.calls == 3, ws[0].purchase_executor.calls)
    check("f2_real_body_burst_lines", out.count("[DCO_BURST] ATC-level FAST_SELLING on") == 2, out[-700:])
    # the escape hatch restores the looser test
    ok, out, ws, m = _run([DCO_EDGE_BODY, WIN], **{**BURST_FAST, "TARGET_ATC_DCO_BURST_ANY_BODY": "1"})
    check("f2_any_body_flag_bursts", ok and ws[0].purchase_executor.calls == 2, ws[0].purchase_executor.calls)
    # a result with no body head at all keeps the prior behaviour
    ok, out, ws, m = _run([DCO, WIN], **BURST_FAST)
    check("f2_no_body_head_bursts", ok and ws[0].purchase_executor.calls == 2, ws[0].purchase_executor.calls)


def test_f6_burst_sleep_never_under_the_hold_interval():
    """F6: two ATCs must not land closer than the cart-hold read interval, or a
    silently landed add is never read before the re-POST."""
    import time as _t
    ws = _fleet([DCO, DCO, WIN])
    m = H.race_mgr(ws)
    t0 = _t.time()
    with env(**{**BASE, **BURST_FAST, "TARGET_CART_HOLD_CHECK_INTERVAL_S": "1.0",
                "TARGET_ATC_DCO_BURST_MIN_S": "1.0", "TARGET_ATC_DCO_BURST_MAX_S": "1.0"}):
        ok, out = quiet(H._race, m, HOT_TCIN)
    dt = _t.time() - t0
    check("f6_two_bursts_take_at_least_two_intervals", ok and dt >= 1.9, (ok, dt))
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    check("f6_floor_is_the_hold_interval",
          "_atc_lo = max(_hold_iv, _dco_burst['lo'] - _dco_burst_waited)" in src)


# ── 4. source pins (bat arming + wiring) ─────────────────────────────────────

def test_bat_arms_burst():
    lines = H.BAT_PATH.read_bytes().decode("utf-8", "replace").split("\r\n")
    check("bat_burst_on", lines.count("set TARGET_ATC_DCO_BURST=1") == 1
          and H._bat_value("TARGET_ATC_DCO_BURST") == "1")
    check("bat_burst_max", H._bat_value("TARGET_ATC_DCO_BURST_MAX") == "2")
    check("bat_hold_check_1s", H._bat_value("TARGET_CART_HOLD_CHECK_INTERVAL_S") == "1.0")
    check("bat_burst_require_bank", H._bat_value("TARGET_ATC_DCO_BURST_REQUIRE_BANK") == "1")
    check("bat_burst_bank_wait", H._bat_value("TARGET_ATC_DCO_BURST_BANK_WAIT_S") == "6")
    check("bat_burst_min_s", H._bat_value("TARGET_ATC_DCO_BURST_MIN_S") == "1.0")
    check("bat_burst_max_s", H._bat_value("TARGET_ATC_DCO_BURST_MAX_S") == "1.5")
    i = lines.index("set TARGET_ATC_DCO_BURST=1")
    rem = " ".join(lines[max(0, i - 25):i])
    check("bat_burst_rem_explains", "DCO_BURST" in rem and "Kill: TARGET_ATC_DCO_BURST=0" in rem)


def test_source_wiring():
    src = Path(bpm_mod.__file__).read_text(encoding="utf-8")
    check("src_cfg_once_per_thread", "_dco_burst = _dco_burst_cfg()" in src and "_dco_burst_n = 0" in src)
    check("src_excludes_dco_from_wave_first",
          "_wf_kinds = tuple(k for k in _wf_kinds if k != 'dco')" in src)
    check("src_cadence_override",
          "_atc_lo = max(_hold_iv, _dco_burst['lo'] - _dco_burst_waited)" in src
          and "_atc_hi = max(_atc_lo, _dco_burst['hi'] - _dco_burst_waited)" in src)
    # Review 2026-09-20: F6 (never sleep under the cart-hold read interval), F2 (the
    # burst needs the demand-throttle BODY, not just gate_kind), F1 (the bank wait
    # polls with the caller's margin).
    check("src_f6_hold_floor", "TARGET_CART_HOLD_CHECK_INTERVAL_S" in src
          and "_hold_iv = max(0.0, min(_hold_iv, _dco_burst['hi']))" in src)
    check("src_f2_body_gate", "_dco_burst['on'] and _dco_body" in src
          and "'DCO_RATE_LIMITED' in _gb" in src)
    check("src_f1_margin_wait", "wait_fn(wait_s, margin_s)" in src)
    i = src.find("if _dco_burst_take:\n                            # DCO burst cadence wins")
    j = src.find("_wf_takes = _wf_only and _gk in _wf_kinds")
    e = src.find("_atc_lo, _atc_hi = _edge_lo, _edge_hi")
    check("src_override_after_edge_block_and_takes", 0 < j < e < i, (i, j, e))
    check("src_retry_cadence_line_guarded", "and not _wf_takes and not _dco_burst_take:" in src)
    check("src_room_guard", "_room < _dco_burst['hi'] + _dco_burst['bank_wait_s'] + 20.0" in src)
    check("src_require_bank_gate", "if _dco_burst['require_bank'] and not _bank_ready:" in src)


def main():
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            H.FAILED.append(f"{t.__name__}: raised {type(e).__name__}: {e}")
            print(f"[FAIL] {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print("  FAIL:", f)
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
