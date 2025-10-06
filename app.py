#!/usr/bin/env python3
"""
Bulletproof Dashboard Application - Real-time updates with SSE and infinite purchase loops
Thread-safe, bulletproof error handling, immediate UI updates
"""

import json
import time
import random
import threading
import queue
import hashlib
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
import logging
import os
import pickle

# Import our bulletproof modules
from src.monitoring import StockMonitor
from src.purchasing import BulletproofPurchaseManager
# Removed test imports - using core system only

# Initialize Flask app
app = Flask(__name__, template_folder='dashboard/templates')
app.secret_key = 'bulletproof-dashboard-2025'

# Thread-safe shared data with locks and timer persistence
class ThreadSafeData:
    def __init__(self):
        self.lock = threading.Lock()
        self.stock_data = {}
        self.purchase_states = {}
        self.activity_log = []
        self.last_update_time = None
        self.monitor_running = False
        self.connected_clients = set()

        # Timer persistence - survives manual page refresh
        self.timer_start_time = None
        self.timer_duration = 20  # Store the actual cycle duration
        self.server_startup_time = time.time()
        self.last_cycle_completion = None
        self.is_server_startup = True  # True on fresh server start

        # Cycle tracking for atomic events
        self.cycle_counter = 0
        self.last_cycle_id = None

        # PRODUCTION-GRADE CACHE MANAGEMENT
        self.stock_data_checksum = None

        # Test mode support
        self.test_monitor = None
        self.test_mode_enabled = False
        self.stock_data_ttl = 30  # Max 30 seconds cache validity
        self.cache_miss_count = 0
        self.cache_hit_count = 0

        # Circuit breaker for API failures
        self.api_failure_count = 0
        self.api_circuit_open = False
        self.last_api_failure_time = None
        self.circuit_breaker_timeout = 60  # 1 minute timeout

    def initialize_timer(self, is_manual_refresh=False):
        """Initialize timer with persistence logic"""
        log_message = None
        log_level = "info"
        result = None

        with self.lock:
            now = time.time()

            if is_manual_refresh and self.timer_start_time:
                # Manual refresh - continue current cycle then start new
                elapsed = now - self.timer_start_time
                cycle_duration = 20  # Use fixed 20s for UI consistency

                if elapsed < cycle_duration:
                    # Current cycle still running - keep existing timer
                    remaining = cycle_duration - elapsed
                    log_message = f"Manual refresh: continuing current cycle ({remaining:.1f}s remaining)"
                    result = remaining
                else:
                    # Current cycle complete - start new one
                    self.timer_start_time = now
                    new_duration = random.randint(15, 25)
                    self.timer_duration = new_duration
                    log_message = f"Manual refresh: starting new {new_duration}s cycle"
                    result = new_duration
            else:
                # Server startup - fresh timer
                self.timer_start_time = now
                self.is_server_startup = False
                new_duration = random.randint(15, 25)
                self.timer_duration = new_duration
                log_message = f"Server startup: starting fresh {new_duration}s timer"
                result = new_duration

        # Log outside the lock to prevent deadlock
        if log_message:
            add_activity_log(log_message, log_level, "timer")

        return result

    def get_timer_status(self):
        """Get current timer status with countdown"""
        with self.lock:
            if not self.timer_start_time:
                return {'active': False, 'remaining': 0, 'total': 0}

            now = time.time()
            elapsed = now - self.timer_start_time
            cycle_duration = self.timer_duration  # Use actual cycle duration
            remaining = max(0, cycle_duration - elapsed)

            return {
                'active': True,
                'remaining': remaining,
                'total': cycle_duration,
                'elapsed': elapsed,
                'start_time': self.timer_start_time
            }

    def mark_cycle_complete(self):
        """Mark current cycle as complete and prepare for next"""
        with self.lock:
            self.last_cycle_completion = time.time()
            # Timer will be reset in next monitoring loop iteration

    def get_next_cycle_id(self):
        """Generate unique cycle ID for atomic events"""
        with self.lock:
            self.cycle_counter += 1
            cycle_id = int(time.time() * 1000) + self.cycle_counter
            self.last_cycle_id = cycle_id
            return cycle_id

    def calculate_stock_checksum(self, stock_data):
        """Calculate MD5 checksum of stock data for integrity validation"""
        data_str = json.dumps(stock_data, sort_keys=True)
        return hashlib.md5(data_str.encode()).hexdigest()

    def is_stock_cache_valid(self):
        """Check if current stock cache is still valid (TTL + integrity)"""
        if not self.last_update_time:
            return False

        # Check TTL (Time To Live)
        age_seconds = (datetime.now() - self.last_update_time).total_seconds()
        if age_seconds > self.stock_data_ttl:
            return False

        # Check data integrity
        current_checksum = self.calculate_stock_checksum(self.stock_data)
        if current_checksum != self.stock_data_checksum:
            print(f"[CACHE] Data integrity check failed - checksum mismatch")
            return False

        return True

    def update_stock_cache(self, fresh_stock_data):
        """Update stock cache with integrity validation"""
        # Calculate new checksum
        new_checksum = self.calculate_stock_checksum(fresh_stock_data)

        # CRITICAL FIX: Preserve WAITING_FOR_REFRESH entries that weren't in API response
        combined_data = fresh_stock_data.copy()

        # Add back any WAITING_FOR_REFRESH entries that weren't in the fresh data
        for tcin, data in self.stock_data.items():
            if tcin not in fresh_stock_data and data.get('status_detail') == 'WAITING_FOR_REFRESH':
                # Only preserve if the product name isn't a placeholder
                if not data.get('title', '').startswith('Product '):
                    print(f"[CACHE] Preserving WAITING_FOR_REFRESH status for {tcin}")
                    combined_data[tcin] = data
                else:
                    print(f"[CACHE] Clearing placeholder WAITING_FOR_REFRESH for {tcin}")
                    # This will force a fresh API call on next cycle

        # Update with combined data
        self.stock_data = combined_data
        self.stock_data_checksum = new_checksum
        self.last_update_time = datetime.now()
        self.cache_hit_count += 1

        print(f"[CACHE] Stock cache updated - checksum: {new_checksum[:8]}...")

    def handle_api_failure(self):
        """Circuit breaker pattern for API failures"""
        self.api_failure_count += 1
        self.last_api_failure_time = time.time()

        if self.api_failure_count >= 3:
            self.api_circuit_open = True
            print(f"[CIRCUIT_BREAKER] API circuit opened after {self.api_failure_count} failures")

    def handle_api_success(self):
        """Reset circuit breaker on successful API call"""
        if self.api_circuit_open:
            print(f"[CIRCUIT_BREAKER] API circuit closed - service recovered")

        self.api_failure_count = 0
        self.api_circuit_open = False
        self.last_api_failure_time = None

    def is_circuit_breaker_open(self):
        """Check if circuit breaker should block API calls"""
        if not self.api_circuit_open:
            return False

        # Auto-recovery after timeout
        if self.last_api_failure_time:
            time_since_failure = time.time() - self.last_api_failure_time
            if time_since_failure > self.circuit_breaker_timeout:
                print(f"[CIRCUIT_BREAKER] Attempting API recovery after {time_since_failure:.1f}s")
                self.api_circuit_open = False
                return False

        return True

# Event-based architecture for bulletproof coordination
class EventBus:
    def __init__(self):
        self.lock = threading.Lock()
        self.subscribers = {}

    def subscribe(self, event_type, callback):
        """Subscribe to an event type"""
        with self.lock:
            if event_type not in self.subscribers:
                self.subscribers[event_type] = []
            self.subscribers[event_type].append(callback)

    def publish(self, event_type, data=None):
        """Publish an event to all subscribers"""
        with self.lock:
            if event_type in self.subscribers:
                for callback in self.subscribers[event_type]:
                    try:
                        callback(data)
                    except Exception as e:
                        print(f"[EVENT] Error in {event_type} callback: {e}")

# Global thread-safe data and event system
shared_data = ThreadSafeData()
event_bus = EventBus()

# Core monitoring using purchase manager only
realtime_validator = None

# SSE event queue for real-time updates
sse_queue = queue.Queue()

# Activity log persistence
ACTIVITY_LOG_FILE = 'logs/activity_log.pkl'

def load_activity_log():
    """Load activity log from file (thread-safe)"""
    try:
        if os.path.exists(ACTIVITY_LOG_FILE):
            with open(ACTIVITY_LOG_FILE, 'rb') as f:
                log_data = pickle.load(f)
                with shared_data.lock:
                    shared_data.activity_log = log_data
                print(f"[SYSTEM] Loaded {len(log_data)} activity log entries")
        else:
            os.makedirs('logs', exist_ok=True)
            with shared_data.lock:
                shared_data.activity_log = []
            print("[SYSTEM] Created new activity log")
    except Exception as e:
        print(f"[WARN] Failed to load activity log: {e}")
        with shared_data.lock:
            shared_data.activity_log = []

def save_activity_log():
    """Save activity log to file with rotation (thread-safe)"""
    try:
        os.makedirs('logs', exist_ok=True)
        with shared_data.lock:
            log_copy = shared_data.activity_log.copy()

        # PRODUCTION-GRADE: Check log size and rotate if needed
        if os.path.exists(ACTIVITY_LOG_FILE):
            file_size = os.path.getsize(ACTIVITY_LOG_FILE)
            if file_size > 10 * 1024 * 1024:  # 10MB rotation threshold
                rotate_activity_log()

        temp_file = f"{ACTIVITY_LOG_FILE}.tmp.{os.getpid()}"
        with open(temp_file, 'wb') as f:
            pickle.dump(log_copy, f)

        if os.path.exists(ACTIVITY_LOG_FILE):
            os.remove(ACTIVITY_LOG_FILE)
        os.rename(temp_file, ACTIVITY_LOG_FILE)
    except Exception as e:
        print(f"[WARN] Failed to save activity log: {e}")

