#!/usr/bin/env python3
"""
SMOKE TEST: Can the bot add QTY=2 to cart (instead of 1), and is qty=2 as
fast as qty=1?

Why this exists
---------------
Every purchase the bot has ever made fired qty=1 (744/744 in the logs). We want
to (a) prove the qty=2 add-to-cart path works against a real Target item, and
(b) measure whether asking for 2 costs any speed vs 1. Speed is the priority.

SAFETY — no purchase can happen
-------------------------------
Runs in TEST_MODE. In test mode the place-order POST is compose-and-abort
(purchase_executor.py:3700 — it builds the request but never sends it and
returns synthetic success), and the cart is auto-cleared after each run. The
only real network writes are: add-to-cart (reversible) + clear-cart. We also
pop TARGET_API_PLACE_ORDER as a second guard. This script does NOT edit any
production code.

Test item: 50270379 (Polar Ice gum) — the repo's known-stable always-in-stock
TCIN. The gum is unlimited, so qty=2 SHOULD be accepted; that proves the
mechanism + speed. (Hyped SKUs are OOS/limited and can only be checked during a
real drop — see the cart-test note in the report.)

Run:
    venv/Scripts/python.exe tests/test_qty2_atc_smoke.py
Env:
    TARGET_TEST_TCIN=50270379   # override the test item
"""
import os

# ---- SAFETY ENV — set BEFORE importing the executor (read in __init__) -------
os.environ['TEST_MODE'] = 'true'                 # compose-and-abort + auto cart-clear
os.environ.pop('TARGET_API_PLACE_ORDER', None)   # 2nd guard: never the real order path
os.environ['TARGET_FORCE_QTY_1'] = 'false'       # allow qty>1 through
os.environ['TARGET_PDP_QTY_LOOKUP'] = '0'        # no PDP nav — keep it fast/representative
os.environ.setdefault('TARGET_QTY_CEILING', '2')

import asyncio
import sys
import time
import json
import re
import builtins
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_TCIN = os.environ.get('TARGET_TEST_TCIN', '50270379')


def banner(msg, char='='):
    print('\n' + char * 72)
    print(msg)
    print(char * 72)


_real_print = builtins.print
_cap = {'lines': []}


def _hook(*a, **k):
    _real_print(*a, **k)
    line = ' '.join(str(x) for x in a)
    if any(s in line for s in (
        'Firing ATC fetch qty=', 'ATC fetch status', 'ATC fetch:',
        'Fetch ATC succeeded', 'rejected qty=', 'per-customer limit',
        'Self-heal', 'EXCEEDED',
    )):
        _cap['lines'].append(line)


def _t_of(line):
    m = re.search(r't=([0-9.]+)s', line)
    return float(m.group(1)) if m else None


async def _read_cart_qty(executor, tab):
    """Authed GET of the cart, reusing warm Shape headers; return max quantity seen."""
    try:
        hdrs = {k: v for k, v in (executor._cached_cart_headers or {}).items()
                if k.lower() not in ('cookie', 'referer')}
        hdrs['x-application-name'] = 'web'
        hdrs['Accept'] = 'application/json'
        hdrs_js = json.dumps(hdrs)
        js = ("(async()=>{try{const r=await fetch("
              "'https://carts.target.com/web_checkouts/v1/cart_views?cart_type=REGULAR"
              "&field_groups=CART,CART_ITEMS',{credentials:'include',headers:" + hdrs_js +
              "});return await r.text();}catch(e){return 'ERR:'+e;}})()")
        txt = await tab.evaluate(js, await_promise=True)
        qs = re.findall(r'"quantity"\s*:\s*(\d+)', txt or '')
        return (max(int(x) for x in qs) if qs else None), (txt or '')[:200]
    except Exception as e:
        return None, f'readback_err:{e}'


async def run_one(executor, sm, qty):
    """Warm, fire the real prod ATC at `qty` in TEST_MODE, capture timing + cart qty."""
    _cap['lines'] = []
    # Fresh Shape tokens before each run (single-use).
    await executor.warm_shape_headers(force_fresh=True)

    cart = {'qty': None, 'snip': ''}
    orig_clear = executor._clear_cart

    async def clear_with_readback(tab):
        cart['qty'], cart['snip'] = await _read_cart_qty(executor, tab)
        return await orig_clear(tab)

    executor._clear_cart = clear_with_readback
    t0 = time.time()
    builtins.print = _hook
    try:
        result = await asyncio.wait_for(
            executor.execute_purchase(TEST_TCIN, quantity=qty), timeout=90)
    except asyncio.TimeoutError:
        result = {'success': False, 'reason': 'test_timeout'}
    except Exception as e:
        result = {'success': False, 'reason': f'exc:{type(e).__name__}:{e}'}
    finally:
        builtins.print = _real_print
        executor._clear_cart = orig_clear
    wall = round(time.time() - t0, 2)

    fired = next((l for l in _cap['lines'] if 'Firing ATC fetch qty=' in l), '')
    status = next((l for l in _cap['lines']
                   if 'ATC fetch status' in l or 'ATC fetch:' in l or 'Fetch ATC succeeded' in l), '')
    tf, ts = _t_of(fired), _t_of(status)
    atc_ms = round((ts - tf) * 1000) if (tf is not None and ts is not None) else None
    accepted_201 = any(('status: 201' in l or 'status: 200' in l or 'Fetch ATC succeeded' in l)
                       for l in _cap['lines'])
    limited = any(('rejected qty=' in l or 'per-customer limit' in l
                   or 'Self-heal' in l or 'EXCEEDED' in l) for l in _cap['lines'])
    return {
        'qty_requested': qty,
        'cart_qty_before_clear': cart['qty'],
        'atc_roundtrip_ms': atc_ms,
        'wall_s': wall,
        'atc_ok_2xx': accepted_201,
        'limit_rejection_seen': limited,
        'fire_line': fired.strip(),
        'status_line': status.strip(),
        'result_reason': str(result.get('reason')) if isinstance(result, dict) else str(result),
    }


