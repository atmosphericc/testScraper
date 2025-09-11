#!/usr/bin/env python3
import requests
import json

try:
    print("Testing API...")
    r = requests.get('http://localhost:5001/api/live-stock-status', timeout=3)
    print(f'✅ Success: {r.status_code}')
    data = r.json()
    print(f'Products returned: {len(data.get("products", []))}')
    if data.get('products'):
        print(f'First product: {data["products"][0]["name"]} - {data["products"][0]["availability"]}')
    print('✅ API is working properly!')
except Exception as e:
    print(f'❌ Error: {e}')