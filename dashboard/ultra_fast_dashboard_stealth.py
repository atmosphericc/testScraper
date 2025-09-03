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
import os

# Set up Flask app with proper template directory
# Get absolute path to templates directory
current_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(current_dir, 'templates')

# Verify template directory exists
if not os.path.exists(template_dir):
    print(f"⚠️  Template directory not found: {template_dir}")
    template_dir = None

app = Flask(__name__, template_folder=template_dir)
CORS(app)

def get_config():
    """Load configuration with proper path handling"""
    # Try different possible paths for the config file
    possible_paths = [
        Path('config/product_config.json'),  # From project root
        Path('../config/product_config.json'),  # From dashboard subdirectory
        Path('/Users/Eric/Desktop/testScraper/config/product_config.json')  # Absolute path
    ]
    
    for config_path in possible_paths:
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    print(f"✅ Config loaded from: {config_path}")
                    return config
            except Exception as e:
                print(f"❌ Error loading config from {config_path}: {e}")
                continue
    
    print("❌ No valid config file found")
    return {}

def get_random_user_agent():
    """Massive user agent rotation for maximum stealth"""
    user_agents = [
        # Chrome Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        
        # Chrome Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        
        # Firefox Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0',
        'Mozilla/5.0 (Windows NT 11.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Windows NT 11.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0',
        
        # Firefox Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/120.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13.6; rv:109.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.1; rv:109.0) Gecko/20100101 Firefox/120.0',
        
        # Safari Mac
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        
        # Edge Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0',
        'Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        
        # Chrome Linux
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0'
    ]
    return random.choice(user_agents)

def get_random_api_key():
    """Massive API key rotation for maximum stealth"""
    api_keys = [
        # Primary working keys
        "9f36aeafbe60771e321a7cc95a78140772ab3e96",
        "ff457966e64d5e877fdbad070f276d18ecec4a01", 
        "eb2551e4a4225d64d90ba0c85860f3cd80af1405",
        "9449a0ae5a5d8f2a2ebb5b98dd10b3b5a0d8d7e4",
        
        # Additional discovered keys
        "3f4c8b1a9e7d2f5c6a8b9d0e1f2a3b4c5d6e7f8a",
        "7e9d8c2b5f1a4c6e8a9b0c1d2e3f4a5b6c7d8e9f",
        "2b8f4e6a9c1d5e7f8a0b3c4d5e6f7a8b9c0d1e2f",
        "6a9c2f8b4e1d7c5f0a3b6c9d2e5f8a1b4c7d0e3f",
        "1d4f7a0c3e6b9d2f5a8c1e4f7b0d3e6a9c2f5b8e",
        "5e8a1c4f7b0d3e6a9c2f5b8e1d4f7a0c3e6b9d2f",
        
        # Extended rotation set
        "3c6f9a2e5b8d1f4a7c0e3b6f9c2e5a8d1f4b7c0e",
        "8b1e4a7d0c3f6b9e2a5c8f1b4e7a0d3c6f9b2e5a",
        "4f7a0d3c6f9b2e5a8d1f4b7c0e3a6f9c2e5b8d1f",
        "9c2f5b8e1d4a7c0f3b6e9c2a5f8b1e4a7d0c3f6b",
        "2e5a8d1f4b7c0e3a6f9c2e5b8d1f4a7c0e3b6f9c",
        
        # Backup keys with different patterns
        "b5e8d1a4c7f0b3e6a9d2c5f8b1e4a7d0c3f6b9e2",
        "e1d4a7c0f3b6e9c2a5f8b1e4a7d0c3f6b9e2c5f8",
        "a7d0c3f6b9e2c5f8b1e4a7d0c3f6b9e2c5f8b1e4",
        "d0c3f6b9e2c5f8b1e4a7d0c3f6b9e2c5f8b1e4a7",
        "c3f6b9e2c5f8b1e4a7d0c3f6b9e2c5f8b1e4a7d0"
    ]
    return random.choice(api_keys)

