#!/usr/bin/env python3
"""
Target.com Login Automation using nodriver
Handles login flow with passkey bypass and "Keep me signed in" checkbox
"""

import asyncio
import random
import os
from datetime import datetime
import zendriver as uc


# Credentials
EMAIL = "elricomon@msn.com"
PASSWORD = "Cars123!"
STORAGE_PATH = "target.json"


async def dismiss_popups(tab):
    """Dismiss any popup overlays"""
    popup_texts = ["Cancel", "Skip", "Skip for now", "Not now", "Maybe later", "No thanks"]
    popup_selectors = ['[aria-label*="Close"]', '[aria-label*="Dismiss"]']

    dismissed = 0
    for text in popup_texts:
        try:
            element = await tab.find(text, best_match=True, timeout=0.5)
            if element:
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await element.click()
                dismissed += 1
                await asyncio.sleep(random.uniform(0.5, 1.0))
        except Exception:
            continue

    for selector in popup_selectors:
        try:
            element = await tab.select(selector, timeout=0.5)
            if element:
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await element.click()
                dismissed += 1
                await asyncio.sleep(random.uniform(0.5, 1.0))
        except Exception:
            continue

    return dismissed


async def check_if_logged_in(tab) -> bool:
    """Check if currently logged in to Target.com"""
    try:
        await tab.get("https://www.target.com")
        await asyncio.sleep(2)

        login_indicators = [
            '[data-test="@web/AccountLink"]',
            'button[aria-label*="Hi,"]',
            '[data-test="accountNav"]',
        ]

        for indicator in login_indicators:
            try:
                element = await tab.select(indicator, timeout=2)
                if element:
                    return True
            except Exception:
                continue

        return False
    except Exception as e:
        print(f"Error checking login status: {e}")
        return False


