#!/usr/bin/env python3
"""
Target.com Login Automation
Handles login flow with passkey bypass and "Keep me signed in" checkbox
"""

import asyncio
import random
import os
from datetime import datetime
from playwright.async_api import async_playwright, Page


# Credentials
EMAIL = "elricomon@msn.com"
PASSWORD = "Cars123!"
STORAGE_PATH = "target.json"


async def dismiss_popups(page: Page):
    """Dismiss any popup overlays"""
    popup_selectors = [
        'button:has-text("Cancel")',
        'button:has-text("Skip")',
        'button:has-text("Skip for now")',
        'button:has-text("Not now")',
        'button:has-text("Maybe later")',
        'button:has-text("No thanks")',
        '[aria-label*="Close"]',
        '[aria-label*="Dismiss"]',
    ]

    dismissed = 0
    for selector in popup_selectors:
        try:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await element.click()
                dismissed += 1
                await asyncio.sleep(random.uniform(0.5, 1.0))
        except:
            continue

    return dismissed


async def check_if_logged_in(page: Page) -> bool:
    """Check if currently logged in to Target.com"""
    try:
        # Navigate to Target homepage
        await page.goto("https://www.target.com", wait_until='domcontentloaded', timeout=10000)
        await page.wait_for_timeout(2000)

        # Check for login indicators
        login_indicators = [
            '[data-test="@web/AccountLink"]',
            'button[aria-label*="Hi,"]',
            'button:has-text("Hi,")',
            '[data-test="accountNav"]'
        ]

        for indicator in login_indicators:
            try:
                element = await page.wait_for_selector(indicator, timeout=2000)
                if element:
                    return True
            except:
                continue

        return False
    except Exception as e:
        print(f"Error checking login status: {e}")
        return False


async def perform_target_login(page: Page) -> bool:
    """
    Perform full Target.com login flow
    Returns True if successful, False otherwise
    """
    try:
        print("=" * 60)
        print("TARGET.COM LOGIN")
        print("=" * 60)
        print(f"Email: {EMAIL}")
        print("=" * 60)
        print()

        # Navigate to Target homepage
        print("1. Navigating to Target.com...")
        await page.goto("https://www.target.com", wait_until='domcontentloaded')
        await asyncio.sleep(2)
        await dismiss_popups(page)

        # Find and click Account menu
        print("2. Opening Account menu...")
        account_selectors = [
            '[data-test="@web/AccountLink"]',
            'button[aria-label*="Account"]',
        ]

        account_button = None
        for selector in account_selectors:
            try:
                account_button = await page.wait_for_selector(selector, timeout=3000)
                if account_button and await account_button.is_visible():
                    break
            except:
                continue

        if not account_button:
            print("❌ Could not find Account menu")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await account_button.click()
        await asyncio.sleep(2)

        # Click Sign in
        print("3. Clicking 'Sign in'...")
        signin_selectors = [
            'button:has-text("Sign in")',
            'a:has-text("Sign in")',
        ]

        signin_link = None
        for selector in signin_selectors:
            try:
                signin_link = await page.wait_for_selector(selector, timeout=3000)
                if signin_link and await signin_link.is_visible():
                    break
            except:
                continue

        if not signin_link:
            print("❌ Could not find 'Sign in' link")
            return False

        await asyncio.sleep(random.uniform(0.3, 0.7))
        await signin_link.click()
        await asyncio.sleep(3)

        # Enter email
        print("4. Entering email...")
        email_field = await page.wait_for_selector('input[name="username"]', timeout=5000)
        if not email_field:
            print("❌ Could not find email field")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await email_field.click()
        await asyncio.sleep(random.uniform(0.2, 0.4))

        for char in EMAIL:
            await email_field.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

        print(f"   ✓ Email entered: {EMAIL}")

        # Click Continue
        print("5. Clicking Continue...")
        continue_button = await page.wait_for_selector('button:has-text("Continue")', timeout=3000)
        if not continue_button:
            print("❌ Could not find Continue button")
            return False

        await asyncio.sleep(random.uniform(0.3, 0.7))
        await continue_button.click()
        await asyncio.sleep(4)  # Wait for page transition

        # Check "Keep me signed in" checkbox (before clicking Enter Password)
        print("6. Checking 'Keep me signed in' checkbox...")
        await asyncio.sleep(1)

        checkbox_found = False
        try:
            checkbox = page.get_by_label('Keep me signed in')
            if await checkbox.is_visible(timeout=2000):
                is_checked = await checkbox.is_checked()
                if not is_checked:
                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    await checkbox.check()
                    await asyncio.sleep(0.5)
                    print("   ✓ 'Keep me signed in' checked!")
                checkbox_found = True
        except:
            pass

        if not checkbox_found:
            try:
                checkbox = page.get_by_role('checkbox')
                if await checkbox.is_visible(timeout=2000):
                    is_checked = await checkbox.is_checked()
                    if not is_checked:
                        await checkbox.check()
                        print("   ✓ 'Keep me signed in' checked!")
                    checkbox_found = True
            except:
                pass

        if not checkbox_found:
            try:
                label = page.locator('text=Keep me signed in')
                if await label.is_visible(timeout=2000):
                    await label.click()
                    print("   ✓ Clicked 'Keep me signed in' label!")
            except:
                print("   ⚠️  Could not find checkbox")

        # Click "Enter your password"
        print("7. Clicking 'Enter your password'...")
        enter_pwd_selectors = [
            'button:has-text("Enter your password")',
            ':text("Enter your password")',
        ]

        password_option = None
        for selector in enter_pwd_selectors:
            try:
                password_option = await page.wait_for_selector(selector, timeout=3000)
                if password_option and await password_option.is_visible():
                    break
            except:
                continue

        if not password_option:
            print("❌ Could not find 'Enter your password' button")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await password_option.click()
        await asyncio.sleep(3)

        # Enter password
        print("8. Entering password...")
        password_field = await page.wait_for_selector('input[type="password"]', timeout=5000)
        if not password_field:
            print("❌ Could not find password field")
            return False

        await asyncio.sleep(random.uniform(1.0, 2.0))
        await password_field.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))

        # Type password slowly for F5 security
        for i, char in enumerate(PASSWORD):
            await password_field.type(char, delay=random.uniform(80, 200))
            if i > 0 and i % 3 == 0:
                await asyncio.sleep(random.uniform(0.1, 0.3))

        await asyncio.sleep(random.uniform(0.5, 1.0))
        print("   ✓ Password entered")

        # Click Sign in button
        print("9. Clicking 'Sign in' button...")
        await asyncio.sleep(random.uniform(0.8, 1.5))

        signin_selectors = [
            'button[type="submit"]',
            'button:has-text("Sign in")',
        ]

        signin_button = None
        for selector in signin_selectors:
            try:
                signin_button = await page.wait_for_selector(selector, timeout=3000)
                if signin_button and await signin_button.is_visible():
                    is_disabled = await signin_button.get_attribute('disabled')
                    if not is_disabled:
                        break
            except:
                continue

        if not signin_button:
            print("❌ Could not find sign-in button")
            return False

        await asyncio.sleep(random.uniform(0.3, 0.7))
        await signin_button.click()
        print("   ✓ Sign-in clicked!")

        # Wait for login to complete
        print("10. Waiting for login response...")
        await asyncio.sleep(4)
        await dismiss_popups(page)
        await asyncio.sleep(2)
        await dismiss_popups(page)

        # Verify login success
        print("11. Verifying login success...")
        login_indicators = [
            '[data-test="@web/AccountLink"]',
            '[data-test="accountNav"]',
            'button[aria-label*="Account"]',
            'button[aria-label*="Hi,"]',
        ]

        for selector in login_indicators:
            try:
                element = await page.wait_for_selector(selector, timeout=3000)
                if element and await element.is_visible():
                    print("\n" + "=" * 60)
                    print("🎉 LOGIN SUCCESSFUL!")
                    print("=" * 60)
                    return True
            except:
                continue

        print("❌ Login verification failed")
        return False

    except Exception as e:
        print(f"❌ Login failed with error: {e}")
        return False


