#!/usr/bin/env python3
"""
Quick script to check API price data for all configured products
"""

import json
import requests
import random
from pathlib import Path

def get_massive_user_agent_rotation():
    """Get random user agent"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]
    return random.choice(user_agents)

def get_config():
    """Load product configuration"""
    config_path = Path("config/product_config.json")
    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)
    return {"products": []}

def check_api_prices():
    """Check API prices for all products"""
    config = get_config()
    products = config.get('products', [])
    
    if not products:
        print("No products found in config")
        return
    
    # API endpoint
    batch_endpoint = 'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1'
    
    # Get all TCINs
    tcins = [p['tcin'] for p in products]
    
    # API parameters - Adding all discovered pricing parameters
    api_key = "ff457966e64d5e877fdbad070f276d18ecec4a01"
    params = {
        'key': api_key,
        'tcins': ','.join(tcins),
        'store_id': '1859',
        'pricing_store_id': '1859',
        'has_pricing_store_id': 'true',
        'has_pricing_context': 'true',
        'pricing_context': 'digital',
        'has_promotions': 'true',
        'is_bot': 'false',
        'channel': 'WEB',
        'visitor_id': 'web',
        'platform': 'desktop'
    }
    
    # Headers
    headers = {
        'accept': 'application/json',
        'user-agent': get_massive_user_agent_rotation(),
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'referer': 'https://www.target.com/',
    }
    
    print("Checking API prices for all products...")
    print("=" * 80)
    
    try:
        response = requests.get(batch_endpoint, params=params, headers=headers, timeout=15)
        
        if response.status_code != 200:
            print(f"API request failed: HTTP {response.status_code}")
            return
        
        data = response.json()
        
        if 'data' not in data or 'product_summaries' not in data['data']:
            print("Invalid API response structure")
            print(f"Response keys: {list(data.keys())}")
            return
        
        product_summaries = data['data']['product_summaries']
        print(f"Found {len(product_summaries)} products in API response")
        print("=" * 80)
        
        for product_summary in product_summaries:
            tcin = product_summary.get('tcin')
            if not tcin:
                continue
                
            # Find config product for max price reference
            config_product = next((p for p in products if p['tcin'] == tcin), None)
            max_price = config_product.get('max_price', 'N/A') if config_product else 'N/A'
            
            # Get product name
            item = product_summary.get('item', {})
            product_desc = item.get('product_description', {})
            name = product_desc.get('title', 'Unknown Product')[:60]
            
            print(f"TCIN: {tcin}")
            print(f"   Name: {name}")
            print(f"   Max Price (Config): ${max_price}")
            print()
            
            # Examine all price-related fields
            print("   PRICE DATA ANALYSIS:")
            
            # Top-level price object
            if 'price' in product_summary:
                price_obj = product_summary['price']
                print(f"   Top-level 'price' object:")
                for key, value in price_obj.items():
                    print(f"      {key}: {value}")
                print()
            else:
                print("   No top-level 'price' object")
            
            # Item-level pricing
            if 'pricing' in item:
                pricing_obj = item['pricing']
                print(f"   Item-level 'pricing' object:")
                for key, value in pricing_obj.items():
                    print(f"      {key}: {value}")
                print()
            else:
                print("   No item-level 'pricing' object")
            
            # Look for seller/fulfillment indicators
            print("   SELLER/FULFILLMENT ANALYSIS:")
            
            # Check fulfillment data for seller information
            fulfillment = product_summary.get('fulfillment', {})
            if fulfillment:
                print("   Fulfillment data:")
                for key, value in fulfillment.items():
                    if any(keyword in key.lower() for keyword in ['seller', 'vendor', 'partner', 'marketplace', 'third_party', 'ship', 'sold_by']):
                        print(f"      {key}: {value}")
                
                # Check shipping options specifically
                shipping = fulfillment.get('shipping_options', {})
                if shipping:
                    print("   Shipping options:")
                    for key, value in shipping.items():
                        print(f"      {key}: {value}")
            
            # Check item enrichment data
            enrichment = item.get('enrichment', {})
            if enrichment:
                print("   Enrichment data:")
                for key, value in enrichment.items():
                    if any(keyword in key.lower() for keyword in ['seller', 'vendor', 'partner', 'marketplace', 'sold_by', 'ship']):
                        print(f"      {key}: {value}")
            
            # Focus on relationship_type_code which seems important
            print("   KEY SELLER INDICATORS:")
            print(f"      relationship_type_code: {item.get('relationship_type_code', 'NOT FOUND')}")
            
            # Check merchandise classification
            merch_class = item.get('merchandise_classification', {})
            if merch_class:
                print("   Merchandise Classification:")
                for key, value in merch_class.items():
                    print(f"      {key}: {value}")
            
            # Check product classification  
            prod_class = item.get('product_classification', {})
            if prod_class:
                print("   Product Classification:")
                for key, value in prod_class.items():
                    print(f"      {key}: {value}")
            
            print("   " + "-" * 60)
            print()
            
    except Exception as e:
        print(f"Error checking API prices: {e}")

if __name__ == "__main__":
    check_api_prices()