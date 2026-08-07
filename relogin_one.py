#!/usr/bin/env python3
"""Perfect the single-account re-login cycle: COMPLETELY SIGN OUT -> RE-LOGIN with
the config email+password (matched pair, no remembered account) -> SAVE session.

Verbose, one step at a time, ONE run. Usage:
    python relogin_one.py <account_id>            # full live cycle
    python relogin_one.py <account_id> --dry      # show plan only, no browser

Reuses the harvester's proven primitives (cookie export, identity, selectors).
"""
from __future__ import annotations
import asyncio, os, platform, sys, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from harvest_accounts import (
    load_accounts, DEFAULT_CONFIG, _export_cookies, _write_session,
    _find_input, _type_humanish, _click_button_by_text, AUTH_COOKIE_HINTS,
)
from src.session.account_identity import build_identity, apply_identity

_LOGFILE = ROOT / "logs" / "relogin.log"


def log(step, msg):
    """Print to console AND append to logs/relogin.log so the login step is
    persisted for review (the wrapper runs this unattended before app.py)."""
    line = f"[{step}] {msg}"
    print(line, flush=True)
    try:
        _LOGFILE.parent.mkdir(exist_ok=True)
        with open(_LOGFILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
    except Exception:
        pass


async def _fill_verified(tab, css_selector, value) -> bool:
    """Read back the field value; if keystrokes didn't fully register (flaky —
    leaves the Continue/Sign-in button disabled), re-fill via the React-native
    value setter + input/change events so React state matches. Returns True if the
    field already held the value (no re-fill needed)."""
    import json as _j
    sel = _j.dumps(css_selector)
    val = _j.dumps(value)
    try:
        current = await tab.evaluate(f"(()=>{{const e=document.querySelector({sel});return e?e.value:null;}})()")
    except Exception:
        current = None
    if current == value:
        return True
    try:
        await tab.evaluate(
            f"(()=>{{const e=document.querySelector({sel}); if(!e) return false;"
            f"const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
            f"s.call(e,{val}); e.dispatchEvent(new Event('input',{{bubbles:true}}));"
            f"e.dispatchEvent(new Event('change',{{bubbles:true}})); return e.value==={val};}})()")
    except Exception:
        pass
    return False


async def _on_account_page_loggedin(tab) -> bool:
    """True iff /account loads without redirecting to login (authoritative)."""
    try:
        await tab.get("https://www.target.com/account")
        await asyncio.sleep(3)
        url = (getattr(tab, 'url', '') or '').lower()
        return ('login' not in url and 'signin' not in url and '/account' in url)
    except Exception:
        return False


async def full_signout(tab) -> bool:
    """Clear cookies + storage so NO account is remembered. Verify /account now
    redirects to the login (username-request) screen."""
    log("SIGNOUT", "clearing cookies + storage...")
    try:
        await tab.get("https://www.target.com")
        await asyncio.sleep(2)
        try:
            await tab.evaluate("(()=>{try{localStorage.clear();sessionStorage.clear();}catch(e){}})()")
        except Exception:
            pass
        try:
            import zendriver as uc
            await tab.send(uc.cdp.network.clear_browser_cookies())
        except Exception as e:
            log("SIGNOUT", f"cookie clear warning: {e}")
        await asyncio.sleep(1.5)
    except Exception as e:
        log("SIGNOUT", f"warning: {e}")
    # Verify signed out: /account must redirect to login.
    try:
        await tab.get("https://www.target.com/account")
        await asyncio.sleep(3)
        url = (getattr(tab, 'url', '') or '').lower()
        signed_out = ('login' in url or 'signin' in url)
        log("SIGNOUT", f"verify: url={tab.url[:80]} -> {'SIGNED OUT' if signed_out else 'STILL HAS SESSION'}")
        return signed_out
    except Exception as e:
        log("SIGNOUT", f"verify error: {e}")
        return False


async def _advance(tab, present_check, label) -> bool:
    """Click submit/continue robustly (native -> Enter -> JS), verifying we left
    the current screen via present_check() going False."""
    try:
        await tab.evaluate("""(()=>{const e=document.activeElement;
          if(e){['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));}})()""")
    except Exception:
        pass
    # Wait up to ~6s for the submit button to enable (disabled until React validates).
    for _ in range(12):
        en = await tab.evaluate("(()=>{const b=document.querySelector('button[type=submit]');"
                                "return b?!b.disabled:false;})()")
        if en:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(0.3)
    # form.requestSubmit() is the reliable React submit (a coordinate .click() on
    # Target's button silently no-ops ~half the time). Try it first, then fallbacks.
    for method in ('requestsubmit', 'native', 'enter', 'js'):
        try:
            if method == 'requestsubmit':
                await tab.evaluate("(()=>{const b=document.querySelector('button[type=submit]');"
                                   "if(b&&b.form){b.form.requestSubmit(b);return true;}"
                                   "const f=document.querySelector('form');if(f){f.requestSubmit();return true;}return false;})()")
            elif method == 'native':
                b = await tab.select('button[type="submit"]', timeout=2)
                if b:
                    await b.click()
            elif method == 'enter':
                el = await tab.select('input[type="password"], input[type="email"], input[name="username"]', timeout=1)
                if el:
                    await el.send_keys("\r")
            else:
                await tab.evaluate("(()=>{const b=document.querySelector('button[type=submit]');if(b){b.click();return true;}return false;})()")
        except Exception:
            pass
        # Poll up to 6s for the screen to actually change.
        for _ in range(6):
            await asyncio.sleep(1)
            if not await present_check():
                log("LOGIN", f"{label} advanced via {method}")
                return True
    log("LOGIN", f"{label} did NOT advance")
    return False


async def _kmsi_state(tab) -> str:
    # 'checked' | 'unchecked' | 'absent' — pure querySelector (no element passing).
    try:
        return await tab.evaluate(
            "(()=>{const e=document.querySelector('#keepMeSignedIn');"
            "return e?(e.checked?'checked':'unchecked'):'absent';})()")
    except Exception:
        return 'absent'


async def ensure_kmsi(tab):
    state = await _kmsi_state(tab)
    if state == 'absent':
        log("KMSI", "checkbox not on this screen"); return
    if state == 'checked':
        log("KMSI", "already checked"); return
    # 1) native label click (the styled label toggles the hidden input)
    try:
        lbl = await tab.select('label[for="keepMeSignedIn"]', timeout=1)
        if lbl:
            await lbl.click()
    except Exception:
        pass
    if await _kmsi_state(tab) != 'checked':
        # 2) native checkbox click
        try:
            cb = await tab.select('#keepMeSignedIn', timeout=1)
            if cb:
                await cb.click()
        except Exception:
            pass
    if await _kmsi_state(tab) != 'checked':
        # 3) JS click + forced state + change (React)
        try:
            await tab.evaluate("""(()=>{const e=document.querySelector('#keepMeSignedIn');
              if(e){e.click(); if(!e.checked){e.checked=true;
              e.dispatchEvent(new Event('click',{bubbles:true}));
              e.dispatchEvent(new Event('change',{bubbles:true}));}}})()""")
        except Exception:
            pass
    log("KMSI", "enabled" if await _kmsi_state(tab) == 'checked' else "could NOT enable")


async def reveal_password(tab) -> bool:
    async def pw():
        return bool(await _find_input(tab, ('input[type="password"]',), timeout=2))
    if await pw():
        return True
    for method in ('native', 'js', 'dispatch'):
        try:
            if method == 'native':
                opt = await tab.select('#password', timeout=3)
                if opt and not await tab.evaluate("el => el.tagName === 'INPUT'", opt):
                    await opt.click()
            elif method == 'js':
                await tab.evaluate("(()=>{const e=document.querySelector('#password');if(e&&e.tagName!=='INPUT'){e.click();}})()")
            else:
                await tab.evaluate("""(()=>{const e=document.querySelector('#password');
                  if(!e||e.tagName==='INPUT')return;const r=e.getBoundingClientRect();
                  const o={bubbles:true,cancelable:true,composed:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2};
                  for(const t of ['pointerdown','mousedown','pointerup','mouseup','click'])e.dispatchEvent(new MouseEvent(t,o));})()""")
        except Exception:
            pass
        await asyncio.sleep(2.5)
        if await pw():
            log("LOGIN", f"password field revealed via {method}")
            return True
    return False


async def login(tab, email, password) -> bool:
    log("LOGIN", "navigating to username-request sign-in...")
    await tab.get("https://www.target.com/login?client_id=ecom-web-1.0.0"
                  "&ui_namespace=ui-default&actions=create_session_request_username")
    await asyncio.sleep(3.5)

    ef = await _find_input(tab, ('input[name="username"]', 'input#username', 'input[type="email"]',
                                 'input[autocomplete="username"]', 'input[name="email"]'), timeout=6)
    if not ef:
        log("LOGIN", "username field NOT found"); return False
    await ef.click(); await asyncio.sleep(0.3)
    await _type_humanish(ef, email)
    await asyncio.sleep(0.3)
    if not await _fill_verified(tab, 'input[name="username"], input[type="email"], input#username', email):
        log("LOGIN", "username did not register reliably — re-filled via React setter")
    else:
        log("LOGIN", "username typed + verified")

    # Continue off the username screen (reuses the robust _advance: requestSubmit
    # first, then click/Enter/JS, waiting for the button to enable and for the
    # screen to change). Advance = username input no longer present.
    async def _username_present():
        return bool(await _find_input(tab, ('input[name="username"]', 'input[type="email"]', 'input#username'), timeout=1))
    if not await _advance(tab, _username_present, "username"):
        log("LOGIN", "could NOT advance past username"); return False

    if not await reveal_password(tab):
        log("LOGIN", "could NOT reach password field after username"); return False

    await ensure_kmsi(tab)
    pw = await _find_input(tab, ('input[type="password"]',), timeout=4)
    if not pw:
        log("LOGIN", "password field vanished"); return False
    await pw.click(); await asyncio.sleep(0.3)
    await _type_humanish(pw, password)
    await asyncio.sleep(0.3)
    if await _fill_verified(tab, 'input[type="password"]', password):
        log("LOGIN", "password typed + verified")
    else:
        log("LOGIN", "password re-filled via React setter")

    async def _password_present():
        return bool(await _find_input(tab, ('input[type="password"]',), timeout=1))
    await _advance(tab, _password_present, "password")
    await asyncio.sleep(4)
    return await _on_account_page_loggedin(tab)


def _mask(email: str) -> str:
    if '@' in email:
        nm, dom = email.split('@', 1)
        return nm[:3] + '***@' + dom
    return (email[:3] + '***') if email else '(none)'


async def relogin_account(acc: dict, force: bool = False, manual: bool = False) -> bool:
    """Ensure ONE account is logged in + saved. Validate-first: if already logged
    in, just refresh cookies; else own profile+identity -> sign out -> re-login
    with this account's email+password -> save. force=True always re-logs in.
    manual=True: open the login (through this account's IP) and PAUSE for you to
    sign in by hand — for one-time enrollment on a new IP that triggers a device
    challenge the automated flow can't clear.
    Each account is fully isolated (own profile dir + own session file)."""
    acc_id = acc["account_id"]
    creds_ok = bool(acc["username"] and acc["password"] and acc["password"] != "REPLACE_ME")
    log("ACCOUNT", f"=== {acc_id} | {_mask(acc['username'])} | profile={acc['profile_dir']} "
                   f"| session={acc['session_path']} | creds={'OK' if creds_ok else 'MISSING'} ===")
    if not creds_ok:
        log("ACCOUNT", f"{acc_id}: skipped — no valid email+password in config")
        return False

    import zendriver as uc
    identity = build_identity(acc_id, timezone=acc["timezone"] or None)
    profile_dir = ROOT / acc["profile_dir"]; profile_dir.mkdir(parents=True, exist_ok=True)
    browser_args = ["--window-size=1920,1080"]

    # Per-account exit IP: if proxy_url is set, LOGIN exits the SAME IP the purchase
    # path uses (coherence — cookies minted on the IP they're used from). BD auth
    # URLs go through a local forwarder (Chrome can't do inline auth); plain
    # host:port pass through; empty = home IP. Forwarder port 25000+ (distinct from
    # harvest 24000 / purchase 23000 / stock 22000); accounts run sequentially so
    # one port is reused safely.
    fwd_pool = None
    proxy_url = (acc.get("proxy_url") or "").strip()
    # RELOGIN_SKIP_PROXY=1 enrolls on the HOME IP without editing the config (the
    # purchase path keeps its per-account BD IP). Diagnostic/fallback for the case
    # where the BD IP is what Target's login-surface Shape is rejecting.
    if os.environ.get("RELOGIN_SKIP_PROXY", "").lower() in ("1", "true", "yes"):
        if proxy_url:
            log("PROXY", f"{acc_id}: proxy SKIPPED (RELOGIN_SKIP_PROXY=1) — login via HOME IP")
        proxy_url = ""
    if proxy_url:
        low = proxy_url.lower()
        if "@" in proxy_url or "brd." in low or "superproxy" in low:
            try:
                from src.proxy.local_forwarder import ForwarderPool
                fwd_pool = ForwarderPool()
                fwd_pool.add_upstream(proxy_url, 25000)
                await fwd_pool.start_all()
                browser_args.append("--proxy-server=127.0.0.1:25000")
                log("PROXY", f"{acc_id}: login+purchase exit via account BD IP (forwarder 25000)")
            except Exception as e:
                log("PROXY", f"{acc_id}: forwarder failed ({e}) — falling back to HOME IP")
                fwd_pool = None
        else:
            browser_args.append(f"--proxy-server={proxy_url}")
            log("PROXY", f"{acc_id}: login+purchase exit via {proxy_url}")

    browser = await uc.start(uc.Config(
        user_data_dir=str(profile_dir.resolve()), headless=False, browser_args=browser_args,
        sandbox=platform.system() != "Darwin",
        browser_connection_timeout=1.0, browser_connection_max_tries=30))
    try:
        tab = browser.tabs[0] if browser.tabs else await browser.get("about:blank")
        # Fingerprint spoofing can BACKFIRE on Target's Shape-protected login when the
        # spoofed Chrome build drifts from the real installed Chrome (stale _CHROME_BUILDS
        # vs a much newer Chrome = an incoherent UA/JA3 that Shape flags -> generic
        # "Something went wrong" block). RELOGIN_SKIP_FINGERPRINT=1 logs in with the real
        # browser identity (diagnostic + fallback for the new-account enrollment case).
        if os.environ.get("RELOGIN_SKIP_FINGERPRINT", "").lower() in ("1", "true", "yes"):
            log("INIT", f"{acc_id}: fingerprint SKIPPED (RELOGIN_SKIP_FINGERPRINT=1) — using real browser identity")
        else:
            await apply_identity(tab, identity)
            log("INIT", f"{acc_id}: identity applied (tz={identity['timezone']})")

        # MANUAL enrollment: open the login (through this account's IP) and let the
        # operator sign in + clear any one-time device challenge by hand, then save.
        if manual:
            await tab.get("https://www.target.com/account")
            await asyncio.sleep(3)
            if await _on_account_page_loggedin(tab):
                log("MANUAL", f"{acc_id}: already logged in")
            else:
                print(f"\n  >>> [{acc_id}] LOG IN BY HAND in the browser window "
                      f"(email {_mask(acc['username'])}), clear any code, then press ENTER here <<<", flush=True)
                await asyncio.get_event_loop().run_in_executor(None, input, "")
            ok = await _on_account_page_loggedin(tab)
            log("RESULT", f"{acc_id}: {'LOGGED IN ✅' if ok else 'NOT logged in ❌'}")
            if ok:
                cookies = await _export_cookies(tab)
                _write_session(ROOT / acc["session_path"], cookies, identity)
                auth = [h for h in AUTH_COOKIE_HINTS if any(h in c["name"] for c in cookies)]
                log("SAVE", f"{acc_id}: saved {acc['session_path']} ({len(cookies)} cookies, auth={auth})")
            return ok

        # VALIDATE-FIRST: if the saved session is still logged in, just refresh its
        # cookies and skip the full sign-out+login. Minimizes real logins (each one
        # is Shape exposure + a new-device-challenge risk); only dead accounts get a
        # full re-login. Pass force=True to always re-login from scratch.
        if not force and await _on_account_page_loggedin(tab):
            cookies = await _export_cookies(tab)
            _write_session(ROOT / acc["session_path"], cookies, identity)
            auth = [h for h in AUTH_COOKIE_HINTS if any(h in c["name"] for c in cookies)]
            log("RESULT", f"{acc_id}: already logged in ✅ (cookies refreshed, {len(cookies)} cookies, auth={auth})")
            return True

        # Retry the whole sign-out + login up to 3× — attempts are independent and
        # CDP form-submit on Target's React login is intermittently a no-op, so a
        # couple of retries take per-attempt reliability to near-certain.
        ok = False
        for attempt in range(1, 4):
            log("ACCOUNT", f"{acc_id}: attempt {attempt}/3")
            await full_signout(tab)
            ok = await login(tab, acc["username"], acc["password"])
            if ok:
                break
            log("ACCOUNT", f"{acc_id}: attempt {attempt} did not log in — retrying")
            await asyncio.sleep(2)
        log("RESULT", f"{acc_id}: {'LOGGED IN ✅' if ok else 'NOT logged in ❌ (after 3 attempts)'}")
        if ok:
            cookies = await _export_cookies(tab)
            _write_session(ROOT / acc["session_path"], cookies, identity)
            auth = [h for h in AUTH_COOKIE_HINTS if any(h in c["name"] for c in cookies)]
            log("SAVE", f"{acc_id}: saved {acc['session_path']} ({len(cookies)} cookies, auth={auth})")
        return ok
    finally:
        await asyncio.sleep(1)
        try:
            await browser.stop()
        except Exception:
            pass
        if fwd_pool is not None:
            try:
                await fwd_pool.stop_all()
            except Exception:
                pass


def _bot_is_running() -> bool:
    """True when a live app.py process is detected on this box.

    Used by the live-bot guard below. Deliberately fails OPEN: any detection
    error (no PowerShell, odd locale, permissions) returns False so a genuine
    recovery relogin is never blocked by the guard itself.
    """
    try:
        import subprocess
        # Emit ONE line per process ("<pid>\t<command line>") with any embedded
        # newlines flattened. A raw CommandLine dump splits multi-line commands
        # across output lines, which would evaluate the exclusions below against
        # the wrong fragment (a `python -c` script merely containing the text
        # "app.py" then reads as a live bot).
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine -replace "
             "'[\\r\\n]+',' ')\" }"],
            capture_output=True, text=True, timeout=15).stdout or ""
    except Exception:
        return False
    me = str(os.getpid())
    for line in out.splitlines():
        pid, _, cmd = line.partition("\t")
        if not cmd or pid.strip() == me:
            continue
        low = cmd.lower()
        # Substring match also catches test_app.py / unified_app.py — those drive
        # the same Chrome profiles, so a relogin is equally destructive there.
        if "app.py" in low and "relogin_one" not in low:
            return True
    return False


