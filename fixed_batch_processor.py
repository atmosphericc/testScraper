#!/usr/bin/env python3
"""
Fixed batch response processor for the Target RedSky API
"""

import json
import html
from datetime import datetime

def process_batch_response_fixed(data, enabled_products):
    """
    FIXED version of the batch response processor that correctly handles the new API structure
    """
    if not data or 'data' not in data or 'product_summaries' not in data['data']:
        print("[ERROR] Invalid batch response structure")
        print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
        return {}
        
    product_summaries = data['data']['product_summaries']
    processed_data = {}
    
    print(f"[DATA] Processing {len(product_summaries)} products from batch response...")
    
    for product_summary in product_summaries:
        try:
            # Extract TCIN
            tcin = product_summary.get('tcin')
            if not tcin:
                print("[WARN] Product summary missing TCIN, skipping")
                continue
            
            print(f"\n[PROCESSING] TCIN: {tcin}")
            
            # Extract item section
            item = product_summary.get('item', {})
            if not item:
                print(f"[WARN] {tcin}: No item section found")
                continue
            
            # Extract fulfillment section  
            fulfillment = product_summary.get('fulfillment', {})
            if not fulfillment:
                print(f"[WARN] {tcin}: No fulfillment section found")
                continue
            
            # Extract product name from the correct location
            product_desc = item.get('product_description', {})
            raw_name = product_desc.get('title', 'Unknown Product')
            clean_name = html.unescape(raw_name)  # Decode HTML entities like &#233;
            
            print(f"[NAME] {tcin}: {clean_name}")
            
            # Extract shipping options for availability
            shipping = fulfillment.get('shipping_options', {})
            availability_status = shipping.get('availability_status', 'UNKNOWN')
            
            print(f"[STATUS] {tcin}: {availability_status}")
            
            # Check if it's a marketplace seller
            relationship_code = item.get('relationship_type_code', 'UNKNOWN')
            is_target_direct = relationship_code == 'SA'  # SA = Sold and shipped by Target
            is_marketplace = not is_target_direct
            
            print(f"[SELLER] {tcin}: {'Target Direct' if is_target_direct else 'Marketplace'} (code: {relationship_code})")
            
            # Detect if this is a preorder
            is_preorder = 'PRE_ORDER' in availability_status
            
            # Determine availability based on product type and seller
            if is_preorder:
                # For preorders, use PRE_ORDER_SELLABLE/UNSELLABLE logic
                base_available = availability_status == 'PRE_ORDER_SELLABLE'
                print(f"[PREORDER] {tcin}: {availability_status} -> {'AVAILABLE' if base_available else 'UNAVAILABLE'}")
            else:
                # For regular products, use IN_STOCK logic
                base_available = availability_status == 'IN_STOCK'
                print(f"[REGULAR] {tcin}: {availability_status} -> {'AVAILABLE' if base_available else 'UNAVAILABLE'}")
            
            # Final availability = base availability AND Target direct seller
            is_available = base_available and is_target_direct
            
            # Determine status message
            if not is_target_direct:
                normalized_status = 'MARKETPLACE_SELLER'
            elif not base_available:
                normalized_status = 'OUT_OF_STOCK'
            else:
                normalized_status = 'IN_STOCK'
            
            print(f"[FINAL] {tcin}: Available={is_available}, Status={normalized_status}")
            
            # Get additional info
            street_date = None
            if is_preorder:
                # Try to get street date from various locations
                mmbv_content = item.get('mmbv_content', {})
                street_date = mmbv_content.get('street_date')
            
            buy_url = item.get('enrichment', {}).get('buy_url', f"https://www.target.com/p/-/A-{tcin}")
            store_available = not fulfillment.get('is_out_of_stock_in_all_store_locations', True)
            
            # Build result
            result = {
                'tcin': tcin,
                'name': clean_name,
                'available': is_available,
                'status': normalized_status,
                'last_checked': datetime.now().isoformat(),
                'quantity': 1 if is_available else 0,
                'availability_status': availability_status,
                'sold_out': not is_available,
                'response_time': 0,  # Will be filled by caller
                'confidence': 'high',
                'method': 'ultimate_stealth_batch_fixed',
                'url': buy_url,
                
                # Store availability
                'store_available': store_available,
                
                # Seller verification
                'is_target_direct': is_target_direct,
                'is_marketplace': is_marketplace,
                'seller_code': relationship_code,
                
                # Preorder-specific fields
                'is_preorder': is_preorder,
                'street_date': street_date,
                'preorder_status': availability_status if is_preorder else None,
                
                # Additional tracking
                'stealth_applied': True,
                'batch_api': True,
                'has_data': True
            }
            
            processed_data[tcin] = result
            
        except Exception as e:
            print(f"[ERROR] Failed to process product summary: {e}")
            continue
    
    print(f"\n[SUMMARY] Successfully processed {len(processed_data)} products")
    in_stock_count = sum(1 for r in processed_data.values() if r.get('available'))
    print(f"[SUMMARY] {in_stock_count} products in stock")
    
    return processed_data

def test_fixed_processor():
    """Test the fixed processor with real API data"""
    try:
        # Load the API response we saved earlier
        with open('api_response_debug.json', 'r') as f:
            api_data = json.load(f)
        
        # Load config to get enabled products
        with open('config/product_config.json', 'r') as f:
            config = json.load(f)
        
        enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
        
        print("="*60)
        print("TESTING FIXED BATCH PROCESSOR")
        print("="*60)
        
        # Test the fixed processor
        result = process_batch_response_fixed(api_data, enabled_products)
        
        print("\n" + "="*60)
        print("FINAL RESULTS")
        print("="*60)
        
        for tcin, product in result.items():
            print(f"\nTCIN: {tcin}")
            print(f"  Name: {product['name']}")
            print(f"  Available: {product['available']}")
            print(f"  Status: {product['status']}")
            print(f"  Is Target Direct: {product['is_target_direct']}")
            print(f"  Is Preorder: {product['is_preorder']}")
            if product['is_preorder']:
                print(f"  Preorder Status: {product['preorder_status']}")
            print(f"  URL: {product['url']}")
        
        return result
        
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        return {}

if __name__ == "__main__":
    test_fixed_processor()