def rotate_activity_log():
    """Rotate activity log files for production archival"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        archive_file = f"{ACTIVITY_LOG_FILE}.{timestamp}"

        if os.path.exists(ACTIVITY_LOG_FILE):
            os.rename(ACTIVITY_LOG_FILE, archive_file)
            print(f"[LOG_ROTATION] Archived activity log to {archive_file}")

            # Clean up old archives (keep last 10)
            log_dir = os.path.dirname(ACTIVITY_LOG_FILE)
            archive_pattern = os.path.basename(ACTIVITY_LOG_FILE) + "."
            archives = []

            for file in os.listdir(log_dir):
                if file.startswith(archive_pattern) and file != os.path.basename(ACTIVITY_LOG_FILE):
                    archives.append(os.path.join(log_dir, file))

            # Sort by modification time and keep newest 10
            archives.sort(key=os.path.getmtime, reverse=True)
            for old_archive in archives[10:]:
                os.remove(old_archive)
                print(f"[LOG_ROTATION] Cleaned up old archive: {old_archive}")

    except Exception as e:
        print(f"[WARN] Failed to rotate activity log: {e}")

def add_activity_log(message, level="info", category="system"):
    """Add entry to activity log with timestamp and persistence (thread-safe)"""
    timestamp = datetime.now()

    entry = {
        'timestamp': timestamp.isoformat(),  # Convert datetime to ISO string for JSON serialization
        'message': message,
        'level': level,
        'category': category,
        'time_str': timestamp.strftime('%H:%M:%S'),
        'date_str': timestamp.strftime('%Y-%m-%d'),
        'full_time': timestamp.strftime('%Y-%m-%d %H:%M:%S')
    }

    with shared_data.lock:
        shared_data.activity_log.insert(0, entry)

        # Keep only last 500 entries
        if len(shared_data.activity_log) > 500:
            shared_data.activity_log = shared_data.activity_log[:500]

    # Save to file
    save_activity_log()

    # Broadcast to SSE clients using enhanced SSE (but prevent recursive logging)
    broadcast_sse_event('activity_log', entry)

    print(f"[{timestamp.strftime('%H:%M:%S')}] [{category.upper()}] {message}")

def purchase_status_callback(tcin, status, state):
    """Callback for real-time purchase status updates"""
    print(f"[REALTIME] {tcin} status changed to: {status}")

    # Update shared state
    with shared_data.lock:
        if tcin not in shared_data.purchase_states:
            shared_data.purchase_states[tcin] = {}
        shared_data.purchase_states[tcin].update({
            'status': status,
            'order_number': state.get('order_number'),
            'failure_reason': state.get('failure_reason'),
            'product_title': state.get('product_title'),
            'completes_at': state.get('completes_at')
        })

    # Broadcast to SSE clients - convert any datetime objects to strings
    json_safe_state = {}
    for key, value in state.items():
        if isinstance(value, datetime):
            json_safe_state[key] = value.isoformat()
        elif hasattr(value, '__class__') and 'time' in value.__class__.__name__.lower():
            # Handle other time-like objects (like timestamp floats)
            json_safe_state[key] = value
        else:
            json_safe_state[key] = value

    enhanced_broadcast_sse_event('purchase_status', {
        'tcin': tcin,
        'status': status,
        'state': json_safe_state
    })

    # Log purchase events
    if status == 'attempting':
        add_activity_log(f"MOCK: Starting purchase attempt: {state.get('product_title', tcin)}", "info", "purchase")
    elif status == 'purchased':
        add_activity_log(f"MOCK: Purchase successful: {state.get('product_title', tcin)} - Order: {state.get('order_number')}", "success", "purchase")
    elif status == 'failed':
        add_activity_log(f"MOCK: Purchase failed: {state.get('product_title', tcin)} - {state.get('failure_reason')}", "error", "purchase")

def broadcast_sse_event(event_type, data):
    """Broadcast SSE event to all connected clients"""
    try:
        event_data = {
            'type': event_type,
            'data': data,
            'timestamp': datetime.now().isoformat()
        }
        sse_queue.put(event_data)
    except Exception as e:
        print(f"[SSE] Failed to broadcast event: {e}")

def broadcast_atomic_api_cycle_event(cycle_id, stock_data, purchase_changes, timer_info, summary):
    """Broadcast atomic API cycle completion event with all changes bundled"""
    try:
        # Prepare stock updates
        stock_updates = {}
        for tcin, data in stock_data.items():
            stock_updates[tcin] = {
                'title': data.get('title', f'Product {tcin}'),
                'in_stock': data.get('in_stock', False),
                'status_detail': data.get('status_detail', 'UNKNOWN'),
                'last_checked': data.get('last_checked'),
                'is_preorder': data.get('is_preorder', False)
            }

        # Prepare timer sync info
        timer_status = shared_data.get_timer_status()

        # Create atomic event
        atomic_event = {
            'type': 'api_cycle_complete',
            'cycle_id': cycle_id,
            'timestamp': datetime.now().isoformat(),
            'data': {
                'stock_updates': stock_updates,
                'purchase_state_changes': purchase_changes,
                'timer_sync': {
                    'current_remaining': timer_status.get('remaining', 0),
                    'total_duration': timer_status.get('total', 20),
                    'next_cycle_starts_at': time.time() + timer_status.get('remaining', 0)
                },
                'summary': summary
            }
        }

        sse_queue.put(atomic_event)
        print(f"[SSE] Broadcast atomic API cycle event {cycle_id}")

    except Exception as e:
        print(f"[SSE] Failed to broadcast atomic cycle event: {e}")

# Event-driven thread managers for bulletproof architecture
class StockMonitorThread:
    """Dedicated thread for stock monitoring with timer persistence"""
    def __init__(self, event_bus, shared_data):
        self.event_bus = event_bus
        self.shared_data = shared_data
        self.stock_monitor = StockMonitor()
        self.running = False
        self.thread = None

    def start(self):
        """Start the stock monitoring thread"""
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        # Log from a separate thread to avoid deadlock
        threading.Thread(target=lambda: add_activity_log("Stock monitor thread started", "success", "system"), daemon=True).start()

    def stop(self):
        """Stop the stock monitoring thread"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _monitor_loop(self):
        """Main stock monitoring loop with timer persistence"""
        print("[STOCK_MONITOR] Initializing with timer persistence...")

        # Initialize timer with persistence logic
        is_manual_refresh = not self.shared_data.is_server_startup
        print(f"[STOCK_MONITOR] is_manual_refresh: {is_manual_refresh}")
        initial_cycle_duration = self.shared_data.initialize_timer(is_manual_refresh)
        print(f"[STOCK_MONITOR] initialize_timer returned: {initial_cycle_duration}")

        if initial_cycle_duration is None:
            print("[STOCK_MONITOR] WARNING: initial_cycle_duration is None, using default 20s")
            initial_cycle_duration = 20

        print(f"[STOCK_MONITOR] Starting with {initial_cycle_duration:.1f}s initial cycle")

        # WARMUP DELAY ON STARTUP - Give browser time to launch and initialize before first purchase attempt
        if not is_manual_refresh:
            print("[STOCK_MONITOR] 🔥 Browser warmup: waiting 10 seconds for browser to launch and navigate to target.com...")
            for i in range(10):
                if not self.running:
                    return
                time.sleep(1)
            print("[STOCK_MONITOR] ✅ Browser warmup complete, ready for stock checks and purchases")

        # IMMEDIATE STOCK CHECK ON STARTUP ONLY - for instant stock status display AND immediate purchase attempts
        if not is_manual_refresh:
            print("[STOCK_MONITOR] Performing IMMEDIATE startup stock check...")
            startup_stock_data = self._check_stock()
            if startup_stock_data:
                print(f"[STOCK_MONITOR] Startup stock check completed: {len(startup_stock_data)} products")

                # Publish stock update event immediately - this will trigger immediate purchase attempts for in-stock items
                self.event_bus.publish('stock_updated', {
                    'stock_data': startup_stock_data,
                    'timestamp': time.time()
                })
                print("[STOCK_MONITOR] Published startup stock update - purchase attempts will start immediately for in-stock items")
            else:
                print("[STOCK_MONITOR] Startup stock check failed, will retry in cycle")
        else:
            print("[STOCK_MONITOR] Manual refresh - respecting existing timer, no immediate check")

        # Wait for initial cycle duration with 1-second checks
        print(f"[STOCK_MONITOR] Waiting {initial_cycle_duration:.1f}s for first cycle...")
        for i in range(int(initial_cycle_duration)):
            if not self.running:
                return
            time.sleep(1)

        while self.running:
            try:
                # Generate new cycle duration for this iteration
                cycle_duration = random.randint(15, 25)
                start_time = time.time()

                with self.shared_data.lock:
                    self.shared_data.timer_start_time = start_time
                    self.shared_data.timer_duration = cycle_duration

                print(f"[STOCK_MONITOR] Starting {cycle_duration}s cycle...")

                # Perform stock check with test mode awareness
                with self.shared_data.lock:
                    test_mode_active = (hasattr(self.shared_data, 'test_monitor') and
                                       self.shared_data.test_monitor is not None and
                                       self.shared_data.test_monitor.test_mode)
                    if test_mode_active:
                        scenario = self.shared_data.test_monitor.test_scenario
                        cycle_count = self.shared_data.test_monitor.test_cycle_count + 1
                        print(f"[STOCK_MONITOR] Performing TEST MODE stock check (scenario: {scenario}, cycle: {cycle_count})...")
                    else:
                        print("[STOCK_MONITOR] Performing stock check...")

                stock_data = self._check_stock()

                if stock_data:
                    # Publish stock update event (will trigger atomic API cycle processing)
                    self.event_bus.publish('stock_updated', {
                        'stock_data': stock_data,
                        'timestamp': time.time()
                    })

                    # Mark cycle complete
                    self.shared_data.mark_cycle_complete()

                    # NOTE: Old individual SSE events (stock_update, activity_log) are now replaced
                    # by atomic api_cycle_complete event from PurchaseManagerThread._handle_stock_update()

                else:
                    print("[STOCK_MONITOR] Stock check failed, continuing cycle")

                # Wait for cycle duration with 1-second checks
                print(f"[STOCK_MONITOR] Waiting {cycle_duration}s for next cycle...")
                for i in range(cycle_duration):
                    if not self.running:
                        return
                    time.sleep(1)

            except Exception as e:
                print(f"[STOCK_MONITOR] Error in monitoring loop: {e}")
                add_activity_log(f"Stock monitoring error: {str(e)}", "error", "system")
                time.sleep(5)  # Brief pause before retrying

    def _check_stock(self):
        """Check stock with bulletproof error handling and test mode support"""
        try:
            # Check if test mode is enabled and use test monitor
            monitor_to_use = self.stock_monitor

            with self.shared_data.lock:
                if (hasattr(self.shared_data, 'test_monitor') and
                    self.shared_data.test_monitor is not None and
                    self.shared_data.test_monitor.test_mode):
                    monitor_to_use = self.shared_data.test_monitor
                    print("[STOCK_MONITOR] Using test monitor for stock check")

            # Check circuit breaker before API call
            if self.shared_data.is_circuit_breaker_open():
                print("[STOCK_MONITOR] Circuit breaker open - skipping API call")
                return None

            stock_data = monitor_to_use.check_stock()
            if stock_data:
                # Update cache with fresh stock data
                with self.shared_data.lock:
                    self.shared_data.update_stock_cache(stock_data)

                    # Mark API success for circuit breaker
                    self.shared_data.handle_api_success()

                # Update catalog names with real product names (when available)
                update_catalog_names_from_stock_data(stock_data)

                # Log results (activity log will be added by atomic cycle processing)
                in_stock_count = sum(1 for data in stock_data.values() if data.get('in_stock'))
                total_count = len(stock_data)

                # NOTE: Old individual SSE broadcasts removed - now handled by atomic api_cycle_complete event
                # Individual activity_log and stock_update events are replaced by atomic processing

                return stock_data

        except Exception as e:
            # PRODUCTION-GRADE: Enhanced error handling with circuit breaker
            error_context = "API"
            with self.shared_data.lock:
                # Update circuit breaker on API failure
                self.shared_data.handle_api_failure()

                if (hasattr(self.shared_data, 'test_monitor') and
                    self.shared_data.test_monitor is not None and
                    self.shared_data.test_monitor.test_mode):
                    error_context = "TEST_MODE"
                    print(f"[STOCK_MONITOR] Test mode stock check failed: {e}")
                    add_activity_log(f"Test mode stock check failed: {str(e)}", "error", "test_mode")
                else:
                    print(f"[STOCK_MONITOR] Stock check failed (failure #{self.shared_data.api_failure_count}): {e}")
                    add_activity_log(f"Stock check failed: {str(e)} (failure #{self.shared_data.api_failure_count})", "error", "api")

                    # If circuit breaker opened, add alert
                    if self.shared_data.api_circuit_open:
                        add_activity_log("⚠️ API circuit breaker activated - service degraded", "warning", "circuit_breaker")

            return None

