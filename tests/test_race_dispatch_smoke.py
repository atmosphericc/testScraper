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


# ---- 2026-09-16 INF-2 (hot-sku 0916 plan P10): race-state started_at guard ----
# CA-8: the 60 s concurrency gate can reset a racing TCIN to a bare
# {'status': 'ready'}; the next racer's merge then wrote 'attempting' WITHOUT
# started_at, and check_and_complete_purchases force-completed it with
# elapsed = the Unix epoch ("REAL purchase timeout after 1789548887.0s"), while
# the concurrency gate read the same record as elapsed 0 (blocks forever).
# TARGET_RACE_STATE_STARTED_AT_GUARD=1 stamps instead of skipping.
import contextlib  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

_GUARD = "TARGET_RACE_STATE_STARTED_AT_GUARD"


@contextlib.contextmanager
def _guard(on):
    old = os.environ.get(_GUARD)
    if on:
        os.environ[_GUARD] = "1"
    else:
        os.environ.pop(_GUARD, None)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(_GUARD, None)
        else:
            os.environ[_GUARD] = old


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **k)
    return r, buf.getvalue()


def _real_mgr(states):
    """A BulletproofPurchaseManager without __init__ (no browsers, no disk)."""
    m = object.__new__(BulletproofPurchaseManager)
    m._state_lock = threading.RLock()
    m._states = {k: dict(v) for k, v in states.items()}
    m._load_states_unsafe = lambda: {k: dict(v) for k, v in m._states.items()}

    def _save(s):
        m._states = {k: dict(v) for k, v in s.items()}
        return True
    m._save_states_unsafe = _save
    m.status_callback = None
    m._active_purchases = {}
    m.worker_pool = None
    m._warmup_cycle_counter = 5
    m._maybe_run_session_sentinel = lambda: None
    m.started = []

    def _start(tcin, title, max_qty=1):
        m.started.append(tcin)
        return {"success": True, "duration": 1}
    m.start_purchase = _start
    return m


def test_inf2_bare_ready_plus_inflight_record_gets_started_at():
    for on in (False, True):
        stub, agg = _Stub(), _agg(2)
        agg["t0"] = 1234567.0
        stub._states = {"123": {"status": "ready"}}      # reset by the 60 s gate
        with _guard(on):
            _quiet(_record, stub, agg, {"success": False, "reason": "rate_limited_429"}, "W1/primary")
        st = stub._states["123"]
        assert st["status"] == "attempting", st
        if on:
            assert st.get("started_at") == 1234567.0, st
        else:
            assert "started_at" not in st, st
    # no t0 on the aggregate -> stamped now
    stub, agg = _Stub(), _agg(2)
    stub._states = {"123": {"status": "ready"}}
    with _guard(True):
        _quiet(_record, stub, agg, {"success": False, "reason": "x"}, "W1/primary")
    assert abs(stub._states["123"]["started_at"] - time.time()) < 5, stub._states
    # an existing stamp is never overwritten; a finalized record is not stamped
    stub, agg = _Stub(), _agg(2)
    agg["t0"] = 5.0
    stub._states = {"123": {"status": "attempting", "started_at": 777.0}}
    with _guard(True):
        _quiet(_record, stub, agg, {"success": False, "reason": "x"}, "W1/primary")
        assert stub._states["123"]["started_at"] == 777.0, stub._states
        stub2, agg2 = _Stub(), _agg(1)
        stub2._states = {"123": {"status": "ready"}}
        _quiet(_record, stub2, agg2, {"success": False, "reason": "x"}, "W1/primary")
        assert stub2._states["123"]["status"] == "failed", stub2._states
        assert "started_at" not in stub2._states["123"], stub2._states


def test_inf2_missing_started_at_never_epoch_force_completed():
    rec = {"status": "attempting", "real_purchase": True, "tcin": "123", "race": True}
    # flag off: today's behaviour (documents the CA-8 bug)
    m = _real_mgr({"123": rec})
    with _guard(False):
        done, out = _quiet(m.check_and_complete_purchases)
    assert done == [("123", "failed")], done
    assert "timeout after 1" in out and float(out.split("timeout after ")[1].split("s")[0]) > 1e8, out
    # flag on: stamped, not force-completed
    m = _real_mgr({"123": rec})
    with _guard(True):
        done, out = _quiet(m.check_and_complete_purchases)
        assert done == [], done
        assert "[RACE_STATE] 123" in out, out
        st = m._states["123"]
        assert st["status"] == "attempting" and abs(st["started_at"] - time.time()) < 5, st
        done, out = _quiet(m.check_and_complete_purchases)
        assert done == [] and "[RACE_STATE]" not in out, (done, out)
        # the normal force-complete window then applies with an honest elapsed
        m._states["123"]["started_at"] = time.time() - 500
        done, out = _quiet(m.check_and_complete_purchases)
        assert done == [("123", "failed")], done
        el = float(out.split("timeout after ")[1].split("s")[0])
        assert 400 < el < 1000, out


def test_inf2_gate_stamps_then_resets_after_60s():
    rec = {"status": "attempting", "real_purchase": True}
    stock = {"999": {"in_stock": True, "title": "Other", "max_qty": 1}}
    # flag off: the stamp-less record blocks every dispatch, tick after tick
    m = _real_mgr({"123": rec})
    with _guard(False):
        _quiet(m.process_stock_data, stock)
        _quiet(m.process_stock_data, stock)
    assert m.started == [], m.started
    assert "started_at" not in m._states["123"], m._states
    # flag on: stamped now (still blocks this tick) ...
    m = _real_mgr({"123": rec})
    with _guard(True):
        _, out = _quiet(m.process_stock_data, stock)
        assert m.started == [], m.started
        assert abs(m._states["123"]["started_at"] - time.time()) < 5, m._states
        assert "had no started_at" in out, out
        # ... and the normal 60 s stuck rule resets it once it is old
        m._states["123"]["started_at"] = time.time() - 61
        _quiet(m.process_stock_data, stock)
    assert m._states["123"] == {"status": "ready"}, m._states
    assert m.started == ["999"], m.started
    # queued records get the same stamp
    m = _real_mgr({"123": {"status": "queued"}})
    with _guard(True):
        _quiet(m.process_stock_data, stock)
    assert isinstance(m._states["123"].get("started_at"), float), m._states


def test_inf2_race_agg_carries_t0():
    src = (ROOT / "src" / "purchasing" / "bulletproof_purchase_manager.py").read_text(encoding="utf-8")
    assert "'t0': time.time()" in src, "race_agg lost its t0 stamp"


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
