#!/usr/bin/env python3
"""Offline test for the 2026-10-22 `proxy_ref` account field (src/purchasing/worker_pool.py). No browser, no network,
no bot: it builds worker configs from temp accounts / proxyIps files. Guarantees (v2 after verifier PR-1022):
absent field = unchanged configs; "resi:<i>" / "reserve:<i>" / "proxies:<i>" resolve to the list entry (0-based);
an explicit proxy_url wins; a bad ref is loud, leaves the account on the home IP and never collapses the fleet;
two accounts on one ref keep the fleet (the second boots on home, loudly); a URL mis-pasted into proxy_ref is never
echoed; https://, bare host:port, and credentials the forwarder cannot parse ('@', '%', ':' in the user) are refused
before any Chrome launches; error text never carries the secret; WorkerPool.auto() keeps 3 accounts in every case.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.purchasing import worker_pool as WP  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + ("" if cond else f"  {detail}"))
    RESULTS.append((name, bool(cond)))


SECRET = "s3cr3t-pw"
URL_R = f"http://brd-customer-x-zone-resi-session-a1:{SECRET}@brd.superproxy.io:22225"
URL_V = f"http://brd-customer-x-zone-isp-ip-168.158.32.64:{SECRET}@brd.superproxy.io:33335"
URL_P = f"http://brd-customer-x-zone-isp-ip-31.105.1.1:{SECRET}@brd.superproxy.io:33335"
URL_DECODO = f"http://user-sp1-sessionduration-30:{SECRET}@gate.decodo.com:7000"
BAD = {
    "https": f"https://u:{SECRET}@gate.decodo.com:7000",
    "bare_hostport": "gate.decodo.com:7000",
    "at_in_password": f"http://u:p@ss@gate.decodo.com:7000",
    "pct_in_password": f"http://u:p%40ss@gate.decodo.com:7000",
    "colon_in_user": f"http://u:x:{SECRET}@gate.decodo.com:7000",
    "bad_port": f"http://u:{SECRET}@gate.decodo.com:abc",
}


def build(accounts, proxies, capture=False, use_auto=False):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        acc = td / "target_accounts.json"
        acc.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
        pcfg = td / "proxyIps.json"
        pcfg.write_text(json.dumps(proxies), encoding="utf-8")
        old_p, old_a = WP.PROXY_CFG, WP.ACCOUNTS_CONFIG
        WP.PROXY_CFG = pcfg
        WP.ACCOUNTS_CONFIG = acc
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                if use_auto:
                    pool = WP.WorkerPool.auto()
                    cfgs = list(pool._configs)
                else:
                    cfgs = WP._build_worker_configs_from_accounts(acc)
        finally:
            WP.PROXY_CFG, WP.ACCOUNTS_CONFIG = old_p, old_a
        return (cfgs, buf.getvalue()) if capture else cfgs


A = lambda i, **kw: dict({"account_id": f"acct{i}", "enabled": True, "session_path": f"t{i}.json", "profile_dir": f"p{i}"}, **kw)  # noqa: E731
PROX = {"proxies": [URL_P], "reserve_proxies": ["x", URL_V], "residential_proxies": [URL_R, URL_DECODO]}
os.environ.pop("TARGET_WORKER_POOL_SIZE", None)

# 1. absent field: unchanged (home IP)
c = build([A(1), A(2)], PROX)
check("absent_ref_unchanged", [x.proxy_url for x in c] == [None, None], [x.proxy_url for x in c])

# 2. each ref kind resolves, 0-based; a Decodo-shaped URL is accepted
c, out = build([A(1, proxy_ref="resi:0"), A(2, proxy_ref="reserve:1"), A(3, proxy_ref="proxies:0"), A(4, proxy_ref="resi:1")], PROX, capture=True)
check("resi_reserve_proxies_resolve", [x.proxy_url for x in c] == [URL_R, URL_V, URL_P, URL_DECODO], [x.proxy_url for x in c])
check("ok_lines_masked", "[PROXY_REF] acct1: resi:0 -> brd.superproxy.io:22225" in out and "acct4: resi:1 -> gate.decodo.com:7000" in out
      and SECRET not in out, out)

# 3. explicit proxy_url wins over a ref
c, out = build([A(1, proxy_ref="resi:0", proxy_url="127.0.0.1:23099")], PROX, capture=True)
check("explicit_url_wins", c[0].proxy_url == "127.0.0.1:23099" and "explicit proxy_url wins" in out, (c[0].proxy_url, out))

# 4. bad refs: loud, home IP, fleet intact, no secret in the message ("reserve:0" is the placeholder 'x' = not a URL)
for bad in ("resi:5", "reserve:0", "junk", "resi:x"):
    c, out = build([A(1, proxy_ref=bad), A(2)], PROX, capture=True)
    check(f"bad_ref_{bad}_is_loud_and_home", len(c) == 2 and c[0].proxy_url is None and "[PROXY_REF] [ERROR] acct1" in out
          and "HOME IP" in out and SECRET not in out, (len(c), c[0].proxy_url, out[-200:]))

# 5. a URL mis-pasted INTO proxy_ref is never echoed
c, out = build([A(1, proxy_ref=URL_R)], PROX, capture=True)
check("url_in_ref_not_echoed", c[0].proxy_url is None and "<not a ref>" in out and SECRET not in out and "brd.superproxy.io" not in out, out[-240:])

# 6. shapes the forwarder cannot route are refused by the resolver, loudly, without the secret
for name, url in BAD.items():
    c, out = build([A(1, proxy_ref="resi:0")], {"residential_proxies": [url]}, capture=True)
    check(f"refused_{name}", c[0].proxy_url is None and "[PROXY_REF] [ERROR]" in out and SECRET not in out and "gate.decodo.com" not in out, out[-240:])

# 7. two accounts on one ref: fleet intact, the second boots on home, loudly
c, out = build([A(1, proxy_ref="resi:0"), A(2, proxy_ref="resi:0"), A(3)], PROX, capture=True)
check("duplicate_ref_keeps_fleet", len(c) == 3 and c[0].proxy_url == URL_R and c[1].proxy_url is None and c[2].proxy_url is None
      and "already used by another account" in out and SECRET not in out, ([x.proxy_url for x in c], out[-240:]))

# 8. WorkerPool.auto() never falls back to the legacy pool because of a ref (bad, duplicate, or missing cfg)
for label, accts, prox in (("bad", [A(1, proxy_ref="resi:9"), A(2), A(3)], PROX),
                           ("dup", [A(1, proxy_ref="resi:0"), A(2, proxy_ref="resi:0"), A(3)], PROX),
                           ("nocfg", [A(1, proxy_ref="resi:0"), A(2), A(3)], {})):
    c, out = build(accts, prox, capture=True, use_auto=True)
    check(f"auto_keeps_three_{label}", len(c) == 3 and "falling back" not in out and [x.account_id for x in c] == ["acct1", "acct2", "acct3"],
          ([x.account_id for x in c], out[-200:]))

# 9. the resolver's own error text never carries the secret
try:
    WP._resolve_proxy_ref("resi:9", cfg_path=None)
except ValueError as e:
    check("resolver_error_no_secret", SECRET not in str(e), str(e))

# 10. the existing duplicate guard still raises for two EXPLICIT identical proxy_url values, with the value masked
try:
    build([A(1, proxy_url=URL_R), A(2, proxy_url=URL_R)], PROX)
    check("explicit_duplicate_raises_masked", False, "no exception")
except ValueError as e:
    check("explicit_duplicate_raises_masked", "Duplicate proxy_url" in str(e) and SECRET not in str(e), str(e))

n_total = len(RESULTS)
n_pass = sum(1 for _, ok in RESULTS if ok)
print(f"\n=== {n_pass}/{n_total} passed ===")
fails = [n for n, ok in RESULTS if not ok]
if fails:
    print("  FAILED: " + ", ".join(fails))
sys.exit(1 if fails else 0)