class PurchaseManagerThread:
    """Dedicated thread for purchase management with atomic state transitions"""
    def __init__(self, event_bus, shared_data):
        self.event_bus = event_bus
        self.shared_data = shared_data
        self.purchase_manager = BulletproofPurchaseManager(status_callback=purchase_status_callback)
        self.running = False
        self.thread = None

        # Initialize real-time validation system
        # Removed test validation system - using core purchase manager only
        print("[SYSTEM] Core bulletproof monitoring active")

        # Subscribe to stock update events
        self.event_bus.subscribe('stock_updated', self._handle_stock_update)

    def _initialize_session_system(self):
        """Initialize persistent session system - called only once"""
        def session_init_task():
            try:
                print("[PURCHASE_THREAD] 🚀 Initializing persistent session system...")

                # Run async session initialization
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                session_ready = loop.run_until_complete(self.purchase_manager._ensure_session_ready())
                loop.close()

                if session_ready:
                    print("[PURCHASE_THREAD] ✅ Persistent session system ready - browser should be at Target.com")
                    add_activity_log("Persistent session initialized - browser at Target.com", "success", "session")
                else:
                    print("[PURCHASE_THREAD] ⚠️ Session system failed - falling back to mock purchasing")
                    add_activity_log("Session system failed - using mock purchasing mode", "warning", "session")

            except Exception as e:
                print(f"[PURCHASE_THREAD] ❌ Session initialization error: {e}")
                add_activity_log(f"Session initialization error: {str(e)}", "error", "session")

        # Run session initialization in background to avoid blocking startup
        threading.Thread(target=session_init_task, daemon=True).start()

    def start(self):
        """Start the purchase management thread"""
        self.running = True

        # Initialize session system ONCE at startup
        self._initialize_session_system()

        self.thread = threading.Thread(target=self._purchase_loop, daemon=True)
        self.thread.start()

        # Start real-time validation
        global realtime_validator
        if realtime_validator and not realtime_validator.running:
            realtime_validator.start_validation()

        # Log from a separate thread to avoid deadlock
        threading.Thread(target=lambda: add_activity_log("Purchase manager thread started", "success", "system"), daemon=True).start()

    def stop(self):
        """Stop the purchase management thread"""
        self.running = False

        # Stop session system
        self._cleanup_session_system()

        # Stop real-time validation
        global realtime_validator
        if realtime_validator and realtime_validator.running:
            realtime_validator.stop_validation()

        if self.thread:
            self.thread.join(timeout=5)

    def _cleanup_session_system(self):
        """Clean up session system resources"""
        def cleanup_task():
            try:
                if hasattr(self.purchase_manager, 'session_keepalive') and self.purchase_manager.session_keepalive:
                    self.purchase_manager.session_keepalive.stop()
                    print("[SESSION] Keep-alive service stopped")

                if hasattr(self.purchase_manager, 'session_manager') and self.purchase_manager.session_manager:
                    # Run async cleanup
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.purchase_manager.session_manager.cleanup())
                    loop.close()
                    print("[SESSION] Session manager cleaned up")

            except Exception as e:
                print(f"[SESSION] Cleanup error: {e}")

        # Run cleanup in background
        threading.Thread(target=cleanup_task, daemon=True).start()

    def _purchase_loop(self):
        """Main purchase management loop - checks completions every second"""
        print("[PURCHASE_MANAGER] Starting 5-second completion monitoring...")

        while self.running:
            try:
                # Check and complete purchases every second for 5-second timing
                completed_purchases = self.purchase_manager.check_and_complete_purchases()

                if completed_purchases:
                    print(f"[PURCHASE_MANAGER] Completed {len(completed_purchases)} purchases")
                    for tcin, outcome in completed_purchases:
                        print(f"[PURCHASE_MANAGER] {tcin} -> {outcome}")

                # Update shared purchase states
                purchase_states = self.purchase_manager.get_all_states()
                with self.shared_data.lock:
                    self.shared_data.purchase_states.update(purchase_states)

                time.sleep(1)  # Check every second for precise 5-second timing

            except Exception as e:
                print(f"[PURCHASE_MANAGER] Error in purchase loop: {e}")
                add_activity_log(f"Purchase management error: {str(e)}", "error", "purchase")
                time.sleep(1)

    def _handle_stock_update(self, event_data):
        """Handle stock update events with atomic response - NEW ATOMIC VERSION"""
        cycle_start_time = time.time()
        cycle_id = None

        try:
            stock_data = event_data['stock_data']
            current_time = time.time()

            # Smart duplicate prevention - allow legitimate cycles but prevent rapid-fire conflicts
            if hasattr(self, '_last_stock_update_time'):
                time_since_last = current_time - self._last_stock_update_time
                if time_since_last < 0.5:  # Short window to prevent race conditions
                    print(f"[PURCHASE_DEBUG] Preventing rapid-fire stock update (only {time_since_last:.2f}s since last)")
                    return

            self._last_stock_update_time = current_time

            # Generate unique cycle ID
            cycle_id = shared_data.get_next_cycle_id()
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Starting API cycle processing at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Stock data contains {len(stock_data)} products")

            # STEP 1: Stock-aware reset of completed purchases (only for OUT OF STOCK products)
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] STEP 1: Stock-aware reset of completed purchases...")

            # Get detailed state before reset
            reset_tcins = self.purchase_manager.get_completed_purchase_tcins()  # Get list before reset
            all_states_before = self.purchase_manager.get_all_states()
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Found {len(reset_tcins)} completed purchases to evaluate: {reset_tcins}")

            # Log current states for debugging
            for tcin in reset_tcins:
                state = all_states_before.get(tcin, {})
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Before reset - {tcin}: {state.get('status')} (order: {state.get('order_number', 'N/A')})")

            # Perform the stock-aware reset (only resets OUT OF STOCK completed purchases)
            reset_count = self.purchase_manager.reset_completed_purchases_by_stock_status(stock_data)

            # Verify the stock-aware reset completed correctly
            all_states_after = self.purchase_manager.get_all_states()
            reset_verification_errors = []

            # Create stock status lookup for verification
            stock_lookup = {}
            for tcin, data in stock_data.items():
                in_stock = data.get('in_stock', False)
                stock_lookup[tcin] = in_stock

            for tcin in reset_tcins:
                before_state = all_states_before.get(tcin, {})
                after_state = all_states_after.get(tcin, {})
                before_status = before_state.get('status')
                actual_status = after_state.get('status')
                is_in_stock = stock_lookup.get(tcin, False)

                if is_in_stock:
                    # IN STOCK products should keep their purchase status unchanged
                    if actual_status != before_status:
                        reset_verification_errors.append(f"{tcin}: IN STOCK should remain '{before_status}' but got '{actual_status}'")
                    else:
                        print(f"[CYCLE ATOMIC CYCLE {cycle_id}] OK Verified unchanged - {tcin}: {actual_status} (IN STOCK)")
                else:
                    # OUT OF STOCK products should be reset to ready
                    if actual_status != 'ready':
                        reset_verification_errors.append(f"{tcin}: OUT OF STOCK should be 'ready' but got '{actual_status}'")
                    else:
                        print(f"[CYCLE ATOMIC CYCLE {cycle_id}] OK Verified reset - {tcin}: {actual_status} (OUT OF STOCK)")

            if reset_verification_errors:
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] ERROR STOCK-AWARE RESET VERIFICATION FAILED:")
                for error in reset_verification_errors:
                    print(f"[CYCLE ATOMIC CYCLE {cycle_id}] ERROR   {error}")
                # Continue processing but log the error
                add_activity_log(f"Cycle {cycle_id}: Stock-aware reset verification failed for {len(reset_verification_errors)} purchases", "error", "purchase_reset")
            else:
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] OK Stock-aware reset verification passed for all {len(reset_tcins)} purchases")

            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Reset {reset_count} purchases to ready")

            # STEP 2: Process stock data according to state rules
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] STEP 2: Processing stock data...")
            in_stock_tcins = [tcin for tcin, data in stock_data.items() if data.get('in_stock')]
            out_stock_tcins = [tcin for tcin, data in stock_data.items() if not data.get('in_stock')]
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] IN STOCK: {in_stock_tcins}")
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] OUT OF STOCK: {out_stock_tcins}")

            # IN STOCK + ready status -> go to "attempting"
            # OUT OF STOCK -> stay in "ready" status
            purchase_actions = self.purchase_manager.process_stock_data(stock_data)
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Generated {len(purchase_actions)} purchase actions")

            # STEP 3: Prepare atomic purchase changes data
            purchase_changes = {
                'resets': reset_tcins,
                'new_attempts': []
            }

            for action in purchase_actions:
                attempt_data = {
                    'tcin': action['tcin'],
                    'status': 'attempting',
                    'completes_at': action.get('completes_at'),
                    'final_outcome': action.get('final_outcome'),
                    'product_title': action.get('title')
                }
                purchase_changes['new_attempts'].append(attempt_data)

            # STEP 4: Prepare summary data
            in_stock_count = sum(1 for data in stock_data.values() if data.get('in_stock'))
            summary = {
                'total_products': len(stock_data),
                'in_stock_count': in_stock_count,
                'new_attempts_count': len(purchase_actions),
                'resets_count': reset_count
            }

            # STEP 5: Broadcast single atomic event with ALL changes
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] STEP 5: Broadcasting atomic SSE event...")
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Event contains: {summary['total_products']} products, {summary['in_stock_count']} in stock, {summary['new_attempts_count']} attempts, {summary['resets_count']} resets")

            broadcast_atomic_api_cycle_event(
                cycle_id=cycle_id,
                stock_data=stock_data,
                purchase_changes=purchase_changes,
                timer_info={},  # Will be filled by broadcast function
                summary=summary
            )

            # Add activity log entry
            add_activity_log(
                f"API Cycle {cycle_id}: {summary['total_products']} products • {summary['in_stock_count']} in stock • {summary['new_attempts_count']} new attempts • {summary['resets_count']} resets",
                "info", "api_cycle"
            )

            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] OK COMPLETED at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} - {len(purchase_actions)} attempts started, {reset_count} resets")
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Next cycle will be #{cycle_id + 1}")

            # ENHANCED MONITORING: Record comprehensive cycle data
            try:
                cycle_duration = time.time() - cycle_start_time
                final_purchase_states = self.purchase_manager.get_all_states()

                monitoring_system.record_cycle_data(
                    cycle_id=str(cycle_id),
                    cycle_duration=cycle_duration,
                    stock_data=stock_data,
                    purchase_actions=purchase_actions,
                    completions=[],  # Completions happen in separate check
                    resets=reset_count,
                    purchase_states=final_purchase_states
                )

                print(f"[CYCLE MONITORING] Cycle {cycle_id} data recorded - duration: {cycle_duration:.3f}s")

            except Exception as monitor_error:
                print(f"[CYCLE MONITORING] Error recording cycle data: {monitor_error}")

        except Exception as e:
            print(f"[PURCHASE_MANAGER] Error in atomic stock update: {e}")
            add_activity_log(f"API cycle processing failed: {str(e)}", "error", "api_cycle")

            # ENHANCED MONITORING: Record failed cycle
            try:
                if cycle_id:
                    cycle_duration = time.time() - cycle_start_time
                    monitoring_system.record_cycle_data(
                        cycle_id=str(cycle_id),
                        cycle_duration=cycle_duration,
                        stock_data={},
                        purchase_actions=[],
                        completions=[],
                        resets=0,
                        purchase_states={}
                    )
            except Exception as monitor_error:
                print(f"[CYCLE MONITORING] Error recording failed cycle: {monitor_error}")

