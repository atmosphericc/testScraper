import json
import os
from pathlib import Path
from datetime import datetime, timedelta
import asyncio
from playwright.async_api import async_playwright
import logging

class SessionManager:
    def __init__(self, storage_path="sessions/target_storage.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(exist_ok=True)
        self.last_validation = None
        self.is_valid = False
        self.logger = logging.getLogger(__name__)
    
    async def create_session(self):
        """Interactive session creation - run this manually"""
        print("="*60)
        print("TARGET SESSION SETUP")
        print("="*60)
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            
            if self.storage_path.exists():
                print("Loading existing session...")
                context = await browser.new_context(storage_state=str(self.storage_path))
            else:
                print("Creating new session...")
                context = await browser.new_context()
            
            page = await context.new_page()
            await page.goto("https://www.target.com")
            await page.wait_for_timeout(3000)
            
            # Check if logged in
            try:
                await page.wait_for_selector('[data-test="@web/AccountLink"]', timeout=2000)
                print("✓ Already logged in!")
            except:
                print("\n⚠️  Not logged in. Please:")
                print("1. Click 'Sign in' on Target.com")
                print("2. Enter your credentials")
                print("3. Complete any 2FA if required")
                print("4. Make sure 'Keep me signed in' is checked")
                input("\nPress ENTER after you're logged in...")
            
            # Verify payment method
            print("\nVerifying account has payment methods...")
            await page.goto("https://www.target.com/account/payment-methods")
            await page.wait_for_timeout(2000)
            
            try:
                await page.wait_for_selector('[data-test="PaymentCardItem"]', timeout=3000)
                print("✓ Payment method found!")
            except:
                print("⚠️  No payment methods found. Please add a credit card.")
                input("Press ENTER after adding payment method...")
            
            # Save session
            await context.storage_state(path=str(self.storage_path))
            print(f"\n✓ Session saved to {self.storage_path}")
            
            await browser.close()
            
            # Validate
            if await self.validate_session():
                print("✓ Session is valid and ready!")
                return True
            else:
                print("✗ Session validation failed. Please try again.")
                return False
    
    async def validate_session(self, force=False):
        """Check if session is still valid"""
        # Only check every 30 minutes unless forced
        if not force and self.last_validation:
            if datetime.now() - self.last_validation < timedelta(minutes=30):
                return self.is_valid
        
        if not self.storage_path.exists():
            self.logger.error("No session file found!")
            return False
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(storage_state=str(self.storage_path))
                page = await context.new_page()
                
                # Quick check - go to account page
                await page.goto("https://www.target.com/account", wait_until='domcontentloaded')
                await page.wait_for_timeout(2000)
                
                # Check if redirected to login
                if "signin" in page.url.lower():
                    self.logger.error("Session expired - redirected to login")
                    self.is_valid = False
                else:
                    try:
                        # Look for account element
                        await page.wait_for_selector('[data-test="@web/AccountLink"]', timeout=3000)
                        self.is_valid = True
                        self.logger.info("Session validated successfully")
                    except:
                        self.is_valid = False
                        self.logger.error("Session invalid - could not find account element")
                
                await browser.close()
                self.last_validation = datetime.now()
                return self.is_valid
                
        except Exception as e:
            self.logger.error(f"Session validation error: {e}")
            self.is_valid = False
            return False