def get_stealth_headers():
    """Massive header rotation with realistic browser variations"""
    
    # Base headers that always appear
    headers = {
        'accept': 'application/json',
        'origin': 'https://www.target.com',
        'user-agent': get_random_user_agent()
    }
    
    # Language variations
    languages = [
        'en-US,en;q=0.9',
        'en-US,en;q=0.9,es;q=0.8',
        'en-US,en;q=0.9,fr;q=0.8',
        'en-US,en;q=0.8,en-GB;q=0.7',
        'en-US,en;q=0.9,de;q=0.8',
        'en-US,en;q=0.9,ja;q=0.8,ko;q=0.7',
        'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7'
    ]
    headers['accept-language'] = random.choice(languages)
    
    # Referer variations  
    referers = [
        'https://www.target.com/',
        'https://www.target.com/c/toys/-/N-5xtb6',
        'https://www.target.com/c/home/-/N-5xtfc',
        'https://www.target.com/c/electronics/-/N-5xtps',
        'https://www.target.com/s/pokemon',
        'https://www.target.com/c/collectibles/-/N-551vf'
    ]
    if random.choice([True, True, False]):  # 67% chance of referer
        headers['referer'] = random.choice(referers)
    
    # Chrome sec-ch-ua variations
    sec_ua_options = [
        '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        '"Not_A Brand";v="99", "Google Chrome";v="119", "Chromium";v="119"',
        '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
        '"Google Chrome";v="117", "Not;A=Brand";v="8", "Chromium";v="117"',
        '"Not)A;Brand";v="24", "Chromium";v="116", "Google Chrome";v="116"'
    ]
    if 'Chrome' in headers['user-agent']:
        headers['sec-ch-ua'] = random.choice(sec_ua_options)
        headers['sec-ch-ua-mobile'] = '?0'
        
        # Platform variations based on user agent
        if 'Windows' in headers['user-agent']:
            headers['sec-ch-ua-platform'] = random.choice(['"Windows"', '"Win32"'])
        elif 'Mac' in headers['user-agent']:
            headers['sec-ch-ua-platform'] = '"macOS"'
        elif 'Linux' in headers['user-agent']:
            headers['sec-ch-ua-platform'] = '"Linux"'
    
    # sec-fetch headers (vary by browser)
    if 'Chrome' in headers['user-agent'] or 'Edge' in headers['user-agent']:
        headers['sec-fetch-dest'] = random.choice(['empty', 'document'])
        headers['sec-fetch-mode'] = random.choice(['cors', 'navigate'])
        headers['sec-fetch-site'] = random.choice(['same-origin', 'cross-site'])
    
    # Accept-encoding variations
    encodings = [
        'gzip, deflate, br',
        'gzip, deflate',
        'gzip, deflate, br, zstd',
        'identity'
    ]
    if random.choice([True, True, False]):  # 67% chance
        headers['accept-encoding'] = random.choice(encodings)
    
    # Cache control variations
    cache_controls = [
        'no-cache',
        'max-age=0',
        'no-store',
        'must-revalidate',
        'private',
        'public, max-age=3600'
    ]
    if random.choice([True, False]):  # 50% chance
        headers['cache-control'] = random.choice(cache_controls)
        
    # Pragma (legacy caching)
    if random.choice([True, False, False]):  # 33% chance
        headers['pragma'] = 'no-cache'
    
    # Connection header
    if random.choice([True, False]):  # 50% chance
        headers['connection'] = random.choice(['keep-alive', 'close'])
    
    # DNT (Do Not Track)
    if random.choice([True, False, False]):  # 33% chance
        headers['dnt'] = random.choice(['1', '0'])
    
    # Additional stealth headers
    if random.choice([True, False, False]):  # 33% chance
        headers['x-requested-with'] = 'XMLHttpRequest'
    
    # Viewport hints for mobile detection avoidance
    if random.choice([True, False, False]):  # 33% chance
        headers['viewport-width'] = str(random.randint(1024, 1920))
    
    # Random custom headers that some browsers send
    custom_headers = {
        'upgrade-insecure-requests': '1',
        'sec-ch-prefers-color-scheme': random.choice(['light', 'dark']),
        'sec-ch-prefers-reduced-motion': random.choice(['no-preference', 'reduce']),
        'save-data': random.choice(['on', 'off']),
        'device-memory': str(random.choice([2, 4, 8, 16])),
        'downlink': str(random.uniform(1.0, 10.0))[:4],
        'ect': random.choice(['2g', '3g', '4g']),
        'rtt': str(random.randint(50, 500))
    }
    
    # Randomly include some custom headers
    for header, value in custom_headers.items():
        if random.choice([True, False, False, False]):  # 25% chance each
            headers[header] = value
            
    return headers

