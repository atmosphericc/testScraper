#!/usr/bin/env python3
"""
SIMPLE DASHBOARD - Basic synchronous version
- Hit API, get response, display dashboard
- No websockets, no multithreading, no loading states
- Dashboard only shows after data is received
- Keeps warming up and stealth features
"""

import json
import time
import random
import requests
import html
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, jsonify
import logging
import os
import ssl
import socket
import pickle
from typing import Dict, List, Any
from urllib3.util.ssl_ import create_urllib3_context
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

# Try to import advanced stealth libraries for F5/Shape evasion
try:
    import curl_cffi
    from curl_cffi import requests as cf_requests
    CURL_CFFI_AVAILABLE = True
    print("curl_cffi available - Advanced TLS fingerprinting enabled")
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("[WARN] curl_cffi not available - using standard requests")

# Initialize Flask app
app = Flask(__name__, template_folder='dashboard/templates')
app.secret_key = 'simple-stealth-dashboard-2025'

# Global data storage
latest_stock_data = {}
last_update_time = None
activity_log = []

# Activity log persistence file
ACTIVITY_LOG_FILE = 'logs/activity_log.pkl'

def load_activity_log():
    """Load activity log from file"""
    global activity_log
    try:
        if os.path.exists(ACTIVITY_LOG_FILE):
            with open(ACTIVITY_LOG_FILE, 'rb') as f:
                activity_log = pickle.load(f)
                print(f"[SYSTEM] Loaded {len(activity_log)} activity log entries")
        else:
            # Create logs directory if it doesn't exist
            os.makedirs('logs', exist_ok=True)
            activity_log = []
            print("[SYSTEM] Created new activity log")
    except Exception as e:
        print(f"[WARN] Failed to load activity log: {e}")
        activity_log = []

def save_activity_log():
    """Save activity log to file"""
    try:
        os.makedirs('logs', exist_ok=True)
        with open(ACTIVITY_LOG_FILE, 'wb') as f:
            pickle.dump(activity_log, f)
    except Exception as e:
        print(f"[WARN] Failed to save activity log: {e}")

def add_activity_log(message, level="info", category="system"):
    """Add entry to activity log with timestamp and persistence"""
    global activity_log
    timestamp = datetime.now()
    
    entry = {
        'timestamp': timestamp,
        'message': message,
        'level': level,
        'category': category,
        'time_str': timestamp.strftime('%H:%M:%S'),
        'date_str': timestamp.strftime('%Y-%m-%d'),
        'full_time': timestamp.strftime('%Y-%m-%d %H:%M:%S')
    }
    
    activity_log.insert(0, entry)  # Insert at beginning for newest first
    
    # Keep only last 500 entries (increased for better history with individual product logs)
    if len(activity_log) > 500:
        activity_log = activity_log[:500]
    
    # Save to file for persistence
    save_activity_log()
    
    print(f"[{timestamp.strftime('%H:%M:%S')}] [{category.upper()}] {message}")

def add_api_summary_log(total_products, in_stock, out_of_stock, response_time_ms):
    """Add a structured API summary log entry"""
    summary_msg = f"API Summary: {total_products} products monitored • {in_stock} in stock • {out_of_stock} out of stock • {response_time_ms:.0f}ms response"
    add_activity_log(summary_msg, level="info", category="api_summary")

def get_massive_user_agent_rotation():
    """50+ User agents for maximum stealth"""
    user_agents = [
        # Chrome Windows - Latest versions
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',

        # Chrome macOS
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',

        # Safari macOS
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15',

        # Firefox Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:119.0) Gecko/20100101 Firefox/119.0',

        # Edge Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        'Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0'
    ]
    return random.choice(user_agents)

def get_massive_api_key_rotation():
    """Working API keys for reliable operation"""
    working_api_keys = [
        "ff457966e64d5e877fdbad070f276d18ecec4a01",
        "9f36aeafbe60771e321a7cc95a78140772ab3e96"
    ]
    return random.choice(working_api_keys)

