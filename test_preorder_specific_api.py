#!/usr/bin/env python3
"""
One final attempt: test if there are pre-order specific API parameters
or a different inventory check that applies to pre-orders
"""

import requests
import json
import time

def test_preorder_inventory_params():
    """Test API with pre-order specific parameters"""
    
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    test_cases = [
        ('94681776', 'GREYED OUT'),
        ('94734932', 'CLICKABLE')
    ]
    
    # Try parameters that might reveal pre-order inventory
    param_variations = [
        {
            'name': 'Include preorder status',
            'extra_params': {
                'include_preorder_status': 'true',
                'preorder_inventory': 'true',
            }
        },
        {
            'name': 'Real-time inventory check',
            'extra_params': {
                'real_time_inventory': 'true',
                'inventory_level': 'true',
            }
        },
        {
            'name': 'Add-to-cart context',
            'extra_params': {
                'add_to_cart_context': 'true',
                'cart_eligible': 'true',
            }
        },
        {
            'name': 'Fulfillment details',
            'extra_params': {
                'detailed_fulfillment': 'true',
                'shipping_inventory': 'true',
            }
        }
    ]
    
    print("🧪 TESTING PRE-ORDER SPECIFIC API PARAMETERS")
    print("="*60)
    
    for tcin, status in test_cases:
        print(f"\n📦 TESTING {tcin} ({status}):")
        
        for param_set in param_variations:
            print(f"\n  {param_set['name']}:")
            
            # Base parameters
            params = {
                'key': api_key,
                'tcin': tcin,
                'store_id': store_id,
                'pricing_store_id': store_id,
                'visitor_id': f"{int(time.time()*1000)}{'0'*10}",
            }
            
            # Add extra parameters
            params.update(param_set['extra_params'])
            
            try:
                response = requests.get(base_url, params=params, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Look for any new inventory-related fields
                    def search_for_inventory(obj, path=""):
                        found = []
                        if isinstance(obj, dict):
                            for key, value in obj.items():
                                current_path = f"{path}.{key}" if path else key
                                if any(term in key.lower() for term in ['inventory', 'stock', 'available', 'preorder', 'order']):
                                    found.append((current_path, value))
                                if isinstance(value, dict):
                                    found.extend(search_for_inventory(value, current_path))
                        return found
                    
                    inventory_fields = search_for_inventory(data)
                    
                    if inventory_fields:
                        print(f"    ✅ Found inventory fields:")
                        for field, value in inventory_fields:
                            print(f"      {field}: {value}")
                    else:
                        print(f"    ❌ No new inventory fields")
                
                else:
                    print(f"    ❌ HTTP {response.status_code}")
            
            except Exception as e:
                print(f"    ❌ Error: {e}")
            
            time.sleep(0.3)

def manual_field_inspection():
    """Manually inspect specific fields that might be inventory indicators"""
    
    print(f"\n🔍 MANUAL FIELD INSPECTION")
    print("Looking at specific fields that might indicate inventory")
    print("="*60)
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'GREYED OUT'),
        ('test_response_94734932.json', '94734932', 'CLICKABLE')
    ]
    
    # Fields to specifically check
    fields_to_check = [
        'data.product.item.fulfillment',
        'data.product.item.compliance', 
        'data.product.item.environmental_segmentation',
        'data.product.item.ribbons',
        'data.product.item.handling',
        'data.product.sales_classification_nodes',
        'data.product.price',
        'data.product.ratings_and_reviews',
    ]
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            print(f"\n{tcin} ({status}):")
            
            for field_path in fields_to_check:
                # Navigate to the field
                parts = field_path.split('.')
                current = data
                
                try:
                    for part in parts:
                        current = current[part]
                    
                    print(f"  {field_path}: {current}")
                    
                except KeyError:
                    print(f"  {field_path}: [MISSING]")
                    
        except Exception as e:
            print(f"Error with {filename}: {e}")

def final_hypothesis():
    """Final hypothesis: maybe it's based on a combination of existing fields"""
    
    print(f"\n💡 FINAL HYPOTHESIS TESTING")
    print("="*50)
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'GREYED OUT'),
        ('test_response_94734932.json', '94734932', 'CLICKABLE')
    ]
    
    print("Testing combinations of existing fields:")
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            item = data['data']['product']['item']
            product = data['data']['product']
            
            # Extract all boolean and key numerical values
            purchase_limit = item.get('fulfillment', {}).get('purchase_limit', 0)
            free_shipping = product.get('free_shipping', {}).get('enabled', False)
            street_date = item.get('mmbv_content', {}).get('street_date', '')
            gift_wrap = item.get('fulfillment', {}).get('is_gift_wrap_eligible', False)
            
            print(f"\n{tcin} ({status}):")
            print(f"  purchase_limit: {purchase_limit}")
            print(f"  free_shipping: {free_shipping}") 
            print(f"  street_date: {street_date}")
            print(f"  gift_wrap_eligible: {gift_wrap}")
            
            # Test different combination logics
            combo1 = purchase_limit > 0 and not free_shipping
            combo2 = purchase_limit > 0 and gift_wrap
            combo3 = purchase_limit > 0  # Simple logic
            
            print(f"  Combo 1 (limit>0 AND not free_ship): {combo1}")
            print(f"  Combo 2 (limit>0 AND gift_wrap): {combo2}")  
            print(f"  Combo 3 (limit>0 only): {combo3}")
            
        except Exception as e:
            print(f"Error: {e}")
    
    print(f"\nMaybe the actual inventory status requires:")
    print("1. A different API endpoint entirely")
    print("2. Real-time cart validation")  
    print("3. Authentication/session context")
    print("4. Or it's simply not available in public APIs")

if __name__ == "__main__":
    test_preorder_inventory_params()
    manual_field_inspection()
    final_hypothesis()
    
    print(f"\n🎯 CONCLUSION:")
    print("If no clear pattern emerges, we may need to accept that")
    print("pre-order inventory exhaustion is not visible in the current API.")
    print("The bot may need to use fallback strategies like attempt-and-handle-failure.")