import aiohttp
import asyncio
import random
import logging
from typing import Dict, Optional

class StockChecker:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        
    def get_headers(self):
        """Generate randomized headers"""
        chrome_version = random.randint(120, 125)
        return {
            'accept': 'application/json',
            'accept-language': 'en-US,en;q=0.9',
            'origin': 'https://www.target.com',
            'user-agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36',
            'sec-ch-ua': f'"Google Chrome";v="{chrome_version}", "Chromium";v="{chrome_version}", "Not?A_Brand";v="24"',
            'sec-fetch-site': 'same-site',
            'sec-fetch-mode': 'cors'
        }
    
    async def check_stock(self, session: aiohttp.ClientSession, tcin: str) -> Dict:
        """Check stock for a single TCIN"""
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': ''.join(random.choices('0123456789ABCDEF', k=32)),
            'has_size_context': 'true'
        }
        
        try:
            async with session.get(
                self.base_url,
                params=params,
                headers=self.get_headers(),
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                
                if response.status == 429:
                    self.logger.warning("Rate limited! Backing off...")
                    return {
                        'tcin': tcin,
                        'available': False,
                        'status': 'rate_limited',
                        'error': 'Rate limited by Target'
                    }
                
                if response.status != 200:
                    return {
                        'tcin': tcin,
                        'available': False,
                        'status': 'error',
                        'error': f'HTTP {response.status}'
                    }
                
                data = await response.json()
                return self.parse_availability(tcin, data)
                
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout checking {tcin}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'timeout'
            }
        except Exception as e:
            self.logger.error(f"Error checking {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'error',
                'error': str(e)
            }
    
    def parse_availability(self, tcin: str, data: Dict) -> Dict:
        """Parse Target API response for availability"""
        try:
            product = data['data']['product']
            item = product['item']
            
            # Extract key fields
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = product.get('price', {}).get('current_retail', 0)
            
            # Fulfillment info
            fulfillment = item.get('fulfillment', {})
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            # Eligibility rules
            eligibility = item.get('eligibility_rules', {})
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            # STOCK DETECTION LOGIC
            if is_marketplace:
                # Third-party seller
                available = purchase_limit > 0
                seller_type = "third-party"
            else:
                # Target direct
                available = ship_to_guest and purchase_limit >= 2
                seller_type = "target"
            
            # Special handling for pre-orders
            if not ship_to_guest and purchase_limit == 1 and price >= 400:
                # Likely a pre-order bundle
                available = True
                seller_type = "target-preorder"
            
            return {
                'tcin': tcin,
                'name': name[:50],  # Truncate long names
                'price': price,
                'available': available,
                'seller_type': seller_type,
                'purchase_limit': purchase_limit,
                'ship_to_guest': ship_to_guest,
                'status': 'success'
            }
            
        except Exception as e:
            self.logger.error(f"Error parsing response for {tcin}: {e}")
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error',
                'error': str(e)
            }