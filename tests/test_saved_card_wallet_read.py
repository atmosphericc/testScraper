#!/usr/bin/env python3
"""
READ-ONLY: per-account saved-card WALLET check (no cart writes at all).

Why this exists
---------------
tests/test_saved_card_compose_smoke.py (07-13, PASSED 3/3) proves the
multi-account plumbing: session validity, MEMBER write-auth (ATC 201),
Shape capture, per-account forwarder isolation, checkout compose, cart
clear. But in API mode the compose-and-abort body is only
{'cart_type','channel_id'} and it is aborted BEFORE the POST — the saved
card is never read, attached, or validated. The DOM payment traversal
(where the CVV fallback lives) is bypassed entirely. So the smoke does
NOT answer "does business/alt-1 actually have a working card on file?" —
the exact silent-loss failure the 06-24/06-30 post-mortems flagged.

This harness answers it the way a human would, automated: navigate each
account's own browser (own profile, own BD exit IP via forwarder) to the
Payments page and read the saved-card tiles. Pure navigation + DOM read +
screenshot. NOTHING is written: no ATC, no checkout, no cart mutation.
Screenshots land in logs/screenshots/payments_<account>_<ts>.png as
human-verifiable evidence.

What it can and cannot prove
----------------------------
- CAN: a card exists on file; its last4; visible expiry text; the session
  reaches an authenticated account page (not bounced to login).
- CANNOT: that the issuer will approve an auth (only a real order proves
  that), or that the CVV digits in config/target_accounts.json are right
  (Target never shows CVV; those came from the user: primary=464,
  business/alt-1=229).

Run (bot must be DOWN — same profiles, same 2300x forwarder band):
    venv/Scripts/python.exe tests/test_saved_card_wallet_read.py
Env:
    TARGET_SMOKE_ACCOUNTS=business,alt-1   # default: every enabled account
"""
import os

os.environ.setdefault('TEST_MODE', 'true')   # defense only — no executor is used

import asyncio
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

# Reuses the smoke's forwarder scaffolding so the two can't drift
# (importing it only sets safety env + defines helpers; main() is guarded).
from test_saved_card_compose_smoke import _start_forwarders, _stop_forwarders, banner

PAYMENTS_URL = 'https://www.target.com/account/payments'
ONLY = [s.strip() for s in os.environ.get('TARGET_SMOKE_ACCOUNTS', '').split(',') if s.strip()]

CARD_RE = re.compile(r'ending in\s*(\d{4})|•{2,4}\s*(\d{4})', re.I)
EXPIRY_RE = re.compile(r'\b(0[1-9]|1[0-2])\s*/\s*(\d{4}|\d{2})\b')
NO_CARD_RE = re.compile(r'add (a )?payment card|no saved payment', re.I)
SIGNIN_RE = re.compile(r'sign in to your target account|keep me signed in', re.I)


def _expiry_ok(mm: str, yy: str) -> bool:
    year = int(yy) + 2000 if len(yy) == 2 else int(yy)
    now = datetime.now()
    return (year, int(mm)) >= (now.year, now.month)


async def read_wallet(sm, shot_path: Path) -> dict:
    out = {'signed_in': None, 'cards': [], 'expiries': [], 'no_card_ui': False,
           'url': None, 'screenshot': None, 'error': None}
    tab = await sm.browser.get(PAYMENTS_URL)
    deadline = time.time() + 35
    txt = ''
    while time.time() < deadline:
        await asyncio.sleep(2.0)
        try:
            txt = await tab.evaluate('document.body ? document.body.innerText : ""') or ''
            out['url'] = await tab.evaluate('location.href')
        except Exception:
            continue
        if SIGNIN_RE.search(txt) or 'login' in (out['url'] or ''):
            out['signed_in'] = False
            break
        cards = ['*' + (m.group(1) or m.group(2)) for m in CARD_RE.finditer(txt)]
        if cards:
            out['signed_in'] = True
            out['cards'] = sorted(set(cards))
            out['expiries'] = [f'{m.group(1)}/{m.group(2)}' +
                               ('' if _expiry_ok(m.group(1), m.group(2)) else ' EXPIRED')
                               for m in EXPIRY_RE.finditer(txt)]
            break
        if NO_CARD_RE.search(txt):
            out['signed_in'] = True
            out['no_card_ui'] = True
            break
    if out['signed_in'] is None:
        out['error'] = f'page never showed cards/sign-in within 35s (len={len(txt)})'
    try:
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        await tab.save_screenshot(str(shot_path))
        out['screenshot'] = str(shot_path)
    except Exception as e:
        out['error'] = (out['error'] or '') + f' screenshot: {e}'
    return out


def main() -> int:
    from src.purchasing.worker_pool import WorkerPool

    banner('SAVED-CARD WALLET READ — navigation + DOM read only, ZERO writes', '#')
    pool = WorkerPool.from_accounts_file(ROOT / 'config' / 'target_accounts.json')
    try:
        fwd, fwd_loop = _start_forwarders(pool)
    except Exception as e:
        print(f'[FATAL] forwarder start failed: {e} — not falling back to home IP.')
        return 2

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    results = {}
    try:
        pool.build_all()
        print('\n[1/2] Launching account browsers (no Shape warmup needed for a read)...')
        init = pool.ensure_all_ready(per_worker_timeout=120.0, warmup_shape_headers=False)
        for lbl, r in init.items():
            print(f'        {lbl}: ok={r.get("ok")} init={r.get("init_seconds")}s err={r.get("error")}')

        print('\n[2/2] Reading Payments page per account...')
        for w in pool.workers:
            acct = w.label().split('/')[-1]
            if ONLY and acct not in ONLY:
                continue
            if w.session_manager is None or not init.get(w.label(), {}).get('ok'):
                results[acct] = {'pass': False, 'reason': 'browser init failed'}
                continue
            banner(f'ACCOUNT: {acct}')
            shot = ROOT / 'logs' / 'screenshots' / f'payments_{acct}_{ts}.png'
            try:
                r = w.run_async(read_wallet(w.session_manager, shot)).result(timeout=90)
            except Exception as e:
                r = {'error': f'{type(e).__name__}: {e}', 'signed_in': None, 'cards': []}
            r['pass'] = bool(r.get('signed_in')) and bool(r.get('cards'))
            results[acct] = r
            print(f'  signed_in={r.get("signed_in")} cards={r.get("cards")} '
                  f'expiries={r.get("expiries")} no_card_ui={r.get("no_card_ui")} '
                  f'err={r.get("error")}\n  evidence: {r.get("screenshot")}')
    finally:
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
        print(f'  [{flag}] {acct}: cards={r.get("cards")} expiries={r.get("expiries")} '
              f'signed_in={r.get("signed_in")} shot={r.get("screenshot")}')
    if not results:
        print('  no accounts ran!')
        all_ok = False
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
