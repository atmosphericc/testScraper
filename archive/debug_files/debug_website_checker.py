#!/usr/bin/env python3
"""
Debug the website checker to see what's happening
"""
import asyncio
from playwright.async_api import async_playwright
import sys
from pathlib import Path

async def debug_single_product(tcin: str):
    """Debug a single product to see page structure"""
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # Show browser for debugging
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}
        )
        page = await context.new_page()
        
        url = f"https://www.target.com/p/-/A-{tcin}"
        print(f"🌐 Navigating to {url}")
        
        await page.goto(url)
        await page.wait_for_timeout(5000)  # Wait 5 seconds
        
        # Take a screenshot
        await page.screenshot(path=f"debug_{tcin}.png")
        print(f"📸 Screenshot saved as debug_{tcin}.png")
        
        # Get page title
        title = await page.title()
        print(f"📄 Page title: {title}")
        
        # Look for various elements
        print(f"\n🔍 Searching for availability indicators...")
        
        # Check for add to cart buttons
        selectors_to_try = [
            'button:has-text("Add to cart")',
            '[data-test*="add"]',
            '[data-test*="cart"]', 
            '[data-test*="button"]',
            'button',
            'text="Out of stock"',
            'text="Add to cart"',
            'text="Ship it"',
            'text="Pick it up"'
        ]
        
        for selector in selectors_to_try:
            try:
                elements = await page.locator(selector).all()
                print(f"  {selector}: Found {len(elements)} elements")
                
                for i, element in enumerate(elements[:3]):  # Show first 3
                    try:
                        text = await element.text_content()
                        is_visible = await element.is_visible()
                        print(f"    [{i}] Text: '{text}' Visible: {is_visible}")
                    except:
                        pass
            except Exception as e:
                print(f"  {selector}: Error - {e}")
        
        # Get some page content
        print(f"\n📝 Page content sample:")
        content = await page.content()
        if 'out of stock' in content.lower():
            print("  ✅ Found 'out of stock' in page content")
        if 'add to cart' in content.lower():
            print("  ✅ Found 'add to cart' in page content")
        
        input("Press Enter to close browser...")
        await browser.close()

async def main():
    """Debug specific products"""
    # Debug the ones that should be in stock
    await debug_single_product('89542109')  # Should be IN STOCK

if __name__ == "__main__":
    asyncio.run(main())