async def main():
    args = sys.argv[1:]
    if not args:
        print("usage: python relogin_one.py <account_id|all> [more ids...] [--dry]")
        return 2
    dry = "--dry" in args
    force = "--force" in args
    manual = "--manual" in args
    ids = [a for a in args if not a.startswith("--")]
    accounts = load_accounts(DEFAULT_CONFIG)
    by_id = {a["account_id"]: a for a in accounts}

    if "all" in ids:
        selected = accounts
    else:
        selected = [by_id[i] for i in ids if i in by_id]
        missing = [i for i in ids if i not in by_id]
        for m in missing:
            log("PLAN", f"no enabled account '{m}' in config")
    if not selected:
        print("no matching enabled accounts"); return 2

    log("PLAN", f"{len(selected)} account(s): " +
                ", ".join(f"{a['account_id']}({_mask(a['username'])})" for a in selected))
    if dry:
        log("PLAN", "dry run — no browser launched."); return 0

    # ── Live-bot guard (2026-08-04) ──────────────────────────────────────────
    # 20:24 on 08-04 a relogin ran against the STILL-RUNNING bot: the signout
    # cleared cookies+storage on a profile Chrome held locked, the login then
    # died at "username field NOT found", and primary's target.json was left
    # rewritten at 24KB (vs 56-57KB for the untouched accounts) — a destroyed
    # session going into the next drop window. A relogin can never safely share
    # a profile with a live app.py, so refuse instead of corrupting it.
    # Fails OPEN (any detection error => proceed) so this can't block a real
    # recovery. Override: --allow-while-running.
    if "--allow-while-running" not in args and _bot_is_running():
        log("PLAN", "REFUSING: app.py is already running — a relogin would clear "
                    "cookies on a profile the live bot holds locked and can leave "
                    "the saved session destroyed (08-04 incident). Stop the bot "
                    "first, or re-run with --allow-while-running if you are sure.")
        return 2

    results = {}
    for idx, acc in enumerate(selected):
        if idx:
            import random
            delay = random.uniform(4.0, 8.0)
            log("PLAN", f"...staggering {delay:.1f}s before next account...")
            await asyncio.sleep(delay)
        try:
            results[acc["account_id"]] = await relogin_account(acc, force=force, manual=manual)
        except Exception as e:
            log("RESULT", f"{acc['account_id']}: ERROR {type(e).__name__}: {e}")
            results[acc["account_id"]] = False

    print("\n===== SUMMARY =====")
    for aid, ok in results.items():
        print(f"  {aid:12s}: {'✅ logged in + saved' if ok else '❌ FAILED'}")
    n_ok = sum(1 for v in results.values() if v)
    print(f"  {n_ok}/{len(results)} accounts logged in.")
    return 0 if n_ok == len(results) else 1


def _arm_deadman():
    """Global deadman: hard-exit if the whole run overstays its welcome.

    The nightly wrapper runs this script SYNCHRONOUSLY before launching (and
    when relaunching) app.py. Several navigations in the login flow are
    unbounded CDP awaits — a dead websocket or a hung Shape challenge would
    park this process forever, and the bot would never come up that night
    (2026-07-05 readiness audit). Normal 3-account runs take 40-120s; the
    default 600s ceiling is generous. Exit code 86 marks the timeout so the
    wrapper log shows what happened. Override: RELOGIN_DEADMAN_S (0 disables).
    """
    try:
        budget = float(os.environ.get("RELOGIN_DEADMAN_S", "600"))
    except ValueError:
        budget = 600.0
    if budget <= 0:
        return
    import threading

    def _bang():
        try:
            sys.stderr.write(f"\n[DEADMAN] relogin_one.py exceeded {budget:.0f}s — force-exiting (code 86)\n")
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(86)

    t = threading.Timer(budget, _bang)
    t.daemon = True
    t.start()


if __name__ == "__main__":
    _arm_deadman()
    raise SystemExit(asyncio.run(main()))
