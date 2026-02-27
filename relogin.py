import asyncio
import zendriver as uc
from zendriver import cdp
import os
import json
from datetime import datetime

STORAGE_PATH = "target.json"
USER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nodriver-profile")


def cookie_to_dict(c: cdp.network.Cookie) -> dict:
    """Convert a CDP Cookie object to a plain dict for JSON storage."""
    same_site = None
    if c.same_site is not None:
        same_site = c.same_site.to_json()  # "Strict", "Lax", or "None"

    return {
        'name': c.name,
        'value': c.value,
        'domain': c.domain,
        'path': c.path,
        'expires': float(c.expires) if (c.expires is not None) else -1,
        'httpOnly': c.http_only,
        'secure': c.secure,
        'sameSite': same_site,
        'session': c.session,
    }


async def inject_cookies(tab, cookies: list):
    """Inject a list of saved cookie dicts into the browser via CDP."""
    injected = 0
    for cookie in cookies:
        try:
            same_site = None
            ss = cookie.get('sameSite')
            if ss in ('Strict', 'Lax', 'None'):
                same_site = cdp.network.CookieSameSite.from_json(ss)

            expires = None
            exp = cookie.get('expires', -1)
            if exp and exp > 0:
                expires = cdp.network.TimeSinceEpoch(exp)

            await tab.send(cdp.network.set_cookie(
                name=cookie['name'],
                value=cookie['value'],
                domain=cookie.get('domain') or None,
                path=cookie.get('path', '/'),
                secure=cookie.get('secure', False),
                http_only=cookie.get('httpOnly', False),
                same_site=same_site,
                expires=expires,
            ))
            injected += 1
        except Exception:
            pass
    return injected


async def relogin():
    os.makedirs(USER_DATA_DIR, exist_ok=True)

    browser = await uc.start(
        user_data_dir=USER_DATA_DIR,
        headless=False,
        browser_args=['--window-size=1920,1080'],
    )

    tab = browser.tabs[0] if browser.tabs else await browser.get("about:blank")

    # Pre-load existing cookies before navigation so they're sent with the first request
    if os.path.exists(STORAGE_PATH):
        print(f"Loading existing cookies from {STORAGE_PATH}...")
        try:
            with open(STORAGE_PATH, 'r') as f:
                session_data = json.load(f)
            cookies = session_data.get('cookies', [])
            if cookies:
                n = await inject_cookies(tab, cookies)
                print(f"Pre-loaded {n}/{len(cookies)} cookies")
        except Exception as e:
            print(f"Could not pre-load cookies: {e}")

    # Navigate to target.com
    print("Navigating to target.com...")
    tab = await browser.get("https://www.target.com")
    await asyncio.sleep(3)

    # Check if already logged in
    logged_in = False
    try:
        hi_elem = await tab.find("Hi,", best_match=True, timeout=3)
        if hi_elem:
            print("Already logged in!")
            logged_in = True
    except Exception:
        pass

    if not logged_in:
        print("Not logged in. Please log in manually in the browser.")
        input("After logging in, press ENTER to save session...")
        await asyncio.sleep(2)

    # Always use the current active tab after any navigation
    if browser.tabs:
        tab = browser.tabs[0]

    # Export all cookies using Storage.getCookies (the current non-deprecated method)
    print("Exporting cookies...")
    cookies_list = []
    try:
        cookies_raw = await tab.send(cdp.storage.get_cookies())
        print(f"Got {len(cookies_raw)} cookies")
        cookies_list = [cookie_to_dict(c) for c in cookies_raw]
    except Exception as e:
        print(f"Cookie export failed: {e}")

    if not cookies_list:
        print("WARNING: No cookies captured. Make sure you are logged in before pressing ENTER.")
    else:
        target_cookies = [c for c in cookies_list if 'target.com' in c.get('domain', '')]
        session_count = sum(1 for c in cookies_list if c.get('session', False))
        print(f"  Total: {len(cookies_list)} | Target.com: {len(target_cookies)} | Session cookies: {session_count}")

        # Show auth-related cookies to confirm login was captured
        auth_names = ['accessToken', 'idToken', 'visitorId', 'TGTSES', 'sapphire', 'WC_']
        found_auth = [c['name'] for c in cookies_list if any(a in c['name'] for a in auth_names)]
        if found_auth:
            print(f"  Auth cookies captured: {found_auth}")
        else:
            print("  WARNING: No auth cookies found - you may not be logged in")

    storage_state = {
        'cookies': cookies_list,
        'saved_at': datetime.now().isoformat(),
    }

    with open(STORAGE_PATH, 'w') as f:
        json.dump(storage_state, f, indent=2)

    print(f"\nSession saved to {STORAGE_PATH} ({len(cookies_list)} cookies)")
    print("Browser staying open. Press Ctrl+C to close.")
    await asyncio.Event().wait()


asyncio.run(relogin())
