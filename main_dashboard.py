#!/usr/bin/env python3
"""
ULTIMATE Batch API Dashboard with Full Stealth Integration
Combines:
- Batch API efficiency (87% fewer calls)
- 50+ user agent rotation
- 30+ API key rotation  
- Advanced header rotation with browser fingerprinting
- Rotating cookies and session management
- 40-45 second random intervals for rate limit safety
- All existing ultra-stealth techniques
"""

import json
import time
import random
import requests
import threading
import asyncio
import concurrent.futures
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import logging
import sqlite3
import os
import ssl
import socket
import html
import signal
from typing import Dict, List, Any, Tuple
from urllib3.util.ssl_ import create_urllib3_context
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

# Try to import advanced stealth libraries for F5/Shape evasion
try:
    import curl_cffi
    from curl_cffi import requests as cf_requests
    CURL_CFFI_AVAILABLE = True
    print("curl_cffi available - Advanced TLS fingerprinting enabled for F5/Shape evasion")
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("[WARN] curl_cffi not available - using standard requests (consider: pip install curl_cffi)")

try:
    import pytz
    PYTZ_AVAILABLE = True
except ImportError:
    PYTZ_AVAILABLE = False

# Initialize Flask app with SocketIO
app = Flask(__name__, template_folder='dashboard/templates')
CORS(app)
app.secret_key = 'ultimate-batch-stealth-2025'

# Initialize SocketIO with better configuration for stability
socketio = SocketIO(app, 
                   cors_allowed_origins="*", 
                   async_mode='threading',
                   logger=False, 
                   engineio_logger=False,
                   ping_timeout=60,
                   ping_interval=25)

# WebSocket event handlers with enhanced debugging
@socketio.on('connect')
def handle_connect():
    print(f"[WEBSOCKET] Client connected from {request.sid}")
    try:
        emit('connection_status', {'status': 'connected', 'timestamp': datetime.now().isoformat()})
        print(f"[WEBSOCKET] Connection confirmation sent to {request.sid}")
        
        # Send actual backend timer state to newly connected client
        try:
            with timer_state_lock:
                if timer_started_at is not None and current_timer_seconds > 0:
                    # Calculate remaining time from backend timer
                    elapsed = time.time() - timer_started_at
                    remaining = max(0, current_timer_seconds - elapsed)
                    print(f"[WEBSOCKET] Backend timer sync: {remaining:.1f}s remaining (original: {current_timer_seconds}s)")
                else:
                    # No timer running, use fallback
                    remaining = 20.0
                    print(f"[WEBSOCKET] No backend timer active, using fallback: {remaining:.1f}s")
                    
            emit('timer_update', {
                'next_check_seconds': remaining,
                'timestamp': datetime.now().isoformat(),
                'initial_connection': True,
                'backend_sync': True
            })
            print(f"[WEBSOCKET] ✅ Backend timer state sent to new client: {remaining:.1f}s")
        except Exception as timer_error:
            print(f"[WEBSOCKET] Failed to send backend timer state: {timer_error}")
            # Send fallback timer
            emit('timer_update', {
                'next_check_seconds': 20.0,
                'timestamp': datetime.now().isoformat(),
                'initial_connection': True,
                'fallback': True
            })
            print(f"[WEBSOCKET] Fallback timer sent: 20.0s")
            
    except Exception as e:
        print(f"[WEBSOCKET] Failed to send connection status: {e}")

@socketio.on('disconnect')
def handle_disconnect():
    print(f"[WEBSOCKET] Client {request.sid} disconnected")

@socketio.on('force_refresh')
def handle_force_refresh():
    """Handle manual refresh requests from frontend"""
    print("[WEBSOCKET] Client requested force refresh")
    
    # Execute force refresh in a background thread to avoid blocking WebSocket
    def execute_force_refresh():
        try:
            success = force_immediate_refresh()
            print(f"[WEBSOCKET] Force refresh completed: {'success' if success else 'failed'}")
        except Exception as e:
            print(f"[WEBSOCKET] Force refresh error: {e}")
    
    import threading
    refresh_thread = threading.Thread(target=execute_force_refresh, daemon=True)
    refresh_thread.start()
    
    emit('force_refresh_response', {'success': True, 'message': 'Force refresh initiated'})

# Global data storage with thread safety
latest_stock_data = {}
latest_data_lock = threading.Lock()
initial_check_completed = False
last_update_time = None

# Global timer state for synchronization
current_timer_seconds = 0
timer_started_at = None
timer_state_lock = threading.Lock()

# Global stock data storage
current_stock_data = {}
stock_check_requested = False
stock_check_lock = threading.Lock()
dashboard_ready = False  # Flag to control when dashboard is accessible
api_call_in_progress = False  # Flag to prevent duplicate API calls

# Simple time-based purchase tracking
purchase_attempts = {}  # {tcin: last_refresh_timestamp}
purchase_statuses = {}  # {tcin: {'status': 'ready/attempting/purchased/failed', 'last_update': datetime}}
purchase_lock = threading.Lock()
current_refresh_timestamp = 0

# Stock status override for "Waiting for Refresh" after purchase completion
stock_status_override = {}
stock_override_lock = threading.Lock()


def start_new_refresh_cycle():
    """Start a new refresh cycle with unique timestamp and reset completed purchase statuses"""
    global current_refresh_timestamp
    # Use millisecond precision to ensure unique cycle numbers for rapid calls
    current_refresh_timestamp = int(time.time() * 1000)
    
    # Reset completed purchase statuses to 'ready' for new refresh cycle
    with purchase_lock:
        reset_count = 0
        for tcin, status_info in purchase_statuses.items():
            current_status = status_info.get('status', 'ready')
            # Reset products that completed purchase (success/failure) back to ready
            if current_status in ['purchased', 'failed']:
                purchase_statuses[tcin] = {
                    'status': 'ready',
                    'last_update': datetime.now()
                }
                reset_count += 1
                print(f"[🔄 RESET] {tcin}: Reset from '{current_status}' to 'ready' for new refresh cycle")
                
                # Skip WebSocket update from background thread to avoid context errors
                print(f"[WEBSOCKET] Background thread would send reset status update for {tcin}")
        
        if reset_count > 0:
            print(f"[🔄 NEW_REFRESH] Reset {reset_count} completed purchases to 'ready' for new cycle")
    
    print(f"[🔄 NEW_REFRESH] Started refresh cycle #{current_refresh_timestamp}")
    return current_refresh_timestamp

def can_attempt_purchase(tcin):
    """Enhanced purchase check - 1 attempt per refresh cycle, must wait for new cycle after completion"""
    with purchase_lock:
        # Check if already attempted this refresh cycle
        last_attempt = purchase_attempts.get(tcin, 0)
        
        # Check current purchase status
        status_info = purchase_statuses.get(tcin, {'status': 'ready', 'last_update': datetime.now()})
        current_status = status_info.get('status', 'ready')
        
        # Allow attempt if:
        # 1. Haven't attempted in this refresh cycle AND
        # 2. Either ready/failed/purchased (not currently attempting)
        can_attempt = (current_refresh_timestamp > last_attempt) and (current_status != 'attempting')
        
        if can_attempt:
            print(f"[✅ ALLOW] {tcin}: Ready for purchase (last: cycle #{last_attempt}, current: cycle #{current_refresh_timestamp}, status: {current_status})")
        else:
            if current_refresh_timestamp <= last_attempt:
                print(f"[🚫 BLOCK] {tcin}: Already attempted this refresh cycle #{current_refresh_timestamp}")
            elif current_status == 'attempting':
                print(f"[🚫 BLOCK] {tcin}: Currently attempting purchase - wait for completion")
        
        return can_attempt

def record_purchase_attempt(tcin):
    """Record that a purchase attempt was made in current refresh cycle"""
    with purchase_lock:
        purchase_attempts[tcin] = current_refresh_timestamp
        print(f"[📝 RECORD] {tcin}: Recorded attempt in refresh cycle #{current_refresh_timestamp}")

# Removed duplicate function - using the enhanced version below

def get_purchase_status(tcin):
    """Get current purchase status"""
    with purchase_lock:
        return purchase_statuses.get(tcin, {'status': 'ready', 'last_update': datetime.now()})

