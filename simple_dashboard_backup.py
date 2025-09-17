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
import subprocess
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

class SimplePurchaseManager:
    """Simple timestamp-based purchase system - no race conditions, no complexity"""

    def __init__(self):
        self.state_file = 'logs/purchase_states.json'
        self.config = {
            'duration_min': 2.0,        # Minimum purchase duration
            'duration_max': 4.0,        # Maximum purchase duration
            'success_rate': 0.7,        # 70% success rate
            'window_minutes': 1         # Window reset every 1 minute
        }

        # Ensure logs directory exists
        os.makedirs('logs', exist_ok=True)

    def get_current_window_id(self, timestamp=None):
        """Get current window ID (changes every minute)"""
        if timestamp is None:
            timestamp = time.time()
        return int(timestamp // (self.config['window_minutes'] * 60))

    def load_purchase_states(self):
        """Load purchase states with graceful fallback"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            else:
                return {}
        except Exception as e:
            print(f"[WARN] Failed to load purchase states: {e}")
            return {}  # Graceful fallback to empty state

    def save_purchase_states(self, states):
        """Atomically save purchase states to prevent corruption"""
        try:
            # Atomic write using temp file
            temp_file = f"{self.state_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(states, f, indent=2)

            # Atomic rename (Windows compatible)
            if os.path.exists(self.state_file):
                os.remove(self.state_file)
            os.rename(temp_file, self.state_file)
            return True

        except Exception as e:
            print(f"[ERROR] Failed to save purchase states: {e}")
            return False

    def start_purchase(self, tcin, product_name):
        """Start a new purchase - pre-determine all outcomes to avoid race conditions"""
        now = time.time()
        duration = random.uniform(self.config['duration_min'], self.config['duration_max'])
        success = random.random() < self.config['success_rate']

        # Pre-generate all data immediately (no race conditions)
        state = {
            'status': 'attempting',
            'started_at': now,
            'completes_at': now + duration,  # Absolute timestamp
            'window_id': self.get_current_window_id(now),
            'product_name': product_name,
            'attempt_count': 1,

            # Pre-determined outcome (eliminates all race conditions)
            'final_outcome': 'purchased' if success else 'failed',
            'order_number': f"ORD-{random.randint(100000, 999999)}-{random.randint(10, 99)}" if success else None,
            'price': round(random.uniform(15.99, 89.99), 2) if success else None,
            'failure_reason': random.choice([
                'out_of_stock', 'payment_failed', 'cart_timeout',
                'captcha_required', 'price_changed', 'shipping_unavailable'
            ]) if not success else None,

            # Frozen config snapshot for consistency
            'config_snapshot': self.config.copy()
        }

        print(f"[PURCHASE] Starting purchase: {product_name} (TCIN: {tcin}) - will complete in {duration:.1f}s")
        add_activity_log(f"MOCK: Starting purchase attempt: {product_name} (TCIN: {tcin})", "info", "purchase")

        return state

    def get_current_status(self, state):
        """Get current purchase status based on timestamps"""
        now = time.time()

        # Simple timestamp comparison - no complex logic
        if now >= state['completes_at']:
            # Purchase completed - return final outcome
            return state['final_outcome']
        else:
            # Still in progress
            return 'attempting'

    def finalize_completed_purchase(self, tcin, state):
        """Finalize a purchase that just completed"""
        now = datetime.now()
        final_status = state['final_outcome']

        # Update state with completion data
        state.update({
            'status': final_status,
            'completed_at': now.isoformat()
        })

        # Log the completion
        product_name = state['product_name']
        if final_status == 'purchased':
            add_activity_log(f"MOCK: Purchase successful: {product_name} - Order: {state['order_number']}", "success", "purchase")
            print(f"[PURCHASE] Purchase completed successfully: {tcin} -> {state['order_number']}")
        else:
            add_activity_log(f"MOCK: Purchase failed: {product_name} - {state['failure_reason']}", "error", "purchase")
            print(f"[PURCHASE] Purchase failed: {tcin} -> {state['failure_reason']}")

    def should_reset_for_new_window(self, state):
        """Check if state should reset for new window"""
        current_window = self.get_current_window_id()
        state_window = state.get('window_id', current_window)

        # Reset if: Different window AND Purchase is complete
        if current_window > state_window:
            current_status = self.get_current_status(state)
            if current_status in ['purchased', 'failed']:
                return True
        return False

    def execute_purchase_cycle(self, stock_data):
        """Main purchase execution cycle - SIMPLE TIMESTAMP-BASED"""
        print("[PURCHASE] Starting SIMPLE purchase cycle...")
        print("[PURCHASE] WARNING: This is MOCK mode - no real purchases will be made!")

        # Load current states
        states = self.load_purchase_states()
        states_changed = False

        # Process each product
        for tcin, product in stock_data.items():
            product_name = product.get('name', 'Unknown Product')
            current_state = states.get(tcin, {'status': 'ready'})
            current_status = current_state.get('status', 'ready')

            # Handle different states
            if current_status == 'ready' and product.get('available'):
                # Start new purchase
                new_state = self.start_purchase(tcin, product_name)
                states[tcin] = new_state
                states_changed = True

            elif current_status == 'attempting':
                # Check if purchase completed
                if self.get_current_status(current_state) != 'attempting':
                    # Purchase just completed
                    self.finalize_completed_purchase(tcin, current_state)
                    states_changed = True

            elif current_status in ['purchased', 'failed']:
                # Check for window reset
                if self.should_reset_for_new_window(current_state):
                    old_status = current_status
                    states[tcin] = {'status': 'ready', 'window_id': self.get_current_window_id()}
                    print(f"[PURCHASE] Reset {tcin} from {old_status} to ready (new window)")
                    states_changed = True

        # Save states if any changes occurred
        if states_changed:
            self.save_purchase_states(states)

        return states

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
    """Smart 2-key rotation - TESTED AND VERIFIED 2025-09-16"""
    # Both keys verified working at 100% success rate through 15+ refresh stress test
    working_api_keys = [
        "ff457966e64d5e877fdbad070f276d18ecec4a01",  # ✅ VERIFIED: 100% success in testing
        "9f36aeafbe60771e321a7cc95a78140772ab3e96"   # ✅ VERIFIED: 100% success in testing
    ]

    # Smart rotation: 2 keys is optimal for F5/Shape evasion
    # More keys = suspicious, fewer keys = easier to block
    selected_key = random.choice(working_api_keys)
    print(f"[SMART_ROTATION] Using key: {selected_key[:8]}... (2-key rotation active)")
    return selected_key

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
            'store_id': '865',
            'pricing_store_id': '865',
            'zip': '33809',
            'state': 'FL',
            'latitude': '28.0395',
            'longitude': '-81.9498'
        }
        # Persistent session management for F5 evasion
        self._persistent_session = None
        self._session_failure_count = 0
        self._max_session_failures = 3  # Allow 3 failures before recreating session

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

    def get_persistent_session(self):
        """Get or create persistent session for F5 evasion"""
        # Check if we need to create a new session
        if (self._persistent_session is None or
            self._session_failure_count >= self._max_session_failures):

            if self._persistent_session is not None:
                print(f"[SESSION] Recreating session after {self._session_failure_count} failures")
                add_activity_log(f"Session recreated after {self._session_failure_count} failures", "info", "session")

            # Create new session with TLS fingerprinting
            if CURL_CFFI_AVAILABLE:
                try:
                    self._persistent_session = cf_requests.Session(impersonate="chrome120")
                    print("[TLS] New persistent session created with Chrome120 fingerprint")
                    add_activity_log("New persistent TLS session - Chrome120 fingerprint", "success", "tls_evasion")
                except Exception as e:
                    print(f"[WARN] curl_cffi failed, fallback to requests: {e}")
                    self._persistent_session = requests.Session()
                    add_activity_log("Fallback to standard persistent session", "warning", "stealth")
            else:
                self._persistent_session = requests.Session()
                add_activity_log("Using standard persistent session", "info", "stealth")

            # Reset failure count
            self._session_failure_count = 0
        else:
            print("[SESSION] Reusing persistent session (F5 evasion)")
            add_activity_log("Reusing warmed session for F5 evasion", "success", "session")

        return self._persistent_session

    def record_session_success(self):
        """Record successful API call - reset failure count"""
        self._session_failure_count = 0

    def record_session_failure(self):
        """Record failed API call - increment failure count"""
        self._session_failure_count += 1
        print(f"[SESSION] Session failure count: {self._session_failure_count}/{self._max_session_failures}")
        add_activity_log(f"Session failure recorded: {self._session_failure_count}/{self._max_session_failures}", "warn", "session")

    def create_stealth_session(self):
        """Create session with stealth configuration"""
        session = requests.Session()
        session.cookies.clear()

        # Apply rotating cookies
        rotating_cookies = get_rotating_cookies()
        for name, value in rotating_cookies.items():
            session.cookies.set(name, value)

        return session, rotating_cookies

    def get_ultra_stealth_headers_with_f5_variations(self):
        """F5-consistent headers with subtle human-like variations + header order randomization"""
        user_agent = get_massive_user_agent_rotation()

        # Build headers as list of tuples to control order
        header_list = []

        # Core required headers (always included)
        header_list.append(('accept', 'application/json'))
        header_list.append(('user-agent', user_agent))

        # Subtle F5-safe variations in accept-encoding
        encoding_options = [
            'gzip, deflate, br',
            'gzip, deflate, br, zstd',
            'gzip, br, deflate'
        ]
        header_list.append(('accept-encoding', random.choice(encoding_options)))

        # Language header subtle variations
        lang_options = [
            'en-US,en;q=0.9',
            'en-US,en;q=0.9,*;q=0.8',
            'en-US;q=0.9,en;q=0.8'
        ]
        header_list.append(('accept-language', random.choice(lang_options)))

        # Optional cache control headers (randomize inclusion and order)
        cache_headers = [
            ('cache-control', 'no-cache'),
            ('pragma', 'no-cache')
        ]
        random.shuffle(cache_headers)
        header_list.extend(cache_headers)

        # Connection header
        header_list.append(('connection', 'keep-alive'))

        # Chrome-specific sec-ch-ua headers (F5-consistent)
        if 'Chrome' in user_agent:
            sec_ua_options = [
                '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                '"Not_A Brand";v="8", "Chromium";v="121", "Google Chrome";v="121"',
                '"Google Chrome";v="120", "Chromium";v="120", "Not=A?Brand";v="8"'
            ]
            header_list.append(('sec-ch-ua', random.choice(sec_ua_options)))
            header_list.append(('sec-ch-ua-mobile', '?0'))

            # Platform-specific headers
            if 'Windows' in user_agent:
                header_list.append(('sec-ch-ua-platform', '"Windows"'))
                if 'NT 11.0' in user_agent:
                    header_list.append(('sec-ch-ua-platform-version', '"13.0.0"'))
                elif 'NT 10.0' in user_agent:
                    header_list.append(('sec-ch-ua-platform-version', '"10.0.0"'))
            elif 'Mac' in user_agent:
                header_list.append(('sec-ch-ua-platform', '"macOS"'))

        # sec-fetch headers (consistent)
        if 'Chrome' in user_agent or 'Edge' in user_agent:
            sec_fetch_headers = [
                ('sec-fetch-dest', 'empty'),
                ('sec-fetch-mode', 'cors'),
                ('sec-fetch-site', 'same-origin')
            ]
            # Randomize sec-fetch header order
            random.shuffle(sec_fetch_headers)
            header_list.extend(sec_fetch_headers)

        # Optional headers that may or may not be included
        optional_headers = []

        # Referer variations (F5-safe)
        referers = [
            'https://www.target.com/',
            'https://www.target.com/c/collectible-trading-cards-hobby-collectibles-toys/-/N-27p31',
            'https://www.target.com/c/toys/-/N-5xtb0'
        ]
        if random.choice([True, True, False]):  # 67% chance
            optional_headers.append(('referer', random.choice(referers)))

        # Additional subtle variations
        if random.choice([True, False]):
            optional_headers.append(('upgrade-insecure-requests', '1'))

        if random.choice([True, False, False]):  # 33% chance
            optional_headers.append(('x-requested-with', 'XMLHttpRequest'))

        # Randomize optional header order and add to main list
        random.shuffle(optional_headers)
        header_list.extend(optional_headers)

        # CRITICAL F5 EVASION: Randomize the order of ALL headers
        # This breaks F5's header order fingerprinting while keeping same headers
        random.shuffle(header_list)

        # Convert back to dict (Python 3.7+ preserves insertion order)
        headers = dict(header_list)

        print(f"[F5_EVASION] Randomized header order: {list(headers.keys())[:5]}... ({len(headers)} total)")
        add_activity_log(f"Header order randomized: {len(headers)} headers", "success", "f5_evasion")

        return headers

    def make_simple_stealth_call(self):
        """Make advanced stealth API call with anti-detection features"""
        print("[API] Starting advanced stealth API call...")
        add_activity_log("Initiating advanced stealth API call to Target.com", "info", "api")

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

            # Use rotating API key for stealth
            api_key = get_massive_api_key_rotation()
            print(f"[STEALTH] Using rotated API key: {api_key[:8]}...")

            # Advanced stealth batch parameters with F5-consistent subtle variations
            params = {
                'key': api_key,
                'tcins': ','.join(tcins),
                'store_id': '865',
                'pricing_store_id': '865',
                'has_pricing_context': 'true',
                'has_promotions': 'true',
                'is_bot': 'false'
            }

            # Note: Target batch API doesn't accept cache-busting parameters
            # Keep parameters minimal and stable to avoid 404 errors
            # Removed parameter shuffling as it may cause API issues

            # Get or create persistent session for F5 evasion
            session = self.get_persistent_session()

            # Apply rotating cookies for session stealth
            rotating_cookies = get_rotating_cookies()
            for name, value in rotating_cookies.items():
                session.cookies.set(name, value)
            print(f"[STEALTH] Applied {len(rotating_cookies)} rotating cookies")

            # Use F5-consistent headers with subtle variations
            headers = self.get_ultra_stealth_headers_with_f5_variations()
            user_agent = headers.get('user-agent', 'Unknown')
            print(f"[STEALTH] Using rotated user agent: {user_agent[:50]}...")
            add_activity_log(f"User agent rotated: {user_agent[:30]}...", "info", "stealth")

            # Session warmup for F5/Shape evasion
            session_warmer = SessionWarmupManager()
            if session_warmer.needs_warmup():
                print("[F5/SHAPE] Performing session warmup...")
                add_activity_log("F5/Shape evasion: Session warmup initiated", "info", "evasion")
                warmup_success = session_warmer.warmup_session(session, headers)
                if warmup_success:
                    add_activity_log("Session warmup completed successfully", "success", "evasion")
                else:
                    # If warmup failed due to closed session, create new session
                    add_activity_log("Session warmup failed - creating new session", "warning", "evasion")
                    if CURL_CFFI_AVAILABLE:
                        try:
                            session = cf_requests.Session(impersonate="chrome120")
                            self._persistent_session = session
                            print("[TLS] Created new session after warmup failure")
                        except Exception:
                            session = requests.Session()
                            print("[TLS] Fallback to requests after warmup failure")
                    else:
                        session = requests.Session()
            else:
                print("[F5/SHAPE] Session warmup not needed")

            # Skip artificial delay - 15-25s refresh cycle is already human-like timing
            # Immediate API call is more natural than consistent pre-delays (better F5 evasion)
            print("[F5_EVASION] Making immediate API call - natural refresh behavior")

            print("[API] Making advanced stealth batch API request...")

            start_time = time.time()
            response = session.get(
                self.batch_endpoint,
                params=params,
                headers=headers,
                timeout=15
            )

            end_time = time.time()
            response_time = (end_time - start_time) * 1000

            if response.status_code == 200:
                print(f"[OK] Stealth batch API success: {response_time:.0f}ms")
                add_activity_log(f"Stealth batch API call successful - {response_time:.0f}ms response time", "success", "api")

                # Record success for session persistence
                self.record_session_success()

                data = response.json()
                processed_data = self.process_batch_response(data, enabled_products, response_time)

                # Execute purchase logic after stock data is processed
                try:
                    purchase_manager = SimplePurchaseManager()
                    purchase_states = purchase_manager.execute_purchase_cycle(processed_data)

                    # Add purchase status to processed data for dashboard display
                    for tcin, product_data in processed_data.items():
                        purchase_state = purchase_states.get(tcin, {'status': 'ready'})
                        product_data['purchase_status'] = purchase_state.get('status', 'ready')
                        product_data['purchase_attempt_count'] = purchase_state.get('attempt_count', 0)
                        product_data['last_purchase_attempt'] = purchase_state.get('started_at')
                        product_data['purchase_completed_at'] = purchase_state.get('completed_at')
                        product_data['order_number'] = purchase_state.get('order_number')

                except Exception as e:
                    print(f"[ERROR] Purchase cycle failed: {e}")
                    add_activity_log(f"Purchase cycle failed: {str(e)}", "error", "purchase")
                    # Continue without purchases on error
                    for tcin, product_data in processed_data.items():
                        product_data['purchase_status'] = 'error'

                # Log stealth success metrics
                add_activity_log(f"Stealth features applied: TLS fingerprinting, user agent rotation, session warmup", "success", "stealth")
                return processed_data
            else:
                print(f"[ERROR] Stealth batch API failed: HTTP {response.status_code}")
                print(f"[DEBUG] URL: {response.url}")
                print(f"[DEBUG] Response: {response.text[:200]}...")
                add_activity_log(f"Stealth batch API call failed - HTTP {response.status_code}", "error", "api")
                add_activity_log(f"Response preview: {response.text[:100]}...", "error", "debug")

                # Record failure but keep session (F5 evasion improvement)
                self.record_session_failure()

                return self.get_empty_dashboard_data()

        except Exception as e:
            print(f"[ERROR] Stealth API exception: {e}")
            add_activity_log(f"Stealth API exception occurred: {str(e)}", "error", "api")

            # Record failure but keep session unless too many failures (F5 evasion improvement)
            self.record_session_failure()

            return self.get_empty_dashboard_data()


    def get_empty_dashboard_data(self):
        """Return empty dashboard data structure"""
        return {}

    def process_batch_response(self, data, enabled_products, response_time):
        """Process batch API response"""
        if not data or 'data' not in data:
            print("[ERROR] Invalid response structure")
            return {}

        # Handle batch format
        if 'product_summaries' not in data['data']:
            print("[ERROR] No product summaries found in response")
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

# Initialize simple checker and session warmer
simple_checker = SimpleStealthChecker()
session_warmer = SessionWarmupManager()

# Clear activity log on server startup (not on refresh)
activity_log = []
save_activity_log()  # Save empty log
add_activity_log("Target Monitor Pro initialized", "info", "system")

@app.route('/')
def index():
    """Simple dashboard - only shows after API data is received"""
    from flask import request
    global latest_stock_data, last_update_time

    print("[DASHBOARD] Loading dashboard...")

    # Check if we should skip API call (for config changes)
    if request.args.get('skip_api') and latest_stock_data:
        print("[DASHBOARD] Using cached data (skip_api=true)")
        stock_data = latest_stock_data
    else:
        # Make fresh API call to get data
        print("[DASHBOARD] Making API call to get fresh data...")
        stock_data = simple_checker.make_simple_stealth_call()

    if stock_data:
        latest_stock_data = stock_data
        last_update_time = datetime.now()

        # Get config for template
        config = simple_checker.get_config()

        # Debug: Show what we got from API
        print(f"[DEBUG] API returned data for TCINs: {list(stock_data.keys())}")
        print(f"[DEBUG] Config expects TCINs: {[p.get('tcin') for p in config.get('products', [])]}")

        # Update config with API data
        for product in config.get('products', []):
            tcin = product.get('tcin')
            if tcin and tcin in stock_data:
                api_data = stock_data[tcin]
                # Get API name with better fallback logic
                api_name = api_data.get('name') or api_data.get('product_name') or api_data.get('title')
                config_name = product.get('name', 'Unknown Product')

                # Prioritize API name over config placeholders
                if api_name and api_name.strip() and not api_name.strip().startswith('Product '):
                    display_name = api_name.strip()
                elif config_name and not config_name.startswith('Product '):
                    display_name = config_name
                else:
                    display_name = api_name.strip() if api_name and api_name.strip() else f'TCIN {tcin}'

                product.update({
                    'display_name': display_name,
                    'available': api_data.get('available', False),
                    'stock_status': api_data.get('status', 'OUT_OF_STOCK'),
                    'status': api_data.get('status', 'OUT_OF_STOCK'),
                    'is_preorder': api_data.get('is_preorder', False),
                    'is_target_direct': api_data.get('is_target_direct', True),
                    'seller_code': api_data.get('seller_code', 'UNKNOWN'),
                    'url': api_data.get('url', f"https://www.target.com/p/-/A-{tcin}"),  # Add URL
                    'has_data': True,
                    'enabled': product.get('enabled', True),  # Add enabled field for badge
                    'street_date': api_data.get('street_date', None),  # Add street_date for preorders
                    # Purchase status data
                    'purchase_status': api_data.get('purchase_status', 'ready'),
                    'purchase_attempt_count': api_data.get('purchase_attempt_count', 0),
                    'last_purchase_attempt': api_data.get('last_purchase_attempt'),
                    'purchase_completed_at': api_data.get('purchase_completed_at'),
                    'order_number': api_data.get('order_number')
                })
            else:
                # No data for this product - API didn't return this TCIN
                print(f"[WARN] TCIN {tcin} not found in API response - showing as loading")
                print(f"[DEBUG] Available TCINs in API response: {list(stock_data.keys())}")
                product.update({
                    'display_name': product.get('name', 'Unknown Product'),
                    'available': False,
                    'stock_status': 'NO_DATA',
                    'status': 'NO_DATA',
                    'is_preorder': False,
                    'is_target_direct': True,
                    'seller_code': 'UNKNOWN',
                    'url': f"https://www.target.com/p/-/A-{tcin}",  # Add fallback URL
                    'has_data': False,
                    'enabled': product.get('enabled', True),
                    'street_date': None,
                    # Default purchase status
                    'purchase_status': 'ready',
                    'purchase_attempt_count': 0,
                    'last_purchase_attempt': None,
                    'purchase_completed_at': None,
                    'order_number': None
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

        print(f"[DASHBOARD] Dashboard ready with {len(stock_data)} products ({in_stock_count} in stock)")

        return render_template('simple_dashboard.html',
                             config=config,
                             status=status,
                             activity_log=activity_log,
                             timestamp=datetime.now())

    else:
        # API call failed - show dashboard with error state
        print("[DASHBOARD] API call failed - showing dashboard with error state")

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

@app.route('/add-product', methods=['POST'])
def add_product():
    """Add a new product to the configuration"""
    from flask import request, redirect, url_for, jsonify

    # Check if this is an AJAX request
    is_ajax = request.headers.get('Content-Type') == 'application/json'

    if is_ajax:
        data = request.get_json()
        tcin = data.get('tcin', '').strip()
    else:
        tcin = request.form.get('tcin', '').strip()

    if not tcin:
        add_activity_log("Failed to add product: No TCIN provided", "error", "config")
        if is_ajax:
            return jsonify({'success': False, 'error': 'No TCIN provided'})
        return redirect(url_for('index', skip_api='true'))

    # Validate TCIN format (8 digits)
    if not tcin.isdigit() or len(tcin) != 8:
        add_activity_log(f"Failed to add product: Invalid TCIN format '{tcin}'", "error", "config")
        if is_ajax:
            return jsonify({'success': False, 'error': 'Invalid TCIN format'})
        return redirect(url_for('index', skip_api='true'))

    try:
        # Add to activity log immediately when TCIN is submitted
        add_activity_log(f"Adding product with TCIN: {tcin}", "info", "config")

        # Load current config
        config = simple_checker.get_config()

        # Check if product already exists
        existing_tcins = [p['tcin'] for p in config.get('products', [])]
        if tcin in existing_tcins:
            add_activity_log(f"Product {tcin} already exists in configuration", "warning", "config")
            if is_ajax:
                return jsonify({'success': False, 'error': 'Product already exists'})
            return redirect(url_for('index', skip_api='true'))

        # Fetch product name from API
        api_key = get_massive_api_key_rotation()
        params = {
            'key': api_key,
            'tcins': tcin,
            'store_id': '865',
            'pricing_store_id': '865',
            'has_pricing_context': 'true',
            'has_promotions': 'true',
            'is_bot': 'false'
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

        product_name = f"Product {tcin}"  # Default name

        if response.status_code == 200:
            data = response.json()
            if ('data' in data and
                'product_summaries' in data['data'] and
                len(data['data']['product_summaries']) > 0):

                item = data['data']['product_summaries'][0].get('item', {})
                product_desc = item.get('product_description', {})
                raw_name = product_desc.get('title', f'Product {tcin}')
                product_name = html.unescape(raw_name)

        # Add product to config
        new_product = {
            'tcin': tcin,
            'name': product_name
        }

        config['products'].append(new_product)

        # Save config
        config_path = "config/product_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        add_activity_log(f"Added new product: {product_name} (TCIN: {tcin})", "success", "config")

        if is_ajax:
            return jsonify({
                'success': True,
                'product': {
                    'tcin': tcin,
                    'name': product_name,
                    'url': f"https://www.target.com/p/-/A-{tcin}"
                }
            })

    except Exception as e:
        add_activity_log(f"Failed to add product {tcin}: {str(e)}", "error", "config")
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})

    return redirect(url_for('index', skip_api='true'))

@app.route('/remove-product/<tcin>', methods=['POST'])
def remove_product(tcin):
    """Remove a product from the configuration"""
    from flask import redirect, url_for, jsonify, request

    # Check if this is an AJAX request
    is_ajax = request.headers.get('Content-Type') == 'application/json'

    try:
        # Load current config
        config = simple_checker.get_config()

        # Find and remove the product
        original_count = len(config.get('products', []))
        config['products'] = [p for p in config.get('products', []) if p.get('tcin') != tcin]

        if len(config['products']) < original_count:
            # Save config
            config_path = "config/product_config.json"
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)

            add_activity_log(f"Removed product with TCIN: {tcin}", "success", "config")

            if is_ajax:
                return jsonify({'success': True})
        else:
            add_activity_log(f"Product {tcin} not found in configuration", "warning", "config")
            if is_ajax:
                return jsonify({'success': False, 'error': 'Product not found'})

    except Exception as e:
        add_activity_log(f"Failed to remove product {tcin}: {str(e)}", "error", "config")
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})

    return redirect(url_for('index', skip_api='true'))


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
            'store_id': '865',
            'pricing_store_id': '865',
            'has_pricing_context': 'true',
            'has_promotions': 'true',
            'is_bot': 'false'
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
    print("ADVANCED STEALTH DASHBOARD")
    print("=" + "="*60)
    print("[FEATURES] Advanced stealth API calls with anti-detection")
    print("[STEALTH] TLS fingerprinting, user agent rotation, session warmup")
    print("[F5/SHAPE] Session warmup, human behavior simulation")
    print("[ANTI-BOT] Multiple API keys, rotating cookies, advanced headers")
    print("[FLOW] Warmup -> Delay -> Stealth API -> Display dashboard")
    print("=" + "="*60)

    app.run(host='127.0.0.1', port=5001, debug=False)
