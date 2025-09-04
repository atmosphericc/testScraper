#!/usr/bin/env python3
"""
Compare all 4 TCINs to find the REAL availability pattern
Since free_shipping logic failed, let's find what actually differentiates them
"""

import json
from datetime import datetime, date

def compare_all_four_preorders():
    """Deep comparison of all 4 preorder TCINs to find the real pattern"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'NOT AVAILABLE (user confirmed)'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'AVAILABLE (user confirmed)'),  
        ('tcin_response_94827553.json', '94827553', 'AVAILABLE (user confirmed)'),
        ('test_response_94734932.json', '94734932', 'AVAILABLE (user confirmed)')
    ]
    
    print("🔍 COMPARING ALL 4 PREORDER TCINs")
    print("Looking for the REAL availability pattern")
    print("="*70)
    
    all_data = {}
    
    # Load all data
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
                all_data[tcin] = {
                    'status': status,
                    'data': data,
                    'item': data['data']['product']['item'],
                    'product': data['data']['product']
                }
        except Exception as e:
            print(f"❌ Error loading {filename}: {e}")
    
    print(f"\n📊 BASIC INFO COMPARISON:")
    for tcin, info in all_data.items():
        item = info['item']
        product = info['product']
        
        name = item.get('product_description', {}).get('title', 'Unknown')
        price = product.get('price', {}).get('current_retail', 0)
        street_date = item.get('mmbv_content', {}).get('street_date')
        purchase_limit = item.get('fulfillment', {}).get('purchase_limit', 0)
        free_shipping = product.get('free_shipping', {}).get('enabled', False)
        
        print(f"\n  {tcin} ({info['status']}):")
        print(f"    Name: {name[:50]}")
        print(f"    Price: ${price}")
        print(f"    Street Date: {street_date}")
        print(f"    Purchase Limit: {purchase_limit}")
        print(f"    Free Shipping: {free_shipping}")
    
    # Look for fields that exist in some but not others
    print(f"\n🔍 FIELD PRESENCE ANALYSIS:")
    all_field_sets = {}
    
    for tcin, info in all_data.items():
        item = info['item']
        all_field_sets[tcin] = set(item.keys())
    
    # Find fields that are present in different combinations
    all_possible_fields = set()
    for field_set in all_field_sets.values():
        all_possible_fields.update(field_set)
    
    # Show field presence matrix
    print(f"\nField presence matrix (✅ = present, ❌ = absent):")
    not_available_tcin = '94681776'
    available_tcins = ['94723520', '94827553', '94734932']
    
    fields_only_in_not_available = set()
    fields_only_in_available = set()
    
    for field in sorted(all_possible_fields):
        presence = []
        for tcin in [not_available_tcin] + available_tcins:
            has_field = field in all_field_sets[tcin]
            symbol = "✅" if has_field else "❌"
            presence.append(f"{tcin}: {symbol}")
        
        # Check if field distinguishes available from not available
        not_avail_has = field in all_field_sets[not_available_tcin]
        avail_have = [field in all_field_sets[tcin] for tcin in available_tcins]
        
        if not_avail_has and not any(avail_have):
            fields_only_in_not_available.add(field)
            print(f"  🎯 {field}: {' | '.join(presence)} <- ONLY in NOT AVAILABLE")
        elif not not_avail_has and all(avail_have):
            fields_only_in_available.add(field)
            print(f"  🎯 {field}: {' | '.join(presence)} <- ONLY in AVAILABLE")
        elif not_avail_has != any(avail_have):
            print(f"  ⚠️  {field}: {' | '.join(presence)} <- MIXED PATTERN")
    
    print(f"\n🎯 POTENTIAL DISTINGUISHING FIELDS:")
    if fields_only_in_not_available:
        print(f"Fields ONLY in NOT AVAILABLE preorder: {sorted(fields_only_in_not_available)}")
        
        # Show values of these fields
        for field in sorted(fields_only_in_not_available):
            value = all_data[not_available_tcin]['item'].get(field)
            print(f"  {field}: {value}")
    
    if fields_only_in_available:
        print(f"Fields ONLY in AVAILABLE preorders: {sorted(fields_only_in_available)}")
        
        # Show values of these fields for available ones
        for field in sorted(fields_only_in_available):
            print(f"  {field}:")
            for tcin in available_tcins:
                if field in all_data[tcin]['item']:
                    value = all_data[tcin]['item'][field]
                    print(f"    {tcin}: {value}")
    
    # Also check for value differences in common fields
    print(f"\n🔍 VALUE DIFFERENCES IN COMMON FIELDS:")
    common_fields = set(all_field_sets[not_available_tcin])
    for tcin in available_tcins:
        common_fields &= all_field_sets[tcin]
    
    for field in sorted(common_fields):
        values = {}
        for tcin, info in all_data.items():
            values[tcin] = info['item'].get(field)
        
        # Check if NOT AVAILABLE has different value than AVAILABLE ones
        not_avail_value = values[not_available_tcin]
        avail_values = [values[tcin] for tcin in available_tcins]
        
        if not all(v == not_avail_value for v in avail_values):
            print(f"\n  🎯 {field} - VALUES DIFFER:")
            print(f"    NOT AVAILABLE ({not_available_tcin}): {not_avail_value}")
            for tcin in available_tcins:
                print(f"    AVAILABLE ({tcin}): {values[tcin]}")

def check_product_level_differences():
    """Check product-level fields (not just item-level)"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'NOT AVAILABLE'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'AVAILABLE'),  
        ('tcin_response_94827553.json', '94827553', 'AVAILABLE'),
        ('test_response_94734932.json', '94734932', 'AVAILABLE')
    ]
    
    print(f"\n🔍 PRODUCT-LEVEL FIELD ANALYSIS:")
    print("="*70)
    
    product_data = {}
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
                product_data[tcin] = {
                    'status': status,
                    'product': data['data']['product']
                }
        except Exception as e:
            print(f"Error loading {filename}: {e}")
            continue
    
    # Check product-level field presence
    not_available_tcin = '94681776'
    available_tcins = ['94723520', '94827553', '94734932']
    
    print(f"Product-level field differences:")
    
    # Get all product-level fields
    all_product_fields = set()
    product_field_sets = {}
    
    for tcin, info in product_data.items():
        product = info['product']
        fields = set()
        
        def get_all_keys(obj, prefix=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    current_key = f"{prefix}.{key}" if prefix else key
                    fields.add(current_key)
                    if isinstance(value, dict):
                        get_all_keys(value, current_key)
        
        get_all_keys(product)
        product_field_sets[tcin] = fields
        all_product_fields.update(fields)
    
    # Find distinguishing product-level fields
    for field in sorted(all_product_fields):
        not_avail_has = field in product_field_sets[not_available_tcin]
        avail_have = [field in product_field_sets[tcin] for tcin in available_tcins]
        
        if not_avail_has and not any(avail_have):
            print(f"  🎯 ONLY in NOT AVAILABLE: {field}")
        elif not not_avail_has and all(avail_have):
            print(f"  🎯 ONLY in AVAILABLE: {field}")

if __name__ == "__main__":
    compare_all_four_preorders()
    check_product_level_differences()
    
    print(f"\n🎯 NEXT STEPS:")
    print("Look for the field(s) that clearly distinguish:")
    print("- NOT AVAILABLE: 94681776")  
    print("- AVAILABLE: 94723520, 94827553, 94734932")
    print("The real availability indicator should be consistent across all 4 cases.")