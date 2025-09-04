#!/usr/bin/env python3
"""
FINAL PREORDER INVENTORY PARSER
Based on Target Redsky API research and availability status fields

This creates a working parser that uses the proper availability status fields
discovered from Target's API documentation to detect preorder inventory exhaustion.
"""

import requests
import json
import time
from typing import Dict, List, Tuple, Optional

class PreorderInventoryParser:
    """
    Enhanced preorder parser that uses Target's actual availability status fields
    to determine if preorders are available or exhausted.
    """
    
    def __init__(self):
        self.api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
        self.store_id = "865"
        self.base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
        
    def get_headers(self):
        return {
            'accept': 'application/json',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'referer': 'https://www.target.com/',
        }
    
    def generate_visitor_id(self):
        return f"{int(time.time()*1000)}{'0'*10}"
    
    def get_product_data(self, tcin: str) -> Optional[dict]:
        """Get product data from Target API"""
        params = {
            'key': self.api_key,
            'tcin': tcin,
            'store_id': self.store_id,
            'pricing_store_id': self.store_id,
            'visitor_id': self.generate_visitor_id(),
        }
        
        try:
            response = requests.get(self.base_url, params=params, headers=self.get_headers(), timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"Error fetching data for {tcin}: {e}")
        
        return None
    
    def is_preorder(self, data: dict) -> bool:
        """Detect if item is a preorder"""
        try:
            item = data['data']['product']['item']
            
            # Preorders don't have eligibility_rules and have future street_date
            has_eligibility = 'eligibility_rules' in item
            street_date = item.get('mmbv_content', {}).get('street_date')
            
            return not has_eligibility and street_date is not None
        except Exception:
            return False
    
    def check_availability_status_fields(self, data: dict) -> Dict[str, any]:
        """
        Check for availability status fields found in Target API documentation:
        - availability_status (IN_STOCK, OUT_OF_STOCK)
        - loyalty_availability_status 
        - available_to_promise_quantity
        - reason_code (INVENTORY_UNAVAILABLE)
        - is_out_of_stock_in_all_store_locations
        """
        availability_info = {}
        
        try:
            product = data['data']['product']
            item = data['data']['product']['item']
            
            # Look for availability status fields
            def search_for_availability_fields(obj, path=""):
                found = {}
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        current_path = f"{path}.{key}" if path else key
                        
                        # Check for key availability fields
                        availability_fields = [
                            'availability_status',
                            'loyalty_availability_status', 
                            'available_to_promise_quantity',
                            'reason_code',
                            'is_out_of_stock_in_all_store_locations',
                            'location_available_to_promise_quantity'
                        ]
                        
                        if any(field in key.lower() for field in availability_fields):
                            found[current_path] = value
                            
                        # Also check for values indicating stock status
                        elif isinstance(value, str) and any(status in value.upper() for status in ['OUT_OF_STOCK', 'IN_STOCK', 'INVENTORY_UNAVAILABLE']):
                            found[f"{current_path}(value)"] = value
                            
                        # Recurse into nested objects
                        if isinstance(value, (dict, list)):
                            nested = search_for_availability_fields(value, current_path)
                            found.update(nested)
                            
                elif isinstance(obj, list):
                    for i, item in enumerate(obj):
                        current_path = f"{path}[{i}]" if path else f"[{i}]"
                        nested = search_for_availability_fields(item, current_path)
                        found.update(nested)
                        
                return found
            
            availability_info = search_for_availability_fields(data)
            
        except Exception as e:
            print(f"Error checking availability fields: {e}")
            
        return availability_info
    
    def check_preorder_availability(self, tcin: str) -> Tuple[bool, str, Dict[str, any]]:
        """
        Check if preorder is available for purchase.
        Returns: (is_available, reason, details)
        """
        data = self.get_product_data(tcin)
        if not data:
            return False, "API_ERROR", {}
        
        # Check if it's actually a preorder
        if not self.is_preorder(data):
            return False, "NOT_PREORDER", {}
        
        # Get basic preorder info
        item = data['data']['product']['item']
        purchase_limit = item.get('fulfillment', {}).get('purchase_limit', 0)
        street_date = item.get('mmbv_content', {}).get('street_date', 'Unknown')
        
        # Check for availability status fields
        availability_fields = self.check_availability_status_fields(data)
        
        details = {
            'tcin': tcin,
            'purchase_limit': purchase_limit,
            'street_date': street_date,
            'availability_fields': availability_fields
        }
        
        # Logic based on discovered API fields:
        # 1. First check for explicit availability status fields
        for field_path, value in availability_fields.items():
            if 'availability_status' in field_path.lower():
                if value == "OUT_OF_STOCK":
                    return False, "EXPLICIT_OUT_OF_STOCK", details
                elif value == "IN_STOCK":
                    return True, "EXPLICIT_IN_STOCK", details
            
            elif 'available_to_promise_quantity' in field_path.lower():
                if isinstance(value, (int, float)) and value <= 0:
                    return False, "ZERO_INVENTORY", details
                elif isinstance(value, (int, float)) and value > 0:
                    return True, "HAS_INVENTORY", details
            
            elif 'reason_code' in field_path.lower():
                if value == "INVENTORY_UNAVAILABLE":
                    return False, "INVENTORY_UNAVAILABLE", details
            
            elif 'is_out_of_stock' in field_path.lower():
                if value is True:
                    return False, "OUT_OF_STOCK_ALL_LOCATIONS", details
        
        # 2. Fallback to purchase_limit logic if no explicit status fields
        if purchase_limit > 0:
            return True, "PURCHASE_LIMIT_AVAILABLE", details
        else:
            return False, "NO_PURCHASE_LIMIT", details

