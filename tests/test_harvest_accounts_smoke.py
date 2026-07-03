#!/usr/bin/env python3
"""Smoke test for the multi-account harvester — NO browser, NO network.

Validates the offline-safe surface of harvest_accounts.py + account_identity.py:
  - per-account fingerprints are deterministic AND distinct (the cluster-linking fix)
  - config loading + uniqueness/collision guards
  - session-health verdicts
  - Bright-Data proxy detection falls back safely

Run: python tests/test_harvest_accounts_smoke.py    (or via pytest)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.session.account_identity import build_identity, identity_signature  # noqa: E402
import harvest_accounts as H  # noqa: E402


def test_fingerprints_deterministic_and_distinct():
    ids = ["primary", "alt-1", "alt-2", "alt-3", "alt-4", "alt-5"]
    built = {a: build_identity(a) for a in ids}
    # deterministic
    for a in ids:
        assert build_identity(a) == built[a], f"{a} fingerprint not deterministic"
    # distinct across the full linkable signature
    sigs = [identity_signature(built[a]) for a in ids]
    assert len(set(sigs)) == len(sigs), "two accounts share a fingerprint signature"
    # timezone override honored
    pinned = build_identity("alt-1", timezone="America/Chicago")
    assert pinned["timezone"] == "America/Chicago"


def test_config_loading_and_defaults(tmp_path: Path = None):
    d = Path(tempfile.mkdtemp()) if tmp_path is None else tmp_path
    cfg = d / "accounts.json"
    cfg.write_text(json.dumps({"accounts": [
        {"account_id": "primary", "username": "a@x.com", "password": "p1"},
        {"account_id": "alt-1", "username": "b@x.com", "password": "p2",
         "session_path": "target-2.json", "profile_dir": "nodriver-profile-2"},
        {"account_id": "disabled-one", "username": "c@x.com", "password": "p3", "enabled": False},
    ]}), encoding="utf-8")
    accounts = H.load_accounts(cfg)
    assert len(accounts) == 2, "disabled account should be filtered out"
    assert accounts[0]["session_path"] == "target.json"       # index-0 default
    assert accounts[0]["profile_dir"] == "nodriver-profile"
    assert accounts[1]["session_path"] == "target-2.json"


def test_duplicate_session_path_rejected():
    d = Path(tempfile.mkdtemp())
    cfg = d / "accounts.json"
    cfg.write_text(json.dumps({"accounts": [
        {"account_id": "a", "session_path": "dup.json", "profile_dir": "p1"},
        {"account_id": "b", "session_path": "dup.json", "profile_dir": "p2"},
    ]}), encoding="utf-8")
    try:
        H.load_accounts(cfg)
        raise AssertionError("expected ValueError on duplicate session_path")
    except ValueError:
        pass


def test_session_health_verdicts():
    d = Path(tempfile.mkdtemp())
    # missing
    assert not H._session_health(d / "nope.json")["exists"]
    # logged-out (no auth cookies)
    p1 = d / "out.json"
    p1.write_text(json.dumps({"cookies": [{"name": "visitorId", "value": "x"}],
                              "saved_at": "2026-06-22T00:00:00+00:00"}), encoding="utf-8")
    h1 = H._session_health(p1)
    assert h1["exists"] and not h1["auth_cookies"]
    assert "LOGGED-OUT" in H._health_verdict(h1)
    # logged-in
    p2 = d / "in.json"
    p2.write_text(json.dumps({"cookies": [
        {"name": "accessToken", "value": "x"}, {"name": "refreshToken", "value": "y"},
    ], "saved_at": "2026-06-22T00:00:00+00:00"}), encoding="utf-8")
    h2 = H._session_health(p2)
    assert "accessToken" in h2["auth_cookies"] and "refreshToken" in h2["auth_cookies"]
    assert "OK" in H._health_verdict(h2)


def test_proxy_resolution():
    assert H._resolve_proxy_arg("") is None
    assert H._resolve_proxy_arg("127.0.0.1:24000") == "--proxy-server=127.0.0.1:24000"
    # Bright Data auth-style URL must NOT be passed to Chrome (no inline auth) -> None
    assert H._resolve_proxy_arg("http://brd-customer-x:pw@brd.superproxy.io:22225") is None


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} smoke tests passed.")
    return passed == len(tests)


if __name__ == "__main__":
    raise SystemExit(0 if _run_all() else 1)
