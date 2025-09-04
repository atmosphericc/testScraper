#!/usr/bin/env python3
"""
Check what's actually different between available and unavailable pre-orders
Maybe the 7-day rule is wrong - let's compare all three TCINs
"""

import json
from datetime import datetime, date

def analyze_preorder_patterns():
    """Analyze all three pre-order TCINs to find the real availability pattern"""
    
    test_cases = [
        {
            'file': 'preorder_response_94681776_Out_of_Stock_Pre-order.json',
            'tcin': '94681776',
            'user_says': 'NOT AVAILABLE for preorder',
            'street_date': '2025-09-26',
            'days_away': 22
        },
        {
            'file': 'preorder_response_94723520_In_Stock_Pre-order.json', 
            'tcin': '94723520',
            'user_says': 'AVAILABLE for preorder',
            'street_date': '2025-09-05',
            'days_away': 1
        },
        {
            'file': 'tcin_response_94827553.json',
            'tcin': '94827553', 
            'user_says': 'AVAILABLE on target.com',
            'street_date': '2025-10-01',
            'days_away': 27
        }
    ]
    
    print("🔍 ANALYZING ALL PREORDER PATTERNS")
    print("="*60)
    
    for case in test_cases:
        try:
            with open(case['file']) as f:
                data = json.load(f)
                
            item = data['data']['product']['item']
            
            print(f"\\nTCIN {case['tcin']} ({case['user_says']}):")
            print(f"  Street Date: {case['street_date']} ({case['days_away']} days away)")
            
            # Check purchase limit
            purchase_limit = item.get('fulfillment', {}).get('purchase_limit', 0)
            print(f"  Purchase Limit: {purchase_limit}")
            
            # Check all unique fields
            all_fields = set(item.keys())
            print(f"  Unique fields: {sorted(all_fields)}")
            
            # Look for any boolean or availability flags
            for key, value in item.items():
                if isinstance(value, bool):
                    print(f"  Boolean {key}: {value}")
                elif isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        if isinstance(subvalue, bool) and any(word in subkey.lower() for word in ['active', 'available', 'eligible']):
                            print(f"  {key}.{subkey}: {subvalue}")
                            
        except Exception as e:
            print(f"  Error analyzing {case['file']}: {e}")
    
    print(f"\\n🤔 HYPOTHESIS TO TEST:")
    print(f"Maybe the 7-day rule is wrong and availability depends on something else?")
    print(f"- 1 day away (94723520): AVAILABLE ✅")
    print(f"- 22 days away (94681776): NOT AVAILABLE ❌") 
    print(f"- 27 days away (94827553): AVAILABLE ✅ (according to user)")
    print(f"")
    print(f"This suggests the time-based rule is incorrect!")

if __name__ == "__main__":
    analyze_preorder_patterns()
    
    print(f"\\n" + "="*60)
    print(f"🎯 NEED TO FIND THE REAL PATTERN")
    print(f"Since 94827553 (27 days away) is available according to target.com,")
    print(f"but 94681776 (22 days away) is not available,")
    print(f"the availability is NOT based on days until release.")
    print(f"")
    print(f"There must be another field or logic that determines preorder availability!")