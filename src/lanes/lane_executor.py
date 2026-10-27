"""LaneExecutor — per-lane ATC firing loop.

Each lane runs its own loop: fire → read result → wait (per-lane cadence)
→ fire again.  The lane's cadence is driven by ITS OWN response history,
not a fleet-wide policy.

This replaces the wave-first 55-70s sleep with a per-lane decision:
  - Edge 429 → fast retry (3-4.5s) on THIS lane's IP bucket
  - DCO 429  → immediate burst (1-1.5s), up to 2 re-shots
  - Shape 401 → cool off (25s after 3 consecutive), then fresh re-entry
  - Success   → hand to the checkout chain, end this lane's race

The default fire_fn (from lane_pool._shooter_fire_fn) uses the lane's
curl_cffi shooter. On a 201 it hands the cart to the account's Chrome
for checkout. Custom fire_fn can be passed for testing.
"""

from __future__ import annotations

import asyncio
import os
import time
import random
import threading
from typing import Any, Callable, Dict, Optional

from .lane import Lane


# Race budget: how long one lane will keep firing on a single TCIN
RACE_BUDGET_S = float(os.environ.get('TARGET_LANE_RACE_BUDGET_S', '110'))


def lane_race_loop(
    lane: Lane,
    tcin: str,
    quantity: int,
    fire_fn: Callable[..., Dict[str, Any]],
    stock_live_fn: Optional[Callable[..., bool]] = None,
) -> Dict[str, Any]:
    """Run one lane's ATC race loop.

    Parameters
    ----------
    lane : Lane
        The lane running this race.
    tcin : str
        Product to buy.
    quantity : int
        Units to attempt.
    fire_fn : callable
        Called as fire_fn(lane, tcin, quantity) → dict with at minimum
        {'atc_status': int, 'body_hint': str, 'success': bool}.
        This should invoke the existing _api_fast_lane via the lane's
        worker's purchase_executor.
    stock_live_fn : callable, optional
        Called as stock_live_fn(tcin) → bool.  When it returns False,
        the lane ends its race (stock is gone).

    Returns
    -------
    dict
        Summary of this lane's race: shots fired, results, whether an
        order was placed.
    """
    race_start = time.time()
    shots = []
    best_result = None

    print(f"[LANE_EXEC] {lane.label()} starting race for {tcin} "
          f"(budget={RACE_BUDGET_S}s, fast={lane.cadence.fast_retry_min_s}-"
          f"{lane.cadence.fast_retry_max_s}s)")

    while True:
        elapsed = time.time() - race_start

        # Budget expired
        if elapsed >= RACE_BUDGET_S:
            print(f"[LANE_EXEC] {lane.label()} budget expired after "
                  f"{elapsed:.1f}s / {len(shots)} shots")
            break

        # Stock check
        if stock_live_fn is not None:
            try:
                if not stock_live_fn(tcin):
                    print(f"[LANE_EXEC] {lane.label()} stock gone for {tcin} "
                          f"after {len(shots)} shots")
                    break
            except Exception:
                pass

        # Wait for this lane's cadence (skip on first shot)
        if shots:
            delay = lane.cadence.next_fire_delay()
            remaining = RACE_BUDGET_S - (time.time() - race_start)
            if delay > remaining:
                print(f"[LANE_EXEC] {lane.label()} next delay {delay:.1f}s "
                      f"exceeds remaining budget {remaining:.1f}s — ending")
                break
            time.sleep(delay)

        # Fire
        shot_t0 = time.time()
        try:
            result = fire_fn(lane, tcin, quantity)
        except Exception as e:
            result = {'atc_status': 0, 'body_hint': str(e), 'success': False,
                      'error': str(e)}

        shot_elapsed = time.time() - shot_t0
        atc_status = result.get('atc_status', 0)
        body_hint = result.get('body_hint', '')
        success = result.get('success', False)

        # Record in cadence
        lane.cadence.record_result(atc_status, body_hint)

        shot_record = {
            'shot_n': len(shots) + 1,
            'atc_status': atc_status,
            'body_hint': body_hint[:80],
            'elapsed_s': round(shot_elapsed, 3),
            'race_age_s': round(time.time() - race_start, 1),
        }
        shots.append(shot_record)

        # Log
        _edge_tag = ''
        if atc_status == 429:
            if 'RATE_LIMITED' in body_hint or not body_hint:
                _edge_tag = ' [EDGE]'
            elif 'DCO' in body_hint or 'high demand' in body_hint:
                _edge_tag = ' [DCO]'
        elif atc_status == 401:
            _edge_tag = ' [SHAPE]'
        elif atc_status in (200, 201):
            _edge_tag = ' [CART!]'

        print(f"[LANE_EXEC] {lane.label()} shot #{len(shots)} "
              f"status={atc_status}{_edge_tag} "
              f"rt={shot_elapsed:.2f}s race={time.time() - race_start:.1f}s "
              f"next_delay={lane.cadence.next_fire_delay():.1f}s")

        # Success — hand off to checkout
        if success:
            best_result = result
            print(f"[LANE_EXEC] {lane.label()} ATC SUCCESS on shot #{len(shots)} — "
                  f"handing to checkout chain")
            break

        if best_result is None or atc_status > (best_result.get('atc_status') or 0):
            best_result = result

    # Race summary
    summary = {
        'lane': lane.lane_id,
        'tcin': tcin,
        'shots': len(shots),
        'edge_pass': lane.cadence.total_edge_pass,
        'orders': lane.cadence.total_orders,
        'elapsed_s': round(time.time() - race_start, 1),
        'shot_log': shots,
        'best_result': best_result,
    }

    print(f"[LANE_EXEC] {lane.label()} race done: {len(shots)} shots in "
          f"{summary['elapsed_s']}s, orders={lane.cadence.total_orders}")

    return summary
