#!/usr/bin/env python3
"""
Make live batch API call with no caching - get real-time stock data
"""

import requests
import json
import time

def make_live_batch_call():
    """Make live batch API call with cache-busting headers"""
    
    tcins = ["89542109", "94724987", "94681785", "94681770", "94336414"]
    
    # Batch endpoint that works
    url = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
    
    params = {
        'key': 'ff457966e64d5e877fdbad070f276d18ecec4a01',
        'tcins': ','.join(tcins),
        'store_id': '1859',
        'pricing_store_id': '1859',
        'zip': '33809',
        'state': 'FL',
        'latitude': '28.0395',
        'longitude': '-81.9498',
        'is_bot': 'false',
        # Cache busting
        '_': str(int(time.time() * 1000))  # Timestamp
    }
    
    # Headers to prevent caching
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://www.target.com/',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }
    
    current_time = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    
    print("🔴 LIVE BATCH API CALL - NO CACHING")
    print("="*80)
    print(f"⏰ Timestamp: {current_time}")
    print(f"🎯 TCINs: {', '.join(tcins)}")
    print(f"🌐 Endpoint: product_summary_with_fulfillment_v1")
    print(f"📍 Location: Lakeland, FL (Store 1859)")
    print("🚫 Cache-Control: no-cache, no-store, must-revalidate")
    print("="*80)
    
    try:
        start_time = time.time()
        response = requests.get(url, params=params, headers=headers, timeout=20)
        end_time = time.time()
        
        response_time = (end_time - start_time) * 1000  # Convert to ms
        
        print(f"📡 Response Time: {response_time:.0f}ms")
        print(f"📊 Status Code: {response.status_code}")
        print(f"📄 Content Length: {len(response.text):,} characters")
        print(f"🔗 Full URL: {response.url}")
        
        if response.status_code == 200:
            print("✅ SUCCESS - LIVE DATA RECEIVED")
            
            data = response.json()
            
            print(f"\n📋 RAW API RESPONSE (LIVE - {current_time}):")
            print("="*80)
            print(json.dumps(data, indent=2))
            print("="*80)
            
            # Parse live stock data
            print(f"\n📊 LIVE STOCK STATUS:")
            print("="*60)
            
            if 'data' in data and 'product_summaries' in data['data']:
                products = data['data']['product_summaries']
                
                print(f"{'TCIN':<12} {'Status':<15} {'Product':<50}")
                print("-" * 85)
                
                for product in products:
                    tcin = product.get('tcin', 'Unknown')
                    
                    # Get product name
                    title = "Unknown Product"
                    if 'item' in product and 'product_description' in product['item']:
                        title = product['item']['product_description'].get('title', 'Unknown Product')
                        # Decode HTML entities
                        title = title.replace('&#233;', 'é').replace('&#38;', '&').replace('&#8212;', '—')
                    
                    # Get stock status
                    fulfillment = product.get('fulfillment', {})
                    shipping = fulfillment.get('shipping_options', {})
                    status = shipping.get('availability_status', 'UNKNOWN')
                    
                    # Status icon
                    if status == 'IN_STOCK':
                        status_display = "✅ IN_STOCK"
                    elif status == 'OUT_OF_STOCK':
                        status_display = "❌ OUT_OF_STOCK"  
                    elif status == 'DISCONTINUED':
                        status_display = "🚫 DISCONTINUED"
                    else:
                        status_display = f"❓ {status}"
                    
                    print(f"{tcin:<12} {status_display:<15} {title[:47]}")
                
                print("-" * 85)
                
                # Summary
                in_stock_count = sum(1 for p in products 
                                   if p.get('fulfillment', {}).get('shipping_options', {}).get('availability_status') == 'IN_STOCK')
                
                print(f"\n📈 SUMMARY (Live as of {current_time}):")
                print(f"   🎯 Total Products: {len(products)}")
                print(f"   ✅ In Stock: {in_stock_count}")
                print(f"   ❌ Out of Stock: {len(products) - in_stock_count}")
                print(f"   ⚡ Response Time: {response_time:.0f}ms")
                
            else:
                print("❌ Unexpected response format")
                print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            
        else:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            
    except requests.exceptions.Timeout:
        print("⏰ REQUEST TIMEOUT")
    except requests.exceptions.RequestException as e:
        print(f"❌ REQUEST ERROR: {e}")
    except json.JSONDecodeError as e:
        print(f"❌ JSON DECODE ERROR: {e}")
        print(f"Raw response: {response.text[:500]}...")
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}")

if __name__ == '__main__':
    make_live_batch_call()