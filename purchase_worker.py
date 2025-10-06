#!/usr/bin/env python3
"""
Purchase Worker - Standalone subprocess for executing purchases
Bypasses asyncio threading issues by running in isolated process
"""

import sys
import json
import time
import random
import logging
from pathlib import Path
from datetime import datetime

# Use sync API to avoid asyncio issues entirely
from playwright.sync_api import sync_playwright

def main():
    """Main entry point for purchase worker subprocess"""
    if len(sys.argv) != 3:
        print("Usage: purchase_worker.py <tcin> <session_file>")
        sys.exit(1)

    tcin = sys.argv[1]
    session_file = sys.argv[2]

    # Configure logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    start_time = time.time()

    try:
        print(f"[PURCHASE_WORKER] Starting purchase for TCIN {tcin}")

        # Check if we're in test mode
        test_mode = True  # Always test mode for safety

        result = execute_purchase(tcin, session_file, test_mode, logger)

        # Output result as JSON for parent process
        print("PURCHASE_RESULT:", json.dumps(result))

        if result['success']:
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        error_result = {
            'success': False,
            'tcin': tcin,
            'reason': 'worker_error',
            'error': str(e),
            'execution_time': time.time() - start_time
        }
        print("PURCHASE_RESULT:", json.dumps(error_result))
        sys.exit(1)

def execute_purchase(tcin: str, session_file: str, test_mode: bool, logger):
    """Execute purchase using Playwright sync API"""
    start_time = time.time()

    try:
        with sync_playwright() as p:
            # Launch browser
            browser = p.chromium.launch(headless=False)

            # Load session if it exists
            context_options = {}
            session_path = Path(session_file)
            if session_path.exists():
                context_options['storage_state'] = str(session_path)
                logger.info(f"Loading session from {session_file}")

            context = browser.new_context(**context_options)
            page = context.new_page()

            # Navigate to product page
            product_url = f"https://www.target.com/p/-/A-{tcin}"
            logger.info(f"Navigating to: {product_url}")

            page.goto(product_url, wait_until='domcontentloaded', timeout=15000)

            # Wait for page to stabilize
            time.sleep(2)

            # Check if we need to log in
            if not validate_login_status(page, logger):
                logger.warning("Not logged in - would need credentials")
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'not_logged_in',
                    'execution_time': time.time() - start_time
                }

            # Look for add to cart button
            add_to_cart_selectors = [
                'button[data-test="addToCartButton"]',
                'button[data-test="chooseOptionsButton"]',
                'button:has-text("Add to cart")',
                'button:has-text("Preorder")',
                'button:has-text("Pre-order")'
            ]

            add_button = None
            for selector in add_to_cart_selectors:
                try:
                    add_button = page.wait_for_selector(selector, timeout=3000)
                    if add_button and add_button.is_visible():
                        logger.info(f"Found add to cart button: {selector}")
                        break
                except:
                    continue

            if not add_button:
                return {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'add_to_cart_button_not_found',
                    'execution_time': time.time() - start_time
                }

            # In test mode, stop before clicking
            if test_mode:
                logger.info("TEST MODE: Would click add to cart button, but stopping here")

                # Save session
                context.storage_state(path=session_file)

                browser.close()

                return {
                    'success': True,
                    'tcin': tcin,
                    'reason': 'test_mode_success',
                    'message': 'Found add to cart button, would proceed with purchase',
                    'execution_time': time.time() - start_time
                }

            # Real mode - actually click (not implemented for safety)
            logger.warning("Real purchase mode not implemented for safety")
            browser.close()

            return {
                'success': False,
                'tcin': tcin,
                'reason': 'real_mode_not_implemented',
                'execution_time': time.time() - start_time
            }

    except Exception as e:
        logger.error(f"Purchase execution failed: {e}")
        return {
            'success': False,
            'tcin': tcin,
            'reason': 'execution_error',
            'error': str(e),
            'execution_time': time.time() - start_time
        }

def validate_login_status(page, logger):
    """Check if user is logged in"""
    login_indicators = [
        '[data-test="@web/AccountLink"]',
        '[data-test="accountNav"]',
        'button[aria-label*="Account"]',
        'button[aria-label*="Hi,"]'
    ]

    for indicator in login_indicators:
        try:
            element = page.wait_for_selector(indicator, timeout=2000)
            if element and element.is_visible():
                logger.info(f"Login validated via: {indicator}")
                return True
        except:
            continue

    logger.warning("No login indicators found")
    return False

if __name__ == "__main__":
    main()