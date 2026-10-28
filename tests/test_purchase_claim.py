#!/usr/bin/env python3
"""Cross-path double-fire guard (src/purchasing/purchase_claim.py).

Why this exists (2026-10-27). Under TARGET_LANE_MODE=1 the same in-stock TCIN is
handed to BOTH purchase paths: the main manager (app.py:2097, unconditional) and
the lane pool (app.py:2099-2103). Both check out as the SAME logged-in member
account and the SAME saved card, and they shared NO reservation / dedup / mutex,
so two real orders / two charges could land on one account x TCIN. The lane pool
also fans one flip to EVERY lane of an account, so the lanes could double-fire an
account against themselves.

PurchaseClaimRegistry is one strictly-exclusive per-(account_id, TCIN) claim both
paths consult. These tests pin: (1) the registry's claim/release semantics and
thread-safety; (2) the lane pool fires an account x TCIN at most once and yields
to a claim the main path holds; (3) the main path acquires/releases the claim
around its worker reservation, yields to a lane's claim, and -- critically --
behaves EXACTLY as before when no registry is injected (lanes off); (4) the
stale-reservation sweep frees a leaked claim.

No browser, no network, no bot. Run: python tests/test_purchase_claim.py
"""
from __future__ import annotations

import os
import sys
import time
import threading
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, HERE)  # so we can reuse the real-dispatch harness

from src.purchasing.purchase_claim import PurchaseClaimRegistry  # noqa: E402
from src.lanes.lane import Lane  # noqa: E402
from src.lanes.lane_pool import LanePool  # noqa: E402
from src.purchasing.bulletproof_purchase_manager import (  # noqa: E402
    BulletproofPurchaseManager as M,
)
import test_identity_rest as H  # noqa: E402  (harness: race_mgr, _Worker, env, quiet, _race, HOT_TCIN, FAST_RETRY)


# ── helpers ───────────────────────────────────────────────────────────────

class _W:
    """Minimal worker: the reservation only ever touches .label()."""
    def __init__(self, lbl):
        self._lbl = lbl

    def label(self):
        return self._lbl


def _stub_manager(reg=None):
    """A REAL manager with only the reservation state materialised (no
    __init__, no browser, no network) -- mirrors test_multi_sku_dispatch so
    the test exercises the shipping reserve/release/sweep code, not a stub."""
    m = M.__new__(M)
    m._reserve_lock = threading.Lock()
    m._worker_reservations = {}
    m._xpath_claim = reg
    return m


def _lane(aid, iplabel):
    return Lane(lane_id=f"{aid}@{iplabel}", account_id=aid, ip_label=iplabel)


def _blocking_fn(gate, started):
    """A fire_fn that records the lane then parks on `gate`, so the lane's
    claim stays held while the test asserts registry state deterministically."""
    def fn(lane, tcin, qty):
        started.append(lane.lane_id)
        gate.wait(timeout=5.0)
    return fn


def _drain(pool, tcin, qty, gate, started):
    threads = pool.on_stock_flip(tcin, qty, fire_fn=_blocking_fn(gate, started))
    return threads


# ── registry semantics ──────────────────────────────────────────────────

def test_claim_grants_when_free():
    reg = PurchaseClaimRegistry()
    assert reg.try_claim("primary", "T1", "h1") is True
    assert reg.owner("primary", "T1") == "h1"
    assert reg.active_count() == 1


def test_claim_reentrant_same_holder():
    reg = PurchaseClaimRegistry()
    assert reg.try_claim("primary", "T1", "h1") is True
    assert reg.try_claim("primary", "T1", "h1") is True  # idempotent for same holder
    assert reg.active_count() == 1


def test_claim_refuses_other_holder():
    reg = PurchaseClaimRegistry()
    assert reg.try_claim("primary", "T1", "h1") is True
    assert reg.try_claim("primary", "T1", "h2") is False
    assert reg.owner("primary", "T1") == "h1"  # existing claim untouched


def test_claim_independent_keys():
    reg = PurchaseClaimRegistry()
    assert reg.try_claim("primary", "T1", "h1")
    assert reg.try_claim("primary", "T2", "h2")   # different TCIN, same account
    assert reg.try_claim("alt-1", "T1", "h3")     # different account, same TCIN
    assert reg.active_count() == 3


def test_release_by_owner_frees_slot():
    reg = PurchaseClaimRegistry()
    reg.try_claim("primary", "T1", "h1")
    reg.release("primary", "T1", "h1")
    assert reg.owner("primary", "T1") is None
    assert reg.try_claim("primary", "T1", "h2") is True  # free for the next claimant


