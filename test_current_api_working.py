#!/usr/bin/env python3
"""
Test the current working API endpoint to ensure it still works
and then try to understand why batch doesn't work
"""

import requests
import json
import time
import random
import asyncio
import aiohttp

class CurrentAPITester:
    def __init__(self):
        # Use the same endpoints as your working code
        self.pdp_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        self.batch_url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        
    def generate_visitor_id(self):
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
        
    def get_headers(self):
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        ]
        
        user_agent = random.choice(user_agents)
        version = user_agent.split('Chrome/')[1].split('.')[0]
        
        return {
            'accept': 'application/json',
            'accept-encoding': 'gzip, deflate, br, zstd',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'no-cache',
            'origin': 'https://www.target.com',
            'referer': 'https://www.target.com/',
            'sec-ch-ua': f'"Chromium";v="{version}", "Google Chrome";v="{version}", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': user_agent,
        }

    def test_individual_pdp_api(self, tcin: str):
        """Test individual PDP API (this should work)"""
        print(f"\\n🧪 TESTING INDIVIDUAL PDP API (TCIN {tcin})")
        print("-" * 50)
        
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'has_pricing_store_id': 'true',
            'visitor_id': self.generate_visitor_id(),
        }
        
        try:
            response = requests.get(
                self.pdp_url,
                params=params,
                headers=self.get_headers(),
                timeout=15
            )
            
            print(f"  Status: {response.status_code}")
            if response.status_code == 200:
                print(f"  ✅ PDP API working")
                return True
            else:
                print(f"  ❌ PDP API failed: {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"  ❌ PDP API error: {e}")
            return False

    def test_batch_search_api(self, tcins: list):
        """Test batch search API (this seems to be failing)"""
        print(f"\\n🧪 TESTING BATCH SEARCH API ({len(tcins)} TCINs)")
        print("-" * 50)
        
        tcin_query = " OR ".join([f"tcin:{tcin}" for tcin in tcins])
        
        params = {
            'key': self.api_key,
            'category': '5xtg6',
            'channel': 'WEB',
            'count': '24',
            'default_purchasability_filter': 'false',
            'include_sponsored': 'true',
            'keyword': tcin_query,
            'offset': '0',
            'page': f'/s/{tcin_query}',
            'platform': 'desktop',
            'pricing_store_id': self.store_id,
            'store_ids': self.store_id,
            'useragent': 'Mozilla/5.0',
            'visitor_id': self.generate_visitor_id()
        }
        
        try:
            response = requests.get(
                self.batch_url,
                params=params,
                headers=self.get_headers(),
                timeout=15
            )
            
            print(f"  Status: {response.status_code}")
            if response.status_code == 200:
                print(f"  ✅ Batch API working")
                return True
            else:
                print(f"  ❌ Batch API failed: {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"  ❌ Batch API error: {e}")
            return False

    async def test_async_batch(self, tcins: list):
        """Test batch API with async/aiohttp (same as your BatchStockChecker)"""
        print(f"\\n🧪 TESTING ASYNC BATCH API ({len(tcins)} TCINs)")
        print("-" * 50)
        
        tcin_query = " OR ".join([f"tcin:{tcin}" for tcin in tcins])
        
        params = {
            'key': self.api_key,
            'category': '5xtg6',
            'channel': 'WEB', 
            'count': '24',
            'default_purchasability_filter': 'false',
            'include_sponsored': 'true',
            'keyword': tcin_query,
            'offset': '0',
            'page': f'/s/{tcin_query}',
            'platform': 'desktop',
            'pricing_store_id': self.store_id,
            'store_ids': self.store_id,
            'useragent': 'Mozilla/5.0',
            'visitor_id': self.generate_visitor_id()
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.batch_url,
                    params=params,
                    headers=self.get_headers(),
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    
                    print(f"  Async status: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        print(f"  ✅ Async batch API working")
                        
                        # Quick check of data structure
                        search_data = data.get('data', {}).get('search', {})
                        products = search_data.get('products', [])
                        print(f"  Found {len(products)} products")
                        
                        return True
                    else:
                        text = await response.text()
                        print(f"  ❌ Async batch failed: {text[:200]}")
                        return False
                        
        except Exception as e:
            print(f"  ❌ Async batch error: {e}")
            return False

    def test_dashboard_status(self):
        """Test if your dashboard is actually running and working"""
        print(f"\\n🧪 TESTING DASHBOARD STATUS")
        print("-" * 50)
        
        dashboard_urls = [
            'http://localhost:5000',  # Legacy dashboard
            'http://localhost:5001',  # Ultra-fast dashboard
        ]
        
        for url in dashboard_urls:
            try:
                response = requests.get(f"{url}/", timeout=5)
                print(f"  {url}: Status {response.status_code}")
                if response.status_code == 200:
                    print(f"    ✅ Dashboard running")
                    
                    # Try to get API data
                    api_response = requests.get(f"{url}/api/products", timeout=5)
                    print(f"    API endpoint: Status {api_response.status_code}")
                    if api_response.status_code == 200:
                        data = api_response.json()
                        print(f"    API data: {len(data.get('products', []))} products")
                    
            except Exception as e:
                print(f"  {url}: ❌ {e}")

def main():
    tester = CurrentAPITester()
    
    test_tcins = ['94681776', '94723520', '94827553']
    
    print("🔍 TESTING CURRENT API STATUS")
    print("Checking which APIs are working vs failing")
    print("="*60)
    
    # Test individual PDP API for each TCIN
    pdp_working = 0
    for tcin in test_tcins:
        if tester.test_individual_pdp_api(tcin):
            pdp_working += 1
    
    print(f"\\n📊 PDP API Results: {pdp_working}/{len(test_tcins)} working")
    
    # Test batch API
    batch_sync_working = tester.test_batch_search_api(test_tcins)
    print(f"📊 Batch API (sync): {'✅ Working' if batch_sync_working else '❌ Failed'}")
    
    # Test async batch
    batch_async_working = asyncio.run(tester.test_async_batch(test_tcins))
    print(f"📊 Batch API (async): {'✅ Working' if batch_async_working else '❌ Failed'}")
    
    # Test dashboard
    tester.test_dashboard_status()
    
    print(f"\\n🎯 CONCLUSIONS:")
    if pdp_working > 0:
        print("✅ Individual PDP API is working - we can get preorder data")
    if not (batch_sync_working or batch_async_working):
        print("❌ Batch API seems to be down/deprecated")
        print("💡 Your dashboard might need to fall back to individual API calls")
    
    print(f"\\n🔍 NEXT STEPS:")
    print("Since individual PDP API works, let's focus on finding the")
    print("correct availability logic in that API response data.")

if __name__ == "__main__":
    main()