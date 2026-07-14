#!/usr/bin/env python3
"""
SMOKE: per-account saved-card / checkout-compose verification (NO ORDER PLACED).

Why this exists
---------------
2026-06-30 flagged "business acct saved-card unverified" and 06-24 flagged
"alt-1 needs saved card"; the 07-12 end-to-end gum smoke PASSED but its
per-account attribution was never logged. Before the next drop we want EVERY
account proven able to: ATC (2xx) -> checkout -> payment traversal (per-account
CVV DOM fallback if prompted) -> TEST_MODE compose-and-abort -> cart cleared.
An account whose saved card is missing/broken surfaces here, not mid-drop.

SAFETY - no purchase can happen
-------------------------------
- TEST_MODE=true is set BEFORE any src import: _place_order is compose-and-abort
  (purchase_executor.py ~3945 - builds the request, never sends, synthetic
  success) and the cart is auto-cleared after each cycle.
- TARGET_API_PLACE_ORDER is popped as a second guard.
- Test item: 50270379 (Polar Ice gum) - the repo's always-in-stock TCIN. qty=1.
- Any worker whose executor.test_mode is not True is REFUSED, not run.
The only real network writes are add-to-cart (reversible) + clear-cart - the
same envelope as tests/test_qty2_atc_smoke.py, extended to all accounts via
WorkerPool.

2026-07-13 first-run fixes
--------------------------
(a) WorkerPool alone does NOT bring up the BD-IP forwarders - in production
    that is BulletproofPurchaseManager._setup_purchase_forwarders(), called
    BEFORE build_all() (SessionManager reads cfg.proxy_url at init). The
    first run skipped it: Chrome got the raw credentialed BD URL, and Chrome
    IGNORES inline proxy credentials -> 407 -> chrome-error page -> Shape JS
    never loads -> every ATC died instantly with status 0 "Failed to fetch"
    (nothing ever reached Target). _start_forwarders() below replicates the
    production step 1:1; if it cannot start, the smoke ABORTS - it never
    silently falls back to a 3-account ATC burst from the home IP.
(b) Teardown order: browsers must be closed INSIDE their live worker loops
    (session_manager.cleanup(), as tests/test_qty2_atc_smoke.py does) and the
    loops then closed deliberately. The first run just stopped the loops and
    let GC finalize them -> zendriver's asyncio_atexit loop-close hooks fired
    at GC time and mutually recursed: 3x RecursionError, ~4.6k lines of spew
    after the summary. (Earlier note blamed the builtins.print hook x log tee;
    the traceback is pure asyncio_atexit frames - the hook is innocent.)

Scope limit (found on the 07-13 PASS run)
------------------------------------------
In API mode the compose-and-abort body is only {'cart_type','channel_id'}
and is aborted BEFORE the POST; the DOM payment traversal (CVV fallback)
is bypassed. So a PASS here proves session + write-auth + Shape + ATC +
compose + cart hygiene per account — it does NOT read or validate the
saved card. Card-on-file presence/expiry is covered by the companion
tests/test_saved_card_wallet_read.py (read-only Payments-page check).

Do NOT run while the bot is up: same Chrome profiles, same 2300x forwarder
port band. A port-in-use abort here usually means exactly that.

Run:
    venv/Scripts/python.exe tests/test_saved_card_compose_smoke.py
Env:
    TARGET_SMOKE_ACCOUNTS=business,alt-1   # default: every enabled account
    TARGET_TEST_TCIN=50270379
"""
import os

# ---- SAFETY ENV - set BEFORE importing anything from src (read in __init__) --
os.environ['TEST_MODE'] = 'true'                 # compose-and-abort + auto cart-clear
os.environ.pop('TARGET_API_PLACE_ORDER', None)   # 2nd guard: never the real order path
os.environ['TARGET_FORCE_QTY_1'] = 'true'        # qty=1 - card proof, not qty proof
os.environ['TARGET_PDP_QTY_LOOKUP'] = '0'        # no PDP nav - keep it fast

