#!/usr/bin/env python3
"""
Buy Bot - Automated purchase when items come in stock
"""

import asyncio
from playwright.async_api import async_playwright
from pathlib import Path
import logging
from datetime import datetime

class BuyBot:
    def __init__(self):
        # Path to saved session in existing folder
        self.storage_path = Path("../existing/target_storage.json")
        
        # Setup logging
        log_dir = Path("../logs")
        log_dir.mkdir(exist_ok=True)
        
        self.logger = logging.getLogger('buybot')
        self.logger.setLevel(logging.INFO)
        
        fh = logging.FileHandler(log_dir / 'purchases.log', mode='a')
        fh.setFormatter(logging.Formatter('%(asctime)s - BUYBOT - %(message)s'))
        self.logger.addHandler(fh)
    
    async def attempt_purchase(self, tcin: str, price: float) -> bool:
        """
        Try to buy a product
        Returns True if successful
        """
        self.logger.info(f"Starting purchase: TCIN {tcin} at ${price:.2f}")
        
        if not self.storage_path.exists():
            self.logger.error("No saved session found")
            return False
        
        try:
            async with async_playwright() as p:
                # Launch browser (headless for speed)
                browser = await p.chromium.launch(headless=True)
                
                # Load saved session
                context = await browser.new_context(storage_state=str(self.storage_path))
                page = await context.new_page()
                page.set_default_timeout(10000)
                
                # Go to product page
                url = f"https://www.target.com/p/-/A-{tcin}"
                await page.goto(url, wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)
                
                # Try shipping option
                try:
                    ship_btn = await page.wait_for_selector('button[data-test="fulfillment-cell-shipping"]', timeout=3000)
                    await ship_btn.click()
                    await page.wait_for_timeout(1000)
                except:
                    pass  # Already selected or not available
                
                # Add to cart
                try:
                    add_btn = await page.wait_for_selector('button[id^="addToCartButtonOrTextIdFor"]', timeout=5000)
                    await add_btn.click()
                    await page.wait_for_timeout(2000)
                    self.logger.info(f"Added {tcin} to cart")
                except:
                    self.logger.error(f"Could not add {tcin} to cart")
                    await browser.close()
                    return False
                
                # Go to cart
                await page.goto("https://www.target.com/cart")
                await page.wait_for_timeout(2000)
                
                # Click checkout
                try:
                    checkout_btn = await page.wait_for_selector('button[data-test="checkout-button"]', timeout=5000)
                    
                    # Only actually checkout if AUTO_CHECKOUT=true
                    import os
                    if os.environ.get('AUTO_CHECKOUT') == 'true':
                        await checkout_btn.click()
                        self.logger.warning(f"CHECKOUT CLICKED for {tcin}")
                        await page.wait_for_timeout(5000)
                    else:
                        self.logger.info(f"Item {tcin} in cart (auto-checkout disabled)")
                    
                    # Save screenshot
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    screenshot = f"../logs/cart_{tcin}_{timestamp}.png"
                    await page.screenshot(path=screenshot)
                    
                    await browser.close()
                    return True
                    
                except Exception as e:
                    self.logger.error(f"Checkout failed for {tcin}: {e}")
                    await browser.close()
                    return False
                    
        except Exception as e:
            self.logger.error(f"Buy bot error: {e}")
            return False

# Test function
async def test():
    """Test the buy bot"""
    import sys
    tcin = sys.argv[1] if len(sys.argv) > 1 else "92800127"
    
    print(f"Testing buy bot with TCIN: {tcin}")
    bot = BuyBot()
    success = await bot.attempt_purchase(tcin, 99.99)
    print(f"Result: {'Success' if success else 'Failed'}")

if __name__ == "__main__":
    asyncio.run(test())