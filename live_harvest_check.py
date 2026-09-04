#!/usr/bin/env python3
"""LIVE check of the real-click Shape harvest on ONE account (2026-09-03).

Runs the PRODUCTION code path (SessionManager -> PurchaseExecutor -> warmup ->
background harvest loop -> boot SELFTEST) for a single identity, then reads the
cart before/after to prove nothing landed, and shuts Chrome down. No purchase
code is ever invoked (execute_purchase is never called). USER-GATED: opens one
headful Chrome on the account's real profile — never auto-run by the bot.

    venv\\Scripts\\python.exe live_harvest_check.py [account_id] [seconds]
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'primary'
BUDGET_S = float(sys.argv[2]) if len(sys.argv) > 2 else 170.0

# Mirror the bat's arming for this check (set BEFORE importing the executor).
os.environ.setdefault('TARGET_FP_CHROMIUM', '0')
os.environ.setdefault('TARGET_APPLY_FINGERPRINT', '1')
os.environ.setdefault('TARGET_SHAPE_HARVEST', '1')
os.environ.setdefault('TARGET_HARVEST_TCINS', '21516452,50225561,53274278')
os.environ.setdefault('TARGET_HARVEST_BANK', '3')
os.environ.setdefault('TARGET_HARVEST_TTL_S', '300')
os.environ.setdefault('TARGET_HARVEST_SELFTEST', '1')
os.environ.setdefault('TARGET_HARVEST_REPLAY', '1')
os.environ.setdefault('TARGET_ATC_RESPONSE_HEADER_CAPTURE', '1')
os.environ.setdefault('TARGET_WARMUP_TAB_COUNT', '1')
os.environ.setdefault('TARGET_RETRY_WARM', '0')
os.environ.pop('TEST_MODE', None)

from src.session.session_manager import SessionManager          # noqa: E402
from src.session.purchase_executor import PurchaseExecutor      # noqa: E402

CART_JS = """(async () => {
    try {
        const r = await fetch('https://carts.target.com/web_checkouts/v1/cart?cart_type=REGULAR&field_groups=CART%2CCART_ITEMS%2CSUMMARY&key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14',
                              {credentials: 'include', headers: {'Accept': 'application/json', 'x-application-name': 'web'}});
        const t = await r.text();
        let items = [];
        try { const p = JSON.parse(t); items = (p.cart_items || []).map(i => String(i.tcin)); } catch (e) {}
        return {status: r.status, items: items, n: items.length};
    } catch (e) { return {status: 0, items: [], n: -1, err: String(e)}; }
})()"""


def log(msg):
    print(f"[LIVE_CHECK {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def account_cfg(aid):
    cfg = json.load(open('config/target_accounts.json', encoding='utf-8'))
    enabled = [a for a in cfg.get('accounts', []) if a.get('enabled', True) is not False]
    for i, a in enumerate(enabled):
        if a.get('account_id') == aid:
            return {
                'session_path': a.get('session_path') or ('target.json' if i == 0 else f'target-{i + 1}.json'),
                'profile_dir': a.get('profile_dir') or ('nodriver-profile' if i == 0 else f'nodriver-profile-{i + 1}'),
                'proxy_url': (str(a.get('proxy_url') or '').strip() or None),
                'timezone': (str(a.get('timezone') or '').strip() or None),
            }
    raise SystemExit(f'account {aid!r} not found/enabled')


async def cart_snapshot(ex, tag):
    try:
        tab = await ex.session_manager.get_page()
        res = await asyncio.wait_for(tab.evaluate(CART_JS, await_promise=True), timeout=15.0)
        log(f"CART {tag}: status={res.get('status')} items={res.get('n')} tcins={res.get('items')}")
        return res
    except Exception as e:
        log(f"CART {tag}: read failed: {type(e).__name__}: {e}")
        return None


async def main():
    c = account_cfg(ACCOUNT)
    if c['proxy_url'] and 'brd.superproxy.io' in c['proxy_url']:
        log("this account exits a Bright Data proxy (needs the local forwarder) — use an account on the HOME IP for this check")
        return 2
    log(f"account={ACCOUNT} session={c['session_path']} profile={c['profile_dir']} proxy={c['proxy_url'] or 'HOME IP'} tz={c['timezone']}")
    sm = SessionManager(session_path=c['session_path'], user_data_dir=c['profile_dir'],
                        proxy_url=c['proxy_url'], account_id=ACCOUNT, timezone=c['timezone'],
                        apply_fingerprint=True)
    ex = PurchaseExecutor(sm)
    t0 = time.time()
    rc = 1
    try:
        ok = await asyncio.wait_for(sm.initialize(), timeout=90.0)
        log(f"session initialize -> {ok} ({time.time() - t0:.0f}s)")
        if not ok:
            return 3
        await cart_snapshot(ex, 'BEFORE')
        # Production boot path: warmup tab + dummy POST heartbeat -> spawns the
        # background refill loop AND (flag-gated) the harvest loop.
        # Production retries the warm (manager + sentinel); the very first dummy
        # POST on a fresh tab often loses its CORS preflight (403) before Shape
        # initialises — 09-01 boot needed 3 tries. Mirror that here.
        w = False
        for _try in range(1, 7):
            w = await asyncio.wait_for(ex.warm_shape_headers(), timeout=60.0)
            log(f"warm_shape_headers try {_try} -> {w}")
            if w:
                break
            await asyncio.sleep(5.0)
        log(f"harvest task spawned={ex._harvest_task is not None}; cfg={ex._harvest_cfg}")
        if ex._harvest_task is None:
            log("harvest task NOT spawned (flag off or no TCINs) — aborting")
            return 4
        deadline = t0 + BUDGET_S
        last = ''
        while time.time() < deadline:
            await asyncio.sleep(3.0)
            s = (f"{ex._shape_bank.summary()} replay_on={ex._harvest_replay_on} "
                 f"selftest_done={ex._harvest_selftest_done} tcin={ex._harvest_tcin} "
                 f"stats={ex._harvest_stats} disabled={ex._harvest_disabled_reason!r}")
            if s != last:
                log(s)
                last = s
            if ex._harvest_selftest_done and ex._shape_bank.count() >= 1 and time.time() - t0 > 60:
                # self-test ran and the bank refilled afterwards: enough evidence
                break
        after = await cart_snapshot(ex, 'AFTER')
        log(f"FINAL: {ex._shape_bank.summary()} replay_on={ex._harvest_replay_on} "
            f"selftest_done={ex._harvest_selftest_done} last_replay={ex._harvest_last_replay} "
            f"stats={ex._harvest_stats} landed_suspect={ex._harvest_landed_suspect}")
        rc = 0
        return rc
    finally:
        for t in ([ex._harvest_task] if ex._harvest_task else []) + list(ex._warmup_refill_tasks):
            try:
                t.cancel()
            except Exception:
                pass
        try:
            await asyncio.wait_for(sm.cleanup(), timeout=20.0)
            log("cleanup done")
        except Exception as e:
            log(f"cleanup error: {e}")
            try:
                sm.close_browser_sync()
            except Exception:
                pass


if __name__ == '__main__':
    try:
        code = asyncio.run(asyncio.wait_for(main(), timeout=BUDGET_S + 150))
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}")
        code = 9
    sys.exit(code or 0)
