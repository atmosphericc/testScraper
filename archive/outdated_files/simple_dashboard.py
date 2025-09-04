#!/usr/bin/env python3
"""
Simple synchronous dashboard for debugging
"""
import json
import time
import random
import requests
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

def get_config():
    """Load configuration"""
    config_path = Path('config/product_config.json')
    if not config_path.exists():
        return {}
    
    with open(config_path, 'r') as f:
        return json.load(f)

def get_random_user_agent():
    """Rotate user agents for stealth"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0'
    ]
    return random.choice(user_agents)

def get_random_api_key():
    """Rotate API keys for stealth"""
    api_keys = [
        "9f36aeafbe60771e321a7cc95a78140772ab3e96",
        "ff457966e64d5e877fdbad070f276d18ecec4a01", 
        "eb2551e4a4225d64d90ba0c85860f3cd80af1405",
        "9449a0ae5a5d8f2a2ebb5b98dd10b3b5a0d8d7e4"
    ]
    return random.choice(api_keys)

def get_stealth_headers():
    """Generate rotating stealth headers"""
    headers = {
        'accept': 'application/json',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://www.target.com',
        'referer': 'https://www.target.com/',
        'user-agent': get_random_user_agent(),
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'x-requested-with': 'XMLHttpRequest'
    }
    
    # Randomly add/remove some headers for variety
    if random.choice([True, False]):
        headers['cache-control'] = 'no-cache'
    if random.choice([True, False]):
        headers['pragma'] = 'no-cache'
        
    return headers

def check_stock():
    """Enhanced stock check with full stealth features"""
    config = get_config()
    if not config.get('products'):
        return {}
    
    results = {}
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    
    # Create session for cookie persistence
    session = requests.Session()
    
    for product in config['products']:
        if product.get('enabled', True):
            tcin = product['tcin']
            print(f"🔍 Checking {tcin}...")
            
            try:
                # Rotate API key and visitor ID for each request
                api_key = get_random_api_key()
                visitor_id = ''.join(random.choices('0123456789ABCDEF', k=32))
                
                params = {
                    'key': api_key,
                    'tcin': tcin,
                    'store_id': '865',
                    'pricing_store_id': '865',
                    'has_pricing_store_id': 'true',
                    'visitor_id': visitor_id,
                    'isBot': 'false',  # Anti-bot parameter
                    'channel': 'WEB',
                    'page': '/p/A-' + tcin
                }
                
                # Get rotating stealth headers
                headers = get_stealth_headers()
                
                # Add random delay between requests for stealth
                time.sleep(random.uniform(0.5, 2.0))
                
                response = requests.get(base_url, params=params, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    product_data = data.get('data', {}).get('product', {})
                    
                    # Get product name
                    item_data = product_data.get('item', {})
                    raw_name = item_data.get('product_description', {}).get('title', product.get('name', f'Product {tcin}'))
                    
                    import html
                    product_name = html.unescape(raw_name) if raw_name else product.get('name', f'Product {tcin}')
                    
                    # Check availability
                    fulfillment = product_data.get('fulfillment', {})
                    shipping_options = fulfillment.get('shipping_options', {})
                    
                    sold_out = fulfillment.get('sold_out', True)
                    availability_status = shipping_options.get('availability_status', 'UNAVAILABLE')
                    available_to_promise = shipping_options.get('available_to_promise_quantity', 0)
                    
                    available = not sold_out and available_to_promise > 0
                    
                    results[tcin] = {
                        'available': available,
                        'status': 'IN_STOCK' if available else 'OUT_OF_STOCK',
                        'name': product_name,
                        'tcin': tcin,
                        'last_checked': datetime.now().isoformat(),
                        'quantity': available_to_promise
                    }
                    print(f"✅ {tcin}: {product_name} - {'IN STOCK' if available else 'OUT OF STOCK'}")
                    
                else:
                    results[tcin] = {
                        'available': False,
                        'status': 'ERROR',
                        'name': product.get('name', f'Product {tcin}'),
                        'tcin': tcin,
                        'last_checked': datetime.now().isoformat(),
                        'error': f'HTTP {response.status_code}'
                    }
                    print(f"❌ {tcin}: HTTP {response.status_code}")
                    
            except Exception as e:
                results[tcin] = {
                    'available': False,
                    'status': 'ERROR',
                    'name': product.get('name', f'Product {tcin}'),
                    'tcin': tcin,
                    'last_checked': datetime.now().isoformat(),
                    'error': str(e)
                }
                print(f"❌ {tcin}: {e}")
    
    return results

@app.route('/')
def index():
    """Simple dashboard"""
    return """
    <html>
    <head><title>Target Monitor - Simple Dashboard</title></head>
    <body>
        <h1>Target Stock Monitor</h1>
        <div id="products">Loading...</div>
        <script>
            function loadData() {
                fetch('/api/stock')
                    .then(r => r.json())
                    .then(data => {
                        let html = '<h2>Products:</h2>';
                        for (let tcin in data) {
                            let p = data[tcin];
                            html += '<div style="border:1px solid #ccc; margin:10px; padding:10px;">';
                            html += '<h3>' + p.name + ' (' + tcin + ')</h3>';
                            html += '<p>Status: <strong>' + p.status + '</strong></p>';
                            html += '<p>Available: ' + (p.available ? 'YES' : 'NO') + '</p>';
                            html += '<p>Last Checked: ' + p.last_checked + '</p>';
                            if (p.error) html += '<p>Error: ' + p.error + '</p>';
                            html += '</div>';
                        }
                        document.getElementById('products').innerHTML = html;
                    });
            }
            loadData();
            setInterval(loadData, 30000);
        </script>
    </body>
    </html>
    """

@app.route('/api/stock')
def api_stock():
    """Stock API"""
    try:
        results = check_stock()
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)})

if __name__ == '__main__':
    print("🚀 Starting Simple Dashboard on http://localhost:5002")
    app.run(host='127.0.0.1', port=5002, debug=False)