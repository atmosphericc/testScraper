import asyncio
from playwright.async_api import async_playwright
import os
import json

STORAGE_PATH = "target.json"

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

        # Ensure we have a stable page context after login
        await page.goto("https://www.target.com")
        await page.wait_for_timeout(2000)

        # Capture browser fingerprint for consistent identity
        print("Capturing browser fingerprint...")
        fingerprint_data = await page.evaluate("""
            () => {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillText('Browser fingerprint', 2, 2);

                const gl = document.createElement('canvas').getContext('webgl');
                const debugInfo = gl ? gl.getExtension('WEBGL_debug_renderer_info') : null;

                return {
                    userAgent: navigator.userAgent,
                    language: navigator.language,
                    platform: navigator.platform,
                    screenWidth: screen.width,
                    screenHeight: screen.height,
                    availWidth: screen.availWidth,
                    availHeight: screen.availHeight,
                    colorDepth: screen.colorDepth,
                    pixelDepth: screen.pixelDepth,
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                    webglVendor: debugInfo ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) : null,
                    webglRenderer: debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : null,
                    canvasFingerprint: canvas.toDataURL(),
                    hardwareConcurrency: navigator.hardwareConcurrency,
                    deviceMemory: navigator.deviceMemory,
                    cookieEnabled: navigator.cookieEnabled,
                    doNotTrack: navigator.doNotTrack,
                    onLine: navigator.onLine,
                    touchSupport: 'ontouchstart' in window
                };
            }
        """)

        # Save session with fingerprint data and timestamps
        import time
        storage_state = await context.storage_state()
        storage_state['fingerprint'] = fingerprint_data
        storage_state['created_at'] = time.time()
        storage_state['last_used'] = time.time()

        with open(STORAGE_PATH, 'w') as f:
            json.dump(storage_state, f, indent=2)

        # Verify
        with open(STORAGE_PATH, 'r') as f:
            data = json.load(f)
            print(f"Saved {len(data.get('cookies', []))} cookies")
            print(f"Captured fingerprint: {data.get('fingerprint', {}).get('userAgent', 'Unknown')}")

        # Keep browser open
        print("\nBrowser staying open. Press Ctrl+C to close.")
        await asyncio.Event().wait()  # Wait forever

asyncio.run(save_login())