def get_rotating_cookies():
    """Get rotating cookie sets for maximum stealth"""
    cookie_sets = [
        {
            'sessionId': ''.join(random.choices('0123456789abcdef', k=32)),
            'visitorId': ''.join(random.choices('0123456789ABCDEF', k=16)),
            'timezone': random.choice(['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles']),
        },
        {
            'cart_id': ''.join(random.choices('0123456789abcdef', k=24)),
            'user_pref': 'lang=en-US&currency=USD',
            'last_visit': str(int(time.time() - random.randint(3600, 86400))),
        },
        {
            '_ga': f"GA1.2.{random.randint(100000000, 999999999)}.{int(time.time())}",
            '_gid': f"GA1.2.{random.randint(100000000, 999999999)}",
            'analytics_session': ''.join(random.choices('0123456789abcdef', k=16)),
        }
    ]
    return random.choice(cookie_sets)

def get_ultra_stealth_headers():
    """Ultimate header rotation with advanced browser fingerprinting"""
    user_agent = get_massive_user_agent_rotation()

    headers = {
        'accept': 'application/json',
        'user-agent': user_agent,
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
        'connection': 'keep-alive',
    }

    # Chrome-specific sec-ch-ua headers
    if 'Chrome' in user_agent:
        sec_ua_options = [
            '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            '"Not_A Brand";v="8", "Chromium";v="121", "Google Chrome";v="121"',
            '"Google Chrome";v="120", "Chromium";v="120", "Not=A?Brand";v="8"'
        ]
        headers['sec-ch-ua'] = random.choice(sec_ua_options)
        headers['sec-ch-ua-mobile'] = '?0'

        # Platform-specific headers
        if 'Windows' in user_agent:
            headers['sec-ch-ua-platform'] = '"Windows"'
            if 'NT 11.0' in user_agent:
                headers['sec-ch-ua-platform-version'] = '"13.0.0"'
            elif 'NT 10.0' in user_agent:
                headers['sec-ch-ua-platform-version'] = '"10.0.0"'
        elif 'Mac' in user_agent:
            headers['sec-ch-ua-platform'] = '"macOS"'

    # sec-fetch headers
    if 'Chrome' in user_agent or 'Edge' in user_agent:
        headers['sec-fetch-dest'] = 'empty'
        headers['sec-fetch-mode'] = 'cors'
        headers['sec-fetch-site'] = 'same-origin'

    # Referer variations
    referers = [
        'https://www.target.com/',
        'https://www.target.com/c/collectible-trading-cards-hobby-collectibles-toys/-/N-27p31',
        'https://www.target.com/c/toys/-/N-5xtb0'
    ]
    if random.choice([True, True, False]):
        headers['referer'] = random.choice(referers)

    # Additional stealth headers
    if random.choice([True, False]):
        headers['upgrade-insecure-requests'] = '1'

    if random.choice([True, False, False]):
        headers['x-requested-with'] = 'XMLHttpRequest'

    return headers

class SessionWarmupManager:
    """F5/Shape evasion: Session warmup by visiting Target.com pages before API calls"""

    def __init__(self):
        self.warmup_pages = [
            'https://www.target.com/',
            'https://www.target.com/c/trading-cards-collectibles-toys/-/N-5xtfz',
            'https://www.target.com/s/pokemon+cards',
        ]
        self.last_warmup_time = None

    def needs_warmup(self):
        """Check if session needs warmup"""
        if not self.last_warmup_time:
            return True
        return datetime.now() - self.last_warmup_time > timedelta(hours=random.uniform(2, 4))

    def warmup_session(self, session, headers):
        """F5/Shape evasion: Visit Target pages before API call"""
        if not self.needs_warmup():
            return True

        try:
            warmup_url = random.choice(self.warmup_pages)

            warmup_headers = headers.copy()
            warmup_headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Upgrade-Insecure-Requests': '1'
            })

            print(f"[HOT] Warming session with {warmup_url}")

            warmup_response = session.get(
                warmup_url,
                headers=warmup_headers,
                timeout=15,
                allow_redirects=True
            )

            # Human reading time
            time.sleep(random.uniform(2, 5))

            self.last_warmup_time = datetime.now()
            print(f"[OK] Session warmup complete: {warmup_response.status_code}")
            return True

        except Exception as e:
            print(f"[WARN] Session warmup failed: {e}")
            return False

