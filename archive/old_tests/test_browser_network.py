#!/usr/bin/env python3
"""
Capture network calls from authenticated browser session
"""
import sys
import asyncio
import json
from playwright.async_api import async_playwright
from pathlib import Path

async def capture_authenticated_network():
    """Capture network calls using authenticated browser session"""
    
    print("CAPTURING NETWORK CALLS WITH AUTHENTICATED SESSION")
    print("="*60)
    
    session_path = Path('sessions/target_storage.json')
    if not session_path.exists():
        print("No session file found!")
        return
    
    # Test the known in-stock product
    tcin = "89542109"
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            storage_state=str(session_path)
        )
        
        page = await context.new_page()
        
        # Capture all network activity
        network_data = []
        
        def handle_request(request):
            network_data.append({
                'type': 'request',
                'url': request.url,
                'method': request.method,
                'headers': dict(request.headers),
                'post_data': request.post_data
            })
        
        async def handle_response(response):
            # Only capture API responses
            if any(keyword in response.url for keyword in ['api', 'redsky', 'cart', 'fulfillment']):
                try:
                    if response.status == 200:
                        content_type = response.headers.get('content-type', '')
                        if 'json' in content_type:
                            body = await response.body()
                            data = json.loads(body.decode('utf-8'))
                            
                            network_data.append({
                                'type': 'response',
                                'url': response.url,
                                'status': response.status,
                                'headers': dict(response.headers),
                                'data': data
                            })
                            
                            print(f"Captured API response: {response.url} ({response.status})")
                            
                except Exception as e:
                    print(f"Error capturing response from {response.url}: {e}")
        
        page.on('request', handle_request)
        page.on('response', handle_response)
        
        print(f"Loading product page: {tcin}")
        
        # Load the page
        url = f"https://www.target.com/p/-/A-{tcin}"
        await page.goto(url, wait_until='domcontentloaded')
        
        # Wait for dynamic content
        await page.wait_for_timeout(5000)
        
        # Try to trigger more API calls by interacting with the page
        try:
            # Look for add to cart button
            add_to_cart = page.locator('button:has-text("Add to cart")')
            if await add_to_cart.count() > 0:
                print("Found Add to Cart button - checking state...")
                is_disabled = await add_to_cart.first.is_disabled()
                print(f"Add to Cart disabled: {is_disabled}")
                
                if not is_disabled:
                    print("Button enabled - hovering to trigger potential API calls...")
                    await add_to_cart.first.hover()
                    await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"Error interacting with page: {e}")
        
        await browser.close()
        
        # Save all captured network data
        with open('authenticated_network_capture.json', 'w', encoding='utf-8') as f:
            json.dump(network_data, f, indent=2, ensure_ascii=False)
        
        print(f"\nCaptured {len(network_data)} network events")
        
        # Analyze API calls
        api_responses = [item for item in network_data if item['type'] == 'response']
        print(f"API responses captured: {len(api_responses)}")
        
        for response in api_responses:
            print(f"  {response['url']} ({response['status']})")
            
            # Look for inventory-related data
            if 'data' in response:
                data_str = json.dumps(response['data']).lower()
                inventory_keywords = ['available', 'stock', 'inventory', 'purchasable', 'fulfillment', 'add_to_cart']
                found = [kw for kw in inventory_keywords if kw in data_str]
                if found:
                    print(f"    Contains: {found}")

if __name__ == "__main__":
    asyncio.run(capture_authenticated_network())