import asyncio
import builtins
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_TCIN = os.environ.get('TARGET_TEST_TCIN', '50270379')
ONLY = [s.strip() for s in os.environ.get('TARGET_SMOKE_ACCOUNTS', '').split(',') if s.strip()]


def banner(msg, ch='='):
    print('\n' + ch * 72)
    print(msg)
    print(ch * 72)


def _exit_ip(url):
    """Mask a BD auth URL to its exit IP for logs - never print credentials
    (the 07-13 run wrote the full user:pass BD URL into the smoke log)."""
    if not url:
        return 'home-IP'
    m = re.search(r'-ip-([0-9.]+)[:@]', url)
    return f'BD:{m.group(1)}' if m else url


def _start_forwarders(pool):
    """Replicate BulletproofPurchaseManager._setup_purchase_forwarders() -
    the production step that runs BEFORE build_all(): one local CONNECT
    forwarder per BD-auth account, each worker's cfg.proxy_url rewritten to
    the 127.0.0.1:<port> Chrome can actually use (Chrome ignores inline
    user:pass proxy credentials). Same port band (23000 + worker_id) and same
    needs-forwarder predicate as production, imported from the manager so the
    two can't drift. Returns (fwd, loop); (None, None) when no account needs
    one. Raises on failure - the caller aborts, no home-IP fallback.
    """
    from src.proxy.local_forwarder import ForwarderPool
    from src.purchasing.bulletproof_purchase_manager import BulletproofPurchaseManager as _BPM

    needing = [w for w in pool.workers
               if w.cfg.proxy_url and _BPM._proxy_needs_forwarder(w.cfg.proxy_url)]
    if not needing:
        print('[FORWARDER] no BD-auth proxies configured - all workers on home IP / plain proxy')
        return None, None

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    threading.Thread(target=_run, daemon=True, name='SmokeForwarderLoop').start()
    ready.wait()

    fwd = ForwarderPool()
    for w in needing:
        port = _BPM.PURCHASE_FORWARDER_PORT_BASE + w.cfg.worker_id
        bd_url = w.cfg.proxy_url
        fwd.add_upstream(bd_url, port)
        w.cfg.proxy_url = f'127.0.0.1:{port}'   # what Chrome actually uses
        print(f'[FORWARDER] {w.label()} -> 127.0.0.1:{port} (exit via {_exit_ip(bd_url)})')
    asyncio.run_coroutine_threadsafe(fwd.start_all(), loop).result(timeout=15)
    print(f'[FORWARDER] pool live: {len(fwd.upstreams)} BD exit(s)')
    return fwd, loop


def _stop_forwarders(fwd, loop):
    """Mirror of BulletproofPurchaseManager._shutdown_purchase_forwarders()."""
    if fwd is not None and loop is not None:
        try:
            asyncio.run_coroutine_threadsafe(fwd.stop_all(), loop).result(timeout=5)
        except Exception:
            pass
    if loop is not None:
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass


