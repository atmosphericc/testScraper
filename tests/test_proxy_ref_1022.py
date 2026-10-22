#!/usr/bin/env python3
"""Offline test for the 2026-10-22 `proxy_ref` account field (src/purchasing/worker_pool.py). No browser, no network,
no bot: it builds worker configs from temp accounts / proxyIps files. Guarantees: absent field = unchanged configs;
"resi:<i>" / "reserve:<i>" / "proxies:<i>" resolve to the list entry (0-based); an explicit proxy_url wins; a bad ref
is loud, leaves the account on the home IP and never collapses the fleet; two accounts on one ref trip the existing
duplicate-proxy guard; error text never carries the secret.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.purchasing import worker_pool as WP  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


SECRET = "s3cr3t-pw"
URL_R = f"http://brd-customer-x-zone-resi-session-a1:{SECRET}@brd.superproxy.io:22225"
URL_V = f"http://brd-customer-x-zone-isp-ip-168.158.32.64:{SECRET}@brd.superproxy.io:33335"
URL_P = f"http://brd-customer-x-zone-isp-ip-31.105.1.1:{SECRET}@brd.superproxy.io:33335"


def build(accounts, proxies, capture=False):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        acc = td / "target_accounts.json"
        acc.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
        pcfg = td / "proxyIps.json"
        pcfg.write_text(json.dumps(proxies), encoding="utf-8")
        old = WP.PROXY_CFG
        WP.PROXY_CFG = pcfg
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cfgs = WP._build_worker_configs_from_accounts(acc)
        finally:
            WP.PROXY_CFG = old
        return (cfgs, buf.getvalue()) if capture else cfgs


A = lambda i, **kw: dict({"account_id": f"acct{i}", "enabled": True, "session_path": f"t{i}.json", "profile_dir": f"p{i}"}, **kw)  # noqa: E731
PROX = {"proxies": [URL_P], "reserve_proxies": ["x", URL_V], "residential_proxies": [URL_R]}

# 1. absent field: unchanged (home IP)
c = build([A(1), A(2)], PROX)
check("absent_ref_unchanged", [x.proxy_url for x in c] == [None, None], [x.proxy_url for x in c])

# 2. each ref kind resolves, 0-based
c, out = build([A(1, proxy_ref="resi:0"), A(2, proxy_ref="reserve:1"), A(3, proxy_ref="proxies:0")], PROX, capture=True)
check("resi_reserve_proxies_resolve", [x.proxy_url for x in c] == [URL_R, URL_V, URL_P], [x.proxy_url for x in c])
check("ok_lines_masked", "[PROXY_REF] acct1: resi:0 -> brd.superproxy.io:22225" in out and SECRET not in out, out)

# 3. explicit proxy_url wins over a ref
c, out = build([A(1, proxy_ref="resi:0", proxy_url="127.0.0.1:23099")], PROX, capture=True)
check("explicit_url_wins", c[0].proxy_url == "127.0.0.1:23099" and "explicit proxy_url wins" in out, (c[0].proxy_url, out))

# 4. bad refs: loud, home IP, fleet intact, no secret in the message
for bad in ("resi:5", "reserve:0", "junk", "resi:x"):
    c, out = build([A(1, proxy_ref=bad), A(2)], PROX, capture=True)
    check(f"bad_ref_{bad}_is_loud_and_home", len(c) == 2 and c[0].proxy_url is None and "[PROXY_REF] [ERROR] acct1" in out
          and "HOME IP" in out and SECRET not in out, (len(c), c[0].proxy_url, out[-200:]))

# 5. missing proxyIps.json: loud, home IP
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    acc = td / "target_accounts.json"
    acc.write_text(json.dumps({"accounts": [A(1, proxy_ref="resi:0")]}), encoding="utf-8")
    old = WP.PROXY_CFG
    WP.PROXY_CFG = td / "missing.json"
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            c = WP._build_worker_configs_from_accounts(acc)
    finally:
        WP.PROXY_CFG = old
    check("missing_cfg_is_loud_and_home", c[0].proxy_url is None and "cannot read missing.json" in buf.getvalue(), buf.getvalue()[-200:])

# 6. two accounts on one ref -> the existing duplicate-proxy guard raises
try:
    build([A(1, proxy_ref="resi:0"), A(2, proxy_ref="resi:0")], PROX)
    check("duplicate_ref_raises", False, "no exception")
except ValueError as e:
    check("duplicate_ref_raises", "Duplicate proxy_url" in str(e) and SECRET not in str(e), str(e))

# 7. the resolver's own error text never carries the secret
try:
    WP._resolve_proxy_ref("resi:9", cfg_path=None)
except ValueError as e:
    check("resolver_error_no_secret", SECRET not in str(e), str(e))

n_total = 7 + 4 + 2  # named checks above
n_pass = n_total - len(FAILS)
print(f"\n=== {n_pass}/{n_total} passed ===")
if FAILS:
    print("  FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
