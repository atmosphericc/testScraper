#!/usr/bin/env python3
"""
Debug 94724987 specifically to see why it's showing as in stock when it should be out
"""
import asyncio
from playwright.async_api import async_playwright

async def debug_94724987():
    """Debug the specific product that's showing wrong results"""
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()
        
        url = "https://www.target.com/p/-/A-94724987"
        print(f"🔍 DEBUGGING 94724987 - Should be OUT OF STOCK")
        print(f"URL: {url}")
        print("=" * 60)
        
        try:
            await page.goto(url, wait_until='domcontentloaded')
            await page.wait_for_timeout(5000)
            
            # Get page title
            title = await page.title()
            print(f"📦 Product: {title}")
            
            # Method 1: Look for explicit out of stock text
            content = await page.content()
            content_lower = content.lower()
            
            print(f"\n🔍 CHECKING FOR OUT OF STOCK INDICATORS:")
            oos_indicators = [
                "out of stock",
                "sold out", 
                "currently unavailable",
                "temporarily out of stock",
                "not available"
            ]
            
            found_oos = []
            for indicator in oos_indicators:
                if indicator in content_lower:
                    found_oos.append(indicator)
                    print(f"  ❌ FOUND: '{indicator}'")
            
            if not found_oos:
                print(f"  ℹ️  No explicit out of stock text found")
            
            # Method 2: Check Add to cart buttons
            print(f"\n🔘 CHECKING ADD TO CART BUTTONS:")
            try:
                # Look for all "Add to cart" buttons
                add_to_cart_locator = page.locator('button:has-text("Add to cart")')
                count = await add_to_cart_locator.count()
                print(f"  Found {count} 'Add to cart' buttons")
                
                for i in range(count):
                    button = add_to_cart_locator.nth(i)
                    if await button.is_visible():
                        is_disabled = await button.is_disabled()
                        text = await button.text_content()
                        print(f"    Button {i+1}: '{text}' - Disabled: {is_disabled}")
                        
            except Exception as e:
                print(f"  Error checking buttons: {e}")
            
            # Method 3: Check all buttons for any availability indicators
            print(f"\n🔘 ALL RELEVANT BUTTONS:")
            try:
                all_buttons = await page.locator('button').all()
                relevant_buttons = []
                
                for button in all_buttons:
                    try:
                        if await button.is_visible():
                            text = await button.text_content() or ""
                            if any(word in text.lower() for word in ['add', 'cart', 'ship', 'pick', 'buy', 'order']):
                                is_disabled = await button.is_disabled()
                                relevant_buttons.append({
                                    'text': text.strip(),
                                    'disabled': is_disabled
                                })
                    except:
                        continue
                
                print(f"  Found {len(relevant_buttons)} relevant buttons:")
                for btn in relevant_buttons[:8]:  # Show first 8
                    status = "🔴 DISABLED" if btn['disabled'] else "🟢 ENABLED"
                    print(f"    {status}: '{btn['text']}'")
                    
            except Exception as e:
                print(f"  Error: {e}")
            
            # Method 4: Check for shipping/pickup availability
            print(f"\n📦 SHIPPING/PICKUP STATUS:")
            shipping_indicators = [
                "shipping",
                "pickup",
                "delivery",
                "ship to",
                "pick it up"
            ]
            
            for indicator in shipping_indicators:
                if indicator in content_lower:
                    # Look for "not available" near shipping terms
                    import re
                    pattern = rf'{indicator}[^<>]*?not available'
                    matches = re.findall(pattern, content_lower)
                    if matches:
                        print(f"  ❌ {indicator.title()}: Not available")
                    else:
                        print(f"  ℹ️  {indicator.title()}: Found in content")
            
            # Final determination
            print(f"\n🎯 MANUAL DETERMINATION:")
            print(f"Based on the above analysis...")
            
            # Check if we have any enabled add to cart buttons
            enabled_add_buttons = [btn for btn in relevant_buttons if not btn['disabled'] and 'add to cart' in btn['text'].lower()]
            
            if found_oos and not enabled_add_buttons:
                print(f"  🔴 SHOULD BE: OUT OF STOCK (has OOS text, no enabled add buttons)")
            elif enabled_add_buttons and not found_oos:
                print(f"  🟢 SHOULD BE: IN STOCK (has enabled add buttons, no OOS text)")
            elif found_oos and enabled_add_buttons:
                print(f"  ⚠️  CONFLICTING SIGNALS - needs closer inspection")
            else:
                print(f"  ❓ UNCLEAR - default to OUT OF STOCK for safety")
            
        except Exception as e:
            print(f"❌ Error: {e}")
        
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_94724987())