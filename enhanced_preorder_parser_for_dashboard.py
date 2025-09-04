#!/usr/bin/env python3
"""
Enhanced Preorder Parser for Dashboard Integration
Generated from comprehensive API research and testing
"""


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
    

if __name__ == "__main__":
    # Test the enhanced parser
    print("Enhanced preorder parser created for dashboard integration")
    print("This function can be imported into the dashboard's stock checking logic")
