#!/usr/bin/env python3
"""LIVE verification of the multi-account session architecture (NO purchase).

Exercises the real path the bot uses:
  1. Build the WorkerPool from config/target_accounts.json (one worker/account).
  2. Launch every account's browser concurrently (ensure_all_ready) — applies the
     per-account fingerprint + proxy, loads the harvested session.
  3. Validate each account is GENUINELY logged in via SessionManager.ensure_logged_in
     (nav-refresh -> restart -> credential re-login ladder — the Sentinel's check).
  4. Print a per-account PASS/FAIL summary.

Launches real headful Chrome(s) and hits target.com (account page only). Safe:
no add-to-cart, no checkout. Run present: python verify_multi_account_live.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.purchasing.worker_pool import WorkerPool


def main() -> int:
    cfg = ROOT / "config" / "target_accounts.json"
    if not cfg.exists():
        print(f"[FATAL] {cfg} not found.")
        return 2

    print("=" * 64)
    print("LIVE MULTI-ACCOUNT VERIFY (no purchase)")
    print("=" * 64)

    pool = WorkerPool.from_accounts_file(cfg)
    print(f"Pool: {pool.size} account(s) -> "
          f"{[(c.account_id, c.session_path, c.proxy_url or 'home-IP') for c in pool._configs]}\n")

    pool.build_all()

    print("[1/2] Launching all account browsers concurrently...")
    t0 = time.time()
    init = pool.ensure_all_ready(per_worker_timeout=120.0, warmup_shape_headers=True)
    print(f"      launch complete in {time.time()-t0:.1f}s")
    for lbl, r in init.items():
        print(f"        {lbl}: ok={r.get('ok')} init={r.get('init_seconds')}s "
              f"warmup={r.get('warmup_seconds')}s err={r.get('error')}")

    print("\n[2/2] READ-ONLY login status (navigate /account, screenshot)...")
    results = {}
    for w in pool.workers:
        sm = w.session_manager
        label = w.label()
        try:
            ok = w.run_async(sm.validate_logged_in()).result(timeout=60)
        except Exception as e:
            ok = False
            print(f"        {label}: EXCEPTION {type(e).__name__}: {e}")
        results[label] = ok
        # Screenshot + final URL so the actual state is visible / undeniable.
        try:
            async def _shot(_sm=sm, _label=w.cfg.account_id):
                tab = await _sm.get_page()
                url = getattr(tab, 'url', '') or ''
                path = str(ROOT / f"login_state_{_label}.png")
                try:
                    await tab.save_screenshot(path)
                except Exception:
                    path = "(screenshot failed)"
                return url, path
            url, shot = w.run_async(_shot()).result(timeout=30)
            print(f"        {label}: logged_in={ok} | url={url} | shot={shot}")
        except Exception as e:
            print(f"        {label}: logged_in={ok} | (status detail failed: {e})")

    # [3/3] EGRESS IP — prove each account exits its OWN proxy IP (the 429 throttle-key
    # measurement docs/MULTI_ACCOUNT.md:180 asks for). Fetches Bright Data's own IP echo
    # THROUGH each account's browser, so the reported IP is that browser's real exit.
    print("\n[3/3] EGRESS IP per account (fetch lumtest.com/myip.json through each browser)...")
    egress = {}
    for w in pool.workers:
        label = w.label()
        try:
            async def _egress(_sm=w.session_manager):
                tab = await _sm.get_page()
                # Top-level NAV (not fetch): target.com's CSP blocks cross-origin
                # fetch, but navigating to the echo endpoint exits via the browser's
                # own proxy, so the reported IP is that account's real exit.
                await tab.get('https://lumtest.com/myip.json')
                await tab
                return await tab.evaluate("document.body ? document.body.innerText : ''")
            raw = w.run_async(_egress()).result(timeout=45)
            try:
                import json as _json
                d = _json.loads(raw)
                ip = d.get('ip', '?')
                geo = d.get('geo') or {}
                asn = d.get('asn') or {}
                loc = f"{geo.get('city','?')}, {geo.get('region','?')} {d.get('country','?')}"
                print(f"        {label}: exit_ip={ip}  ({loc})  asn={asn.get('org') or asn.get('asnum','?')}")
                egress[label] = ip
            except Exception:
                print(f"        {label}: raw={str(raw)[:180]}")
                egress[label] = None
        except Exception as e:
            print(f"        {label}: EGRESS CHECK FAILED {type(e).__name__}: {e}")
            egress[label] = None
    _ips = [v for v in egress.values() if v]
    _distinct = len(set(_ips)) == len(pool.workers) and len(_ips) == len(pool.workers)
    print(f"\n  EGRESS DISTINCT: {'✅ every account exits a UNIQUE IP' if _distinct else '❌ shared/failed exit IPs → ' + str(egress)}")

    print("\n" + "=" * 64)
    print("RESULT")
    print("=" * 64)
    all_ok = True
    for w in pool.workers:
        label = w.label()
        ok = results.get(label, False)
        all_ok = all_ok and ok
        print(f"  {label:22s}: {'✅ LOGGED IN' if ok else '❌ NOT LOGGED IN'}")
    print("=" * 64)
    print("ALL ACCOUNTS READY ✅" if all_ok else "SOME ACCOUNTS NEED ATTENTION ❌ (run: python harvest_accounts.py --manual --account <id>)")

    try:
        pool.shutdown()
    except Exception:
        pass
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
