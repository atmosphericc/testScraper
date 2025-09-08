#!/usr/bin/env python3
"""
Test script to verify stock status flow without running the full dashboard
"""

import sys
import os
import time
import threading
from datetime import datetime

# Add the project directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock the required globals and functions to test the logic
latest_stock_data = {
    '89542109': {
        'name': 'Test Product',
        'available': True,
        'status': 'In Stock'
    }
}

latest_data_lock = threading.Lock()
purchase_cooldowns = {}
purchase_lock = threading.Lock()
stock_status_override = {}
stock_override_lock = threading.Lock()

def init_purchase_status(tcin):
    """Initialize purchase status for a product"""
    with purchase_lock:
        if tcin not in purchase_cooldowns:
            purchase_cooldowns[tcin] = {
                'status': 'ready',
                'cooldown_until': None,
                'last_attempt': None,
                'attempt_count': 0
            }

def can_attempt_purchase(tcin):
    """Check if product can be purchased (not in cooldown)"""
    with purchase_lock:
        if tcin not in purchase_cooldowns:
            init_purchase_status(tcin)
        status_info = purchase_cooldowns[tcin]
        return status_info['status'] in ['ready']

def set_purchase_status(tcin, status, cooldown_minutes=None):
    """Set purchase status and optional cooldown"""
    with purchase_lock:
        if tcin not in purchase_cooldowns:
            init_purchase_status(tcin)
        
        purchase_cooldowns[tcin]['status'] = status
        purchase_cooldowns[tcin]['last_attempt'] = datetime.now()
        
        if cooldown_minutes and cooldown_minutes > 0:
            from datetime import timedelta
            purchase_cooldowns[tcin]['cooldown_until'] = datetime.now() + timedelta(minutes=cooldown_minutes)

def set_stock_waiting_for_response(tcin):
    """Set stock status to 'Waiting for Refresh' after purchase completion"""
    with stock_override_lock:
        stock_status_override[tcin] = 'Waiting for Refresh'
        print(f"Stock status set to 'Waiting for Refresh' for {tcin}")

def clear_all_stock_overrides():
    """Clear all stock status overrides on refresh"""
    with stock_override_lock:
        cleared_count = len(stock_status_override)
        if cleared_count > 0:
            print(f"Clearing {cleared_count} 'Waiting for Refresh' overrides - showing real API status")
            stock_status_override.clear()
        return cleared_count

def get_stock_override(tcin):
    """Get stock status override if exists"""
    with stock_override_lock:
        return stock_status_override.get(tcin)

def get_purchase_status(tcin):
    """Get purchase status for a product"""
    with purchase_lock:
        if tcin not in purchase_cooldowns:
            init_purchase_status(tcin)
        return purchase_cooldowns[tcin]['status']

def simulate_purchase_complete(tcin, success):
    """Simulate purchase completion"""
    if success:
        set_purchase_status(tcin, 'purchased', cooldown_minutes=10/60)
        set_stock_waiting_for_response(tcin)
        print(f"Mock purchase SUCCESS for {tcin} - Stock: Waiting for Refresh, Purchase: Success")
    else:
        set_purchase_status(tcin, 'failed', cooldown_minutes=10/60)
        set_stock_waiting_for_response(tcin)
        print(f"Mock purchase FAILED for {tcin} - Stock: Waiting for Refresh, Purchase: Failed")

def simulate_api_call():
    """Simulate the /api/live-stock-status API call"""
    print("\n=== SIMULATING API CALL ===")
    
    # Get current stock data first
    with latest_data_lock:
        stock_data = latest_stock_data.copy()
    
    print(f"Original stock data: {stock_data}")
    
    # Apply existing "Waiting for Refresh" overrides FIRST
    for tcin in stock_data.keys():
        override = get_stock_override(tcin)
        if override:
            stock_data[tcin] = stock_data[tcin].copy()
            stock_data[tcin]['status'] = override
            print(f"Applied stock override for {tcin}: {override}")
    
    print(f"Stock data with overrides: {stock_data}")
    
    # Clear all overrides for next refresh cycle AFTER applying them to response
    cleared_overrides_count = clear_all_stock_overrides()
    
    # Only trigger purchase attempts if NO overrides were cleared (meaning this is a fresh check, not post-purchase refresh)
    if cleared_overrides_count == 0:
        print("No overrides cleared - triggering purchase attempts for fresh in-stock products")
        # Check if we should trigger purchases (simulate the trigger logic)
        for tcin, product_data in stock_data.items():
            if product_data.get('available') and can_attempt_purchase(tcin):
                print(f"Triggering purchase for {tcin}")
                # Set purchase status to 'attempting' IMMEDIATELY
                set_purchase_status(tcin, 'attempting')
                print(f"Purchase status set to 'attempting' for {tcin}")
            else:
                print(f"Not triggering purchase for {tcin} - available: {product_data.get('available')}, can_attempt: {can_attempt_purchase(tcin)}")
    else:
        print(f"{cleared_overrides_count} override(s) cleared - skipping purchase triggers to avoid double purchases")
    
    return stock_data

def test_flow():
    """Test the complete stock status flow"""
    tcin = '89542109'
    
    print("=== TESTING STOCK STATUS FLOW ===")
    print(f"Testing with TCIN: {tcin}")
    
    # Initialize
    init_purchase_status(tcin)
    
    print(f"Initial purchase status: {get_purchase_status(tcin)}")
    print(f"Initial stock override: {get_stock_override(tcin)}")
    
    # Step 1: Simulate dashboard refresh (should trigger purchase)
    print("\n--- STEP 1: Dashboard refresh (should trigger purchase) ---")
    result = simulate_api_call()
    
    print(f"Purchase status after API call: {get_purchase_status(tcin)}")
    print(f"Stock override after API call: {get_stock_override(tcin)}")
    
    # Step 2: Simulate purchase completion (should set 'Waiting for Refresh')
    print("\n--- STEP 2: Purchase completes ---")
    simulate_purchase_complete(tcin, success=True)  # or False
    
    print(f"Purchase status after completion: {get_purchase_status(tcin)}")
    print(f"Stock override after completion: {get_stock_override(tcin)}")
    
    # Step 3: Simulate next dashboard refresh (should show 'Waiting for Refresh' then clear it)
    print("\n--- STEP 3: Next dashboard refresh ---")
    result = simulate_api_call()
    
    print(f"Final purchase status: {get_purchase_status(tcin)}")
    print(f"Final stock override: {get_stock_override(tcin)}")
    
    print("\n=== TEST COMPLETE ===")

if __name__ == "__main__":
    test_flow()