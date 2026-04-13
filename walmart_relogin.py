"""
Walmart manual login helper — uses zendriver (same as Target bot).

Run this when:
  - First time setting up the Walmart bot
  - Session expired / getting login prompts during checkout

Usage:
    python walmart_relogin.py

What it does:
  1. Opens real Chrome (same browser Target uses — known to work)
  2. Navigates to walmart.com
  3. Waits for you to log in manually
  4. Saves cookies to walmart-profile/cookies.json
"""

import asyncio
import json
import os
from pathlib import Path

COOKIES_FILE = "walmart-profile/cookies.json"
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "walmart-profile-login")


async def walmart_relogin():
    try:
        import zendriver as uc
    except ImportError:
        print("ERROR: zendriver is not installed. Run: pip install zendriver")
        return

    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    Path(COOKIES_FILE).parent.mkdir(parents=True, exist_ok=True)

    print("=" * 55)
    print("  WALMART SESSION LOGIN")
    print("=" * 55)
    print("Opening Chrome browser...")

    config = uc.Config(
        user_data_dir=PROFILE_DIR,
        headless=False,
        browser_args=["--window-size=1920,1080"],
        browser_connection_timeout=1.0,
        browser_connection_max_tries=30,
    )
    browser = await uc.start(config)
    tab = await browser.get("https://www.walmart.com")

    print("Navigated to walmart.com")

    # First, try to log out if already logged in
    print("Checking for existing session...")
    try:
        content = await tab.get_content()
        if any(x in content for x in ['"isLoggedIn":true', 'account/logout', '"type":"REGISTERED"']):
            print("Existing session detected — logging out first...")
            # Navigate to logout
            await tab.get("https://www.walmart.com/account/logout")
            await asyncio.sleep(3)
            # Go back to home
            await tab.get("https://www.walmart.com")
            await asyncio.sleep(2)
    except Exception:
        pass

    # Now prompt for login
    print()
    print("Please log in manually in the browser window.")
    print("  1. Click 'Sign In' on walmart.com")
    print("  2. Enter your email and password")
    print("  3. Complete any CAPTCHA or 2FA if prompted")
    print("  4. Wait until your name appears in the top right")
    print()
    input("Once fully logged in, press ENTER here to save cookies...")
    await asyncio.sleep(2)

    # Extract cookies via CDP
    print("Saving cookies...")
    try:
        all_cookies = await browser.cookies.get_all()
        cookie_list = []
        for c in all_cookies:
            cookie_list.append({
                "name": c.name,
                "value": c.value,
                "domain": c.domain,
                "path": c.path,
                "secure": c.secure,
                "httpOnly": c.http_only,
                "sameSite": str(c.same_site) if c.same_site else "None",
                "expires": float(c.expires) if c.expires else -1,
            })

        walmart_cookies = [c for c in cookie_list if "walmart" in c.get("domain", "")]
        print(f"  Total: {len(cookie_list)} | Walmart.com: {len(walmart_cookies)}")

        auth_names = ["auth", "customer", "CID", "SPID", "_px3", "bm_sv", "ACID"]
        found_auth = [c["name"] for c in cookie_list if any(a in c["name"] for a in auth_names)]
        if found_auth:
            print(f"  Auth cookies found: {found_auth[:8]}")
        else:
            print("  WARNING: No auth cookies found — you may not be fully logged in")

        with open(COOKIES_FILE, "w") as f:
            json.dump(cookie_list, f, indent=2)

        print(f"\nCookies saved to {COOKIES_FILE} ({len(cookie_list)} total)")
    except Exception as e:
        print(f"ERROR saving cookies: {e}")

    await browser.stop()


asyncio.run(walmart_relogin())
