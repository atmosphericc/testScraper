#!/usr/bin/env python3
"""
Debug script to test ATC button click on the second tab (checkout tab).
Run this to diagnose why ATC isn't clicking on the second tab.

Usage:
    python test_atc_debug.py <product_url>

Example:
    python test_atc_debug.py "https://www.walmart.com/ip/15042474261"
"""

import asyncio
import sys
import logging
from pathlib import Path

# Setup logging to see detailed output
logging.basicConfig(
    level=logging.DEBUG,
    format='%(name)s - %(levelname)s - %(message)s'
)

# Add paths
sys.path.insert(0, str(Path(__file__).parent))

from walmart.session_manager import WalmartSessionManager
from walmart.purchase_executor import WalmartPurchaseExecutor
from walmart.config import WALMART_EMAIL, WALMART_PASSWORD


async def debug_atc_click():
    """Debug ATC click on checkout tab."""

    if len(sys.argv) < 2:
        print("Usage: python test_atc_debug.py <product_url>")
        print("Example: python test_atc_debug.py 'https://www.walmart.com/ip/15042474261'")
        sys.exit(1)

    product_url = sys.argv[1]
    item_id = product_url.split("/ip/")[-1].split("?")[0]

    print(f"\n{'='*60}")
    print(f"Testing ATC click on checkout tab (second tab)")
    print(f"Product URL: {product_url}")
    print(f"Item ID: {item_id}")
    print(f"{'='*60}\n")

    session = None
    try:
        # Start session
        print("[DEBUG] Starting session manager...")
        session = WalmartSessionManager(
            status_callback=lambda msg: print(f"[SESSION] {msg}")
        )
        await session.start()

        # Load cookies if available
        print("[DEBUG] Loading saved cookies...")

        # Login if needed
        print("[DEBUG] Logging in...")
        await session.login(WALMART_EMAIL, WALMART_PASSWORD)

        # Warm the session on Tab 1
        print("[DEBUG] Warming session on Tab 1...")
        await session.warm_session([item_id])

        # Open checkout tab (Tab 2)
        print("[DEBUG] Opening checkout tab (Tab 2)...")
        await session.open_checkout_tab()

        # Get the checkout page (second tab)
        page = session.get_checkout_page()
        print(f"[DEBUG] Checkout page obtained: {page}")

        # Create executor with the checkout tab
        print("[DEBUG] Creating purchase executor for checkout tab...")
        executor = WalmartPurchaseExecutor(
            page,
            status_callback=lambda msg: print(f"[EXECUTOR] {msg}"),
            session=session
        )

        # Navigate to product page on Tab 2
        print("[DEBUG] Navigating to product page on checkout tab...")
        await executor._navigate(product_url)

        print("\n[DEBUG] About to attempt ATC click...")
        print("[DEBUG] Checking if page is ready and button exists...\n")

        # Try to click ATC
        success = await executor._add_to_cart(item_id)

        if success:
            print("\n✓ ATC CLICK SUCCESSFUL!")
        else:
            print("\n✗ ATC CLICK FAILED!")
            print("[DEBUG] Taking screenshot of the page for inspection...")
            await executor._screenshot(f"debug_atc_fail_{item_id}")

        # Take a final screenshot regardless
        print("[DEBUG] Taking final screenshot...")
        await executor._screenshot(f"debug_final_{item_id}")

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()

    finally:
        if session:
            print("\n[DEBUG] Stopping session...")
            await session.stop()
        print("[DEBUG] Done!")


if __name__ == "__main__":
    asyncio.run(debug_atc_click())