def test_all_tcins():
    """Test the parser with all known TCINs"""
    parser = PreorderInventoryParser()
    
    test_cases = [
        ('94681776', 'EXPECTED: NOT AVAILABLE (greyed out button)'),
        ('94723520', 'EXPECTED: AVAILABLE (clickable button)'),
        ('94827553', 'EXPECTED: AVAILABLE (clickable button)'),
        ('94734932', 'EXPECTED: AVAILABLE (clickable button)')
    ]
    
    print("🔍 FINAL PREORDER INVENTORY PARSER TEST")
    print("Using discovered availability status fields from Target API")
    print("=" * 70)
    
    results = []
    
    for tcin, expected in test_cases:
        print(f"\n📦 TESTING {tcin} ({expected}):")
        
        is_available, reason, details = parser.check_preorder_availability(tcin)
        
        print(f"  Result: {'AVAILABLE' if is_available else 'NOT AVAILABLE'}")
        print(f"  Reason: {reason}")
        print(f"  Purchase Limit: {details.get('purchase_limit')}")
        print(f"  Street Date: {details.get('street_date')}")
        
        # Show discovered availability fields
        availability_fields = details.get('availability_fields', {})
        if availability_fields:
            print(f"  🎯 Found availability fields:")
            for field, value in availability_fields.items():
                print(f"    {field}: {value}")
        else:
            print(f"  ⚠️  No availability status fields found in API response")
        
        results.append((tcin, is_available, reason, details))
        
        time.sleep(0.5)  # Be nice to the API
    
    # Summary
    print(f"\n🎯 SUMMARY:")
    print("=" * 50)
    
    correct_predictions = 0
    for tcin, is_available, reason, details in results:
        # Expected results based on user feedback
        expected_available = tcin != '94681776'  # Only 94681776 should be unavailable
        
        if is_available == expected_available:
            status = "✅ CORRECT"
            correct_predictions += 1
        else:
            status = "❌ INCORRECT"
        
        print(f"{tcin}: {'AVAILABLE' if is_available else 'NOT AVAILABLE'} ({reason}) - {status}")
    
    accuracy = (correct_predictions / len(results)) * 100
    print(f"\nAccuracy: {accuracy:.1f}% ({correct_predictions}/{len(results)} correct)")
    
    if accuracy == 100:
        print("🎉 PERFECT! Parser correctly identifies all preorder availability!")
    elif accuracy >= 75:
        print("👍 GOOD! Parser works for most cases, minor adjustments may be needed.")
    else:
        print("⚠️  NEEDS WORK: Parser accuracy is too low, requires further investigation.")
    
    return results

