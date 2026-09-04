#!/usr/bin/env python3
"""READ-ONLY RedSky probe through the real browser transport (2026-09-04).

Launches ONE real Chrome (SessionManager), parks on the Target homepage so
Shape mints its normal session cookies, then fires the exact browser-native bulk
RedSky read the stock pool uses (credentials:'include', real JA3). Reports the
status + body kind. That's it: no captcha interaction, no synthetic input, no
purchase code. Answers one question: can a real browser read stock right now?

    venv\\Scripts\\python.exe redsky_browser_probe.py [account]
"""
import asyncio, os, sys, time, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
os.environ.setdefault('TARGET_FP_CHROMIUM', '0')
os.environ.setdefault('TARGET_APPLY_FINGERPRINT', '1')
os.environ.pop('TEST_MODE', None)

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'primary'
_cfg = json.load(open('config/target_accounts.json', encoding='utf-8'))
_enabled = [a for a in _cfg.get('accounts', []) if a.get('enabled', True) is not False]
_acc = next((a for a in _enabled if a.get('account_id') == ACCOUNT), _enabled[0])
_idx = _enabled.index(_acc)
SESSION = _acc.get('session_path') or ('target.json' if _idx == 0 else f'target-{_idx+1}.json')
PROFILE = _acc.get('profile_dir') or ('nodriver-profile' if _idx == 0 else f'nodriver-profile-{_idx+1}')
PROXY = (str(_acc.get('proxy_url') or '').strip() or None)

from src.session.session_manager import SessionManager  # noqa

KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
TCINS = "1011960739,21516452"

REDSKY_JS = f"""(async () => {{
  try {{
    const u = new URL('https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1');
    u.searchParams.set('key','{KEY}'); u.searchParams.set('tcins','{TCINS}');
    u.searchParams.set('store_id','1176'); u.searchParams.set('pricing_store_id','1176');
    u.searchParams.set('has_pricing_context','true'); u.searchParams.set('has_promotions','true');
    u.searchParams.set('_', Date.now()+''+Math.random().toString(36).slice(2));
    const r = await fetch(u.toString(), {{ cache:'no-store', credentials:'include',
      headers:{{'accept':'application/json','accept-language':'en-US,en;q=0.9'}} }});
    const t = await r.text();
    return {{status:r.status, body:t.slice(0,400)}};
  }} catch(e) {{ return {{status:-1, body:String(e)}}; }}
}})()"""

def log(m): print(f"[PROBE {time.strftime('%H:%M:%S')}] {m}", flush=True)

async def read(tab, tag):
    try:
        res = await asyncio.wait_for(tab.evaluate(REDSKY_JS, await_promise=True), timeout=20.0)
    except Exception as e:
        log(f"{tag}: errored {type(e).__name__}: {e}"); return None
    st, body = res.get('status'), res.get('body', '')
    kind = 'CAPTCHA' if 'captcha' in body else ('OK-DATA' if 'product_summaries' in body else 'other')
    log(f"{tag}: http={st} {kind} body={body[:160]!r}")
    return st, kind

async def main():
    log(f"account={ACCOUNT} profile={PROFILE} proxy={PROXY or 'HOME IP'}")
    sm = SessionManager(session_path=SESSION, user_data_dir=PROFILE, proxy_url=PROXY,
                        account_id=ACCOUNT, timezone=(str(_acc.get('timezone') or '').strip() or None),
                        apply_fingerprint=True)
    rc = 1
    try:
        ok = await asyncio.wait_for(sm.initialize(), timeout=90.0)
        log(f"initialize -> {ok}")
        if not ok: return 3
        tab = await sm.get_page()
        try: await asyncio.wait_for(tab.get("https://www.target.com"), timeout=30.0)
        except Exception as e: log(f"homepage nav: {e}")
        await asyncio.sleep(5)
        r1 = await read(tab, "A) first read")
        await asyncio.sleep(4)
        r2 = await read(tab, "B) second read")
        kinds = [r[1] for r in (r1, r2) if r]
        if 'OK-DATA' in kinds:
            log("VERDICT: real browser READS RedSky NOW -> tonight's 403s were double-bot overload; the clean single bot will detect stock.")
            rc = 0
        elif 'CAPTCHA' in kinds:
            log("VERDICT: real browser is captcha-gated too -> Shape flagged this device/session; needs rest (flag decays), not more traffic.")
            rc = 2
        else:
            log("VERDICT: non-captcha block on the browser transport -> inspect body above.")
            rc = 4
        return rc
    finally:
        try: await asyncio.wait_for(sm.cleanup(), timeout=20.0)
        except Exception:
            try: sm.close_browser_sync()
            except Exception: pass

if __name__ == '__main__':
    try: code = asyncio.run(asyncio.wait_for(main(), timeout=240))
    except Exception as e: log(f"FATAL {type(e).__name__}: {e}"); code = 9
    sys.exit(code or 0)
