#!/usr/bin/env python3
"""
DROP-IN REPLACEMENT for existing parse_availability method
Handles both regular products AND pre-orders seamlessly

INTEGRATION INSTRUCTIONS:
1. Replace the existing parse_availability method in your stock checker classes with this one
2. No other changes needed - same input/output interface
3. Will automatically detect and handle pre-orders
"""

from datetime import datetime, date
from typing import Dict
import logging

def parse_availability(tcin: str, data: Dict, logger=None) -> Dict:
    """
    🔥 ENHANCED parse_availability method with PRE-ORDER support
    
    DROP-IN REPLACEMENT for existing method - same interface, enhanced functionality
    
    Auto-detects:
    - Regular products (uses existing eligibility_rules logic)
    - Pre-orders (uses street_date logic)
    
    Args:
        tcin: Product TCIN
        data: API response data
        logger: Optional logger instance
    
    Returns:
        Same format as existing method, with additional pre-order fields
    """
    if logger is None:
        logger = logging.getLogger(__name__)
        
    try:
        product = data['data']['product']
        item = product['item']
        
        # Extract basic info (same as existing)
        name = item.get('product_description', {}).get('title', 'Unknown')
        price = product.get('price', {}).get('current_retail', 0)
        fulfillment = item.get('fulfillment', {})
        purchase_limit = fulfillment.get('purchase_limit', 0)
        
        # 🎯 AUTO-DETECT: Pre-order vs Regular product
        has_eligibility_rules = 'eligibility_rules' in item
        street_date = item.get('mmbv_content', {}).get('street_date')
        
        if not has_eligibility_rules and street_date:
            # 📦 PRE-ORDER DETECTED
            logger.debug(f"Pre-order detected: {tcin} (street_date: {street_date})")
            return _parse_preorder_availability(tcin, name, price, purchase_limit, street_date, logger)
            
        elif has_eligibility_rules:
            # 📦 REGULAR PRODUCT - use existing logic
            return _parse_regular_availability(tcin, name, price, item, logger)
            
        else:
            # Unknown pattern - fallback with conservative approach
            logger.warning(f"Unknown product pattern for {tcin}")
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': price,
                'available': purchase_limit > 0,  # Conservative: if can purchase, assume available
                'purchase_limit': purchase_limit,
                'confidence': 'low',
                'reason': 'unknown_pattern_fallback',
                'status': 'success'
            }
            
    except Exception as e:
        logger.error(f"Error parsing response for {tcin}: {e}")
        return {
            'tcin': tcin,
            'available': False,
            'status': 'parse_error',
            'confidence': 'error',
            'error': str(e)
        }

def _parse_preorder_availability(tcin: str, name: str, price: float, 
                               purchase_limit: int, street_date: str, logger) -> Dict:
    """Parse pre-order availability based on street date and purchase limit"""
    try:
        # Parse street date (format: YYYY-MM-DD)
        release_date = datetime.strptime(street_date, '%Y-%m-%d').date()
        today = date.today()
        days_until_release = (release_date - today).days
        
        # 🎯 PRE-ORDER AVAILABILITY LOGIC:
        # Available if purchase_limit > 0 (can pre-order)
        # This matches Target's behavior - pre-orders show as "available" when you can place the order
        available = purchase_limit > 0
        
        # Status messaging
        if available:
            if days_until_release > 0:
                reason = f"preorder_available_releases_in_{days_until_release}_days"
            elif days_until_release == 0:
                reason = "preorder_available_releases_today"
            else:
                reason = "preorder_available_already_released"
            confidence = "high"
        else:
            reason = "preorder_unavailable_no_purchase_limit"
            confidence = "high"
        
        logger.info(f"Pre-order {tcin}: available={available}, releases={street_date} ({days_until_release} days)")
        
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
            'status': 'success'
        }
        
    except ValueError as e:
        # Date parsing failed - conservative fallback
        logger.error(f"Pre-order date parsing failed for {tcin}: {e}")
        return {
            'tcin': tcin,
            'name': name[:50],
            'price': price,
            'available': purchase_limit > 0,  # Fallback: if can purchase, assume available
            'purchase_limit': purchase_limit,
            'product_type': 'preorder',
            'confidence': 'medium',
            'reason': f'preorder_date_parse_error',
            'status': 'success'
        }

def _parse_regular_availability(tcin: str, name: str, price: float, item: Dict, logger) -> Dict:
    """Parse regular product availability using existing refined logic"""
    fulfillment = item.get('fulfillment', {})
    eligibility = item.get('eligibility_rules', {})
    
    is_marketplace = fulfillment.get('is_marketplace', False)
    purchase_limit = fulfillment.get('purchase_limit', 0)
    
    if is_marketplace:
        # Third-party seller - simple logic
        available = purchase_limit > 0
        seller_type = "third-party"
        confidence = "high"
        reason = "marketplace_seller"
        
    else:
        # Target direct - use existing refined algorithm
        ship_to_guest_active = eligibility.get('ship_to_guest', {}).get('is_active', False)
        inventory_excluded = eligibility.get('inventory_notification_to_guest_excluded', {}).get('is_active', False)
        hold_active = eligibility.get('hold', {}).get('is_active', False)
        
        # Existing decision logic
        if inventory_excluded:
            available = False
            confidence = "high"
            reason = "inventory_notification_excluded"
        elif ship_to_guest_active and purchase_limit >= 1:
            if hold_active:
                available = False
                confidence = "medium"
                reason = "hold_restriction_present"
            else:
                available = True
                confidence = "high" 
                reason = "ship_to_guest_active"
        else:
            available = False
            confidence = "medium"
            reason = "no_positive_signals"
        
        seller_type = "target"
    
    return {
        'tcin': tcin,
        'name': name[:50],
        'price': price,
        'available': available,
        'seller_type': seller_type,
        'purchase_limit': purchase_limit,
        'product_type': 'regular',
        'confidence': confidence,
        'reason': reason,
        'status': 'success'
    }

# Test the drop-in replacement
if __name__ == "__main__":
    import json
    
    print("🔥 TESTING DROP-IN REPLACEMENT")
    print("="*40)
    
    # Test with the pre-order data we collected
    test_files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520')
    ]
    
    for filename, tcin in test_files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            result = parse_availability(tcin, data)
            
            print(f"\nTCIN {tcin}:")
            print(f"  Available: {'✅ YES' if result['available'] else '❌ NO'}")
            print(f"  Type: {result.get('product_type', 'unknown')}")
            print(f"  Reason: {result['reason']}")
            
            if result.get('product_type') == 'preorder':
                print(f"  Release: {result.get('street_date')} ({result.get('days_until_release')} days)")
            
        except Exception as e:
            print(f"Error testing {filename}: {e}")