def create_enhanced_parser_for_dashboard():
    """Create the enhanced parser function for dashboard integration"""
    print(f"\n📋 ENHANCED PARSER FOR DASHBOARD INTEGRATION:")
    print("=" * 60)
    
    enhanced_parser_code = '''
def is_available_preorder_enhanced(item_data, product_data):
    """
    Enhanced preorder availability detection using Target API availability status fields.
    
    Args:
        item_data: The item section from Target API response
        product_data: The full product section from Target API response
        
    Returns:
        bool: True if preorder is available, False if exhausted/unavailable
    """
    
    # First confirm this is actually a preorder
    has_eligibility = 'eligibility_rules' in item_data
    street_date = item_data.get('mmbv_content', {}).get('street_date')
    
    if has_eligibility or not street_date:
        # Not a preorder, use existing logic
        return False
    
    # Check for availability status fields (discovered from API research)
    def find_availability_status(obj, path=""):
        """Recursively search for availability status fields"""
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                
                # Check for explicit availability status
                if 'availability_status' in key.lower():
                    if value == "OUT_OF_STOCK":
                        return False, f"OUT_OF_STOCK via {current_path}"
                    elif value == "IN_STOCK":
                        return True, f"IN_STOCK via {current_path}"
                
                # Check for inventory quantity
                elif 'available_to_promise_quantity' in key.lower():
                    if isinstance(value, (int, float)):
                        if value <= 0:
                            return False, f"Zero inventory via {current_path}"
                        else:
                            return True, f"Has inventory ({value}) via {current_path}"
                
                # Check for reason codes
                elif 'reason_code' in key.lower():
                    if value == "INVENTORY_UNAVAILABLE":
                        return False, f"Inventory unavailable via {current_path}"
                
                # Check for out of stock flags
                elif 'is_out_of_stock' in key.lower():
                    if value is True:
                        return False, f"Out of stock flag via {current_path}"
                
                # Recurse into nested objects
                if isinstance(value, (dict, list)):
                    result, reason = find_availability_status(value, current_path)
                    if result is not None:
                        return result, reason
        
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]" if path else f"[{i}]"
                result, reason = find_availability_status(item, current_path)
                if result is not None:
                    return result, reason
        
        return None, "No availability fields found"
    
    # Search for availability status in both item and product data
    full_data = {'item': item_data, 'product': product_data}
    availability_result, reason = find_availability_status(full_data)
    
    if availability_result is not None:
        return availability_result
    
    # Fallback to purchase limit logic if no availability fields found
    purchase_limit = item_data.get('fulfillment', {}).get('purchase_limit', 0)
    return purchase_limit > 0
    '''
    
    print(enhanced_parser_code)
    
    # Save the enhanced parser to a file
    with open('/Users/Eric/Desktop/testScraper/enhanced_preorder_parser_for_dashboard.py', 'w') as f:
        f.write(f'''#!/usr/bin/env python3
"""
Enhanced Preorder Parser for Dashboard Integration
Generated from comprehensive API research and testing
"""

{enhanced_parser_code}

if __name__ == "__main__":
    # Test the enhanced parser
    print("Enhanced preorder parser created for dashboard integration")
    print("This function can be imported into the dashboard's stock checking logic")
''')
    
    print(f"\n💾 Enhanced parser saved to: enhanced_preorder_parser_for_dashboard.py")
    print(f"📋 Ready for dashboard integration after testing validates accuracy!")

if __name__ == "__main__":
    # Run the comprehensive test
    test_results = test_all_tcins()
    
    # Create the enhanced parser for dashboard integration  
    create_enhanced_parser_for_dashboard()
    
    print(f"\n🔧 NEXT STEPS:")
    print("1. Review test results above to confirm accuracy")
    print("2. If accuracy is good, integrate enhanced parser into dashboard")
    print("3. Test dashboard with preorder TCINs to validate functionality")
    print("4. Monitor for any edge cases or false positives/negatives")