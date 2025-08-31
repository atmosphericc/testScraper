#!/usr/bin/env python3
"""
Debug Target pages to understand current page structure and fix detection
"""
import asyncio
from playwright.async_api import async_playwright
import re

async def debug_target_page(tcin: str, expected_status: str):
    """Debug a Target product page to understand its structure"""
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()
        
        url = f"https://www.target.com/p/-/A-{tcin}"
        print(f"\n{'='*70}")
        print(f"🔍 DEBUGGING {tcin} - Expected: {expected_status}")
        print(f"URL: {url}")
        print(f"{'='*70}")
        
        try:
            # Navigate and wait
            await page.goto(url, wait_until='domcontentloaded')
            await page.wait_for_timeout(5000)  # Wait for dynamic content
            
            # Get basic info
            title = await page.title()
            product_name = title.replace(' : Target', '').strip()
            print(f"📦 Product: {product_name}")
            
            # Get page content for analysis
            content = await page.content()
            content_lower = content.lower()
            
            print(f"\n🔍 AVAILABILITY ANALYSIS:")
            
            # Check for obvious out of stock indicators
            oos_phrases = [
                "out of stock",
                "sold out", 
                "currently unavailable",
                "temporarily out of stock",
                "not available"
            ]
            
            found_oos = False
            for phrase in oos_phrases:
                if phrase in content_lower:
                    print(f"  ❌ Found OOS indicator: '{phrase}'")
                    found_oos = True
            
            # Check for availability indicators
            avail_phrases = [
                "add to cart",
                "ship it",
                "pick it up",
                "add to bag",
                "buy now"
            ]
            
            found_avail = False
            for phrase in avail_phrases:
                if phrase in content_lower:
                    print(f"  ✅ Found availability indicator: '{phrase}'")
                    found_avail = True
            
            # Extract all button text
            print(f"\n🔘 ALL BUTTONS ON PAGE:")
            try:
                buttons = await page.locator('button').all()
                button_count = len(buttons)
                print(f"  Found {button_count} buttons")
                
                important_buttons = []
                for i, button in enumerate(buttons):
                    try:
                        if await button.is_visible():
                            text = await button.text_content()
                            if text and text.strip():
                                text = text.strip()
                                # Look for important buttons
                                if any(word in text.lower() for word in ['add', 'cart', 'ship', 'pick', 'buy', 'order']):
                                    is_disabled = await button.is_disabled()
                                    important_buttons.append({
                                        'text': text,
                                        'disabled': is_disabled,
                                        'index': i
                                    })
                    except:
                        pass
                
                if important_buttons:
                    print(f"  📝 Important buttons found:")
                    for btn in important_buttons[:5]:  # Show first 5
                        status = "DISABLED" if btn['disabled'] else "ENABLED"
                        print(f"    - '{btn['text']}' ({status})")
                else:
                    print(f"  📝 No important buttons found")
                    
            except Exception as e:
                print(f"  ❌ Error analyzing buttons: {e}")
            
            # Check for price
            print(f"\n💰 PRICE DETECTION:")
            price_patterns = [
                r'\$([0-9,]+\.?[0-9]*)',
                r'current_retail["\']?\s*:\s*([0-9.]+)'
            ]
            
            found_prices = []
            for pattern in price_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    try:
                        price = float(match.replace(',', ''))
                        if 1 <= price <= 10000:  # Reasonable range
                            found_prices.append(price)
                    except:
                        pass
            
            if found_prices:
                # Get most common reasonable price
                unique_prices = list(set(found_prices))
                if unique_prices:
                    price = unique_prices[0]  # Take first reasonable price
                    print(f"  💰 Detected price: ${price:.2f}")
            else:
                print(f"  💰 No price detected")
            
            # Final determination
            print(f"\n🎯 DETERMINATION:")
            if found_oos and not found_avail:
                detected_status = "OUT OF STOCK"
                confidence = "HIGH"
            elif found_avail and not found_oos:
                detected_status = "IN STOCK"
                confidence = "HIGH"
            elif found_avail and found_oos:
                detected_status = "CONFLICTING SIGNALS"
                confidence = "LOW"
            elif important_buttons:
                # Check if we have enabled buttons
                enabled_buttons = [b for b in important_buttons if not b['disabled']]
                if enabled_buttons:
                    detected_status = "LIKELY IN STOCK"
                    confidence = "MEDIUM"
                else:
                    detected_status = "LIKELY OUT OF STOCK"
                    confidence = "MEDIUM"
            else:
                detected_status = "UNKNOWN"
                confidence = "VERY LOW"
            
            print(f"  🤖 Detected: {detected_status} (confidence: {confidence})")
            print(f"  📋 Expected: {expected_status}")
            
            if detected_status.replace("LIKELY ", "") == expected_status.replace("_", " "):
                print(f"  ✅ MATCH!")
            else:
                print(f"  ❌ MISMATCH!")
            
        except Exception as e:
            print(f"❌ Error debugging page: {e}")
        
        finally:
            await browser.close()

async def main():
    """Debug all 5 products to fix detection logic"""
    
    print("🔧 DEBUGGING TARGET PAGES TO FIX STOCK DETECTION")
    print("This will analyze the actual page content to improve accuracy")
    
    test_cases = [
        ('89542109', 'IN_STOCK'),      # Should be the only one in stock
        ('94724987', 'OUT_OF_STOCK'),  # Should be out of stock
        ('94681785', 'OUT_OF_STOCK'),  # Should be out of stock  
        ('94681770', 'OUT_OF_STOCK'),  # Should be out of stock
        ('94336414', 'OUT_OF_STOCK'),  # Should be out of stock
    ]
    
    for tcin, expected in test_cases:
        await debug_target_page(tcin, expected)
        await asyncio.sleep(2)  # Small delay between requests
    
    print(f"\n{'='*70}")
    print("🎯 SUMMARY")
    print("Based on this analysis, I can improve the website checker's detection logic")

if __name__ == "__main__":
    asyncio.run(main())