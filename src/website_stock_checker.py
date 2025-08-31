#!/usr/bin/env python3
"""
Browser-based stock checker that checks actual Target.com website
This will give us the TRUE availability status
"""
import asyncio
from playwright.async_api import async_playwright
import sys
from pathlib import Path

class WebsiteStockChecker:
    """Check actual stock status by visiting Target.com product pages"""
    
    def __init__(self):
        self.session_path = Path('sessions/target_storage.json')
    
    async def check_website_stock(self, tcin: str) -> dict:
        """Check stock by visiting actual Target website"""
        
        async with async_playwright() as p:
            try:
                # Launch browser with stealth settings
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-automation',
                        '--disable-dev-shm-usage', 
                        '--no-sandbox',
                        '--disable-extensions',
                        '--disable-plugins',
                        '--disable-images',
                        '--disable-background-timer-throttling',
                    ]
                )
                
                # Create context with session if available
                context_options = {
                    'viewport': {'width': 1920, 'height': 1080},
                    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
                    'locale': 'en-US',
                    'timezone_id': 'America/New_York'
                }
                
                # Add session if it exists
                if self.session_path.exists():
                    context_options['storage_state'] = str(self.session_path)
                
                context = await browser.new_context(**context_options)
                page = await context.new_page()
                
                # Add stealth script
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });
                """)
                
                page.set_default_timeout(15000)
                
                # Navigate to product page
                url = f"https://www.target.com/p/-/A-{tcin}"
                print(f"🌐 Checking website for {tcin}...")
                
                await page.goto(url, wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)  # Wait for dynamic content
                
                # Get page title for product name
                title = await page.title()
                product_name = title.replace(' : Target', '').strip()[:50]
                
                # Check for various availability indicators
                availability_info = {
                    'tcin': tcin,
                    'name': product_name,
                    'available': False,
                    'availability_text': 'unknown',
                    'button_text': 'not_found',
                    'price_text': 'not_found',
                    'status': 'checked'
                }
                
                # Look for add to cart button
                add_to_cart_selectors = [
                    '[data-test="chooseOptionsButton"]',
                    '[data-test="addToCartButtonOrTextIdFor"]', 
                    '[data-test="orderPickupButton"]',
                    'button[data-test*="add"]',
                    'button[data-test*="cart"]',
                    'button[data-test*="pickup"]',
                    '[data-test="shippingButton"]',
                    'button:has-text("Add to cart")',
                    'button:has-text("Pick it up")',
                    'button:has-text("Ship it")'
                ]
                
                button_found = False
                for selector in add_to_cart_selectors:
                    try:
                        button = page.locator(selector).first
                        if await button.is_visible():
                            button_text = await button.text_content()
                            availability_info['button_text'] = button_text.strip()
                            
                            # Check if button is disabled
                            is_disabled = await button.is_disabled()
                            if not is_disabled and button_text:
                                availability_info['available'] = True
                                availability_info['availability_text'] = f"Button: {button_text}"
                                button_found = True
                                break
                    except:
                        continue
                
                # Look for out of stock indicators
                oos_selectors = [
                    '[data-test="outOfStockMessage"]',
                    'text="Out of stock"',
                    'text="Sold out"',
                    'text="Currently unavailable"',
                    'text="Not available"',
                    '[data-test="preorderButton"]',
                    'text="Temporarily out of stock"'
                ]
                
                for selector in oos_selectors:
                    try:
                        element = page.locator(selector).first
                        if await element.is_visible():
                            oos_text = await element.text_content()
                            availability_info['available'] = False
                            availability_info['availability_text'] = f"OOS: {oos_text.strip()}"
                            button_found = True
                            break
                    except:
                        continue
                
                # Get price information
                price_selectors = [
                    '[data-test="product-price"]',
                    '[data-test="price-range"]',
                    '.h-text-bold',
                    '[data-test*="price"]'
                ]
                
                for selector in price_selectors:
                    try:
                        element = page.locator(selector).first
                        if await element.is_visible():
                            price_text = await element.text_content()
                            if price_text and '$' in price_text:
                                availability_info['price_text'] = price_text.strip()
                                break
                    except:
                        continue
                
                # If no specific indicators found, look at page content
                if not button_found:
                    page_content = await page.content()
                    if 'out of stock' in page_content.lower():
                        availability_info['available'] = False
                        availability_info['availability_text'] = "Page content indicates OOS"
                    elif 'add to cart' in page_content.lower():
                        availability_info['available'] = True
                        availability_info['availability_text'] = "Page content has add to cart"
                
                await browser.close()
                return availability_info
                
            except Exception as e:
                await browser.close()
                return {
                    'tcin': tcin,
                    'available': False,
                    'status': 'error',
                    'error': str(e)
                }

async def main():
    """Test website stock checking on known products"""
    
    print("🌐 WEBSITE-BASED STOCK CHECK")
    print("Checking actual Target.com pages for TRUE availability")
    print("=" * 70)
    
    checker = WebsiteStockChecker()
    
    # Test products based on your verification
    test_products = [
        ('89542109', 'Should be IN STOCK'),
        ('94724987', 'Should be OUT OF STOCK'), 
        ('94681785', 'Should be OUT OF STOCK'),
        ('94681770', 'Should be OUT OF STOCK'),
        ('94336414', 'Should be OUT OF STOCK')
    ]
    
    results = []
    
    for tcin, expected in test_products:
        print(f"\n📦 Testing {tcin} ({expected})")
        result = await checker.check_website_stock(tcin)
        results.append(result)
        
        status = "🟢 IN STOCK" if result.get('available') else "🔴 OUT OF STOCK"
        print(f"   Result: {status}")
        print(f"   Name: {result.get('name', 'Unknown')}")
        print(f"   Availability: {result.get('availability_text', 'N/A')}")
        print(f"   Button: {result.get('button_text', 'N/A')}")
        print(f"   Price: {result.get('price_text', 'N/A')}")
        
        if result.get('error'):
            print(f"   Error: {result['error']}")
    
    print(f"\n{'='*70}")
    print("📊 WEBSITE vs API COMPARISON")
    print("=" * 70)
    
    for result in results:
        tcin = result['tcin']
        website_status = "IN" if result.get('available') else "OUT"
        print(f"{tcin}: Website says {website_status} STOCK")

if __name__ == "__main__":
    asyncio.run(main())