#!/usr/bin/env python3
"""
Test a simple hypothesis: Maybe ALL preorders with purchase_limit > 0 are actually available
And the issue is that I'm overcomplicating the logic
"""

import json
from datetime import datetime, date

def test_simple_hypothesis():
    """Test if simple purchase_limit > 0 is actually correct for all preorders"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'NOT AVAILABLE (user confirmed)'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'AVAILABLE (user confirmed)'),  
        ('tcin_response_94827553.json', '94827553', 'AVAILABLE (user confirmed)'),
        ('test_response_94734932.json', '94734932', 'AVAILABLE (user confirmed)')
    ]
    
    print("🧪 TESTING SIMPLE HYPOTHESIS")
    print("Maybe all preorders with purchase_limit > 0 are available?")
    print("="*60)
    
    for filename, tcin, user_status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            item = data['data']['product']['item']
            product = data['data']['product']
            
            # Basic preorder detection
            has_eligibility = 'eligibility_rules' in item
            street_date = item.get('mmbv_content', {}).get('street_date')
            is_preorder = not has_eligibility and street_date
            
            if is_preorder:
                purchase_limit = item.get('fulfillment', {}).get('purchase_limit', 0)
                simple_logic_says = purchase_limit > 0
                
                print(f"\\n{tcin} ({user_status}):")
                print(f"  Purchase Limit: {purchase_limit}")
                print(f"  Simple Logic Says: {'AVAILABLE' if simple_logic_says else 'NOT AVAILABLE'}")
                print(f"  Street Date: {street_date}")
                
                # Check if simple logic matches user confirmation
                user_says_available = 'AVAILABLE' in user_status and 'NOT AVAILABLE' not in user_status
                
                if simple_logic_says == user_says_available:
                    print(f"  ✅ MATCHES user confirmation")
                else:
                    print(f"  ❌ CONTRADICTS user confirmation")
                    print(f"     User: {user_says_available}, Simple Logic: {simple_logic_says}")
        
        except Exception as e:
            print(f"Error with {filename}: {e}")

def check_if_issue_is_timing():
    """Check if the issue might be that availability changes over time"""
    print(f"\\n⏰ TIMING ANALYSIS:")
    print("="*50)
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'NOT AVAILABLE', '2025-09-26'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'AVAILABLE', '2025-09-05'),  
        ('tcin_response_94827553.json', '94827553', 'AVAILABLE', '2025-10-01'),
        ('test_response_94734932.json', '94734932', 'AVAILABLE', '2025-09-05')
    ]
    
    today = date.today()
    
    print(f"Today: {today}")
    
    for filename, tcin, status, street_date in files:
        try:
            release_date = datetime.strptime(street_date, '%Y-%m-%d').date()
            days_until = (release_date - today).days
            
            print(f"\\n{tcin} ({status}):")
            print(f"  Releases: {street_date} ({days_until} days from now)")
            
        except Exception as e:
            print(f"  Error: {e}")
    
    print(f"\\n🤔 OBSERVATIONS:")
    print("- 94681776 (NOT AVAILABLE): 22 days out")
    print("- 94723520 (AVAILABLE): 1 day out") 
    print("- 94827553 (AVAILABLE): 27 days out")
    print("- 94734932 (AVAILABLE): 1 day out")
    print()
    print("No clear time-based pattern. 94827553 is 27 days out but available.")

def radical_hypothesis_test():
    """Test radical hypothesis: Maybe the API doesn't actually show availability"""
    print(f"\\n🚀 RADICAL HYPOTHESIS TEST:")
    print("="*50)
    print("Maybe the API response doesn't actually contain availability info")
    print("and we need to determine availability through a different method:")
    print()
    print("POSSIBILITIES:")
    print("1. All preorders with purchase_limit > 0 are technically 'available'")
    print("2. The distinction you're making is based on something not in the API")
    print("3. Availability changes frequently and API is stale")
    print("4. Different regions/accounts see different availability")
    print("5. The API endpoint we're using doesn't have real-time availability")
    print()
    print("QUESTIONS:")
    print("- When you say 94681776 is 'not available for preorder', what exactly")
    print("  happens when you try to add it to cart on target.com?")
    print("- Does it show 'out of stock', 'coming soon', or something else?")
    print("- Are you testing from the same location/account each time?")

if __name__ == "__main__":
    test_simple_hypothesis()
    check_if_issue_is_timing()
    radical_hypothesis_test()
    
    print(f"\\n🎯 RECOMMENDATION:")
    print("Since we can't find a consistent API pattern, maybe we should:")
    print("1. Use simple logic: purchase_limit > 0 = available for all preorders")
    print("2. Test actual cart functionality to see what really works")
    print("3. Accept that the API might not show real-time availability")
    print("4. Or find a different API endpoint that has more accurate data")