#!/usr/bin/env python3
"""
Debug 94724987 using authenticated session to see actual page state
"""
import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def debug_authenticated_94724987():
    """Debug 94724987 using authenticated session"""
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,  # Show browser to see actual page
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-automation',
                '--no-sandbox',
            ]
        )
        
        # Load authenticated session
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            storage_state='sessions/target_storage.json'
        )
        
        page = await context.new_page()
        
        try:
            url = "https://www.target.com/p/-/A-94724987"
            print(f"🔍 DEBUGGING 94724987 WITH AUTHENTICATION")
            print(f"URL: {url}")
            print("=" * 60)
            
            await page.goto(url, wait_until='domcontentloaded')
            await page.wait_for_timeout(8000)  # Extra wait to see final state
            
            # Take a screenshot for manual inspection
            await page.screenshot(path='debug_94724987_authenticated.png')
            print("📸 Screenshot saved as debug_94724987_authenticated.png")
            
            # Get page title
            title = await page.title()
            print(f"📦 Product: {title}")
            
            # Get page content
            content = await page.content()
            content_lower = content.lower()
            
            # Check for out of stock indicators
            print(f"\n🔍 OUT OF STOCK TEXT ANALYSIS:")
            oos_phrases = [
                "out of stock",
                "sold out", 
                "currently unavailable",
                "temporarily out of stock",
                "item not available",
                "not available"
            ]
            
            found_oos = []
            for phrase in oos_phrases:
                if phrase in content_lower:
                    found_oos.append(phrase)
                    print(f"  ❌ FOUND: '{phrase}'")
            
            if not found_oos:
                print("  ℹ️  No explicit out of stock text found in page content")
            
            # Check Add to Cart buttons in detail
            print(f"\n🔘 DETAILED BUTTON ANALYSIS:")
            try:
                # Method 1: Specific Add to Cart locator
                add_to_cart_locator = page.locator('button:has-text("Add to cart")')
                count = await add_to_cart_locator.count()
                print(f"  Found {count} 'Add to cart' buttons via locator")
                
                for i in range(count):
                    button = add_to_cart_locator.nth(i)
                    if await button.is_visible():
                        is_disabled = await button.is_disabled()
                        text = await button.text_content()
                        print(f"    Button {i+1}: '{text}' - Disabled: {is_disabled}, Visible: True")
                
                # Method 2: All buttons search
                all_buttons = await page.locator('button').all()
                print(f"  Found {len(all_buttons)} total buttons on page")
                
                cart_related_buttons = []
                for i, button in enumerate(all_buttons):
                    try:
                        if await button.is_visible():
                            text = await button.text_content() or ""
                            if any(word in text.lower() for word in ['add', 'cart', 'buy', 'purchase']):
                                is_disabled = await button.is_disabled()
                                cart_related_buttons.append({
                                    'index': i,
                                    'text': text.strip(),
                                    'disabled': is_disabled
                                })
                    except:
                        continue
                
                print(f"  Found {len(cart_related_buttons)} cart-related buttons:")
                for btn in cart_related_buttons[:10]:  # Show first 10
                    status = "🔴 DISABLED" if btn['disabled'] else "🟢 ENABLED"
                    print(f"    {status}: '{btn['text']}'")
                    
            except Exception as e:
                print(f"  ❌ Error analyzing buttons: {e}")
            
            # Check specific availability sections
            print(f"\n📦 AVAILABILITY SECTION ANALYSIS:")
            
            # Look for specific Target availability patterns
            availability_selectors = [
                '[data-test*="availability"]',
                '[data-test*="fulfillment"]',
                '[data-test*="shipping"]',
                '[data-test*="pickup"]',
                '.availability-status',
                '.fulfillment-section'
            ]
            
            for selector in availability_selectors:
                try:
                    elements = await page.locator(selector).all()
                    if elements:
                        print(f"  Found {len(elements)} elements for '{selector}':")
                        for i, element in enumerate(elements[:3]):  # Show first 3
                            if await element.is_visible():
                                text = await element.text_content()
                                print(f"    {i+1}: {text[:100]}...")
                except:
                    continue
            
            # Look for specific text in the DOM
            print(f"\n🔍 DOM TEXT SEARCH:")
            
            # Search for key phrases in the actual DOM
            search_phrases = [
                "shipping not available",
                "pickup not available", 
                "delivery not available",
                "temporarily unavailable",
                "out of stock"
            ]
            
            for phrase in search_phrases:
                try:
                    locator = page.locator(f'text="{phrase}"')
                    count = await locator.count()
                    if count > 0:
                        print(f"  ❌ FOUND '{phrase}': {count} occurrences")
                        for i in range(min(count, 2)):
                            element = locator.nth(i)
                            text = await element.text_content()
                            print(f"    Text: {text}")
                    else:
                        print(f"  ✅ '{phrase}': Not found")
                except Exception as e:
                    print(f"  ❓ '{phrase}': Error - {e}")
            
            print(f"\n⏱️  Pausing for manual inspection...")
            print("Check the browser window to see the actual page state")
            await page.wait_for_timeout(20000)  # 20 seconds to inspect manually
            
        except Exception as e:
            print(f"❌ Error: {e}")
        
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_authenticated_94724987())