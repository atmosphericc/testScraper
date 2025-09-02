#!/usr/bin/env python3
"""
Ultra-Fast Monitor Integration
Seamlessly integrates the ultra-fast stock checker with existing TargetMonitor system
"""
import asyncio
import json
import time
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
from dataclasses import dataclass, asdict

from ultra_fast_stock_checker import UltraFastStockChecker, StockResult

@dataclass
class ProductConfig:
    """Enhanced product configuration"""
    tcin: str
    name: str
    max_price: float
    priority: str = "normal"  # high, normal, low
    check_frequency: int = 30  # seconds
    enabled: bool = True
    auto_purchase: bool = False
    last_available: Optional[float] = None
    availability_history: List[bool] = None

    def __post_init__(self):
        if self.availability_history is None:
            self.availability_history = []

class UltraFastMonitorIntegration:
    """
    Integration layer that makes ultra-fast checker work seamlessly with existing system
    - Reads existing product_config.json
    - Integrates with existing BuyBot
    - Provides dashboard API endpoints
    - Handles production/test mode switching
    - Smart priority scheduling
    """
    
    def __init__(self, config_path: str = "config/product_config.json"):
        self.config_path = Path(config_path)
        self.config = {}
        self.products: Dict[str, ProductConfig] = {}
        
        # Core components
        self.ultra_checker: Optional[UltraFastStockChecker] = None
        self.logger = logging.getLogger(__name__)
        
        # Runtime state
        self.is_running = False
        self.last_config_reload = 0
        self.performance_metrics = {}
        
        # Production safety
        self.test_mode = True
        self.purchase_confirmations = {}
        
    async def initialize(self, test_mode: bool = True):
        """Initialize the integrated ultra-fast monitor"""
        self.test_mode = test_mode
        
        self.logger.info(f"Initializing Ultra-Fast Monitor (Test Mode: {test_mode})")
        
        # Load configuration
        await self.load_configuration()
        
        # Initialize ultra-fast checker with purchase callback
        self.ultra_checker = UltraFastStockChecker(
            purchase_callback=self._handle_purchase_opportunity,
            num_background_sessions=self.config.get('settings', {}).get('background_sessions', 4)
        )
        
        await self.ultra_checker.initialize()
        
        self.logger.info("Ultra-Fast Monitor Integration initialized successfully")
        
    async def load_configuration(self):
        """Load and parse product configuration"""
        if not self.config_path.exists():
            self.logger.error(f"Configuration file not found: {self.config_path}")
            # Create default configuration
            await self._create_default_config()
            return
            
        try:
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
                
            # Parse products
            self.products.clear()
            for product_data in self.config.get('products', []):
                product = ProductConfig(
                    tcin=product_data['tcin'],
                    name=product_data.get('name', f"Product {product_data['tcin']}"),
                    max_price=product_data.get('max_price', 999.99),
                    priority=product_data.get('priority', 'normal'),
                    check_frequency=product_data.get('check_frequency', 30),
                    enabled=product_data.get('enabled', True),
                    auto_purchase=product_data.get('auto_purchase', False)
                )
                
                self.products[product.tcin] = product
                
            self.logger.info(f"Loaded {len(self.products)} products from configuration")
            self.last_config_reload = time.time()
            
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise
            
    async def _create_default_config(self):
        """Create default configuration file"""
        default_config = {
            "products": [
                {
                    "tcin": "89542109",
                    "name": "Example Product 1",
                    "max_price": 50.00,
                    "priority": "high",
                    "check_frequency": 10,
                    "enabled": True,
                    "auto_purchase": False
                }
            ],
            "settings": {
                "background_sessions": 4,
                "max_concurrent_checks": 50,
                "check_interval": 5,
                "priority_multiplier": 2,
                "purchase_timeout": 30,
                "test_mode": True
            },
            "purchase": {
                "enabled": False,
                "max_attempts": 3,
                "confirmation_required": True
            }
        }
        
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(default_config, f, indent=2)
            
        self.config = default_config
        self.logger.info(f"Created default configuration at {self.config_path}")
        
    async def _handle_purchase_opportunity(self, stock_result: StockResult):
        """Handle purchase opportunities with safety checks"""
        tcin = stock_result.tcin
        product = self.products.get(tcin)
        
        if not product:
            self.logger.warning(f"Purchase opportunity for unknown product: {tcin}")
            return
            
        # Update availability history
        product.availability_history.append(True)
        product.last_available = time.time()
        
        # Keep only last 10 availability checks
        if len(product.availability_history) > 10:
            product.availability_history = product.availability_history[-10:]
            
        self.logger.info(f"🎯 STOCK AVAILABLE: {product.name} ({tcin}) - Method: {stock_result.method}")
        
        # Production safety checks
        if not self.test_mode and not product.auto_purchase:
            self.logger.info(f"⚠️ Auto-purchase disabled for {tcin}, manual confirmation required")
            self.purchase_confirmations[tcin] = {
                'product': product,
                'stock_result': stock_result,
                'timestamp': time.time()
            }
            return
            
        if self.test_mode:
            self.logger.info(f"🧪 TEST MODE: Would purchase {product.name} for ≤${product.max_price}")
            return
            
        # Trigger actual purchase
        try:
            await self._execute_purchase(product, stock_result)
        except Exception as e:
            self.logger.error(f"Purchase execution failed for {tcin}: {e}")
            
    async def _execute_purchase(self, product: ProductConfig, stock_result: StockResult):
        """Execute purchase using existing BuyBot integration"""
        self.logger.info(f"🚀 EXECUTING PURCHASE: {product.name} ({product.tcin})")
        
        try:
            # Import and use existing BuyBot
            import sys
            sys.path.insert(0, 'src')
            
            # Try to use existing BuyBot
            try:
                from buy_bot import BuyBot
                
                # Initialize BuyBot for this purchase
                buy_bot = BuyBot(
                    headless=True,
                    session_path="sessions/target_storage.json"
                )
                
                # Attempt purchase
                purchase_result = await buy_bot.purchase_product(
                    tcin=product.tcin,
                    max_price=product.max_price
                )
                
                if purchase_result.get('success'):
                    self.logger.info(f"✅ PURCHASE SUCCESSFUL: {product.name}")
                else:
                    self.logger.error(f"❌ PURCHASE FAILED: {product.name} - {purchase_result.get('error')}")
                    
            except ImportError:
                self.logger.warning("BuyBot not available, using mock purchase")
                # Mock purchase for testing
                await asyncio.sleep(1)  # Simulate purchase time
                self.logger.info(f"🎭 MOCK PURCHASE COMPLETED: {product.name}")
                
        except Exception as e:
            self.logger.error(f"Purchase execution error: {e}")
            
    async def start_monitoring(self):
        """Start the ultra-fast monitoring loop"""
        self.is_running = True
        self.logger.info("Starting ultra-fast monitoring loop")
        
        # Get enabled products by priority
        priority_groups = {
            'high': [p for p in self.products.values() if p.enabled and p.priority == 'high'],
            'normal': [p for p in self.products.values() if p.enabled and p.priority == 'normal'],
            'low': [p for p in self.products.values() if p.enabled and p.priority == 'low']
        }
        
        self.logger.info(f"Monitoring: {sum(len(group) for group in priority_groups.values())} products")
        
        # Schedule monitoring tasks
        tasks = []
        
        # High priority products - fastest checking
        if priority_groups['high']:
            tasks.append(self._monitor_priority_group('high', priority_groups['high'], 5))
            
        # Normal priority products
        if priority_groups['normal']:
            tasks.append(self._monitor_priority_group('normal', priority_groups['normal'], 15))
            
        # Low priority products
        if priority_groups['low']:
            tasks.append(self._monitor_priority_group('low', priority_groups['low'], 30))
            
        # Configuration hot-reload task
        tasks.append(self._config_reload_task())
        
        # Performance monitoring task
        tasks.append(self._performance_monitoring_task())
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            self.logger.error(f"Monitoring error: {e}")
        finally:
            self.is_running = False
            
    async def _monitor_priority_group(self, priority: str, products: List[ProductConfig], interval: int):
        """Monitor a specific priority group of products"""
        tcins = [p.tcin for p in products]
        self.logger.info(f"Starting {priority} priority monitoring: {len(tcins)} products every {interval}s")
        
        while self.is_running:
            try:
                start_time = time.time()
                
                # Ultra-fast parallel check
                results = await self.ultra_checker.check_multiple_products(tcins)
                
                check_time = time.time() - start_time
                
                # Update performance metrics
                self.performance_metrics[f'{priority}_group'] = {
                    'last_check_time': check_time,
                    'products_checked': len(tcins),
                    'available_count': sum(1 for r in results.values() if r.available),
                    'last_check': time.time()
                }
                
                self.logger.debug(f"{priority.capitalize()} check: {len(tcins)} products in {check_time:.2f}s")
                
                # Sleep until next check
                await asyncio.sleep(interval)
                
            except Exception as e:
                self.logger.error(f"Error in {priority} priority monitoring: {e}")
                await asyncio.sleep(5)  # Brief pause before retry
                
    async def _config_reload_task(self):
        """Periodically reload configuration for hot updates"""
        while self.is_running:
            try:
                # Check if config file was modified
                if self.config_path.exists():
                    file_mtime = self.config_path.stat().st_mtime
                    if file_mtime > self.last_config_reload:
                        self.logger.info("Configuration file updated, reloading...")
                        await self.load_configuration()
                        
            except Exception as e:
                self.logger.error(f"Config reload error: {e}")
                
            await asyncio.sleep(10)  # Check every 10 seconds
            
    async def _performance_monitoring_task(self):
        """Monitor system performance and log metrics"""
        while self.is_running:
            try:
                if self.ultra_checker:
                    report = await self.ultra_checker.get_performance_report()
                    
                    # Log performance summary
                    self.logger.info(f"Performance: {report.get('total_checks_performed', 0)} checks, "
                                   f"{report.get('accuracy_rate', 0)*100:.1f}% accuracy")
                    
            except Exception as e:
                self.logger.error(f"Performance monitoring error: {e}")
                
            await asyncio.sleep(60)  # Report every minute
            
    async def get_status(self) -> Dict[str, Any]:
        """Get comprehensive system status for dashboard"""
        status = {
            'running': self.is_running,
            'test_mode': self.test_mode,
            'total_products': len(self.products),
            'enabled_products': len([p for p in self.products.values() if p.enabled]),
            'performance_metrics': self.performance_metrics,
            'pending_purchases': len(self.purchase_confirmations),
            'last_config_reload': self.last_config_reload
        }
        
        if self.ultra_checker:
            performance_report = await self.ultra_checker.get_performance_report()
            status['ultra_checker_stats'] = performance_report
            
        return status
        
    async def stop_monitoring(self):
        """Stop monitoring gracefully"""
        self.logger.info("Stopping ultra-fast monitoring...")
        self.is_running = False
        
        if self.ultra_checker:
            await self.ultra_checker.cleanup()
            
        self.logger.info("Ultra-fast monitoring stopped")


async def main():
    """Example usage of integrated ultra-fast monitor"""
    
    # Initialize logging
    logging.basicConfig(level=logging.INFO)
    
    # Create integrated monitor
    monitor = UltraFastMonitorIntegration()
    
    try:
        # Initialize in test mode
        await monitor.initialize(test_mode=True)
        
        # Get initial status
        status = await monitor.get_status()
        print("System Status:")
        print(json.dumps(status, indent=2, default=str))
        
        # Start monitoring (run for 30 seconds as example)
        print("\nStarting monitoring for 30 seconds...")
        monitoring_task = asyncio.create_task(monitor.start_monitoring())
        
        await asyncio.sleep(30)
        
        await monitor.stop_monitoring()
        
    except KeyboardInterrupt:
        print("\nShutting down...")
        await monitor.stop_monitoring()
        
if __name__ == "__main__":
    asyncio.run(main())