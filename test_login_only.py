#!/usr/bin/env python3
"""
Simple standalone login test - Just login to Target.com and verify it works
"""

import asyncio
import random
from playwright.async_api import async_playwright

# Your credentials
EMAIL = "elricomon@msn.com"
PASSWORD = "Cars123!"

async def dismiss_popups(page):
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
                print(f"  ✓ Dismissing popup: {selector}")
                await asyncio.sleep(random.uniform(0.3, 0.8))
                await element.click()
                dismissed += 1
                await asyncio.sleep(random.uniform(0.5, 1.0))
        except:
            continue

    if dismissed > 0:
        print(f"  ✓ Dismissed {dismissed} popup(s)")

async def test_login():
    print("="*60)
    print("STANDALONE LOGIN TEST")
    print("="*60)
    print(f"Email: {EMAIL}")
    print(f"Password: {'*' * len(PASSWORD)}")
    print("="*60)
    print()

    async with async_playwright() as p:
        print("1. Launching browser in guest mode (disables password/passkey manager)...")
        browser = await p.chromium.launch(
            headless=False,
            args=[
                '--guest',  # Guest mode disables password manager
                '--disable-blink-features=AutomationControlled'
            ]
        )

        print("2. Creating browser context with credential services disabled...")
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800}
        )

        print("3. Opening new page...")
        page = await context.new_page()

        # Use CDP to set up virtual WebAuthn authenticator to handle passkey requests silently
        print("4. Setting up virtual WebAuthn authenticator via CDP...")
        # Get CDP session
        client = await context.new_cdp_session(page)

        # Enable WebAuthn and add virtual authenticator
        await client.send('WebAuthn.enable')
        await client.send('WebAuthn.addVirtualAuthenticator', {
            'options': {
                'protocol': 'ctap2',
                'transport': 'internal',
                'hasResidentKey': True,
                'hasUserVerification': True,
                'isUserVerified': True,
                'automaticPresenceSimulation': True
            }
        })
        print("   ✓ Virtual authenticator enabled - passkey prompts will be handled automatically")

        print("5. Navigating to Target.com homepage...")
        await page.goto("https://www.target.com", wait_until='domcontentloaded')
        print(f"   Current URL: {page.url}")

        # Wait and dismiss any initial popups
        await asyncio.sleep(2)
        await dismiss_popups(page)

        print("\n6. Looking for Account menu...")
        account_selectors = [
            '[data-test="@web/AccountLink"]',
            'button[aria-label*="Account"]',
            'a[href*="/account"]',
            'text="Account"',
            '[data-test="accountNav"]'
        ]

        account_button = None
        for selector in account_selectors:
            try:
                account_button = await page.wait_for_selector(selector, timeout=3000)
                if account_button and await account_button.is_visible():
                    print(f"   ✓ Found account button: {selector}")
                    break
            except:
                print(f"   ✗ Not found: {selector}")
                continue

        if not account_button:
            print("\n❌ FAILED: Could not find Account menu")
            await browser.close()
            return False

        print("   ✓ Clicking Account menu...")
        await asyncio.sleep(random.uniform(0.5, 1.0))
        await account_button.click()
        await asyncio.sleep(2)

        print("\n7. Looking for 'Sign in' link...")
        signin_link_selectors = [
            'a:has-text("Sign in")',
            'button:has-text("Sign in")',
            '[data-test*="signIn"]',
            'text="Sign in"'
        ]

        signin_link = None
        for selector in signin_link_selectors:
            try:
                signin_link = await page.wait_for_selector(selector, timeout=3000)
                if signin_link and await signin_link.is_visible():
                    print(f"   ✓ Found sign in link: {selector}")
                    break
            except:
                print(f"   ✗ Not found: {selector}")
                continue

        if not signin_link:
            print("\n❌ FAILED: Could not find 'Sign in' link")
            await page.screenshot(path='logs/account_menu_screenshot.png')
            print("   📸 Screenshot saved: logs/account_menu_screenshot.png")
            await asyncio.sleep(10)
            await browser.close()
            return False

        print("   ✓ Clicking 'Sign in' link...")
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await signin_link.click()
        await asyncio.sleep(3)

        print(f"   Current URL: {page.url}")

        print("\n8. Looking for email field...")
        email_selectors = [
            'input[type="email"]',
            'input[name="username"]',
            'input[id*="username"]',
            'input[id*="email"]',
            '#username'
        ]

        email_field = None
        for selector in email_selectors:
            try:
                email_field = await page.wait_for_selector(selector, timeout=3000)
                if email_field:
                    print(f"   ✓ Found email field: {selector}")
                    break
            except:
                print(f"   ✗ Not found: {selector}")
                continue

        if not email_field:
            print("\n❌ FAILED: Could not find email field")
            await browser.close()
            return False

        print("\n9. Typing email (human-like)...")
        await asyncio.sleep(random.uniform(0.5, 1.0))
        await email_field.click()
        await asyncio.sleep(random.uniform(0.2, 0.4))

        for char in EMAIL:
            await email_field.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

        print(f"   ✓ Email entered: {EMAIL}")

        # Target.com uses two-step login - need to submit email first!
        print("\n10. Looking for 'Continue' or 'Next' button...")
        await asyncio.sleep(random.uniform(0.5, 1.0))

        continue_selectors = [
            'button:has-text("Continue")',
            'button:has-text("Next")',
            'button[type="submit"]',
            '#login'
        ]

        continue_button = None
        for selector in continue_selectors:
            try:
                continue_button = await page.wait_for_selector(selector, timeout=3000)
                if continue_button and await continue_button.is_visible():
                    print(f"   ✓ Found continue button: {selector}")
                    break
            except:
                print(f"   ✗ Not found: {selector}")
                continue

        if continue_button:
            print("   ✓ Clicking continue to proceed to password...")
            await asyncio.sleep(random.uniform(0.3, 0.7))
            await continue_button.click()
            print("   ✓ Waiting for modal to appear...")
            await asyncio.sleep(4)  # Longer wait for modal

            # Take screenshot immediately to see what's on screen
            await page.screenshot(path='logs/after_continue_click.png')
            print("   📸 Screenshot saved: logs/after_continue_click.png")

            # CRITICAL: Dismiss passkey popup/modal that appears after email submit
            print("\n   🔍 AGGRESSIVE MODAL DETECTION...")

            # Get ALL buttons on the page and check their text
            all_buttons = await page.query_selector_all('button')
            print(f"   Found {len(all_buttons)} buttons on page")

            modal_dismissed = False
            for idx, button in enumerate(all_buttons):
                try:
                    if not await button.is_visible():
                        continue

                    text = await button.inner_text()
                    text_lower = text.lower().strip()
                    aria_label = await button.get_attribute('aria-label') or ''

                    print(f"   Button {idx}: text='{text}' aria-label='{aria_label}'")

                    # Look for close-like text OR empty text with close aria-label OR just empty text (likely X button)
                    is_close_button = (
                        any(word in text_lower for word in ['close', 'cancel', 'skip', 'not now', 'dismiss', 'no thanks']) or
                        any(word in aria_label.lower() for word in ['close', 'dismiss']) or
                        (text.strip() == '' and aria_label.strip() == '')  # Empty button = likely X icon
                    )

                    if is_close_button:
                        print(f"\n   ✓✓✓ CLICKING BUTTON {idx}: text='{text}' aria='{aria_label}'")
                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        await button.click()
                        print("   ✓✓✓ Button clicked!")
                        await asyncio.sleep(3)  # Wait for modal to close
                        modal_dismissed = True
                        break
                except Exception as e:
                    print(f"   Error with button {idx}: {e}")
                    continue

            # If still not dismissed, try specific selectors
            if not modal_dismissed:
                print("\n   Trying specific selectors...")
                passkey_close_selectors = [
                    'button:has-text("Close")',
                    'button:has-text("close")',
                    'button:has-text("Cancel")',
                    'button[aria-label="Close"]',
                    '[role="button"]:has-text("Close")',
                    'div[role="dialog"] button',
                ]

                for selector in passkey_close_selectors:
                    try:
                        close_button = await page.wait_for_selector(selector, timeout=1000)
                        if close_button and await close_button.is_visible():
                            print(f"   ✓ Found close button: {selector}")
                            await asyncio.sleep(random.uniform(0.5, 1.0))
                            await close_button.click()
                            print("   ✓ Passkey modal dismissed!")
                            await asyncio.sleep(2)
                            modal_dismissed = True
                            break
                    except:
                        continue

            # Last resort: multiple Escape presses
            if not modal_dismissed:
                print("\n   Trying Escape keys (3x)...")
                for i in range(3):
                    await page.keyboard.press('Escape')
                    await asyncio.sleep(0.5)

            # Take another screenshot after modal handling
            await page.screenshot(path='logs/after_modal_handling.png')
            print("   📸 Screenshot saved: logs/after_modal_handling.png")

            print("   ✓ Modal handling complete")

        # CRITICAL: Check "Keep me signed in" checkbox BEFORE clicking "Enter your password"
        print("\n11. Looking for 'Keep me signed in' checkbox...")
        await asyncio.sleep(1)

        # Take screenshot to see the checkbox
        await page.screenshot(path='logs/before_checkbox.png')
        print("   📸 Screenshot saved: logs/before_checkbox.png")

        checkbox_found = False

        # Try recommended Playwright methods first (more reliable)
        try:
            # Method 1: By label text (most reliable)
            checkbox = page.get_by_label('Keep me signed in')
            if await checkbox.is_visible(timeout=2000):
                is_checked = await checkbox.is_checked()
                print(f"   ✓ Found checkbox by label (checked={is_checked})")
                if not is_checked:
                    print(f"   ✓ Checking 'Keep me signed in' checkbox...")
                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    await checkbox.check()
                    await asyncio.sleep(0.5)
                    print("   ✓ 'Keep me signed in' checked!")
                else:
                    print("   ✓ 'Keep me signed in' already checked")
                checkbox_found = True
        except Exception as e:
            print(f"   ✗ getByLabel failed: {e}")

        # Method 2: By role
        if not checkbox_found:
            try:
                checkbox = page.get_by_role('checkbox')
                if await checkbox.is_visible(timeout=2000):
                    is_checked = await checkbox.is_checked()
                    print(f"   ✓ Found checkbox by role (checked={is_checked})")
                    if not is_checked:
                        print(f"   ✓ Checking 'Keep me signed in' checkbox...")
                        await asyncio.sleep(random.uniform(0.3, 0.7))
                        await checkbox.check()
                        await asyncio.sleep(0.5)
                        print("   ✓ 'Keep me signed in' checked!")
                    else:
                        print("   ✓ 'Keep me signed in' already checked")
                    checkbox_found = True
            except Exception as e:
                print(f"   ✗ getByRole failed: {e}")

        # Method 3: Click on the label text which will toggle the checkbox
        if not checkbox_found:
            try:
                label = page.locator('text=Keep me signed in')
                if await label.is_visible(timeout=2000):
                    print(f"   ✓ Found 'Keep me signed in' label, clicking it...")
                    await asyncio.sleep(random.uniform(0.3, 0.7))
                    await label.click()
                    await asyncio.sleep(0.5)
                    print("   ✓ Clicked 'Keep me signed in' label!")
                    checkbox_found = True
            except Exception as e:
                print(f"   ✗ Label click failed: {e}")

        if not checkbox_found:
            print("   ⚠️  Could not find checkbox, but continuing...")

        # NOW click "Enter your password" button to reveal password field
        print("\n12. Looking for 'Enter your password' button...")
        await asyncio.sleep(2)

        enter_password_selectors = [
            'button:has-text("Enter your password")',
            ':text("Enter your password")',
            'div:has-text("Enter your password")',
            '[role="button"]:has-text("Enter your password")'
        ]

        password_option_clicked = False
        for selector in enter_password_selectors:
            try:
                enter_pwd_button = await page.wait_for_selector(selector, timeout=3000)
                if enter_pwd_button and await enter_pwd_button.is_visible():
                    print(f"   ✓ Found 'Enter your password' option: {selector}")
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                    await enter_pwd_button.click()
                    print("   ✓ Clicked 'Enter your password'!")
                    await asyncio.sleep(3)  # Wait for password field to appear
                    password_option_clicked = True
                    break
            except Exception as e:
                print(f"   ✗ Not found: {selector}")
                continue

        if not password_option_clicked:
            print("\n❌ FAILED: Could not find 'Enter your password' button")
            await page.screenshot(path='logs/no_enter_password_button.png')
            print("   📸 Screenshot saved: logs/no_enter_password_button.png")
            await asyncio.sleep(10)
            await browser.close()
            return False

        print("\n12. Looking for password field...")
        await asyncio.sleep(random.uniform(0.5, 1.0))

        password_selectors = [
            'input[type="password"]',
            'input[name="password"]',
            'input[id*="password"]',
            '#password'
        ]

        password_field = None
        for selector in password_selectors:
            try:
                password_field = await page.wait_for_selector(selector, timeout=5000)
                if password_field:
                    print(f"   ✓ Found password field: {selector}")
                    break
            except:
                print(f"   ✗ Not found: {selector}")
                continue

        if not password_field:
            print("\n❌ FAILED: Could not find password field")
            print("   Taking screenshot for debugging...")
            await page.screenshot(path='logs/password_field_missing.png')
            print("   📸 Screenshot saved: logs/password_field_missing.png")
            print("\n   Keeping browser open for 30 seconds so you can inspect...")
            await asyncio.sleep(30)
            await browser.close()
            return False

        print("\n13. Entering password (human-like to avoid F5 detection)...")
        await asyncio.sleep(random.uniform(1.0, 2.0))

        # Click password field naturally
        await password_field.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))

        # Type password slowly character-by-character to appear more human
        for i, char in enumerate(PASSWORD):
            await password_field.type(char, delay=random.uniform(80, 200))
            # Occasionally pause longer (like a human thinking)
            if i > 0 and i % 3 == 0:
                await asyncio.sleep(random.uniform(0.1, 0.3))

        await asyncio.sleep(random.uniform(0.5, 1.0))

        print(f"   ✓ Password entered: {'*' * len(PASSWORD)}")

        print("\n14. Looking for sign-in button...")
        await asyncio.sleep(random.uniform(0.8, 1.5))

        signin_selectors = [
            'button[type="submit"]',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            '#login',
            '[data-test*="signin"]'
        ]

        signin_button = None
        for selector in signin_selectors:
            try:
                signin_button = await page.wait_for_selector(selector, timeout=3000)
                if signin_button and await signin_button.is_visible():
                    is_disabled = await signin_button.get_attribute('disabled')
                    if not is_disabled:
                        print(f"   ✓ Found sign-in button: {selector}")
                        break
            except:
                print(f"   ✗ Not found: {selector}")
                continue

        if not signin_button:
            print("\n❌ FAILED: Could not find sign-in button")
            await browser.close()
            return False

        print("\n15. Clicking sign-in button...")
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await signin_button.click()
        print("    ✓ Sign-in clicked!")

        print("\n16. Waiting for response...")
        await asyncio.sleep(4)

        print("\n17. Checking for popups (passkey, save password, etc.)...")
        await dismiss_popups(page)
        await asyncio.sleep(2)
        await dismiss_popups(page)  # Second pass

        print("\n18. Checking login result...")
        current_url = page.url
        print(f"    Current URL: {current_url}")

        # Check for error messages
        error_selectors = [
            'text="incorrect"',
            'text="Invalid"',
            'text="error"',
            '[class*="error"]',
            '[role="alert"]'
        ]

        for selector in error_selectors:
            try:
                error_elem = await page.query_selector(selector)
                if error_elem:
                    error_text = await error_elem.inner_text()
                    if error_text and len(error_text) > 0:
                        print(f"\n❌ LOGIN ERROR: {error_text}")
                        await browser.close()
                        return False
            except:
                continue

        # Check if we're still on login page
        if 'login' in current_url.lower() or 'signin' in current_url.lower():
            print(f"\n⚠️  Still on login page: {current_url}")
            print("    This might mean:")
            print("    - Credentials incorrect")
            print("    - 2FA required")
            print("    - Security check")

            # Take screenshot for debugging
            await page.screenshot(path='logs/login_test_screenshot.png')
            print("    📸 Screenshot saved: logs/login_test_screenshot.png")

            print("\n    Keeping browser open for 30 seconds so you can inspect...")
            await asyncio.sleep(30)

            await browser.close()
            return False

        # Check for successful login indicators
        print("\n19. Verifying login success...")
        login_indicators = [
            '[data-test="@web/AccountLink"]',
            '[data-test="accountNav"]',
            'button[aria-label*="Account"]',
            'button[aria-label*="Hi,"]',
            'button:has-text("Hi,")',
        ]

        for selector in login_indicators:
            try:
                element = await page.wait_for_selector(selector, timeout=3000)
                if element and await element.is_visible():
                    print(f"    ✓ Found login indicator: {selector}")
                    print("\n" + "="*60)
                    print("🎉 LOGIN SUCCESSFUL!")
                    print("="*60)

                    # Save session
                    print("\n20. Saving session to target.json...")
                    storage_state = await context.storage_state()
                    import json
                    with open('target.json', 'w') as f:
                        json.dump(storage_state, f, indent=2)
                    print("    ✓ Session saved!")

                    print("\nKeeping browser open for 10 seconds so you can see...")
                    await asyncio.sleep(10)

                    await browser.close()
                    return True
            except:
                continue

        print("\n⚠️  Login state unclear - no definitive indicators found")
        print("    Keeping browser open for 30 seconds so you can inspect...")
        await asyncio.sleep(30)

        await browser.close()
        return False

if __name__ == "__main__":
    import os
    os.makedirs('logs', exist_ok=True)

    result = asyncio.run(test_login())

    if result:
        print("\n✅ TEST PASSED - Login successful!")
    else:
        print("\n❌ TEST FAILED - Login did not succeed")