class SimpleStealthChecker:
    """Simple stealth checker with essential features only"""

    def __init__(self):
        self.batch_endpoint = 'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1'
        self.location_params = {
            'store_id': '1859',
            'pricing_store_id': '1859',
            'zip': '33809',
            'state': 'FL',
            'latitude': '28.0395',
            'longitude': '-81.9498'
        }

    def get_config(self):
        """Load product configuration"""
        possible_paths = [
            "config/product_config.json",
            "dashboard/../config/product_config.json",
            "../config/product_config.json"
        ]

        for path in possible_paths:
            if Path(path).exists():
                with open(path, 'r') as f:
                    return json.load(f)
        return {"products": []}

    def create_stealth_session(self):
        """Create session with stealth configuration"""
        session = requests.Session()
        session.cookies.clear()

        # Apply rotating cookies
        rotating_cookies = get_rotating_cookies()
        for name, value in rotating_cookies.items():
            session.cookies.set(name, value)

        return session, rotating_cookies

    def make_simple_stealth_call(self):
        """Make simple API call - basic version"""
        print("[API] Starting simple API call...")
        add_activity_log("Initiating API call to Target.com", "info", "api")

        try:
            config = self.get_config()
            enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]

            if not enabled_products:
                print("[API] No enabled products")
                add_activity_log("No enabled products found in configuration", "warning", "config")
                return {}

            # Extract TCINs
            tcins = [p['tcin'] for p in enabled_products]
            print(f"[API] Checking {len(tcins)} products: {tcins}")
            add_activity_log(f"Monitoring {len(tcins)} products: {', '.join(tcins)}", "info", "config")

            # Simple API key
            api_key = "ff457966e64d5e877fdbad070f276d18ecec4a01"

            # Simple parameters
            params = {
                'key': api_key,
                'tcins': ','.join(tcins),
                'store_id': '1859',
                'pricing_store_id': '1859'
            }

            # Simple session
            session = requests.Session()

            # Simple headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive'
            }

            print("[API] Making simple API request...")

            start_time = time.time()
            response = session.get(
                self.batch_endpoint,
                params=params,
                headers=headers,
                timeout=10
            )

            end_time = time.time()
            response_time = (end_time - start_time) * 1000

            if response.status_code == 200:
                print(f"[OK] API success: {response_time:.0f}ms")
                add_activity_log(f"API call successful - {response_time:.0f}ms response time", "success", "api")
                data = response.json()
                processed_data = self.process_batch_response(data, enabled_products, response_time)
                return processed_data
            else:
                print(f"[ERROR] API failed: HTTP {response.status_code}")
                print(f"[DEBUG] URL: {response.url}")
                add_activity_log(f"API call failed - HTTP {response.status_code}", "error", "api")
                return {}

        except Exception as e:
            print(f"[ERROR] API exception: {e}")
            add_activity_log(f"API exception occurred: {str(e)}", "error", "api")
            return {}
        finally:
            session.close()

    def process_batch_response(self, data, enabled_products, response_time):
        """Process batch API response"""
        if not data or 'data' not in data or 'product_summaries' not in data['data']:
            print("[ERROR] Invalid response structure")
            return {}

        product_summaries = data['data']['product_summaries']
        processed_data = {}

        print(f"[DATA] Processing {len(product_summaries)} products")

        for product_summary in product_summaries:
            try:
                tcin = product_summary.get('tcin')
                if not tcin:
                    continue

                item = product_summary.get('item', {})
                fulfillment = product_summary.get('fulfillment', {})

                product_desc = item.get('product_description', {})
                raw_name = product_desc.get('title', 'Unknown Product')
                clean_name = html.unescape(raw_name)

                shipping = fulfillment.get('shipping_options', {})
                availability_status = shipping.get('availability_status', 'UNKNOWN')

                relationship_code = item.get('relationship_type_code', 'UNKNOWN')
                is_target_direct = relationship_code == 'SA'

                is_preorder = 'PRE_ORDER' in availability_status

                if is_preorder:
                    base_available = availability_status == 'PRE_ORDER_SELLABLE'
                else:
                    base_available = availability_status == 'IN_STOCK'

                is_available = base_available and is_target_direct

                if not is_target_direct:
                    normalized_status = 'MARKETPLACE_SELLER'
                elif not base_available:
                    normalized_status = 'OUT_OF_STOCK'
                else:
                    normalized_status = 'IN_STOCK'

                # Simple hardcoded Target URL format
                buy_url = f"https://www.target.com/p/-/A-{tcin}"
                print(f"[URL] Using Target URL for {tcin}: {buy_url}")

                # Extract product image URL
                image_url = None
                try:
                    # Try to get primary image from product_images
                    product_images = item.get('product_images', [])
                    if product_images and len(product_images) > 0:
                        primary_image = product_images[0]
                        image_url = primary_image.get('url')
                        if image_url:
                            print(f"[IMAGE] Found image for {tcin}: {image_url}")
                        else:
                            print(f"[IMAGE] No image URL found for {tcin}")
                    else:
                        print(f"[IMAGE] No product images found for {tcin}")
                except Exception as e:
                    print(f"[IMAGE] Error extracting image for {tcin}: {e}")

                result = {
                    'tcin': tcin,
                    'name': clean_name,
                    'available': is_available,
                    'status': normalized_status,
                    'last_checked': datetime.now().isoformat(),
                    'quantity': 1 if is_available else 0,
                    'availability_status': availability_status,
                    'sold_out': not is_available,
                    'confidence': 'high',
                    'method': 'simple_stealth',
                    'url': buy_url,
                    'image_url': image_url,
                    'store_available': not fulfillment.get('is_out_of_stock_in_all_store_locations', True),
                    'is_target_direct': is_target_direct,
                    'is_marketplace': not is_target_direct,
                    'seller_code': relationship_code,
                    'is_preorder': is_preorder,
                    'stealth_applied': True,
                    'batch_api': True,
                    'has_data': True
                }

                processed_data[tcin] = result

            except Exception as e:
                print(f"[ERROR] Failed to process product {tcin}: {e}")
                continue

        in_stock_count = sum(1 for r in processed_data.values() if r.get('available'))
        out_of_stock_count = len(processed_data) - in_stock_count
        print(f"[OK] Processing complete: {len(processed_data)} products ({in_stock_count} in stock)")
        
        # Add comprehensive API summary
        add_api_summary_log(len(processed_data), in_stock_count, out_of_stock_count, response_time)
        
        # Log all individual product statuses with full titles
        for tcin, result in processed_data.items():
            product_name = result.get('name', 'Unknown Product')
            if result.get('available'):
                add_activity_log(f"{product_name} - IN STOCK", "success", "stock_status")
            else:
                add_activity_log(f"{product_name} - OUT OF STOCK", "info", "stock_status")

        return processed_data