def test_release_by_nonowner_is_noop():
    reg = PurchaseClaimRegistry()
    reg.try_claim("primary", "T1", "h1")
    reg.release("primary", "T1", "h2")  # not the owner -> must not free it
    assert reg.owner("primary", "T1") == "h1"


def test_release_all_for_holder():
    reg = PurchaseClaimRegistry()
    reg.try_claim("primary", "T1", "h1")
    reg.try_claim("alt-1", "T2", "h1")
    reg.try_claim("primary", "T3", "h2")
    freed = reg.release_all_for_holder("h1")
    assert freed == 2
    assert reg.owner("primary", "T3") == "h2"
    assert reg.active_count() == 1


def test_concurrent_claims_single_winner():
    """20 threads race one slot -> exactly one wins (the atomicity guarantee)."""
    reg = PurchaseClaimRegistry()
    n = 20
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker(i):
        barrier.wait()
        ok = reg.try_claim("primary", "T1", f"h{i}")
        with lock:
            results.append(ok)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=5.0)
    assert sum(1 for r in results if r) == 1, f"exactly one winner, got {sum(bool(r) for r in results)}"
    assert reg.active_count() == 1


# ── lane pool: at most one fire per account x TCIN ──────────────────────

def test_lane_pool_intra_account_single_fire():
    """Two lanes of ONE account on one flip -> exactly one fires; the other
    yields on the claim. (The collision the guard closes.)"""
    reg = PurchaseClaimRegistry()
    pool = LanePool([_lane("primary", "bd0"), _lane("primary", "bd1")],
                    claim_registry=reg)
    gate, started = threading.Event(), []
    threads = _drain(pool, "T1", 1, gate, started)
    try:
        assert len(threads) == 1, f"expected 1 fire, got {len(threads)}"
        assert reg.owner("primary", "T1") in ("primary@bd0", "primary@bd1")
        assert reg.active_count() == 1
    finally:
        gate.set()
        for t in threads:
            t.join(timeout=5.0)
    # released once the runner exits -> a later flip can fire again
    assert reg.owner("primary", "T1") is None
    assert reg.active_count() == 0


def test_lane_pool_no_registry_double_fires():
    """Contrast / regression anchor: with NO registry the OLD behaviour stands
    -- both lanes of one account fire the same TCIN (the bug)."""
    pool = LanePool([_lane("primary", "bd0"), _lane("primary", "bd1")])
    gate, started = threading.Event(), []
    threads = _drain(pool, "T1", 1, gate, started)
    try:
        assert len(threads) == 2, f"without the guard both lanes fire; got {len(threads)}"
    finally:
        gate.set()
        for t in threads:
            t.join(timeout=5.0)


def test_lane_pool_different_accounts_both_fire():
    """Different accounts are independent -> both fire (no false exclusion)."""
    reg = PurchaseClaimRegistry()
    pool = LanePool([_lane("primary", "bd0"), _lane("alt-1", "bd0")],
                    claim_registry=reg)
    gate, started = threading.Event(), []
    threads = _drain(pool, "T1", 1, gate, started)
    try:
        assert len(threads) == 2, f"both accounts should fire, got {len(threads)}"
        assert reg.owner("primary", "T1") == "primary@bd0"
        assert reg.owner("alt-1", "T1") == "alt-1@bd0"
    finally:
        gate.set()
        for t in threads:
            t.join(timeout=5.0)


def test_lane_pool_yields_to_main_path_claim():
    """A lane must yield an account x TCIN the main path already owns."""
    reg = PurchaseClaimRegistry()
    assert reg.try_claim("primary", "T1", "W1/primary")  # main path holds it
    pool = LanePool([_lane("primary", "bd0")], claim_registry=reg)
    gate, started = threading.Event(), []
    threads = _drain(pool, "T1", 1, gate, started)
    try:
        assert len(threads) == 0, "lane must yield to the main path's claim"
        assert reg.owner("primary", "T1") == "W1/primary"  # untouched
    finally:
        gate.set()
        for t in threads:
            t.join(timeout=5.0)


# ── main path: reserve / release / yield / sweep ────────────────────────

def test_main_reserve_acquires_and_releases_claim():
    reg = PurchaseClaimRegistry()
    m = _stub_manager(reg)
    taken = m._reserve_workers_for_tcin("T1", [_W("W1/primary")], 1)
    assert len(taken) == 1
    assert reg.owner("primary", "T1") == "W1/primary"
    m._release_worker_reservation("W1/primary")
    assert reg.owner("primary", "T1") is None
    assert "W1/primary" not in m._worker_reservations


