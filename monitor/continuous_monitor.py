#!/usr/bin/env python3
"""
Continuous Stock Monitor - Fast monitoring without rate limits
"""

import asyncio
import aiohttp
import json
import time
import random
from datetime import datetime
from collections import deque
from pathlib import Path
import logging

class ContinuousStockMonitor:
    def __init__(self):
        # Setup paths
        self.config_path = Path("../product_config.json")  # Config in parent dir
        self.log_dir = Path("../logs")
        self.log_dir.mkdir(exist_ok=True)
        
        # Setup logging
        self.setup_logging()
        
        # Load config
        self.reload_config()
        
        # Rate limiting
        self.request_times = deque(maxlen=100)
        self.max_requests_per_minute = 40  # Conservative limit
        
        # Tracking
        self.total_checks = 0
        self.in_stock_items = {}
        
        # Speed settings
        self.batch_size = 5  # 5 concurrent requests
        self.cycle_delay = 2  # 2 seconds between cycles
        
    def setup_logging(self):
        """Setup all loggers"""
        # Main logger
        self.logger = logging.getLogger('monitor')
        self.logger.setLevel(logging.INFO)
        
        fh = logging.FileHandler(self.log_dir / 'monitor.log')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.logger.addHandler(fh)
        
        # Console output
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
        self.logger.addHandler(ch)
        
        # Purchase logger
        self.purchase_log = logging.getLogger('purchase')
        self.purchase_log.setLevel(logging.INFO)
        ph = logging.FileHandler(self.log_dir / 'purchases.log')
        ph.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.purchase_log.addHandler(ph)
    
    def reload_config(self):
        """Load product config"""
        with open(self.config_path, 'r') as f:
            self.config = json.load(f)
        self.products = [p for p in self.config['products'] if p.get('enabled', True)]
        self.logger.info(f"Loaded {len(self.products)} products")
    
    async def check_product(self, session, product):
        """Check single product availability"""
        tcin = product['tcin']
        max_price = product.get('max_price', 999.99)
        
        url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        params = {
            'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
            'tcin': tcin,
            'store_id': '865',
            'pricing_store_id': '865'
        }
        
        headers = {
            'accept': 'application/json',
            'user-agent': f'Mozilla/5.0 Chrome/{random.randint(120,125)}.0.0.0'
        }
        
        try:
            async with session.get(url, params=params, headers=headers, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Parse availability
                    product_data = data['data']['product']
                    fulfillment = product_data['item']['fulfillment']
                    eligibility = product_data['item'].get('eligibility_rules', {})
                    
                    purchase_limit = fulfillment.get('purchase_limit', 0)
                    ship_to_guest = eligibility.get('ship_to_guest', {}).get('is_active', False)
                    is_marketplace = fulfillment.get('is_marketplace', False)
                    
                    # Get price
                    price = product_data.get('price', {}).get('current_retail', 0)
                    
                    # Check if available
                    if not is_marketplace:
                        available = ship_to_guest and purchase_limit >= 2
                    else:
                        available = purchase_limit > 0
                    
                    # Check if we should buy
                    if available and price <= max_price:
                        if tcin not in self.in_stock_items:
                            # NEW IN STOCK!
                            self.logger.warning(f"IN STOCK: {tcin} at ${price:.2f}")
                            self.purchase_log.info(f"STOCK ALERT: {tcin} - ${price:.2f}")
                            self.in_stock_items[tcin] = time.time()
                            
                            # Trigger buy bot
                            asyncio.create_task(self.trigger_buy(tcin, price))
                    else:
                        if tcin in self.in_stock_items:
                            self.logger.info(f"OUT OF STOCK: {tcin}")
                            del self.in_stock_items[tcin]
                    
                    self.total_checks += 1
                    return True
                    
                elif resp.status == 429:
                    self.logger.error("RATE LIMITED - backing off")
                    await asyncio.sleep(30)
                    
        except Exception as e:
            self.logger.error(f"Error checking {tcin}: {e}")
        
        return False
    
    async def trigger_buy(self, tcin, price):
        """Trigger buy bot asynchronously"""
        try:
            from buy_bot import BuyBot
            bot = BuyBot()
            success = await bot.attempt_purchase(tcin, price)
            if success:
                self.purchase_log.info(f"BUY SUCCESS: {tcin}")
            else:
                self.purchase_log.info(f"BUY FAILED: {tcin}")
        except Exception as e:
            self.logger.error(f"Buy bot error: {e}")
    
    async def check_batch(self, session, batch):
        """Check multiple products concurrently"""
        tasks = []
        for product in batch:
            await asyncio.sleep(random.uniform(0.05, 0.15))  # Small jitter
            tasks.append(self.check_product(session, product))
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def monitor_loop(self):
        """Main monitoring loop"""
        async with aiohttp.ClientSession() as session:
            cycle = 0
            
            while True:
                cycle += 1
                
                # Check in batches
                for i in range(0, len(self.products), self.batch_size):
                    batch = self.products[i:i + self.batch_size]
                    await self.check_batch(session, batch)
                    
                    if i + self.batch_size < len(self.products):
                        await asyncio.sleep(0.3)  # Small delay between batches
                
                # Print status every 10 cycles
                if cycle % 10 == 0:
                    self.logger.info(f"Cycle {cycle}: {self.total_checks} checks, {len(self.in_stock_items)} in stock")
                    print(f"Stats: {self.total_checks} total checks, {len(self.in_stock_items)} currently in stock")
                
                # Reload config every 20 cycles
                if cycle % 20 == 0:
                    self.reload_config()
                
                await asyncio.sleep(self.cycle_delay)
    
    async def start(self):
        """Start monitoring"""
        print("="*60)
        print("CONTINUOUS STOCK MONITOR")
        print(f"Monitoring {len(self.products)} products")
        print(f"Logs: ../logs/")
        print("Press Ctrl+C to stop")
        print("="*60 + "\n")
        
        await self.monitor_loop()

if __name__ == "__main__":
    monitor = ContinuousStockMonitor()
    try:
        asyncio.run(monitor.start())
    except KeyboardInterrupt:
        print("\nStopped")