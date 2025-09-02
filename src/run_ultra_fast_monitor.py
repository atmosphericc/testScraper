#!/usr/bin/env python3
"""
Ultra-Fast Target Monitor - Main Integration Class
Combines all ultra-fast components for zero missed opportunities
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

from ultra_fast_stock_checker import UltraFastStockChecker, StockResult
from ultra_fast_config_manager import UltraFastConfigManager
from ultra_fast_safety_system import UltraFastSafetySystem
from ultra_fast_smart_scheduler import UltraFastSmartScheduler

@dataclass
class ProductConfig:
    """Product configuration for ultra-fast monitoring"""
    tcin: str
    name: str
    max_price: float
    quantity: int
    enabled: bool
    priority: int = 1
    check_frequency: float = 0.17  # 10 seconds

class UltraFastTargetMonitor:
    """
    Ultra-Fast Target Monitor with Zero Missed Opportunities
    
    Integrates all ultra-fast components:
    - UltraFastStockChecker for sub-3s stock checking
    - Smart scheduling based on priority
    - Configuration hot-reloading
    - Safety systems for purchase protection
    - Real-time dashboard integration
    """
    
    def __init__(self, test_mode: bool = True, config_path: str = "config/product_config.json"):
        self.test_mode = test_mode
        self.config_path = Path(config_path)
        self.logger = logging.getLogger(__name__)
        
        # Core components
        self.stock_checker: Optional[UltraFastStockChecker] = None
        self.config_manager: Optional[UltraFastConfigManager] = None
        self.safety_system: Optional[UltraFastSafetySystem] = None
        self.scheduler: Optional[UltraFastSmartScheduler] = None
        
        # State
        self.products: List[ProductConfig] = []
        self.running = False
        self.last_results: Dict[str, StockResult] = {}
        
    async def initialize(self):
        """Initialize all ultra-fast components"""
        self.logger.info("🚀 Initializing Ultra-Fast Target Monitor...")
        
        # Initialize configuration manager
        self.config_manager = UltraFastConfigManager(self.config_path)
        await self.config_manager.initialize()
        
        # Initialize safety system
        self.safety_system = UltraFastSafetySystem(test_mode=self.test_mode)
        await self.safety_system.initialize()
        
        # Initialize stock checker with purchase callback
        self.stock_checker = UltraFastStockChecker(
            purchase_callback=self._handle_purchase_opportunity,
            num_background_sessions=4
        )
        await self.stock_checker.initialize()
        
        # Load products and initialize scheduler
        await self._load_products()
        
        # Initialize smart scheduler
        self.scheduler = UltraFastSmartScheduler(
            products=self.products,
            stock_check_callback=self._batch_stock_check
        )
        
        self.logger.info("✅ Ultra-Fast Monitor initialized successfully")
        
    async def _load_products(self):
        """Load products from configuration"""
        config = await self.config_manager.get_config()
        self.products = []
        
        for product_data in config.get('products', []):
            if product_data.get('enabled', True):
                product = ProductConfig(
                    tcin=product_data['tcin'],
                    name=product_data['name'],
                    max_price=product_data['max_price'],
                    quantity=product_data['quantity'],
                    enabled=product_data['enabled'],
                    priority=product_data.get('priority', 1),
                    check_frequency=product_data.get('check_frequency', 0.17)
                )
                self.products.append(product)
                
        self.logger.info(f"Loaded {len(self.products)} enabled products")
        
    async def _handle_purchase_opportunity(self, stock_result: StockResult):
        """Handle when a product becomes available"""
        product = next((p for p in self.products if p.tcin == stock_result.tcin), None)
        if not product:
            return
            
        self.logger.info(f"🎯 STOCK ALERT: {product.name} ({stock_result.tcin}) is available!")
        
        # Safety check
        purchase_decision = await self.safety_system.evaluate_purchase(
            tcin=stock_result.tcin,
            name=product.name,
            max_price=product.max_price,
            stock_result=stock_result
        )
        
        if purchase_decision['proceed']:
            if self.test_mode:
                self.logger.info(f"🧪 TEST MODE: Would purchase {product.name}")
                self.logger.info(f"   Price: ${product.max_price}, Qty: {product.quantity}")
            else:
                # TODO: Integrate with actual purchase bot
                self.logger.info(f"🛒 ATTEMPTING PURCHASE: {product.name}")
                # await self._attempt_purchase(product, stock_result)
        else:
            self.logger.warning(f"⚠️ Purchase blocked by safety system: {purchase_decision['reason']}")
            
    async def _batch_stock_check(self, tcins: List[str]) -> Dict[str, StockResult]:
        """Batch stock check callback for scheduler"""
        if not self.stock_checker:
            return {}
            
        results = await self.stock_checker.check_multiple_products(tcins)
        
        # Update last results
        self.last_results.update(results)
        
        # Log summary
        available = [tcin for tcin, result in results.items() if result.available]
        if available:
            self.logger.info(f"📦 AVAILABLE NOW: {', '.join(available)}")
            
        return results
        
    async def start_monitoring(self):
        """Start the ultra-fast monitoring system"""
        self.logger.info("🎯 Starting Ultra-Fast Monitoring...")
        self.logger.info(f"Mode: {'TEST' if self.test_mode else 'PRODUCTION'}")
        self.logger.info(f"Products: {len(self.products)} enabled")
        
        if not self.products:
            self.logger.error("No products configured for monitoring!")
            return
            
        self.running = True
        
        # Start scheduler
        if self.scheduler:
            await self.scheduler.start()
            
        self.logger.info("✅ Ultra-Fast Monitor is running!")
        
        try:
            # Keep running until stopped
            while self.running:
                await asyncio.sleep(1)
                
                # Check for configuration updates
                if self.config_manager and await self.config_manager.check_for_updates():
                    self.logger.info("🔄 Configuration updated, reloading...")
                    await self._reload_configuration()
                    
        except KeyboardInterrupt:
            self.logger.info("🛑 Stopping monitor...")
        finally:
            await self.stop_monitoring()
            
    async def _reload_configuration(self):
        """Reload configuration without stopping"""
        await self._load_products()
        if self.scheduler:
            await self.scheduler.update_products(self.products)
            
    async def stop_monitoring(self):
        """Stop the monitoring system"""
        self.running = False
        
        if self.scheduler:
            await self.scheduler.stop()
            
        if self.stock_checker:
            await self.stock_checker.cleanup()
            
        self.logger.info("✅ Ultra-Fast Monitor stopped")
        
    async def get_status(self) -> Dict:
        """Get current monitoring status for dashboard"""
        status = {
            'running': self.running,
            'test_mode': self.test_mode,
            'products_count': len(self.products),
            'enabled_products': len([p for p in self.products if p.enabled]),
            'last_results': {
                tcin: {
                    'available': result.available,
                    'confidence': result.confidence,
                    'method': result.method,
                    'check_time': result.check_time
                }
                for tcin, result in self.last_results.items()
            }
        }
        
        # Performance stats
        if self.stock_checker:
            perf_report = await self.stock_checker.get_performance_report()
            status['performance'] = perf_report
            
        return status
        
    async def manual_check_all(self) -> Dict[str, StockResult]:
        """Manual check of all products (for dashboard)"""
        tcins = [p.tcin for p in self.products if p.enabled]
        if not tcins or not self.stock_checker:
            return {}
            
        self.logger.info(f"🔍 Manual check requested for {len(tcins)} products")
        results = await self.stock_checker.check_multiple_products(tcins)
        
        self.last_results.update(results)
        return results

async def main():
    """Test the ultra-fast monitor"""
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    monitor = UltraFastTargetMonitor(test_mode=True)
    await monitor.initialize()
    
    # Run for a short test
    await monitor.start_monitoring()

if __name__ == "__main__":
    asyncio.run(main())