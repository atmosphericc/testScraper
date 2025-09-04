#!/usr/bin/env python3
"""
Simple test: Maybe all preorders with purchase_limit > 0 are actually available
Let's test this simple hypothesis against user feedback
"""

from final_preorder_parser import parse_availability_with_preorders
import json

def simple_preorder_logic(tcin: str, data: dict) -> dict:
    """Ultra-simple preorder logic: if has purchase_limit > 0, it's available"""
    try:
        product = data['data']['product']
        item = product['item']
        
        name = item.get('product_description', {}).get('title', 'Unknown')
        price = product.get('price', {}).get('current_retail', 0)
        fulfillment = item.get('fulfillment', {})
        purchase_limit = fulfillment.get('purchase_limit', 0)
        
        # Check if preorder
        has_eligibility_rules = 'eligibility_rules' in item
        street_date = item.get('mmbv_content', {}).get('street_date')
        
        if not has_eligibility_rules and street_date:
            # PREORDER: Simple rule - available if purchase_limit > 0
            available = purchase_limit > 0
            
            return {
                'tcin': tcin,
                'name': name[:50],
                'price': price,
                'available': available,
                'purchase_limit': purchase_limit,
                'product_type': 'preorder',
                'street_date': street_date,
                'logic': 'simple_purchase_limit_only',
                'reason': f'preorder_{"available" if available else "unavailable"}_purchase_limit_{purchase_limit}',
                'status': 'success'
            }
        else:
            return {'tcin': tcin, 'available': False, 'reason': 'not_preorder'}
            
    except Exception as e:
        return {'tcin': tcin, 'available': False, 'error': str(e)}

def test_simple_logic():
    """Test the simple logic against our known cases"""
    print("🧪 TESTING ULTRA-SIMPLE PREORDER LOGIC")
    print("Rule: preorder available if purchase_limit > 0")
    print("="*60)
    
    test_cases = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'User says: NOT AVAILABLE'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'User says: AVAILABLE'),
        ('tcin_response_94827553.json', '94827553', 'User says: AVAILABLE')
    ]
    
    for filename, tcin, user_feedback in test_cases:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            result = simple_preorder_logic(tcin, data)
            
            print(f"\\nTCIN {tcin}:")
            print(f"  {user_feedback}")
            print(f"  Purchase Limit: {result.get('purchase_limit', 'N/A')}")
            print(f"  Simple Logic Says: {'✅ AVAILABLE' if result['available'] else '❌ NOT AVAILABLE'}")
            print(f"  Street Date: {result.get('street_date', 'N/A')}")
            
            # Compare with user feedback
            user_says_available = 'AVAILABLE' in user_feedback
            logic_says_available = result['available']
            
            if user_says_available == logic_says_available:
                print(f"  🎯 MATCHES USER FEEDBACK!")
            else:
                print(f"  ❌ CONTRADICTS USER FEEDBACK")
                print(f"     User: {user_says_available}, Logic: {logic_says_available}")
            
        except Exception as e:
            print(f"  ❌ Error: {e}")

    print(f"\\n🤔 HYPOTHESIS:")
    print("If the simple logic matches user feedback, then maybe:")
    print("1. All preorders with purchase_limit > 0 are actually available")  
    print("2. The distinction between 'available' and 'not available' preorders")
    print("   might be something else entirely (like regional availability,")
    print("   account-specific restrictions, or time-sensitive windows)")
    
    print(f"\\n💡 ALTERNATIVE THEORIES:")
    print("- Maybe 94681776 became unavailable after you checked?")
    print("- Maybe availability depends on user account/location?")
    print("- Maybe the API doesn't show real-time cart availability?")

if __name__ == "__main__":
    test_simple_logic()
    
    print(f"\\n" + "="*60)
    print("🎯 RECOMMENDATION:")
    print("If simple logic works, use: available = purchase_limit > 0 for preorders")
    print("This would be the most reliable approach based on API data.")