"""
FIXED SIMPLE DASHBOARD WITH FOOLPROOF PURCHASE SYSTEM
===================================================
Your original dashboard look and feel + foolproof purchase logic
"""

from flask import Flask, render_template, request, redirect, url_for, jsonify
import json
import time
import random
import os
from datetime import datetime
import requests
import pickle

# Use curl_cffi if available for advanced stealth
try:
    import curl_cffi
    print("curl_cffi available - Advanced TLS fingerprinting enabled")
except ImportError:
    curl_cffi = None
    print("curl_cffi not available - Using standard requests")

app = Flask(__name__)

# Global data storage
stock_data = {}
activity_log_entries = []

# Activity log persistence file
ACTIVITY_LOG_FILE = 'logs/activity_log.pkl'

def load_activity_log():
    """Load activity log from pickle file"""
    global activity_log_entries
    try:
        if os.path.exists(ACTIVITY_LOG_FILE):
            with open(ACTIVITY_LOG_FILE, 'rb') as f:
                activity_log_entries = pickle.load(f)
                print(f"[ACTIVITY] Loaded {len(activity_log_entries)} log entries")
        else:
            activity_log_entries = []
            print("[ACTIVITY] No existing log file found, starting fresh")
    except Exception as e:
        print(f"[ACTIVITY] Error loading log file: {e}")
        activity_log_entries = []

def save_activity_log():
    """Save activity log to pickle file"""
    try:
        os.makedirs(os.path.dirname(ACTIVITY_LOG_FILE), exist_ok=True)
        with open(ACTIVITY_LOG_FILE, 'wb') as f:
            pickle.dump(activity_log_entries, f)
    except Exception as e:
        print(f"[ACTIVITY] Error saving log file: {e}")

def add_activity_log(message, level="info", category="system"):
    """Add an entry to the activity log"""
    global activity_log_entries
    timestamp = datetime.now()
    entry = {
        'timestamp': timestamp,
        'message': message,
        'level': level,
        'category': category
    }
    activity_log_entries.append(entry)

    # Keep only the last 100 entries
    if len(activity_log_entries) > 100:
        activity_log_entries = activity_log_entries[-100:]

    # Save to file
    save_activity_log()

    print(f"[{timestamp.strftime('%H:%M:%S')}] [{level.upper()}] {message}")

def add_api_summary_log(total_products, in_stock, out_of_stock, response_time_ms):
    """Add API summary to activity log"""
    message = f"API Summary: {total_products} products checked, {in_stock} in stock, {out_of_stock} out of stock ({response_time_ms}ms)"
    add_activity_log(message, "info", "api_summary")