async def amain():
    banner('QTY=2 ATC SMOKE TEST  —  TEST_MODE, NO ORDER PLACED', '#')
    print(f"  Test item (always-in-stock): {TEST_TCIN} (Polar Ice gum)")
    print(f"  TEST_MODE={os.environ.get('TEST_MODE')}  "
          f"TARGET_API_PLACE_ORDER={os.environ.get('TARGET_API_PLACE_ORDER', '<unset>')}")

    # Pre-flight stock (raw RedSky, no browser)
    try:
        from src.monitoring.stock_monitor import StockMonitor
        mon = StockMonitor()
        mon.get_config = lambda: {"products": [{"tcin": TEST_TCIN, "name": "gum", "enabled": True}]}
        r = mon.check_stock() or {}
        e = r.get(TEST_TCIN, {})
        print(f"  Pre-flight stock: in_stock={e.get('in_stock')} "
              f"detail={e.get('status_detail') or e.get('availability_status')}")
    except Exception as ex:
        print(f"  Pre-flight stock check skipped: {ex!r}")

    from src.session.session_manager import SessionManager
    from src.session.purchase_executor import PurchaseExecutor

    banner('Launching browser (nodriver-profile)…')
    sm = SessionManager(session_path="target.json")
    if not await sm.initialize():
        print("FAIL(3): SessionManager.initialize() returned False")
        return 3
    executor = PurchaseExecutor(sm)
    print(f"  executor.test_mode = {executor.test_mode}  (MUST be True)")
    if not executor.test_mode:
        print("ABORT(3): test_mode is not True — refusing to run for safety.")
        try:
            await sm.cleanup()
        except Exception:
            pass
        return 3

    # Best-effort login signal
    for name in ('is_logged_in', 'check_login', 'verify_login', 'check_target_login'):
        fn = getattr(sm, name, None)
        if fn:
            try:
                res = fn()
                if asyncio.iscoroutine(res):
                    res = await res
                print(f"  login signal ({name}): {res}")
                break
            except Exception as ex:
                print(f"  login signal ({name}) error: {ex!r}")

    runs = []
    try:
        # Throwaway warm-up: the FIRST add-to-cart of a cold session commonly eats
        # one Shape-token 401 and auto-recovers (unrelated to quantity). Absorb it
        # here so the measured runs below are a clean apples-to-apples speed compare.
        banner("WARM-UP run (discarded)")
        _ = await run_one(executor, sm, 1)
        for q in (1, 2):
            banner(f"RUN qty={q}")
            runs.append(await run_one(executor, sm, q))
    finally:
        # Belt-and-suspenders final cart clear
        try:
            tab = None
            for nm in ('get_main_tab', 'get_tab', 'main_tab'):
                g = getattr(sm, nm, None)
                if g:
                    tab = g() if not asyncio.iscoroutinefunction(g) else await g()
                    if asyncio.iscoroutine(tab):
                        tab = await tab
                    break
            if tab is not None:
                await executor._clear_cart(tab)
                print("\n[cleanup] final cart clear done")
        except Exception as ex:
            print(f"\n[cleanup] final clear skipped: {ex!r}")
        try:
            await sm.cleanup()
        except Exception:
            pass

    banner('RESULTS', '#')
    for r in runs:
        print(json.dumps(r, indent=2, default=str))

    r1 = next((r for r in runs if r['qty_requested'] == 1), {})
    r2 = next((r for r in runs if r['qty_requested'] == 2), {})
    print("\n────────────────────────── SUMMARY ──────────────────────────")
    print(f"qty=1 : cart_qty={r1.get('cart_qty_before_clear')}  "
          f"ATC={r1.get('atc_roundtrip_ms')}ms  wall={r1.get('wall_s')}s  2xx={r1.get('atc_ok_2xx')}")
    print(f"qty=2 : cart_qty={r2.get('cart_qty_before_clear')}  "
          f"ATC={r2.get('atc_roundtrip_ms')}ms  wall={r2.get('wall_s')}s  2xx={r2.get('atc_ok_2xx')}  "
          f"limited={r2.get('limit_rejection_seen')}")
    a1, a2 = r1.get('atc_roundtrip_ms'), r2.get('atc_roundtrip_ms')
    if isinstance(a1, int) and isinstance(a2, int):
        print(f"speed  : qty=2 ATC is {a2 - a1:+d}ms vs qty=1  "
              f"({'same speed' if abs(a2 - a1) < 150 else 'NOTE: difference >150ms'})")
    cq2 = r2.get('cart_qty_before_clear')
    if cq2 == 2:
        print("verdict: ✅ QTY=2 ACCEPTED for this item (2 landed in cart).")
    elif cq2 == 1 or r2.get('limit_rejection_seen'):
        print("verdict: ⚠️ QTY=2 CLAMPED to 1 — this item is limited to 1/account.")
    elif r2.get('atc_ok_2xx'):
        print("verdict: ✅ qty=2 ATC returned 2xx with no limit rejection (cart read unavailable).")
    else:
        print("verdict: ❓ Inconclusive — check ATC status / login above.")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(asyncio.run(amain()) or 0)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
