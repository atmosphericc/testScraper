#!/usr/bin/env python3
"""
Quick batch test for the 4 specific TCINs
"""

import requests
import json

def test_batch_tcins():
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    # The 4 TCINs to test
    tcins = "94734932,94681776,94723520,94827553"
    
    params = {
        'key': api_key,
        'tcins': tcins,
        'store_id': store_id,
        'pricing_store_id': store_id,
        'has_pricing_context': 'true',
        'has_promotions': 'true',
        'is_bot': 'false',
    }
    
    print(f"🧪 TESTING BATCH: {tcins}")
    print("=" * 60)
    
    try:
        response = requests.get(fulfillment_url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if 'data' in data and 'product_summaries' in data['data']:
                summaries = data['data']['product_summaries']
                
                print(f"✅ Received {len(summaries)} products in batch response\n")
                
                for summary in summaries:
                    if 'fulfillment' in summary:
                        fulfillment = summary['fulfillment']
                        product_id = fulfillment.get('product_id')
                        
                        if product_id:
                            shipping_options = fulfillment.get('shipping_options', {})
                            availability_status = shipping_options.get('availability_status')
                            
                            if availability_status == 'PRE_ORDER_SELLABLE':
                                stock_status = "✅ IN STOCK (Available for preorder)"
                            elif availability_status == 'PRE_ORDER_UNSELLABLE':
                                stock_status = "❌ OUT OF STOCK (Preorder exhausted)"
                            else:
                                stock_status = f"❓ UNKNOWN STATUS: {availability_status}"
                            
                            print(f"📦 TCIN {product_id}: {stock_status}")
                
            else:
                print("❌ No product summaries found in response")
        
        else:
            print(f"❌ API call failed with status {response.status_code}")
            print(f"Response: {response.text[:200]}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_batch_tcins()