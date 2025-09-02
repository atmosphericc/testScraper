#!/usr/bin/env python3
"""
Exhaustive API analysis for 89542109 (known to be in stock)
Search for ANY field that might indicate availability
"""
import sys
import asyncio
import aiohttp
import json
sys.path.insert(0, 'src')
from stock_checker import StockChecker

def search_nested_dict(data, search_terms, path=""):
    """Recursively search for terms in nested dictionary"""
    findings = []
    
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            
            # Check if key contains search terms
            key_lower = key.lower()
            for term in search_terms:
                if term in key_lower:
                    findings.append(f"KEY: {current_path} = {value}")
            
            # Check if value contains search terms (if string)
            if isinstance(value, str):
                value_lower = value.lower()
                for term in search_terms:
                    if term in value_lower:
                        findings.append(f"VALUE: {current_path} = '{value}'")
            
            # Recurse into nested structures
            findings.extend(search_nested_dict(value, search_terms, current_path))
            
    elif isinstance(data, list):
        for i, item in enumerate(data):
            current_path = f"{path}[{i}]"
            findings.extend(search_nested_dict(item, search_terms, current_path))
    
    return findings

async def exhaustive_analysis():
    """Exhaustive analysis of API response for availability indicators"""
    
    tcin = "89542109"  # Known to be available on website
    
    checker = StockChecker(use_website_checking=False)
    
    print(f"EXHAUSTIVE API ANALYSIS FOR {tcin}")
    print("=" * 50)
    print("Goal: Find ANY field indicating availability")
    print("=" * 50)
    
    async with aiohttp.ClientSession() as session:
        params = {
            'key': checker.api_key,
            'tcin': tcin,
            'store_id': checker.store_id,
            'pricing_store_id': checker.store_id,
            'has_pricing_store_id': 'true',
            'has_financing_options': 'true',
            'visitor_id': checker.generate_visitor_id(),
            'has_size_context': 'true'
        }
        
        async with session.get(checker.base_url, params=params, headers=checker.get_headers()) as response:
            if response.status == 200:
                data = await response.json()
                
                # Save full response for manual inspection
                with open(f'full_api_response_{tcin}.json', 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                
                print(f"Full response saved to: full_api_response_{tcin}.json\n")
                
                # Search for availability-related terms
                availability_terms = [
                    'available', 'stock', 'inventory', 'purchas', 'buy', 'cart', 
                    'ship', 'deliver', 'fulfil', 'order', 'pickup', 'store',
                    'limit', 'quantity', 'active', 'enable', 'disable',
                    'eligible', 'status', 'state', 'in_stock', 'out_of_stock'
                ]
                
                print("SEARCHING FOR AVAILABILITY INDICATORS:")
                print("-" * 40)
                
                findings = search_nested_dict(data, availability_terms)
                
                if findings:
                    for finding in findings[:20]:  # Show first 20 findings
                        print(f"  {finding}")
                    if len(findings) > 20:
                        print(f"  ... and {len(findings) - 20} more findings")
                else:
                    print("  No availability-related terms found")
                
                # Specific analysis of key sections
                product = data['data']['product']
                item = product['item']
                
                print(f"\nSPECIFIC SECTION ANALYSIS:")
                print("-" * 30)
                
                # Check if there are any boolean fields
                print("BOOLEAN FIELDS:")
                def find_booleans(obj, path=""):
                    bools = []
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            current_path = f"{path}.{k}" if path else k
                            if isinstance(v, bool):
                                bools.append(f"  {current_path}: {v}")
                            else:
                                bools.extend(find_booleans(v, current_path))
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj):
                            bools.extend(find_booleans(item, f"{path}[{i}]"))
                    return bools
                
                boolean_fields = find_booleans(data)
                for bf in boolean_fields[:15]:  # Show first 15
                    print(bf)
                if len(boolean_fields) > 15:
                    print(f"  ... and {len(boolean_fields) - 15} more boolean fields")
                
                # Check for numeric fields that might indicate stock levels
                print(f"\nNUMERIC FIELDS (potential stock indicators):")
                def find_numbers(obj, path=""):
                    nums = []
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            current_path = f"{path}.{k}" if path else k
                            if isinstance(v, (int, float)) and v != 0 and 'id' not in k.lower():
                                nums.append(f"  {current_path}: {v}")
                            else:
                                nums.extend(find_numbers(v, current_path))
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj):
                            nums.extend(find_numbers(item, f"{path}[{i}]"))
                    return nums
                
                numeric_fields = find_numbers(data)
                for nf in numeric_fields[:15]:  # Show first 15
                    print(nf)
                if len(numeric_fields) > 15:
                    print(f"  ... and {len(numeric_fields) - 15} more numeric fields")
                
            else:
                print(f"API request failed: {response.status}")

if __name__ == "__main__":
    asyncio.run(exhaustive_analysis())