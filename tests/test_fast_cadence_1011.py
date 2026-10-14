#!/usr/bin/env python3
"""Offline tests: FX-1011-CADENCE (2026-10-11) -- per-account fast lottery cadence.

  TARGET_FAST_CADENCE_IDENTS=<acct>[,<acct>...] | all   unset / empty / 0 = OFF (exact prior behaviour)
  TARGET_FAST_CADENCE_MIN_S / _MAX_S                     default 3.0 / 4.5 s, floor 1.0 s

For the listed accounts an edge 429 (ERR_A2C_TCIN_RATE_LIMITED) or an ATC-level DCO 429
schedules the next shot MIN-MAX s later instead of the wave-first cold re-entry 55-70 s
later. Everything else -- the race budget, [RETRY_OOS], the live-racer guard, the
Shape-scoped 401 handling (wave-first / bank gate / pulse) and FX-1006-YIELD -- is unchanged.

These tests drive the REAL race loop (_start_real_purchase -> racer threads -> the
retry-while-in-stock loop) against stub workers, with the manager module's `time.sleep`
replaced by a recorder so a 55-70 s cold re-entry costs nothing. No browser, no network,
no bot. Run: python tests/test_fast_cadence_1011.py
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time as _real_time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_identity_rest as H  # noqa: E402  (env, quiet, check, race_mgr, _Worker, _race)
import test_mass_restock_1006 as MR  # noqa: E402  (EDGE, RACE_ENV, FLAGS, MSKU, _with_checker, _st)
import src.purchasing.bulletproof_purchase_manager as bpm  # noqa: E402

check, env, quiet = H.check, H.env, H.quiet
EDGE, RACE_ENV, FLAGS, MSKU = MR.EDGE, MR.RACE_ENV, MR.FLAGS, MR.MSKU
A, B = MR.A, MR.B
A401 = {"success": False, "reason": "atc_failed_api_mode", "gate_kind": "auth401"}
NEW = ("TARGET_FAST_CADENCE_IDENTS", "TARGET_FAST_CADENCE_MIN_S", "TARGET_FAST_CADENCE_MAX_S")
for _k in NEW:
    os.environ.pop(_k, None)
OFF = {k: None for k in NEW}
WAVE = (55.0, 70.0)
FAST = (3.0, 4.5)


class _FakeTime:
    """The manager module's `time`: a recording no-op `sleep`; everything else real."""
    def __init__(self):
        self.rec = []
        self._lock = threading.Lock()

    def sleep(self, s):
        with self._lock:
            self.rec.append(float(s))

    def __getattr__(self, name):
        return getattr(_real_time, name)

    def big(self):
        return [s for s in self.rec if s >= 1.0]


class _fake_sleep:
    def __enter__(self):
        self.ft = _FakeTime()
        self.saved = bpm.time
        bpm.time = self.ft
        return self.ft

    def __exit__(self, *a):
        bpm.time = self.saved


def _fleet(script=None, alt_script=None):
    sc = list(script or [EDGE])
    return [H._Worker(1, "primary", sc), H._Worker(2, "business", sc),
            H._Worker(3, "alt-1", list(alt_script or sc))]


def _race(ws, extra, tcin=A, pending=None):
    """One real race on `tcin` with MAX=3 shots per account and a recorded sleep."""
    m = H.race_mgr(ws)
    statuses = {tcin: MR._st()}
    if pending:
        for t in pending:
            m._states[t] = {"status": "ready"}
            statuses[t] = MR._st()
        now = _real_time.time()
        m._fx1006_miss_pending = {t: {"first_miss_ts": now, "window_start": now - 1.0} for t in pending}
    MR._with_checker(m, statuses)
    with env(**{**FLAGS, **RACE_ENV, "TARGET_RETRY_WHILE_IN_STOCK_MAX": "3", **OFF, **extra}), _fake_sleep() as ft:
        ok, out = quiet(H._race, m, tcin)
    return ok, out, ft, m


def _in(band, xs):
    return sum(1 for x in xs if band[0] - 1e-9 <= x <= band[1] + 1e-9)


# --------------------------------------------------------------------------- pure helpers

def test_cfg_parsing():
    c = bpm._fast_cadence_cfg({})
    check("cfg_unset_off", c["on"] is False and c["idents"] == set(), c)
    check("cfg_defaults", (c["lo"], c["hi"]) == FAST, c)
    for raw in ("", " ", "0", ",", "; ,"):
        check(f"cfg_off_for_{raw!r}", bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": raw})["on"] is False, raw)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "Alt-1, primary;W2/business"})
    check("cfg_names_lower_split", c["on"] and c["idents"] == {"alt-1", "primary", "w2/business"}, c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "0.2",
                               "TARGET_FAST_CADENCE_MAX_S": "0.1"})
    check("cfg_floor_1s_and_hi_ge_lo", (c["lo"], c["hi"]) == (1.0, 1.0), c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "garbage",
                               "TARGET_FAST_CADENCE_MAX_S": "nan"})
    check("cfg_garbage_defaults", (c["lo"], c["hi"]) == FAST, c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "6",
                               "TARGET_FAST_CADENCE_MAX_S": "2"})
    check("cfg_hi_raised_to_lo", (c["lo"], c["hi"]) == (6.0, 6.0), c)
    c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MAX_S": "999"})
    check("cfg_cap_60s", c["hi"] == 60.0, c)


