#!/usr/bin/env python3
"""Regression test for the _PurchaseLogTee process-killer crash (2026-08-11).

Both app.py crashes that night (0xC0000005, launches #1 and #2) ended in the
same traceback: a purchase-cleanup print() -> _PurchaseLogTee.write() ->
`with self._lock:` -> __getattr__ -> AttributeError('_lock'), on a tee instance
whose __dict__ had lost its attributes (torn down by an overlapping purchase
thread while another thread was still printing — a race the ATC gate breaker's
0.0s bail-fasts hammer). A stdout tee must NEVER raise into print(); these tests
pin that contract.

Run: venv/Scripts/python.exe -m pytest tests/test_purchase_log_tee.py -q
 or: venv/Scripts/python.exe tests/test_purchase_log_tee.py
"""
import io
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.purchasing.bulletproof_purchase_manager import _PurchaseLogTee  # noqa: E402


def _make(tmp_name="tee_test.log"):
    orig = io.StringIO()
    log_path = Path(__file__).resolve().parent.parent / "logs" / "purchases" / tmp_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return _PurchaseLogTee(orig, log_path), orig, log_path


def test_happy_path_tees_to_both():
    tee, orig, log_path = _make("tee_happy.log")
    tee.write("hello world\n")
    tee.flush()
    tee.close()
    assert "hello world" in orig.getvalue()
    assert "hello world" in log_path.read_text(encoding="utf-8")
    print("[OK] happy path: tees to orig + file")


def test_write_never_raises_when_lock_missing():
    """The EXACT 08-11 crash: _lock gone from __dict__."""
    tee, _orig, _lp = _make("tee_nolock.log")
    del tee.__dict__["_lock"]
    tee.write("must not raise\n")   # was: AttributeError('_lock') -> process down
    tee.flush()
    print("[OK] write/flush survive a missing _lock")


def test_all_methods_survive_a_cleared_dict():
    """Interpreter-teardown shape: __dict__ fully cleared while a thread prints."""
    tee, _orig, _lp = _make("tee_cleared.log")
    tee.__dict__.clear()            # _lock, _orig, _file all gone
    tee.write("still no raise\n")
    tee.flush()
    tee.close()
    print("[OK] write/flush/close survive a cleared __dict__")


def test_concurrent_writes_during_teardown():
    """Stress the real race: many threads printing while one tears the tee down."""
    tee, _orig, _lp = _make("tee_race.log")
    errors = []

    def hammer():
        for _ in range(2000):
            try:
                tee.write("x")
            except Exception as e:   # noqa: BLE001 — the whole point is: never
                errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(6)]
    for t in threads:
        t.start()
    # Rip the instance apart mid-flight, repeatedly.
    for _ in range(50):
        tee.__dict__.pop("_lock", None)
        tee.__dict__.pop("_orig", None)
        tee.__dict__.pop("_file", None)
    for t in threads:
        t.join()
    assert not errors, f"write() raised under teardown race: {errors[:3]}"
    print(f"[OK] 6x2000 concurrent writes through a torn-down tee: 0 raises")


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"\n{n}/{n} passed")
