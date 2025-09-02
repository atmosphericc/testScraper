#!/usr/bin/env python3
"""
Simple Smart Scheduler for Ultra-Fast System
"""
import asyncio
import time
import logging
from typing import Dict, List, Any, Callable

class UltraFastSmartScheduler:
    """Simple scheduling system for products"""
    
    def __init__(self, products: List[Any], stock_check_callback: Callable):
        self.products = products
        self.stock_check_callback = stock_check_callback
        self.running = False
        self.logger = logging.getLogger(__name__)
        
    async def start(self):
        """Start the scheduler"""
        self.running = True
        self.logger.info("Smart scheduler started")
        
        # Just run a simple loop checking all products
        while self.running:
            try:
                tcins = [p.tcin for p in self.products if p.enabled]
                if tcins:
                    await self.stock_check_callback(tcins)
                await asyncio.sleep(10)  # Check every 10 seconds
            except Exception as e:
                self.logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(5)
                
    async def stop(self):
        """Stop the scheduler"""
        self.running = False
        
    async def update_products(self, products: List[Any]):
        """Update product list"""
        self.products = products