def test_main_reserve_yields_to_lane_claim():
    reg = PurchaseClaimRegistry()
    assert reg.try_claim("primary", "T1", "primary@bd0")  # a lane owns it
    m = _stub_manager(reg)
    taken = m._reserve_workers_for_tcin("T1", [_W("W1/primary")], 1)
    assert taken == [], "main path must not take a worker a lane owns"
    assert "W1/primary" not in m._worker_reservations  # reservation not taken
    assert reg.owner("primary", "T1") == "primary@bd0"  # untouched


def test_main_reserve_idempotent_same_worker_same_tcin():
    reg = PurchaseClaimRegistry()
    m = _stub_manager(reg)
    w = _W("W1/primary")
    m._reserve_workers_for_tcin("T1", [w], 1)
    taken2 = m._reserve_workers_for_tcin("T1", [w], 1)  # re-reserve own worker
    assert len(taken2) == 1, "own re-reservation of same (worker, TCIN) stays granted"
    assert reg.owner("primary", "T1") == "W1/primary"


def test_main_reserve_noop_without_registry():
    """MODE=0 invariant: no registry -> reserve/release behave exactly as
    before, with no registry interaction and no exception."""
    m = _stub_manager(None)
    taken = m._reserve_workers_for_tcin("T1", [_W("W1/primary")], 1)
    assert len(taken) == 1
    assert m._worker_reservations.get("W1/primary") is not None
    m._release_worker_reservation("W1/primary")
    assert "W1/primary" not in m._worker_reservations


def test_stale_sweep_releases_leaked_claim():
    """A racer that died without releasing leaks its claim; the stale sweep
    must free it so the slot can never be permanently blocked."""
    reg = PurchaseClaimRegistry()
    m = _stub_manager(reg)
    m._reserve_workers_for_tcin("T1", [_W("W1/primary")], 1)
    assert reg.owner("primary", "T1") == "W1/primary"
    # age the reservation past any TTL
    with m._reserve_lock:
        tcin, _ts = m._worker_reservations["W1/primary"]
        m._worker_reservations["W1/primary"] = (tcin, 0.0)
    m._sweep_stale_reservations(ttl_s=1)
    assert "W1/primary" not in m._worker_reservations
    assert reg.owner("primary", "T1") is None


# ── main path: the LEGACY single-worker branch (race_on False) via the real
#    dispatch. This closes the gap the claims-verifier found: the reservation
#    is bypassed on this branch, so the claim lives in execute_real_purchase.
#    One ready worker -> len(ready) == 1 -> race_on False -> legacy branch.

def test_legacy_single_worker_yields_to_lane_claim():
    """With a lane already holding primary x TCIN, the legacy single-worker
    path must NOT fire (purchase executor never called)."""
    reg = PurchaseClaimRegistry()
    w = H._Worker(1, "primary", [{"success": False, "reason": "rate_limited_429"}])
    m = H.race_mgr([w])
    m._xpath_claim = reg
    assert reg.try_claim("primary", H.HOT_TCIN, "primary@bd0")  # a lane owns it
    with H.env(**H.FAST_RETRY):
        ok, out = H.quiet(H._race, m, H.HOT_TCIN)
    assert ok, f"dispatch did not complete; recorded={m.recorded}"
    assert w.purchase_executor.calls == 0, \
        f"legacy path must not fire when a lane owns the slot; calls={w.purchase_executor.calls}"
    assert any(r.get("reason") == "xpath_claimed" for _, r in m.recorded), \
        f"expected an xpath_claimed yield result; got {m.recorded}"
    assert reg.owner("primary", H.HOT_TCIN) == "primary@bd0"  # lane's claim untouched


def test_legacy_single_worker_fires_and_releases():
    """With no competing claim, the legacy single-worker path fires and the
    claim it takes is released after the attempt (finally)."""
    reg = PurchaseClaimRegistry()
    w = H._Worker(1, "primary", [{"success": False, "reason": "rate_limited_429"}])
    m = H.race_mgr([w])
    m._xpath_claim = reg
    with H.env(**H.FAST_RETRY):
        ok, out = H.quiet(H._race, m, H.HOT_TCIN)
    assert ok, f"dispatch did not complete; recorded={m.recorded}"
    assert w.purchase_executor.calls >= 1, \
        f"legacy path should fire when unclaimed; calls={w.purchase_executor.calls}"
    # released in execute_real_purchase's finally once the attempt ends
    for _ in range(40):
        if reg.owner("primary", H.HOT_TCIN) is None:
            break
        time.sleep(0.05)
    assert reg.owner("primary", H.HOT_TCIN) is None, "claim must be released after the attempt"
    assert reg.active_count() == 0