def monitoring_loop():
    """Legacy monitoring loop - replaced by event-driven architecture"""
    print("[MONITOR] Starting bulletproof event-driven architecture...")

    # Initialize event-driven thread managers
    stock_thread = StockMonitorThread(event_bus, shared_data)
    purchase_thread = PurchaseManagerThread(event_bus, shared_data)

    with shared_data.lock:
        shared_data.monitor_running = True

    # Start independent threads
    stock_thread.start()
    purchase_thread.start()

    add_activity_log("Bulletproof event-driven monitoring started", "success", "system")

    try:
        # Keep main thread alive and monitor thread health
        while True:
            with shared_data.lock:
                if not shared_data.monitor_running:
                    break

            # Monitor thread health every 10 seconds
            time.sleep(10)

            if not stock_thread.thread.is_alive():
                print("[MONITOR] Stock thread died, restarting...")
                add_activity_log("Stock monitor thread crashed, restarting", "error", "system")
                stock_thread = StockMonitorThread(event_bus, shared_data)
                stock_thread.start()

            if not purchase_thread.thread.is_alive():
                print("[MONITOR] Purchase thread died, restarting...")
                add_activity_log("Purchase manager thread crashed, restarting", "error", "system")
                purchase_thread = PurchaseManagerThread(event_bus, shared_data)
                purchase_thread.start()

    finally:
        # Clean shutdown
        print("[MONITOR] Shutting down threads...")
        stock_thread.stop()
        purchase_thread.stop()
        add_activity_log("Monitoring threads stopped", "info", "system")

def start_monitoring():
    """Start the background monitoring thread"""
    # Initialize test monitor for simulated stock data
    shared_data.test_monitor = StockMonitor()
    print("[SYSTEM] Test monitor initialized for simulated stock data")

    monitor_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitor_thread.start()
    add_activity_log("Background monitoring started", "success", "system")
    print("[SYSTEM] Background monitoring started")

@app.route('/')
def index():
    """Main dashboard route"""
    # Load product configuration
    stock_monitor = StockMonitor()
    config = stock_monitor.get_config()

    # Get current data (thread-safe)
    with shared_data.lock:
        current_stock_data = shared_data.stock_data.copy()
        current_purchase_states = shared_data.purchase_states.copy()
        current_activity_log = shared_data.activity_log.copy()
        last_update = shared_data.last_update_time

    # Prepare products for template
    for product in config.get('products', []):
        tcin = product.get('tcin')

        # Get stock data
        stock_info = current_stock_data.get(tcin, {})

        # Get purchase state
        purchase_state = current_purchase_states.get(tcin, {'status': 'ready'})

        # Update product with combined data
        product.update({
            'display_name': stock_info.get('title') or product.get('name', f'Product {tcin}'),
            'available': stock_info.get('in_stock', False),
            'stock_status': stock_info.get('status_detail', 'LOADING'),
            'status': stock_info.get('status_detail', 'LOADING'),
            'has_data': bool(stock_info),
            'url': f"https://www.target.com/p/-/A-{tcin}",
            'enabled': product.get('enabled', True),

            # Purchase status
            'purchase_status': purchase_state.get('status', 'ready'),
            'purchase_attempt_count': purchase_state.get('attempt_count', 0),
            'order_number': purchase_state.get('order_number'),
            'last_purchase_attempt': purchase_state.get('last_attempt'),
            'purchase_completed_at': purchase_state.get('completed_at'),
            'completes_at': purchase_state.get('completes_at')  # For countdown
        })

    # Build status
    in_stock_count = sum(1 for p in config.get('products', []) if p.get('available', False))

    with shared_data.lock:
        monitor_running = shared_data.monitor_running

    status = {
        'monitoring': monitor_running,
        'in_stock_count': in_stock_count,
        'last_update': last_update.isoformat() if last_update else datetime.now().isoformat(),
        'data_age_seconds': int((datetime.now() - last_update).total_seconds()) if last_update else 0,
        'timestamp': datetime.now(),
        'data_loaded': bool(current_stock_data)
    }

    # Load catalog data with active status
    catalog_config = get_catalog_config()
    active_tcins = [p.get('tcin') for p in config.get('products', [])]

    # COHESIVE SYSTEM: Auto-populate catalog with any active products not already there
    existing_catalog_tcins = [p['tcin'] for p in catalog_config.get('catalog', [])]
    for product in config.get('products', []):
        tcin = product.get('tcin')
        if tcin and tcin not in existing_catalog_tcins:
            # Add active product to catalog
            new_catalog_item = {
                'tcin': tcin,
                'name': product.get('name', f'Product {tcin}'),
                'date_added': datetime.now().isoformat(),
                'url': f"https://www.target.com/p/-/A-{tcin}"
            }
            catalog_config['catalog'].append(new_catalog_item)
            print(f"[COHESIVE] Auto-added active product {tcin} to catalog on page load")

    # Save updated catalog if we added anything
    if catalog_config.get('catalog') and any(item['tcin'] not in existing_catalog_tcins for item in catalog_config['catalog']):
        save_catalog_config(catalog_config)

    # COHESIVE SYSTEM: Add active monitoring status to each catalog item
    for catalog_item in catalog_config.get('catalog', []):
        catalog_item['is_actively_monitored'] = catalog_item['tcin'] in active_tcins

    return render_template('simple_dashboard.html',
                         config=config,
                         catalog=catalog_config,
                         status=status,
                         activity_log=current_activity_log,
                         timestamp=datetime.now())

@app.route('/api/stream')
def sse_stream():
    """Server-Sent Events stream for real-time updates"""
    def event_stream():
        client_id = id(threading.current_thread())
        shared_data.connected_clients.add(client_id)

        try:
            while True:
                try:
                    # Get event from queue with timeout
                    event = sse_queue.get(timeout=30)

                    # Format SSE data
                    yield f"data: {json.dumps(event)}\n\n"

                except queue.Empty:
                    # Send heartbeat to keep connection alive
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"

                except Exception as e:
                    print(f"[SSE] Error in event stream: {e}")
                    break

        finally:
            shared_data.connected_clients.discard(client_id)

    response = Response(event_stream(), content_type='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Connection'] = 'keep-alive'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

@app.route('/api/status')
def api_status():
    """API endpoint for current status"""
    with shared_data.lock:
        return jsonify({
            'stock_data': shared_data.stock_data,
            'purchase_states': shared_data.purchase_states,
            'monitoring': shared_data.monitor_running,
            'connected_clients': len(shared_data.connected_clients),
            'last_update': shared_data.last_update_time.isoformat() if shared_data.last_update_time else None
        })

@app.route('/api/timer-status')
def api_timer_status():
    """API endpoint for timer countdown status"""
    timer_status = shared_data.get_timer_status()
    return jsonify(timer_status)

@app.route('/api/purchase-states')
def api_purchase_states():
    """API endpoint for current purchase states"""
    with shared_data.lock:
        purchase_states = shared_data.purchase_states.copy()
    return jsonify(purchase_states)

