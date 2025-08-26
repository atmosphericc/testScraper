#!/usr/bin/env python3
"""
Continuous Stock Monitor
Checks multiple products as fast as possible without hitting rate limits
"""

import asyncio
import aiohttp
import json
import time
import random
import sys
import os
from datetime import datetime
from collections import deque
from typing import Dict, List, Any
import logging
from pathlib import Path

# Add parent dir to path to import existing code if needed
sys.path.insert(0, str(Path(__file__).parent.parent))

class ContinuousStockMonitor:
    def __init__(self, config_path="product_config.json", log_dir="logs"):
        """Initialize the monitor with config and logging"""
        self.config_path = config_path
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # Setup logging
        self.setup_logging()
        
        # Load configuration
        self.reload_config()
        
        # Rate limiting tracking
        self.request_times = deque(maxlen=100)
        self.rate_limit_window = 60  # seconds
        self.max_requests_per_window = 50  # stay well under any limits
        
        # Performance tracking
        self.total_checks = 0
        self.total_errors = 0
        self.last_in_stock = {}
        self.session = None
        
        # Timing configuration
        self.batch_size = 5  # concurrent requests per batch
        self.batch_delay = 0.5  # seconds between batches
        self.cycle_delay = 3  # seconds between full cycles
        
        self.logger.info("Monitor initialized successfully")
    
    def setup_logging(self):
        """Configure comprehensive logging"""
        # Main monitor log
        self.logger = logging.getLogger('stock_monitor')
        self.logger.setLevel(logging.INFO)
        
        # File handler for all monitor activity
        fh = logging.FileHandler(self.log_dir / 'monitor.log')
        fh.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        self.logger.addHandler(fh)
        
        # Console handler for important events
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
        self.logger.addHandler(ch)
        
        # Separate purchase logger
        self.purchase_logger = logging.getLogger('purchases')
        self.purchase_logger.setLevel(logging.INFO)
        ph = logging.FileHandler(self.log_dir / 'purchases.log')
        ph.setFormatter(logging.Formatter(
            '%(asctime)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        self.purchase_logger.addHandler(ph)
        
        # Error logger
        self.error_logger = logging.getLogger('errors')
        self.error_logger.setLevel(logging.ERROR)
        eh = logging.FileHandler(self.log_dir / 'errors.log')
        eh.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s\n%(exc_info)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        self.error_logger.addHandler(eh)
    
    def reload_config(self):
        """Reload configuration from file"""
        try:
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
                self.products = [p for p in self.config['products'] if p.get('enabled', True)]
                self.settings = self.config.get('settings', {})
                self.logger.info(f"Config reloaded: {len(self.products)} active products")
        except Exception as e:
            self.error_logger.error(f"Failed to reload config: {e}", exc_info=True)
            if not hasattr(self, 'config'):
                raise
    
    async def check_rate_limit(self):
        """Adaptive rate limiting to prevent hitting API limits"""
        now = time.time()
        
        # Remove old requests outside window
        cutoff = now - self.rate_limit_window
        while self.request_times and self.request_times[0] < cutoff:
            self.request_times.popleft()
        
        # Check if we're approaching limit
        if len(self.request_times) >= self.max_requests_per_window * 0.8:
            # Getting close to limit, slow down
            wait_time = self.rate_limit_window - (now - self.request_times[0]) + 1
            self.logger.warning(f"Approaching rate limit, waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)
        
        # Record this request
        self.request_times.append(now)
    
    async def fetch_product_status(self, session: aiohttp.ClientSession, product: Dict) -> Dict[str, Any]:
        """Check single product status"""
        tcin = product['tcin']
        max_price = product.get('max_price', 999.99)
        
        # Rate limit check
        await self.check_rate_limit()
        
        url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        params = {
            'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
            'tcin': tcin,
            'is_bot': 'false',
            'store_id': '865',
            'pricing_store_id': '865',
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': f"{random.randint(100000,999999)}",
            'channel': 'WEB'
        }
        
        headers = {
            'accept': 'application/json',
            'user-agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Safari/{random.randint(600,610)}.36'
        }
        
        start_time = time.time()
        
        try:
            async with session.get(url, params=params, headers=headers, timeout=5) as response:
                self.total_checks += 1
                
                if response.status == 429:
                    self.logger.error(f"Rate limited on {tcin}")
                    await asyncio.sleep(30)
                    return {'tcin': tcin, 'status': 'rate_limited', 'available': False}
                
                if response.status != 200:
                    return {'tcin': tcin, 'status': 'error', 'available': False}
                
                data = await response.json()
                response_time = time.time() - start_time
                
                # Parse product data
                result = self.parse_product_availability(data, product)
                result['response_time'] = response_time
                
                # Log if status changed
                if result['available'] and tcin not in self.last_in_stock:
                    self.logger.warning(f"NOW IN STOCK: {tcin} - {result.get('name', 'Unknown')} at ${result.get('price', 0):.2f}")
                    self.purchase_logger.info(f"STOCK ALERT: {tcin} - {result.get('name')} - ${result.get('price'):.2f} - Triggering buy")
                    self.last_in_stock[tcin] = time.time()
                    
                    # Trigger buy bot
                    asyncio.create_task(self.trigger_buy_bot(product, result))
                
                elif not result['available'] and tcin in self.last_in_stock:
                    self.logger.info(f"Now OUT OF STOCK: {tcin}")
                    del self.last_in_stock[tcin]
                
                return result
                
        except asyncio.TimeoutError:
            self.total_errors += 1
            return {'tcin': tcin, 'status': 'timeout', 'available': False}
        except Exception as e:
            self.total_errors += 1
            self.error_logger.error(f"Error checking {tcin}: {e}", exc_info=True)
            return {'tcin': tcin, 'status': 'error', 'available': False}
    
    def parse_product_availability(self, api_response: Dict, product_config: Dict) -> Dict[str, Any]:
        """Parse API response to determine availability"""
        tcin = product_config['tcin']
        max_price = product_config.get('max_price', 999.99)
        
        try:
            product_data = api_response['data']['product']
            
            # Extract name
            import html
            name = html.unescape(product_data['item']['product_description']['title'])
            
            # Extract price
            price_data = product_data.get('price', {})
            current_price = price_data.get('current_retail', 0)
            if not current_price:
                current_price = 0
            
            # Check availability
            fulfillment = product_data['item']['fulfillment']
            eligibility = product_data['item'].get('eligibility_rules', {})
            
            is_marketplace = fulfillment.get('is_marketplace', False)
            purchase_limit = fulfillment.get('purchase_limit', 0)
            ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
            
            # Determine if available using bulletproof logic
            if is_marketplace:
                seller_type = "third-party"
                available = purchase_limit > 0 and not self.settings.get('only_target_direct', False)
            else:
                seller_type = "target"
                available = ship_to_guest and purchase_limit >= 2
            
            # Check if we want to buy
            want_to_buy = available and current_price <= max_price
            
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': float(current_price),
                'available': available,
                'want_to_buy': want_to_buy,
                'seller_type': seller_type,
                'purchase_limit': purchase_limit,
                'status': 'success'
            }
            
        except Exception as e:
            self.error_logger.error(f"Parse error for {tcin}: {e}", exc_info=True)
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error'
            }
    
    async def trigger_buy_bot(self, product: Dict, status: Dict):
        """Trigger the buy bot asynchronously"""
        try:
            # Import and run the buy bot
            from buy_bot import BuyBot
            
            bot = BuyBot()
            success = await bot.attempt_purchase(product['tcin'], status.get('price', 0))
            
            if success:
                self.purchase_logger.info(f"Buy bot SUCCESS for {product['tcin']}")
            else:
                self.purchase_logger.warning(f"Buy bot FAILED for {product['tcin']}")
                
        except Exception as e:
            self.error_logger.error(f"Buy bot error for {product['tcin']}: {e}")
            # Don't crash the monitor if buy bot fails
    
    async def check_batch(self, session: aiohttp.ClientSession, batch: List[Dict]) -> List[Dict]:
        """Check a batch of products concurrently"""
        tasks = []
        for product in batch:
            # Small random delay to avoid thundering herd
            await asyncio.sleep(random.uniform(0.05, 0.15))
            tasks.append(self.fetch_product_status(session, product))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle any exceptions
        clean_results = []
        for r in results:
            if isinstance(r, Exception):
                self.error_logger.error(f"Batch error: {r}")
                clean_results.append({'status': 'exception', 'available': False})
            else:
                clean_results.append(r)
        
        return clean_results
    
    async def run_cycle(self, session: aiohttp.ClientSession, cycle_num: int):
        """Run one complete monitoring cycle"""
        self.logger.info(f"Cycle {cycle_num}: Checking {len(self.products)} products")
        
        all_results = []
        
        # Process in batches
        for i in range(0, len(self.products), self.batch_size):
            batch = self.products[i:i + self.batch_size]
            batch_results = await self.check_batch(session, batch)
            all_results.extend(batch_results)
            
            # Brief pause between batches
            if i + self.batch_size < len(self.products):
                await asyncio.sleep(self.batch_delay)
        
        # Log summary
        available = sum(1 for r in all_results if r.get('available'))
        errors = sum(1 for r in all_results if r.get('status') != 'success')
        avg_response = sum(r.get('response_time', 0) for r in all_results) / len(all_results) if all_results else 0
        
        self.logger.info(
            f"Cycle {cycle_num} complete: {available}/{len(all_results)} available, "
            f"{errors} errors, avg response: {avg_response:.2f}s"
        )
        
        return all_results
    
    async def monitor_loop(self):
        """Main monitoring loop"""
        cycle = 0
        
        connector = aiohttp.TCPConnector(limit=10)  # Limit concurrent connections
        async with aiohttp.ClientSession(connector=connector) as session:
            self.session = session
            
            while True:
                cycle += 1
                
                try:
                    # Run one cycle
                    await self.run_cycle(session, cycle)
                    
                    # Print stats periodically
                    if cycle % 10 == 0:
                        self.print_stats(cycle)
                    
                    # Reload config periodically
                    if cycle % 20 == 0:
                        self.reload_config()
                    
                    # Wait before next cycle
                    await asyncio.sleep(self.cycle_delay)
                    
                except Exception as e:
                    self.error_logger.error(f"Cycle {cycle} failed: {e}", exc_info=True)
                    await asyncio.sleep(5)  # Wait longer on error
    
    def print_stats(self, cycle: int):
        """Print monitoring statistics"""
        error_rate = (self.total_errors / self.total_checks * 100) if self.total_checks > 0 else 0
        
        stats = f"""
========== MONITOR STATS (Cycle {cycle}) ==========
Total Checks: {self.total_checks}
Total Errors: {self.total_errors} ({error_rate:.1f}%)
Currently In Stock: {len(self.last_in_stock)}
Request Rate: {len(self.request_times)}/{self.rate_limit_window}s
================================================
"""
        print(stats)
        self.logger.info(stats.replace('\n', ' '))
    
    async def start(self):
        """Start the monitoring system"""
        print("=" * 60)
        print("CONTINUOUS STOCK MONITOR")
        print(f"Monitoring {len(self.products)} products")
        print(f"Batch Size: {self.batch_size} concurrent requests")
        print(f"Cycle Delay: {self.cycle_delay} seconds")
        print(f"Logs Directory: {self.log_dir}")
        print("=" * 60)
        print("\nPress Ctrl+C to stop\n")
        
        try:
            await self.monitor_loop()
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            self.print_stats(self.total_checks)
            self.logger.info("Monitor stopped by user")


if __name__ == "__main__":
    monitor = ContinuousStockMonitor()
    asyncio.run(monitor.start())