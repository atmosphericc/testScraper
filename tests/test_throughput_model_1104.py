#!/usr/bin/env python3
"""Offline tests for tools/analysis/throughput_model.py (2026-11-04). stdlib only, no network, no store.

Run: python tests/test_throughput_model_1104.py
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "analysis"))

import throughput_model as T  # noqa: E402

PARAMS = T.DEFAULT_PARAMS


def _model():
    return T.Model(T.Inputs(PARAMS))


class TestInputs(unittest.TestCase):
    def test_loading_and_scenarios(self):
        i = T.Inputs(PARAMS)
        k = "wall1_pass_flipera_hot_contended_0-0.5s"
        lo, pt, hi = (i.get(k, s) for s in T.SCENARIOS)
        self.assertLessEqual(lo, pt)
        self.assertLessEqual(pt, hi)
        self.assertAlmostEqual(pt, 7 / 93, places=6)
        self.assertIn(k, i.used)

    def test_null_uses_labelled_fallback(self):
        i = T.Inputs(PARAMS)
        v = i.get("wall1_pass_flipera_hot_open_0.5-1.5s")
        # open 0.5-3 s = 0/0 + 1/8; must NOT reuse the 0-3 s pooled 10/17, which double-counts the 9/9 at 0-0.5 s
        self.assertAlmostEqual(v, 1 / 8, places=6)
        self.assertEqual(i.fallback["wall1_pass_flipera_hot_open_0.5-1.5s"], "wall1_pass_flipera_hot_open_1.5-3s")

    def test_missing_key_without_fallback_raises(self):
        with self.assertRaises(KeyError):
            T.Inputs(PARAMS).get("no_such_key")

    def test_null_without_fallback_raises(self):
        with self.assertRaises(KeyError):
            T.Inputs(PARAMS).get("qty_guard_deletions")

    def test_distributions_sum_to_one(self):
        m = _model()
        for s in T.SCENARIOS:
            self.assertAlmostEqual(sum(m.delay_mix(T.Cfg(scen=s)).values()), 1.0, places=9)
        self.assertAlmostEqual(sum(m.depth_dist().values()), 1.0, places=9)
        self.assertEqual(m.delay_mix(T.Cfg(sentinel_fixed=True))["b0"], 1.0)


class TestClosedForm(unittest.TestCase):
    def test_w1_one_account_one_bin_is_stage_product(self):
        m = _model()
        # Isolate one bin (sentinel fixed => b0) and a full bank (bank_cap False => banked credential).
        cfg = T.Cfg(accounts=1, width=1, sentinel_fixed=True, bank_cap=False)
        t = m.table(cfg)
        for reg in ("contended", "open"):
            p1, p2, p3, p4 = t["p1"][(reg, "b0")], t["p2"][("banked", "b0")], t["p3"]["b0"], t["p4"]["b0"]
            r = m.acct_eval(cfg, reg)
            self.assertAlmostEqual(r["cart"], p1 * p2 * p3, places=12)
            self.assertAlmostEqual(r["order"], p1 * p2 * p3 * p4, places=12)

    def test_ci_envelope_ordering(self):
        m = _model()
        for cfg in (T.Cfg(), T.Cfg(sentinel_fixed=True), T.Cfg(width=2, mode="winner")):
            for which in (0, 1, 2):
                for key in ("cart", "order"):
                    v = [m.night_eval(replace(cfg, scen=s))[which][key] for s in T.SCENARIOS]
                    self.assertLessEqual(v[0], v[1] + 1e-12, (cfg, which, key))
                    self.assertLessEqual(v[1], v[2] + 1e-12, (cfg, which, key))

    def test_sentinel_fix_not_worse_at_point(self):
        m = _model()
        a = m.night_eval(T.Cfg())[2]["order"]
        b = m.night_eval(T.Cfg(sentinel_fixed=True))[2]["order"]
        self.assertGreaterEqual(b, a)

    def test_accounts_additive(self):
        m = _model()
        one = m.window_eval(T.Cfg(accounts=1), "open")["cart"]
        three = m.window_eval(T.Cfg(accounts=3), "open")["cart"]
        self.assertAlmostEqual(three, 3 * one, places=12)


class TestStacking(unittest.TestCase):
    def test_guard_deletes_loses_double_201(self):
        m = _model()
        # Force certain 201s: every stage p=1 => N201 == W for a full bank.
        forced = frozenset(T.STAGES)
        g2 = T.Cfg(accounts=1, width=2, mode="guard", bank_cap=False, sentinel_fixed=True, force=forced)
        w2 = replace(g2, mode="winner")
        self.assertAlmostEqual(m.acct_eval(g2, "open")["cart"], 0.0, places=12)   # two 201s => win destroyed
        self.assertAlmostEqual(m.acct_eval(w2, "open")["cart"], 1.0, places=12)   # one kept
        g1 = replace(g2, width=1)
        self.assertAlmostEqual(m.acct_eval(g1, "open")["cart"], 1.0, places=12)

    def test_winner_never_below_guard(self):
        m = _model()
        for A in (1, 3):
            for W in (1, 2, 3):
                g = m.night_eval(T.Cfg(accounts=A, width=W, mode="guard"))[2]["cart"]
                w = m.night_eval(T.Cfg(accounts=A, width=W, mode="winner"))[2]["cart"]
                self.assertGreaterEqual(w + 1e-12, g)

    def test_winner_select_monotone_in_width(self):
        m = _model()
        for sf in (False, True):
            for fs in ("indep", "shared"):
                prev = -1.0
                for W in (1, 2, 3):
                    v = m.night_eval(T.Cfg(width=W, mode="winner", sentinel_fixed=sf, fs_mode=fs))[2]["order"]
                    self.assertGreaterEqual(v + 1e-12, prev)
                    prev = v


class TestCorrelationAndPool(unittest.TestCase):
    def test_corr_w1_never_above_independent_and_equal_at_w1(self):
        m = _model()
        for W in (1, 2, 3):
            a = m.night_eval(T.Cfg(width=W, mode="winner"))[2]["cart"]
            b = m.night_eval(T.Cfg(width=W, mode="winner", corr_w1=True))[2]["cart"]
            self.assertLessEqual(b, a + 1e-12)
            if W == 1:
                self.assertAlmostEqual(a, b, places=12)

    def test_w2_correlation_ordering(self):
        m = _model()
        v = lambda **k: m.night_eval(T.Cfg(width=2, mode="winner", **k))[2]["cart"]  # noqa: E731
        ind, fs, cr, both = v(), v(fs_mode="shared"), v(corr_w1=True), v(fs_mode="shared", corr_w1=True)
        self.assertGreater(ind, fs)
        self.assertGreater(ind, cr)
        self.assertLess(both, min(fs, cr))
        self.assertGreater(both, m.night_eval(T.Cfg())[2]["cart"])

    def test_late_open_hi_only_raises_and_only_open(self):
        m = _model()
        base, hi = T.Cfg(), T.Cfg(late_open_hi=True)
        self.assertGreater(m.window_eval(hi, "open")["cart"], m.window_eval(base, "open")["cart"])
        self.assertAlmostEqual(m.window_eval(hi, "contended")["cart"], m.window_eval(base, "contended")["cart"], places=12)

    def test_pool_and_run_constants(self):
        self.assertEqual(T.N_RUNS, 7)
        self.assertEqual(T.N_CONTENDED + T.N_OPEN, 41)


class TestBankCap(unittest.TestCase):
    def test_draw_count_capped_by_depth(self):
        m = _model()
        cfg = T.Cfg(width=3)
        self.assertEqual(m.draws_for(cfg, 0), ["page"])
        self.assertEqual(m.draws_for(cfg, 2), ["banked", "banked"])
        self.assertEqual(m.draws_for(cfg, 6), ["banked"] * 3)
        self.assertEqual(m.draws_for(replace(cfg, bank_cap=False), 0), ["banked"] * 3)

    def test_capped_never_exceeds_ideal(self):
        m = _model()
        cap = m.night_eval(T.Cfg(width=3, mode="winner"))[2]["raw"]
        ideal = m.night_eval(T.Cfg(width=3, mode="winner", bank_cap=False))[2]["raw"]
        self.assertLessEqual(cap, ideal)

    def test_empty_bank_still_fires_one_page_signed_draw(self):
        m = _model()
        r = m.acct_eval(T.Cfg(accounts=1, width=3, sentinel_fixed=True), "open")
        self.assertLessEqual(r["draws"], 3.0)
        self.assertGreater(r["draws"], 1.0)


class TestMonteCarlo(unittest.TestCase):
    def test_deterministic_same_seed(self):
        m = _model()
        a = T.simulate(m, T.Cfg(width=2, mode="winner"), "open", 5000, seed=7)
        b = T.simulate(m, T.Cfg(width=2, mode="winner"), "open", 5000, seed=7)
        self.assertEqual(a, b)
        c = T.simulate(m, T.Cfg(width=2, mode="winner"), "open", 5000, seed=8)
        self.assertNotEqual(a, c)

    def _agree(self, cfg, regime, n=200000, seed=3):
        m = _model()
        cf = m.window_eval(cfg, regime)["cart"]
        mc = T.simulate(m, cfg, regime, n, seed=seed)["cart"]
        # counts per window are in 0..3 so E[X^2] <= 3 E[X]; SE <= sqrt(3*mean/n); tolerance = 4 SE
        tol = 4 * (3 * max(cf, 1e-9) / n) ** 0.5
        self.assertAlmostEqual(cf, mc, delta=tol)

    def test_mc_agrees_with_closed_form(self):
        self._agree(T.Cfg(width=2, mode="guard"), "open")

    def test_mc_agrees_shared_fs(self):
        self._agree(T.Cfg(width=2, mode="winner", fs_mode="shared"), "open")
        self._agree(T.Cfg(width=2, mode="winner", fs_mode="shared"), "contended")

    def test_mc_agrees_corr_w1(self):
        self._agree(T.Cfg(width=2, mode="winner", corr_w1=True), "open")
        self._agree(T.Cfg(width=2, mode="winner", corr_w1=True, fs_mode="shared"), "open")

    def test_ceiling_only_lowers(self):
        m = _model()
        cfg = T.Cfg(width=3, mode="winner")
        a = T.simulate(m, cfg, "open", 20000, seed=5)["cart"]
        b = T.simulate(m, cfg, "open", 20000, seed=5, ceiling=6)["cart"]
        self.assertLessEqual(b, a + 1e-12)

    def test_report_deterministic(self):
        def run():
            buf = io.StringIO()
            with redirect_stdout(buf):
                T.main(["--mc-n", "2000", "--no-db"])
            return buf.getvalue()
        a, b = run(), run()
        self.assertEqual(a, b)
        self.assertIn("[MODEL]", a)
        self.assertIn("[MEASURED]", a)


if __name__ == "__main__":
    unittest.main(verbosity=1)