@app.route('/api/validate-sync')
def api_validate_sync():
    """SYNC VALIDATION: Compare current backend state with what frontend should have"""
    try:
        with shared_data.lock:
            current_stock_data = shared_data.stock_data.copy()
            current_purchase_states = shared_data.purchase_states.copy()
            last_cycle_id = shared_data.last_cycle_id

        # Prepare expected UI state based on backend data
        expected_ui_state = {}

        for tcin, stock_info in current_stock_data.items():
            purchase_state = current_purchase_states.get(tcin, {'status': 'ready'})

            expected_ui_state[tcin] = {
                'stock_status': 'IN_STOCK' if stock_info.get('in_stock') else 'OUT_OF_STOCK',
                'purchase_status': purchase_state.get('status', 'ready'),
                'product_title': stock_info.get('title', f'Product {tcin}'),
                'completes_at': purchase_state.get('completes_at'),
                'backend_stock_data': stock_info,
                'backend_purchase_data': purchase_state
            }

        return jsonify({
            'success': True,
            'last_cycle_id': last_cycle_id,
            'expected_ui_state': expected_ui_state,
            'timestamp': datetime.now().isoformat(),
            'validation_note': 'Compare this expected state with actual frontend UI state'
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/refresh')
def refresh():
    """Manual refresh endpoint - now returns JSON for AJAX"""
    print(f"[PURCHASE_DEBUG] MANUAL REFRESH triggered at timestamp {time.time():.3f}")
    add_activity_log("Manual refresh triggered by user", "info", "user_action")

    # Check if this is an AJAX request
    if request.headers.get('Content-Type') == 'application/json' or request.headers.get('Accept') == 'application/json':
        return jsonify({
            'success': True,
            'message': 'Refresh triggered - data will update via SSE'
        })

    # Fallback for non-AJAX requests
    return redirect(url_for('index'))

@app.route('/clear-logs', methods=['POST'])
def clear_logs():
    """Clear activity logs endpoint"""
    try:
        with shared_data.lock:
            shared_data.activity_log.clear()

        # Clear the log file
        save_activity_log()

        # Add a log entry about the clearing
        add_activity_log("Activity logs cleared by user", "info", "user_action")

        # Broadcast to SSE clients using enhanced SSE
        enhanced_broadcast_sse_event('logs_cleared', {
            'message': 'Activity logs have been cleared',
            'timestamp': datetime.now().isoformat()
        })

        return jsonify({
            'success': True,
            'message': 'Activity logs cleared successfully'
        })

    except Exception as e:
        error_msg = f"Failed to clear logs: {str(e)}"
        add_activity_log(error_msg, "error", "system")
        return jsonify({
            'success': False,
            'error': error_msg
        }), 500

@app.route('/add-product', methods=['POST'])
def add_product():
    """Add a new product to configuration (with error handling)"""
    try:
        # Check if this is an AJAX request
        is_ajax = request.headers.get('Content-Type') == 'application/json'

        if is_ajax:
            data = request.get_json()
            tcin = data.get('tcin', '').strip()
        else:
            tcin = request.form.get('tcin', '').strip()

        if not tcin or not tcin.isdigit() or len(tcin) != 8:
            error_msg = "Invalid TCIN format (must be 8 digits)"
            add_activity_log(f"Failed to add product: {error_msg}", "error", "config")
            return jsonify({'success': False, 'error': error_msg})

        # Load config with error handling
        try:
            stock_monitor = StockMonitor()
            config = stock_monitor.get_config()
        except Exception as e:
            error_msg = f"Failed to load configuration: {str(e)}"
            add_activity_log(error_msg, "error", "config")
            return jsonify({'success': False, 'error': error_msg})

        # Check if exists
        existing_tcins = [p['tcin'] for p in config.get('products', [])]
        if tcin in existing_tcins:
            error_msg = "Product already exists"
            add_activity_log(f"Product {tcin} already exists in configuration", "warning", "config")
            return jsonify({'success': False, 'error': error_msg})

        # Try to fetch product name with error handling
        try:
            temp_stock_data = stock_monitor.check_stock()
            product_name = temp_stock_data.get(tcin, {}).get('title', f'Product {tcin}')
        except Exception as e:
            print(f"[CONFIG] Failed to fetch product name: {e}")
            product_name = f'Product {tcin}'

        # Add to config
        new_product = {
            'tcin': tcin,
            'name': product_name,
            'enabled': True
        }

        config['products'].append(new_product)

        # Save config with error handling
        try:
            config_path = "config/product_config.json"
            temp_config_path = f"{config_path}.tmp.{os.getpid()}"

            with open(temp_config_path, 'w') as f:
                json.dump(config, f, indent=2)

            if os.path.exists(config_path):
                os.remove(config_path)
            os.rename(temp_config_path, config_path)

        except Exception as e:
            error_msg = f"Failed to save configuration: {str(e)}"
            add_activity_log(error_msg, "error", "config")
            return jsonify({'success': False, 'error': error_msg})

        # Update shared_data immediately to prevent "LOADING" state
        with shared_data.lock:
            # Initialize stock data for new TCIN - it will get real data on next API cycle
            if tcin not in shared_data.stock_data:
                shared_data.stock_data[tcin] = {
                    'title': product_name,
                    'in_stock': False,
                    'status_detail': 'WAITING_FOR_REFRESH'
                }
            # Initialize purchase state
            if tcin not in shared_data.purchase_states:
                shared_data.purchase_states[tcin] = {'status': 'ready'}

        add_activity_log(f"Added new product: {product_name} (TCIN: {tcin})", "success", "config")

        # COHESIVE SYSTEM: Auto-add to catalog when adding to active monitoring
        try:
            catalog_config = get_catalog_config()
            existing_catalog_tcins = [p['tcin'] for p in catalog_config.get('catalog', [])]

            if tcin not in existing_catalog_tcins:
                # Add to catalog as well
                new_catalog_item = {
                    'tcin': tcin,
                    'name': product_name,
                    'date_added': datetime.now().isoformat(),
                    'url': f"https://www.target.com/p/-/A-{tcin}"
                }
                catalog_config['catalog'].append(new_catalog_item)

                if save_catalog_config(catalog_config):
                    print(f"[COHESIVE] Auto-added {tcin} to catalog when added to active monitoring")
                else:
                    print(f"[COHESIVE] Warning: Failed to auto-add {tcin} to catalog")
            else:
                print(f"[COHESIVE] {tcin} already exists in catalog")
        except Exception as e:
            print(f"[COHESIVE] Warning: Failed to auto-add to catalog: {e}")
            # Don't fail the main operation if catalog update fails

        return jsonify({
            'success': True,
            'product': {
                'tcin': tcin,
                'name': product_name,
                'url': f"https://www.target.com/p/-/A-{tcin}"
            }
        })

    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        add_activity_log(f"Failed to add product {tcin}: {error_msg}", "error", "config")
        return jsonify({'success': False, 'error': error_msg})

@app.route('/remove-product/<tcin>', methods=['POST'])
def remove_product(tcin):
    """Remove a product from configuration (with error handling)"""
    try:
        is_ajax = request.headers.get('Content-Type') == 'application/json'

        # Load config with error handling
        try:
            stock_monitor = StockMonitor()
            config = stock_monitor.get_config()
        except Exception as e:
            error_msg = f"Failed to load configuration: {str(e)}"
            add_activity_log(error_msg, "error", "config")
            return jsonify({'success': False, 'error': error_msg})

        # Remove product
        original_count = len(config.get('products', []))
        config['products'] = [p for p in config.get('products', []) if p.get('tcin') != tcin]

        if len(config['products']) < original_count:
            # Save config with error handling
            try:
                config_path = "config/product_config.json"
                temp_config_path = f"{config_path}.tmp.{os.getpid()}"

                with open(temp_config_path, 'w') as f:
                    json.dump(config, f, indent=2)

                if os.path.exists(config_path):
                    os.remove(config_path)
                os.rename(temp_config_path, config_path)

            except Exception as e:
                error_msg = f"Failed to save configuration: {str(e)}"
                add_activity_log(error_msg, "error", "config")
                return jsonify({'success': False, 'error': error_msg})

            # Clean up shared data for removed product
            with shared_data.lock:
                if tcin in shared_data.stock_data:
                    del shared_data.stock_data[tcin]
                    print(f"[CONFIG] Removed {tcin} from stock_data")
                if tcin in shared_data.purchase_states:
                    del shared_data.purchase_states[tcin]
                    print(f"[CONFIG] Removed {tcin} from purchase_states")

            add_activity_log(f"Removed product with TCIN: {tcin}", "success", "config")
            return jsonify({'success': True})
        else:
            error_msg = "Product not found"
            add_activity_log(f"Product {tcin} not found in configuration", "warning", "config")
            return jsonify({'success': False, 'error': error_msg})

    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        add_activity_log(f"Failed to remove product {tcin}: {error_msg}", "error", "config")
        return jsonify({'success': False, 'error': error_msg})

# ========== PRODUCT CATALOG API ENDPOINTS ==========

def get_catalog_config():
    """Load catalog configuration"""
    catalog_file = "config/product_catalog.json"
    try:
        if os.path.exists(catalog_file):
            with open(catalog_file, 'r') as f:
                return json.load(f)
        else:
            return {"catalog": []}
    except Exception as e:
        print(f"[CATALOG] Failed to load catalog: {e}")
        return {"catalog": []}

def save_catalog_config(catalog_data):
    """Save catalog configuration"""
    catalog_file = "config/product_catalog.json"
    try:
        # Ensure config directory exists
        os.makedirs('config', exist_ok=True)

        # Atomic write
        temp_file = f"{catalog_file}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(catalog_data, f, indent=2)

        if os.path.exists(catalog_file):
            os.remove(catalog_file)
        os.rename(temp_file, catalog_file)
        return True
    except Exception as e:
        print(f"[CATALOG] Failed to save catalog: {e}")
        return False

@app.route('/catalog/add', methods=['POST'])
def add_to_catalog():
    """Add TCIN to catalog without monitoring"""
    try:
        data = request.get_json()
        tcin = data.get('tcin', '').strip()

        # Validate TCIN
        if not tcin or len(tcin) != 8 or not tcin.isdigit():
            return jsonify({'success': False, 'error': 'Invalid TCIN format'})

        # Load catalog
        catalog_config = get_catalog_config()

        # Check if already in catalog
        existing_catalog_tcins = [p['tcin'] for p in catalog_config.get('catalog', [])]
        if tcin in existing_catalog_tcins:
            return jsonify({'success': False, 'error': 'Product already in catalog'})

        # Try to fetch product name (only if not already in catalog with a real name)
        try:
            stock_monitor = StockMonitor()
            temp_stock_data = stock_monitor.check_stock()
            fetched_name = temp_stock_data.get(tcin, {}).get('title')

            if fetched_name and not fetched_name.startswith('Product '):
                # Got real product name from API
                product_name = fetched_name
                print(f"[CATALOG] Fetched real product name: {product_name}")
            else:
                # API didn't return a real name, use placeholder
                product_name = f'Product {tcin}'
                print(f"[CATALOG] Using placeholder name: {product_name}")
        except Exception as e:
            print(f"[CATALOG] Failed to fetch product name: {e}")
            product_name = f'Product {tcin}'

        # Add to catalog
        new_catalog_item = {
            'tcin': tcin,
            'name': product_name,
            'date_added': datetime.now().isoformat(),
            'url': f"https://www.target.com/p/-/A-{tcin}"
        }

        catalog_config['catalog'].append(new_catalog_item)

        # Save catalog
        if save_catalog_config(catalog_config):
            add_activity_log(f"Added {product_name} to catalog (TCIN: {tcin})", "success", "catalog")
            return jsonify({
                'success': True,
                'product': new_catalog_item
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save catalog'})

    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        add_activity_log(f"Failed to add {tcin} to catalog: {error_msg}", "error", "catalog")
        return jsonify({'success': False, 'error': error_msg})

@app.route('/catalog/remove/<tcin>', methods=['POST'])
def remove_from_catalog(tcin):
    """Remove TCIN from catalog"""
    try:
        # Load catalog
        catalog_config = get_catalog_config()

        # Find and remove product
        original_length = len(catalog_config.get('catalog', []))
        catalog_config['catalog'] = [p for p in catalog_config.get('catalog', []) if p['tcin'] != tcin]

        if len(catalog_config['catalog']) < original_length:
            # Save updated catalog
            if save_catalog_config(catalog_config):
                add_activity_log(f"Removed product with TCIN: {tcin} from catalog", "success", "catalog")
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Failed to save catalog'})
        else:
            return jsonify({'success': False, 'error': 'Product not found in catalog'})

    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        add_activity_log(f"Failed to remove {tcin} from catalog: {error_msg}", "error", "catalog")
        return jsonify({'success': False, 'error': error_msg})

@app.route('/catalog/activate/<tcin>', methods=['POST'])
def activate_from_catalog(tcin):
    """Move TCIN from catalog to active monitoring"""
    try:
        # Load catalog to get product info
        catalog_config = get_catalog_config()
        catalog_product = None
        for product in catalog_config.get('catalog', []):
            if product['tcin'] == tcin:
                catalog_product = product
                break

        if not catalog_product:
            return jsonify({'success': False, 'error': 'Product not found in catalog'})

        # Check if already in active monitoring
        config = json.load(open('config/product_config.json', 'r'))
        existing_tcins = [p['tcin'] for p in config.get('products', [])]
        if tcin in existing_tcins:
            return jsonify({'success': False, 'error': 'Product already in active monitoring'})

        # Add to active monitoring (reuse existing logic)
        new_product = {
            'tcin': tcin,
            'name': catalog_product['name'],
            'enabled': True,
            'url': f"https://www.target.com/p/-/A-{tcin}"
        }

        config['products'].append(new_product)

        # Save active config
        with open('config/product_config.json', 'w') as f:
            json.dump(config, f, indent=2)

        # Update shared_data immediately
        with shared_data.lock:
            if tcin not in shared_data.stock_data:
                shared_data.stock_data[tcin] = {
                    'title': catalog_product['name'],
                    'in_stock': False,
                    'status_detail': 'WAITING_FOR_REFRESH'
                }
            if tcin not in shared_data.purchase_states:
                shared_data.purchase_states[tcin] = {'status': 'ready'}

        add_activity_log(f"Activated {catalog_product['name']} from catalog to monitoring", "success", "config")
        return jsonify({
            'success': True,
            'product': new_product
        })

    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        add_activity_log(f"Failed to activate {tcin} from catalog: {error_msg}", "error", "catalog")
        return jsonify({'success': False, 'error': error_msg})

def update_catalog_names_from_stock_data(stock_data):
    """Update catalog product names when real names become available"""
    try:
        catalog_config = get_catalog_config()
        updated = False

        for catalog_item in catalog_config.get('catalog', []):
            tcin = catalog_item['tcin']
            current_name = catalog_item.get('name', '')

            # Only update if current name is placeholder and we have real data
            if (current_name.startswith('Product ') and
                tcin in stock_data and
                'title' in stock_data[tcin]):

                real_name = stock_data[tcin]['title']
                if real_name and not real_name.startswith('Product '):
                    catalog_item['name'] = real_name
                    updated = True
                    print(f"[CATALOG] Updated name for {tcin}: {current_name} -> {real_name}")

        if updated:
            save_catalog_config(catalog_config)
            print(f"[CATALOG] Saved updated catalog with real product names")

    except Exception as e:
        print(f"[CATALOG] Failed to update catalog names: {e}")

@app.route('/catalog/list', methods=['GET'])
def get_catalog():
    """Get all catalog items with their status"""
    try:
        catalog_config = get_catalog_config()

        # Load active products to determine status
        active_config = json.load(open('config/product_config.json', 'r'))
        active_tcins = [p['tcin'] for p in active_config.get('products', [])]

        # Add status to catalog items
        for item in catalog_config.get('catalog', []):
            item['is_active'] = item['tcin'] in active_tcins

        return jsonify(catalog_config)

    except Exception as e:
        print(f"[CATALOG] Failed to get catalog: {e}")
        return jsonify({"catalog": []})

# ========== COMPREHENSIVE TEST MODE API ENDPOINTS ==========

@app.route('/api/test/enable', methods=['POST'])
def enable_test_mode():
    """Enable test mode with specified scenario"""
    try:
        data = request.get_json() or {}
        scenario = data.get('scenario', 'alternating')

        # Access the global stock monitor instance (create new for test mode)
        from stock_monitor import StockMonitor
        test_monitor = StockMonitor()
        test_monitor.enable_test_mode(scenario)

        # Update the global stock monitor thread to use test mode
        # Find the StockMonitorThread and update its monitor
        for thread_name, thread_obj in threading.enumerate():
            if hasattr(thread_obj, 'target') and 'StockMonitorThread' in str(thread_obj.target):
                # This approach won't work, we need to store reference to the monitor
                pass

        # Store test monitor reference in shared_data for thread communication
        with shared_data.lock:
            shared_data.test_monitor = test_monitor
            shared_data.test_monitor.enable_test_mode(scenario)

        # Enhanced logging with SSE broadcast
        test_status = test_monitor.get_test_status()
        enhanced_broadcast_sse_event('test_mode_enabled', {
            'scenario': scenario,
            'status': test_status,
            'message': f'Test mode enabled with scenario: {scenario}'
        })

        add_activity_log(f"Test mode enabled with scenario: {scenario}", "info", "test_mode")

        return jsonify({
            'success': True,
            'scenario': scenario,
            'available_scenarios': list(test_monitor.test_scenarios.keys()),
            'message': f'Test mode enabled with scenario: {scenario}'
        })

    except Exception as e:
        error_msg = f"Failed to enable test mode: {str(e)}"
        add_activity_log(error_msg, "error", "test_mode")
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/test/disable', methods=['POST'])
def disable_test_mode():
    """Disable test mode and return to normal operation"""
    try:
        with shared_data.lock:
            if hasattr(shared_data, 'test_monitor'):
                shared_data.test_monitor.disable_test_mode()
                delattr(shared_data, 'test_monitor')

        add_activity_log("Test mode disabled - returning to normal API calls", "info", "test_mode")

        # Enhanced SSE broadcast for test mode disable
        enhanced_broadcast_sse_event('test_mode_disabled', {
            'message': 'Test mode disabled, returning to normal operation'
        })

        return jsonify({
            'success': True,
            'message': 'Test mode disabled, returning to normal operation'
        })

    except Exception as e:
        error_msg = f"Failed to disable test mode: {str(e)}"
        add_activity_log(error_msg, "error", "test_mode")
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/test/status')
def get_test_status():
    """Get current test mode status"""
    try:
        with shared_data.lock:
            if hasattr(shared_data, 'test_monitor'):
                status = shared_data.test_monitor.get_test_status()
            else:
                status = {'test_mode': False}

        return jsonify({
            'success': True,
            'status': status
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'status': {'test_mode': False}
        }), 500

@app.route('/api/test/override', methods=['POST'])
def set_test_override():
    """Manually override stock data for specific TCINs"""
    try:
        data = request.get_json() or {}
        tcin_data = data.get('tcin_data', {})

        with shared_data.lock:
            if hasattr(shared_data, 'test_monitor'):
                shared_data.test_monitor.set_test_data_override(tcin_data)
                message = f"Test override set for TCINs: {list(tcin_data.keys())}"
                add_activity_log(message, "info", "test_mode")

                return jsonify({
                    'success': True,
                    'message': message,
                    'overrides': list(tcin_data.keys())
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Test mode not enabled'
                }), 400

    except Exception as e:
        error_msg = f"Failed to set test override: {str(e)}"
        add_activity_log(error_msg, "error", "test_mode")
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/test/scenarios')
def list_test_scenarios():
    """List all available test scenarios with descriptions"""
    scenarios = {
        'alternating': 'Alternates all products between in stock and out of stock every cycle',
        'rapid_changes': 'Rapid stock changes with different patterns per product to test race conditions',
        'purchase_timing': 'One product in stock per cycle to test individual purchase attempt timing',
        'edge_cases': 'Tests edge cases: all out, all in, marketplace sellers, API errors',
        'sync_stress': 'Maximum stress test with aggressive patterns to test UI synchronization'
    }

    return jsonify({
        'success': True,
        'scenarios': scenarios,
        'count': len(scenarios)
    })

# ========== ENHANCED SSE EVENT TRACKING & VALIDATION ==========

# Global event tracking for debugging
sse_event_tracker = {
    'events_sent': 0,
    'events_by_type': {},
    'last_events': [],
    'client_count': 0
}

def enhanced_broadcast_sse_event(event_type, data, event_id=None):
    """Enhanced SSE broadcast with comprehensive logging and tracking"""
    try:
        # Generate unique event ID if not provided
        if event_id is None:
            event_id = f"{event_type}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

        event_data = {
            'type': event_type,
            'data': data,
            'timestamp': datetime.now().isoformat(),
            'event_id': event_id,
            'server_time': time.time()
        }

        # Enhanced logging
        print(f"[SSE_ENHANCED] Broadcasting event {event_id}: {event_type}")
        print(f"[SSE_ENHANCED] Client count: {len(shared_data.connected_clients)}")
        print(f"[SSE_ENHANCED] Event data preview: {str(data)[:200]}...")

        # Track event statistics
        sse_event_tracker['events_sent'] += 1
        sse_event_tracker['events_by_type'][event_type] = sse_event_tracker['events_by_type'].get(event_type, 0) + 1
        sse_event_tracker['last_events'].append({
            'event_id': event_id,
            'type': event_type,
            'timestamp': event_data['timestamp'],
            'client_count': len(shared_data.connected_clients)
        })

        # Keep only last 50 events in memory
        if len(sse_event_tracker['last_events']) > 50:
            sse_event_tracker['last_events'] = sse_event_tracker['last_events'][-50:]

        # Add to queue
        sse_queue.put(event_data)

        # Log successful broadcast (but don't create recursive loops)
        if event_type != 'activity_log':  # Prevent recursive logging for activity log events
            add_activity_log(f"SSE event {event_id} ({event_type}) broadcast to {len(shared_data.connected_clients)} clients", "info", "sse")

        return event_id

    except Exception as e:
        print(f"[SSE_ENHANCED] Failed to broadcast event: {e}")
        add_activity_log(f"SSE broadcast failed: {str(e)}", "error", "sse")
        return None

@app.route('/api/debug/sse-stats')
def get_sse_stats():
    """Get SSE event statistics for debugging"""
    try:
        return jsonify({
            'success': True,
            'stats': {
                'total_events_sent': sse_event_tracker['events_sent'],
                'events_by_type': sse_event_tracker['events_by_type'],
                'connected_clients': len(shared_data.connected_clients),
                'last_events': sse_event_tracker['last_events'][-10:],  # Last 10 events
                'queue_size': sse_queue.qsize() if hasattr(sse_queue, 'qsize') else 'unknown'
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== PRODUCTION-GRADE HEALTH MONITORING ==========

@app.route('/health')
def health_check():
    """Production health check endpoint for load balancers"""
    try:
        with shared_data.lock:
            stock_data_age = 0
            if shared_data.last_update_time:
                stock_data_age = (datetime.now() - shared_data.last_update_time).total_seconds()

            health_status = {
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'uptime_seconds': time.time() - shared_data.server_startup_time,
                'monitoring_active': shared_data.monitor_running,
                'stock_data_age_seconds': stock_data_age,
                'circuit_breaker_open': shared_data.api_circuit_open,
                'api_failure_count': shared_data.api_failure_count,
                'connected_clients': len(shared_data.connected_clients)
            }

            # Determine overall health
            if shared_data.api_circuit_open:
                health_status['status'] = 'degraded'
                health_status['message'] = 'API circuit breaker is open'
            elif stock_data_age > 120:  # No data for 2 minutes
                health_status['status'] = 'unhealthy'
                health_status['message'] = f'Stock data is {stock_data_age:.0f}s old'
            elif not shared_data.monitor_running:
                health_status['status'] = 'unhealthy'
                health_status['message'] = 'Monitoring not active'

        # Return appropriate HTTP status
        if health_status['status'] == 'healthy':
            return jsonify(health_status), 200
        elif health_status['status'] == 'degraded':
            return jsonify(health_status), 200
        else:
            return jsonify(health_status), 503

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Health check failed: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/metrics')
def metrics_endpoint():
    """Production metrics endpoint for monitoring systems"""
    try:
        with shared_data.lock:
            uptime = time.time() - shared_data.server_startup_time
            stock_data_age = 0
            if shared_data.last_update_time:
                stock_data_age = (datetime.now() - shared_data.last_update_time).total_seconds()

            metrics = {
                'system': {
                    'uptime_seconds': uptime,
                    'uptime_hours': uptime / 3600,
                    'server_startup_time': shared_data.server_startup_time,
                    'monitoring_active': shared_data.monitor_running
                },
                'cache': {
                    'stock_data_age_seconds': stock_data_age,
                    'cache_hit_count': shared_data.cache_hit_count,
                    'cache_miss_count': shared_data.cache_miss_count,
                    'cache_hit_ratio': shared_data.cache_hit_count / max(1, shared_data.cache_hit_count + shared_data.cache_miss_count),
                    'stock_data_checksum': shared_data.stock_data_checksum
                },
                'api': {
                    'failure_count': shared_data.api_failure_count,
                    'circuit_breaker_open': shared_data.api_circuit_open,
                    'last_failure_time': shared_data.last_api_failure_time
                },
                'connections': {
                    'connected_clients': len(shared_data.connected_clients),
                    'sse_events_sent': sse_event_tracker.get('events_sent', 0),
                    'sse_events_by_type': sse_event_tracker.get('events_by_type', {})
                },
                'data': {
                    'products_monitored': len(shared_data.stock_data),
                    'in_stock_count': sum(1 for data in shared_data.stock_data.values() if data.get('in_stock')),
                    'activity_log_entries': len(shared_data.activity_log),
                    'purchase_states_count': len(shared_data.purchase_states)
                }
            }

        return jsonify(metrics), 200

    except Exception as e:
        return jsonify({
            'error': f'Metrics collection failed: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/debug/force-sync')
def force_sync():
    """Force an immediate sync validation check"""
    try:
        # Trigger immediate health check
        with shared_data.lock:
            current_stock_data = shared_data.stock_data.copy()
            current_purchase_states = shared_data.purchase_states.copy()

        # Send sync validation event
        sync_event_id = enhanced_broadcast_sse_event('force_sync', {
            'trigger': 'manual_force_sync',
            'stock_count': len(current_stock_data),
            'purchase_count': len(current_purchase_states),
            'timestamp': datetime.now().isoformat()
        })

        add_activity_log("Manual sync validation triggered", "info", "debug")

        return jsonify({
            'success': True,
            'message': 'Sync validation triggered',
            'event_id': sync_event_id
        })

    except Exception as e:
        error_msg = f"Failed to force sync: {str(e)}"
        add_activity_log(error_msg, "error", "debug")
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/debug/purchase-states')
def debug_purchase_states():
    """Debug endpoint to inspect purchase states for stuck statuses"""
    try:
        # Get detailed purchase states from the purchase manager
        if 'purchase_thread' in locals():
            purchase_manager = purchase_thread.purchase_manager
        else:
            # Fallback - create a temporary purchase manager to read states
            from bulletproof_purchase_manager import BulletproofPurchaseManager
            purchase_manager = BulletproofPurchaseManager()

        all_states = purchase_manager.get_all_states()
        current_time = time.time()

        # Analyze each state for potential issues
        state_analysis = {}
        issues_found = []

        for tcin, state in all_states.items():
            status = state.get('status', 'unknown')
            completed_at = state.get('completed_at', 0)
            completes_at = state.get('completes_at', 0)

            analysis = {
                'tcin': tcin,
                'status': status,
                'completed_at': completed_at,
                'completes_at': completes_at,
                'order_number': state.get('order_number'),
                'time_since_completion': current_time - completed_at if completed_at else None,
                'time_until_completion': completes_at - current_time if completes_at else None
            }

            # Check for issues
            if status in ['purchased', 'failed']:
                time_since = analysis['time_since_completion']
                if time_since and time_since > 30:  # Completed more than 30 seconds ago
                    issues_found.append({
                        'tcin': tcin,
                        'issue': 'stuck_completed_state',
                        'status': status,
                        'time_since_completion': time_since,
                        'severity': 'high' if time_since > 120 else 'medium'
                    })

            elif status == 'attempting':
                time_until = analysis['time_until_completion']
                if time_until and time_until < -30:  # Should have completed more than 30 seconds ago
                    issues_found.append({
                        'tcin': tcin,
                        'issue': 'overdue_purchase',
                        'status': status,
                        'overdue_by': abs(time_until),
                        'severity': 'high'
                    })

            state_analysis[tcin] = analysis

        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'summary': {
                'total_products': len(all_states),
                'ready': len([s for s in all_states.values() if s.get('status') == 'ready']),
                'attempting': len([s for s in all_states.values() if s.get('status') == 'attempting']),
                'purchased': len([s for s in all_states.values() if s.get('status') == 'purchased']),
                'failed': len([s for s in all_states.values() if s.get('status') == 'failed']),
                'issues_found': len(issues_found)
            },
            'issues': issues_found,
            'detailed_states': state_analysis
        })

    except Exception as e:
        error_msg = f"Failed to debug purchase states: {str(e)}"
        return jsonify({'success': False, 'error': error_msg}), 500

@app.route('/api/debug/force-reset-purchases', methods=['POST'])
def force_reset_purchases():
    """Force reset all completed purchases to ready state"""
    try:
        data = request.get_json() or {}
        tcins_to_reset = data.get('tcins', [])  # If empty, reset all completed

        # Get purchase manager instance
        if 'purchase_thread' in locals():
            purchase_manager = purchase_thread.purchase_manager
        else:
            # Fallback - create a temporary purchase manager
            from bulletproof_purchase_manager import BulletproofPurchaseManager
            purchase_manager = BulletproofPurchaseManager()

        # Get states before reset
        states_before = purchase_manager.get_all_states()

        if tcins_to_reset:
            # Reset specific TCINs
            reset_count = 0
            with purchase_manager._state_lock:
                states = purchase_manager._load_states_unsafe()
                for tcin in tcins_to_reset:
                    if tcin in states and states[tcin].get('status') in ['purchased', 'failed']:
                        old_status = states[tcin].get('status')
                        states[tcin] = {'status': 'ready'}
                        reset_count += 1
                        print(f"[FORCE_RESET] {tcin}: {old_status} -> ready")

                if reset_count > 0:
                    purchase_manager._save_states_unsafe(states)

            add_activity_log(f"Force reset {reset_count} specific purchases: {tcins_to_reset}", "warning", "debug")
        else:
            # Reset all completed purchases
            reset_count = purchase_manager.reset_completed_purchases_to_ready()
            add_activity_log(f"Force reset all {reset_count} completed purchases", "warning", "debug")

        # Get states after reset
        states_after = purchase_manager.get_all_states()

        return jsonify({
            'success': True,
            'message': f'Force reset {reset_count} purchases',
            'reset_count': reset_count,
            'states_before': states_before,
            'states_after': states_after,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        error_msg = f"Failed to force reset purchases: {str(e)}"
        add_activity_log(error_msg, "error", "debug")
        return jsonify({'success': False, 'error': error_msg}), 500

# ========== ENHANCED MONITORING API ENDPOINTS ==========

@app.route('/api/monitoring/health-report')
def api_health_report():
    """Get comprehensive system health report with enhanced monitoring"""
    try:
        health_report = monitoring_system.get_comprehensive_health_report()
        return jsonify({
            'success': True,
            'health_report': health_report,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/monitoring/alerts')
def api_monitoring_alerts():
    """Get current system alerts and issues"""
    try:
        alerts_summary = monitoring_system.get_alerts_summary()
        return jsonify({
            'success': True,
            'alerts': alerts_summary,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/monitoring/stuck-states')
def api_stuck_states():
    """Get information about stuck purchase states"""
    try:
        stuck_summary = monitoring_system.purchase_monitor.get_stuck_state_summary()

        # Get current stuck states
        current_states = {}
        with shared_data.lock:
            current_states = shared_data.purchase_states.copy()

        current_time = time.time()
        current_stuck = []

        for tcin, state in current_states.items():
            status = state.get('status', 'unknown')
            if status in ['purchased', 'failed']:
                completed_at = state.get('completed_at', 0)
                if completed_at and (current_time - completed_at) > 30:
                    current_stuck.append({
                        'tcin': tcin,
                        'status': status,
                        'stuck_duration': current_time - completed_at,
                        'completed_at': completed_at,
                        'order_number': state.get('order_number')
                    })

        return jsonify({
            'success': True,
            'stuck_states_summary': stuck_summary,
            'current_stuck_states': current_stuck,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/monitoring/performance-summary')
def api_performance_summary():
    """Get system performance summary"""
    try:
        perf_summary = monitoring_system.performance_monitor.get_performance_summary(minutes=10)
        cycle_summary = monitoring_system.cycle_monitor.get_cycle_performance_summary(cycles=20)

        return jsonify({
            'success': True,
            'performance_summary': perf_summary,
            'cycle_summary': cycle_summary,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/monitoring/state-transitions')
def api_state_transitions():
    """Get purchase state transition analysis"""
    try:
        transitions = monitoring_system.purchase_monitor.get_state_transition_analysis()

        return jsonify({
            'success': True,
            'state_transitions': transitions,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/validation/status')
def api_validation_status():
    """Get real-time validation system status"""
    try:
        global realtime_validator
        if realtime_validator:
            validation_summary = realtime_validator.get_validation_summary()
            return jsonify({
                'success': True,
                'validation_summary': validation_summary,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Real-time validation not initialized',
                'timestamp': datetime.now().isoformat()
            }), 503

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/validation/incidents')
def api_validation_incidents():
    """Get recent validation incidents"""
    try:
        global realtime_validator
        if realtime_validator:
            minutes = request.args.get('minutes', 10, type=int)
            incidents = realtime_validator.get_recent_incidents(minutes)

            return jsonify({
                'success': True,
                'incidents': incidents,
                'period_minutes': minutes,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Real-time validation not initialized',
                'timestamp': datetime.now().isoformat()
            }), 503

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/validation/configure', methods=['POST'])
def api_configure_validation():
    """Configure real-time validation parameters"""
    try:
        global realtime_validator
        if not realtime_validator:
            return jsonify({
                'success': False,
                'error': 'Real-time validation not initialized'
            }), 503

        data = request.get_json() or {}

        # Extract configuration parameters
        config_params = {}
        if 'stuck_threshold_seconds' in data:
            config_params['stuck_threshold_seconds'] = int(data['stuck_threshold_seconds'])
        if 'check_interval' in data:
            config_params['check_interval'] = int(data['check_interval'])
        if 'auto_recovery_enabled' in data:
            config_params['auto_recovery_enabled'] = bool(data['auto_recovery_enabled'])
        if 'max_recovery_attempts' in data:
            config_params['max_recovery_attempts'] = int(data['max_recovery_attempts'])

        # Apply configuration
        if config_params:
            realtime_validator.configure_validation(**config_params)
            add_activity_log(f"Validation configuration updated: {config_params}", "info", "validation")

            return jsonify({
                'success': True,
                'message': 'Validation configuration updated',
                'updated_params': config_params,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'No valid configuration parameters provided'
            }), 400

    except Exception as e:
        error_msg = f"Failed to configure validation: {str(e)}"
        add_activity_log(error_msg, "error", "validation")
        return jsonify({
            'success': False,
            'error': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/validation/force-check', methods=['POST'])
def api_force_validation_check():
    """Force an immediate validation check"""
    try:
        global realtime_validator
        if not realtime_validator:
            return jsonify({
                'success': False,
                'error': 'Real-time validation not initialized'
            }), 503

        # Perform immediate validation check
        current_time = time.time()
        validation_result = realtime_validator._perform_validation_check(current_time)

        add_activity_log("Manual validation check triggered", "info", "validation")

        return jsonify({
            'success': True,
            'validation_result': validation_result,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        error_msg = f"Failed to perform validation check: {str(e)}"
        add_activity_log(error_msg, "error", "validation")
        return jsonify({
            'success': False,
            'error': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 500

# ========== TEST MODE API ENDPOINTS ==========

@app.route('/api/test-mode/enable', methods=['POST'])
def api_enable_test_mode():
    """Enable test mode with simulated stock data"""
    try:
        data = request.get_json() or {}
        scenario = data.get('scenario', 'alternating')

        # Enable test mode on the stock monitor
        if hasattr(shared_data, 'test_monitor') and shared_data.test_monitor:
            shared_data.test_monitor.enable_test_mode(scenario)
            shared_data.test_mode_enabled = True
            add_activity_log(f"Test mode enabled with scenario: {scenario}", "info", "test_mode")

            return jsonify({
                'success': True,
                'message': f'Test mode enabled with scenario: {scenario}',
                'scenario': scenario,
                'available_scenarios': ['alternating', 'rapid_changes', 'purchase_timing', 'edge_cases', 'sync_stress'],
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Test monitor not available',
                'timestamp': datetime.now().isoformat()
            }), 503

    except Exception as e:
        error_msg = f"Failed to enable test mode: {str(e)}"
        add_activity_log(error_msg, "error", "test_mode")
        return jsonify({
            'success': False,
            'error': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/test-mode/disable', methods=['POST'])
def api_disable_test_mode():
    """Disable test mode and return to live API data"""
    try:
        # Disable test mode
        if hasattr(shared_data, 'test_monitor') and shared_data.test_monitor:
            shared_data.test_monitor.disable_test_mode()
            shared_data.test_mode_enabled = False
            add_activity_log("Test mode disabled - returning to live API data", "info", "test_mode")

            return jsonify({
                'success': True,
                'message': 'Test mode disabled - now using live API data',
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Test monitor not available',
                'timestamp': datetime.now().isoformat()
            }), 503

    except Exception as e:
        error_msg = f"Failed to disable test mode: {str(e)}"
        add_activity_log(error_msg, "error", "test_mode")
        return jsonify({
            'success': False,
            'error': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/test-mode/status')
def api_test_mode_status():
    """Get current test mode status"""
    try:
        if hasattr(shared_data, 'test_monitor') and shared_data.test_monitor:
            test_status = shared_data.test_monitor.get_test_status()

            return jsonify({
                'success': True,
                'test_status': test_status,
                'test_mode_enabled': getattr(shared_data, 'test_mode_enabled', False),
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Test monitor not available',
                'test_mode_enabled': False,
                'timestamp': datetime.now().isoformat()
            })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'test_mode_enabled': False,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/test-mode/scenario', methods=['POST'])
def api_change_test_scenario():
    """Change the current test scenario"""
    try:
        data = request.get_json() or {}
        scenario = data.get('scenario', 'alternating')

        if hasattr(shared_data, 'test_monitor') and shared_data.test_monitor:
            if shared_data.test_monitor.test_mode:
                # Change scenario
                shared_data.test_monitor.enable_test_mode(scenario)
                add_activity_log(f"Test scenario changed to: {scenario}", "info", "test_mode")

                return jsonify({
                    'success': True,
                    'message': f'Test scenario changed to: {scenario}',
                    'scenario': scenario,
                    'timestamp': datetime.now().isoformat()
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Test mode is not currently enabled',
                    'timestamp': datetime.now().isoformat()
                }), 400
        else:
            return jsonify({
                'success': False,
                'error': 'Test monitor not available',
                'timestamp': datetime.now().isoformat()
            }), 503

    except Exception as e:
        error_msg = f"Failed to change test scenario: {str(e)}"
        add_activity_log(error_msg, "error", "test_mode")
        return jsonify({
            'success': False,
            'error': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/test-mode/manual-stock', methods=['POST'])
def api_set_manual_stock_data():
    """Manually set stock data for specific products in test mode"""
    try:
        data = request.get_json() or {}
        stock_overrides = data.get('stock_data', {})

        if hasattr(shared_data, 'test_monitor') and shared_data.test_monitor:
            if shared_data.test_monitor.test_mode:
                # Set manual stock data
                shared_data.test_monitor.set_test_data_override(stock_overrides)
                add_activity_log(f"Manual stock data set for {len(stock_overrides)} products", "info", "test_mode")

                return jsonify({
                    'success': True,
                    'message': f'Manual stock data set for {len(stock_overrides)} products',
                    'stock_overrides': stock_overrides,
                    'timestamp': datetime.now().isoformat()
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Test mode is not currently enabled',
                    'timestamp': datetime.now().isoformat()
                }), 400
        else:
            return jsonify({
                'success': False,
                'error': 'Test monitor not available',
                'timestamp': datetime.now().isoformat()
            }), 503

    except Exception as e:
        error_msg = f"Failed to set manual stock data: {str(e)}"
        add_activity_log(error_msg, "error", "test_mode")
        return jsonify({
            'success': False,
            'error': error_msg,
            'timestamp': datetime.now().isoformat()
        }), 500

if __name__ == '__main__':
    print("=" * 60)
    print("BULLETPROOF MONITORING DASHBOARD")
    print("=" * 60)
    print("[FEATURES] Real-time updates, infinite purchase loops")
    print("[SAFETY] Thread-safe, atomic operations, bulletproof error handling")
    print("[REALTIME] Server-Sent Events for immediate UI updates")
    print("[ARCH] stock_monitor.py + bulletproof_purchase_manager.py")
    print("=" * 60)

    # Initialize
    load_activity_log()
    add_activity_log("Bulletproof monitoring dashboard initialized", "info", "system")

    # Start background monitoring
    start_monitoring()

    # Run Flask app
    app.run(host='127.0.0.1', port=5001, debug=False, threaded=True)