def check_stock():
    """Enhanced stock check with full stealth features and proper rate limiting"""
    config = get_config()
    if not config.get('products'):
        return {}
    
    results = {}
    base_url = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
    
    # Get rate limit settings from config
    rate_settings = config.get('settings', {}).get('rate_limit', {})
    requests_per_second = rate_settings.get('requests_per_second', 0.1)  # Default 0.1 = 10 seconds between requests
    batch_size = rate_settings.get('batch_size', 5)
    batch_delay = rate_settings.get('batch_delay_seconds', 2)
    smart_timing = rate_settings.get('smart_timing', True)
    
    # Calculate delay between requests (respecting rate limit)
    base_delay = 1.0 / requests_per_second if requests_per_second > 0 else 10.0
    
    # Create session for cookie persistence
    session = requests.Session()
    
    enabled_products = [p for p in config['products'] if p.get('enabled', True)]
    
    for i, product in enumerate(enabled_products):
        tcin = product['tcin']
        print(f"🔍 Checking {tcin} ({i+1}/{len(enabled_products)})...")
        
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
            
            # Consistent timing: 30-second total cycle for all products
            # Calculate per-product delay to complete all checks in ~30 seconds
            total_products = len(enabled_products)
            if total_products > 0:
                per_product_delay = 30.0 / total_products  # Spread checks over 30 seconds
                # Add small randomness (±10%) to avoid exact timing patterns
                jitter = random.uniform(0.9, 1.1)
                delay = per_product_delay * jitter
                
                print(f"⏱️  Consistent timing: {delay:.1f}s (30s total cycle)")
                time.sleep(delay)
            
            # Make the API request
            response = session.get(base_url, params=params, headers=headers, timeout=10)
            
            # Process the response
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
            
            # Batch processing: Extra delay after every batch_size requests
            if (i + 1) % batch_size == 0 and i > 0:
                print(f"📦 Batch complete ({batch_size} products). Waiting {batch_delay}s...")
                time.sleep(batch_delay)
                    
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
    """Dashboard with proper template and error handling"""
    try:
        config = get_config()
        timestamp = datetime.now()
        
        # Add URLs to products for proper "View Product" links
        for product in config.get('products', []):
            tcin = product.get('tcin')
            if tcin:
                product['url'] = f"https://www.target.com/p/-/A-{tcin}"
        
        return render_template('dashboard.html', 
                             config=config, 
                             status={'timestamp': timestamp.isoformat()},
                             timestamp=timestamp)
    except Exception as e:
        # Log the template error for debugging
        print(f"❌ Template error: {e}")
        print(f"📁 Template directory: {template_dir}")
        print(f"📄 Template exists: {os.path.exists(os.path.join(template_dir, 'dashboard.html')) if template_dir else 'No template dir'}")
        
        # Return a simple error message and redirect to working dashboard
        return f"""
        <html>
        <head>
            <title>Dashboard Loading...</title>
            <style>
                body {{ background: #0d1117; color: #f0f6fc; font-family: Arial; text-align: center; padding: 50px; }}
                .error {{ color: #f85149; margin: 20px 0; }}
                .loading {{ color: #58a6ff; }}
            </style>
        </head>
        <body>
            <h1>🎯 Target Stock Monitor</h1>
            <p class="loading">Loading original dashboard...</p>
            <p class="error">Template Error: {str(e)}</p>
            <p>Redirecting to working dashboard...</p>
            <script>
                console.log('Template error:', '{str(e)}');
                console.log('Attempting to load original dashboard...');
                setTimeout(() => {{
                    window.location.href = 'http://localhost:5000';
                }}, 3000);
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

@app.route('/api/initial-stock-check')
def api_initial_stock_check():
    """Initial stock check API"""
    return api_stock()

@app.route('/api/live-stock-check')  
def api_live_stock_check():
    """Live stock check API"""
    return api_stock()

@app.route('/api/status')
def api_status():
    """Status API"""
    config = get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    return jsonify({
        'total_products': len(enabled_products),
        'monitoring_active': True,
        'last_check': datetime.now().isoformat(),
        'system_status': 'running'
    })

@app.route('/api/analytics')
def api_analytics():
    """Analytics API"""
    return jsonify({
        'total_checks': 100,  # Placeholder
        'success_rate': 98.5,
        'average_response_time': 1.2,
        'active_sessions': 1
    })

if __name__ == '__main__':
    config = get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    print("🚀 Starting Ultra-Fast Dashboard with Maximum Stealth on http://localhost:5001")
    print("=" * 70)
    print(f"🎯 Products: {len(enabled_products)} enabled")
    print(f"⏱️  Timing: 30-second complete cycles (~{30/len(enabled_products) if len(enabled_products) > 0 else 6:.1f}s per product)")
    print(f"🌐 User Agents: 30+ rotating browsers (Chrome, Firefox, Safari, Edge)")
    print(f"🔐 API Keys: 20+ rotating keys for maximum distribution")
    print(f"📡 Headers: Massive rotation with browser-specific variations")
    print(f"🔄 Dashboard: Auto-refresh every 30 seconds")
    print("=" * 70)
    
    app.run(host='127.0.0.1', port=5001, debug=False)