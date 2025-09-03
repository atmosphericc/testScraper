#!/usr/bin/env python3
"""
TEMPORARY: Ultra-Fast Dashboard with Mock Data
This version uses mock data to show the dashboard working while we debug the Target API
"""

import json
import time
import random
import threading
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

# Initialize Flask app
app = Flask(__name__, template_folder='dashboard/templates')
CORS(app)
app.secret_key = 'ultra-fast-stealth-2025'

# Mock analytics tracking
class MockAnalytics:
    def __init__(self):
        self.analytics_data = {
            'total_checks': 0,
            'success_rate': 98.5,
            'average_response_time': 250,
            'active_sessions': 1
        }
        self.last_check_time = None
        self.update_lock = threading.Lock()
        
    def record_check(self, response_times, success_count, total_count):
        with self.update_lock:
            self.analytics_data['total_checks'] += total_count
            if response_times:
                self.analytics_data['average_response_time'] = sum(response_times) / len(response_times)
            if total_count > 0:
                self.analytics_data['success_rate'] = (success_count / total_count) * 100
            self.last_check_time = datetime.now()

# Global analytics instance
ultra_analytics = MockAnalytics()

def get_config():
    """Load configuration with fallback paths"""
    possible_paths = [
        "config/product_config.json",
        "dashboard/../config/product_config.json",
        Path(__file__).parent / "config" / "product_config.json"
    ]
    
    for path in possible_paths:
        if Path(path).exists():
            with open(path, 'r') as f:
                return json.load(f)
    
    return {"products": []}

def create_mock_stock_data():
    """Create realistic mock stock data that changes over time"""
    config = get_config()
    products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    if not products:
        return {}
    
    results = {}
    
    # Pokemon card names for realistic data
    pokemon_names = [
        "Pokémon Trading Card Game: Blooming Waters Premium Collection",
        "Pokémon Trading Card Game: Scarlet & Violet—White Flare Booster Bundle", 
        "Pokémon Trading Card Game: Scarlet & Violet—Black Bolt Booster Bundle",
        "Pokémon Trading Card Game: Scarlet & Violet—Prismatic Evolutions Surprise Box",
        "Pokémon Trading Card Game: Quaquaval ex Deluxe Battle Deck"
    ]
    
    prices = [69.99, 31.99, 31.99, 24.99, 14.99]
    
    for i, product in enumerate(products):
        tcin = product['tcin']
        
        # Randomly determine if in stock (20% chance for realistic scarcity)
        available = random.random() < 0.2
        
        # Simulate realistic response times
        response_time = random.randint(120, 350)
        
        # Rotate through realistic Pokemon names
        name = pokemon_names[i % len(pokemon_names)]
        price = prices[i % len(prices)]
        
        results[tcin] = {
            'available': available,
            'status': 'IN_STOCK' if available else 'OUT_OF_STOCK',
            'name': name,
            'tcin': tcin,
            'last_checked': datetime.now().isoformat(),
            'quantity': random.randint(0, 5) if available else 0,
            'availability_status': 'IN_STOCK' if available else 'UNAVAILABLE',
            'sold_out': not available,
            'price': price,
            'response_time': response_time,
            'confidence': 'high',
            'method': 'ultra_stealth_api'
        }
    
    return results

def background_stock_monitor():
    """Background thread that updates mock stock data every 30 seconds"""
    print("🔄 Starting background stock monitor for 30s refresh cycle...")
    
    while True:
        try:
            print("📊 Background refresh: Generating fresh mock data...")
            stock_data = create_mock_stock_data()
            
            # Update latest data
            update_latest_data(stock_data)
            
            # Update analytics
            response_times = [r.get('response_time', 0) for r in stock_data.values()]
            success_count = len(stock_data)  # All mock requests "succeed"
            ultra_analytics.record_check(response_times, success_count, len(stock_data))
            
            in_stock_count = sum(1 for r in stock_data.values() if r.get('available'))
            print(f"✅ Background refresh completed - {len(stock_data)} products checked ({in_stock_count} in stock)")
            
            # Wait exactly 30 seconds before next check
            time.sleep(30)
            
        except Exception as e:
            print(f"❌ Background monitor error: {e}")
            time.sleep(10)

# Perform initial stock check to have data ready
print("🚀 Generating initial mock stock data for immediate availability...")
initial_data = create_mock_stock_data()
response_times = [r.get('response_time', 0) for r in initial_data.values()]
success_count = len(initial_data)
ultra_analytics.record_check(response_times, success_count, len(initial_data))
in_stock_count = sum(1 for r in initial_data.values() if r.get('available'))
print(f"✅ Initial mock data ready - {len(initial_data)} products checked ({in_stock_count} in stock)")

# Store latest data for immediate serving
latest_stock_data = initial_data
latest_data_lock = threading.Lock()

def update_latest_data(data):
    global latest_stock_data
    with latest_data_lock:
        latest_stock_data = data

def get_latest_data():
    with latest_data_lock:
        return latest_stock_data.copy()

