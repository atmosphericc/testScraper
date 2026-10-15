#!/usr/bin/env python3
"""Offline test: FAST_SELLING back-off per TCIN (2026-10-14, C-1014-15).

TARGET_FAST_SELLING_COOLDOWN_PER_TCIN=1: a place-order FAST_SELLING on item A starts the
45 s back-off for A only; this account's next shot on item B keeps the fast lane and its
legacy hold does not wait out A's window. Unset / 0 = the account-wide window, exactly as
before. No browser, no network.

    python tests/test_fs_cooldown_per_tcin_1014.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

PASSED, FAILED = [], []
FLAG = "TARGET_FAST_SELLING_COOLDOWN_PER_TCIN"
A, B = "1010892076", "1011960739"


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  {detail}"))


class env:
    def __init__(self, **kv):
        self.kv, self.saved = kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def bare():
    ex = PurchaseExecutor.__new__(PurchaseExecutor)
    ex._fast_selling_until = 0.0
    ex._fs_until_by_tcin = {}
    ex._exec_tcin = ""
    ex.test_mode = False
    return ex


def main():
    # ---- flag OFF: account-wide, exactly as before ----
    with env(**{FLAG: None, "TARGET_FAST_SELLING_COOLDOWN_S": "45"}):
        ex = bare()
        ex._exec_tcin = A
        ex._note_fast_selling_throttle()
        check("off_A_cooling", ex._fast_selling_cooling_down(A) is True)
        check("off_B_cooling_too", ex._fast_selling_cooling_down(B) is True)
        check("off_no_arg_cooling", ex._fast_selling_cooling_down() is True)
        check("off_left_is_global", abs(ex._fs_cooldown_left(B) - (ex._fast_selling_until - time.time())) < 0.05)
    # ---- flag ON: per TCIN ----
    with env(**{FLAG: "1", "TARGET_FAST_SELLING_COOLDOWN_S": "45"}):
        ex = bare()
        ex._exec_tcin = A
        ex._note_fast_selling_throttle()
        check("on_A_cooling", ex._fast_selling_cooling_down(A) is True)
        check("on_B_free", ex._fast_selling_cooling_down(B) is False)
        check("on_global_still_set", ex._fast_selling_until > time.time() + 40)   # flag-off readers unchanged
        # the legacy hold for a cart won on B does not wait out A's window ...
        ex._exec_tcin = B
        with env(TARGET_FAST_SELLING_HOLD_CART="1"):
            t0 = time.time()
            held = asyncio.run(ex._hold_cart_for_fast_selling("pre-shot"))
        check("on_hold_B_no_wait", held is False and time.time() - t0 < 1.0, held)
        # ... while a cart on A still holds (capped tiny so the test stays fast)
        ex._exec_tcin = A
        with env(TARGET_FAST_SELLING_HOLD_CART="1"):
            held = asyncio.run(ex._hold_cart_for_fast_selling("pre-shot", max_s=0.01))
        check("on_hold_A_holds", held is True, held)
        # expiry
        ex._fs_until_by_tcin[A] = time.time() - 1
        check("on_A_expired", ex._fast_selling_cooling_down(A) is False)
        # the won-cart loop's exit note goes per TCIN too, and never shortens a window
        ex._fs_note_tcin(B, time.time() + 30)
        ex._fs_note_tcin(B, time.time() + 5)
        check("on_note_max", ex._fs_cooldown_left(B) > 25)
        # missing attributes (older stubs) never raise
        ex2 = PurchaseExecutor.__new__(PurchaseExecutor)
        ex2._fast_selling_until = 0.0
        check("on_bare_attrs_safe", ex2._fast_selling_cooling_down(A) is False)
        ex2._fs_note_tcin(A, time.time() + 10)
        check("on_bare_note_creates", ex2._fs_cooldown_left(A) > 5)
    # ---- the gate passes the TCIN; the wrapper arms the flag once ----
    src = (ROOT / "src" / "session" / "purchase_executor.py").read_text(encoding="utf-8")
    check("gate_passes_tcin", "and not self._fast_selling_cooling_down(tcin)):" in src)
    check("impl_stamps_tcin", "self._exec_tcin = str(tcin)" in src)
    bat = (ROOT / "run_bot_with_nightly_restart.bat").read_bytes().decode("utf-8", "replace").splitlines()
    sets = [l for l in bat if l.startswith(f"set {FLAG}=")]
    check("bat_armed_once", sets == [f"set {FLAG}=1"], sets)
    print(f"=== {len(PASSED)}/{len(PASSED) + len(FAILED)} passed ===")
    for n in FAILED:
        print(f"  FAIL: {n}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
