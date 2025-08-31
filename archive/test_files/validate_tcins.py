import requests
import json

def test_tcin(tcin):
    """Test if a TCIN is valid and returns stock data"""
    url = f"https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    
    params = {
        'key': '9f36aeafbe60771e321a7cc95a78140772ab3e96',
        'tcin': tcin,
        'store_id': '865',
        'pricing_store_id': '865',
        'has_pricing_store_id': 'true',
        'has_financing_options': 'true',
        'visitor_id': 'TEST123456789ABCDEF',
    }
    
    headers = {
        'accept': 'application/json',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://www.target.com',
        'referer': 'https://www.target.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        print(f"\nTCIN {tcin}:")
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            product = data['data']['product']
            item = product['item']
            
            name = item['product_description']['title'][:50]
            price = product['price']['current_retail']
            
            print(f"✅ VALID - {name} - ${price}")
            return True
        else:
            print(f"❌ INVALID - HTTP {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ ERROR - {e}")
        return False

# Test some current TCINs - you'll need to find these from Target.com
test_tcins = [
    "89542109",   # Your current one
    "87365606",   # Example - replace with real ones
    "54191097",   # Example - replace with real ones  
]

print("Testing TCINs...")
for tcin in test_tcins:
    test_tcin(tcin)
    
print("\nTo fix: Go to Target.com, find products, copy TCINs from URLs")