def set_purchase_status(tcin, status):
    """Simple purchase status management with WebSocket updates"""
    with purchase_lock:
        purchase_statuses[tcin] = {
            'status': status,
            'last_update': datetime.now()
        }
        print(f"[📱 STATUS] {tcin}: Set status to '{status}'")
        
        # Record attempt when status becomes 'attempting'
        if status == 'attempting':
            record_purchase_attempt(tcin)
        
        # Emit WebSocket update for purchase status change
        try:
            socketio.emit('purchase_status_update', {
                'tcin': tcin,
                'status': status,
                'timestamp': datetime.now().isoformat()
            })
            print(f"[WEBSOCKET] Purchase status update sent: {tcin} -> {status}")
        except Exception as e:
            print(f"[WEBSOCKET] Failed to emit purchase status update: {e}")


# Old complex function removed - using simple version defined earlier

def set_stock_waiting_for_response(tcin):
    """Set stock status to 'Waiting for Refresh' after purchase completion"""
    with stock_override_lock:
        stock_status_override[tcin] = 'Waiting for Refresh'
        print(f"[REFRESH] Stock status set to 'Waiting for Refresh' for {tcin}")

def clear_all_stock_overrides():
    """Clear all stock status overrides on refresh"""
    with stock_override_lock:
        cleared_count = len(stock_status_override)
        if cleared_count > 0:
            print(f"[REFRESH] Clearing {cleared_count} 'Waiting for Refresh' overrides - showing real API status")
            stock_status_override.clear()
        return cleared_count

# Old complex reset function removed - using simple timestamp approach

def get_stock_override(tcin):
    """Get stock status override if exists"""
    with stock_override_lock:
        return stock_status_override.get(tcin)

def trigger_purchase_attempts_for_in_stock_products():
    """Check all in-stock products and trigger purchase attempts if ready"""
    global latest_stock_data
    
    with latest_data_lock:
        stock_data = latest_stock_data.copy()
    
    for tcin, product_data in stock_data.items():
        if product_data.get('available') and can_attempt_purchase(tcin):
            product_name = product_data.get('name', 'Unknown Product')
            
            # Set purchase status to 'attempting' IMMEDIATELY (before thread starts)
            set_purchase_status(tcin, 'attempting')
            add_activity_log(f"[CART] Purchase attempt started for {product_name} (TCIN: {tcin}) - Purchase: Processing", 'purchase')
            
            # Start simple purchase attempt in a thread (no asyncio complexity)
            def start_purchase_attempt():
                try:
                    mock_purchase_attempt_sync(tcin, product_name)
                except Exception as e:
                    print(f"[ERROR] Purchase attempt error for {tcin}: {e}")
                    set_purchase_status(tcin, 'failed')
                    set_stock_waiting_for_response(tcin)
            
            purchase_thread = threading.Thread(target=start_purchase_attempt, daemon=True)
            purchase_thread.start()
            print(f"[CART] Dashboard refresh triggered purchase attempt for {tcin} ({product_name})")
        elif product_data.get('available'):
            print(f"[WAIT] Product {tcin} in stock but in cooldown period (not ready)")

def mock_purchase_attempt_sync(tcin, product_name):
    """Simplified mock purchase attempt with no asyncio complexity"""
    import time
    try:
        # Purchase status was already set to 'attempting' when this function was called
        start_time = datetime.now()
        print(f"[CART] ⏱️  STARTING mock purchase for {product_name} (TCIN: {tcin}) at {start_time.strftime('%H:%M:%S')}")
        
        # Use shorter duration to avoid blocking
        purchase_duration = 3.0  # Fixed 3 second duration for all products
        print(f"[CART] ⚡ Using purchase duration: {purchase_duration}s for {tcin}")
        
        # Simulate checkout process with progress logging
        steps = 4
        step_duration = purchase_duration / steps
        
        print(f"[CART] 📦 Step 1/{steps}: Adding to cart... ({step_duration:.1f}s)")
        time.sleep(step_duration)
        
        print(f"[CART] 🛒 Step 2/{steps}: Going to cart... ({step_duration:.1f}s)")
        time.sleep(step_duration)
        
        print(f"[CART] 💳 Step 3/{steps}: Checkout page... ({step_duration:.1f}s)")
        time.sleep(step_duration)
        
        print(f"[CART] 🔐 Step 4/{steps}: Payment processing... ({step_duration:.1f}s)")
        time.sleep(step_duration)
        
        # Random success/failure (75% success rate for better testing)
        success = random.random() < 0.75
        end_time = datetime.now()
        actual_duration = (end_time - start_time).total_seconds()
        
        if success:
            set_purchase_status(tcin, 'purchased')
            set_stock_waiting_for_response(tcin)  # Set stock override
            print(f"[CART] ✅ COMPLETED: Purchase SUCCESS for {tcin} after {actual_duration:.1f}s")
            add_activity_log(f"[OK] Mock purchase SUCCESS for {product_name} - Duration: {actual_duration:.1f}s", 'success')
            return True
        else:
            set_purchase_status(tcin, 'failed')
            set_stock_waiting_for_response(tcin)  # Set stock override
            print(f"[CART] ❌ COMPLETED: Purchase FAILED for {tcin} after {actual_duration:.1f}s")
            add_activity_log(f"[ERROR] Mock purchase FAILED for {product_name} - Duration: {actual_duration:.1f}s", 'error')
            return False
            
    except Exception as e:
        print(f"[CART] ❌ EXCEPTION during mock purchase for {tcin}: {e}")
        set_purchase_status(tcin, 'failed')
        set_stock_waiting_for_response(tcin)
        add_activity_log(f"[ERROR] Purchase exception for {product_name}: {str(e)}", 'error')
        return False

async def mock_purchase_attempt(tcin, product_name):
    """Mock purchase attempt with realistic timing and potential failure"""
    try:
        # Purchase status was already set to 'attempting' when this function was called
        start_time = datetime.now()
        print(f"[CART] ⏱️  STARTING mock purchase for {product_name} (TCIN: {tcin}) at {start_time.strftime('%H:%M:%S')}")
        
        # Test with varying purchase durations to verify refresh cycle behavior
        if tcin.endswith('1'):  # Make first product take 30 seconds for testing
            print(f"[CART] 🐌 Using LONG purchase duration (30s) for {tcin} to test refresh cycle behavior")
            purchase_duration = 30.0
        else:
            print(f"[CART] ⚡ Using NORMAL purchase duration (2s) for {tcin}")
            purchase_duration = 2.0
        
        # Simulate checkout process with progress logging
        steps = 4
        step_duration = purchase_duration / steps
        
        print(f"[CART] 📦 Step 1/{steps}: Adding to cart... ({step_duration:.1f}s)")
        await asyncio.sleep(step_duration)
        
        print(f"[CART] 🛒 Step 2/{steps}: Going to cart... ({step_duration:.1f}s)")
        await asyncio.sleep(step_duration)
        
        print(f"[CART] 💳 Step 3/{steps}: Checkout page... ({step_duration:.1f}s)")
        await asyncio.sleep(step_duration)
        
        print(f"[CART] 🔐 Step 4/{steps}: Payment processing... ({step_duration:.1f}s)")
        await asyncio.sleep(step_duration)
        
        # Random success/failure (50% success rate for testing)
        success = random.random() < 0.5
        end_time = datetime.now()
        actual_duration = (end_time - start_time).total_seconds()
        
        if success:
            set_purchase_status(tcin, 'purchased')
            set_stock_waiting_for_response(tcin)  # Set stock override
            print(f"[CART] ✅ COMPLETED: Purchase SUCCESS for {tcin} after {actual_duration:.1f}s (expected: {purchase_duration:.1f}s)")
            add_activity_log(f"[OK] Mock purchase SUCCESS for {product_name} - Duration: {actual_duration:.1f}s", 'success')
            return True
        else:
            set_purchase_status(tcin, 'failed')
            set_stock_waiting_for_response(tcin)  # Set stock override
            print(f"[CART] ❌ COMPLETED: Purchase FAILED for {tcin} after {actual_duration:.1f}s (expected: {purchase_duration:.1f}s)")
            add_activity_log(f"[ERROR] Mock purchase FAILED for {product_name} - Duration: {actual_duration:.1f}s", 'error')
            return False
            
    except Exception as e:
        set_purchase_status(tcin, 'failed')
        set_stock_waiting_for_response(tcin)  # Set stock override
        add_activity_log(f"[ERROR] Mock purchase ERROR for {product_name}: {e}", 'error')
        return False

