import asyncio
import aiohttp
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set
import sys
import random
import sqlite3
import requests

from stock_checker import StockChecker
from buy_bot import BuyBot
from session_manager import SessionManager
from browser_profile_manager import BrowserProfileManager
from config_watcher import HotReloadMonitor

class TargetMonitor:
    def __init__(self, config_path="config/product_config.json"):
        self.config_path = Path(config_path)
        self.load_config()
        self.setup_logging()
        
        # Load enabled proxies
        enabled_proxies = []
        if 'proxies' in self.config:
            enabled_proxies = [p for p in self.config['proxies'] if p.get('enabled', False)]
        
        # Initialize components
        self.session_manager = SessionManager(self.config['settings']['session']['storage_path'])
        self.stock_checker = StockChecker(proxies=enabled_proxies)
        self.buy_bot = BuyBot(self.config['settings']['session']['storage_path'])
        
        # Set analytics callback for stock checker
        self.stock_checker.record_proxy_analytics = self.record_analytics_proxy
        
        if enabled_proxies:
            self.logger.info(f"Loaded {len(enabled_proxies)} enabled proxies for rotation")
        else:
            self.logger.info("No proxies enabled - using direct connection")
        
        # Initialize browser profile manager (4. Multiple Browser Profiles)
        self.profile_manager = BrowserProfileManager(num_profiles=5)
        self.logger.info(f"Created {len(self.profile_manager.profiles)} unique browser profiles")
        
        # Initialize hot reload monitor (6. Auto-Config Reloading)
        self.hot_reload = HotReloadMonitor(self)
        self.hot_reload.start_hot_reload()
        
        # Tracking
        self.purchase_cooldowns = {}  # tcin -> last_attempt_time
        self.total_checks = 0
        self.session_checks = 0
        self.in_stock_items = set()
        self.active_purchases = set()  # TCINs currently being purchased
        
        # Dashboard Analytics Integration
        self.dashboard_url = 'http://localhost:5000'  # Dashboard API endpoint
        self.analytics_enabled = True
        
        # Rate limiting
        self.rate_limited = False
        self.rate_limit_until = None
        
    def load_config(self):
        """Load configuration from JSON"""
        with open(self.config_path, 'r') as f:
            self.config = json.load(f)
        
        # Filter to enabled products only
        self.products = [p for p in self.config['products'] if p.get('enabled', True)]
        
    def setup_logging(self):
        """Configure logging"""
        log_dir = Path(self.config['settings']['logging']['log_dir'])
        log_dir.mkdir(exist_ok=True)
        
        # Main logger
        logging.basicConfig(
            level=self.config['settings']['logging']['level'],
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / 'monitor.log'),
                logging.StreamHandler()
            ]
        )
        
        self.logger = logging.getLogger(__name__)
        
        # Purchase logger (separate file)
        purchase_logger = logging.getLogger('purchases')
        purchase_handler = logging.FileHandler(log_dir / 'purchases.log')
        purchase_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        purchase_logger.addHandler(purchase_handler)
        purchase_logger.setLevel(logging.INFO)
        
    def is_in_cooldown(self, tcin: str) -> bool:
        """Check if a product is in purchase cooldown"""
        if tcin not in self.purchase_cooldowns:
            return False
        
        cooldown_minutes = self.config['settings']['purchase']['cooldown_after_attempt_minutes']
        cooldown_until = self.purchase_cooldowns[tcin] + timedelta(minutes=cooldown_minutes)
        
        if datetime.now() < cooldown_until:
            remaining = (cooldown_until - datetime.now()).seconds // 60
            self.logger.debug(f"{tcin} in cooldown for {remaining} more minutes")
            return True
        
        return False
    
    def record_analytics(self, endpoint: str, **data):
        """Record analytics data to dashboard"""
        if not self.analytics_enabled:
            return
            
        try:
            url = f"{self.dashboard_url}/api/{endpoint}"
            requests.get(url, params=data, timeout=2)
        except Exception as e:
            # Silently fail - don't let analytics break monitoring
            pass
    
    def record_analytics_proxy(self, proxy_host: str, success: bool, response_time: int = None, error: str = None):
        """Record proxy performance analytics"""
        if not self.analytics_enabled:
            return
            
        try:
            url = f"{self.dashboard_url}/api/record-proxy"
            requests.get(url, params={
                'proxy_host': proxy_host,
                'success': success,
                'response_time': response_time,
                'error': error or ''
            }, timeout=2)
        except Exception:
            # Silently fail - don't let analytics break monitoring
            pass
    
    async def check_and_buy(self, session: aiohttp.ClientSession, product: Dict):
        """Check stock and initiate purchase if available"""
        tcin = product['tcin']
        product_name = product.get('name', 'Unknown Product')
        
        # Skip if currently being purchased
        if tcin in self.active_purchases:
            self.logger.debug(f"Skipping {tcin} - purchase in progress")
            return
        
        # Skip if in cooldown
        if self.is_in_cooldown(tcin):
            return
        
        # SMART TIMING: Add unique delay per product to spread requests
        product_index = next((i for i, p in enumerate(self.products) if p['tcin'] == tcin), 0)
        stagger_delay = product_index * random.uniform(2, 5)  # 2-5 seconds per product
        await asyncio.sleep(stagger_delay)
        
        # Check stock (with response time tracking)
        start_time = asyncio.get_event_loop().time()
        result = await self.stock_checker.check_stock(session, tcin)
        response_time = int((asyncio.get_event_loop().time() - start_time) * 1000)  # Convert to ms
        
        self.total_checks += 1
        
        # Record stock check analytics
        self.record_analytics('record-stock',
            tcin=tcin,
            name=product_name,
            in_stock=result.get('available', False),
            price=result.get('price'),
            availability=result.get('availability_text', ''),
            response_time=response_time
        )
        
        # Handle rate limiting
        if result.get('status') == 'rate_limited':
            self.rate_limited = True
            self.rate_limit_until = datetime.now() + timedelta(seconds=30)
            self.logger.warning("Rate limited! Pausing for 30 seconds...")
            return
        
        # Process availability
        if result.get('available'):
            # Add price to result
            result['max_price'] = product.get('max_price', 999.99)
            
            if tcin not in self.in_stock_items:
                self.logger.warning(f"IN STOCK: {product['name']} at ${result.get('price', 0):.2f}")
                self.in_stock_items.add(tcin)
                
                # Trigger purchase
                self.active_purchases.add(tcin)
                asyncio.create_task(self.execute_purchase(tcin, result))
        else:
            if tcin in self.in_stock_items:
                self.logger.info(f"OUT OF STOCK: {product['name']}")
                self.in_stock_items.discard(tcin)
    
    async def execute_purchase(self, tcin: str, stock_info: Dict):
        """Execute purchase attempt asynchronously"""
        product_name = stock_info.get('name', 'Unknown')
        price = stock_info.get('price', 0)
        
        try:
            self.logger.info(f"Initiating purchase for {tcin}")
            
            # Prepare product info for buy bot
            product_info = {
                'tcin': tcin,
                'name': product_name,
                'price': price,
                'max_price': stock_info.get('max_price', 999.99)
            }
            
            # Attempt purchase
            result = await self.buy_bot.attempt_purchase(product_info)
            
            # Record attempt time
            self.purchase_cooldowns[tcin] = datetime.now()
            
            # Record purchase analytics
            success = result.get('success', False)
            reason = result.get('reason', result.get('message', 'unknown'))
            order_number = result.get('order_number')
            
            self.record_analytics('record-purchase',
                tcin=tcin,
                name=product_name,
                success=success,
                reason=reason,
                price=price,
                order_number=order_number or ''
            )
            
            # Log result
            if success:
                self.logger.warning(f"PURCHASE SUCCESS: {tcin} - {reason}")
                if order_number:
                    self.logger.warning(f"ORDER NUMBER: {order_number}")
            else:
                self.logger.warning(f"PURCHASE FAILED: {tcin} - {reason}")
            
        except Exception as e:
            self.logger.error(f"Purchase error for {tcin}: {e}")
            # Record failed purchase attempt
            self.record_analytics('record-purchase',
                tcin=tcin,
                name=product_name,
                success=False,
                reason=f"Error: {str(e)}",
                price=price
            )
        finally:
            # Remove from active purchases
            self.active_purchases.discard(tcin)
    
    async def monitor_batch(self, session: aiohttp.ClientSession, batch: List[Dict]):
        """Monitor a batch of products"""
        tasks = []
        for product in batch:
            # Small random delay between requests in batch
            await asyncio.sleep(0.1)
            tasks.append(self.check_and_buy(session, product))
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def validate_session_periodic(self):
        """Periodically validate session in background"""
        while True:
            try:
                await asyncio.sleep(1800)  # Check every 30 minutes
                
                self.logger.info("Validating session...")
                if not await self.session_manager.validate_session():
                    self.logger.error("SESSION INVALID - Please re-login!")
                    print("\n" + "="*60)
                    print("SESSION EXPIRED - MONITORING STOPPED")
                    print("Run: python setup.py login")
                    print("="*60)
                    sys.exit(1)
                else:
                    self.logger.info("Session still valid")
                    
            except Exception as e:
                self.logger.error(f"Session validation error: {e}")
    
    async def run(self):
        """Main monitoring loop"""
        self.logger.info("="*60)
        self.logger.info(f"TARGET MONITOR STARTED - {len(self.products)} products")
        self.logger.info(f"Mode: {self.config['settings']['mode']}")
        self.logger.info("="*60)
        
        # Validate session before starting
        self.logger.info("Validating session...")
        if not await self.session_manager.validate_session(force=True):
            self.logger.error("Session invalid! Please run: python setup.py login")
            sys.exit(1)
        
        self.logger.info("Session valid - starting monitoring")
        
        # Start session validator in background
        asyncio.create_task(self.validate_session_periodic())
        
        # Main monitoring loop
        async with aiohttp.ClientSession() as session:
            cycle = 0
            
            while True:
                cycle += 1
                
                # Check if rate limited
                if self.rate_limited:
                    if datetime.now() < self.rate_limit_until:
                        wait_seconds = (self.rate_limit_until - datetime.now()).seconds
                        await asyncio.sleep(min(wait_seconds, 5))
                        continue
                    else:
                        self.rate_limited = False
                        self.logger.info("Rate limit expired, resuming...")
                
                # Reload config every 50 cycles
                if cycle % 50 == 0:
                    self.load_config()
                    self.logger.info(f"Reloaded config - {len(self.products)} products enabled")
                
                # Process in batches
                batch_size = self.config['settings']['rate_limit']['batch_size']
                batch_delay = self.config['settings']['rate_limit']['batch_delay_seconds']
                
                for i in range(0, len(self.products), batch_size):
                    batch = self.products[i:i + batch_size]
                    await self.monitor_batch(session, batch)
                    
                    # Delay between batches
                    if i + batch_size < len(self.products):
                        await asyncio.sleep(batch_delay)
                
                # Status update every 10 cycles
                if cycle % 10 == 0:
                    self.logger.info(
                        f"Status - Cycle: {cycle} | "
                        f"Checks: {self.total_checks} | "
                        f"In Stock: {len(self.in_stock_items)} | "
                        f"Active Purchases: {len(self.active_purchases)}"
                    )
                
                # Small delay before next cycle
                await asyncio.sleep(1)