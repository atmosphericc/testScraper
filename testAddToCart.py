import asyncio
from playwright.async_api import async_playwright
import os

TCIN = "92800127"  # Change this to test other products
TARGET_URL = f"https://www.target.com/p/-/A-{TCIN}"
STORAGE_PATH = "target_storage.json"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        
        # Use persistent session
        context = await browser.new_context(storage_state=STORAGE_PATH if os.path.exists(STORAGE_PATH) else None)
        page = await context.new_page()

        # Go to product page
        await page.goto(TARGET_URL, wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)

        # Click "Shipping" fulfillment option if it exists
        try:
            shipping_button = await page.wait_for_selector('button[data-test="fulfillment-cell-shipping"]', timeout=5000)
            await shipping_button.scroll_into_view_if_needed()
            await shipping_button.click(force=True)
            await page.wait_for_timeout(1000)
        except:
            print("⚠️ Shipping button not found or already selected.")

        # Scroll to ensure button is in view
        await page.evaluate("window.scrollBy(0, window.innerHeight);")
        await page.wait_for_timeout(1000)

        # Click "Add to cart" button
        try:
            add_btn = await page.wait_for_selector('button[id^="addToCartButtonOrTextIdFor"]', timeout=5000)
            await add_btn.scroll_into_view_if_needed()
            await add_btn.click(force=True)
            print("✅ Clicked Add to Cart")
        except:
            print("❌ Add to Cart button not found.")
            await browser.close()
            return

        # Wait for cart to update
        await page.wait_for_timeout(2000)

        # Save session if new
        await context.storage_state(path=STORAGE_PATH)

        # Go to cart
        await page.goto("https://www.target.com/cart")
        print("🛒 Redirected to cart")

        # Wait forever for debugging
        print("🧷 Staying open indefinitely for inspection...")
        await asyncio.Event().wait()

asyncio.run(run())
