#!/usr/bin/env python3
"""
Buy Bot - Handles automated purchases when items come in stock
"""

import asyncio
from playwright.async_api import async_playwright
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

class BuyBot:
    def __init__(self, storage_path="../existing/target_storage.json", headless=True):
        """Initialize the buy bot"""
        self.storage_path = Path(storage_path)
        self.headless = headless
        
        # Setup logging
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        
        self.logger = logging.getLogger('buy_bot')
        self.logger.setLevel(logging.INFO)
        
        fh = logging.FileHandler(log_dir / 'purchases.log', mode='a')
        fh.setFormatter(logging.Formatter(
            '%(asctime)s - BUY_BOT - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        self.logger.addHandler(fh)
    
    async def attempt_purchase(self, tcin: str, price: float) -> bool:
        """
        Attempt to purchase a product
        Returns True if successful, False otherwise
        """
        start_time = datetime.now()
        self.logger.info(f"Starting purchase attempt for TCIN {tcin} at ${price:.2f}")
        
        if not self.storage_path.exists():
            self.logger.error(f"No saved session found at {self.storage_path}")
            return False
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=['--disable-blink-features=AutomationControlled']
                )
                
                # Load saved session
                context = await browser.new_context(storage_state=str(self.storage_path))
                page = await context.new_page()
                
                # Set reasonable timeout
                page.set_default_timeout(10000)
                
                # Navigate to product
                product_url = f"https://www.target.com/p/-/A-{tcin}"
                self.logger.info(f"Navigating to {product_url}")
                
                await page.goto(product_url, wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)
                
                # Check if logged in
                try:
                    await page.wait_for_selector('[data-test="@web/AccountLink"]', timeout=2000)
                    self.logger.info("User is logged in")
                except:
                    self.logger.warning("User may not be logged in")
                
                # Try to select shipping option
                try:
                    shipping_button = await page.wait_for_selector(
                        'button[data-test="fulfillment-cell-shipping"]', 
                        timeout=3000
                    )
                    await shipping_button.click()
                    await page.wait_for_timeout(1000)
                    self.logger.info("Selected shipping option")
                except:
                    self.logger.info("Shipping option not found or already selected")
                
                # Add to cart
                try:
                    add_button = await page.wait_for_selector(
                        'button[id^="addToCartButtonOrTextIdFor"]',
                        timeout=5000
                    )
                    await add_button.scroll_into_view_if_needed()
                    await add_button.click()
                    self.logger.info("Clicked add to cart")
                    
                    await page.wait_for_timeout(2000)
                    
                except Exception as e:
                    self.logger.error(f"Could not add to cart: {e}")
                    await browser.close()
                    return False
                
                # Go to cart
                await page.goto("https://www.target.com/cart")
                await page.wait_for_timeout(2000)
                self.logger.info("Navigated to cart")
                
                # Try to checkout
                try:
                    checkout_button = await page.wait_for_selector(
                        'button[data-test="checkout-button"]',
                        timeout=5000
                    )
                    
                    # If AUTO_CHECKOUT environment variable is set, actually click
                    if os.environ.get('AUTO_CHECKOUT') == 'true':
                        await checkout_button.click()
                        self.logger.warning(f"CHECKOUT CLICKED for {tcin} at ${price:.2f}")
                        
                        # Would continue with checkout process here
                        await page.wait_for_timeout(5000)
                        
                        # Save screenshot
                        screenshot_path = f"logs/checkout_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        await page.screenshot(path=screenshot_path)
                        self.logger.info(f"Screenshot saved: {screenshot_path}")
                    else:
                        self.logger.info(f"Item in cart but AUTO_CHECKOUT not enabled")
                        
                        # Save screenshot of cart
                        screenshot_path = f"logs/cart_{tcin}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                        await page.screenshot(path=screenshot_path)
                    
                    await browser.close()
                    
                    elapsed = (datetime.now() - start_time).total_seconds()
                    self.logger.info(f"Purchase process completed in {elapsed:.2f}s for {tcin}")
                    return True
                    
                except Exception as e:
                    self.logger.error(f"Checkout failed: {e}")
                    await browser.close()
                    return False
                    
        except Exception as e:
            self.logger.error(f"Buy bot error for {tcin}: {e}", exc_info=True)
            return False
    
    async def test_purchase(self, tcin: str):
        """Test purchase flow without actually buying"""
        print(f"Testing purchase flow for TCIN: {tcin}")
        success = await self.attempt_purchase(tcin, 0.00)
        
        if success:
            print("✓ Test completed successfully - item added to cart")
        else:
            print("✗ Test failed - check logs/purchases.log for details")
        
        return success


async def main():
    """Standalone test"""
    if len(sys.argv) > 1:
        tcin = sys.argv[1]
    else:
        tcin = "92800127"  # Default test product
    
    print(f"Testing buy bot with TCIN: {tcin}")
    print("Note: Set AUTO_CHECKOUT=true environment variable to actually checkout")
    
    bot = BuyBot(headless=False)  # Show browser for testing
    await bot.test_purchase(tcin)


if __name__ == "__main__":
    asyncio.run(main())