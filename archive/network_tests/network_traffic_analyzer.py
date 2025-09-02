#!/usr/bin/env python3
"""
Network Traffic Analyzer for Target.com
Captures ALL network requests when visiting product pages to find better APIs
"""
import sys
import asyncio
from playwright.async_api import async_playwright
import json
from pathlib import Path
import re
from urllib.parse import urlparse, parse_qs

class TargetNetworkAnalyzer:
    def __init__(self):
        self.network_calls = []
        self.api_endpoints = set()
        self.inventory_related_calls = []
        
    async def analyze_product_network_traffic(self, tcins):
        """Capture all network traffic when loading Target product pages"""
        
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=False,  # Visible so we can see what's happening
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-automation',
                    '--no-sandbox'
                ]
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
            )
            
            page = await context.new_page()
            
            # Enable network request/response capturing
            await page.route('**/*', self.capture_network_request)
            
            for tcin in tcins:
                print(f"\n🌐 Analyzing network traffic for TCIN: {tcin}")
                print("=" * 60)
                
                # Clear previous calls
                self.network_calls = []
                
                try:
                    url = f"https://www.target.com/p/-/A-{tcin}"
                    print(f"Loading: {url}")
                    
                    # Navigate and wait for network activity to settle
                    await page.goto(url, wait_until='networkidle')
                    
                    # Additional wait for dynamic content and lazy-loaded APIs
                    await page.wait_for_timeout(5000)
                    
                    # Try interacting with quantity selector (if available)
                    try:
                        print("Attempting to interact with quantity selector...")
                        quantity_selector = page.locator('[data-test*="quantity"], select[name*="quantity"], .quantity-selector')
                        if await quantity_selector.count() > 0:
                            await quantity_selector.first.click()
                            await page.wait_for_timeout(2000)
                    except:
                        print("No quantity selector interaction possible")
                    
                    # Try clicking add to cart (if available) 
                    try:
                        print("Attempting to find add to cart button...")
                        add_to_cart = page.locator('button:has-text("Add to cart")')
                        if await add_to_cart.count() > 0:
                            print("Add to cart button found - this might trigger inventory APIs")
                            # Don't actually click, just note its presence
                    except:
                        pass
                    
                    # Analyze captured network calls
                    await self.analyze_captured_calls(tcin)
                    
                except Exception as e:
                    print(f"Error analyzing {tcin}: {e}")
            
            await browser.close()
        
        # Final analysis
        await self.generate_analysis_report()
    
    async def capture_network_request(self, route):
        """Capture and analyze each network request/response"""
        request = route.request
        
        try:
            # Continue the request
            response = await route.fulfill_request(request)
            
            # Capture request details
            call_info = {
                'url': request.url,
                'method': request.method,
                'headers': dict(request.headers),
                'post_data': request.post_data,
                'response_status': response.status if response else None,
                'response_headers': dict(response.headers) if response else None
            }
            
            # Try to capture response body for API calls
            if response and response.status == 200:
                content_type = response.headers.get('content-type', '')
                if 'json' in content_type.lower():
                    try:
                        response_body = await response.body()
                        call_info['response_json'] = json.loads(response_body.decode('utf-8'))
                    except:
                        call_info['response_text'] = 'JSON parse failed'
            
            self.network_calls.append(call_info)
            
        except Exception as e:
            # Still continue the request even if capture fails
            await route.continue_()
    
    async def analyze_captured_calls(self, tcin):
        """Analyze captured network calls for inventory-related APIs"""
        
        print(f"\n📊 Network Analysis for {tcin}:")
        print("-" * 40)
        
        api_calls = []
        inventory_keywords = [
            'inventory', 'stock', 'availability', 'fulfillment', 'cart', 'purchase',
            'add', 'quantity', 'limit', 'store', 'delivery', 'shipping'
        ]
        
        for call in self.network_calls:
            url = call['url']
            
            # Focus on API calls (not assets)
            if any(pattern in url for pattern in ['/api/', 'redsky.target.com', 'target.com/web']):
                api_calls.append(call)
                
                # Check if this might be inventory-related
                url_lower = url.lower()
                if any(keyword in url_lower for keyword in inventory_keywords):
                    self.inventory_related_calls.append({
                        'tcin': tcin,
                        'call': call,
                        'potential_inventory': True
                    })
                
                # Check response for inventory data
                if 'response_json' in call:
                    response_str = json.dumps(call['response_json']).lower()
                    if any(keyword in response_str for keyword in inventory_keywords):
                        self.inventory_related_calls.append({
                            'tcin': tcin,
                            'call': call,
                            'potential_inventory': True,
                            'found_in_response': True
                        })
        
        print(f"   Total network calls: {len(self.network_calls)}")
        print(f"   API calls: {len(api_calls)}")
        print(f"   Potentially inventory-related: {len([c for c in self.inventory_related_calls if c['tcin'] == tcin])}")
        
        # Show interesting API endpoints
        print(f"\n   🔍 API Endpoints Found:")
        unique_apis = set()
        for call in api_calls:
            parsed_url = urlparse(call['url'])
            endpoint = f"{parsed_url.netloc}{parsed_url.path}"
            unique_apis.add(endpoint)
        
        for endpoint in sorted(unique_apis):
            print(f"     {endpoint}")
    
    async def generate_analysis_report(self):
        """Generate comprehensive analysis report"""
        
        print(f"\n{'='*80}")
        print("🔍 COMPREHENSIVE NETWORK ANALYSIS REPORT")
        print("="*80)
        
        # Save all inventory-related calls
        if self.inventory_related_calls:
            with open('inventory_related_network_calls.json', 'w', encoding='utf-8') as f:
                json.dump(self.inventory_related_calls, f, indent=2, ensure_ascii=False)
            
            print(f"✅ Found {len(self.inventory_related_calls)} potentially inventory-related API calls")
            print(f"📁 Detailed data saved to: inventory_related_network_calls.json")
            
            # Analyze patterns
            print(f"\n📈 INVENTORY API ANALYSIS:")
            print("-" * 50)
            
            unique_endpoints = {}
            for item in self.inventory_related_calls:
                call = item['call']
                parsed_url = urlparse(call['url'])
                endpoint = f"{parsed_url.netloc}{parsed_url.path}"
                
                if endpoint not in unique_endpoints:
                    unique_endpoints[endpoint] = {
                        'calls': 0,
                        'methods': set(),
                        'has_json_response': False,
                        'sample_params': None
                    }
                
                unique_endpoints[endpoint]['calls'] += 1
                unique_endpoints[endpoint]['methods'].add(call['method'])
                
                if 'response_json' in call:
                    unique_endpoints[endpoint]['has_json_response'] = True
                
                if not unique_endpoints[endpoint]['sample_params']:
                    parsed = urlparse(call['url'])
                    if parsed.query:
                        unique_endpoints[endpoint]['sample_params'] = parse_qs(parsed.query)
            
            # Report findings
            for endpoint, info in unique_endpoints.items():
                print(f"\n🎯 ENDPOINT: {endpoint}")
                print(f"   Calls: {info['calls']}")
                print(f"   Methods: {list(info['methods'])}")
                print(f"   Has JSON Response: {info['has_json_response']}")
                if info['sample_params']:
                    print(f"   Sample Params: {list(info['sample_params'].keys())}")
        
        else:
            print("❌ No inventory-related API calls found")
            print("   This suggests Target may not use client-side APIs for real-time inventory")
        
        print(f"\n💡 RECOMMENDATIONS:")
        if self.inventory_related_calls:
            print("   1. Test the discovered inventory APIs with your products")
            print("   2. Check if any provide real-time stock status")
            print("   3. Reverse engineer the most promising endpoints")
        else:
            print("   1. Target inventory may be server-side rendered only")
            print("   2. Consider intercepting XHR/Fetch requests during user interactions") 
            print("   3. May need to trigger add-to-cart actions to discover inventory APIs")

async def main():
    """Main analysis function"""
    
    # Test with products we know the stock status of
    test_products = [
        "89542109",  # Known available
        "94724987",  # Known OOS
        "94681785"   # Known OOS
    ]
    
    print("🚀 STARTING COMPREHENSIVE TARGET.COM NETWORK ANALYSIS")
    print("="*80)
    print("This will capture ALL network requests when loading product pages")
    print("Looking for real-time inventory APIs, cart APIs, fulfillment APIs, etc.")
    print("="*80)
    
    analyzer = TargetNetworkAnalyzer()
    await analyzer.analyze_product_network_traffic(test_products)

if __name__ == "__main__":
    asyncio.run(main())