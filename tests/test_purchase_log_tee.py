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


def test_close_waits_for_inflight_write():
    """The 08-14 crash contract: close() must serialize behind an in-flight
    write() (unlocked close during a buffered write = 0xC0000005 in
    python312.dll — killed app.py 4x during the 08-14 restock)."""
    tee, _orig, _lp = _make("tee_close_race.log")

    write_entered = threading.Event()
    release_write = threading.Event()

    class _SlowFile:
        def write(self, data):
            write_entered.set()
            release_write.wait(timeout=5)

        def flush(self):
            pass

        def close(self):
            pass

    tee.__dict__["_file"] = _SlowFile()

    writer = threading.Thread(target=lambda: tee.write("x"))
    writer.start()
    assert write_entered.wait(timeout=5), "writer never entered _file.write"

    close_done = threading.Event()
    closer = threading.Thread(target=lambda: (tee.close(), close_done.set()))
    closer.start()
    # While the write holds the lock, close() must NOT complete.
    assert not close_done.wait(timeout=0.3), "close() ran DURING an in-flight write"
    release_write.set()
    writer.join(timeout=5)
    closer.join(timeout=5)
    assert close_done.is_set(), "close() deadlocked after the write released"
    print("[OK] close() serializes behind an in-flight write")


def test_concurrent_write_flush_close_storm():
    """Real-file storm in the instant-bail shape: writers + flushers hammering
    while close() lands mid-flight. Must finish with zero raises, no deadlock,
    and a closed file."""
    tee, _orig, _lp = _make("tee_storm.log")
    errors = []

    def hammer_write():
        for _ in range(3000):
            try:
                tee.write("y")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    def hammer_flush():
        for _ in range(1500):
            try:
                tee.flush()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    threads = [threading.Thread(target=hammer_write) for _ in range(4)]
    threads += [threading.Thread(target=hammer_flush) for _ in range(2)]
    for t in threads:
        t.start()
    tee.close()          # land the close in the middle of the storm
    tee.close()          # idempotent second close must also be safe
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "storm deadlocked"
    assert not errors, f"raised under write/flush/close storm: {errors[:3]}"
    assert tee.__dict__.get("_file") is None
    tee.write("post-close writes go console-only, no raise\n")
    print("[OK] 4xW+2xF storm with mid-flight close: 0 raises, no deadlock")


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"\n{n}/{n} passed")