def test_applies():
    on = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    check("applies_acct_part", bpm._fast_cadence_applies(on, "W3/alt-1") is True)
    check("applies_case", bpm._fast_cadence_applies(on, "W3/ALT-1") is True)
    check("applies_other_acct_no", bpm._fast_cadence_applies(on, "W1/primary") is False)
    check("applies_none_label_no", bpm._fast_cadence_applies(on, None) is False)
    check("applies_empty_label_no", bpm._fast_cadence_applies(on, "") is False)
    lbl = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": "w2/business"})
    check("applies_full_label", bpm._fast_cadence_applies(lbl, "W2/business") is True)
    check("applies_full_label_other_slot_no", bpm._fast_cadence_applies(lbl, "W3/business") is False)
    for raw in ("all", "*", "all,alt-1"):
        c = bpm._fast_cadence_cfg({"TARGET_FAST_CADENCE_IDENTS": raw})
        check(f"applies_all_{raw!r}", all(bpm._fast_cadence_applies(c, l) for l in ("W1/primary", "W2/business", "x")), raw)
    off = bpm._fast_cadence_cfg({})
    check("applies_off_cfg_no", bpm._fast_cadence_applies(off, "W3/alt-1") is False)
    check("applies_bad_cfg_no", bpm._fast_cadence_applies(None, "W3/alt-1") is False
          and bpm._fast_cadence_applies({"on": True, "idents": None}, "W3/alt-1") is False)


# --------------------------------------------------------------------------- the real race loop

def test_off_state_wave_first_for_everyone():
    """Flag unset: every account takes the 55-70 s cold re-entry after each edge 429 (as armed
    since 09-25), nothing is printed about fast cadence, and each fires MAX=3 shots."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {})
    big = ft.big()
    check("off_race_finished", ok, out[-600:])
    check("off_each_fired_3", [w.purchase_executor.calls for w in ws] == [3, 3, 3],
          [w.purchase_executor.calls for w in ws])
    check("off_six_wave_sleeps", _in(WAVE, big) == 6 and len(big) == 6, big)
    check("off_no_fast_sleep", _in(FAST, big) == 0, big)
    check("off_no_fast_line", "[FAST_CADENCE]" not in out and "[RETRY_CADENCE]" not in out, out[-800:])
    check("off_wave_first_lines_all_three", out.count("cold re-entry in") == 6
          and all(f"ident=W{i}/{a}" in out for i, a in ((1, "primary"), (2, "business"), (3, "alt-1"))),
          out.count("cold re-entry in"))
    check("off_results_edge", len(m.recorded) == 3 and all(r.get("reason") == "rate_limited_429" for _, r in m.recorded),
          m.recorded)


def test_alt1_fast_others_wave_first():
    """IDENTS=alt-1: alt-1 re-shoots 3.0-4.5 s after each edge 429; primary and business keep
    the 55-70 s cold re-entry on the same race. The within-night A/B this flag exists for."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    big = ft.big()
    check("ab_race_finished", ok, out[-600:])
    check("ab_each_fired_3", [w.purchase_executor.calls for w in ws] == [3, 3, 3],
          [w.purchase_executor.calls for w in ws])
    check("ab_four_wave_two_fast", _in(WAVE, big) == 4 and _in(FAST, big) == 2 and len(big) == 6, big)
    check("ab_fast_line_alt1_once", out.count("[FAST_CADENCE] W3/alt-1 ") == 1 and out.count("[FAST_CADENCE]") == 1,
          [l for l in out.splitlines() if "FAST_CADENCE" in l])
    wave_lines = [l for l in out.splitlines() if "cold re-entry in" in l]
    check("ab_wave_lines_never_alt1", len(wave_lines) == 4 and not any("alt-1" in l for l in wave_lines), wave_lines)
    # the existing [RETRY_CADENCE] banner fires once per fast re-shot (alt-1's two); never for the controls
    check("ab_retry_cadence_banner_twice", out.count("[RETRY_CADENCE] edge-429 lottery cadence 3.0-4.5s") == 2
          and out.count("[RETRY_CADENCE]") == 2, [l for l in out.splitlines() if "RETRY_CADENCE" in l])
    check("ab_results_edge", len(m.recorded) == 3 and all(r.get("reason") == "rate_limited_429" for _, r in m.recorded),
          m.recorded)


