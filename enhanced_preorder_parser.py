#!/usr/bin/env python3
"""
Enhanced API parser that handles both regular products and pre-orders
Based on analysis of Target's API response patterns
"""

import json
from datetime import datetime, date
from typing import Dict

class EnhancedPreorderParser:
    """Enhanced parser that handles both regular products and pre-orders"""
    
    def parse_availability(self, tcin: str, data: Dict) -> Dict:
        """
        Enhanced availability parsing that handles pre-orders
        
        Detection Logic:
        1. Check if eligibility_rules exists (regular products have this)
        2. If no eligibility_rules but has street_date -> Pre-order
        3. For pre-orders: available if street_date <= today
        """
        try:
            product = data['data']['product']
            item = product['item']
            
            # Extract basic info
            name = item.get('product_description', {}).get('title', 'Unknown')
            price = product.get('price', {}).get('current_retail', 0)
            fulfillment = item.get('fulfillment', {})
            purchase_limit = fulfillment.get('purchase_limit', 0)
            
            # Check if this is a pre-order (no eligibility_rules + has street_date)
            has_eligibility_rules = 'eligibility_rules' in item
            street_date = item.get('mmbv_content', {}).get('street_date')
            
            if not has_eligibility_rules and street_date:
                # This is a PRE-ORDER
                return self._parse_preorder_availability(tcin, name, price, purchase_limit, street_date)
            
            elif has_eligibility_rules:
                # This is a REGULAR PRODUCT - use existing logic
                return self._parse_regular_availability(tcin, name, price, item)
            
            else:
                # Unknown pattern - fallback
                return {
                    'tcin': tcin,
                    'name': name[:50],
                    'price': price,
                    'available': False,
                    'product_type': 'unknown',
                    'confidence': 'low',
                    'reason': 'unknown_pattern',
                    'status': 'success'
                }
                
        except Exception as e:
            return {
                'tcin': tcin,
                'available': False,
                'status': 'parse_error',
                'error': str(e)
            }
    
    def _parse_preorder_availability(self, tcin: str, name: str, price: float, 
                                   purchase_limit: int, street_date: str) -> Dict:
        """Parse pre-order availability based on street date"""
        try:
            # Parse street date (format: YYYY-MM-DD)
            release_date = datetime.strptime(street_date, '%Y-%m-%d').date()
            today = date.today()
            
            # Pre-order logic:
            # - Available if you can pre-order it (purchase_limit > 0)
            # - Pre-orders are "available" even before release date if you can order them
            available = purchase_limit > 0
            
            # Determine status message
            if available:
                if release_date > today:
                    status_msg = f"pre-order_available_releases_{street_date}"
                else:
                    status_msg = "pre-order_available_released"
                confidence = "high"
            else:
                status_msg = "pre-order_unavailable"
                confidence = "high"
            
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': price,
                'available': available,
                'purchase_limit': purchase_limit,
                'product_type': 'pre-order',
                'street_date': street_date,
                'release_date': release_date.strftime('%Y-%m-%d'),
                'days_until_release': (release_date - today).days,
                'confidence': confidence,
                'reason': status_msg,
                'status': 'success'
            }
            
        except ValueError as e:
            # Date parsing failed
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': price,
                'available': False,
                'product_type': 'pre-order',
                'confidence': 'low',
                'reason': f'date_parse_error_{e}',
                'status': 'success'
            }
    
    def _parse_regular_availability(self, tcin: str, name: str, price: float, item: Dict) -> Dict:
        """Parse regular product availability (existing logic)"""
        fulfillment = item.get('fulfillment', {})
        eligibility = item.get('eligibility_rules', {})
        
        is_marketplace = fulfillment.get('is_marketplace', False)
        purchase_limit = fulfillment.get('purchase_limit', 0)
        
        if is_marketplace:
            # Third-party seller
            available = purchase_limit > 0
            seller_type = "third-party"
            confidence = "high"
            reason = "marketplace"
            
        else:
            # Target direct - use refined algorithm
            ship_to_guest_active = eligibility.get('ship_to_guest', {}).get('is_active', False)
            inventory_excluded = eligibility.get('inventory_notification_to_guest_excluded', {}).get('is_active', False)
            hold_active = eligibility.get('hold', {}).get('is_active', False)
            
            if inventory_excluded:
                available = False
                confidence = "high"
                reason = "inventory_notification_excluded"
            elif ship_to_guest_active and purchase_limit >= 1:
                if hold_active:
                    available = False
                    confidence = "medium"
                    reason = "hold_restriction"
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
            'purchase_limit': purchase_limit,
            'product_type': 'regular',
            'seller_type': seller_type,
            'confidence': confidence,
            'reason': reason,
            'status': 'success'
        }

def test_parser():
    """Test the enhanced parser with our pre-order examples"""
    parser = EnhancedPreorderParser()
    
    test_files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', 'Out of Stock Pre-order'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', 'In Stock Pre-order')
    ]
    
    print("🧪 TESTING ENHANCED PRE-ORDER PARSER")
    print("="*50)
    
    for filename, description in test_files:
        print(f"\nTesting {description}:")
        
        try:
            with open(filename) as f:
                data = json.load(f)
            
            result = parser.parse_availability(data['data']['product']['tcin'], data)
            
            print(f"  TCIN: {result['tcin']}")
            print(f"  Product: {result['name']}")
            print(f"  Available: {'✅ YES' if result['available'] else '❌ NO'}")
            print(f"  Type: {result['product_type']}")
            print(f"  Reason: {result['reason']}")
            print(f"  Confidence: {result['confidence']}")
            
            if result['product_type'] == 'pre-order':
                print(f"  Release Date: {result.get('release_date', 'N/A')}")
                print(f"  Days Until Release: {result.get('days_until_release', 'N/A')}")
            
        except Exception as e:
            print(f"  ❌ Error testing {filename}: {e}")

if __name__ == "__main__":
    test_parser()