#!/usr/bin/env python3
"""Smoke test: per-account exit-proxy wiring on the purchase path.

Exercises BulletproofPurchaseManager._setup_purchase_forwarders without a real
manager (bypasses __init__ via __new__) and without network:
  - a worker with a BD auth proxy_url gets a live local forwarder; its
    cfg.proxy_url is rewritten to 127.0.0.1:<port> (Chrome-usable)
  - a worker with no proxy_url stays on the home IP (proxy_url None)
  - a worker with a plain host:port proxy passes through untouched
  - no worker needing a forwarder => no forwarder thread started (no-op)
  - shutdown tears the pool + loop down cleanly

Binds loopback ports in the 23000 band briefly; no outbound connection is made.
Run: python tests/test_purchase_proxy_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager as B  # noqa: E402


class _Cfg:
    def __init__(self, wid, proxy):
        self.worker_id = wid
        self.account_id = f"acct-{wid}"
        self.proxy_url = proxy


class _W:
    def __init__(self, wid, proxy):
        self.cfg = _Cfg(wid, proxy)

    def label(self):
        return f"W{self.cfg.worker_id}/{self.cfg.account_id}"


class _Pool:
    def __init__(self, workers):
        self._workers = workers

    @property
    def workers(self):
        return list(self._workers)


def _mgr(workers):
    m = B.__new__(B)            # skip heavy __init__
    m.worker_pool = _Pool(workers)
    m._forwarder_pool = None
    m._forwarder_loop = None
    m._forwarder_thread = None
    return m


def test_bd_url_gets_local_forwarder():
    bd = "http://brd-customer-c:pw@brd.superproxy.io:33335"
    w = _W(2, bd)
    m = _mgr([_W(1, None), w, _W(3, "127.0.0.1:9999")])
    m._setup_purchase_forwarders()
    try:
        # BD worker rewritten to the local forwarder port (base 23000 + worker_id).
        assert w.cfg.proxy_url == f"127.0.0.1:{B.PURCHASE_FORWARDER_PORT_BASE + 2}", w.cfg.proxy_url
        assert m._forwarder_pool is not None
        assert len(m._forwarder_pool.upstreams) == 1, m._forwarder_pool.upstreams
        # The forwarder bound the expected port.
        assert (B.PURCHASE_FORWARDER_PORT_BASE + 2) in m._forwarder_pool.upstreams
    finally:
        m._shutdown_purchase_forwarders()
    assert m._forwarder_pool is None


def test_no_proxy_and_plain_proxy_untouched():
    w_home = _W(1, None)
    w_plain = _W(3, "127.0.0.1:9999")
    m = _mgr([w_home, w_plain])
    m._setup_purchase_forwarders()
    try:
        assert w_home.cfg.proxy_url is None
        assert w_plain.cfg.proxy_url == "127.0.0.1:9999"   # pass-through, no rewrite
        # No worker needed a forwarder => none started.
        assert m._forwarder_pool is None
        assert m._forwarder_thread is None
    finally:
        m._shutdown_purchase_forwarders()


def test_multiple_bd_accounts_distinct_ports():
    bd = "http://brd-customer-c:pw@brd.superproxy.io:33335"
    ws = [_W(1, bd), _W(2, bd), _W(3, bd)]
    m = _mgr(ws)
    m._setup_purchase_forwarders()
    try:
        ports = {w.cfg.proxy_url for w in ws}
        assert len(ports) == 3, ports                       # distinct local ports
        assert len(m._forwarder_pool.upstreams) == 3
    finally:
        m._shutdown_purchase_forwarders()


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  FAIL {t.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} smoke tests passed.")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(_run())