def test_all_fast_custom_band():
    """IDENTS=all with a 2.0 s band: six 2.0 s waits, no cold re-entry anywhere."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "all", "TARGET_FAST_CADENCE_MIN_S": "2",
                                "TARGET_FAST_CADENCE_MAX_S": "2"})
    big = ft.big()
    check("all_race_finished", ok, out[-600:])
    check("all_six_2s_sleeps", len(big) == 6 and all(abs(s - 2.0) < 1e-6 for s in big), big)
    check("all_no_wave_lines", "cold re-entry in" not in out, out[-800:])
    check("all_three_fast_lines", out.count("[FAST_CADENCE]") == 3, out.count("[FAST_CADENCE]"))


def test_fast_ident_401_stays_wave_first():
    """A Shape-scoped 401 on a fast account still takes the wave-first cold re-entry (and the
    bank gate / pulse paths are untouched): fast cadence is for the edge / DCO lottery only."""
    ws = _fleet(alt_script=[A401])
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    big = ft.big()
    check("f401_race_finished", ok, out[-600:])
    check("f401_six_wave_sleeps", _in(WAVE, big) == 6 and _in(FAST, big) == 0, big)
    wave_alt = [l for l in out.splitlines() if "cold re-entry in" in l and "alt-1" in l]
    check("f401_alt1_wave_first_lines", len(wave_alt) == 2 and all("ATC-level auth401" in l for l in wave_alt), wave_alt)


def test_fast_dco_is_fast_too():
    """An ATC-level DCO 429 (edge admitted, cart service throttled) on a fast account re-shoots at
    the fast band as well (DCO-as-edge cadence is armed; the DCO burst is off here)."""
    dco = {"success": False, "reason": "rate_limited_429", "gate_kind": "dco",
           "gate_body": "FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION"}
    ws = _fleet(alt_script=[dco])
    ok, out, ft, m = _race(ws, {"TARGET_FAST_CADENCE_IDENTS": "alt-1"})
    big = ft.big()
    check("fdco_race_finished", ok, out[-600:])
    check("fdco_four_wave_two_fast", _in(WAVE, big) == 4 and _in(FAST, big) == 2 and len(big) == 6, big)


def test_fast_wait_still_yields():
    """FX-1006-YIELD inside the fast wait: with a skipped live TCIN pending, the fast account ends
    its window mid-wait (tagged [fast-cadence]) exactly as the wave-first accounts do."""
    ws = _fleet()
    ok, out, ft, m = _race(ws, {**MSKU, "TARGET_FAST_CADENCE_IDENTS": "alt-1", "TARGET_MULTI_SKU_MISS_REARM": "1",
                                "TARGET_WAVE_FIRST_YIELD_MISSED": "1"}, pending=[B])
    check("fy_race_finished", ok, out[-600:])
    check("fy_three_yields", out.count("[WAVE_YIELD]") == 3, [l for l in out.splitlines() if "WAVE_YIELD" in l])
    check("fy_alt1_yield_tagged_fast", any("[WAVE_YIELD] W3/alt-1" in l and "[fast-cadence]" in l for l in out.splitlines()),
          [l for l in out.splitlines() if "WAVE_YIELD" in l])
    check("fy_each_fired_once", [w.purchase_executor.calls for w in ws] == [1, 1, 1],
          [w.purchase_executor.calls for w in ws])
    check("fy_reservations_released", m._worker_reservations == {}, m._worker_reservations)


def test_source_wiring():
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    check("wiring_env_read_once", src.count("env.get('TARGET_FAST_CADENCE_IDENTS'") == 1)
    check("wiring_fast_on_used", src.count("_fast_on") == 4, src.count("_fast_on"))  # 1 definition + 3 uses
    check("wiring_kinds_override", "_wf_kinds = tuple(k for k in _wf_kinds if k == 'auth401')" in src)
    check("wiring_band_override", "_edge_lo, _edge_hi = float(_fast_cfg['lo']), float(_fast_cfg['hi'])" in src)
    check("wiring_yield_aware_sleep", "[fast-cadence]" in src
          and "elif _fast_on and _gk in ('edge', 'dco') and _wave_yield_on():" in src)
    check("wiring_single_std_sleep_site_kept", src.count("time.sleep(random.uniform(_atc_lo, _atc_hi))") == 1)
    suite = (ROOT / "tests" / "run_offline_suite.py").read_text(encoding="utf-8")
    check("wiring_listed_in_offline_suite", '"test_fast_cadence_1011"' in suite)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_text(encoding="utf-8", errors="replace")
    n_set = len(re.findall(r"(?im)^\s*set\s+TARGET_FAST_CADENCE_IDENTS=", bat))
    check("wiring_bat_sets_idents_at_most_once", n_set <= 1, n_set)


def main():
    tests = [test_cfg_parsing, test_applies, test_off_state_wave_first_for_everyone,
             test_alt1_fast_others_wave_first, test_all_fast_custom_band, test_fast_ident_401_stays_wave_first,
             test_fast_dco_is_fast_too, test_fast_wait_still_yields, test_source_wiring]
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(f"{t.__name__}_raised", False, f"{type(e).__name__}: {e}")
    print(f"\n=== {len(H.PASSED)}/{len(H.PASSED) + len(H.FAILED)} passed ===")
    for f in H.FAILED:
        print(f"  FAILED: {f}"[:600])
    return 0 if not H.FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