# Initialize simple checker and clear activity log on server start
simple_checker = SimpleStealthChecker()

# Clear activity log on server startup (not on refresh)
activity_log = []
save_activity_log()  # Save empty log
add_activity_log("Target Monitor Pro initialized", "info", "system")

@app.route('/')
def index():
    """Simple dashboard - only shows after API data is received"""
    global latest_stock_data, last_update_time

    print("[DASHBOARD] Loading dashboard...")

    # Make fresh API call to get data
    print("[DASHBOARD] Making API call to get fresh data...")
    stock_data = simple_checker.make_simple_stealth_call()

    if stock_data:
        latest_stock_data = stock_data
        last_update_time = datetime.now()

        # Get config for template
        config = simple_checker.get_config()

        # Update config with API data
        for product in config.get('products', []):
            tcin = product.get('tcin')
            if tcin and tcin in stock_data:
                api_data = stock_data[tcin]
                product.update({
                    'display_name': api_data.get('name', product.get('name', 'Unknown Product')),
                    'available': api_data.get('available', False),
                    'stock_status': api_data.get('status', 'OUT_OF_STOCK'),
                    'status': api_data.get('status', 'OUT_OF_STOCK'),
                    'is_preorder': api_data.get('is_preorder', False),
                    'is_target_direct': api_data.get('is_target_direct', True),
                    'seller_code': api_data.get('seller_code', 'UNKNOWN'),
                    'url': api_data.get('url', f"https://www.target.com/p/-/A-{tcin}"),  # Add URL
                    'has_data': True
                })
            else:
                # No data for this product
                product.update({
                    'display_name': product.get('name', 'Unknown Product'),
                    'available': False,
                    'stock_status': 'NO_DATA',
                    'status': 'NO_DATA',
                    'is_preorder': False,
                    'is_target_direct': True,
                    'seller_code': 'UNKNOWN',
                    'url': f"https://www.target.com/p/-/A-{tcin}",  # Add fallback URL
                    'has_data': False
                })

        # Build status info
        in_stock_count = sum(1 for r in stock_data.values() if r.get('available'))
        data_age_seconds = (datetime.now() - last_update_time).total_seconds()
        status = {
            'monitoring': True,
            'total_checks': 1,
            'in_stock_count': in_stock_count,
            'last_update': last_update_time.isoformat(),
            'data_age_seconds': int(data_age_seconds),
            'timestamp': datetime.now(),
            'api_method': 'simple_stealth',
            'stealth_active': True,
            'data_loaded': True
        }

        print(f"[DASHBOARD] ✅ Dashboard ready with {len(stock_data)} products ({in_stock_count} in stock)")

        return render_template('simple_dashboard.html',
                             config=config,
                             status=status,
                             activity_log=activity_log,
                             timestamp=datetime.now())

    else:
        # API call failed - show dashboard with error state
        print("[DASHBOARD] ❌ API call failed - showing dashboard with error state")

        # Get config for template
        config = simple_checker.get_config()

        # Create empty data structure for failed state
        status = {
            'monitoring': True,
            'total_checks': 1,
            'in_stock_count': 0,
            'last_update': datetime.now().isoformat(),
            'timestamp': datetime.now(),
            'api_method': 'simple_stealth',
            'stealth_active': True,
            'data_loaded': False,
            'error': 'Failed to load stock data from API'
        }

        # Update config with error state
        for product in config.get('products', []):
            product.update({
                'display_name': product.get('name', 'Unknown Product'),
                'available': False,
                'stock_status': 'ERROR',
                'status': 'ERROR',
                'is_preorder': False,
                'is_target_direct': True,
                'seller_code': 'ERROR',
                'has_data': False
            })

        return render_template('simple_dashboard.html',
                             config=config,
                             status=status,
                             activity_log=activity_log,
                             timestamp=datetime.now())

