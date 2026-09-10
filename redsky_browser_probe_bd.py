#!/usr/bin/env python3
"""READ-ONLY RedSky probe: real browser on a Bright Data reserve IP (2026-09-04).

Mirrors one stock-pool session exactly: fresh scratch profile, exit via a BD ISP
IP through the local CONNECT forwarder (the production path), park on the Target
homepage so Shape mints its normal cookies, then two browser-native bulk RedSky
reads. Reports status + body kind. No captcha interaction, no synthetic input,
no purchase code. Answers: can the POOL's transport (BD IP + real Chrome) read
stock right now?

    venv\\Scripts\\python.exe redsky_browser_probe_bd.py [pinned_ip]
"""
import asyncio, os, sys, time, json, re, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
os.environ.setdefault('TARGET_FP_CHROMIUM', '0')
os.environ.setdefault('TARGET_APPLY_FINGERPRINT', '0')   # pool sessions run un-spoofed real Chrome
os.environ.pop('TEST_MODE', None)

WANT_IP = sys.argv[1] if len(sys.argv) > 1 else '168.158.111.240'
# 2026-09-07: `home` / `local` = same fresh-profile probe with NO proxy (the
# home IP); reserve_proxies entries are searchable too; unknown IP = exit 5.
HOME = WANT_IP.lower() in ('home', 'local')
cfg = json.load(open('config/proxyIps.json', encoding='utf-8'))
if HOME:
    BD_URL, PIN = None, 'home'
else:
    _pool = list(cfg['proxies']) + list(cfg.get('reserve_proxies', []))
    BD_URL = next((u for u in _pool if f'-ip-{WANT_IP}:' in u), None)
    if BD_URL is None:
        print(f"[PROBE-BD] {WANT_IP} is not in proxyIps 'proxies' or 'reserve_proxies' — nothing probed", flush=True)
        sys.exit(5)
    PIN = re.search(r'-ip-([0-9.]+)', BD_URL).group(1)
PORT = 22900
PROFILE = ROOT / 'state' / 'smoke_profiles' / ('redsky_probe_home' if HOME else 'redsky_probe_bd')

from src.proxy.local_forwarder import ForwarderPool   # noqa
from src.session.session_manager import SessionManager  # noqa

KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
TCINS = "1011960739,21516452"
# 2026-09-09: PROBE_REDSKY_CHANNEL=apps probes the mobile-app RedSky aggregation
# (`/v1/apps/tcin_product_list_v2` + x-channel-id: APPS) that public monitors use
# to read stock without the HUMAN captcha; default 'web' = the sweep's endpoint.
CHANNEL = os.environ.get('PROBE_REDSKY_CHANNEL', 'web').strip().lower()
if CHANNEL == 'apps':
    _URL = 'https://redsky.target.com/redsky_aggregations/v1/apps/tcin_product_list_v2'
    _EXTRA_HDRS = "'x-channel-id':'APPS','x-client-platform':'iPhone','x-client-version':'2026.28.0',"
elif CHANNEL == 'apps_plain':
    # the app aggregation with PLAIN headers: custom x-* headers force a CORS
    # preflight that RedSky rejects from an in-page fetch (probe 2026-09-09).
    _URL = 'https://redsky.target.com/redsky_aggregations/v1/apps/tcin_product_list_v2'
    _EXTRA_HDRS = ""
else:
    _URL = 'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1'
    _EXTRA_HDRS = ""
REDSKY_JS = f"""(async () => {{
  try {{
    const u = new URL('{_URL}');
    u.searchParams.set('key','{KEY}'); u.searchParams.set('tcins','{TCINS}');
    u.searchParams.set('store_id','1176'); u.searchParams.set('pricing_store_id','1176');
    u.searchParams.set('has_pricing_context','true'); u.searchParams.set('has_promotions','true');
    u.searchParams.set('_', Date.now()+''+Math.random().toString(36).slice(2));
    const r = await fetch(u.toString(), {{ cache:'no-store', credentials:'include',
      headers:{{{_EXTRA_HDRS}'accept':'application/json','accept-language':'en-US,en;q=0.9'}} }});
    const t = await r.text();
    return {{status:r.status, body:t.slice(0,400)}};
  }} catch(e) {{ return {{status:-1, body:String(e)}}; }}
}})()"""
IP_JS = """(async () => { try { const r = await fetch('https://api.ipify.org?format=json', {cache:'no-store'}); return (await r.json()).ip; } catch(e) { return 'err:'+e; } })()"""

