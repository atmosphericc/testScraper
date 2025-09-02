#!/usr/bin/env python3
"""
Simple Network Traffic Analyzer for Target.com
Captures network requests to find inventory APIs
"""
import sys
import asyncio
from playwright.async_api import async_playwright
import json
from urllib.parse import urlparse

async def analyze_target_network():
    """Analyze Target.com network traffic for inventory APIs"""
    
    tcin = "89542109"  # Known available product
    network_calls = []
    
    print("STARTING TARGET.COM NETWORK ANALYSIS")
    print("="*60)
    print(f"Analyzing product: {tcin}")
    print("="*60)
    
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        
        # Capture network requests
        def log_request(request):
            network_calls.append({
                'url': request.url,
                'method': request.method,
                'resource_type': request.resource_type
            })
        
        def log_response(response):
            # Find the corresponding request
            for call in network_calls:
                if call['url'] == response.url:
                    call['status'] = response.status
                    call['headers'] = dict(response.headers)
                    break
        
        page.on('request', log_request)
        page.on('response', log_response)
        
        # Load the product page
        url = f"https://www.target.com/p/-/A-{tcin}"
        print(f"Loading: {url}")
        
        await page.goto(url, wait_until='networkidle')
        await page.wait_for_timeout(5000)
        
        await browser.close()
    
    # Analyze the captured calls
    print(f"\nCAPTURED {len(network_calls)} NETWORK REQUESTS")
    print("-"*50)
    
    api_calls = []
    inventory_related = []
    
    # Filter for API calls
    for call in network_calls:
        url = call['url']
        
        # Look for API endpoints
        if any(domain in url for domain in ['redsky.target.com', 'api.target.com']):
            api_calls.append(call)
            
        # Look for inventory-related keywords in URLs
        inventory_keywords = ['inventory', 'stock', 'availability', 'fulfillment', 'cart', 'add']
        if any(keyword in url.lower() for keyword in inventory_keywords):
            inventory_related.append(call)
    
    print(f"API Calls Found: {len(api_calls)}")
    print(f"Inventory-Related: {len(inventory_related)}")
    
    print(f"\nAPI ENDPOINTS DISCOVERED:")
    print("-"*30)
    for call in api_calls:
        parsed = urlparse(call['url'])
        endpoint = f"{parsed.netloc}{parsed.path}"
        status = call.get('status', 'N/A')
        print(f"  {call['method']} {endpoint} ({status})")
    
    if inventory_related:
        print(f"\nINVENTORY-RELATED CALLS:")
        print("-"*30)
        for call in inventory_related:
            print(f"  {call['method']} {call['url']}")
    
    # Save detailed results
    with open('network_analysis_results.json', 'w') as f:
        json.dump({
            'total_calls': len(network_calls),
            'api_calls': api_calls,
            'inventory_related': inventory_related,
            'all_calls': network_calls
        }, f, indent=2)
    
    print(f"\nDetailed results saved to: network_analysis_results.json")

if __name__ == "__main__":
    asyncio.run(analyze_target_network())