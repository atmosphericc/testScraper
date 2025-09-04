#!/usr/bin/env python3
"""
Find the API field that indicates pre-order inventory status
Looking for what makes 94681776 show "greyed out pre-order button" vs others that are clickable
"""

import json

def search_inventory_indicators():
    """Search for inventory-related fields that might indicate pre-order stock status"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'GREYED OUT (no pre-order inventory)'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'CLICKABLE (has pre-order inventory)'),  
        ('tcin_response_94827553.json', '94827553', 'CLICKABLE (has pre-order inventory)'),
        ('test_response_94734932.json', '94734932', 'CLICKABLE (has pre-order inventory)')
    ]
    
    print("🔍 SEARCHING FOR PRE-ORDER INVENTORY INDICATORS")
    print("Looking for fields that show inventory exhaustion vs availability")
    print("="*70)
    
    all_data = {}
    
    # Load all data
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
                all_data[tcin] = {
                    'status': status,
                    'full_data': data
                }
        except Exception as e:
            print(f"Error loading {filename}: {e}")
    
    # Search for inventory/stock related terms
    inventory_terms = [
        'inventory', 'stock', 'available', 'quantity', 'count', 'limit',
        'sold', 'out', 'exhausted', 'remaining', 'level', 'status',
        'purchasable', 'orderable', 'backorder', 'preorder'
    ]
    
    def search_for_terms(obj, path="", terms=None):
        """Recursively search for fields containing inventory terms"""
        if terms is None:
            terms = inventory_terms
            
        found = []
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                
                # Check if key contains inventory terms
                if any(term in key.lower() for term in terms):
                    found.append((current_path, key, value))
                
                # Check if string value contains inventory terms  
                elif isinstance(value, str) and any(term in value.lower() for term in terms):
                    found.append((current_path, f"{key}(value)", value))
                
                # Recurse
                if isinstance(value, (dict, list)):
                    found.extend(search_for_terms(value, current_path, terms))
                    
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]" if path else f"[{i}]"
                found.extend(search_for_terms(item, current_path, terms))
        
        return found
    
    # Search each TCIN for inventory indicators
    all_inventory_fields = {}
    
    for tcin, info in all_data.items():
        print(f"\\n📦 SEARCHING {tcin} ({info['status']}):")
        
        inventory_fields = search_for_terms(info['full_data'])
        
        if inventory_fields:
            print(f"  Found {len(inventory_fields)} potential inventory fields:")
            for path, key, value in inventory_fields:
                print(f"    {path}: {value}")
                
                # Store for comparison
                if path not in all_inventory_fields:
                    all_inventory_fields[path] = {}
                all_inventory_fields[path][tcin] = value
        else:
            print(f"  No obvious inventory fields found")
    
    # Compare inventory fields across TCINs
    print(f"\\n🎯 INVENTORY FIELD COMPARISON:")
    print("="*70)
    
    greyed_out_tcin = '94681776'
    clickable_tcins = ['94723520', '94827553', '94734932']
    
    distinguishing_fields = []
    
    for field_path, tcin_values in all_inventory_fields.items():
        if len(tcin_values) > 1:  # Field exists in multiple TCINs
            
            # Get values
            greyed_out_value = tcin_values.get(greyed_out_tcin)
            clickable_values = [tcin_values.get(tcin) for tcin in clickable_tcins if tcin in tcin_values]
            
            # Check if greyed out has different value than clickable ones
            if greyed_out_value is not None and clickable_values:
                all_clickable_same = len(set(str(v) for v in clickable_values if v is not None)) <= 1
                greyed_different = str(greyed_out_value) not in [str(v) for v in clickable_values if v is not None]
                
                if greyed_different or not all_clickable_same:
                    distinguishing_fields.append((field_path, tcin_values))
                    
                    print(f"\\n🎯 {field_path} - POTENTIAL INDICATOR:")
                    print(f"    GREYED OUT ({greyed_out_tcin}): {greyed_out_value}")
                    for tcin in clickable_tcins:
                        if tcin in tcin_values:
                            print(f"    CLICKABLE ({tcin}): {tcin_values[tcin]}")
    
    if not distinguishing_fields:
        print("\\n❌ No distinguishing inventory fields found!")
        print("The inventory status might be determined by:")
        print("1. A different API endpoint")
        print("2. Real-time cart/inventory API calls")
        print("3. Fields we haven't identified yet")
    
    return distinguishing_fields

def search_fulfillment_deeper():
    """Deep dive into fulfillment section looking for inventory status"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'GREYED OUT'),
        ('test_response_94734932.json', '94734932', 'CLICKABLE')  # Compare just these two for clarity
    ]
    
    print(f"\\n🔍 DEEP FULFILLMENT ANALYSIS:")
    print("="*50)
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            print(f"\\n{tcin} ({status}):")
            
            # Check entire fulfillment section
            item = data['data']['product']['item']
            fulfillment = item.get('fulfillment', {})
            
            print(f"  Complete fulfillment data:")
            for key, value in fulfillment.items():
                print(f"    {key}: {value}")
            
            # Check if there are any boolean flags we missed
            for key, value in item.items():
                if isinstance(value, bool):
                    print(f"  Boolean field {key}: {value}")
            
        except Exception as e:
            print(f"  Error: {e}")

def check_specific_patterns():
    """Check for specific patterns that might indicate inventory"""
    print(f"\\n🔍 CHECKING SPECIFIC PATTERNS:")
    print("="*50)
    
    # Based on what we know, let's check specific things
    patterns_to_check = [
        # Maybe there are shipping/delivery differences
        "shipping",
        "delivery", 
        "fulfillment_speed",
        "availability_status",
        "purchasability",
        # Maybe there are different eligibility rules after all
        "eligible",
        "restriction",
        "blocked",
        "disabled"
    ]
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'GREYED OUT'),
        ('test_response_94734932.json', '94734932', 'CLICKABLE')
    ]
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            print(f"\\n{tcin} ({status}) - Pattern check:")
            
            # Convert entire response to string for text search
            full_text = json.dumps(data).lower()
            
            for pattern in patterns_to_check:
                if pattern in full_text:
                    print(f"  Contains '{pattern}': ✅")
                    
                    # Try to find the specific field
                    def find_pattern_fields(obj, path=""):
                        found = []
                        if isinstance(obj, dict):
                            for key, value in obj.items():
                                current_path = f"{path}.{key}" if path else key
                                if pattern in key.lower() or (isinstance(value, str) and pattern in value.lower()):
                                    found.append((current_path, value))
                                if isinstance(value, (dict, list)):
                                    found.extend(find_pattern_fields(value, current_path))
                        elif isinstance(obj, list):
                            for i, item in enumerate(obj):
                                current_path = f"{path}[{i}]" if path else f"[{i}]"
                                found.extend(find_pattern_fields(item, current_path))
                        return found
                    
                    pattern_fields = find_pattern_fields(data)
                    for field_path, value in pattern_fields:
                        print(f"    {field_path}: {value}")
                else:
                    print(f"  Contains '{pattern}': ❌")
        
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == "__main__":
    distinguishing_fields = search_inventory_indicators()
    search_fulfillment_deeper() 
    check_specific_patterns()
    
    print(f"\\n🎯 SUMMARY:")
    if distinguishing_fields:
        print(f"Found {len(distinguishing_fields)} potential inventory indicators!")
        print("These fields might show pre-order inventory exhaustion.")
    else:
        print("No clear inventory indicators found in current API responses.")
        print("May need to:")
        print("1. Try different API parameters")
        print("2. Use a different endpoint")  
        print("3. Make real-time cart calls to determine availability")