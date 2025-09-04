#!/usr/bin/env python3
"""
Refined pre-order availability logic based on street_date analysis
"""

from datetime import datetime, date, timedelta
from typing import Dict
import logging

def parse_preorder_availability_refined(tcin: str, name: str, price: float, 
                                      purchase_limit: int, street_date: str, logger=None) -> Dict:
    """
    Refined pre-order availability logic based on street_date proximity
    
    Key insight: Pre-orders seem to be available for purchase only when close to release date
    - NOT AVAILABLE: street_date = 2025-09-26 (22 days away)  
    - AVAILABLE: street_date = 2025-09-05 (1 day away)
    
    Theory: Pre-orders become purchasable within ~7 days of release
    """
    if logger is None:
        logger = logging.getLogger(__name__)
        
    try:
        # Parse street date (format: YYYY-MM-DD)
        release_date = datetime.strptime(street_date, '%Y-%m-%d').date()
        today = date.today()
        days_until_release = (release_date - today).days
        
        # 🎯 REFINED PRE-ORDER LOGIC:
        # Available if:
        # 1. Has purchase_limit > 0 (basic requirement)
        # 2. Release date is within pre-order window (testing different thresholds)
        
        has_purchase_limit = purchase_limit > 0
        
        # Test different time windows to find the pattern
        within_1_day = days_until_release <= 1
        within_3_days = days_until_release <= 3  
        within_7_days = days_until_release <= 7
        within_14_days = days_until_release <= 14
        
        # Based on our test data:
        # - 1 day away = AVAILABLE
        # - 22 days away = NOT AVAILABLE
        # Let's try 7-day window as the threshold
        
        available = has_purchase_limit and within_7_days
        
        # Determine status and confidence
        if not has_purchase_limit:
            reason = "preorder_no_purchase_limit"
            confidence = "high"
            available = False
        elif within_1_day:
            reason = "preorder_available_releases_very_soon"
            confidence = "high"
        elif within_7_days:
            reason = f"preorder_available_releases_in_{days_until_release}_days"
            confidence = "high"
        else:
            reason = f"preorder_not_yet_available_releases_in_{days_until_release}_days"
            confidence = "high"
            available = False
        
        logger.info(f"Pre-order {tcin}: days_until_release={days_until_release}, available={available}, reason={reason}")
        
        return {
            'tcin': tcin,
            'name': name[:50],
            'price': price,
            'available': available,
            'purchase_limit': purchase_limit,
            'product_type': 'preorder',
            'street_date': street_date,
            'release_date': release_date.strftime('%Y-%m-%d'),
            'days_until_release': days_until_release,
            'confidence': confidence,
            'reason': reason,
            'status': 'success',
            # Debug info
            'debug': {
                'has_purchase_limit': has_purchase_limit,
                'within_1_day': within_1_day,
                'within_3_days': within_3_days,
                'within_7_days': within_7_days,
                'within_14_days': within_14_days
            }
        }
        
    except ValueError as e:
        logger.error(f"Pre-order date parsing failed for {tcin}: {e}")
        return {
            'tcin': tcin,
            'name': name[:50],
            'price': price,
            'available': False,
            'purchase_limit': purchase_limit,
            'product_type': 'preorder',
            'confidence': 'low',
            'reason': 'preorder_date_parse_error',
            'status': 'success'
        }

def test_refined_logic():
    """Test the refined logic with our known examples"""
    import json
    
    print("🧪 TESTING REFINED PRE-ORDER LOGIC")
    print("="*50)
    
    test_cases = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'Should be NOT AVAILABLE'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'Should be AVAILABLE')
    ]
    
    for filename, tcin, expected in test_cases:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            item = data['data']['product']['item']
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = data['data']['product'].get('price', {}).get('current_retail', 0)
            purchase_limit = item.get('fulfillment', {}).get('purchase_limit', 0)
            street_date = item.get('mmbv_content', {}).get('street_date')
            
            result = parse_preorder_availability_refined(tcin, name, price, purchase_limit, street_date)
            
            print(f"\\nTCIN {tcin} ({expected}):")
            print(f"  Available: {'✅ YES' if result['available'] else '❌ NO'}")
            print(f"  Reason: {result['reason']}")
            print(f"  Days until release: {result['days_until_release']}")
            print(f"  Debug info: {result['debug']}")
            
            # Check if it matches expected
            expected_available = 'AVAILABLE' in expected
            actual_available = result['available']
            
            if expected_available == actual_available:
                print(f"  ✅ CORRECT PREDICTION!")
            else:
                print(f"  ❌ INCORRECT - Expected: {expected_available}, Got: {actual_available}")
            
        except Exception as e:
            print(f"  ❌ Error testing {filename}: {e}")

if __name__ == "__main__":
    test_refined_logic()
    
    print("\\n" + "="*50)
    print("🎯 TESTING DIFFERENT TIME THRESHOLDS:")
    
    # Test different thresholds
    thresholds = [1, 3, 7, 14, 30]
    
    for threshold in thresholds:
        print(f"\\nIf threshold = {threshold} days:")
        print(f"  TCIN 94681776 (22 days away): {'✅ AVAILABLE' if 22 <= threshold else '❌ NOT AVAILABLE'}")
        print(f"  TCIN 94723520 (1 day away): {'✅ AVAILABLE' if 1 <= threshold else '❌ NOT AVAILABLE'}")