async def ensure_logged_in_with_session_save(context, existing_page=None) -> bool:
    """
    Ensure user is logged in and save session
    Works with an existing browser context
    If existing_page provided, uses it instead of creating new page (avoids tab flash)
    Returns True if logged in (or successfully logged in), False otherwise
    """
    import json

    try:
        # Use existing page if provided, otherwise create new one
        if existing_page:
            page = existing_page
            should_close_page = False  # Don't close page we didn't create
            print("[LOGIN] ✓ Using existing page for login check (NO NEW TAB)")
        else:
            page = await context.new_page()
            should_close_page = True  # Close page we created
            print("[LOGIN] ⚠️  CREATED NEW PAGE FOR LOGIN CHECK - THIS WILL FLASH!")

        # Check if already logged in
        print("[LOGIN] Checking login status...")
        if await check_if_logged_in(page):
            print("[LOGIN] ✅ Already logged in!")

            # Only close if we created the page
            if should_close_page:
                await page.close()

            # Save current session
            print("[LOGIN] Saving session state...")
            storage_state = await context.storage_state()
            with open(STORAGE_PATH, 'w') as f:
                json.dump(storage_state, f, indent=2)
            print(f"[LOGIN] ✓ Session saved to {STORAGE_PATH}")

            return True

        # Not logged in - perform login
        print("[LOGIN] Not logged in. Starting login flow...")
        login_success = await perform_target_login(page)

        if login_success:
            # Save session after successful login
            print("[LOGIN] Saving session state...")
            storage_state = await context.storage_state()
            with open(STORAGE_PATH, 'w') as f:
                json.dump(storage_state, f, indent=2)
            print(f"[LOGIN] ✓ Session saved to {STORAGE_PATH}")

        # Only close if we created the page
        if should_close_page:
            await page.close()

        return login_success

    except Exception as e:
        print(f"[LOGIN] ❌ Error during login check: {e}")
        return False
