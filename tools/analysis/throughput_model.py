#!/usr/bin/env python3
"""Funnel-yield model of our own hot-SKU purchase bot (WS-G2/G3, 2026-11-04).

Expected units per stock window = sum over draws fired in the first seconds of
  P(wall 1 edge limiter passes at window age t)
  x P(wall 2 Shape admits | credential kind)
  x P(cart service answers 201, not FAST_SELLING 429)
  x P(place-order answers 200)
with every input read from logs/analysis_2026_11_04/WS_G1_params.json (the ONLY
source of numbers; nothing is re-mined). Pure analysis: stdlib only, reads JSON
(and, for the back-test only, the event store read-only), changes nothing.

Usage:  python tools/analysis/throughput_model.py [--params PATH] [--out PATH]
                [--mc-n 200000] [--seed 1104] [--no-mc] [--db PATH | --no-db]

Labels: [MEASURED] = a JSON input (key shown); [MODEL] = computed here;
[ASSUMPTION] = a value the store does not measure, stated explicitly.

Structure (see logs/analysis_2026_11_04/WS_G2_model_design.md):
  window regime r in {contended, open}      -> p1 (wall 1) by age bin
  per account: first-shot delay bin b       -> mixture of the measured delays
               bank depth D at the flip     -> which draws are banked_replay
  W draws per account in the t=0 volley, j-th draw needs bank depth >= j
  N201 per account ~ sum of independent Bernoulli(q_j); >=2 => stacking rule
     guard-deletes : the account's win is lost (today)
     winner-select : one 201 is kept (the P2 build)
Closed form for expectations; seeded Monte Carlo for cross-checks and for the
6-admit-per-line ceiling toggle (which has no easy closed form).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass, field, replace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_PARAMS = os.path.join(ROOT, "logs", "analysis_2026_11_04", "WS_G1_params.json")
DEFAULT_DB = os.path.join(ROOT, "logs", "events", "events.sqlite")

# Flip-era window mix (WS_G1_params.md GAPS / section 1c; not a JSON key).
N_CONTENDED, N_OPEN = 38, 3
N_RUNS = 7  # flip-era windows span 7 runs (014647:11, 1015:9, 1009:6, 233818:5, 1029:4, 1002:3, 1023:3); all 3 open are in run_20260930_233818
SCENARIOS = ("low", "point", "high")
_SC_FIELD = {"low": "ci_lo", "point": "point", "high": "ci_hi"}
BINS = ("b0", "b1", "b2", "b3")  # <=150ms | 150ms-1.5s | 1.5-3s | >3s
BIN_LABEL = {"b0": "<=150 ms", "b1": "150 ms-1.5 s", "b2": "1.5-3 s", "b3": ">3 s"}
OFFSET_0915 = 3.1  # logits: 09-15 alt-1 34% vs home 2% in the same races (VERIFICATION V2)

P1_KEYS = {
    "contended": ["wall1_pass_flipera_hot_contended_0-0.5s", "wall1_pass_flipera_hot_contended_0.5-1.5s",
                  "wall1_pass_flipera_hot_contended_1.5-3s", "wall1_pass_flipera_hot_contended_3-10s"],
    "open": ["wall1_pass_flipera_hot_open_0-0.5s", "wall1_pass_flipera_hot_open_0.5-1.5s",
             "wall1_pass_flipera_hot_open_1.5-3s", "wall1_pass_flipera_hot_open_3-10s"],
}
# JSON nulls (n=0): explicit, labelled fallbacks.
P1_FALLBACK = {
    "wall1_pass_flipera_hot_open_0.5-1.5s": "wall1_pass_flipera_hot_open_1.5-3s",  # open 0.5-3 s = 0/0 + 1/8 = 1/8 (WS_G1 sec 1c)
    "wall1_pass_flipera_hot_open_3-10s": "wall1_pass_flipera_hot_3-10s",
}
P2_BANKED = {"early": "wall2_admit_given_pastw1_banked_replay_0-3s", "late": "wall2_admit_given_pastw1_banked_replay_>=3s"}
P2_PAGE = "wall2_admit_given_pastw1_allhistory_since0911_home_page_signed"  # ASSUMPTION: used at all ages
P2_PAGE_ALT = "wall2_admit_given_pastw1_page_signed_0-3s"  # n=1, sensitivity only
P3_KEYS = {"early": "cart_201_given_admitted_0to3s", "late": "cart_201_given_admitted_ge3s"}
P4_KEYS = {"early": "wall3_po200_hot_linked_<=5s", "late": "wall3_po200_hot_linked_5-30s"}
DELAY_KEYS = ("first_shot_delay_le150ms_all", "first_shot_delay_le1500ms_all", "first_shot_delay_le3000ms_all")
DEPTH_KEYS = {d: f"bank_depth_at_flip_eq{d}" for d in (0, 2, 3, 4, 5, 6)}  # eq1 absent => remainder
STAGES = ("wall1", "wall2", "cart", "po")
STAGE_LABEL = {"wall1": "wall 1 edge limiter at t=0", "wall2": "wall 2 Shape admit",
               "cart": "cart service FAST_SELLING (201)", "po": "place-order / reservation (200)"}


class Inputs:
    """Loads WS_G1_params.json; every read is recorded for the inputs table."""

    def __init__(self, path=DEFAULT_PARAMS):
        with open(path, encoding="utf-8") as f:
            self.d = json.load(f)
        self.path = path
        self.used = {}      # key -> record
        self.fallback = {}  # key -> fallback key used because point is null

    def rec(self, key, fallback=None):
        r = self.d.get(key)
        if r is None or not isinstance(r, dict):
            raise KeyError(f"parameter key missing from {self.path}: {key}")
        if r.get("point") is None:
            fb = fallback or P1_FALLBACK.get(key)
            if fb is None:
                raise KeyError(f"parameter {key} is null (not measured) and no fallback given")
            self.fallback[key] = fb
            self.used[key] = r
            return self.rec(fb)
        self.used[key] = r
        return r

    def get(self, key, scen="point", fallback=None):
        r = self.rec(key, fallback)
        v = r.get(_SC_FIELD[scen])
        if v is None:  # e.g. no CI on a count
            v = r["point"]
        return float(v)


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _shift(p, delta):
    if not delta:
        return p
    return 1.0 / (1.0 + math.exp(-(_logit(p) + delta)))


@dataclass(frozen=True)
class Cfg:
    accounts: int = 3
    width: int = 1
    mode: str = "guard"            # guard | winner
    sentinel_fixed: bool = False   # every first shot <=150 ms
    fs_mode: str = "indep"         # indep | shared (FAST_SELLING is a window-level state)
    offsets: tuple = ()            # per-account p1 logit offsets (R1); () = R0
    bank_cap: bool = True          # j-th draw needs bank depth >= j; False = ideal full bank
    scen: str = "point"
    force: frozenset = field(default_factory=frozenset)  # stages set to p=1 (attribution)
    ps_alt: bool = False           # page_signed p2 from the n=1 0-3 s key
    corr_w1: bool = False          # same-account draws share ONE wall-1 roll (perfect correlation)
    late_open_hi: bool = False     # late (>3 s) first shots in open windows use the open >300 s wall-1 rate

    def offs(self, i):
        return self.offsets[i] if i < len(self.offsets) else 0.0


class Model:
    def __init__(self, inputs):
        self.inp = inputs
        self._tab = {}

    # ---- inputs -> tables -------------------------------------------------
    def table(self, cfg):
        key = (cfg.scen, cfg.force, cfg.ps_alt, cfg.late_open_hi)
        if key in self._tab:
            return self._tab[key]
        g, sc = self.inp.get, cfg.scen
        one = lambda st, v: 1.0 if st in cfg.force else v  # noqa: E731
        t = {"p1": {}, "p2": {}, "p3": {}, "p4": {}}
        for r, keys in P1_KEYS.items():
            for b, k in zip(BINS, keys):
                t["p1"][(r, b)] = one("wall1", g(k, sc))
        if cfg.late_open_hi:
            t["p1"][("open", "b3")] = one("wall1", g("wall1_pass_flipera_hot_open_gt300s", sc))
        for b in BINS:
            era = "early" if b != "b3" else "late"
            t["p2"][("banked", b)] = one("wall2", g(P2_BANKED[era], sc))
            t["p2"][("page", b)] = one("wall2", g(P2_PAGE_ALT if cfg.ps_alt else P2_PAGE, sc))
            t["p3"][b] = one("cart", g(P3_KEYS[era], sc))
            t["p4"][b] = one("po", g(P4_KEYS[era], sc))
        self._tab[key] = t
        return t

    def delay_mix(self, cfg):
        if cfg.sentinel_fixed:
            return {"b0": 1.0, "b1": 0.0, "b2": 0.0, "b3": 0.0}
        f = [self.inp.get(k, cfg.scen) for k in DELAY_KEYS]
        f = [min(max(x, 0.0), 1.0) for x in f]
        f = [f[0], max(f[1], f[0]), max(f[2], f[1])]
        m = {"b0": f[0], "b1": f[1] - f[0], "b2": f[2] - f[1], "b3": 1.0 - f[2]}
        return m

    def depth_dist(self):
        d = {k: self.inp.get(v, "point") for k, v in DEPTH_KEYS.items()}
        d[1] = max(0.0, 1.0 - sum(d.values()))
        s = sum(d.values())
        return {k: v / s for k, v in sorted(d.items())}

    # ---- closed form ------------------------------------------------------
    def draws_for(self, cfg, D):
        """List of credential kinds for the draws actually fired at bank depth D."""
        if not cfg.bank_cap:
            return ["banked"] * cfg.width
        out = []
        for j in range(1, cfg.width + 1):
            if D >= j:
                out.append("banked")
            elif j == 1:
                out.append("page")
        return out

    def acct_eval(self, cfg, regime, delta=0.0, p3mode="indep"):
        """One account in one window of `regime`. Returns expectations."""
        t = self.table(cfg)
        mix = self.delay_mix(cfg)
        dd = self.depth_dist() if cfg.bank_cap else {6: 1.0}
        res = dict(draws=0.0, w1=0.0, adm=0.0, raw=0.0, cart=0.0, order=0.0)
        for b in BINS:
            pb = mix[b]
            if pb <= 0:
                continue
            p1 = _shift(t["p1"][(regime, b)], delta)
            p3 = t["p3"][b]
            if b != "b3":
                if p3mode == "one":
                    p3 = 1.0
                elif p3mode == "zero":
                    p3 = 0.0
            p4 = t["p4"][b]
            for D, pD in dd.items():
                creds = self.draws_for(cfg, D)
                p2s = [t["p2"][(c, b)] for c in creds]
                pc = p1 if cfg.corr_w1 else 1.0   # shared wall-1 roll: factor out
                qs = [(1.0 if cfg.corr_w1 else p1) * p2 * p3 for p2 in p2s]
                w = pb * pD
                p0 = 1.0
                for q in qs:
                    p0 *= (1 - q)
                p1x = sum(q * _prod(1 - qq for k, qq in enumerate(qs) if k != j) for j, q in enumerate(qs))
                kept = p1x if cfg.mode == "guard" else 1 - p0
                res["draws"] += w * len(qs)
                res["w1"] += w * len(qs) * p1
                res["adm"] += w * p1 * sum(p2s)
                res["raw"] += w * pc * sum(qs)
                res["cart"] += w * pc * kept
                res["order"] += w * pc * kept * p4
        return res

    def window_eval(self, cfg, regime):
        """All accounts, one window. Adds P(any cart) and P(any order)."""
        def agg(p3mode):
            accts = [self.acct_eval(cfg, regime, cfg.offs(i), p3mode) for i in range(cfg.accounts)]
            tot = {k: sum(a[k] for a in accts) for k in accts[0]}
            tot["p_cart"] = 1 - _prod(1 - a["cart"] for a in accts)
            tot["p_order"] = 1 - _prod(1 - a["order"] for a in accts)
            return tot
        if cfg.fs_mode == "indep":
            return agg("indep")
        ps = self.inp.get(P3_KEYS["early"], cfg.scen)
        ps = 1.0 if "cart" in cfg.force else ps
        hi, lo = agg("one"), agg("zero")
        out = {k: ps * hi[k] + (1 - ps) * lo[k] for k in hi}
        return out

    def night_eval(self, cfg, n_c=N_CONTENDED, n_o=N_OPEN):
        c, o = self.window_eval(cfg, "contended"), self.window_eval(cfg, "open")
        n = {k: n_c * c[k] + n_o * o[k] for k in ("draws", "w1", "adm", "raw", "cart", "order")}
        for k in ("p_cart", "p_order"):
            n[k] = 1 - (1 - c[k]) ** n_c * (1 - o[k]) ** n_o
        return c, o, n


def _prod(it):
    p = 1.0
    for x in it:
        p *= x
    return p


# ---- Monte Carlo (seeded; cross-check + 6-admit ceiling) ------------------
def simulate(model, cfg, regime, n=200_000, seed=1104, ceiling=None):
    rng = random.Random(seed)
    t = model.table(cfg)
    mix = model.delay_mix(cfg)
    bins = [b for b in BINS if mix[b] > 0]
    bw = [mix[b] for b in bins]
    dd = model.depth_dist() if cfg.bank_cap else {6: 1.0}
    Ds, dw = list(dd.keys()), list(dd.values())
    ps = model.inp.get(P3_KEYS["early"], cfg.scen)
    ps = 1.0 if "cart" in cfg.force else ps
    s_raw = s_cart = s_ord = 0
    n_anycart = n_anyorder = 0
    for _ in range(n):
        fs_open = rng.random() < ps if cfg.fs_mode == "shared" else None
        draws = []  # (acct, b, admitted)
        for i in range(cfg.accounts):
            b = rng.choices(bins, bw)[0]
            D = rng.choices(Ds, dw)[0]
            p1 = _shift(t["p1"][(regime, b)], cfg.offs(i))
            roll = rng.random() < p1
            for c in model.draws_for(cfg, D):
                w1 = roll if cfg.corr_w1 else rng.random() < p1
                adm = w1 and rng.random() < t["p2"][(c, b)]
                draws.append([i, b, adm])
        if ceiling is not None:
            adm_idx = [k for k, d in enumerate(draws) if d[2]]
            if len(adm_idx) > ceiling:
                keep = set(rng.sample(adm_idx, ceiling))
                for k in adm_idx:
                    if k not in keep:
                        draws[k][2] = False
        n201 = [0] * cfg.accounts
        pb4 = [None] * cfg.accounts
        for i, b, adm in draws:
            if adm:
                if cfg.fs_mode == "shared" and b != "b3":
                    ok = fs_open
                else:
                    ok = rng.random() < t["p3"][b]
                if ok:
                    n201[i] += 1
                    pb4[i] = b
        c_any = o_any = False
        for i in range(cfg.accounts):
            k = n201[i]
            s_raw += k
            kept = (k == 1) if cfg.mode == "guard" else (k >= 1)
            if kept:
                s_cart += 1
                c_any = True
                if rng.random() < t["p4"][pb4[i]]:
                    s_ord += 1
                    o_any = True
        n_anycart += c_any
        n_anyorder += o_any
    return dict(raw=s_raw / n, cart=s_cart / n, order=s_ord / n, p_cart=n_anycart / n, p_order=n_anyorder / n, n=n)


# ---- back-test observed counts -------------------------------------------
HOT_SQL = ("(tcin LIKE '10108920%' OR tcin LIKE '101242210%' OR tcin LIKE '101140749%' OR tcin IN ('1012055696','1011209279',"
           "'1011960739','95274164','95274160','1012422107','1011407490','1012644665','1012644666','1012644667','95290385'))")
# Embedded result of the read-only query below (run 2026-11-04, WS-G3) -- used if the store is unavailable.
OBSERVED_DEFAULT = dict(
    source="embedded (events.sqlite read-only query, WS-G3 2026-11-04)",
    windows_contended=38, windows_open=3,
    carts_le3s={"contended": 1, "open": 2}, carts_all_ages={"contended": 7, "open": 5},
    po200=1, po200_open=1, carts_all_total=12,
    open_windows=[("run_20260930_233818", 1, 0, 2), ("run_20260930_233818", 3, 2, 3), ("run_20260930_233818", 6, 0, 0)],
)


def observed_from_db(path):
    import sqlite3
    c = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
    cls = "('wall1_limited','wall1_keyless','wall2_denied','admitted_fs','cart','inventory','cart_limit')"
    psd = "('wall2_denied','admitted_fs','cart','inventory','cart_limit')"
    rows = c.execute(f"""select run_id,window_id,sum(gate in {psd}),sum(gate in {cls}),
        sum(gate='cart' and window_age_ms<3000),sum(gate='cart')
        from shots where run_id>='run_20260930' and window_age_ms is not null and {HOT_SQL} group by 1,2""").fetchall()
    obs = dict(source="events.sqlite read-only (live query)", windows_contended=0, windows_open=0,
               carts_le3s={"contended": 0, "open": 0}, carts_all_ages={"contended": 0, "open": 0}, open_windows=[])
    for run, win, p, n, c3, ca in rows:
        if not n:
            continue  # no classified shot => not a scored window
        reg = "open" if p / n >= 0.25 else "contended"
        obs["windows_" + reg] += 1
        obs["carts_le3s"][reg] += c3 or 0
        obs["carts_all_ages"][reg] += ca or 0
        if reg == "open":
            obs["open_windows"].append((run, win, c3 or 0, ca or 0))
    obs["po200"] = c.execute(f"select count(*) from place_orders where run_id>='run_20260930' and status=200 and cart_line is not null and {HOT_SQL}").fetchone()[0]
    obs["po200_open"] = obs["po200"]
    obs["carts_all_total"] = c.execute("select count(*) from shots where run_id>='run_20260930' and gate='cart'").fetchone()[0]
    return obs


# ---- report ---------------------------------------------------------------
def o_share(M, cfg):
    c, o, n = M.night_eval(cfg)
    return N_OPEN * o["cart"] / n["cart"]


def tri(vals, nd=3):
    lo, pt, hi = vals
    return f"{pt:.{nd}f} [{lo:.{nd}f}-{hi:.{nd}f}]"


def run_scen(model, cfg, fn):
    """Evaluate fn(model, cfg_with_scenario) for low/point/high."""
    return [fn(model, replace(cfg, scen=s)) for s in SCENARIOS]


def _env(model, cfg, which, key):
    """(low,point,high) of a metric: which in contended|open|night."""
    def f(m, c):
        cc, oo, nn = m.night_eval(c)
        return {"contended": cc, "open": oo, "night": nn}[which][key]
    return run_scen(model, cfg, f)


def build_report(inp, mc_n=200_000, seed=1104, do_mc=True, db=None):
    M = Model(inp)
    L = []
    w = L.append
    cur = Cfg()
    sen = replace(cur, sentinel_fixed=True)

    w("# Throughput (funnel-yield) model of our own purchase bot - generated report")
    w("")
    w("Labels: **[MEASURED]** = input read from WS_G1_params.json (key in the inputs table); **[MODEL]** = computed here; "
      "**[ASSUMPTION]** = not measured, stated. Cells are `point [CI-low scenario - CI-high scenario]`: the low/high "
      "scenarios set EVERY stage input to its Wilson 95% bound at once, so the envelope is deliberately wide (it is an "
      "all-bounds-together envelope, not a 95% interval on the output).")
    w("")
    w(f"Window mix [MEASURED, WS_G1 GAPS]: flip-era windows {N_CONTENDED} contended / {N_OPEN} open (all 3 open on run_20260930_233818). "
      f"The 'pool' [MODEL] = {N_CONTENDED} contended + {N_OPEN} open windows = 41 windows. Only the first-volley "
      "(first shot of each account) is modelled; later re-entries (55-70 s on) are NOT in these numbers.")
    w("")

    # (i) current
    w("## 1. Current configuration: 3 accounts, W=1, measured first-shot delays, guard-deletes, global p (R0)")
    w("")
    w("| quantity [MODEL] | contended window | open window | 41-window pool (7 runs) |")
    w("|---|---|---|---|")
    for lab, key, nd in (("expected 201s fired (raw)", "raw", 3), ("expected carts retained", "cart", 3),
                         ("expected orders", "order", 4), ("P(>=1 cart)", "p_cart", 3), ("P(>=1 order)", "p_order", 3)):
        w(f"| {lab} | {tri(_env(M, cur, 'contended', key), nd)} | {tri(_env(M, cur, 'open', key), nd)} | {tri(_env(M, cur, 'night', key), nd)} |")
    w("")

    c1, o1, n1 = M.night_eval(cur)
    pp = [x for x in _env(M, cur, "night", "p_order")]
    run_p = [1 - (1 - x) ** (1.0 / N_RUNS) for x in pp]
    w("Pool vs run [MODEL]: the 41 windows are a POOL across 7 runs, not one night; all 3 open windows are in one run "
      "(run_20260930_233818). Per average run = pool / 7:")
    w("")
    w("| quantity [MODEL] | per 41-window pool | per average run (pool / 7) | per contended window | per open window |")
    w("|---|---|---|---|---|")
    for lab, key, nd in (("expected carts", "cart", 3), ("expected orders", "order", 4)):
        pl = _env(M, cur, "night", key)
        w(f"| {lab} | {tri(pl, nd)} | {tri([x / N_RUNS for x in pl], nd)} | {c1[key]:.4f} | {o1[key]:.4f} |")
    w(f"| P(>=1 order) | {tri(pp, 3)} | {tri(run_p, 3)} (= 1-(1-P_pool)^(1/7)) | {c1['p_order']:.4f} | {o1['p_order']:.4f} |")
    w("")
    w(f"About {o_share(M, cur) * 100:.0f}% of the pool's expected carts come from the {N_OPEN} open windows, which all sit in ONE run; "
      f"P(>=1 order) = {pp[1]*100:.0f}% is per 41-window pool, about {run_p[1]*100:.0f}% per average run. [MODEL]")
    w("")

    # waterfall
    w("### Funnel waterfall, current configuration (expected counts, point scenario) [MODEL]")
    w("")
    w("| stage | contended window | open window | pool |")
    w("|---|---|---|---|")
    c, o, n = M.night_eval(cur)
    for lab, k in (("draws fired (first volley)", "draws"), ("pass wall 1", "w1"), ("admitted by Shape (wall 2)", "adm"),
                   ("cart 201 (raw)", "raw"), ("cart kept after stacking rule", "cart"), ("order (place-order 200)", "order")):
        w(f"| {lab} | {c[k]:.4f} | {o[k]:.4f} | {n[k]:.3f} |")
    w("")

    # (ii) sentinel fixed
    w("## 2. Sentinel-defer fixed: every first shot <=150 ms (the TARGET_SENTINEL_DEFER PASS condition)")
    w("")
    w("| quantity [MODEL] | contended window | open window | 41-window pool |")
    w("|---|---|---|---|")
    for lab, key, nd in (("expected carts retained", "cart", 3), ("expected orders", "order", 4), ("P(>=1 order)", "p_order", 3)):
        w(f"| {lab} | {tri(_env(M, sen, 'contended', key), nd)} | {tri(_env(M, sen, 'open', key), nd)} | {tri(_env(M, sen, 'night', key), nd)} |")
    pc, ps_ = _env(M, cur, "night", "cart"), _env(M, sen, "night", "cart")
    po_, pso = _env(M, cur, "night", "order"), _env(M, sen, "night", "order")
    w("")
    w(f"Worth of the fix [MODEL]: carts/pool {pc[1]:.3f} -> {ps_[1]:.3f} ({(ps_[1]/pc[1]-1)*100:+.1f}% point; "
      f"low-scenario {(ps_[0]/pc[0]-1)*100:+.1f}%, high {(ps_[2]/pc[2]-1)*100:+.1f}%); orders/pool {po_[1]:.4f} -> {pso[1]:.4f}. "
      "Cause of the small size: the measured first-shot delays are already <=150 ms in 80.5% of account-window cells, and the "
      "late 19.5% of cells carry the thin 0.5-3 s bins plus a >3 s tail whose wall-1 pass is 0/61.")
    w("")

    # (iii) W grid
    w("## 3. Width grid: accounts x W under both stacking modes (point; [low-high] envelope) [MODEL]")
    w("")
    w("CAVEAT [NOT ESTABLISHED]: the guard-deletes loss is a CODE READING (BUILD_SCOPING Candidate 1), not a measurement: 0 double-201s "
      "were observed in 12 carts, and if the second add returned 400 MAX_PURCHASE instead of 201 the stacking columns would change. "
      "Also: W=2 has never been fired simultaneously at t=0 in our data (the only repeat shots <3 s are 12 re-fires 1.7-2.3 s after an "
      "FS answer), so every W>1 row is an extrapolation under the independence assumption of section 7.")
    for lab, base in (("measured first-shot delays", cur), ("sentinel-defer fixed", sen)):
        w("")
        w(f"### {lab}")
        w("")
        w("| accounts | W | draws/acct (bank-capped) | carts/pool guard-deletes | carts/pool winner-select | orders/pool guard-deletes | orders/pool winner-select | P(>=1 order/pool) winner |")
        w("|---|---|---|---|---|---|---|---|")
        for A in (1, 2, 3):
            for W in (1, 2, 3):
                g = replace(base, accounts=A, width=W, mode="guard")
                s = replace(base, accounts=A, width=W, mode="winner")
                dr = M.night_eval(s)[2]["draws"] / (A * (N_CONTENDED + N_OPEN))
                w(f"| {A} | {W} | {dr:.2f} | {tri(_env(M, g, 'night', 'cart'), 3)} | {tri(_env(M, s, 'night', 'cart'), 3)} | "
                  f"{tri(_env(M, g, 'night', 'order'), 4)} | {tri(_env(M, s, 'night', 'order'), 4)} | {tri(_env(M, s, 'night', 'p_order'), 3)} |")
    w("")

    # MC with ceiling
    mc_lines = []
    if do_mc:
        w("### Monte Carlo cross-check and the ~6-admit-per-line ceiling (A=3, point inputs, measured delays) [MODEL]")
        w("")
        w(f"Seeded Monte Carlo, n={mc_n:,} windows per cell, seed {seed}. Ceiling = at most 6 Shape-admitted draws per window "
          "(all three accounts share the one home line); scope of that ceiling is [NOT ESTABLISHED] so it is a toggle.")
        w("")
        w("| W | mode | window | carts closed-form | carts MC | carts MC + 6-admit ceiling | orders MC | orders MC + ceiling |")
        w("|---|---|---|---|---|---|---|---|")
        for W, mode in ((1, "guard"), (2, "winner"), (3, "winner")):
            cfgw = replace(cur, width=W, mode=mode)
            for reg in ("contended", "open"):
                cf = M.window_eval(cfgw, reg)
                a = simulate(M, cfgw, reg, mc_n, seed)
                b = simulate(M, cfgw, reg, mc_n, seed, ceiling=6)
                w(f"| {W} | {mode} | {reg} | {cf['cart']:.4f} | {a['cart']:.4f} | {b['cart']:.4f} | {a['order']:.4f} | {b['order']:.4f} |")
        w("")
        w("With 3 accounts and W<=3 the ceiling can only bind when more than 6 of up to 9 draws are admitted, i.e. in open windows "
          "at W=3; it never binds in contended windows. [MODEL]")
        w("")

    # (iv) marginal
    w("## 4. Marginal units per added draw, and why it flattens (open window carts, point inputs, measured delays) [MODEL]")
    w("")
    w("Draws are added account-first (1,1)->(2,1)->(3,1) then width (3,2)->(3,3). Columns peel off the loss mechanisms in order: "
      "ideal (full bank, no stacking loss: expected raw 201s) -> bank-capped raw 201s (j-th draw needs j banked sets at the flip; "
      "page_signed fallback only for the first) -> winner-select carts -> guard-deletes carts.")
    w("")
    for reg in ("open", "contended"):
        w(f"### {reg} window")
        w("")
        w("| config (A,W) | draws | ideal raw 201s | bank-capped raw 201s | carts winner-select | carts guard-deletes | marginal carts per added draw (winner) | marginal (guard) |")
        w("|---|---|---|---|---|---|---|---|")
        prev = None
        for A, W in ((1, 1), (2, 1), (3, 1), (3, 2), (3, 3)):
            ideal = M.window_eval(replace(cur, accounts=A, width=W, bank_cap=False, mode="winner"), reg)
            capped = M.window_eval(replace(cur, accounts=A, width=W, mode="winner"), reg)
            gd = M.window_eval(replace(cur, accounts=A, width=W, mode="guard"), reg)
            nd = A * W
            if prev is None:
                mw = mg = "-"
            else:
                dn = nd - prev[0]
                mw = f"{(capped['cart'] - prev[1]) / dn:.4f}"
                mg = f"{(gd['cart'] - prev[2]) / dn:.4f}"
            w(f"| ({A},{W}) | {nd} | {ideal['raw']:.4f} | {capped['raw']:.4f} | {capped['cart']:.4f} | {gd['cart']:.4f} | {mw} | {mg} |")
            prev = (nd, capped["cart"], gd["cart"])
        w("")
    dd = M.depth_dist()
    w(f"Bank depth at the flip [MEASURED bank_depth_at_flip_eq*]: P(D>=1)={sum(v for k, v in dd.items() if k >= 1):.3f}, "
      f"P(D>=2)={sum(v for k, v in dd.items() if k >= 2):.3f}, P(D>=3)={sum(v for k, v in dd.items() if k >= 3):.3f}; "
      "so bank depth caps the 2nd and 3rd draw only ~17% of the time (the 0-set cells: 27/162, 24 of them on one run). "
      "The binding losses are the per-draw stage product and, in OPEN windows, stacking under guard-deletes; the >=3 s cliff is "
      "visible by comparing sections 1 and 2.")
    w("")

    # (v) attribution
    w("## 5. Stage attribution: which single stage removes the most expected units today [MODEL]")
    w("")
    w("Each stage forced to p=1 for every draw in the current configuration; gain is the increase in expected value. "
      "Stage 4 does not change carts. Ranked by orders/pool point gain.")
    w("")
    base_c = _env(M, cur, "night", "cart")
    base_o = _env(M, cur, "night", "order")
    base_oo = _env(M, cur, "open", "order")
    base_oc = _env(M, cur, "contended", "order")
    rows = []
    for st in STAGES:
        cf = replace(cur, force=frozenset([st]))
        cn, on = _env(M, cf, "night", "cart"), _env(M, cf, "night", "order")
        oo, oc = _env(M, cf, "open", "order"), _env(M, cf, "contended", "order")
        rows.append((on[1] - base_o[1], st, cn, on, oo, oc))
    rows.sort(reverse=True)
    w("| rank | stage removed | carts/pool | orders/pool | orders per open window | orders per contended window | orders/pool multiplier |")
    w("|---|---|---|---|---|---|---|")
    for i, (gain, st, cn, on, oo, oc) in enumerate(rows, 1):
        w(f"| {i} | {STAGE_LABEL[st]} | {tri(cn, 3)} | {tri(on, 4)} | {oo[1]:.4f} | {oc[1]:.4f} | x{on[1]/base_o[1]:.1f} |")
    w("")
    p4r, p3r = inp.rec(P4_KEYS["early"]), inp.rec(P3_KEYS["early"])
    w("Ranking caveat [NOT ESTABLISHED]: the 'place-order / reservation' multiplier is 1/p4 with p4 = 1 success in 6 "
      f"(Wilson {p4r['ci_lo']*100:.0f}-{p4r['ci_hi']*100:.0f}%, multiplier {1/p4r['ci_hi']:.1f}-{1/p4r['ci_lo']:.0f}); the cart-service multiplier is 1/p3 with "
      f"p3 = {p3r['k']}/{p3r['n']} (Wilson {p3r['ci_lo']*100:.0f}-{p3r['ci_hi']*100:.0f}%, multiplier {1/p3r['ci_hi']:.1f}-{1/p3r['ci_lo']:.0f}). The top three stages are within "
      "~38% of each other while their input CIs differ by an order of magnitude, so the RANKING IS NOT ESTABLISHED. "
      "Forcing wall 1 to 1 also lifts the >3 s tail (draws that land late), which inflates its row.")
    w("")
    w(f"Baseline: carts/pool {tri(base_c, 3)}, orders/pool {tri(base_o, 4)}, orders per open window {base_oo[1]:.4f}, per contended window {base_oc[1]:.4f}.")
    w("")

    # keying
    w("## 6. Keying regime sensitivity (R0 / R1 / R2), current configuration, point inputs [MODEL]")
    w("")
    w("R0 = global p shared (above). R1 = shared on/off switch + a per-client logit offset on wall 1 for ONE of our three accounts "
      "(sensitivity, not a fit; +3.1 logits is the 09-15 contrast 34% vs 2% [MEASURED/cited WS_D, per_client_offset_0915 = null in JSON]). "
      "Our three home accounts show NO such spread in the flip era (0-3 s pass 17.9% / 11.4% / 14.0%, CIs overlap) [MEASURED wall1_pass_flipera_hot_0to3s_ident_*].")
    w("")
    w("| regime | carts per contended window | carts per open window | orders/pool | note |")
    w("|---|---|---|---|---|")
    w(f"| R0 global p | {M.window_eval(cur,'contended')['cart']:.4f} | {M.window_eval(cur,'open')['cart']:.4f} | {M.night_eval(cur)[2]['order']:.4f} | baseline |")
    for dl in (1.5, OFFSET_0915):
        r1 = replace(cur, offsets=(dl, 0.0, 0.0))
        w(f"| R1 one account +{dl} logits | {M.window_eval(r1,'contended')['cart']:.4f} | {M.window_eval(r1,'open')['cart']:.4f} | {M.night_eval(r1)[2]['order']:.4f} | upper-bound style sensitivity |")
    base_cc = M.window_eval(cur, "contended")["cart"]
    lo_d, hi_d = 0.0, 12.0
    for _ in range(60):
        mid = (lo_d + hi_d) / 2
        if M.window_eval(replace(cur, offsets=(mid, 0, 0)), "contended")["cart"] < 2 * base_cc:
            lo_d = mid
        else:
            hi_d = mid
    w(f"| R2 per-line independent | = R0 for one line | = R0 | = R0 | home only; lines add nothing the model can use unless p differs by line |")
    w("")
    w(f"R2, one sentence plus one number [MODEL]: lines would matter beyond plain width only if line p differs; one of our three "
      f"accounts would need a wall-1 offset of about +{hi_d:.2f} logits (odds x{math.exp(hi_d):.1f}) to double the carts per contended window "
      f"(09-15 saw ~+3.1 on a BD line, which would be x{M.window_eval(replace(cur, offsets=(OFFSET_0915,0,0)),'contended')['cart']/base_cc:.1f}).")
    w("")

    # sensitivities
    w("## 7. Other sensitivities (current config unless stated; orders/pool point) [MODEL]")
    w("")
    w("| sensitivity | carts/pool | orders/pool | comment |")
    w("|---|---|---|---|")
    def srow(lab, cfg, com):
        n = M.night_eval(cfg)[2]
        w(f"| {lab} | {n['cart']:.3f} | {n['order']:.4f} | {com} |")
    srow("baseline", cur, "")
    srow("FAST_SELLING shared per window (all draws see the same cart-service state)", replace(cur, fs_mode="shared"), "13 of 16 admitted shots were FS; if FS is a window state, W gains less")
    srow("same, W=2 winner-select", replace(cur, fs_mode="shared", width=2, mode="winner"), "compare with the independent W=2 row in section 3")
    srow("page_signed wall-2 admit from the n=1 0-3 s key (100%)", replace(cur, ps_alt=True), "page_signed arm has n=1 at 0-3 s; baseline uses the 1/11 all-history rate")
    srow("late first shots in OPEN windows use open >300 s wall-1 rate (22/25)", replace(cur, late_open_hi=True), "default keeps the pooled 3-10 s rate (0/61): conservative, measured at the same age; the >300 s rate is a different age regime")
    srow("W=2 winner-select, shared FS, sentinel fixed", replace(cur, fs_mode="shared", width=2, mode="winner", sentinel_fixed=True), "")
    w("")

    w("")
    w("### W=2 winner-select under four correlation assumptions (3 accounts, measured delays) [MODEL]")
    w("")
    w("W=2 has never been fired simultaneously at t=0 in our data; case (iv) is UNTESTED, not refuted.")
    w("")
    w("| case | carts/pool | orders/pool | carts/pool vs current W=1 | open-window carts |")
    w("|---|---|---|---|---|")
    base_n = M.night_eval(cur)[2]["cart"]
    w2 = replace(cur, width=2, mode="winner")
    for lab, cf in (("(i) independent draws", w2), ("(ii) shared FAST_SELLING only", replace(w2, fs_mode="shared")),
                    ("(iii) same-account wall-1 correlated only (one roll shared by the W draws)", replace(w2, corr_w1=True)),
                    ("(iv) both (ii) and (iii)", replace(w2, fs_mode="shared", corr_w1=True))):
        cc_, oo_, nn_ = M.night_eval(cf)
        w(f"| {lab} | {nn_['cart']:.3f} | {nn_['order']:.4f} | {(nn_['cart']/base_n-1)*100:+.1f}% | {oo_['cart']:.3f} |")
    sn = M.night_eval(sen)[2]["cart"]
    w("")
    w(f"For scale, sentinel-defer fixed is {(sn/base_n-1)*100:+.1f}% carts/pool; case (iv) is the tie point with it. [MODEL]")
    w("")

    # backtest
    w("## 8. Back-test on the flip-era windows (consistency check, NOT out-of-sample) [MODEL vs MEASURED]")
    w("")
    obs = observed_from_db(db) if db and os.path.exists(db) else OBSERVED_DEFAULT
    nc, no = obs["windows_contended"], obs["windows_open"]
    pc_, po_w = M.window_eval(cur, "contended"), M.window_eval(cur, "open")
    lo_c = [M.window_eval(replace(cur, scen=s), "contended") for s in SCENARIOS]
    lo_o = [M.window_eval(replace(cur, scen=s), "open") for s in SCENARIOS]
    pred_c = [nc * x["cart"] for x in lo_c]
    pred_o = [no * x["cart"] for x in lo_o]
    pred_tot = [a + b for a, b in zip(pred_c, pred_o)]
    ord_tot = [nc * x["order"] + no * y["order"] for x, y in zip(lo_c, lo_o)]
    oc, oo = obs["carts_le3s"]["contended"], obs["carts_le3s"]["open"]
    w(f"Observed source: {obs['source']}. Windows scored: {nc} contended, {no} open. Predicted = windows x per-window expectation, "
      "current configuration (3 accounts, W=1, measured delays, guard-deletes, global p).")
    w("")
    w("| quantity | predicted point [low-high] | observed | ratio obs/pred |")
    w("|---|---|---|---|")
    w(f"| carts at window age <3 s, contended windows | {tri(pred_c, 2)} | {oc} | {oc/pred_c[1]:.2f} |")
    w(f"| carts at window age <3 s, open windows | {tri(pred_o, 2)} | {oo} | {oo/pred_o[1]:.2f} |")
    w(f"| carts at window age <3 s, all scored windows | {tri(pred_tot, 2)} | {oc+oo} | {(oc+oo)/pred_tot[1]:.2f} |")
    w(f"| orders (place-order 200), all scored windows | {tri(ord_tot, 3)} | {obs['po200']} | {obs['po200']/ord_tot[1]:.2f} |")
    w("")
    w(f"Context [MEASURED]: all-ages 201s since 09-30 = {obs['carts_all_total']} (the first-volley model is not built to explain the "
      f"{obs['carts_all_total'] - oc - oo} carts that arrived at >=3 s, mostly the >300 s bin of the one open run). Per open window "
      f"(run, window, carts<3 s, carts all ages): {obs['open_windows']}.")
    w("")
    w("Circularity [NOT ESTABLISHED as validation]: 'open' is defined by outcome (>=25% of the window's shots passed, a rate that includes "
      "the very shots being scored); the 3 open windows are ONE run; and p3 = 3/16 contains the very 3 carts the back-test 'predicts'. "
      "The back-test therefore carries almost no weight.")
    w("")
    verdict_c = "over-predicts" if pred_tot[1] > oc + oo else "under-predicts"
    verdict_o = "over-predicts" if ord_tot[1] > obs["po200"] else "under-predicts"
    w(f"Verdict [MODEL vs MEASURED, no tuning]: for <3 s carts the model {verdict_c} by {abs(pred_tot[1]-(oc+oo)):.2f} carts "
      f"(predicted {pred_tot[1]:.2f} vs observed {oc+oo}); for orders it {verdict_o} (predicted {ord_tot[1]:.3f} vs observed {obs['po200']}). "
      "The parameters were mined from the same events, so agreement is in-sample consistency; with 3 carts and 1 order the "
      "back-test carries almost no weight and has no power to discriminate models.")
    w("")

    # inputs
    w("## 9. Inputs used [MEASURED] (all read from WS_G1_params.json)")
    w("")
    w("| JSON key | point | ci_lo | ci_hi | k | n | note |")
    w("|---|---|---|---|---|---|---|")
    for k in sorted(inp.used):
        r = inp.used[k]
        f = lambda x: "null" if x is None else f"{x:.4f}"  # noqa: E731
        note = f"null in JSON; fallback -> {inp.fallback[k]}" if k in inp.fallback else ""
        w(f"| {k} | {f(r.get('point'))} | {f(r.get('ci_lo'))} | {f(r.get('ci_hi'))} | {r.get('k')} | {r.get('n')} | {note} |")
    w("")
    w("Explicit [ASSUMPTION]s: (a) page_signed wall-2 admit uses the all-history key (1/11) at all ages; (b) a first shot later than 3 s "
      "is evaluated with the 3-10 s wall-1 bin and the >=3 s stage rates; (c) draws are independent given window regime and bin; "
      "(d) p4 is the per-linked-place-order rate and is applied once per retained cart (a cart gets several place-order tries in the "
      "chain, so this understates per-cart conversion; the single order in the store came from a 1-of-3 cart set); "
      "(e) bank state is independent of regime; (f) eq1 bank depth = remainder (no key); (g) after a guard-deletes loss the account "
      "does not re-win inside the volley.")
    w("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--params", default=DEFAULT_PARAMS)
    ap.add_argument("--out")
    ap.add_argument("--mc-n", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=1104)
    ap.add_argument("--no-mc", action="store_true")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--no-db", action="store_true")
    a = ap.parse_args(argv)
    inp = Inputs(a.params)
    text = build_report(inp, a.mc_n, a.seed, not a.no_mc, None if a.no_db else a.db)
    sys.stdout.write(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
