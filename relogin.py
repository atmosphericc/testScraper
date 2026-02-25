import asyncio
from patchright.async_api import async_playwright
import os
import json

STORAGE_PATH = "target_storage.json"
USER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_user_data")

async def relogin():
    async with async_playwright() as p:
        os.makedirs(USER_DATA_DIR, exist_ok=True)

        context = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            channel="chrome",
            headless=False,
            no_viewport=True,
            ignore_default_args=[
                '--enable-automation',
                '--no-sandbox',
                '--disable-setuid-sandbox',
            ],
            args=[
                '--test-type',
                '--start-maximized',
            ],
            chromium_sandbox=True,
        )

        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        print("Navigating to target.com...")
        await page.goto("https://www.target.com")
        await page.wait_for_timeout(3000)

        try:
            await page.wait_for_selector('[data-test="@web/AccountLink"]', timeout=5000)
            print("Already logged in! Saving updated session...")
        except:
            print("Not logged in. Please log in manually in the browser.")
            input("After logging in, press ENTER to save session...")

        await context.storage_state(path=STORAGE_PATH)
        print(f"Session saved to {STORAGE_PATH}")

        with open(STORAGE_PATH, 'r') as f:
            data = json.load(f)
            print(f"Saved {len(data.get('cookies', []))} cookies")

        print("\nBrowser staying open. Press Ctrl+C to close.")
        await asyncio.Event().wait()

asyncio.run(relogin())
