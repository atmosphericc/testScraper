import aiohttp
import asyncio
import random
import logging
import time
from typing import Dict, List
from datetime import datetime, timedelta

class BatchStockChecker:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        self.consecutive_errors = 0
        self.last_error_time = None
        
    def generate_visitor_id(self):
        """Generate realistic visitor ID"""
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('0123456789ABCDEF', k=16))
        return f"{timestamp:016X}{random_suffix}"
        
    def get_headers(self):
        """Generate randomized headers"""
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        ]
        
        user_agent = random.choice(user_agents)
        version = user_agent.split('Chrome/')[1].split('.')[0] if 'Chrome/' in user_agent else "130"
        
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

    async def check_multiple_products(self, session: aiohttp.ClientSession, tcins: List[str]) -> List[Dict]:
        """Check multiple products in a single request - MUCH more efficient"""
        
        # Create search query for multiple TCINs
        tcin_query = " OR ".join([f"tcin:{tcin}" for tcin in tcins])
        
        params = {
            'key': self.api_key,
            'category': '5xtg6',  # General category
            'channel': 'WEB',
            'count': '24',
            'default_purchasability_filter': 'false',
            'include_sponsored': 'true',
            'keyword': tcin_query,  # Search for multiple TCINs
            'offset': '0',
            'page': f'/s/{tcin_query}',
            'platform': 'desktop',
            'pricing_store_id': self.store_id,
            'store_ids': self.store_id,
            'useragent': 'Mozilla/5.0',
            'visitor_id': self.generate_visitor_id()
        }
        
        # Human-like delay between requests
        base_delay = random.uniform(8, 15)  # 8-15 seconds between batch requests
        jitter = random.uniform(-2, 3) 
        await asyncio.sleep(max(5, base_delay + jitter))
        
        try:
            async with session.get(
                self.base_url,
                params=params,
                headers=self.get_headers(),
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                
                if response.status == 200:
                    data = await response.json()
                    return self.parse_batch_response(tcins, data)
                else:
                    self.logger.warning(f"Batch request failed: HTTP {response.status}")
                    # Fallback to individual requests with longer delays
                    return await self.fallback_individual_checks(session, tcins)
                    
        except Exception as e:
            self.logger.error(f"Batch check error: {e}")
            return await self.fallback_individual_checks(session, tcins)
    
    def parse_batch_response(self, tcins: List[str], data: Dict) -> List[Dict]:
        """Parse batch search response"""
        results = []
        
        # Initialize all as unavailable
        for tcin in tcins:
            results.append({
                'tcin': tcin,
                'available': False,
                'status': 'not_found'
            })
        
        # Parse found products
        search_response = data.get('data', {}).get('search', {})
        products = search_response.get('products', [])
        
        for product in products:
            tcin = product.get('tcin')
            if tcin in tcins:
                # Update result for this TCIN
                for result in results:
                    if result['tcin'] == tcin:
                        result.update(self.parse_product_availability(tcin, product))
                        break
        
        return results
    
    def parse_product_availability(self, tcin: str, product: Dict) -> Dict:
        """Parse individual product from batch response"""
        try:
            item = product.get('item', {})
            price = product.get('price', {}).get('current_retail', 0)
            name = item.get('product_description', {}).get('title', 'Unknown')
            
            # Check availability
            fulfillment = item.get('fulfillment', {})
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            eligibility = item.get('eligibility_rules', {})
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            # Stock logic
            if is_marketplace:
                available = purchase_limit > 0
            else:
                available = ship_to_guest and purchase_limit >= 1
            
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': price,
                'available': available,
                'purchase_limit': purchase_limit,
                'status': 'success'
            }
            
        except Exception as e:
            self.logger.error(f"Error parsing {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error'
            }
    
    async def fallback_individual_checks(self, session: aiohttp.ClientSession, tcins: List[str]) -> List[Dict]:
        """Fallback to individual checks with very long delays"""
        results = []
        
        for i, tcin in enumerate(tcins):
            if i > 0:  # Don't delay before first request
                # Very long delays for individual fallback
                await asyncio.sleep(random.uniform(20, 40))  
            
            # Use your existing individual check logic here
            result = await self.check_single_fallback(session, tcin)
            results.append(result)
        
        return results
    
    async def check_single_fallback(self, session: aiohttp.ClientSession, tcin: str) -> Dict:
        """Individual TCIN check as fallback"""
        # This would use your existing single product check logic
        return {
            'tcin': tcin,
            'available': False,
            'status': 'fallback_timeout'
        }