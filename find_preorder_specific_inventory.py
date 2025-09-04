#!/usr/bin/env python3
"""
Since regular products use eligibility_rules for inventory status,
pre-orders must use a different system. Let's find the pre-order inventory fields.
"""

import json

def compare_preorder_vs_regular_inventory_systems():
    """Compare how regular vs pre-order inventory is represented in API"""
    
    print("🔍 COMPARING INVENTORY SYSTEMS:")
    print("Regular products: use eligibility_rules")  
    print("Pre-orders: use ??? (let's find out)")
    print("="*60)
    
    # Load our pre-order data
    preorder_files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'GREYED OUT'),
        ('test_response_94734932.json', '94734932', 'CLICKABLE')
    ]
    
    for filename, tcin, status in preorder_files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            print(f"\n📦 PRE-ORDER {tcin} ({status}):")
            item = data['data']['product']['item']
            product = data['data']['product']
            
            # Focus on fields that might be pre-order inventory indicators
            print(f"  Has eligibility_rules: {'eligibility_rules' in item}")
            print(f"  Street date: {item.get('mmbv_content', {}).get('street_date')}")
            
            # Look for any boolean flags that might indicate inventory status
            print(f"\n  Boolean fields in item:")
            for key, value in item.items():
                if isinstance(value, bool):
                    print(f"    {key}: {value}")
                elif isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        if isinstance(subvalue, bool):
                            print(f"    {key}.{subkey}: {subvalue}")
            
            # Look for any numerical fields that might be inventory counters
            print(f"\n  Numerical fields in item:")
            for key, value in item.items():
                if isinstance(value, (int, float)) and key != 'tcin':
                    print(f"    {key}: {value}")
                elif isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        if isinstance(subvalue, (int, float)):
                            print(f"    {key}.{subkey}: {subvalue}")
            
            # Check product-level fields too
            print(f"\n  Product-level boolean fields:")
            for key, value in product.items():
                if isinstance(value, bool):
                    print(f"    {key}: {value}")
                elif isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        if isinstance(subvalue, bool):
                            print(f"      {key}.{subkey}: {subvalue}")
            
        except Exception as e:
            print(f"Error with {filename}: {e}")

def deep_diff_analysis():
    """Deep comparison between the two pre-orders to find the differentiating field"""
    
    print(f"\n🎯 DEEP DIFF ANALYSIS:")
    print("Looking for ANY field that differs between GREYED OUT vs CLICKABLE")
    print("="*60)
    
    try:
        # Load both pre-orders
        with open('preorder_response_94681776_Out_of_Stock_Pre-order.json') as f:
            greyed_out = json.load(f)
        
        with open('test_response_94734932.json') as f:
            clickable = json.load(f)
        
        def flatten_dict(d, parent_key='', sep='.'):
            """Flatten nested dict for easy comparison"""
            items = []
            for k, v in d.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k
                if isinstance(v, dict):
                    items.extend(flatten_dict(v, new_key, sep=sep).items())
                elif isinstance(v, list):
                    # For lists, just take first item if it's a dict
                    if v and isinstance(v[0], dict):
                        items.extend(flatten_dict(v[0], f"{new_key}[0]", sep=sep).items())
                    else:
                        items.append((new_key, str(v)))
                else:
                    items.append((new_key, v))
            return dict(items)
        
        # Flatten both structures
        greyed_flat = flatten_dict(greyed_out)
        clickable_flat = flatten_dict(clickable)
        
        # Find fields that exist in both but have different values
        different_fields = []
        
        for key in greyed_flat.keys():
            if key in clickable_flat:
                greyed_val = greyed_flat[key]
                clickable_val = clickable_flat[key]
                
                if str(greyed_val) != str(clickable_val):
                    # Skip obvious differences (tcin, name, price, images, etc.)
                    skip_fields = ['tcin', 'title', 'name', 'price', 'image', 'url', 'barcode', 'dpci', 'description', 'brand']
                    if not any(skip in key.lower() for skip in skip_fields):
                        different_fields.append((key, greyed_val, clickable_val))
        
        print(f"Found {len(different_fields)} potentially meaningful differences:")
        
        for key, greyed_val, clickable_val in different_fields[:20]:  # Show first 20
            print(f"\n  {key}:")
            print(f"    GREYED OUT: {greyed_val}")
            print(f"    CLICKABLE:  {clickable_val}")
        
        # Look specifically for boolean differences
        boolean_diffs = [(k, gv, cv) for k, gv, cv in different_fields 
                        if isinstance(gv, bool) or isinstance(cv, bool)]
        
        if boolean_diffs:
            print(f"\n🎯 BOOLEAN DIFFERENCES (most likely inventory flags):")
            for key, greyed_val, clickable_val in boolean_diffs:
                print(f"  {key}: GREYED={greyed_val}, CLICKABLE={clickable_val}")
        
    except Exception as e:
        print(f"Error in diff analysis: {e}")

def hypothesis_test_missing_field():
    """Test hypothesis: maybe inventory field only appears when out of stock"""
    print(f"\n💡 HYPOTHESIS: Missing field indicates out of stock")
    print("="*50)
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'GREYED OUT'),
        ('test_response_94734932.json', '94734932', 'CLICKABLE')
    ]
    
    all_fields = {}
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            # Get all field paths
            def get_all_paths(obj, path=""):
                paths = set()
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        current_path = f"{path}.{key}" if path else key
                        paths.add(current_path)
                        if isinstance(value, dict):
                            paths.update(get_all_paths(value, current_path))
                return paths
            
            all_fields[tcin] = get_all_paths(data)
            
        except Exception as e:
            print(f"Error with {filename}: {e}")
    
    # Find fields that exist in one but not the other
    greyed_fields = all_fields.get('94681776', set())
    clickable_fields = all_fields.get('94734932', set())
    
    only_in_greyed = greyed_fields - clickable_fields
    only_in_clickable = clickable_fields - greyed_fields
    
    if only_in_greyed:
        print(f"Fields ONLY in GREYED OUT preorder:")
        for field in sorted(only_in_greyed):
            print(f"  {field}")
    
    if only_in_clickable:
        print(f"Fields ONLY in CLICKABLE preorder:")
        for field in sorted(only_in_clickable):
            print(f"  {field}")
    
    if not only_in_greyed and not only_in_clickable:
        print("Both have identical field structure - difference must be in values")

if __name__ == "__main__":
    compare_preorder_vs_regular_inventory_systems()
    deep_diff_analysis()
    hypothesis_test_missing_field()
    
    print(f"\n🎯 SUMMARY:")
    print("If we find the pre-order inventory field, we can use the same")
    print("reliable approach as regular products for stock checking.")