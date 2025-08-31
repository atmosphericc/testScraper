#!/usr/bin/env python3
"""
Debug 89542109 (the one that SHOULD be IN STOCK) using authenticated session
"""
import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def debug_authenticated_89542109():
    """Debug 89542109 - the product that should be IN STOCK"""
    
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
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            storage_state='sessions/target_storage.json'
        )
        
        page = await context.new_page()
        
        try:
            url = "https://www.target.com/p/-/A-89542109"
            print(f"🔍 DEBUGGING 89542109 - SHOULD BE IN STOCK")
            print(f"URL: {url}")
            print("=" * 60)
            
            await page.goto(url, wait_until='domcontentloaded')
            await page.wait_for_timeout(8000)  # Extra wait to see final state
            
            # Take a screenshot for manual inspection
            await page.screenshot(path='debug_89542109_authenticated.png')
            print("📸 Screenshot saved as debug_89542109_authenticated.png")
            
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
                "item not available"
            ]
            
            found_oos = []
            for phrase in oos_phrases:
                if phrase in content_lower:
                    found_oos.append(phrase)
                    print(f"  ❌ FOUND: '{phrase}'")
            
            if not found_oos:
                print("  ✅ NO out of stock text found in page content")
            
            # Check shipping/pickup availability
            print(f"\n📦 SHIPPING/PICKUP AVAILABILITY:")
            
            shipping_checks = [
                ("shipping", "not available"),
                ("pickup", "not available"), 
                ("delivery", "not available")
            ]
            
            shipping_unavailable = False
            for service, unavail_text in shipping_checks:
                if service in content_lower and unavail_text in content_lower:
                    print(f"  ❌ {service.title()}: NOT AVAILABLE")
                    shipping_unavailable = True
                elif service in content_lower:
                    print(f"  ✅ {service.title()}: Found (likely available)")
                else:
                    print(f"  ❓ {service.title()}: Not mentioned")
            
            # Detailed button analysis
            print(f"\n🔘 DETAILED ADD TO CART BUTTON ANALYSIS:")
            
            # Primary button check
            try:
                primary_button = page.locator('button:has-text("Add to cart")').first
                if await primary_button.is_visible():
                    is_disabled = await primary_button.is_disabled()
                    button_text = await primary_button.text_content()
                    print(f"  PRIMARY BUTTON: '{button_text}' - Disabled: {is_disabled}")
                    
                    # Check button attributes
                    button_classes = await primary_button.get_attribute('class')
                    button_data_test = await primary_button.get_attribute('data-test')
                    print(f"    Classes: {button_classes}")
                    print(f"    Data-test: {button_data_test}")
                else:
                    print("  PRIMARY BUTTON: Not visible")
                    
            except Exception as e:
                print(f"  PRIMARY BUTTON ERROR: {e}")
            
            # All "Add to cart" buttons
            try:
                all_cart_buttons = await page.locator('button:has-text("Add to cart")').all()
                print(f"  TOTAL 'Add to cart' BUTTONS: {len(all_cart_buttons)}")
                
                for i, button in enumerate(all_cart_buttons):
                    if await button.is_visible():
                        is_disabled = await button.is_disabled()
                        text = await button.text_content()
                        status = "🔴 DISABLED" if is_disabled else "🟢 ENABLED"
                        print(f"    Button {i+1}: {status} - '{text}'")
                        
            except Exception as e:
                print(f"  ALL BUTTONS ERROR: {e}")
            
            # Look for alternative availability indicators
            print(f"\n✨ AVAILABILITY INDICATORS:")
            
            # Check for specific Target availability patterns
            availability_indicators = [
                "add to cart",
                "ship it",
                "pick it up", 
                "order pickup",
                "same day delivery",
                "available",
                "in stock"
            ]
            
            for indicator in availability_indicators:
                if indicator in content_lower:
                    print(f"  ✅ FOUND: '{indicator}'")
                else:
                    print(f"  ❌ NOT FOUND: '{indicator}'")
            
            # Final determination logic
            print(f"\n🎯 MANUAL DETERMINATION:")
            print(f"  OOS Text Found: {len(found_oos) > 0}")
            print(f"  Shipping Unavailable: {shipping_unavailable}")
            print(f"  Expected Status: IN STOCK")
            print(f"  Question: Why might this show as IN STOCK when others don't?")
            
            print(f"\n⏱️  Pausing for manual inspection...")
            print("Check the browser window to compare with the OUT OF STOCK products")
            await page.wait_for_timeout(30000)  # 30 seconds to inspect manually
            
        except Exception as e:
            print(f"❌ Error: {e}")
        
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_authenticated_89542109())