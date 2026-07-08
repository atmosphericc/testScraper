#!/usr/bin/env python3
"""LIVE proof of the 2026-07-07 token keep-fresh fix (NO purchase).

For every account in config/target_accounts.json:
  1. Launch its real browser + load the harvested session.
  2. Read the live accessToken JWT (member / ttl).
  3. Fire the exact carts write-probe an ATC uses — 401 == the morning's
     failure, anything else == write-auth alive.
  4. FORCE a keep-fresh re-mint (refresh_access_token, nav allowed) to prove
     the sentinel can renew a token on a live login-session BEFORE it dies —
     the real production scenario (fresh-at-boot token renewed mid-run).
  5. Re-probe to confirm the freshly minted token still writes.

Exit 0 iff every logged-in account ends green on write-auth. Accounts whose
login-session is dead (need a manual relogin) are reported, not fatal.

Run: python test_token_remint_live.py
"""
from __future__ import annotations

import sys
import time
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.purchasing.worker_pool import WorkerPool

WRITE_PROBE_JS = """(async () => {
    try {
        const resp = await fetch('https://carts.target.com/web_checkouts/v1/cart_items?field_groups=CART,CART_ITEMS,SUMMARY', {
            method: 'POST', credentials: 'include',
            headers: {'Content-Type':'application/json','Accept':'application/json','Origin':'https://www.target.com'},
            body: JSON.stringify({cart_item:{tcin:'81926151',quantity:1,item_channel_id:'10'},
                                  cart_type:'REGULAR',channel_id:'10',shopping_context:'DIGITAL'})
        });
        return resp.status;
    } catch(e) { return 0; }
})()"""


async def _probe(tab) -> int:
    try:
        s = await asyncio.wait_for(tab.evaluate(WRITE_PROBE_JS, await_promise=True), timeout=15.0)
        return int(s)
    except Exception:
        return -1


def main() -> int:
    cfg = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "config/target_accounts.json")
    if not cfg.exists():
        print(f"[FATAL] {cfg} not found.")
        return 2

    print("=" * 68)
    print("LIVE TOKEN KEEP-FRESH PROOF (no purchase)")
    print("=" * 68)

    pool = WorkerPool.from_accounts_file(cfg)
    pool.build_all()
    print(f"Pool: {pool.size} account(s)\n")
    print("[launch] bringing all account browsers up...")
    pool.ensure_all_ready(per_worker_timeout=120.0, warmup_shape_headers=False)

    rows = {}
    for w in pool.workers:
        label = w.label()
        sm = w.session_manager

        async def _run(_sm=sm):
            tab = await _sm.get_page()
            before = await _sm.get_access_token_status(tab)
            probe_before = await _probe(tab)
            # Force the keep-fresh mint even if the token is still healthy —
            # this is what proves the renewal mechanism works on a LIVE
            # login-session (the sentinel calls this every 5 min in prod).
            t0 = time.time()
            minted = await _sm.refresh_access_token(tab, allow_nav=True)
            mint_s = time.time() - t0
            after = await _sm.get_access_token_status(tab)
            probe_after = await _probe(tab)
            return before, probe_before, minted, mint_s, after, probe_after

        try:
            before, pb, minted, mint_s, after, pa = w.run_async(_run()).result(timeout=180)
            # Healthy result = a member token that a carts WRITE does not 401.
            green = bool(after.get('member')) and after.get('ttl_s', -1) > 600 and pa not in (401, 0, -1)
            rows[label] = green
            print(f"\n  {label}")
            print(f"    before : member={before.get('member')} ttl={before.get('ttl_s',-1)/60:.0f}m  write_probe={pb}")
            print(f"    re-mint: minted={minted} in {mint_s:.1f}s")
            print(f"    after  : member={after.get('member')} ttl={after.get('ttl_s',-1)/60:.0f}m  write_probe={pa}")
            print(f"    verdict: {'✅ WRITE-AUTH GREEN' if green else '❌ needs manual relogin (login-session dead)'}")
        except Exception as e:
            rows[label] = False
            print(f"\n  {label}: EXCEPTION {type(e).__name__}: {e}")

    print("\n" + "=" * 68)
    print("RESULT (write-probe legend: 201/400/404/422 = auth-alive, 401 = DEAD)")
    print("=" * 68)
    all_ok = True
    for w in pool.workers:
        label = w.label()
        ok = rows.get(label, False)
        all_ok = all_ok and ok
        print(f"  {label:22s}: {'✅ GREEN' if ok else '❌ NEEDS MANUAL RELOGIN'}")
    print("=" * 68)

    try:
        pool.shutdown()
    except Exception:
        pass
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
