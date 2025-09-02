#!/usr/bin/env python3
"""
Test APIs using actual browser session cookies from authenticated checker
"""
import sys
import asyncio
import json
from playwright.async_api import async_playwright
from pathlib import Path
sys.path.insert(0, 'src')

async def test_apis_with_browser_session():
    """Test APIs using real browser session with cookies"""
    
    print("TESTING APIs WITH AUTHENTICATED BROWSER SESSION")
    print("="*60)
    
    session_path = Path('sessions/target_storage.json')
    if not session_path.exists():
        print("❌ No session file found! Run: python setup.py login")
        return
    
    # Test products
    products = [
        ('89542109', 'KNOWN IN STOCK'),
        ('94724987', 'KNOWN OUT OF STOCK')
    ]
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        
        # Use authenticated session
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            storage_state=str(session_path)
        )
        
        page = await context.new_page()
        
        # Capture network requests
        network_calls = []
        
        async def log_request(request):
            network_calls.append({
                'url': request.url,
                'method': request.method,
                'headers': dict(request.headers),
                'post_data': request.post_data
            })
        
        async def log_response(response):
            # Try to capture response body for API calls
            if 'api' in response.url or 'redsky' in response.url or 'carts' in response.url:
                try:
                    if response.status == 200 and 'json' in response.headers.get('content-type', ''):
                        body = await response.body()
                        data = json.loads(body.decode('utf-8'))
                        
                        # Save interesting API responses
                        url_parts = response.url.split('/')
                        filename = f"browser_api_response_{url_parts[-1]}_{response.status}.json"
                        
                        with open(filename, 'w', encoding='utf-8') as f:
                            json.dump({
                                'url': response.url,
                                'status': response.status,
                                'headers': dict(response.headers),
                                'data': data
                            }, f, indent=2, ensure_ascii=False)
                        
                        print(f"   📁 Saved API response: {filename}")
                        
                except Exception as e:
                    print(f"   Error capturing response: {e}")
        
        page.on('request', log_request)
        page.on('response', log_response)
        
        for tcin, status in products:
            print(f"\n🌐 Loading {tcin} ({status}) with authenticated session")
            print("-" * 50)
            
            # Clear previous calls
            network_calls = []
            
            # Navigate to product page
            url = f"https://www.target.com/p/-/A-{tcin}"
            await page.goto(url, wait_until='networkidle')
            
            # Wait for dynamic content
            await page.wait_for_timeout(3000)
            
            # Try to interact with add to cart to trigger more API calls
            try:
                print("   🖱️ Looking for Add to Cart button...")
                add_to_cart = page.locator('button:has-text("Add to cart")')
                
                if await add_to_cart.count() > 0:
                    print("   ✅ Add to Cart found - checking if enabled...")
                    is_disabled = await add_to_cart.first.is_disabled()
                    
                    if not is_disabled:
                        print("   🚀 Button enabled - this should trigger inventory APIs!")
                        # Don't actually click, just hover to see if it triggers API calls
                        await add_to_cart.first.hover()
                        await page.wait_for_timeout(2000)
                    else:
                        print("   ❌ Button disabled - consistent with out of stock")
                else:
                    print("   ❓ No Add to Cart button found")
                    
            except Exception as e:
                print(f"   Error interacting with button: {e}")
            
            # Try to open quantity selector if it exists
            try:
                quantity_selector = page.locator('[data-test*="quantity"], select[id*="quantity"]')
                if await quantity_selector.count() > 0:
                    print("   📊 Quantity selector found - clicking...")
                    await quantity_selector.first.click()
                    await page.wait_for_timeout(2000)
            except:
                pass
            
            # Analyze captured network calls
            print(f"\n   📡 Network Analysis for {tcin}:")
            api_calls = []
            for call in network_calls:
                if any(domain in call['url'] for domain in ['redsky', 'api.target', 'carts.target']):
                    api_calls.append(call)
            
            print(f"      API calls captured: {len(api_calls)}")
            for call in api_calls:
                print(f"        {call['method']} {call['url']}")
        
        await browser.close()
        
        print(f"\n{'='*60}")
        print("AUTHENTICATED BROWSER SESSION TESTING COMPLETE")
        print("="*60)
        print("Check for any new API response files generated")

if __name__ == "__main__":
    asyncio.run(test_apis_with_browser_session())