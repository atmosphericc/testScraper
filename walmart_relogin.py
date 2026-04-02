"""
Walmart manual login helper — mirrors relogin.py for Target.

Run this when:
  - First time setting up the Walmart bot
  - Session expired / getting login prompts during checkout
  - After a long period of inactivity

Usage:
    python walmart_relogin.py

What it does:
  1. Opens a real Patchright browser using a persistent profile (walmart-profile/)
  2. Navigates to walmart.com
  3. If already logged in, saves cookies immediately
  4. If not, waits for you to log in manually, then saves
  5. Cookies saved to walmart-profile/cookies.json
  6. Browser profile saved to walmart-profile/ (login persists across runs)

After running this, start the bot normally:
    python unified_app.py
"""

import asyncio
import json
import os
from pathlib import Path

COOKIES_FILE = "walmart-profile/cookies.json"
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "walmart-profile")


async def walmart_relogin():
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        print("ERROR: patchright is not installed.")
        print("Run: pip install patchright && python -m patchright install chromium")
        return

    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    print("=" * 55)
    print("  WALMART SESSION LOGIN")
    print("=" * 55)

    playwright = await async_playwright().start()

    # Use persistent context — saves full browser profile so login survives restarts
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
    )

    page = context.pages[0] if context.pages else await context.new_page()

    print("Navigating to walmart.com...")
    await page.goto("https://www.walmart.com", wait_until="domcontentloaded")
    await asyncio.sleep(3)

    # Check if already logged in
    logged_in = False
    try:
        content = await page.content()
        if any(x in content for x in ['"isLoggedIn":true', 'account/logout', '"type":"REGISTERED"']):
            print("Already logged in!")
            logged_in = True
    except Exception:
        pass

    if not logged_in:
        print()
        print("Not logged in. Please log in manually in the browser window.")
        print("  1. Click 'Sign In' on walmart.com")
        print("  2. Enter your email and password")
        print("  3. Complete any CAPTCHA or 2FA if prompted")
        print("  4. Wait until your name appears in the top right")
        print()
        input("Once you are fully logged in, press ENTER here to save the session...")
        await asyncio.sleep(2)

    # Export all cookies
    print("Saving cookies...")
    cookies = await context.cookies()

    if not cookies:
        print("WARNING: No cookies captured.")
    else:
        walmart_cookies = [c for c in cookies if "walmart.com" in c.get("domain", "")]
        print(f"  Total: {len(cookies)} | Walmart.com: {len(walmart_cookies)}")

        auth_names = ["auth", "customer", "CID", "SPID", "_px3", "bm_sv", "ACID"]
        found_auth = [c["name"] for c in cookies if any(a in c["name"] for a in auth_names)]
        if found_auth:
            print(f"  Auth cookies found: {found_auth[:8]}")
        else:
            print("  WARNING: No auth cookies found — you may not be fully logged in")

    Path(COOKIES_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)

    print(f"\nSession saved to {COOKIES_FILE} ({len(cookies)} cookies)")
    print("Profile saved to walmart-profile/ — login will persist on next run.")
    print()
    input("Press ENTER to close the browser...")

    await context.close()
    await playwright.stop()


asyncio.run(walmart_relogin())
