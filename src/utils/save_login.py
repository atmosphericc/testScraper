import asyncio
from playwright.async_api import async_playwright
import os
import json

STORAGE_PATH = "target_storage.json"

async def save_login():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        # Load existing session if it exists (like testAddToCart does)
        if os.path.exists(STORAGE_PATH):
            print("Loading existing session...")
            context = await browser.new_context(storage_state=STORAGE_PATH)
        else:
            print("No existing session, starting fresh...")
            context = await browser.new_context()

        page = await context.new_page()
        await page.goto("https://www.target.com")

        # Check if already logged in
        await page.wait_for_timeout(3000)
        try:
            # Look for account element
            await page.wait_for_selector('[data-test="@web/AccountLink"]', timeout=1000)
            print("Already logged in!")
            print("Updating session file...")
        except:
            print("Not logged in. Please log in manually.")
            input("After logging in, press ENTER...")

        # Save/update the session
        await context.storage_state(path=STORAGE_PATH)
        print(f"Session saved to {STORAGE_PATH}")

        # Verify
        with open(STORAGE_PATH, 'r') as f:
            data = json.load(f)
            print(f"Saved {len(data.get('cookies', []))} cookies")

        # Keep browser open
        print("\nBrowser staying open. Press Ctrl+C to close.")
        await asyncio.Event().wait()  # Wait forever

asyncio.run(save_login())