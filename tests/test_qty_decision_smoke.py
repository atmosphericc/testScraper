#!/usr/bin/env python3
"""Smoke test: optimistic-2 quantity decision (_decide_target_qty).

The bulk RedSky feed usually omits the per-customer limit, so the hint arrives as
1 even on limit-2 SKUs. Optimistic-2 targets the ceiling when the limit is
unreported and respects a genuinely-reported limit; the executor self-heals a
true limit-1 via its 422/409 retry. Covers the env knobs too.

No browser, no network. Run: python tests/test_qty_decision_smoke.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager as B  # noqa: E402

_QTY_ENV = ("TARGET_FORCE_QTY_1", "TARGET_QTY_CEILING", "TARGET_QTY_OPTIMISTIC")


def _clear_env():
    for k in _QTY_ENV:
        os.environ.pop(k, None)


def _q(redsky):
    return B._decide_target_qty(redsky)[0]


def test_unreported_limit_goes_optimistic_2():
    _clear_env()
    assert _q(1) == 2, "unreported (hint=1) should target ceiling 2"
    assert _q(0) == 2
    assert _q(None) == 2


def test_reported_limit_respected_and_capped():
    _clear_env()
    assert _q(2) == 2          # reported 2, ceiling 2
    assert _q(5) == 2          # reported 5 capped to ceiling 2
    assert _q(3) == 2


def test_force_qty_1_overrides_everything():
    _clear_env()
    os.environ["TARGET_FORCE_QTY_1"] = "true"
    try:
        assert _q(5) == 1 and _q(1) == 1
    finally:
        _clear_env()


def test_optimistic_off_falls_back_to_1():
    _clear_env()
    os.environ["TARGET_QTY_OPTIMISTIC"] = "0"
    try:
        assert _q(1) == 1          # unreported -> 1 when optimistic disabled
        assert _q(2) == 2          # reported still respected
    finally:
        _clear_env()


def test_custom_ceiling():
    _clear_env()
    os.environ["TARGET_QTY_CEILING"] = "3"
    try:
        assert _q(1) == 3          # optimistic uses the ceiling
        assert _q(5) == 3          # reported capped to ceiling
        assert _q(2) == 2          # reported below ceiling kept
    finally:
        _clear_env()


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} smoke tests passed.")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
