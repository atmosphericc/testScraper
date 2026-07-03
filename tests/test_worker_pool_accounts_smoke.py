#!/usr/bin/env python3
"""Smoke test: WorkerPool sizes itself from config/target_accounts.json.

Covers the file-driven fleet path (WorkerPool.from_accounts_file / .auto):
  - N enabled accounts -> N workers, with the harvester's path conventions
  - "enabled": false accounts are skipped (and don't shift alt numbering)
  - duplicate session_path/profile_dir is rejected (cross-pollination guard)
  - missing file -> auto() falls back to legacy from_env() sizing

No browser, no network. Run: python tests/test_worker_pool_accounts_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.worker_pool import WorkerPool  # noqa: E402


def _write(tmp: Path, accounts: list) -> Path:
    p = tmp / "target_accounts.json"
    p.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return p


def test_sizes_to_enabled_count():
    with tempfile.TemporaryDirectory() as d:
        cfg = _write(Path(d), [
            {"account_id": "primary", "username": "a@x.com", "password": "p", "enabled": True},
            {"account_id": "alt-1", "username": "b@x.com", "password": "p", "enabled": True},
        ])
        pool = WorkerPool.from_accounts_file(cfg)
        assert pool.size == 2, pool.size
        c = pool._configs
        assert c[0].session_path == "target.json" and c[0].profile_dir == "nodriver-profile"
        assert c[1].session_path == "target-2.json" and c[1].profile_dir == "nodriver-profile-2"


def test_disabled_skipped_without_gap():
    with tempfile.TemporaryDirectory() as d:
        cfg = _write(Path(d), [
            {"account_id": "primary", "enabled": True},
            {"account_id": "parked", "enabled": False},
            {"account_id": "alt-1", "enabled": True},
        ])
        pool = WorkerPool.from_accounts_file(cfg)
        assert pool.size == 2, pool.size
        # The 2nd ENABLED account becomes Worker 2 on target-2.json — the
        # disabled one in between must not consume a slot or a path number.
        assert [c.account_id for c in pool._configs] == ["primary", "alt-1"]
        assert pool._configs[1].session_path == "target-2.json"


def test_duplicate_path_rejected():
    with tempfile.TemporaryDirectory() as d:
        cfg = _write(Path(d), [
            {"account_id": "a", "session_path": "dup.json", "profile_dir": "pa", "enabled": True},
            {"account_id": "b", "session_path": "dup.json", "profile_dir": "pb", "enabled": True},
        ])
        try:
            WorkerPool.from_accounts_file(cfg)
        except ValueError as e:
            assert "Duplicate" in str(e), e
            return
        raise AssertionError("expected ValueError on duplicate session_path")


def test_auto_falls_back_when_file_missing():
    # No accounts file -> auto() must use TARGET_WORKER_POOL_SIZE (default 1).
    os.environ.pop("TARGET_WORKER_POOL_SIZE", None)
    # Point at a nonexistent path by temporarily clearing the module constant.
    import src.purchasing.worker_pool as wp
    orig = wp.ACCOUNTS_CONFIG
    try:
        wp.ACCOUNTS_CONFIG = ROOT / "config" / "__does_not_exist__.json"
        pool = WorkerPool.auto()
        assert pool.size == 1, pool.size
        assert pool._configs[0].account_id == "primary"
    finally:
        wp.ACCOUNTS_CONFIG = orig


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
