#!/usr/bin/env python3
"""Smoke test: 2026-09-11 won-cart ride — manager-side pure predicates.

The 09-11 drop's only ATC 201 was held 80 s at checkout and CLEARED while the
item stayed in stock 11+ min. What stopped a longer hold was OUR manager: the
150 s future timeout (cancel + browser restart) and the 200 s force-complete.
These pin the two pure decisions that now gate both walls:

  - _ride_extension_allowed: keep waiting past 150 s ONLY while the executor
    reports a live won cart (ride_until in the future), the flag is on, and
    the total wait is under the hard cap. Anything else -> classic timeout.
  - _force_complete_due: never force-finalize while a stamped ride deadline
    is still in the future; otherwise exactly `elapsed > force_s`.

No browser, no network. Run: python tests/test_won_cart_ride_smoke.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.bulletproof_purchase_manager import (  # noqa: E402
    BulletproofPurchaseManager as BPM,
)

NOW = time.time()
PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


def test_extension_allowed_while_riding():
    check("ext_allowed_live_ride",
          BPM._ride_extension_allowed(NOW + 120, NOW, 150, 300, True) is True)


def test_extension_denied_when_flag_off():
    check("ext_denied_flag_off",
          BPM._ride_extension_allowed(NOW + 120, NOW, 150, 300, False) is False)


def test_extension_denied_when_not_holding():
    check("ext_denied_not_holding_zero",
          BPM._ride_extension_allowed(0.0, NOW, 150, 300, True) is False)
    check("ext_denied_not_holding_none",
          BPM._ride_extension_allowed(None, NOW, 150, 300, True) is False)
    check("ext_denied_ride_expired",
          BPM._ride_extension_allowed(NOW - 1, NOW, 150, 300, True) is False)


def test_extension_denied_past_hard_cap():
    check("ext_denied_past_cap",
          BPM._ride_extension_allowed(NOW + 120, NOW, 300, 300, True) is False)


def test_extension_bad_value_denied():
    check("ext_bad_value_denied",
          BPM._ride_extension_allowed('nope', NOW, 10, 300, True) is False)


def test_force_complete_classic():
    # No ride stamped -> exactly the prior rule.
    check("force_due_no_ride", BPM._force_complete_due(201, 200, None, NOW) is True)
    check("force_not_due_no_ride", BPM._force_complete_due(199, 200, 0.0, NOW) is False)


def test_force_complete_respects_live_ride():
    check("force_deferred_live_ride",
          BPM._force_complete_due(250, 200, NOW + 60, NOW) is False)
    check("force_due_after_ride_ends",
          BPM._force_complete_due(250, 200, NOW - 1, NOW) is True)
    check("force_bad_value_treated_as_no_ride",
          BPM._force_complete_due(250, 200, 'nope', NOW) is True)


if __name__ == '__main__':
    test_extension_allowed_while_riding()
    test_extension_denied_when_flag_off()
    test_extension_denied_when_not_holding()
    test_extension_denied_past_hard_cap()
    test_extension_bad_value_denied()
    test_force_complete_classic()
    test_force_complete_respects_live_ride()
    print(f"\n=== {PASS}/{PASS + FAIL} passed ===")
    sys.exit(1 if FAIL else 0)