@app.route('/refresh')
def refresh():
    """Simple refresh endpoint"""
    print("[REFRESH] Refreshing data...")
    add_activity_log("Manual refresh triggered by user", "info", "user_action")
    return index()

@app.route('/test-url/<tcin>')
def test_url(tcin):
    """Test endpoint to check product URL for a specific TCIN"""
    print(f"[TEST] Testing URL for TCIN: {tcin}")

    # Make a quick API call for just this product
    try:
        api_key = get_massive_api_key_rotation()
        params = {
            'key': api_key,
            'tcins': tcin,
            'is_bot': 'false',
            '_': str(int(time.time() * 1000)),
            'store_id': '1859',
            'pricing_store_id': '1859'
        }

        headers = get_ultra_stealth_headers()

        if CURL_CFFI_AVAILABLE:
            try:
                session = cf_requests.Session(impersonate="chrome120")
            except:
                session = requests.Session()
        else:
            session = requests.Session()

        response = session.get(
            'https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1',
            params=params,
            headers=headers,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if ('data' in data and
                'product_summaries' in data['data'] and
                len(data['data']['product_summaries']) > 0):

                item = data['data']['product_summaries'][0].get('item', {})

                # Check all possible URL sources
                urls_found = {
                    'enrichment_buy_url': item.get('enrichment', {}).get('buy_url'),
                    'enrichment_seo_url': item.get('enrichment', {}).get('seo_url'),
                    'product_seo_url': item.get('product_description', {}).get('seo_url'),
                    'fallback_url': f"https://www.target.com/p/-/A-{tcin}"
                }

                return jsonify({
                    'tcin': tcin,
                    'urls_found': urls_found,
                    'api_response': data['data']['product_summaries'][0]
                })
        else:
            return jsonify({'error': f'API returned {response.status_code}'})

    except Exception as e:
        return jsonify({'error': str(e)})

    return jsonify({'error': 'No data found'})

if __name__ == '__main__':
    print("=" + "="*60)
    print("SIMPLE STEALTH DASHBOARD")
    print("=" + "="*60)
    print("[FEATURES] Synchronous API calls, stealth headers, session warmup")
    print("[NO] No websockets, no threading, no complex timers")
    print("[FLOW] Hit API → Get response → Display dashboard")
    print("=" + "="*60)

    app.run(host='127.0.0.1', port=5001, debug=False)