def log(m): print(f"[PROBE-BD {time.strftime('%H:%M:%S')} {CHANNEL}] {m}", flush=True)

async def read(tab, tag):
    try:
        res = await asyncio.wait_for(tab.evaluate(REDSKY_JS, await_promise=True), timeout=25.0)
    except Exception as e:
        log(f"{tag}: errored {type(e).__name__}: {e}"); return None
    st, body = res.get('status'), res.get('body', '')
    kind = 'CAPTCHA' if 'captcha' in body else ('OK-DATA' if 'product_summaries' in body else 'other')
    log(f"{tag}: http={st} {kind} body={body[:160]!r}")
    return st, kind

async def main():
    if HOME:
        log(f"HOME IP (no proxy); fresh profile {PROFILE.name}")
    else:
        log(f"pinned BD exit={PIN} via 127.0.0.1:{PORT}; fresh profile {PROFILE.name}")
    if PROFILE.exists():
        shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    fwd = None
    if not HOME:
        fwd = ForwarderPool()
        fwd.add_upstream(BD_URL, PORT)
        await fwd.start_all()
    sm = SessionManager(session_path=str(PROFILE / 'probe_session.json'), user_data_dir=str(PROFILE),
                        proxy_url=(None if HOME else f'127.0.0.1:{PORT}'),
                        account_id=('probe-home' if HOME else 'probe-bd'), timezone='America/Chicago',
                        apply_fingerprint=False)
    rc = 1
    try:
        ok = await asyncio.wait_for(sm.initialize(), timeout=120.0)
        log(f"initialize -> {ok}")
        if not ok: return 3
        tab = await sm.get_page()
        try:
            ip = await asyncio.wait_for(tab.evaluate(IP_JS, await_promise=True), timeout=20.0)
            log(f"browser exit IP = {ip} (expected {PIN})")
        except Exception as e:
            log(f"exit-IP check errored: {e}")
        try: await asyncio.wait_for(tab.get("https://www.target.com"), timeout=40.0)
        except Exception as e: log(f"homepage nav: {e}")
        await asyncio.sleep(6)
        r1 = await read(tab, "A) first read via BD")
        await asyncio.sleep(4)
        r2 = await read(tab, "B) second read via BD")
        kinds = [r[1] for r in (r1, r2) if r]
        where = "the HOME IP" if HOME else f"BD exit {PIN}"
        if 'OK-DATA' in kinds:
            log(f"VERDICT: fresh-profile real Chrome on {where} READS RedSky NOW (OK-DATA) -> usable as a sweep exit.")
            rc = 0
        elif 'CAPTCHA' in kinds:
            log(f"VERDICT: fresh-profile real Chrome on {where} is captcha-gated (HUMAN/PerimeterX) -> "
                f"{'the device/home IP itself is flagged; rest it' if HOME else 'that IP range is flagged; rest it or replace the exit'}.")
            rc = 2
        else:
            log("VERDICT: non-captcha block on BD transport -> inspect body above.")
            rc = 4
        return rc
    finally:
        try: await asyncio.wait_for(sm.cleanup(), timeout=20.0)
        except Exception:
            try: sm.close_browser_sync()
            except Exception: pass
        if fwd is not None:
            try: await asyncio.wait_for(fwd.stop_all(), timeout=5.0)
            except Exception: pass

if __name__ == '__main__':
    try: code = asyncio.run(asyncio.wait_for(main(), timeout=300))
    except Exception as e: log(f"FATAL {type(e).__name__}: {e}"); code = 9
    sys.exit(code or 0)
