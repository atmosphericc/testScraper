#!/usr/bin/env python3
"""
Test batch API with the new set of TCINs including some we haven't seen before
"""

import requests
import json

def test_new_batch_tcins():
    api_key = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    store_id = "865"
    fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
    
    headers = {
        'accept': 'application/json',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://www.target.com/',
    }
    
    # The 4 TCINs to test (including 2 new ones)
    tcins = "94734932,94681776,94336414,89542109"
    tcin_list = tcins.split(',')
    
    params = {
        'key': api_key,
        'tcins': tcins,
        'store_id': store_id,
        'pricing_store_id': store_id,
        'has_pricing_context': 'true',
        'has_promotions': 'true',
        'is_bot': 'false',
    }
    
    print(f"🧪 TESTING NEW BATCH: {tcins}")
    print("=" * 70)
    
    try:
        response = requests.get(fulfillment_url, params=params, headers=headers, timeout=15)
        
        print(f"API Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            # Save response for analysis
            with open('new_batch_response.json', 'w') as f:
                json.dump(data, f, indent=2)
            print(f"💾 Saved response to: new_batch_response.json")
            
            if 'data' in data and 'product_summaries' in data['data']:
                summaries = data['data']['product_summaries']
                
                print(f"✅ Received {len(summaries)} products in batch response\n")
                
                results = {}
                
                for summary in summaries:
                    if 'fulfillment' in summary:
                        fulfillment = summary['fulfillment']
                        product_id = fulfillment.get('product_id')
                        
                        if product_id:
                            shipping_options = fulfillment.get('shipping_options', {})
                            availability_status = shipping_options.get('availability_status')
                            
                            # Determine stock status
                            if availability_status == 'PRE_ORDER_SELLABLE':
                                stock_status = "✅ IN STOCK (Available for preorder)"
                                is_available = True
                            elif availability_status == 'PRE_ORDER_UNSELLABLE':
                                stock_status = "❌ OUT OF STOCK (Preorder exhausted)"
                                is_available = False
                            elif availability_status in ['IN_STOCK', 'AVAILABLE']:
                                stock_status = "✅ IN STOCK (Regular product)"
                                is_available = True
                            elif availability_status in ['OUT_OF_STOCK', 'UNAVAILABLE']:
                                stock_status = "❌ OUT OF STOCK (Regular product)"
                                is_available = False
                            else:
                                stock_status = f"❓ UNKNOWN STATUS: {availability_status}"
                                is_available = False
                            
                            results[product_id] = {
                                'availability_status': availability_status,
                                'is_available': is_available,
                                'stock_status': stock_status
                            }
                            
                            print(f"📦 TCIN {product_id}: {stock_status}")
                            print(f"   Raw status: {availability_status}")
                
                # Check if all requested TCINs were found
                print(f"\n🔍 COVERAGE CHECK:")
                for tcin in tcin_list:
                    if tcin in results:
                        print(f"  {tcin}: ✅ Found")
                    else:
                        print(f"  {tcin}: ❌ Not found in response")
                
                # Summary
                available_count = sum(1 for r in results.values() if r['is_available'])
                total_found = len(results)
                
                print(f"\n📊 SUMMARY:")
                print(f"  Total requested: {len(tcin_list)}")
                print(f"  Total found: {total_found}")
                print(f"  Available: {available_count}")
                print(f"  Unavailable: {total_found - available_count}")
                
            else:
                print("❌ No product summaries found in response")
                print(f"Response keys: {list(data.keys()) if data else 'No data'}")
        
        else:
            print(f"❌ API call failed with status {response.status_code}")
            print(f"Response: {response.text[:300]}")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_new_batch_tcins()