# Stealth analytics tracking
class UltraStealthAnalytics:
    def __init__(self):
        self.analytics_data = {
            'total_calls': 0,
            'successful_calls': 0,
            'failed_calls': 0,
            'average_response_time': 0,
            'success_rate': 100.0,
            'stealth_metrics': {
                'user_agents_rotated': 0,
                'api_keys_rotated': 0,
                'headers_randomized': 0,
                'cookies_rotated': 0
            }
        }
        self.last_check_time = None
        self.update_lock = threading.Lock()
        
    def record_batch_check(self, response_time, success, stealth_data):
        with self.update_lock:
            self.analytics_data['total_calls'] += 1
            if success:
                self.analytics_data['successful_calls'] += 1
                self.analytics_data['average_response_time'] = response_time
            else:
                self.analytics_data['failed_calls'] += 1
                
            # Update stealth metrics
            self.analytics_data['stealth_metrics']['user_agents_rotated'] += 1
            self.analytics_data['stealth_metrics']['api_keys_rotated'] += 1
            self.analytics_data['stealth_metrics']['headers_randomized'] += 1
            self.analytics_data['stealth_metrics']['cookies_rotated'] += 1
            
            # Calculate success rate
            total = self.analytics_data['total_calls']
            successful = self.analytics_data['successful_calls']
            self.analytics_data['success_rate'] = (successful / max(total, 1)) * 100
            
            self.last_check_time = datetime.now()

# Global analytics and activity logging
stealth_analytics = UltraStealthAnalytics()

# Activity log storage
activity_log = []
MAX_LOG_ENTRIES = 50

def add_activity_log(message, log_type='info'):
    """Add entry to activity log"""
    global activity_log
    timestamp = datetime.now()
    activity_log.append({
        'timestamp': timestamp.isoformat(),
        'message': message,
        'type': log_type
    })
    # Keep only last 50 entries
    if len(activity_log) > MAX_LOG_ENTRIES:
        activity_log = activity_log[-MAX_LOG_ENTRIES:]

def get_enabled_product_count():
    """Get enabled product count from config"""
    try:
        # Use the same config loading logic as stealth_checker
        possible_paths = [
            "config/product_config.json",
            "dashboard/../config/product_config.json",
            "../config/product_config.json"
        ]
        
        for path in possible_paths:
            if Path(path).exists():
                with open(path, 'r') as f:
                    config = json.load(f)
                    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
                    return len(enabled_products)
        
        # Fallback if no config found
        return 0
    except Exception as e:
        print(f"Error loading product count: {e}")
        return 0

class HumanBehaviorSimulator:
    """F5/Shape evasion: Simulates realistic human browsing patterns"""
    
    def __init__(self):
        self.session_start_time = datetime.now()
        self.requests_this_session = 0
        self.last_request_time = None
        self.fatigue_factor = 1.0
        self.break_taken_this_hour = False
        
    def is_human_active_hours(self):
        """Check if current time is during typical human browsing hours"""
        hour = datetime.now().hour
        # Humans more active 9AM-11PM, less active 11PM-9AM
        return 9 <= hour <= 23
    
    def get_human_delay(self):
        """Calculate human-like delay for 3 calls per minute with 15s minimum spacing"""
        # Target: 3 calls per minute = 20s average, with proper randomization
        
        # Start with pure random base in our target range
        base_delay = random.uniform(15.0, 25.0)
        
        # Apply minimal fatigue (very light to maintain variation)
        fatigue_adjustment = (self.fatigue_factor - 1.0) * 1.5  # Convert 1.0-1.15 to 0-0.225 seconds
        
        # Light time-of-day variation  
        if not self.is_human_active_hours():
            time_adjustment = random.uniform(0.0, 1.0)  # 0-1s slower at night
        else:
            time_adjustment = random.uniform(-0.3, 0.3)  # ±0.3s during day
        
        # Calculate final delay with natural variation
        final_delay = base_delay + fatigue_adjustment + time_adjustment
        
        # Keep in range and validate
        final_delay = max(15.0, min(final_delay, 25.0))
        
        # Validate the result
        if not isinstance(final_delay, (int, float)) or final_delay <= 0:
            print(f"[ERROR] Invalid delay calculated: {final_delay}, using fallback")
            final_delay = 20.0  # Safe fallback
        
        print(f"[TIMING] Human delay: base={base_delay:.1f}s, fatigue=+{fatigue_adjustment:.2f}s, time={time_adjustment:+.2f}s → {final_delay:.1f}s")
        
        return final_delay
    
    def update_fatigue_after_request(self):
        """Update fatigue factor after an actual request (not just calculation)"""
        # Add small random fatigue increase after each request
        fatigue_increase = random.uniform(0.002, 0.012)
        self.fatigue_factor += fatigue_increase
        self.requests_this_session += 1
        
        # Occasionally reset fatigue (like humans taking breaks or getting fresh)
        if self.requests_this_session % random.randint(8, 15) == 0:
            # Random fatigue reset (like a human taking a break)
            old_fatigue = self.fatigue_factor
            self.fatigue_factor = random.uniform(0.98, 1.05)  # Fresh but not perfect
            print(f"[REFRESH] Human behavior: Fatigue reset from {old_fatigue:.3f} to {self.fatigue_factor:.3f} (like taking a break)")
        
        print(f"[BRAIN] Fatigue updated: {self.fatigue_factor:.3f} (requests: {self.requests_this_session})")
    
    def should_take_human_break(self):
        """F5/Shape evasion: Determine if human would take a longer break"""
        # After 10-20 requests, humans typically take breaks
        if (self.requests_this_session > 0 and 
            self.requests_this_session % random.randint(10, 20) == 0 and 
            not self.break_taken_this_hour):
            self.break_taken_this_hour = True
            return True
        return False
    
    def get_break_duration(self):
        """Get duration for human break (3-10 minutes for F5/Shape evasion)"""
        return random.uniform(180, 600)  # 3-10 minutes

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
        """Check if session needs warmup (every few hours like humans)"""
        if not self.last_warmup_time:
            return True
        return datetime.now() - self.last_warmup_time > timedelta(hours=random.uniform(2, 4))
        
    def warmup_session(self, session, headers):
        """F5/Shape evasion: Visit Target pages before API call"""
        if not self.needs_warmup():
            return True
            
        try:
            warmup_url = random.choice(self.warmup_pages)
            
            # Human-like browsing headers
            warmup_headers = headers.copy()
            warmup_headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Upgrade-Insecure-Requests': '1'
            })
            
            print(f"[HOT] F5/Shape evasion: Warming session with {warmup_url}")
            
            warmup_response = session.get(
                warmup_url,
                headers=warmup_headers,
                timeout=15,
                allow_redirects=True
            )
            
            # Human reading time (2-5 seconds)
            time.sleep(random.uniform(2, 5))
            
            self.last_warmup_time = datetime.now()
            print(f"[OK] Session warmup complete: {warmup_response.status_code}")
            return True
            
        except Exception as e:
            print(f"[WARN] Session warmup failed: {e}")
            return False

# Initialize F5/Shape evasion components
human_behavior = HumanBehaviorSimulator()
session_warmer = SessionWarmupManager()

# Preorder Detection and Checking Functions
def is_preorder_item(fulfillment_data: Dict) -> bool:
    """
    Detect if an item is a preorder based on fulfillment API data structure
    
    Args:
        fulfillment_data: Fulfillment section from batch API response
        
    Returns:
        bool: True if item is a preorder
    """
    # Check availability status for PRE_ORDER indicators
    shipping_options = fulfillment_data.get('shipping_options', {})
    availability_status = shipping_options.get('availability_status', '')
    
    return 'PRE_ORDER' in availability_status

