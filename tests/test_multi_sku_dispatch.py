#!/usr/bin/env python3
"""D1 (2026-09-20): synchronous worker reservation for multi-SKU dispatch.

Why this exists. `tools/analysis/limiter_key.py` over all 104 run logs shows
Target's edge limiter is a SHARED per-TCIN volume bucket, not a per-identity
cooldown: pass rate vs prior shots on that TCIN in 120 s runs 9.6% (0 prior) ->
1.1% (3-4) -> 0.4% (9+), and OTHER identities suppress ours across DIFFERENT
IPs. The bot raced all 3 accounts at ONE TCIN 36 of 36 times on 09-17 while
`[MULTI_SKU_MISS]` skipped 13 live hot TCINs on 09-16 — the worst allocation
possible against a shared bucket.

Multi-SKU dispatch was deliberately NOT shipped before because
`_active_purchases` registration happens inside the spawned thread and lags
within a cycle, so a naive per-TCIN gate could hand ONE worker to TWO SKUs.
These tests pin the missing piece: the reservation is taken SYNCHRONOUSLY and
is exclusive.

No browser, no network. Run: python tests/test_multi_sku_dispatch.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.bulletproof_purchase_manager import (  # noqa: E402
    BulletproofPurchaseManager as M,
    _multi_sku_cfg,
)

FAILS = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILS.append(name)


class _W:
    """Minimal worker: the reservation only ever touches .label()."""
    def __init__(self, lbl):
        self._lbl = lbl

    def label(self):
        return self._lbl


def _Stub():
    """A REAL manager with only the reservation state materialised — no
    __init__, no browser, no network. Using the real class (rather than a
    look-alike) is deliberate: a hand-rolled stub would not carry
    _reserve_state and would hide the very bug this file caught."""
    m = M.__new__(M)
    m._reserve_lock = threading.Lock()
    m._worker_reservations = {}
    return m


def _env(**kw):
    saved = {k: os.environ.get(k) for k in kw}
    os.environ.update({k: str(v) for k, v in kw.items()})
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


W1, W2, W3 = _W("W1/primary"), _W("W2/alt-1"), _W("W3/business")
ALL = [W1, W2, W3]


# ── config ───────────────────────────────────────────────────────────────────

def test_cfg_default_off():
    saved = _env(TARGET_MULTI_SKU_DISPATCH="0")
    try:
        c = _multi_sku_cfg()
        check("cfg_default_off", c["on"] == 0 and c["per_tcin"] == 0, str(c))
    finally:
        _restore(saved)


def test_cfg_defaults_and_clamps():
    saved = _env(TARGET_MULTI_SKU_DISPATCH="1")
    try:
        c = _multi_sku_cfg()
        check("cfg_defaults", c == {"on": 1, "max_tcins": 3, "per_tcin": 1, "ttl_s": 120,
                                    "cap_always": False}, str(c))
    finally:
        _restore(saved)

    saved = _env(TARGET_MULTI_SKU_DISPATCH="1", TARGET_MULTI_SKU_MAX_CONCURRENT="99",
                 TARGET_MULTI_SKU_WORKERS_PER_TCIN="0", TARGET_MULTI_SKU_RESERVE_TTL_S="99999")
    try:
        c = _multi_sku_cfg()
        check("cfg_clamped", c["max_tcins"] == 8 and c["per_tcin"] == 1 and c["ttl_s"] == 900, str(c))
    finally:
        _restore(saved)

    saved = _env(TARGET_MULTI_SKU_DISPATCH="1", TARGET_MULTI_SKU_MAX_CONCURRENT="junk",
                 TARGET_MULTI_SKU_WORKERS_PER_TCIN="nan")
    try:
        c = _multi_sku_cfg()
        check("cfg_garbage_falls_back", c["max_tcins"] == 3 and c["per_tcin"] == 1, str(c))
    finally:
        _restore(saved)


# ── the safety property ──────────────────────────────────────────────────────

def test_a_worker_is_never_given_to_two_tcins():
    """THE invariant that kept this unshipped. Two TCINs, one worker each,
    disjoint — and the third TCIN gets nothing rather than a shared worker."""
    s = _Stub()
    a = M._reserve_workers_for_tcin(s, "AAA", ALL, 1)
    b = M._reserve_workers_for_tcin(s, "BBB", ALL, 1)
    c = M._reserve_workers_for_tcin(s, "CCC", ALL, 1)
    d = M._reserve_workers_for_tcin(s, "DDD", ALL, 1)
    labels = [w.label() for w in a + b + c]
    check("three_tcins_get_disjoint_workers",
          len(labels) == 3 and len(set(labels)) == 3, str(labels))
    check("fourth_tcin_gets_nothing", d == [], str([w.label() for w in d]))


def test_reserve_respects_limit_and_takes_whole_fleet_when_idle():
    s = _Stub()
    one = M._reserve_workers_for_tcin(s, "AAA", ALL, 1)
    check("limit_one", len(one) == 1, str(len(one)))

    s2 = _Stub()
    allw = M._reserve_workers_for_tcin(s2, "AAA", ALL, len(ALL))
    check("idle_fleet_takes_all", len(allw) == 3, str(len(allw)))


def test_same_tcin_reentry_is_idempotent():
    """Re-reserving for the SAME tcin must not consume extra workers."""
    s = _Stub()
    M._reserve_workers_for_tcin(s, "AAA", ALL, 1)
    again = M._reserve_workers_for_tcin(s, "AAA", ALL, 1)
    check("same_tcin_reentry", len(again) == 1 and len(s._worker_reservations) == 1,
          str(s._worker_reservations))


def test_release_frees_the_worker():
    s = _Stub()
    got = M._reserve_workers_for_tcin(s, "AAA", ALL, 1)
    M._release_worker_reservation(s, got[0].label())
    check("release_empties", s._worker_reservations == {}, str(s._worker_reservations))
    re = M._reserve_workers_for_tcin(s, "BBB", ALL, 1)
    check("released_worker_reusable", re and re[0].label() == got[0].label(), str(re))


def test_free_workers_and_reserved_tcins():
    s = _Stub()
    M._reserve_workers_for_tcin(s, "AAA", ALL, 2)
    free = M._free_workers(s, ALL)
    check("free_workers_excludes_reserved", len(free) == 1, str([w.label() for w in free]))
    check("reserved_tcins", M._reserved_tcins(s) == {"AAA"}, str(M._reserved_tcins(s)))


def test_stale_sweep_cannot_wedge_the_fleet():
    """A racer that died without releasing must not hold a worker forever."""
    s = _Stub()
    M._reserve_workers_for_tcin(s, "AAA", ALL, 3)
    for lbl, (tcin, _ts) in list(s._worker_reservations.items()):
        s._worker_reservations[lbl] = (tcin, time.time() - 500)
    M._sweep_stale_reservations(s, 120)
    check("stale_swept", s._worker_reservations == {}, str(s._worker_reservations))

    s2 = _Stub()
    M._reserve_workers_for_tcin(s2, "AAA", ALL, 3)
    M._sweep_stale_reservations(s2, 120)
    check("fresh_not_swept", len(s2._worker_reservations) == 3, str(s2._worker_reservations))


def test_concurrent_reservation_is_exclusive():
    """Hammer the lock: 12 threads, 3 workers -> exactly 3 claims, no double-grant."""
    s = _Stub()
    got = []
    lock = threading.Lock()

    def grab(i):
        r = M._reserve_workers_for_tcin(s, f"T{i}", ALL, 1)
        with lock:
            got.extend(w.label() for w in r)

    ts = [threading.Thread(target=grab, args=(i,)) for i in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("concurrent_exactly_three", len(got) == 3, str(got))
    check("concurrent_no_duplicates", len(set(got)) == len(got), str(got))


def test_uninitialised_manager_does_not_raise():
    """Regression (caught 2026-09-20 by the offline suite): the release runs
    inside a purchase thread's CLEANUP path. A manager built without __init__
    — which several offline tests do — must degrade to a no-op there, never
    raise AttributeError and mask the real purchase result."""
    bare = M.__new__(M)
    try:
        M._release_worker_reservation(bare, "W1/primary")
        M._sweep_stale_reservations(bare, 120)
        free = M._free_workers(bare, ALL)
        tc = M._reserved_tcins(bare)
        got = M._reserve_workers_for_tcin(bare, "AAA", ALL, 1)
        ok = len(free) == 3 and tc == set() and len(got) == 1
        check("uninitialised_manager_safe", ok, f"free={len(free)} tcins={tc} got={len(got)}")
    except AttributeError as e:
        check("uninitialised_manager_safe", False, f"raised {e}")


def test_cap_always_spreads_the_first_tcin():
    """2026-09-20. Without CAP_ALWAYS the FIRST TCIN to flip takes the WHOLE
    fleet, so on a drop where a dozen SKUs go live within seconds every other
    TCIN finds no unreserved worker and prints [MULTI_SKU_MISS] exactly as it
    did before D1 shipped — i.e. dispatch is a no-op in the case it exists for.
    =1 caps every TCIN at WORKERS_PER_TCIN. Measured over 104 run logs: 9-16
    shots at ONE hot TCIN per 120 s yields 0.038 admits vs 0.545 at 2 shots,
    so one worker per TCIN is the better allocation even on a lone live TCIN."""
    saved = _env(TARGET_MULTI_SKU_DISPATCH="1")
    try:
        check("cap_always_default_off", _multi_sku_cfg()["cap_always"] is False)
    finally:
        _restore(saved)

    saved = _env(TARGET_MULTI_SKU_DISPATCH="1", TARGET_MULTI_SKU_CAP_ALWAYS="1")
    try:
        check("cap_always_reads_flag", _multi_sku_cfg()["cap_always"] is True)
    finally:
        _restore(saved)

    # Strict "1" only — no truthy-string surprises on a production flag.
    for v in ("0", "true", "yes", "junk", "2", "-1"):
        saved = _env(TARGET_MULTI_SKU_DISPATCH="1", TARGET_MULTI_SKU_CAP_ALWAYS=v)
        try:
            check(f"cap_always_strict[{v}]", _multi_sku_cfg()["cap_always"] is False)
        finally:
            _restore(saved)

    # Key is present even with dispatch off, so the shape cannot silently drift.
    saved = _env(TARGET_MULTI_SKU_DISPATCH="0", TARGET_MULTI_SKU_CAP_ALWAYS="1")
    try:
        c = _multi_sku_cfg()
        check("cap_always_inert_when_off", c["cap_always"] is False and c["on"] == 0, str(c))
    finally:
        _restore(saved)

    # The limit expression is inline in start_purchase, so pin it at source: a
    # revert to the fleet-grab form is the exact regression that makes dispatch
    # a no-op on a simultaneous multi-SKU drop.
    src = (Path(__file__).resolve().parents[1] / "src" / "purchasing"
           / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    check("cap_always_wired_into_limit",
          "_limit = _ms['per_tcin'] if (_others or _ms.get('cap_always')) else len(ready_workers)"
          in src)
    check("cap_always_single_limit_site", src.count("_limit = _ms['per_tcin']") == 1,
          str(src.count("_limit = _ms['per_tcin']")))


def main():
    print("== D1 multi-SKU dispatch: synchronous worker reservation ==")
    test_cfg_default_off()
    test_cfg_defaults_and_clamps()
    test_a_worker_is_never_given_to_two_tcins()
    test_reserve_respects_limit_and_takes_whole_fleet_when_idle()
    test_same_tcin_reentry_is_idempotent()
    test_release_frees_the_worker()
    test_free_workers_and_reserved_tcins()
    test_stale_sweep_cannot_wedge_the_fleet()
    test_concurrent_reservation_is_exclusive()
    test_uninitialised_manager_does_not_raise()
    test_cap_always_spreads_the_first_tcin()
    print(f"\n{'ALL PASS' if not FAILS else 'FAILURES: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