def main() -> int:
    from src.purchasing.worker_pool import WorkerPool

    cfg = ROOT / 'config' / 'target_accounts.json'
    if not cfg.exists():
        print(f'[FATAL] {cfg} not found - multi-account smoke needs it.')
        return 2

    banner(f'SAVED-CARD COMPOSE SMOKE - TEST_MODE, NO ORDER - item {TEST_TCIN}', '#')
    print(f"  TEST_MODE={os.environ.get('TEST_MODE')}  "
          f"TARGET_API_PLACE_ORDER={os.environ.get('TARGET_API_PLACE_ORDER', '<unset>')}")
    print(f"  account filter: {ONLY or 'ALL enabled accounts'}")

    pool = WorkerPool.from_accounts_file(cfg)
    print(f"  pool: {[(c.account_id, _exit_ip(c.proxy_url)) for c in pool._configs]}")

    # Production order (bulletproof_purchase_manager.py:333-342): forwarders
    # FIRST, build_all() second - SessionManager reads cfg.proxy_url at init.
    try:
        fwd, fwd_loop = _start_forwarders(pool)
    except Exception as e:
        print(f'[FATAL] forwarder start failed: {e}')
        print('        Not falling back to the home IP - a 3-account ATC burst from one IP')
        print('        is not an envelope this smoke may exercise. Port-in-use here usually')
        print('        means the bot is running: stop it, then re-run.')
        return 2

    results = {}
    try:
        pool.build_all()

        print('\n[1/2] Launching all account browsers (fingerprint + forwarder + session)...')
        t0 = time.time()
        init = pool.ensure_all_ready(per_worker_timeout=120.0, warmup_shape_headers=True)
        print(f'      launch complete in {time.time() - t0:.1f}s')
        for lbl, r in init.items():
            print(f'        {lbl}: ok={r.get("ok")} init={r.get("init_seconds")}s '
                  f'warmup={r.get("warmup_seconds")}s err={r.get("error")}')

        print('\n[2/2] Per-account ATC -> checkout -> compose-abort (sequential)...')
        for w in pool.workers:
            acct = w.label().split('/')[-1]
            if ONLY and acct not in ONLY:
                continue
            ex = w.purchase_executor
            if ex is None:
                results[acct] = {'pass': False, 'reason': 'no executor after init'}
                continue
            if not ex.test_mode:
                results[acct] = {'pass': False,
                                 'reason': 'executor.test_mode is not True - REFUSED for safety'}
                continue

            banner(f'ACCOUNT: {acct}  ({w.label()})')
            cap = []
            real_print = builtins.print

            def hook(*a, **k):
                real_print(*a, **k)
                cap.append(' '.join(str(x) for x in a))

            builtins.print = hook
            t1 = time.time()
            try:
                res = w.run_async(ex.execute_purchase(TEST_TCIN, quantity=1)).result(timeout=150)
            except Exception as e:
                res = {'success': False, 'reason': f'exc:{type(e).__name__}:{e}'}
            finally:
                builtins.print = real_print
            wall = round(time.time() - t1, 1)

            atc_ok = any(('status: 201' in l or 'status: 200' in l
                          or 'Fetch ATC succeeded' in l) for l in cap)
            compose = any('Compose-and-abort' in l for l in cap)
            cleared = any('Cart cleared' in l for l in cap)
            cvv_lines = [l.strip() for l in cap if 'cvv' in l.lower()]
            pay_lines = [l.strip() for l in cap if '[PAYMENT]' in l]
            reason = str(res.get('reason')) if isinstance(res, dict) else str(res)
            ok = bool(isinstance(res, dict) and res.get('success')) and atc_ok and compose and cleared
            results[acct] = {
                'pass': ok, 'wall_s': wall, 'atc_2xx': atc_ok,
                'compose_abort': compose, 'cart_cleared': cleared,
                'result_reason': reason,
                'cvv_evidence': cvv_lines[-3:], 'payment_evidence': pay_lines[-4:],
            }
    finally:
        # Teardown ORDER MATTERS (see header, fix b): stop browsers inside
        # their still-live worker loops, stop the loops, then close the loops
        # deliberately so zendriver's asyncio_atexit hooks never fire at GC.
        print('\n[TEARDOWN] closing browsers...')
        for w in pool.workers:
            if w.session_manager is None or w.loop is None:
                continue
            try:
                w.run_async(w.session_manager.cleanup()).result(timeout=30)
            except Exception as e:
                print(f'  [WARN] {w.label()} cleanup: {e} - hard-killing Chrome')
                try:
                    w.session_manager.close_browser_sync()
                except Exception:
                    pass
        loops = [w.loop for w in pool.workers if w.loop is not None]
        try:
            pool.shutdown()
        except Exception as e:
            print(f'  [WARN] pool shutdown: {e}')
        for lp in loops:
            try:
                lp.close()
            except Exception:
                pass
        _stop_forwarders(fwd, fwd_loop)

    banner('SUMMARY')
    all_ok = True
    for acct, r in results.items():
        flag = 'PASS' if r.get('pass') else 'FAIL'
        if not r.get('pass'):
            all_ok = False
        print(f'  [{flag}] {acct}: {r}')
    if not results:
        print('  no accounts ran!')
        all_ok = False
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
