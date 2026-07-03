#!/usr/bin/env python3
"""Smoke test: multi-account race aggregation (_record_race_result).

Verifies the new fan-out's state-merge logic without launching browsers:
  - while racers are in flight the TCIN stays 'attempting' (dup-guard holds)
  - when the last racer reports, state finalizes to 'purchased' if ANY account
    bought, carrying units_bought (summed, qty-aware), per-account breakdown,
    and every captured order number
  - all-fail finalizes to 'failed' with a representative reason
  - concurrent racers don't lose updates (thread-safe aggregate)

No browser, no network. Run: python tests/test_race_dispatch_smoke.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager  # noqa: E402


class _Stub:
    """Minimal stand-in exposing only what _record_race_result touches."""
    def __init__(self):
        self._state_lock = threading.RLock()
        self._states = {"123": {"status": "attempting", "tcin": "123"}}
        self.status_callback = None

    def _load_states_unsafe(self):
        return dict(self._states)

    def _save_states_unsafe(self, states):
        self._states = states


def _agg(total):
    return {"lock": threading.Lock(), "total": total, "started": 0,
            "finished": 0, "results": {}, "units": 0}


def _record(stub, agg, result, label):
    # Call the real method against the stub.
    BulletproofPurchaseManager._record_race_result(stub, "123", result, agg, label)


def test_in_flight_stays_attempting():
    stub, agg = _Stub(), _agg(3)
    _record(stub, agg, {"success": True, "quantity": 2, "order_id": "A1"}, "W1/primary")
    st = stub._states["123"]
    assert st["status"] == "attempting", st["status"]
    assert st["accounts_done"] == 1 and st["units_bought"] == 2, st


def test_finalize_purchased_with_units_and_orders():
    stub, agg = _Stub(), _agg(3)
    _record(stub, agg, {"success": True, "quantity": 2, "order_id": "A1"}, "W1/primary")
    _record(stub, agg, {"success": False, "reason": "rate_limited_429"}, "W2/alt-1")
    _record(stub, agg, {"success": True, "quantity": 1, "order_id": "A3"}, "W3/alt-2")
    st = stub._states["123"]
    assert st["status"] == "purchased", st["status"]
    assert st["units_bought"] == 3, st["units_bought"]          # 2 + 1
    assert st["accounts_done"] == 3 and st["accounts_total"] == 3
    assert set(st["order_numbers"]) == {"A1", "A3"}, st["order_numbers"]
    assert st["race_breakdown"]["W2/alt-1"] == "rate_limited_429", st["race_breakdown"]


def test_all_fail_finalizes_failed():
    stub, agg = _Stub(), _agg(2)
    _record(stub, agg, {"success": False, "reason": "rate_limited_429"}, "W1/primary")
    _record(stub, agg, {"success": False, "reason": "atc_failed_api_mode"}, "W2/alt-1")
    st = stub._states["123"]
    assert st["status"] == "failed", st["status"]
    assert st["units_bought"] == 0, st["units_bought"]
    assert st["failure_reason"] in ("rate_limited_429", "atc_failed_api_mode"), st


def test_concurrent_records_no_loss():
    stub, agg = _Stub(), _agg(20)
    threads = []
    for i in range(20):
        r = {"success": True, "quantity": 1, "order_id": f"O{i}"}
        t = threading.Thread(target=_record, args=(stub, agg, r, f"W{i}"))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    st = stub._states["123"]
    assert st["accounts_done"] == 20, st["accounts_done"]
    assert st["units_bought"] == 20, st["units_bought"]
    assert st["status"] == "purchased", st["status"]


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
