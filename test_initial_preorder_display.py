#!/usr/bin/env python3
"""
Test the initial preorder display fix
"""

import requests
import json

def test_initial_api_response():
    """Test what the initial API endpoint returns"""
    
    print("🧪 TESTING INITIAL API RESPONSE")
    print("=" * 50)
    
    try:
        response = requests.get('http://localhost:5001/api/initial-stock-check')
        
        if response.status_code == 200:
            data = response.json()
            
            print("✅ API Response received")
            print(f"Success: {data.get('success')}")
            print(f"Products count: {len(data.get('products', []))}")
            
            if data.get('products'):
                print("\n📦 Product Details:")
                for product in data['products']:
                    tcin = product.get('tcin')
                    is_preorder = product.get('is_preorder')
                    status = product.get('status')
                    name = product.get('name', '').split(' ')[0:3]
                    
                    product_type = "🎯 PREORDER" if is_preorder else "📦 REGULAR"
                    
                    print(f"  {tcin}: {product_type} - {status}")
                    print(f"    Name: {' '.join(name)}...")
                    print(f"    has is_preorder field: {is_preorder is not None}")
                    print(f"    has street_date field: {product.get('street_date') is not None}")
                    print()
                    
                # Check if preorder fields are included
                preorder_products = [p for p in data['products'] if p.get('is_preorder')]
                print(f"🎯 Preorder products in response: {len(preorder_products)}")
                
                if preorder_products:
                    print("✅ SUCCESS: Preorder fields are included in initial response!")
                else:
                    print("❌ ISSUE: No preorder fields in initial response")
                    
        else:
            print(f"❌ API call failed: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("❌ Dashboard not running. Please start it first with: python main_dashboard.py")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_initial_api_response()