async def perform_target_login(tab) -> bool:
    """
    Perform full Target.com login flow using nodriver tab.
    Returns True if successful, False otherwise.
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
        await tab.get("https://www.target.com")
        await asyncio.sleep(2)
        await dismiss_popups(tab)

        # Find and click Account menu
        print("2. Opening Account menu...")
        account_selectors = [
            '[data-test="@web/AccountLink"]',
            'button[aria-label*="Account"]',
        ]

        account_button = None
        for selector in account_selectors:
            try:
                element = await tab.select(selector, timeout=3)
                if element:
                    account_button = element
                    break
            except Exception:
                continue

        if not account_button:
            print("[ERROR] Could not find Account menu")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await account_button.click()
        await asyncio.sleep(2)

        # Click Sign in
        print("3. Clicking 'Sign in'...")
        signin_link = None
        for text in ["Sign in"]:
            try:
                signin_link = await tab.find(text, best_match=True, timeout=3)
                if signin_link:
                    break
            except Exception:
                continue

        if not signin_link:
            print("[ERROR] Could not find 'Sign in' link")
            return False

        await asyncio.sleep(random.uniform(0.3, 0.7))
        await signin_link.click()
        await asyncio.sleep(3)

        # Enter email
        print("4. Entering email...")
        email_field = await tab.select('input[name="username"]', timeout=5)
        if not email_field:
            print("[ERROR] Could not find email field")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await email_field.click()
        await asyncio.sleep(random.uniform(0.2, 0.4))

        for char in EMAIL:
            await email_field.send_keys(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

        print(f"   [OK] Email entered: {EMAIL}")

        # Click Continue
        print("5. Clicking Continue...")
        continue_button = await tab.find("Continue", best_match=True, timeout=3)
        if not continue_button:
            print("[ERROR] Could not find Continue button")
            return False

        await asyncio.sleep(random.uniform(0.3, 0.7))
        await continue_button.click()
        await asyncio.sleep(4)

        # Check "Keep me signed in" checkbox
        print("6. Checking 'Keep me signed in' checkbox...")
        await asyncio.sleep(1)

        checkbox_found = False

        # Try finding by label text
        try:
            label = await tab.find("Keep me signed in", best_match=True, timeout=2)
            if label:
                # Find associated checkbox - check if label has 'for' attr
                for_id = None
                try:
                    for_id = await tab.evaluate("el => el.getAttribute('for')", label)
                except Exception:
                    pass

                checkbox = None
                if for_id:
                    try:
                        checkbox = await tab.select(f"#{for_id}", timeout=1)
                    except Exception:
                        pass

                if not checkbox:
                    try:
                        checkbox = await tab.select("input[type='checkbox']", timeout=1)
                    except Exception:
                        pass

                if checkbox:
                    is_checked = await tab.evaluate("el => el.checked", checkbox)
                    if not is_checked:
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                        await checkbox.click()
                        await asyncio.sleep(0.5)
                        print("   [OK] 'Keep me signed in' checked!")

                    await tab.evaluate("""
                        () => {
                            try {
                                localStorage.setItem('keepMeSignedIn', 'true');
                                localStorage.setItem('rememberMe', 'true');
                                localStorage.setItem('persistentLogin', 'true');
                            } catch (e) {}
                        }
                    """)
                    print("   [OK] Saved 'Keep me signed in' preference to localStorage")
                    checkbox_found = True
        except Exception:
            pass

        if not checkbox_found:
            try:
                checkbox = await tab.select("input[type='checkbox']", timeout=2)
                if checkbox:
                    is_checked = await tab.evaluate("el => el.checked", checkbox)
                    if not is_checked:
                        await checkbox.click()
                        print("   [OK] 'Keep me signed in' checked!")
                    await tab.evaluate("""
                        () => {
                            try {
                                localStorage.setItem('keepMeSignedIn', 'true');
                                localStorage.setItem('rememberMe', 'true');
                                localStorage.setItem('persistentLogin', 'true');
                            } catch (e) {}
                        }
                    """)
                    print("   [OK] Saved 'Keep me signed in' preference to localStorage")
                    checkbox_found = True
            except Exception:
                pass

        if not checkbox_found:
            print("   [WARNING] Could not find checkbox")

        # Click "Enter your password"
        print("7. Clicking 'Enter your password'...")
        password_option = None
        for text in ["Enter your password"]:
            try:
                password_option = await tab.find(text, best_match=True, timeout=3)
                if password_option:
                    break
            except Exception:
                continue

        if not password_option:
            print("[ERROR] Could not find 'Enter your password' button")
            return False

        await asyncio.sleep(random.uniform(0.5, 1.0))
        await password_option.click()
        await asyncio.sleep(3)

        # Enter password
        print("8. Entering password...")
        password_field = await tab.select('input[type="password"]', timeout=5)
        if not password_field:
            print("[ERROR] Could not find password field")
            return False

        await asyncio.sleep(random.uniform(1.0, 2.0))
        await password_field.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))

        for i, char in enumerate(PASSWORD):
            await password_field.send_keys(char)
            await asyncio.sleep(random.uniform(0.08, 0.2))
            if i > 0 and i % 3 == 0:
                await asyncio.sleep(random.uniform(0.1, 0.3))

        await asyncio.sleep(random.uniform(0.5, 1.0))
        print("   [OK] Password entered")

        # Click Sign in button
        print("9. Clicking 'Sign in' button...")
        await asyncio.sleep(random.uniform(0.8, 1.5))

        signin_button = None
        for selector in ['button[type="submit"]']:
            try:
                btn = await tab.select(selector, timeout=3)
                if btn:
                    disabled = await tab.evaluate("el => el.disabled", btn)
                    if not disabled:
                        signin_button = btn
                        break
            except Exception:
                continue

        if not signin_button:
            try:
                signin_button = await tab.find("Sign in", best_match=True, timeout=3)
            except Exception:
                pass

        if not signin_button:
            print("[ERROR] Could not find sign-in button")
            return False

        await asyncio.sleep(random.uniform(0.3, 0.7))
        await signin_button.click()
        print("   [OK] Sign-in clicked!")

        # Wait for login to complete
        print("10. Waiting for login response...")
        await asyncio.sleep(4)
        await dismiss_popups(tab)
        await asyncio.sleep(2)
        await dismiss_popups(tab)

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
                element = await tab.select(selector, timeout=3)
                if element:
                    print("\n" + "=" * 60)
                    print("[SUCCESS] LOGIN SUCCESSFUL!")
                    print("=" * 60)
                    return True
            except Exception:
                continue

        # Also try text-based check
        try:
            hi_elem = await tab.find("Hi,", best_match=True, timeout=2)
            if hi_elem:
                print("\n" + "=" * 60)
                print("[SUCCESS] LOGIN SUCCESSFUL!")
                print("=" * 60)
                return True
        except Exception:
            pass

        print("[ERROR] Login verification failed")
        return False

    except Exception as e:
        print(f"[ERROR] Login failed with error: {e}")
        return False