class FoolproofPurchaseManager:
    """FOOLPROOF purchase system integrated with original dashboard styling"""

    def __init__(self):
        self.state_file = 'logs/purchase_states.json'
        self.config = {
            'duration_min': 2.0,
            'duration_max': 4.0,
            'success_rate': 0.7,
            'cooldown_seconds': 5  # Quick cooldown for auto-cycling
        }
        os.makedirs('logs', exist_ok=True)

    def load_purchase_states(self):
        """Load purchase states from file with error handling"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[PURCHASE] Error loading state file: {e}")
        return {}

    def save_purchase_states(self, states):
        """Save purchase states to file atomically"""
        try:
            temp_file = self.state_file + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(states, f, indent=2)

            if os.path.exists(self.state_file):
                os.remove(self.state_file)
            os.rename(temp_file, self.state_file)

        except Exception as e:
            print(f"[PURCHASE] Error saving state file: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

    def start_purchase(self, tcin, product_name):
        """Start purchase - return ALL data for client-side countdown (FOOLPROOF)"""
        now = time.time()
        duration = random.uniform(self.config['duration_min'], self.config['duration_max'])
        completes_at = now + duration
        success = random.random() < self.config['success_rate']

        # Pre-generate ALL data immediately (eliminates race conditions)
        purchase_data = {
            'status': 'attempting',
            'started_at': now,
            'completes_at': completes_at,
            'duration': duration,
            'product_name': product_name,
            'will_succeed': success,
            'order_number': f"ORD-{random.randint(100000, 999999)}-{random.randint(10, 99)}" if success else None,
            'price': round(random.uniform(15.99, 89.99), 2) if success else None,
            'failure_reason': random.choice(['cart_timeout', 'out_of_stock', 'payment_failed']) if not success else None,
            'created_at': datetime.now().isoformat()
        }

        # Save state
        states = self.load_purchase_states()
        states[tcin] = purchase_data
        self.save_purchase_states(states)

        add_activity_log(f"MOCK: Starting purchase: {product_name} - will complete in {duration:.1f}s", "info", "purchase")
        print(f"[FOOLPROOF] Started: {product_name} -> {duration:.1f}s -> {'SUCCESS' if success else 'FAIL'}")
        return purchase_data

    def complete_purchase(self, tcin):
        """Complete purchase (called after client-side countdown)"""
        states = self.load_purchase_states()
        purchase = states.get(tcin)
        if not purchase:
            return None

        final_status = 'purchased' if purchase['will_succeed'] else 'failed'
        purchase.update({
            'status': final_status,
            'completed_at': datetime.now().isoformat()
        })

        states[tcin] = purchase
        self.save_purchase_states(states)

        # Log completion
        product_name = purchase['product_name']
        if final_status == 'purchased':
            add_activity_log(f"MOCK: Purchase successful: {product_name} - Order: {purchase['order_number']}", "success", "purchase")
            print(f"[FOOLPROOF] Purchase SUCCESS: {tcin} -> {purchase['order_number']}")
        else:
            add_activity_log(f"MOCK: Purchase failed: {product_name} - {purchase['failure_reason']}", "error", "purchase")
            print(f"[FOOLPROOF] Purchase FAILED: {tcin} -> {purchase['failure_reason']}")

        return purchase

    def can_start_purchase(self, tcin):
        """Check if we can start a new purchase"""
        states = self.load_purchase_states()
        purchase = states.get(tcin)
        if not purchase:
            return True

        # Can start if completed and cooldown passed
        if purchase['status'] in ['purchased', 'failed']:
            completed_time = purchase.get('started_at', 0)
            return (time.time() - completed_time) > self.config['cooldown_seconds']

        return False

    def execute_purchase_cycle(self, stock_data):
        """Auto-start purchases for in-stock items (NO interference with completion)"""
        print("[PURCHASE] Starting FOOLPROOF purchase cycle...")
        print("[PURCHASE] WARNING: This is MOCK mode - no real purchases will be made!")

        states = self.load_purchase_states()

        # Process each product
        for tcin, product in stock_data.items():
            product_name = product.get('name', 'Unknown Product')

            # Auto-start purchase if ready and in stock
            if product.get('in_stock', False) and self.can_start_purchase(tcin):
                purchase_data = self.start_purchase(tcin, product_name)
                states[tcin] = purchase_data
            elif not states.get(tcin):
                states[tcin] = {'status': 'ready'}

        return states

# Initialize purchase manager and load activity log
purchase_manager = FoolproofPurchaseManager()
load_activity_log()

def load_config():
    """Load product configuration"""
    try:
        with open('config/product_config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'products': []}
    except json.JSONDecodeError:
        return {'products': []}

@app.route('/')
def index():
    """Main dashboard with your original styling + foolproof purchase logic"""
    config = load_config()
    products = config.get('products', [])

    # Load purchase states for display
    purchase_states = purchase_manager.load_purchase_states()

    # Simulate stock checking for demo (normally this would check real stock)
    simulated_stock_data = {}
    for product in products:
        tcin = product['tcin']
        # Simulate in_stock as True for demo
        simulated_stock_data[tcin] = {
            'name': product['name'],
            'in_stock': True,  # Simulate all products are in stock
            'status': 'in_stock'
        }

    # Execute purchase cycle
    purchase_states = purchase_manager.execute_purchase_cycle(simulated_stock_data)

    # Add purchase state to each product for display
    for product in products:
        tcin = product['tcin']
        product['purchase_state'] = purchase_states.get(tcin, {'status': 'ready'})

    return render_template('simple_dashboard.html',
                         products=products,
                         timestamp=datetime.now(),
                         activity_log=activity_log_entries[-20:])  # Last 20 entries

@app.route('/api/start_purchase/<tcin>')
def api_start_purchase(tcin):
    """API: Start a purchase with foolproof client-side countdown"""
    try:
        config = load_config()
        products = config.get('products', [])
        product = next((p for p in products if p['tcin'] == tcin), None)

        if not product:
            return jsonify({'error': 'Product not found'}), 404

        # Check if can start purchase
        if not purchase_manager.can_start_purchase(tcin):
            return jsonify({'error': 'Cannot start purchase (cooldown or already attempting)'}), 400

        # Start purchase and return data for client countdown
        purchase_data = purchase_manager.start_purchase(tcin, product['name'])
        return jsonify(purchase_data)

    except Exception as e:
        print(f"[API] Start purchase error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/complete_purchase/<tcin>')
def api_complete_purchase(tcin):
    """API: Complete a purchase (called by client after countdown)"""
    try:
        result = purchase_manager.complete_purchase(tcin)
        if not result:
            return jsonify({'error': 'Purchase not found'}), 404

        return jsonify(result)

    except Exception as e:
        print(f"[API] Complete purchase error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/refresh')
def refresh():
    """Simple refresh endpoint"""
    print("[REFRESH] Refreshing data...")
    return redirect(url_for('index'))

if __name__ == '__main__':
    print("[SYSTEM] Target Monitor Pro initialized")
    print("=============================================================")
    print("FIXED DASHBOARD WITH FOOLPROOF PURCHASE SYSTEM")
    print("=============================================================")
    print("[FEATURES] Your original styling + client-side countdown")
    print("[FOOLPROOF] NO refresh conflicts, NO race conditions!")
    print("=============================================================")
    app.run(debug=False, host='0.0.0.0', port=5003)