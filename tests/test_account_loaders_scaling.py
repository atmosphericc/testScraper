#!/usr/bin/env python3
"""Scaling-correctness tests for the account loaders (2026-08-11 pre-scale review).

Covers the two HIGH/MEDIUM bugs found before scaling account count:
  * the login farm (harvest_accounts.load_accounts) and the purchase fleet
    (worker_pool._build_worker_configs_from_accounts) must derive the SAME default
    session/profile paths — otherwise disabling a MIDDLE account silently binds an
    account's login file to a different worker than reads it (races logged out).
  * duplicate proxy_url / account_id must fail LOUD, not silently correlate two
    accounts onto one BD IP / one device.

No browser, no network. Run: venv/Scripts/python.exe tests/test_account_loaders_scaling.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harvest_accounts import load_accounts  # noqa: E402
from src.purchasing.worker_pool import _build_worker_configs_from_accounts  # noqa: E402

_PASS = 0
_FAIL = 0


def check(cond: bool, label: str) -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}")


def _write(accounts: list) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"accounts": accounts}, f)
    f.close()
    return Path(f.name)


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True
    except Exception:
        return False  # only ValueError counts as the intended fail-loud


def main() -> int:
    print("[1] login farm and purchase fleet agree on DEFAULT paths when a MIDDLE account is disabled")
    cfg = _write([
        {"account_id": "primary",  "username": "p@x.com", "password": "pw", "enabled": True},
        {"account_id": "business", "username": "b@x.com", "password": "pw", "enabled": False},
        {"account_id": "alt-1",    "username": "a@x.com", "password": "pw", "enabled": True},
    ])
    farm = {a["account_id"]: (a["session_path"], a["profile_dir"]) for a in load_accounts(cfg)}
    fleet = {c.account_id: (c.session_path, c.profile_dir) for c in _build_worker_configs_from_accounts(cfg)}
    check(set(farm) == {"primary", "alt-1"}, "only enabled accounts loaded")
    check(farm == fleet, f"farm == fleet paths  (farm={farm}  fleet={fleet})")
    # The specific regression: alt-1 must be target-2.json in BOTH (enabled index 1),
    # not target-3.json in the farm (raw index 2) vs target-2.json in the fleet.
    check(farm.get("alt-1") == ("target-2.json", "nodriver-profile-2"),
          "alt-1 (after disabled middle) -> target-2.json in the farm")
    check(fleet.get("alt-1") == ("target-2.json", "nodriver-profile-2"),
          "alt-1 (after disabled middle) -> target-2.json in the fleet")

    print("[2] duplicate proxy_url fails loud (both loaders)")
    dup_proxy = [
        {"account_id": "a", "session_path": "target.json",  "profile_dir": "nodriver-profile",  "proxy_url": "http://p:1", "enabled": True},
        {"account_id": "b", "session_path": "target-2.json","profile_dir": "nodriver-profile-2","proxy_url": "http://p:1", "enabled": True},
    ]
    cfg2 = _write(dup_proxy)
    check(_raises(lambda: load_accounts(cfg2)), "load_accounts raises on duplicate proxy_url")
    check(_raises(lambda: _build_worker_configs_from_accounts(cfg2)), "worker_pool raises on duplicate proxy_url")

    print("[3] duplicate account_id fails loud in worker_pool")
    dup_id = [
        {"account_id": "same", "session_path": "target.json",  "profile_dir": "nodriver-profile",  "enabled": True},
        {"account_id": "same", "session_path": "target-2.json","profile_dir": "nodriver-profile-2","enabled": True},
    ]
    cfg3 = _write(dup_id)
    check(_raises(lambda: _build_worker_configs_from_accounts(cfg3)), "worker_pool raises on duplicate account_id")

    print("[4] EMPTY proxy_url (home IP) is NOT treated as a duplicate")
    home = [
        {"account_id": "a", "session_path": "target.json",  "profile_dir": "nodriver-profile",  "proxy_url": "", "enabled": True},
        {"account_id": "b", "session_path": "target-2.json","profile_dir": "nodriver-profile-2","proxy_url": "", "enabled": True},
    ]
    cfg4 = _write(home)
    check(not _raises(lambda: load_accounts(cfg4)), "load_accounts allows shared empty proxy_url")
    check(not _raises(lambda: _build_worker_configs_from_accounts(cfg4)), "worker_pool allows shared empty proxy_url")

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
