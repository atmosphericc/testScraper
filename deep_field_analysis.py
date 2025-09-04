#!/usr/bin/env python3
"""
Deep dive into every single field in the API responses to find the hidden availability logic
Maybe I missed something subtle in the nested data structures
"""

import json
from datetime import datetime

def deep_field_search(obj, path="", target_words=None):
    """Recursively search for fields containing target words"""
    if target_words is None:
        target_words = ['available', 'eligible', 'active', 'purchas', 'buy', 'add', 'cart', 'order', 'stock', 'inventory']
    
    found_fields = []
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key
            
            # Check if key contains any target words
            if any(word in key.lower() for word in target_words):
                found_fields.append((current_path, key, value))
            
            # Check if value is a string containing target words
            if isinstance(value, str) and any(word in value.lower() for word in target_words):
                found_fields.append((current_path, f"{key}(string_value)", value))
            
            # Recurse into nested structures
            if isinstance(value, (dict, list)):
                found_fields.extend(deep_field_search(value, current_path, target_words))
                
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            current_path = f"{path}[{i}]" if path else f"[{i}]"
            found_fields.extend(deep_field_search(item, current_path, target_words))
    
    return found_fields

def compare_availability_fields():
    """Deep comparison of all availability-related fields across the 3 TCINs"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'NOT AVAILABLE'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'AVAILABLE'),  
        ('tcin_response_94827553.json', '94827553', 'AVAILABLE')
    ]
    
    print("🔍 DEEP FIELD ANALYSIS FOR AVAILABILITY PATTERNS")
    print("="*70)
    
    all_availability_fields = {}
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            print(f"\\n📦 ANALYZING {tcin} ({status}):")
            
            # Search the entire response for availability-related fields
            availability_fields = deep_field_search(data)
            
            print(f"  Found {len(availability_fields)} potential availability fields:")
            
            for path, key, value in availability_fields:
                print(f"    {path}: {value}")
                
                # Store for comparison
                if path not in all_availability_fields:
                    all_availability_fields[path] = {}
                all_availability_fields[path][tcin] = value
            
        except Exception as e:
            print(f"  ❌ Error analyzing {filename}: {e}")
    
    print(f"\\n🎯 FIELD COMPARISON ACROSS ALL TCINs:")
    print("="*70)
    
    # Compare fields that exist in multiple TCINs
    for field_path, tcin_values in all_availability_fields.items():
        if len(tcin_values) > 1:  # Field exists in multiple TCINs
            print(f"\\n{field_path}:")
            
            # Check if values are different (potential discriminator)
            values = list(tcin_values.values())
            if len(set(str(v) for v in values)) > 1:
                print("  🎯 VALUES DIFFER (potential availability indicator):")
                for tcin, value in tcin_values.items():
                    status = "NOT AVAILABLE" if tcin == "94681776" else "AVAILABLE"
                    print(f"    {tcin} ({status}): {value}")
            else:
                print(f"  Values identical: {values[0]}")

def analyze_boolean_and_numerical_fields():
    """Look specifically at boolean/numerical fields that might indicate availability"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'NOT AVAILABLE'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'AVAILABLE'),  
        ('tcin_response_94827553.json', '94827553', 'AVAILABLE')
    ]
    
    print(f"\\n🔢 BOOLEAN/NUMERICAL FIELD ANALYSIS:")
    print("="*70)
    
    def extract_bool_num_fields(obj, path=""):
        """Extract all boolean and numerical fields"""
        fields = {}
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                
                if isinstance(value, bool):
                    fields[current_path] = value
                elif isinstance(value, (int, float)) and not isinstance(value, str):
                    fields[current_path] = value
                elif isinstance(value, dict):
                    fields.update(extract_bool_num_fields(value, current_path))
                elif isinstance(value, list) and value and isinstance(value[0], dict):
                    fields.update(extract_bool_num_fields(value[0], f"{current_path}[0]"))
        
        return fields
    
    all_bool_num_fields = {}
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            bool_num_fields = extract_bool_num_fields(data)
            
            for field_path, value in bool_num_fields.items():
                if field_path not in all_bool_num_fields:
                    all_bool_num_fields[field_path] = {}
                all_bool_num_fields[field_path][tcin] = value
        
        except Exception as e:
            print(f"Error with {filename}: {e}")
    
    # Show fields that have different values (potential discriminators)
    different_fields = []
    for field_path, tcin_values in all_bool_num_fields.items():
        if len(tcin_values) >= 2:  # Field exists in at least 2 TCINs
            values = list(tcin_values.values())
            if len(set(str(v) for v in values)) > 1:  # Values differ
                different_fields.append((field_path, tcin_values))
    
    print(f"Found {len(different_fields)} boolean/numerical fields with different values:")
    
    for field_path, tcin_values in different_fields:
        print(f"\\n{field_path}:")
        for tcin, value in tcin_values.items():
            status = "NOT AVAILABLE" if tcin == "94681776" else "AVAILABLE"
            print(f"  {tcin} ({status}): {value}")

def find_date_time_fields():
    """Look for any date/time fields that might indicate availability windows"""
    
    files = [
        ('preorder_response_94681776_Out_of_Stock_Pre-order.json', '94681776', 'NOT AVAILABLE'),
        ('preorder_response_94723520_In_Stock_Pre-order.json', '94723520', 'AVAILABLE'),  
        ('tcin_response_94827553.json', '94827553', 'AVAILABLE')
    ]
    
    print(f"\\n📅 DATE/TIME FIELD ANALYSIS:")
    print("="*70)
    
    def extract_date_fields(obj, path=""):
        """Extract fields that look like dates/times"""
        date_fields = {}
        
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                
                # Check for date-like keys
                if any(date_word in key.lower() for date_word in ['date', 'time', 'when', 'start', 'end', 'expire', 'valid']):
                    date_fields[current_path] = value
                
                # Check for ISO date strings
                elif isinstance(value, str) and len(value) > 8:
                    if '-' in value and any(char.isdigit() for char in value):
                        # Looks like a date
                        date_fields[current_path] = value
                
                elif isinstance(value, dict):
                    date_fields.update(extract_date_fields(value, current_path))
        
        return date_fields
    
    for filename, tcin, status in files:
        try:
            with open(filename) as f:
                data = json.load(f)
            
            date_fields = extract_date_fields(data)
            
            print(f"\\n{tcin} ({status}) - Date/Time fields:")
            for field_path, value in date_fields.items():
                print(f"  {field_path}: {value}")
                
        except Exception as e:
            print(f"Error with {filename}: {e}")

if __name__ == "__main__":
    compare_availability_fields()
    analyze_boolean_and_numerical_fields() 
    find_date_time_fields()
    
    print(f"\\n🎯 RESEARCH SUMMARY:")
    print("If any field shows a clear pattern between AVAILABLE vs NOT AVAILABLE,")
    print("that could be the key to determining preorder purchasability!")