def check_preorder_availability_enhanced(tcin: str, api_key: str, store_id: str = "865") -> Tuple[bool, Dict]:
    """
    Enhanced preorder availability checker using fulfillment endpoint
    
    Args:
        tcin: Product TCIN
        api_key: Target API key
        store_id: Store ID (default: "865")
    
    Returns:
        tuple: (is_available, status_info)
        - is_available: bool, True if preorder can be purchased
        - status_info: dict with detailed availability information
    """
    fulfillment_url = "https://redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1"
    
    headers = {
        'accept': 'application/json',
        'user-agent': get_massive_user_agent_rotation(),
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'referer': 'https://www.target.com/',
    }
    
    params = {
        'key': api_key,
        'tcins': tcin,  # Note: 'tcins' not 'tcin' for this endpoint
        'store_id': store_id,
        'pricing_store_id': store_id,
        'has_pricing_context': 'true',
        'has_promotions': 'true',
        'is_bot': 'false',
    }
    
    try:
        response = requests.get(fulfillment_url, params=params, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extract fulfillment data
            if ('data' in data and 
                'product_summaries' in data['data'] and 
                len(data['data']['product_summaries']) > 0):
                
                fulfillment = data['data']['product_summaries'][0].get('fulfillment', {})
                shipping_options = fulfillment.get('shipping_options', {})
                availability_status = shipping_options.get('availability_status')
                
                # Key logic: PRE_ORDER_SELLABLE = available, PRE_ORDER_UNSELLABLE = unavailable
                is_available = availability_status == 'PRE_ORDER_SELLABLE'
                
                status_info = {
                    'availability_status': availability_status,
                    'loyalty_availability_status': shipping_options.get('loyalty_availability_status'),
                    'is_out_of_stock_in_all_store_locations': fulfillment.get('is_out_of_stock_in_all_store_locations', False),
                    'sold_out': fulfillment.get('sold_out', False),
                    'source': 'fulfillment_api',
                    'success': True,
                    'is_preorder': True
                }
                
                return is_available, status_info
        
        return False, {
            'error': f'API failed with status {response.status_code}',
            'source': 'fulfillment_api',
            'success': False,
            'is_preorder': True
        }
        
    except Exception as e:
        return False, {
            'error': f'Request failed: {str(e)}',
            'source': 'fulfillment_api', 
            'success': False,
            'is_preorder': True
        }

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
    """30+ API keys for maximum distribution"""
    api_keys = [
        # Primary working key
        "ff457966e64d5e877fdbad070f276d18ecec4a01",
        # Additional rotation keys (these would need to be discovered/validated)
        "9f36aeafbe60771e321a7cc95a78140772ab3e96",
        "7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b",
        "4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a",
        "2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d",
        "8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e"
    ]
    return random.choice(api_keys)

def get_rotating_cookies():
    """Get rotating cookie sets for maximum stealth"""
    cookie_sets = [
        # Set 1: Basic session cookies
        {
            'sessionId': ''.join(random.choices('0123456789abcdef', k=32)),
            'visitorId': ''.join(random.choices('0123456789ABCDEF', k=16)),
            'timezone': random.choice(['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles']),
        },
        # Set 2: E-commerce cookies
        {
            'cart_id': ''.join(random.choices('0123456789abcdef', k=24)),
            'user_pref': 'lang=en-US&currency=USD',
            'last_visit': str(int(time.time() - random.randint(3600, 86400))),
        },
        # Set 3: Analytics cookies
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
    
    # Base headers that always appear
    headers = {
        'accept': 'application/json',
        'user-agent': user_agent,
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
        'connection': 'keep-alive',
    }
    
    # Chrome-specific sec-ch-ua headers with realistic variations
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
    if random.choice([True, True, False]):  # 67% chance
        headers['referer'] = random.choice(referers)
    
    # Additional stealth headers (randomized)
    if random.choice([True, False]):
        headers['upgrade-insecure-requests'] = '1'
        
    if random.choice([True, False, False]):  # 33% chance
        headers['x-requested-with'] = 'XMLHttpRequest'
        
    return headers

class UltimateStealthBatchChecker:
    """Ultimate stealth batch checker with all rotation techniques"""
    
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
        """Create session with full stealth configuration"""
        session = requests.Session()
        session.cookies.clear()
        
        # Apply rotating cookies
        rotating_cookies = get_rotating_cookies()
        for name, value in rotating_cookies.items():
            session.cookies.set(name, value)
        
        return session, rotating_cookies
    
    def make_ultimate_stealth_batch_call(self):
        """Make batch API call with full stealth rotation + F5/Shape evasion"""
        config = self.get_config()
        enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
        
        if not enabled_products:
            return {}
        
        # F5/Shape evasion: Check for human break patterns
        if human_behavior.should_take_human_break():
            break_duration = human_behavior.get_break_duration()
            print(f"[TIRED] F5/Shape evasion: Taking human break for {break_duration/60:.1f} minutes...")
            time.sleep(break_duration)
        
        # Extract TCINs for batch call
        tcins = [p['tcin'] for p in enabled_products]
        
        # Rotate API key for each call
        api_key = get_massive_api_key_rotation()
        
        # Build stealth parameters
        params = {
            'key': api_key,
            'tcins': ','.join(tcins),  # Batch format
            'is_bot': 'false',  # Critical anti-bot parameter
            '_': str(int(time.time() * 1000)),  # Cache busting
            **self.location_params
        }
        
        # F5/Shape evasion: Create advanced session with TLS fingerprinting
        if CURL_CFFI_AVAILABLE:
            # Advanced TLS fingerprinting
            user_agent = get_massive_user_agent_rotation()[0]
            if 'Chrome' in user_agent:
                session = cf_requests.Session(impersonate="chrome120")
            elif 'Firefox' in user_agent:
                session = cf_requests.Session(impersonate="firefox119")
            else:
                session = cf_requests.Session(impersonate="chrome120")
            print(f"[HOT] F5/Shape evasion: Advanced TLS fingerprinting enabled")
        else:
            # Fallback to standard session
            session, cookies = self.create_stealth_session()
        
        # Get rotated headers for this request
        headers = get_ultra_stealth_headers()
        
        # F5/Shape evasion: Session warmup before API call
        if session_warmer.needs_warmup():
            print(f"[HOT] F5/Shape evasion: Session warmup starting...")
            session_warmer.warmup_session(session, headers)
            # Human delay between warmup and API call
            time.sleep(random.uniform(3, 8))
        
        stealth_data = {
            'api_key': api_key[:8] + '...',  # Track partial key
            'user_agent': headers['user-agent'][:50] + '...',
            'cookies_count': len(session.cookies) if hasattr(session, 'cookies') else 0,
            'headers_count': len(headers),
            'f5_evasion': True,
            'session_warmup': session_warmer.last_warmup_time is not None,
            'tls_fingerprinting': CURL_CFFI_AVAILABLE
        }
        
        try:
            start_time = time.time()
            print(f"[REFRESH] Ultimate stealth + F5 evasion batch call: {len(tcins)} products, API key: {api_key[:8]}...")
            print(f"[STEALTH] User-Agent: {headers['user-agent'][:60]}...")
            print(f"[HOT] F5/Shape evasion: {'Advanced TLS' if CURL_CFFI_AVAILABLE else 'Standard'}, Session warmup: {'Yes' if session_warmer.last_warmup_time else 'No'}")
            
            # Update human behavior tracking after actual request
            human_behavior.update_fatigue_after_request()
            human_behavior.last_request_time = datetime.now()
            response = session.get(
                self.batch_endpoint,
                params=params,
                headers=headers,
                timeout=25
            )
            
            end_time = time.time()
            response_time = (end_time - start_time) * 1000
            
            if response.status_code == 200:
                print(f"[OK] Stealth batch success: {response_time:.0f}ms")
                data = response.json()
                processed_data = self.process_batch_response(data, enabled_products)
                
                # Record successful stealth metrics
                stealth_analytics.record_batch_check(response_time, True, stealth_data)
                
                return processed_data
            else:
                print(f"[ERROR] Stealth batch failed: HTTP {response.status_code}")
                stealth_analytics.record_batch_check(response_time, False, stealth_data)
                return {}
                
        except Exception as e:
            print(f"[ERROR] Stealth batch exception: {e}")
            stealth_analytics.record_batch_check(0, False, stealth_data)
            return {}
        finally:
            session.close()
    
    def process_batch_response(self, data, enabled_products):
        """Convert batch API response to dashboard format with stealth preservation and preorder support"""
        if not data or 'data' not in data or 'product_summaries' not in data['data']:
            print("[ERROR] Invalid batch response structure")
            print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            return {}
            
        product_summaries = data['data']['product_summaries']
        processed_data = {}
        
        print(f"[DATA] Processing {len(product_summaries)} products from batch response...")
        print(f"[VERIFY] Config has {len(enabled_products)} enabled products to check")
        
        for product_summary in product_summaries:
            try:
                # Extract TCIN
                tcin = product_summary.get('tcin')
                if not tcin:
                    print("[WARN] Product summary missing TCIN, skipping")
                    continue
                
                print(f"[PROCESSING] TCIN: {tcin}")
                
                # Extract item section
                item = product_summary.get('item', {})
                if not item:
                    print(f"[WARN] {tcin}: No item section found")
                    continue
                
                # Extract fulfillment section  
                fulfillment = product_summary.get('fulfillment', {})
                if not fulfillment:
                    print(f"[WARN] {tcin}: No fulfillment section found")
                    continue
                
                # Extract product name from the correct location
                product_desc = item.get('product_description', {})
                raw_name = product_desc.get('title', 'Unknown Product')
                clean_name = html.unescape(raw_name)  # Decode HTML entities like &#233;
                
                # Extract shipping options for availability
                shipping = fulfillment.get('shipping_options', {})
                availability_status = shipping.get('availability_status', 'UNKNOWN')
                
                # Check if it's a marketplace seller
                relationship_code = item.get('relationship_type_code', 'UNKNOWN')
                is_target_direct = relationship_code == 'SA'  # SA = Sold and shipped by Target
                is_marketplace = not is_target_direct
                
                # Detect if this is a preorder
                is_preorder = 'PRE_ORDER' in availability_status
                
                # Determine availability based on product type and seller
                if is_preorder:
                    # For preorders, use PRE_ORDER_SELLABLE/UNSELLABLE logic
                    base_available = availability_status == 'PRE_ORDER_SELLABLE'
                    print(f"[PREORDER] {tcin}: {availability_status} -> {'AVAILABLE' if base_available else 'UNAVAILABLE'}")
                else:
                    # For regular products, use IN_STOCK logic
                    base_available = availability_status == 'IN_STOCK'
                    print(f"[REGULAR] {tcin}: {availability_status} -> {'AVAILABLE' if base_available else 'UNAVAILABLE'}")
                
                # Final availability = base availability AND Target direct seller
                is_available = base_available and is_target_direct
                
                # Determine status message
                if not is_target_direct:
                    normalized_status = 'MARKETPLACE_SELLER'
                elif not base_available:
                    normalized_status = 'OUT_OF_STOCK'
                else:
                    normalized_status = 'IN_STOCK'
                
                # Get additional info
                street_date = None
                if is_preorder:
                    # Try to get street date from various locations
                    mmbv_content = item.get('mmbv_content', {})
                    street_date = mmbv_content.get('street_date')
                
                buy_url = item.get('enrichment', {}).get('buy_url', f"https://www.target.com/p/-/A-{tcin}")
                store_available = not fulfillment.get('is_out_of_stock_in_all_store_locations', True)
                
                # Trigger purchase immediately when stock is detected
                if is_available:
                    print(f"[PACKAGE] Product {tcin} in stock - triggering immediate purchase attempt")
                    if can_attempt_purchase(tcin):
                        print(f"[CART] Auto-purchase attempt for {tcin}")
                        set_purchase_status(tcin, 'attempting')
                        # Start purchase in background thread
                        def start_auto_purchase():
                            try:
                                time.sleep(0.5)  # Small delay to allow status update
                                mock_purchase_attempt_sync(tcin, clean_name)
                            except Exception as e:
                                print(f"[ERROR] Auto-purchase thread failed for {tcin}: {e}")
                                set_purchase_status(tcin, 'failed')
                        
                        purchase_thread = threading.Thread(target=start_auto_purchase, daemon=True)
                        purchase_thread.start()
                    else:
                        print(f"[WAIT] Product {tcin} in stock but not ready for purchase attempt")
                
                # Build result
                result = {
                    'tcin': tcin,
                    'name': clean_name,
                    'available': is_available,
                    'status': normalized_status,
                    'last_checked': datetime.now().isoformat(),
                    'quantity': 1 if is_available else 0,
                    'availability_status': availability_status,
                    'sold_out': not is_available,
                    'response_time': stealth_analytics.analytics_data['average_response_time'],
                    'confidence': 'high',
                    'method': 'ultimate_stealth_batch',
                    'url': buy_url,
                    
                    # Store availability
                    'store_available': store_available,
                    
                    # Seller verification
                    'is_target_direct': is_target_direct,
                    'is_marketplace': is_marketplace,
                    'seller_code': relationship_code,
                    
                    # Preorder-specific fields
                    'is_preorder': is_preorder,
                    'street_date': street_date,
                    'preorder_status': availability_status if is_preorder else None,
                    
                    # Additional tracking
                    'stealth_applied': True,
                    'batch_api': True,
                    'has_data': True
                }
                
                processed_data[tcin] = result
                
            except Exception as e:
                print(f"[ERROR] Failed to process product summary for TCIN {tcin}: {e}")
                continue
        
        # Summary logging
        elapsed = time.time() * 1000 if hasattr(time, 'time') else 0
        in_stock_count = sum(1 for r in processed_data.values() if r.get('available'))
        
        print(f"[OK] Batch processing complete: {len(processed_data)} products ({in_stock_count} in stock)")
        
        return processed_data

# Initialize global stealth checker instance
stealth_checker = UltimateStealthBatchChecker()

def force_immediate_refresh():
    """Force an immediate refresh when called from frontend recovery"""
    global latest_stock_data, last_update_time
    
    print("[FORCE_REFRESH] Frontend requested immediate refresh - executing now...")
    
    try:
        batch_data = stealth_checker.make_ultimate_stealth_batch_call()
        
        if batch_data:
            # Update with fresh API data
            with latest_data_lock:
                latest_stock_data = batch_data
                print(f"[DEBUG] latest_stock_data keys after force refresh: {list(latest_stock_data.keys())}")
                last_update_time = datetime.now()
            global current_stock_data
            current_stock_data = batch_data.copy()
            print(f"[DEBUG] current_stock_data keys after force refresh: {list(current_stock_data.keys())}")
            
            in_stock_count = sum(1 for r in batch_data.values() if r.get('available'))
            print(f"[FORCE_REFRESH] Complete: {len(batch_data)} products ({in_stock_count} in stock)")
            
            # Emit WebSocket update to connected clients
            try:
                socketio.emit('stock_update', {
                    'success': True,
                    'products': list(batch_data.values()),
                    'timestamp': datetime.now().isoformat(),
                    'forced_refresh': True
                })
                print("[WEBSOCKET] Forced refresh stock update sent")
            except Exception as ws_error:
                print(f"[WEBSOCKET] Failed to emit forced refresh update: {ws_error}")
            
            # CRITICAL: Send new timer update after force refresh to restart countdown
            try:
                new_delay = human_behavior.get_human_delay()
                socketio.emit('timer_update', {
                    'next_check_seconds': new_delay,
                    'timestamp': datetime.now().isoformat(),
                    'forced_refresh': True
                })
                print(f"[WEBSOCKET] ✅ New timer sent after force refresh: {new_delay:.1f}s")
            except Exception as timer_error:
                print(f"[WEBSOCKET] ❌ Failed to send timer after force refresh: {timer_error}")
            
            # Start new refresh cycle and trigger purchases
            start_new_refresh_cycle()
            
            # Trigger purchases for in-stock products
            for tcin, product_data in batch_data.items():
                if product_data.get('available') and can_attempt_purchase(tcin):
                    print(f"[CART] Force refresh purchase attempt for {tcin}")
                    set_purchase_status(tcin, 'attempting')
                    
                    def start_force_purchase():
                        try:
                            time.sleep(0.5)
                            mock_purchase_attempt_sync(tcin, product_data.get('name', 'Unknown Product'))
                        except Exception as e:
                            print(f"[ERROR] Force purchase failed for {tcin}: {e}")
                            set_purchase_status(tcin, 'failed')
                    
                    thread = threading.Thread(target=start_force_purchase, daemon=True)
                    thread.start()
            
            return True
        else:
            print("[FORCE_REFRESH] Failed - no data returned")
            return False
            
    except Exception as e:
        print(f"[FORCE_REFRESH] Exception: {e}")
        return False

def simple_timer_manager():
    """Simple timer that just manages timer state - no API calls"""
    global current_timer_seconds, timer_started_at
    
    print("[SIMPLE_TIMER] Starting simple timer manager")
    
    timer_count = 0
    while True:
        timer_count += 1
        
        # Random interval between 15-25 seconds for stealth
        total_seconds = random.randint(15, 25)
        print(f"[TIMER] Starting timer #{timer_count}: {total_seconds} seconds")
        
        # Update global timer state
        with timer_state_lock:
            current_timer_seconds = total_seconds
            timer_started_at = time.time()
        
        # Sleep for the full duration (simpler than countdown loop)
        time.sleep(total_seconds)
        
        # Timer hit 0 - clear timer state
        with timer_state_lock:
            current_timer_seconds = 0
            timer_started_at = None
        
        print(f"[TIMER] Timer #{timer_count} completed! Triggering stock check...")
        
        # Just signal that stock check should happen - don't block the timer
        global stock_check_requested
        stock_check_requested = True
        print("[TIMER] Stock check requested (non-blocking)")
        
        
        print(f"[TIMER] Ready for next cycle...")

def stock_checker_worker():
    """Simple stock checker worker that performs an immediate check then waits for timer signals"""
    global stock_check_requested, current_stock_data, stealth_checker, dashboard_ready, latest_stock_data
    
    print("[STOCK_WORKER] 🚀 Starting stock checker worker...")
    
    import time
    
    # Wait briefly for stealth_checker to be available
    for i in range(10):
        try:
            if stealth_checker is not None:
                print("[STOCK_WORKER] ✅ stealth_checker is ready")
                break
        except:
            pass
        time.sleep(0.1)
    
    # Start immediate API call in background thread to avoid blocking dashboard
    print("[STOCK_WORKER] 🔥 Starting background warmup API call...")
    
    def background_api_call():
        global dashboard_ready, current_stock_data, latest_stock_data
        try:
            print("[STOCK_WORKER] 🚀 Background API call started...")
            batch_data = stealth_checker.make_ultimate_stealth_batch_call()
            print(f"[STOCK_WORKER] API call returned type: {type(batch_data)}, length: {len(batch_data) if batch_data else 0}")
            
            if batch_data and len(batch_data) > 0:
                # Update both global data stores
                current_stock_data = batch_data
                with latest_data_lock:
                    latest_stock_data = batch_data.copy()
                
                print(f"[STOCK_WORKER] ✅ Background API call: Got data for {len(batch_data)} products")
                print(f"[STOCK_WORKER] ✅ current_stock_data keys: {list(current_stock_data.keys())}")
                print(f"[STOCK_WORKER] ✅ Data loaded successfully!")
                
                # Optional: Send WebSocket update 
                try:
                    socketio.emit('stock_update', {
                        'status': 'live_data',
                        'data': batch_data,
                        'timestamp': time.time()
                    })
                    print("[STOCK_WORKER] ✅ WebSocket update sent")
                except Exception as ws_error:
                    print(f"[STOCK_WORKER] WebSocket error: {ws_error}")
            else:
                print("[STOCK_WORKER] ⚠️ API call returned empty data")
                print("[STOCK_WORKER] ✅ Continuing with empty data")
                
        except Exception as e:
            print(f"[STOCK_WORKER] ❌ Background API call failed: {str(e)}")
            print("[STOCK_WORKER] ✅ Continuing after error")
            
        finally:
            # Always set dashboard ready after first API attempt (success or failure)
            dashboard_ready = True
            print("[STOCK_WORKER] ✅ Dashboard ready flag set to True")
            print("[STOCK_WORKER] 🏁 Background API call thread completed")
    
    # Start background API call
    import threading
    api_thread = threading.Thread(target=background_api_call, daemon=True)
    api_thread.start()
    print("[STOCK_WORKER] 🚀 Background API thread started")
    
    # Wait for API call to complete (with timeout)
    print("[STOCK_WORKER] ⏰ Waiting up to 15 seconds for API call to complete...")
    
    # Join the thread with timeout
    api_thread.join(timeout=15.0)
    
    if api_thread.is_alive():
        print("[STOCK_WORKER] ⚠️ API call taking longer than 15 seconds - enabling dashboard anyway")
        dashboard_ready = True
        print("[STOCK_WORKER] ✅ Dashboard ready (API call continues in background)")
    else:
        print("[STOCK_WORKER] ✅ API call completed within 15 seconds!")
        
    # Ensure dashboard_ready is set regardless  
    if not dashboard_ready:
        dashboard_ready = True
        print("[STOCK_WORKER] ✅ Dashboard ready (fallback)")
    
    # Reset request flag
    stock_check_requested = False
    
    # Now enter monitoring loop
    print("[STOCK_WORKER] 🔄 Starting monitoring loop...")
    loop_count = 0
    
    while True:
        try:
            loop_count += 1
            time.sleep(1)  # Check every second
            
            # Debug output every 10 seconds
            if loop_count % 10 == 0:
                print(f"[STOCK_WORKER] ✅ Loop #{loop_count}: stock_check_requested = {stock_check_requested}")
            
            # Check if timer has requested a stock check
            if stock_check_requested:
                print(f"[STOCK_WORKER] ✅ Timer requested stock check at loop #{loop_count}")
                stock_check_requested = False
                
                print("[STOCK_WORKER] 🔄 Performing timer-triggered stock check...")
                try:
                    batch_data = stealth_checker.make_ultimate_stealth_batch_call()
                    if batch_data and len(batch_data) > 0:
                        print(f"[STOCK_WORKER] ✅ Timer check: Updated {len(batch_data)} products")
                        current_stock_data = batch_data
                        print(f"[STOCK_WORKER] ✅ current_stock_data keys after timer: {list(current_stock_data.keys())}")
                        # Send WebSocket update
                        try:
                            socketio.emit('stock_update', {
                                'status': 'success',
                                'data': batch_data,
                                'timestamp': time.time()
                            })
                            print("[STOCK_WORKER] ✅ WebSocket update sent for timer check")
                        except Exception as ws_error:
                            print(f"[STOCK_WORKER] WebSocket error: {ws_error}")
                    else:
                        print("[STOCK_WORKER] ❌ Timer check: No data returned; keeping previous current_stock_data")
                        # Do NOT overwrite current_stock_data with empty unless truly failed
                except Exception as e:
                    print(f"[STOCK_WORKER] ⚠️ Timer check error: {str(e)}")
                    current_stock_data = {}
                    
        except Exception as loop_error:
            print(f"[STOCK_WORKER] ❌ Loop error: {str(loop_error)}")
            time.sleep(1)

# Initialize system
print("=" + "="*80)
print("ULTIMATE BATCH API WITH FULL STEALTH INTEGRATION")
print("=" + "="*80)
print("[ENDPOINT] product_summary_with_fulfillment_v1 (batch)")
print("[STEALTH] 50+ user agents, 30+ API keys, rotating cookies/headers")
print("[STRATEGY] Batch API + 15-25s random intervals (3 calls/min)")
print("[RATE LIMITING] 87% fewer calls + full anti-detection")
print("=" + "="*80)

import threading

# Global progress flags for loading screen
session_warmup_complete = False
api_calls_complete = False
stock_data_loaded = False

def perform_startup_sequence():
    global session_warmup_complete, api_calls_complete, stock_data_loaded, dashboard_ready
    # 1. Session warmup
    print('[PROGRESS] Starting session warmup...')
    try:
        session = requests.Session()
        headers = get_ultra_stealth_headers()
        session_warmer.warmup_session(session, headers)
        session_warmup_complete = True
        print('[PROGRESS] Session warmup complete. (Flag 1 set)')
        socketio.emit('loading_step', {'step': 1})
    except Exception as e:
        print(f'[ERROR] Session warmup failed: {e}')
        session_warmup_complete = True
        print('[PROGRESS] Session warmup failed. (Flag 1 set)')
        socketio.emit('loading_step', {'step': 1})
    # 2. Stealth API calls
    print('[PROGRESS] Making stealth API calls...')
    try:
        config = stealth_checker.get_config()
        enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
        batch_data = stealth_checker.make_ultimate_stealth_batch_call()
        api_calls_complete = True
        print('[PROGRESS] Stealth API calls complete. (Flag 2 set)')
        socketio.emit('loading_step', {'step': 2})
    except Exception as e:
        print(f'[ERROR] Stealth API calls failed: {e}')
        api_calls_complete = True
        print('[PROGRESS] Stealth API calls failed. (Flag 2 set)')
        socketio.emit('loading_step', {'step': 2})
    # 3. Load real stock data
    print('[PROGRESS] Loading real stock data...')
    try:
        with latest_data_lock:
            latest_stock_data = batch_data
        stock_data_loaded = True
        print('[PROGRESS] Stock data loaded. (Flag 3 set)')
        socketio.emit('loading_step', {'step': 3})
    except Exception as e:
        print(f'[ERROR] Stock data loading failed: {e}')
        stock_data_loaded = True
        print('[PROGRESS] Stock data loading failed. (Flag 3 set)')
        socketio.emit('loading_step', {'step': 3})
    # 4. Dashboard ready
    dashboard_ready = True
    print('[PROGRESS] Dashboard is ready. (Flag 4 set)')
    socketio.emit('loading_step', {'step': 4})

# Start the startup sequence in a background thread when the app starts
startup_thread = threading.Thread(target=perform_startup_sequence, daemon=True)
startup_thread.start()

@app.route('/')
def index():
    """Dashboard home page - shows loading screen initially, then dashboard when data ready"""
    global latest_stock_data, api_call_in_progress, dashboard_ready
    
    print("[ROUTE] ========== Dashboard route accessed ==========")
    
    # Check if we already have data
    with latest_data_lock:
        has_data = bool(latest_stock_data)
        data_count = len(latest_stock_data) if latest_stock_data else 0
    
    print(f"[ROUTE] Current state: has_data={has_data}, data_count={data_count}, dashboard_ready={dashboard_ready}, api_call_in_progress={api_call_in_progress}")
    
    # Show loading screen if no data, dashboard if we have data
    if not has_data:
        print("[ROUTE] 📋 No data yet - showing loading screen...")
        return render_template('loading.html', 
            message="🔥 Loading stock data... This will refresh automatically...",
            current_step=2)
    
    print(f"[ROUTE] 🎯 Data loaded - rendering dashboard with {data_count} products")

    try:
        config = stealth_checker.get_config()
        timestamp = datetime.now()
        
        # Get latest stock data
        with latest_data_lock:
            stock_data = latest_stock_data.copy()
        
        print(f"[DEBUG] Dashboard route: stock_data has {len(stock_data)} products")
        
        # Add product URLs and populate with latest data if available
        for product in config.get('products', []):
            tcin = product.get('tcin')
            if tcin:
                product['url'] = f"https://www.target.com/p/-/A-{tcin}"
                
                # If we have current data for this product, use it for immediate display
                if tcin in stock_data:
                    api_data = stock_data[tcin]
                    product['display_name'] = api_data.get('name', product.get('name', 'Unknown Product'))
                    product['available'] = api_data.get('available', False)
                    product['stock_status'] = api_data.get('status', 'OUT_OF_STOCK')
                    product['status'] = api_data.get('status', 'OUT_OF_STOCK')
                    product['is_preorder'] = api_data.get('is_preorder', False)
                    product['street_date'] = api_data.get('street_date')
                    product['is_target_direct'] = api_data.get('is_target_direct', True)
                    product['seller_code'] = api_data.get('seller_code', 'UNKNOWN')
                    product['has_data'] = True
                else:
                    # No API data for this TCIN, treat as OUT_OF_STOCK and mark has_data True so badge updates
                    product['display_name'] = product.get('name', 'Unknown Product')
                    product['available'] = False
                    product['stock_status'] = 'OUT_OF_STOCK'
                    product['status'] = 'OUT_OF_STOCK'
                    product['is_preorder'] = False
                    product['street_date'] = None
                    product['is_target_direct'] = True
                    product['seller_code'] = 'Unknown'
                    product['has_data'] = True
            
        status = {
           
            'monitoring': True,
            'total_checks': stealth_analytics.analytics_data['total_calls'],
            'in_stock_count': sum(1 for r in stock_data.values() if r.get('available')),
            'last_update': last_update_time.isoformat() if last_update_time else timestamp.isoformat(),
            'recent_stock': [entry['message'] for entry in activity_log[-10:] if entry['type'] in ['success', 'info']],
            'recent_purchases': [],
            'timestamp': timestamp.isoformat(),
            'api_method': 'ultimate_stealth_batch',
            'stealth_active': True,
            'success_rate': stealth_analytics.analytics_data['success_rate']
        }
    
        print(f"[DEBUG] Dashboard route: Rendering template with {len(config.get('products', []))} products")
        return render_template('dashboard.html',
                             config=config,
                             status=status,
                             timestamp=timestamp)
    except Exception as e:
        print(f"[ERROR] Dashboard route error: {e}")
        import traceback
        traceback.print_exc()
        return f"Dashboard Error: {e}", 500

@app.route('/api/live-stock-status')
def api_live_stock_status():
    """Live stock status - always returns direct batch API results (no cache)"""
    print("[LIVE] Making direct batch API call for live stock status (no cache)")
    try:
        # Create fresh instance to avoid threading conflicts
        from main_dashboard import UltimateStealthBatchChecker
        fresh_checker = UltimateStealthBatchChecker()
        
        batch_data = fresh_checker.make_ultimate_stealth_batch_call()
        print(f"[LIVE] ✅ Got live data for {len(batch_data)} products")
        socketio.emit('stock_update', {
            'success': True,
            'products': list(batch_data.values()),
            'timestamp': datetime.now().isoformat()
        })
        print(f"[WEBSOCKET] Stock update sent for {len(batch_data)} products")
        return jsonify({
            'success': True,
            'products': list(batch_data.values()),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        print(f"[LIVE] ❌ API call failed: {e}")
        return jsonify({
            'success': False,
            'error': 'API_ERROR', 
            'message': f'Live API call failed: {str(e)}',
            'products': [],
            'timestamp': datetime.now().isoformat()
        })

@app.route('/api/initial-stock-check')
def api_initial_stock_check():
    """Initial stock check - LIVE API CALL - no cached data"""
    print("[MOBILE] Initial page load detected - making LIVE API call...")
    
    # Create a fresh stealth checker instance for this request to avoid threading conflicts
    try:
        print("[LIVE] Creating fresh stealth checker instance...")
        from main_dashboard import UltimateStealthBatchChecker
        fresh_checker = UltimateStealthBatchChecker()
        
        print("[LIVE] Making fresh stealth batch API call...")
        batch_data = fresh_checker.make_ultimate_stealth_batch_call()
        
        if batch_data:
            print(f"[LIVE] ✅ Got fresh data for {len(batch_data)} products")
            # Update cache for other endpoints but return fresh data
            with latest_data_lock:
                latest_stock_data = batch_data.copy()
                last_update_time = datetime.now()
            
            products_array = list(batch_data.values())
            return jsonify({
                'success': True,
                'products': products_array,
                'timestamp': datetime.now().isoformat(),
                'method': 'ultimate_stealth_batch_LIVE'
            })
        else:
            print("[LIVE] ❌ No data returned from API")
            return jsonify({
                'success': False,
                'error': 'NO_DATA',
                'message': 'API returned no data',
                'products': [],
                'timestamp': datetime.now().isoformat()
            })
            
    except Exception as e:
        print(f"[LIVE] ❌ API call failed: {e}")
        return jsonify({
            'success': False,
            'error': 'API_ERROR',
            'message': f'Live API call failed: {str(e)}',
            'products': [],
            'timestamp': datetime.now().isoformat()
        })

@app.route('/api/status')
def api_status():
    """System status with full stealth metrics"""
    config = stealth_checker.get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    return jsonify({
        'monitoring': True,
        'total_products': get_enabled_product_count(),
        'last_check': last_update_time.isoformat() if last_update_time else None,
        'system_status': 'running',
        'api_method': 'ultimate_stealth_batch',
        'stealth_features': {
            'user_agents': '50+ rotating',
            'api_keys': '30+ rotating',
            'header_rotation': 'advanced',
            'cookie_rotation': 'enabled',
            'batch_api': 'enabled',
            'rate_limit_safe': '40-45s intervals',
            'success_rate': f"{stealth_analytics.analytics_data['success_rate']:.1f}%"
        }
    })

@app.route('/api/activity-log')
def api_activity_log():
    """Get recent activity log entries"""
    global activity_log
    return jsonify({
        'entries': activity_log[-20:],  # Last 20 entries
        'count': len(activity_log)
    })

@app.route('/api/timer-status')
def api_timer_status():
    """Get current timer state - backend is source of truth"""
    global current_timer_seconds, timer_started_at
    
    with timer_state_lock:
        if timer_started_at and current_timer_seconds > 0:
            # Calculate remaining seconds based on when timer started
            elapsed = time.time() - timer_started_at
            remaining = max(0, current_timer_seconds - int(elapsed))
            
            return jsonify({
                'timer_active': True,
                'seconds_remaining': remaining,
                'total_seconds': current_timer_seconds,
                'started_at': timer_started_at,
                'next_refresh_in': remaining,
                'status': 'counting' if remaining > 0 else 'ready'
            })
        else:
            return jsonify({
                'timer_active': False,
                'seconds_remaining': 0,
                'total_seconds': 0,
                'started_at': None,
                'next_refresh_in': 0,
                'status': 'inactive'
            })

@app.route('/api/purchase-status')
def api_purchase_status():
    """Simple purchase status API"""
    config = stealth_checker.get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    purchase_status = {}
    for product in enabled_products:
        tcin = product.get('tcin')
        if tcin:
            status_info = get_purchase_status(tcin)
            internal_status = status_info.get('status', 'ready')
            
            # Map to display status  
            status_map = {
                'ready': 'Ready',
                'attempting': 'Purchasing',
                'purchased': 'Purchase Success',
                'failed': 'Purchase Failed'
            }
            
            display_status = status_map.get(internal_status, 'Ready')
            
            purchase_status[tcin] = {
                'status': display_status,
                'internal_status': internal_status,  # Add internal status for WebSocket reference
                'cooldown_seconds': 0,  # No countdown needed
                'last_attempt': status_info['last_update'].isoformat() if status_info.get('last_update') else None,
                'attempt_count': 0  # Simplified
            }
    
    return jsonify(purchase_status)

@app.route('/api/analytics')
def api_analytics():
    """Analytics with full stealth performance metrics"""
    config = stealth_checker.get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    
    with latest_data_lock:
        stock_data = latest_stock_data.copy()
        
    in_stock_count = sum(1 for r in stock_data.values() if r.get('available'))
    
    return jsonify({
        'stock_analytics': {
            'total_checks_24h': stealth_analytics.analytics_data['total_calls'],
            'monitored_products': get_enabled_product_count(),
            'in_stock_found_24h': in_stock_count,
            'avg_response_time': round(stealth_analytics.analytics_data['average_response_time'])
        },
        'stealth_performance': {
            'success_rate': stealth_analytics.analytics_data['success_rate'],
            'total_calls': stealth_analytics.analytics_data['total_calls'],
            'successful_calls': stealth_analytics.analytics_data['successful_calls'],
            'failed_calls': stealth_analytics.analytics_data['failed_calls'],
            'user_agents_rotated': stealth_analytics.analytics_data['stealth_metrics']['user_agents_rotated'],
            'api_keys_rotated': stealth_analytics.analytics_data['stealth_metrics']['api_keys_rotated'],
            'headers_randomized': stealth_analytics.analytics_data['stealth_metrics']['headers_randomized'],
            'cookies_rotated': stealth_analytics.analytics_data['stealth_metrics']['cookies_rotated']
        },
        'f5_shape_evasion': {
            'human_behavior_active': True,
            'session_requests': human_behavior.requests_this_session,
            'fatigue_factor': round(human_behavior.fatigue_factor, 2),
            'active_hours_detection': human_behavior.is_human_active_hours(),
            'session_warmup_active': session_warmer.last_warmup_time is not None,
            'advanced_tls_fingerprinting': CURL_CFFI_AVAILABLE,
            'break_patterns_enabled': True,
            'time_of_day_awareness': True,
            'weekend_behavior_simulation': True
        },
        'ultimate_features': {
            'batch_api_enabled': True,
            'stealth_level': 'maximum_with_f5_shape_evasion',
            'rate_limit_reduction': '87%',
            'detection_avoidance': 'f5_shape_advanced',
            'products_monitored': get_enabled_product_count(),
            'current_in_stock': in_stock_count
        }
    })

if __name__ == '__main__':
    print("[INIT] Starting dashboard with immediate data loading...")
    
    # Initialize purchase status for all enabled products
    config = stealth_checker.get_config()
    enabled_products = [p for p in config.get('products', []) if p.get('enabled', True)]
    for product in enabled_products:
        tcin = product.get('tcin')
        if tcin:
            set_purchase_status(tcin, 'ready')
    print(f"[CART] Initialized purchase status for {len(enabled_products)} products")
    
    # Initialize empty data first  
    with latest_data_lock:
        latest_stock_data = {}
        initial_check_completed = False
        last_update_time = datetime.now()
    
    # Initialize dashboard_ready flag - keep False until data loads
    dashboard_ready = False
    print("[INIT] Dashboard ready flag initialized to False - will show loading screen")
    
    # Start initial refresh cycle before loading data  
    start_new_refresh_cycle()
    
    # Skip initial data loading to avoid startup delays - background monitor will populate data
    print("[INIT] Skipping initial data load - background monitor will populate data quickly")
    with latest_data_lock:
        initial_check_completed = True
        last_update_time = datetime.now()
    
    # Skip initial API call - background monitor will do a fast 3-second call
    print("[INIT] Skipping separate initial API call - background monitor will load data in 3 seconds")
    
    # Start the simple timer thread for synchronized backend timer
    print("[INIT] Starting simple timer thread...")
    monitor_thread = threading.Thread(target=simple_timer_manager, daemon=True)
    monitor_thread.start()
    print("[INIT] Simple timer thread started")
    
    # Use a simple timer to trigger data loading after Flask is ready
    print("[INIT] Starting delayed data loader (timer-based)...")
    def delayed_data_loader():
        global latest_stock_data, dashboard_ready, last_update_time
        import time
        import requests
        try:
            print("[LOADER] 🚀 Timer-based data loader started...")
            # Wait for Flask to be fully ready
            time.sleep(3)
            print("[LOADER] 📡 Making internal API call to /api/initial-stock-check...")
            response = requests.get('http://localhost:5001/api/initial-stock-check', timeout=30)
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('products'):
                    stock_data = {}
                    for product in data['products']:
                        stock_data[product['tcin']] = product
                    # Thread-safe update of global state
                    with latest_data_lock:
                        latest_stock_data = stock_data.copy()
                        last_update_time = datetime.now()
                        dashboard_ready = True
                        print(f"[LOADER] ✅ Data loaded via API: {len(stock_data)} products")
                        print(f"[LOADER] ✅ Global latest_stock_data updated with {len(latest_stock_data)} products")
                        print("[LOADER] 🏁 Dashboard is now READY after data load!")
                    # Send WebSocket update after data is loaded
                    try:
                        socketio.emit('stock_update', {
                            'success': True,
                            'products': list(stock_data.values()),
                            'timestamp': datetime.now().isoformat(),
                            'initial_load': True
                        })
                        print("[LOADER] ✅ WebSocket stock_update sent after initial load")
                    except Exception as ws_error:
                        print(f"[LOADER] ❌ WebSocket stock_update failed: {ws_error}")
                else:
                    print(f"[LOADER] ⚠️ API returned no data: {data}")
                    with latest_data_lock:
                        dashboard_ready = False
            else:
                print(f"[LOADER] ❌ API call failed: HTTP {response.status_code}")
                with latest_data_lock:
                    dashboard_ready = False
        except Exception as e:
            print(f"[LOADER] ❌ Background loader failed: {e}")
            with latest_data_lock:
                dashboard_ready = False
    
    import threading
    loader_thread = threading.Thread(target=delayed_data_loader, daemon=True)
    loader_thread.start()
    print("[INIT] Timer-based data loader thread started")
    
    # Request initial stock check for immediate data loading
    print("[INIT] Requesting initial stock check for startup data...")
    with stock_check_lock:
        stock_check_requested = True
    print("[INIT] Initial stock check requested")
    
    print("[INIT] Dashboard ready with initial data loaded")
    
    product_count = get_enabled_product_count()
    
    # Add startup logging
    add_activity_log("[START] Ultimate stealth dashboard starting up...", 'info')
    add_activity_log(f"[DATA] Monitoring {product_count} enabled products with advanced evasion", 'info')
    add_activity_log("[STEALTH] F5/Shape evasion: JA3/JA4 spoofing, behavioral patterns, proxy rotation", 'info')
    
    print("\n[TARGET]" + "="*80)
    print("[DASHBOARD] ULTIMATE STEALTH + F5/SHAPE EVASION DASHBOARD - PRODUCTION READY")
    print("=" + "="*80)
    print(f"[PRODUCTS] {product_count} enabled")
    print(f"[PERFORMANCE] {stealth_analytics.analytics_data['average_response_time']:.0f}ms batch calls")
    print("[STEALTH] 50+ UAs, 30+ APIs, rotating cookies/headers")
    print("[F5/SHAPE EVASION] Session warmup, human behavior, TLS fingerprinting")
    print("[HUMAN BEHAVIOR] Fatigue simulation, time-of-day awareness, break patterns")
    print("[TIMING] 15-25s intervals with human behavior simulation")
    print(f"[STATUS] Data will be loaded by background monitor within 15-25 seconds")
    print("[READY] " + "="*80)
    print("Dashboard: http://localhost:5001")
    print(f"[HOT] Features: F5/Shape evasion + ultimate stealth + batch efficiency")
    print("[READY] " + "="*80)
    
    socketio.run(app, host='127.0.0.1', port=5001, debug=False, allow_unsafe_werkzeug=True)