def test_racing_fleet_with_registry_all_fire_and_release():
    """Racing branch (MULTI_SKU on, >1 ready) with the registry set: each racer
    is a DISTINCT account, so all fire (the in-thread claim is idempotent with
    the synchronous reservation -- no spurious yield), and every claim releases."""
    reg = PurchaseClaimRegistry()
    fail = [{"success": False, "reason": "rate_limited_429"}]
    ws = [H._Worker(1, "primary", fail), H._Worker(2, "business", fail)]
    m = H.race_mgr(ws)
    m._xpath_claim = reg
    with H.env(**dict(H.FAST_RETRY, TARGET_MULTI_SKU_DISPATCH="1")):
        ok, out = H.quiet(H._race, m, H.HOT_TCIN)
    assert ok, f"race did not complete; recorded={m.recorded}"
    assert ws[0].purchase_executor.calls >= 1 and ws[1].purchase_executor.calls >= 1, \
        f"both distinct accounts must fire; calls={[w.purchase_executor.calls for w in ws]}"
    for _ in range(40):
        if reg.active_count() == 0:
            break
        time.sleep(0.05)
    assert reg.active_count() == 0, f"all claims must release; left={reg.snapshot()}"


# ── wiring regression (C-1027): the app.py MODE=1 lane init must build the
#    worker_map and inject the registry correctly. A pre-existing `w.config`
#    typo crashed lane init before the registry was ever injected, so flipping
#    TARGET_LANE_MODE=1 ran the main path UNGUARDED. These pin the fix.

def test_worker_exposes_cfg_not_config():
    """A real Worker stores its config as .cfg (worker.py:87); it has no
    .config. app.py's lane worker_map must read w.cfg.account_id."""
    from src.purchasing.worker import Worker
    w = Worker()
    assert w.cfg.account_id, "Worker().cfg.account_id must be readable"
    assert not hasattr(w, "config"), "Worker has no .config — app.py must use .cfg"


def test_app_lane_wiring_uses_cfg_and_injects_registry():
    """app.py's TARGET_LANE_MODE=1 block must build worker_map via
    w.cfg.account_id (NOT w.config, which crashed init) and inject one shared
    PurchaseClaimRegistry into the main manager and the LaneManager."""
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "worker_map[w.cfg.account_id] = w" in app, \
        "lane worker_map must use w.cfg.account_id"
    assert "w.config.account_id" not in app, \
        "w.config.account_id crashes lane init (Worker has no .config)"
    assert "_xpath_claim = PurchaseClaimRegistry()" in app
    assert "global_purchase_manager._xpath_claim = _xpath_claim" in app
    assert "claim_registry=_xpath_claim" in app


def test_lanepool_init_carries_registry():
    """The registry passed to LanePool is retained (the create -> from_config ->
    LanePool injection chain's final hop)."""
    reg = PurchaseClaimRegistry()
    assert LanePool([], claim_registry=reg).claim_registry is reg
    assert LanePool([]).claim_registry is None


_TESTS = [
    test_claim_grants_when_free,
    test_claim_reentrant_same_holder,
    test_claim_refuses_other_holder,
    test_claim_independent_keys,
    test_release_by_owner_frees_slot,
    test_release_by_nonowner_is_noop,
    test_release_all_for_holder,
    test_concurrent_claims_single_winner,
    test_lane_pool_intra_account_single_fire,
    test_lane_pool_no_registry_double_fires,
    test_lane_pool_different_accounts_both_fire,
    test_lane_pool_yields_to_main_path_claim,
    test_main_reserve_acquires_and_releases_claim,
    test_main_reserve_yields_to_lane_claim,
    test_main_reserve_idempotent_same_worker_same_tcin,
    test_main_reserve_noop_without_registry,
    test_stale_sweep_releases_leaked_claim,
    test_legacy_single_worker_yields_to_lane_claim,
    test_legacy_single_worker_fires_and_releases,
    test_racing_fleet_with_registry_all_fire_and_release,
    test_worker_exposes_cfg_not_config,
    test_app_lane_wiring_uses_cfg_and_injects_registry,
    test_lanepool_init_carries_registry,
]


if __name__ == '__main__':
    passed = 0
    failed = 0
    for fn in _TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(1 if failed else 0)
