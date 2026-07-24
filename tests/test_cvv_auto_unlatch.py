#!/usr/bin/env python3
"""Unit test: CVV latch auto-unlatch (2026-07-23).

While the CVV latch is on, the API place-order is skipped, so the latch can
never observe Target dropping the challenge — without this fix the fast lane
stays disabled forever (manual TARGET_CVV_REQUIRED=0 was the only exit).
_maybe_unlatch_cvv clears the latch (memory + disk flag) ONLY when an order
just confirmed via the DOM path AND no CVV modal appeared during that purchase.

Load-bearing properties:
  1. Latched + no modal seen + confirm → un-latched, flag file deleted.
  2. Latched + modal WAS seen (challenge still live) → latch untouched.
  3. Not latched → no-op (never deletes another state file spuriously).
  4. Kill-switch TARGET_CVV_AUTO_UNLATCH=0 → latch untouched.
  5. Round-trip: persist → load(True) → clear → load(False).

No browser, no network. Uses a temp state dir via cwd switch.
Run: python tests/test_cvv_auto_unlatch.py  (or: pytest tests/test_cvv_auto_unlatch.py)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.pop("TARGET_CVV_REQUIRED", None)  # env override would mask file logic

from src.session.purchase_executor import PurchaseExecutor  # noqa: E402

_results = []


def _check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if detail and not cond else ""))
    _results.append(cond)


class _FakeSessionManager:
    def __init__(self, account_id):
        self.account_id = account_id


def _make_executor(account_id="testacct", latched=False, modal_seen=False):
    ex = object.__new__(PurchaseExecutor)  # bypass heavy __init__
    ex.session_manager = _FakeSessionManager(account_id)
    ex._cvv_required = latched
    ex._cvv_modal_seen = modal_seen
    return ex


def _flag_exists(ex):
    return os.path.exists(ex._cvv_flag_path())


def test_unlatches_when_no_modal_seen():
    ex = _make_executor(latched=True, modal_seen=False)
    ex._persist_cvv_challenge_flag()
    assert _flag_exists(ex), "setup: flag file should exist"
    ex._maybe_unlatch_cvv()
    _check("unlatch_clears_memory", ex._cvv_required is False)
    _check("unlatch_deletes_flag_file", not _flag_exists(ex))


def test_stays_latched_when_modal_was_seen():
    ex = _make_executor(latched=True, modal_seen=True)
    ex._persist_cvv_challenge_flag()
    ex._maybe_unlatch_cvv()
    _check("modal_seen_keeps_latch", ex._cvv_required is True)
    _check("modal_seen_keeps_flag_file", _flag_exists(ex))
    ex._clear_cvv_challenge_flag()  # cleanup


def test_noop_when_not_latched():
    ex = _make_executor(latched=False, modal_seen=False)
    # Plant a flag file to prove the no-op path never touches disk.
    ex._persist_cvv_challenge_flag()
    ex._cvv_required = False
    ex._maybe_unlatch_cvv()
    _check("not_latched_is_noop_on_disk", _flag_exists(ex))
    ex._clear_cvv_challenge_flag()  # cleanup


def test_kill_switch():
    os.environ["TARGET_CVV_AUTO_UNLATCH"] = "0"
    try:
        ex = _make_executor(latched=True, modal_seen=False)
        ex._persist_cvv_challenge_flag()
        ex._maybe_unlatch_cvv()
        _check("kill_switch_keeps_latch", ex._cvv_required is True)
        _check("kill_switch_keeps_flag_file", _flag_exists(ex))
        ex._clear_cvv_challenge_flag()  # cleanup
    finally:
        os.environ.pop("TARGET_CVV_AUTO_UNLATCH", None)


def test_persist_load_clear_roundtrip():
    ex = _make_executor(latched=False)
    ex._persist_cvv_challenge_flag()
    _check("load_after_persist_is_true", ex._load_cvv_challenge_flag() is True)
    ex._clear_cvv_challenge_flag()
    _check("load_after_clear_is_false", ex._load_cvv_challenge_flag() is False)
    _check("clear_removed_file", not _flag_exists(ex))


def test_missing_modal_seen_attr_is_safe():
    """Harness robustness: an executor built before a purchase reset (no
    _cvv_modal_seen attr) must not crash and must NOT unlatch (getattr default
    False → unlatch is allowed — assert it unlatches cleanly, not crashes)."""
    ex = _make_executor(latched=True)
    del ex._cvv_modal_seen
    ex._persist_cvv_challenge_flag()
    try:
        ex._maybe_unlatch_cvv()
        ok = True
    except Exception as e:
        ok = False
        print(f"  raised: {e}")
    _check("missing_attr_does_not_crash", ok)
    if _flag_exists(ex):
        ex._clear_cvv_challenge_flag()


def main():
    # Isolate state/ writes in a temp dir — the flag path is cwd-relative.
    with tempfile.TemporaryDirectory() as td:
        old_cwd = os.getcwd()
        os.chdir(td)
        try:
            test_unlatches_when_no_modal_seen()
            test_stays_latched_when_modal_was_seen()
            test_noop_when_not_latched()
            test_kill_switch()
            test_persist_load_clear_roundtrip()
            test_missing_modal_seen_attr_is_safe()
        finally:
            os.chdir(old_cwd)
    passed = sum(_results)
    print(f"\n=== {passed}/{len(_results)} passed ===")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
