#!/usr/bin/env python3
"""harvest_accounts.py — batch login + session harvest/refresh for N Target accounts.

This is `relogin.py` generalised from one account to many. It reads
config/target_accounts.json, and for each account launches its OWN headful Chrome
(own profile dir, own optional proxy IP, own STABLE per-account device
fingerprint), brings the session to a logged-in state, and writes that account's
cookies to its `session_path` (target.json / target-2.json / ...). The existing
WorkerPool then consumes those files when you set TARGET_WORKER_POOL_SIZE=N.

It also MANAGES re-login: on each run it first tries a silent refresh (just
navigate while already logged in and re-harvest), and only falls back to a full
credential login when the session is actually dead — so you stop hand-running
relogin.py before every drop.

MODES
-----
  --dry-run   Validate config + show the per-account plan + fingerprint
              differentiation. NO browser, NO network. Safe anywhere.
  --check     Report each account's saved-session health (auth cookies present,
              age). Reads the session files only. NO browser, NO network.
  --manual    Launch each account headful and PAUSE for you to log in by hand
              (like relogin.py), then harvest. Use for first-time enrolment /
              when a new-device challenge is expected.
  --auto      Launch each account headful, try silent refresh, else auto-fill the
              login form with the stored credentials (2FA assumed OFF), then
              harvest. Reports any account that hits a challenge so you can finish
              it with --manual --account <id>.

  --account <id>   Restrict to a single account_id (any mode).
  --config <path>  Override config path (default config/target_accounts.json).

SAFETY: --manual / --auto drive real logins against Target. Run them yourself,
present, so you can clear any one-time new-device challenge. --dry-run / --check
touch nothing live.

Login-flow selectors adapted from src/utils/target_login.py (the proven, manual
flow), parameterised per account. Cookie export adapted from relogin.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "target_accounts.json"

# Cookies that prove a logged-in Target session (mirrors session_manager.py).
AUTH_COOKIE_HINTS = ["accessToken", "idToken", "refreshToken", "login-session"]

# Make `src` importable for account_identity.
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_accounts(config_path: Path) -> List[Dict[str, Any]]:
    """Load and lightly validate the accounts config. Returns enabled accounts."""
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found: {config_path}\n"
            f"Copy config/target_accounts.example.json -> {config_path.name} and fill it in."
        )
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    cleaned: List[Dict[str, Any]] = []
    seen_ids, seen_sessions, seen_profiles = set(), set(), set()
    for i, acc in enumerate(accounts):
        if not isinstance(acc, dict):
            continue
        if acc.get("enabled") is False:
            continue
        acc_id = str(acc.get("account_id") or f"account-{i + 1}")
        session_path = str(acc.get("session_path") or (f"target.json" if i == 0 else f"target-{i + 1}.json"))
        profile_dir = str(acc.get("profile_dir") or ("nodriver-profile" if i == 0 else f"nodriver-profile-{i + 1}"))

        # Collision guard — two accounts sharing a session file or profile dir
        # would cross-pollinate cookies and silently corrupt both.
        for key, bag, label in (
            (acc_id, seen_ids, "account_id"),
            (session_path, seen_sessions, "session_path"),
            (profile_dir, seen_profiles, "profile_dir"),
        ):
            if key in bag:
                raise ValueError(f"Duplicate {label} '{key}' in {config_path.name} — each must be unique.")
            bag.add(key)

        cleaned.append({
            "account_id": acc_id,
            "username": acc.get("username", ""),
            "password": acc.get("password", ""),
            "session_path": session_path,
            "profile_dir": profile_dir,
            "proxy_url": (acc.get("proxy_url") or "").strip(),
            "timezone": (acc.get("timezone") or "").strip(),
            "notes": acc.get("notes", ""),
        })
    return cleaned


# --------------------------------------------------------------------------- #
# Offline session-health (no browser)
# --------------------------------------------------------------------------- #
def _session_health(session_path: Path) -> Dict[str, Any]:
    """Inspect a harvested session file. Pure/offline."""
    out: Dict[str, Any] = {"exists": False, "cookie_count": 0, "auth_cookies": [], "age_hours": None, "saved_at": None}
    if not session_path.exists():
        return out
    out["exists"] = True
    try:
        with open(session_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        out["error"] = f"unreadable: {e}"
        return out
    cookies = data.get("cookies", []) or []
    out["cookie_count"] = len(cookies)
    names = {str(c.get("name", "")) for c in cookies}
    out["auth_cookies"] = [h for h in AUTH_COOKIE_HINTS if any(h in n for n in names)]
    saved_at = data.get("saved_at")
    out["saved_at"] = saved_at
    if saved_at:
        try:
            dt = datetime.fromisoformat(saved_at)
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            out["age_hours"] = round((now - dt).total_seconds() / 3600.0, 1)
        except Exception:
            pass
    return out


def _health_verdict(h: Dict[str, Any]) -> str:
    if not h["exists"]:
        return "MISSING (never harvested)"
    if h.get("error"):
        return f"ERROR ({h['error']})"
    if not h["auth_cookies"]:
        return "LOGGED-OUT (no auth cookies — needs login)"
    age = h.get("age_hours")
    age_s = f"{age}h old" if age is not None else "age unknown"
    return f"OK ({len(h['auth_cookies'])}/{len(AUTH_COOKIE_HINTS)} auth cookies, {h['cookie_count']} total, {age_s})"


# --------------------------------------------------------------------------- #
# Cookie export (adapted from relogin.py)
# --------------------------------------------------------------------------- #
def _cookie_to_dict(c) -> Dict[str, Any]:
    same_site = None
    if getattr(c, "same_site", None) is not None:
        try:
            same_site = c.same_site.to_json()
        except Exception:
            same_site = None
    return {
        "name": c.name,
        "value": c.value,
        "domain": c.domain,
        "path": c.path,
        "expires": float(c.expires) if getattr(c, "expires", None) is not None else -1,
        "httpOnly": getattr(c, "http_only", False),
        "secure": getattr(c, "secure", False),
        "sameSite": same_site,
        "session": getattr(c, "session", False),
    }


async def _export_cookies(tab) -> List[Dict[str, Any]]:
    from zendriver import cdp
    try:
        raw = await asyncio.wait_for(tab.send(cdp.storage.get_cookies()), timeout=10.0)
        return [_cookie_to_dict(c) for c in raw]
    except Exception as e:
        print(f"    [WARN] cookie export failed: {e}")
        return []


def _write_session(session_path: Path, cookies: List[Dict[str, Any]], identity: Dict[str, Any]) -> None:
    """Write the harvested session, preserving the relogin.py format. Refuses to
    clobber a good file with an empty cookie list (mirrors the SessionManager guard)."""
    if not cookies:
        print(f"    [SKIP] 0 cookies captured — NOT overwriting {session_path.name} (clobber guard).")
        return
    payload = {
        "cookies": cookies,
        "saved_at": datetime.now(_tz.utc).isoformat(),
        "account_id": identity.get("account_id"),
        "fingerprint": {
            "user_agent": identity.get("user_agent"),
            "viewport": identity.get("viewport"),
            "timezone": identity.get("timezone"),
            "locale": identity.get("locale"),
        },
    }
    tmp = session_path.with_suffix(session_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, session_path)  # atomic
    auth = [h for h in AUTH_COOKIE_HINTS if any(h in c["name"] for c in cookies)]
    print(f"    [OK] wrote {session_path.name}: {len(cookies)} cookies, auth={auth or 'NONE — check login!'}")


# --------------------------------------------------------------------------- #
# Login flow (parameterised; adapted from src/utils/target_login.py)
# --------------------------------------------------------------------------- #
async def _is_logged_in(tab) -> bool:
    """Authoritative login check: the /account page REDIRECTS to login when the
    session isn't authenticated. The old check (AccountLink element / "Hi," text)
    false-positived — those exist when logged OUT too ("Hi, sign in"), which made
    Tier-1 silently skip login and harvest dead/guest cookies. Mirrors the proven
    SessionManager._trigger_token_refresh redirect test."""
    try:
        await tab.get("https://www.target.com/account")
        await asyncio.sleep(2.5)
        url = (getattr(tab, 'url', '') or '').lower()
        if any(x in url for x in ('login', 'signin', 'sign-in', '/guest')):
            return False
        if '/account' in url:
            return True
        # Ambiguous URL (didn't redirect to login, not clearly on /account) —
        # confirm with an account-only element before trusting it.
        for sel in ['[data-test="accountTitle"]', '[data-test="@web/AccountLink"]']:
            try:
                if await tab.select(sel, timeout=2):
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return False


async def _looks_like_challenge(tab) -> bool:
    """Heuristic: did login land on a new-device / verification step-up?"""
    for text in ["Verify", "verification code", "enter the code", "we don't recognize", "Check your email"]:
        try:
            if await tab.find(text, best_match=True, timeout=1):
                return True
        except Exception:
            continue
    return False


async def _type_humanish(field, text: str) -> None:
    for i, ch in enumerate(text):
        await field.send_keys(ch)
        await asyncio.sleep(random.uniform(0.05, 0.16))
        if i and i % 3 == 0:
            await asyncio.sleep(random.uniform(0.1, 0.3))


async def _click_button_by_text(tab, *texts: str) -> bool:
    """Click the first VISIBLE button/link whose text or aria-label contains any
    of `texts`, via in-page JS. Critically, it only considers real interactive
    elements — never <script>/<style> nodes — which is what broke the old
    `tab.find("Sign in")` path (it matched text inside the fullstory <script> and
    then failed to click a node with no layout box)."""
    import json as _json
    want = _json.dumps([t.lower() for t in texts])
    js = """(() => {
      const want = __WANT__;
      const els = Array.from(document.querySelectorAll(
        'button, a[role=button], [role=button], input[type=submit], input[type=button]'));
      for (const e of els) {
        const txt = ((e.innerText||'') + ' ' + (e.value||'') + ' ' +
                     (e.getAttribute&&e.getAttribute('aria-label')||'')).toLowerCase();
        if (e.offsetParent !== null && want.some(w => w && txt.includes(w))) { e.click(); return true; }
      }
      return false;
    })()""".replace("__WANT__", want)
    try:
        return bool(await tab.evaluate(js))
    except Exception:
        return False


async def _find_input(tab, selectors, timeout=4):
    for sel in selectors:
        try:
            el = await tab.select(sel, timeout=timeout)
            if el:
                return el
        except Exception:
            continue
    return None


async def _perform_login(tab, email: str, password: str) -> bool:
    """Drive Target's sign-in form with `email`/`password`. Returns logged-in bool.

    Navigates straight to the login page (the /account redirect) and drives the
    form with input/button SELECTORS + an in-page JS button-clicker — no text-based
    element search (which matched <script> nodes and broke the click). Adaptive to
    both the username-first flow and the password-direct flow (remembered username).
    """
    # Exact selectors confirmed by inspecting the live login DOM (2026-06-23):
    #   choice screen (remembered username): DIV#password role=button "Enter your
    #   password" (also #passkey, #otp "Get a code" — we want #password, NOT otp).
    #   keep-me-signed-in: input#keepMeSignedIn (defaults UNCHECKED, kmsi_default=false).
    async def _ensure_kmsi():
        # The checkbox is custom-styled (the real <input> is visually hidden), so a
        # plain input click can miss. Try the label, then the input, then a JS
        # click+change — verifying .checked after each.
        try:
            cb = await tab.select('#keepMeSignedIn', timeout=3)
            if not cb:
                print("    [KMSI] checkbox not found on this screen")
                return
            if await tab.evaluate("el => !!el.checked", cb):
                print("    [KMSI] already enabled")
                return
            # 1) click the label that toggles it
            try:
                lbl = await tab.select('label[for="keepMeSignedIn"]', timeout=1)
                if lbl:
                    await lbl.click()
            except Exception:
                pass
            # 2) native input click
            if not await tab.evaluate("el => !!el.checked", cb):
                try:
                    await cb.click()
                except Exception:
                    pass
            # 3) JS click + forced state + change event (React)
            if not await tab.evaluate("el => !!el.checked", cb):
                await tab.evaluate("""(()=>{const e=document.querySelector('#keepMeSignedIn');
                  if(!e)return;e.click();
                  if(!e.checked){e.checked=true;
                    e.dispatchEvent(new Event('click',{bubbles:true}));
                    e.dispatchEvent(new Event('change',{bubbles:true}));}})()""")
            ok = await tab.evaluate("el => !!el.checked", cb)
            print(f"    [KMSI] {'enabled' if ok else 'could NOT enable'}")
        except Exception as e:
            print(f"    [KMSI] error: {e}")

    async def _pw_present(timeout=2):
        return bool(await _find_input(tab, ('input[type="password"]', '#password[type="password"]',
                                            '[data-test="login-password"]'), timeout=timeout))

    async def _reveal_password_field() -> bool:
        """Click the 'Enter your password' choice (DIV#password role=button) and
        VERIFY the password input appears. Native click works but can be lost to a
        React re-render, so retry via JS .click() then a dispatched pointer
        sequence. (Confirmed working order by live DOM probing, 2026-06-23.)"""
        if await _pw_present():
            return True
        # 1) native zendriver click
        try:
            opt = await tab.select('#password', timeout=3)
            if opt and not await tab.evaluate("el => el.tagName === 'INPUT'", opt):
                await opt.click()
        except Exception:
            pass
        if await _pw_present(timeout=3):
            return True
        # 2) in-page JS click
        try:
            await tab.evaluate("(()=>{const e=document.querySelector('#password');"
                               "if(e&&e.tagName!=='INPUT'){e.click();return true;}return false;})()")
        except Exception:
            pass
        if await _pw_present(timeout=3):
            return True
        # 3) full dispatched pointer+mouse sequence
        try:
            await tab.evaluate("""(()=>{const e=document.querySelector('#password');
              if(!e||e.tagName==='INPUT')return false;const r=e.getBoundingClientRect();
              const o={bubbles:true,cancelable:true,composed:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2};
              for(const t of ['pointerdown','mousedown','pointerup','mouseup','click'])e.dispatchEvent(new MouseEvent(t,o));
              return true;})()""")
        except Exception:
            pass
        if await _pw_present(timeout=3):
            return True
        # 4) last resort: text-based clicker
        await _click_button_by_text(tab, "enter your password", "use password", "password instead")
        return await _pw_present(timeout=3)

    async def _advanced_past_password() -> bool:
        # We've left the password screen once the password input is gone.
        try:
            still = await tab.select('input[type="password"]', timeout=1)
            return not bool(still)
        except Exception:
            return True

    async def _submit_password() -> None:
        # The submit button can be disabled until React sees a real change event,
        # and a native click can be a no-op. Settle the field's React state, then
        # try Enter -> JS click -> native click, verifying the page advances.
        try:
            await tab.evaluate("""(()=>{const e=document.querySelector('input[type=password]');
              if(e){['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));}})()""")
        except Exception:
            pass
        await asyncio.sleep(0.4)
        for method in ('enter', 'js', 'native'):
            try:
                if method == 'enter':
                    pw = await tab.select('input[type="password"]', timeout=1)
                    if pw:
                        await pw.send_keys("\r")
                elif method == 'js':
                    await tab.evaluate("(()=>{const b=document.querySelector('button[type=submit]');"
                                       "if(b){b.click();return true;}return false;})()")
                else:
                    b = await tab.select('button[type="submit"]', timeout=2)
                    if b:
                        await b.click()
            except Exception:
                pass
            await asyncio.sleep(3)
            if await _advanced_past_password():
                print(f"    [LOGIN] password submitted via {method}")
                return
        print("    [WARN] submit did not advance past the password screen")

    async def _enter_username() -> bool:
        """Type OUR config email into the username field. Returns True if entered."""
        email_field = await _find_input(
            tab, ('input[name="username"]', 'input#username', 'input[type="email"]',
                  'input[autocomplete="username"]', 'input[name="email"]'), timeout=6)
        if not email_field:
            # A remembered-account choice screen may be blocking username entry —
            # try to switch to a different account, then look again.
            await _click_button_by_text(tab, "different account", "not you", "use a different",
                                        "sign in to a different", "try another way")
            await asyncio.sleep(2.5)
            email_field = await _find_input(
                tab, ('input[name="username"]', 'input#username', 'input[type="email"]',
                      'input[autocomplete="username"]', 'input[name="email"]'), timeout=4)
        if not email_field:
            return False
        try:
            await tab.evaluate(
                "(el)=>{el.focus();try{el.select();}catch(e){};el.value='';"
                "el.dispatchEvent(new Event('input',{bubbles:true}));}", email_field)
        except Exception:
            pass
        await email_field.click()
        await asyncio.sleep(random.uniform(0.2, 0.4))
        await _type_humanish(email_field, email)
        await asyncio.sleep(random.uniform(0.3, 0.6))
        return True

    try:
        # CRITICAL (user requirement): always sign in with OUR config email+password
        # as a matched pair — never trust the profile's remembered username, which
        # may be a different/stale account (that mismatch typed the right password
        # against the wrong email). So clear any remembered account, then drive the
        # USERNAME-FIRST flow with this account's email.
        try:
            await tab.get("https://www.target.com")
            await asyncio.sleep(1.5)
            try:
                await tab.evaluate("(()=>{try{localStorage.clear();sessionStorage.clear();}catch(e){}})()")
            except Exception:
                pass
            try:
                import zendriver as _uc
                await tab.send(_uc.cdp.network.clear_browser_cookies())
            except Exception:
                pass
        except Exception:
            pass

        # Username-request login screen (no remembered username).
        await tab.get("https://www.target.com/login?client_id=ecom-web-1.0.0"
                      "&ui_namespace=ui-default&actions=create_session_request_username")
        await asyncio.sleep(3.5)

        if not await _enter_username():
            print("    [ERR] could not reach username entry (remembered account blocking) — finish with --manual.")
            return False

        # Robust CONTINUE off the username screen (same disabled-button/no-op issue
        # as the password submit): try native click -> Enter -> JS, verifying we
        # actually left the username screen.
        async def _username_present():
            try:
                e = await tab.select('input[name="username"], input[type="email"], input#username', timeout=1)
                return bool(e)
            except Exception:
                return False
        try:
            await tab.evaluate("""(()=>{const e=document.querySelector(
              'input[name=username],input[type=email],input#username');
              if(e){['input','change','blur'].forEach(t=>e.dispatchEvent(new Event(t,{bubbles:true})));}})()""")
        except Exception:
            pass
        await asyncio.sleep(0.3)
        for _m in ('native', 'enter', 'js'):
            try:
                if _m == 'native':
                    b = await tab.select('button[type="submit"]', timeout=2)
                    if b:
                        await b.click()
                elif _m == 'enter':
                    ef = await tab.select('input[name="username"], input[type="email"], input#username', timeout=1)
                    if ef:
                        await ef.send_keys("\r")
                else:
                    await tab.evaluate("(()=>{const b=document.querySelector('button[type=submit]');"
                                       "if(b){b.click();return true;}return false;})()")
            except Exception:
                pass
            await asyncio.sleep(3)
            if not await _username_present():
                print(f"    [LOGIN] username submitted via {_m}")
                break

        # Password field, or a choice screen (passkey / get a code / enter password).
        pw_field = await _find_input(tab, ('input[type="password"]',), timeout=3)
        if not pw_field and await _reveal_password_field():
            pw_field = await _find_input(tab, ('input[type="password"]',), timeout=4)

        if not pw_field:
            if await _looks_like_challenge(tab):
                print("    [CHALLENGE] verification step-up before password — finish with --manual.")
            else:
                print("    [ERR] password field not found (unexpected login screen)")
            return False

        # Password screen — set keep-me-signed-in, type OUR password, submit.
        await _ensure_kmsi()
        await pw_field.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await _type_humanish(pw_field, password)
        await asyncio.sleep(random.uniform(0.5, 1.0))
        await _submit_password()
        await asyncio.sleep(6)

        if await _is_logged_in(tab):
            return True
        if await _looks_like_challenge(tab):
            print("    [CHALLENGE] new-device / verification step-up after password — finish with --manual.")
            return False
        print("    [ERR] not logged in after password submit (wrong password? unexpected screen)")
        return False
    except Exception as e:
        print(f"    [ERR] login flow: {e}")
        return False


# --------------------------------------------------------------------------- #
# Per-account harvest (launches a browser)
# --------------------------------------------------------------------------- #
def _resolve_proxy_arg(proxy_url: str) -> Optional[str]:
    """Return a Chrome --proxy-server value, or None. Bright-Data-style auth URLs
    can't be passed to Chrome directly (no inline auth) — those need the local
    forwarder, which v1 does not wire up; warn and fall back to home IP."""
    if not proxy_url:
        return None
    low = proxy_url.lower()
    if "@" in proxy_url or "superproxy" in low or "brd." in low:
        print(f"    [WARN] proxy '{proxy_url[:30]}...' looks auth'd (Bright Data). "
              f"v1 has no forwarder wired — harvesting on HOME IP. See docs/MULTI_ACCOUNT.md.")
        return None
    return f"--proxy-server={proxy_url}"


async def _harvest_one(acc: Dict[str, Any], mode: str, chrome_proxy: Optional[str] = None) -> Dict[str, Any]:
    """Launch one account's Chrome, bring it to logged-in, harvest. mode in {manual, auto}.

    chrome_proxy: pre-resolved Chrome --proxy-server value (e.g. 127.0.0.1:24001
    fronting this account's BD IP via the local forwarder). When set, login exits
    that SAME IP the purchase path uses — so the session's cookies are minted on
    the IP they'll be used from (no login-IP≠purchase-IP mismatch). None = fall
    back to a plain proxy (if any) or the home IP.
    """
    import platform as _platform
    import zendriver as uc
    from src.session.account_identity import build_identity, apply_identity

    acc_id = acc["account_id"]
    session_path = ROOT / acc["session_path"]
    profile_dir = ROOT / acc["profile_dir"]
    profile_dir.mkdir(parents=True, exist_ok=True)
    identity = build_identity(acc_id, timezone=acc["timezone"] or None)

    print(f"\n=== [{acc_id}] profile={acc['profile_dir']} session={acc['session_path']} ===")
    print(f"    fingerprint: {identity['user_agent'][:60]}... tz={identity['timezone']} "
          f"vp={identity['viewport']['width']}x{identity['viewport']['height']}")

    browser_args = ["--window-size=1920,1080"]
    # Prefer the forwarder-backed local address (BD IP); else a plain proxy; else home.
    proxy_arg = f"--proxy-server={chrome_proxy}" if chrome_proxy else _resolve_proxy_arg(acc["proxy_url"])
    if proxy_arg:
        browser_args.append(proxy_arg)
        print(f"    proxy: {proxy_arg}{' (forwarder→BD IP)' if chrome_proxy else ''}")

    result = {"account_id": acc_id, "logged_in": False, "harvested": False, "needs_manual": False}
    browser = None
    try:
        cfg = uc.Config(
            user_data_dir=str(profile_dir.resolve()),
            headless=False,
            browser_args=browser_args,
            sandbox=_platform.system() != "Darwin",
            browser_connection_timeout=1.0,
            browser_connection_max_tries=30,
        )
        browser = await uc.start(cfg)
        tab = browser.tabs[0] if browser.tabs else await browser.get("about:blank")

        # Apply the per-account fingerprint BEFORE any Target navigation.
        applied = await apply_identity(tab, identity)
        print(f"    identity applied: {applied}")

        # Tier 1 — silent refresh: are we already logged in (persistent profile)?
        await tab.get("https://www.target.com")
        await asyncio.sleep(3)
        if await _is_logged_in(tab):
            print("    [TIER1] already logged in — silent refresh, harvesting.")
            result["logged_in"] = True
        elif mode == "manual":
            print("    [TIER2/manual] not logged in. Log in by hand in the browser window.")
            await asyncio.get_event_loop().run_in_executor(None, input, "    Press ENTER after you are logged in...")
            result["logged_in"] = await _is_logged_in(tab)
        else:  # auto
            print("    [TIER2/auto] not logged in. Auto-filling credentials...")
            if not acc["username"] or not acc["password"] or acc["password"] == "REPLACE_ME":
                print("    [ERR] missing/placeholder credentials — skipping.")
            else:
                result["logged_in"] = await _perform_login(tab, acc["username"], acc["password"])
                if not result["logged_in"] and await _looks_like_challenge(tab):
                    result["needs_manual"] = True

        if result["logged_in"]:
            cookies = await _export_cookies(tab)
            _write_session(session_path, cookies, identity)
            result["harvested"] = bool(cookies)
        else:
            print("    [FAIL] not logged in — session NOT written.")
    except Exception as e:
        print(f"    [ERR] harvest failed: {e}")
        result["error"] = str(e)
    finally:
        if browser is not None:
            try:
                await browser.stop()
            except Exception:
                pass
    return result


# --------------------------------------------------------------------------- #
# Offline reporting
# --------------------------------------------------------------------------- #
def cmd_dry_run(accounts: List[Dict[str, Any]]) -> None:
    from src.session.account_identity import build_identity, identity_signature
    print(f"\nDRY RUN — {len(accounts)} enabled account(s). No browser, no network.\n")
    sigs = {}
    for acc in accounts:
        ident = build_identity(acc["account_id"], timezone=acc["timezone"] or None)
        sig = identity_signature(ident)
        sigs.setdefault(sig, []).append(acc["account_id"])
        creds = "set" if (acc["username"] and acc["password"] and acc["password"] != "REPLACE_ME") else "MISSING/placeholder"
        proxy = acc["proxy_url"] or "(home IP)"
        print(f"  {acc['account_id']:10s} -> {acc['session_path']:16s} | {acc['profile_dir']:20s} | "
              f"creds={creds:18s} | proxy={proxy}")
        print(f"             fingerprint: Chrome/{ident['ua_full_version']} {ident['platform']} "
              f"{ident['timezone']} {ident['viewport']['width']}x{ident['viewport']['height']} "
              f"hw={ident['hardware_concurrency']} canvas={ident['canvas_seed']}")
    dupes = {s: ids for s, ids in sigs.items() if len(ids) > 1}
    print()
    if dupes:
        print(f"  [!] FINGERPRINT COLLISION: {dupes} — accounts would look like one device. Change an account_id.")
    else:
        print("  [OK] all accounts have distinct device fingerprints.")
    print("\nNext: run `python harvest_accounts.py --manual` (present) to log accounts in.")


def cmd_check(accounts: List[Dict[str, Any]]) -> None:
    print(f"\nSESSION CHECK — {len(accounts)} enabled account(s). Reading session files only.\n")
    need = []
    for acc in accounts:
        h = _session_health(ROOT / acc["session_path"])
        verdict = _health_verdict(h)
        print(f"  {acc['account_id']:10s} {acc['session_path']:16s} : {verdict}")
        if not h["exists"] or not h["auth_cookies"]:
            need.append(acc["account_id"])
    print()
    if need:
        print(f"  Needs login: {need}  ->  python harvest_accounts.py --manual --account <id>")
    else:
        print("  All sessions look logged-in.")


def _needs_forwarder(url: str) -> bool:
    """A BD-style auth proxy URL that Chrome can't take inline (needs the forwarder)."""
    low = url.lower()
    return ("@" in url) or ("superproxy" in low) or ("brd." in low)


# Harvester forwarder port band. Distinct from the purchase path's 23000+ and the
# stock stack's 22000+ so the three never collide even if run concurrently.
_HARVEST_FORWARDER_PORT_BASE = 24000


async def _setup_harvest_forwarders(accounts: List[Dict[str, Any]]):
    """Start a local CONNECT forwarder for each account whose proxy_url is a BD
    auth URL, so harvest logs in through the SAME exit IP the purchase path uses.
    Returns (pool_or_None, {account_id: '127.0.0.1:port'}). Plain/empty proxies
    are left for _harvest_one to handle (plain proxy or home IP)."""
    needing = [a for a in accounts if a["proxy_url"] and _needs_forwarder(a["proxy_url"])]
    if not needing:
        return None, {}
    try:
        from src.proxy.local_forwarder import ForwarderPool
    except Exception as e:
        print(f"  [FORWARDER] [WARN] import failed ({e}); harvesting on HOME IP.")
        return None, {}

    pool = ForwarderPool()
    addr_map: Dict[str, str] = {}
    for i, a in enumerate(needing):
        port = _HARVEST_FORWARDER_PORT_BASE + i + 1
        try:
            pool.add_upstream(a["proxy_url"], port)
            addr_map[a["account_id"]] = f"127.0.0.1:{port}"
        except Exception as e:
            print(f"  [FORWARDER] [WARN] {a['account_id']} proxy unparseable ({e}); HOME IP.")
    if not pool.upstreams:
        return None, {}
    await pool.start_all()
    print(f"  [FORWARDER] {len(pool.upstreams)} account-IP forwarder(s) live "
          f"(login exits same IP as purchase).")
    return pool, addr_map


async def cmd_harvest(accounts: List[Dict[str, Any]], mode: str) -> None:
    print(f"\nHARVEST ({mode.upper()}) — {len(accounts)} account(s). Staggered launches.\n")
    fwd_pool, addr_map = await _setup_harvest_forwarders(accounts)
    results = []
    try:
        for idx, acc in enumerate(accounts):
            if idx:  # stagger so N logins are not a synchronized burst from one ASN
                delay = random.uniform(4.0, 9.0)
                print(f"\n  ...staggering {delay:.1f}s before next account...")
                await asyncio.sleep(delay)
            results.append(await _harvest_one(acc, mode, chrome_proxy=addr_map.get(acc["account_id"])))
    finally:
        if fwd_pool is not None:
            try:
                await fwd_pool.stop_all()
            except Exception:
                pass

    print("\n===== SUMMARY =====")
    for r in results:
        state = "harvested" if r["harvested"] else ("NEEDS-MANUAL" if r.get("needs_manual") else "FAILED")
        print(f"  {r['account_id']:10s}: {state}")
    manual = [r["account_id"] for r in results if r.get("needs_manual")]
    if manual:
        print(f"\n  Finish these by hand: python harvest_accounts.py --manual --account {' '.join(manual)}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Batch login + session harvest for N Target accounts.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="Validate config + plan + fingerprints. No browser.")
    g.add_argument("--check", action="store_true", help="Report saved-session health. No browser.")
    g.add_argument("--manual", action="store_true", help="Headful; you log in by hand, then harvest.")
    g.add_argument("--auto", action="store_true", help="Headful; auto-fill credentials, then harvest.")
    p.add_argument("--account", nargs="+", default=None, help="Restrict to these account_id(s).")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to accounts config JSON.")
    args = p.parse_args()

    try:
        accounts = load_accounts(Path(args.config))
    except (FileNotFoundError, ValueError) as e:
        print(f"[CONFIG ERROR] {e}")
        return 2
    if args.account:
        wanted = set(args.account)
        accounts = [a for a in accounts if a["account_id"] in wanted]
        if not accounts:
            print(f"[ERROR] no enabled accounts match {sorted(wanted)}")
            return 2
    if not accounts:
        print("[ERROR] no enabled accounts in config.")
        return 2

    if args.check:
        cmd_check(accounts)
    elif args.manual or args.auto:
        asyncio.run(cmd_harvest(accounts, "manual" if args.manual else "auto"))
    else:
        # Default to the safe dry-run when no live mode is given.
        cmd_dry_run(accounts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