# Start the background monitor
monitor_thread = threading.Thread(target=background_stock_monitor, daemon=True)
monitor_thread.start()

@app.route('/')
def index():
    """Beautiful dashboard with mock data"""
    config = get_config()
    timestamp = datetime.now()
    
    # Add product URLs
    for product in config.get('products', []):
        tcin = product.get('tcin')
        if tcin:
            product['url'] = f"https://www.target.com/p/-/A-{tcin}"
    
    # Create status structure expected by template
    status = {
        'monitoring': True,
        'total_checks': ultra_analytics.analytics_data['total_checks'],
        'in_stock_count': sum(1 for r in get_latest_data().values() if r.get('available')),
        'last_update': ultra_analytics.last_check_time.isoformat() if ultra_analytics.last_check_time else timestamp.isoformat(),
        'recent_stock': [],
        'recent_purchases': [],
        'timestamp': timestamp.isoformat()
    }
    
    return render_template('dashboard.html',
                         config=config,
                         status=status,
                         timestamp=timestamp)

@app.route('/api/live-stock-status')
def api_live_stock_status():
    """Instant stock status from mock data"""
    print("📊 Serving latest mock stock data from background refresh...")
    stock_data = get_latest_data()
    return jsonify(stock_data)

@app.route('/api/initial-stock-check')
def api_initial_stock_check():
    """Initial stock check - serves mock data in expected format"""
    print("🚀 Serving initial mock stock data in wrapped format...")
    stock_data = get_latest_data()
    
    # Convert to the format expected by JavaScript: {success: true, products: [...]}
    products_array = []
    for tcin, data in stock_data.items():
        products_array.append(data)
    
    wrapped_response = {
        'success': True,
        'products': products_array,
        'timestamp': datetime.now().isoformat()
    }
    
    return jsonify(wrapped_response)

@app.route('/api/live-stock-check')
def api_live_stock_check():
    """Live stock check - serves mock data"""
    print("🔄 Serving live mock stock data from 30s background refresh...")
    return api_live_stock_status()

@app.route('/api/status')
def api_status():
    """Enhanced system status with stealth metrics in expected format"""
    config = get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    return jsonify({
        'monitoring': True,  # JavaScript expects 'monitoring' not 'monitoring_active'
        'total_products': len(enabled_products),
        'last_check': ultra_analytics.last_check_time.isoformat() if ultra_analytics.last_check_time else None,
        'system_status': 'running',
        'stealth_mode': 'maximum',
        'features': {
            'user_agents': '50+',
            'api_keys': '30+',
            'header_rotation': 'advanced',
            'rate_limiting': 'intelligent',
            'real_time': 'enabled'
        },
        'data_mode': 'mock_data_for_testing'
    })

@app.route('/api/analytics')
def api_analytics():
    """Enhanced analytics with mock performance metrics in expected format"""
    config = get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    stock_data = get_latest_data()
    in_stock_count = sum(1 for r in stock_data.values() if r.get('available'))
    
    return jsonify({
        'stock_analytics': {
            'total_checks_24h': len(enabled_products),  # Show total products being monitored
            'in_stock_found_24h': in_stock_count,       # Current in-stock count
            'avg_response_time': round(ultra_analytics.analytics_data['average_response_time'])  # Response time in ms
        },
        'purchase_analytics': {
            'success_rate': round(ultra_analytics.analytics_data['success_rate'], 1)  # Success rate percentage
        },
        'stealth_metrics': {
            'user_agent_pool': '50+ rotating',
            'api_key_pool': '30+ rotating',
            'header_variations': '15+ per request',
            'rate_limit_compliance': 'intelligent',
            'detection_avoidance': 'maximum',
            'data_mode': 'mock_data_for_testing'
        },
        'system_features': {
            'total_products': len(enabled_products),
            'current_in_stock': in_stock_count,
            'stealth_mode': 'maximum',
            'real_time_accuracy': 'mock_demonstration'
        }
    })

if __name__ == '__main__':
    config = get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    print("🎯" + "="*80)
    print("🚀 ULTRA-FAST DASHBOARD WITH MOCK DATA (TEMPORARY)")
    print("🎯" + "="*80)
    print(f"🎨 Beautiful Dashboard: Original UI with all metrics")
    print(f"🎯 Products: {len(enabled_products)} enabled (using mock data)")
    print(f"⚡ Initial Load: Mock data pre-loaded for instant availability")
    print(f"🔄 Refresh Cycle: Mock updates every 30 seconds")
    print(f"🌐 Mock Features: Rotating stock status, realistic response times")
    print(f"🔐 Purpose: Demonstrate dashboard UI while debugging Target API")
    print(f"📡 Note: This shows how the system will work once API is fixed")
    print(f"⏱️  Flow: Initial load → 30s refresh → page updates after response")
    print(f"🎯" + "="*80)
    print(f"🌍 Dashboard: http://localhost:5001")
    print(f"📊 Features: Full UI demonstration with mock Pokemon card data")
    print(f"🎯" + "="*80)
    
    app.run(host='127.0.0.1', port=5001, debug=False, threaded=True)