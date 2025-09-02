#!/usr/bin/env python3
"""
Refined API stock detection algorithm
Based on deep analysis of eligibility rule patterns
"""
import sys
import asyncio
import aiohttp
sys.path.insert(0, 'src')

def refined_parse_availability(tcin: str, data: dict) -> dict:
    """
    Refined stock detection algorithm based on eligibility rule analysis
    """
    try:
        product = data['data']['product']
        item = product['item']
        
        # Extract key fields
        name = item.get('product_description', {}).get('title', 'Unknown')
        price = product.get('price', {}).get('current_retail', 0)
        
        # Fulfillment info
        fulfillment = item.get('fulfillment', {})
        is_marketplace = fulfillment.get('is_marketplace', False)
        purchase_limit = fulfillment.get('purchase_limit', 0)
        
        # Eligibility rules analysis
        eligibility = item.get('eligibility_rules', {})
        
        # NEW REFINED ALGORITHM
        if is_marketplace:
            # Third-party seller - simple check
            available = purchase_limit > 0
            seller_type = "third-party"
            confidence = "high"
            
        else:
            # Target direct - analyze eligibility patterns
            
            # Check for positive availability signals
            ship_to_guest_active = eligibility.get('ship_to_guest', {}).get('is_active', False)
            scheduled_delivery_active = eligibility.get('scheduled_delivery', {}).get('is_active', False)
            
            # Check for negative availability signals  
            inventory_notification_excluded = eligibility.get('inventory_notification_to_guest_excluded', {}).get('is_active', False)
            
            # Check for restriction signals
            hold_active = eligibility.get('hold', {}).get('is_active', False)
            
            print(f"  REFINED ANALYSIS FOR {tcin}:")
            print(f"    Positive signals: ship_to_guest={ship_to_guest_active}, scheduled_delivery={scheduled_delivery_active}")
            print(f"    Negative signals: inventory_notification_excluded={inventory_notification_excluded}")
            print(f"    Restriction signals: hold={hold_active}")
            print(f"    Purchase limit: {purchase_limit}")
            
            # REFINED DECISION LOGIC
            
            # Rule 1: If inventory notification excluded is active, definitely OOS
            if inventory_notification_excluded:
                available = False
                confidence = "high"
                reason = "inventory_notification_excluded active"
                
            # Rule 2: If ship_to_guest is active AND purchase_limit > 0, likely available
            elif ship_to_guest_active and purchase_limit >= 1:
                # But check for hold restriction
                if hold_active:
                    available = False  # Conservative: hold might mean restricted
                    confidence = "medium"
                    reason = "ship_to_guest active but hold restriction present"
                else:
                    available = True
                    confidence = "high" 
                    reason = "ship_to_guest active, no restrictions"
                    
            # Rule 3: No positive eligibility signals = likely OOS
            elif not ship_to_guest_active and not scheduled_delivery_active:
                available = False
                confidence = "high"
                reason = "no positive eligibility signals"
                
            # Rule 4: Edge cases
            else:
                available = False  # Conservative default
                confidence = "low"
                reason = "unclear eligibility pattern"
            
            seller_type = "target"
            
            print(f"    DECISION: {available} (confidence: {confidence}, reason: {reason})")
        
        return {
            'tcin': tcin,
            'name': name[:50],
            'price': price,
            'available': available,
            'seller_type': seller_type,
            'purchase_limit': purchase_limit,
            'confidence': confidence,
            'reason': reason if 'reason' in locals() else 'marketplace check',
            'status': 'success'
        }
        
    except Exception as e:
        print(f"Error parsing {tcin}: {e}")
        return {
            'tcin': tcin,
            'available': False,
            'status': 'parse_error',
            'error': str(e),
            'confidence': 'error'
        }

async def test_refined_algorithm():
    """Test refined algorithm against known cases"""
    from stock_checker import StockChecker
    
    test_cases = [
        ('94724987', 'Should be FALSE (was false positive)'),
        ('89542109', 'Should be TRUE (was false negative)'), 
        ('94681785', 'Should be FALSE'),
        ('94336414', 'Should be FALSE'),
        ('94681770', 'Should be FALSE')
    ]
    
    print('TESTING REFINED ALGORITHM')
    print('=' * 40)
    
    # Create checker and replace parse function
    checker = StockChecker(use_website_checking=False)
    checker.parse_availability = refined_parse_availability
    
    async with aiohttp.ClientSession() as session:
        for tcin, expected in test_cases:
            print(f'\n{tcin}: {expected}')
            print('-' * 50)
            
            result = await checker.check_stock(session, tcin)
            
            status = 'AVAILABLE' if result.get('available') else 'NOT AVAILABLE'
            confidence = result.get('confidence', 'unknown')
            reason = result.get('reason', 'unknown')
            
            print(f'RESULT: {status} (confidence: {confidence})')
            print(f'REASON: {reason}')

if __name__ == "__main__":
    asyncio.run(test_refined_algorithm())