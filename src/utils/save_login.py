import asyncio
import zendriver as uc
import os
import json
from datetime import datetime

STORAGE_PATH = "target.json"

async def save_login():
    browser = await uc.start(
        user_data_dir="./nodriver-profile",
        headless=False,
    )

    tab = browser.tabs[0] if browser.tabs else await browser.get("about:blank")

    # Load existing cookies if session file exists
    if os.path.exists(STORAGE_PATH):
        print("Loading existing session cookies...")
        try:
            with open(STORAGE_PATH, 'r') as f:
                session_data = json.load(f)
            cookies = session_data.get('cookies', [])
            if cookies:
                for cookie in cookies:
                    try:
                        await tab.send("Network.setCookie", **{
                            k: v for k, v in cookie.items()
                            if k in ('name', 'value', 'domain', 'path', 'expires',
                                     'httpOnly', 'secure', 'sameSite')
                        })
                    except Exception:
                        pass
                print(f"Loaded {len(cookies)} cookies from session file")
        except Exception as e:
            print(f"Could not load existing cookies: {e}")
    else:
        print("No existing session, starting fresh...")

    tab = await browser.get("https://www.target.com")

    # Check if already logged in
    await asyncio.sleep(3)
    try:
        elem = await tab.select('[data-test="@web/AccountLink"]', timeout=1)
        if elem:
            print("Already logged in!")
            print("Updating session file...")
        else:
            raise Exception("Not logged in")
    except Exception:
        print("Not logged in. Please log in manually.")
        input("After logging in, press ENTER...")

    # Enable network to get cookies
    try:
        await tab.send("Network.enable")
    except Exception:
        pass

    # Get all cookies via CDP
    try:
        result = await tab.send("Network.getAllCookies")
        if hasattr(result, 'cookies'):
            raw = result.cookies
            cookies_list = []
            for c in raw:
                cookies_list.append({
                    'name': str(c.name),
                    'value': str(c.value),
                    'domain': str(getattr(c, 'domain', '')),
                    'path': str(getattr(c, 'path', '/')),
                    'expires': float(getattr(c, 'expires', -1)) if getattr(c, 'expires', None) is not None else -1,
                    'httpOnly': bool(getattr(c, 'http_only', False)),
                    'secure': bool(getattr(c, 'secure', False)),
                    'sameSite': str(getattr(c, 'same_site', 'None')).replace('CookieSameSite.', '') if getattr(c, 'same_site', None) else 'None',
                })
        elif isinstance(result, dict):
            cookies_list = result.get('cookies', [])
        else:
            cookies_list = []
    except Exception as e:
        print(f"Could not get cookies: {e}")
        cookies_list = []

    storage_state = {
        'cookies': cookies_list,
        'saved_at': datetime.now().isoformat(),
    }

    with open(STORAGE_PATH, 'w') as f:
        json.dump(storage_state, f, indent=2)

    print(f"Session saved to {STORAGE_PATH}")
    print(f"Saved {len(cookies_list)} cookies")

    # Keep browser open
    print("\nBrowser staying open. Press Ctrl+C to close.")
    await asyncio.Event().wait()

asyncio.run(save_login())
