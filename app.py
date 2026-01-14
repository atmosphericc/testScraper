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
import asyncio
import concurrent.futures
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
import logging
import os
import pickle
import atexit
import signal
import sys
from waitress import serve

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

        # Background initialization state
        self.initialization_complete = False
        self.initialization_status = "Starting..."
        self.initialization_error = None

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
                # Server startup - fresh timer (10s for predictable first cycle with full sync)
                self.timer_start_time = now
                self.is_server_startup = False
                new_duration = 10  # Fixed 10s for consistent startup (browser warmup + sync time)
                self.timer_duration = new_duration
                log_message = f"Server startup: starting 10s initial timer (ensures full sync)"
                result = new_duration

        # Log outside the lock to prevent deadlock
        if log_message:
            add_activity_log(log_message, log_level, "timer")

        return result

    def get_timer_status(self):
        """Get current timer status with countdown"""
        with self.lock:
            if not self.timer_start_time:
                # FIX #1: Stabilize fallback timer state (CRITICAL)
                # Return active: False during initialization to prevent dashboard from syncing
                # to a regenerating timestamp. Dashboard will continue polling until real timer starts.
                if self.monitor_running:
                    return {
                        'active': False,  # Changed from True - prevents false sync
                        'remaining': 20,  # Default cycle duration
                        'total': 20,
                        'elapsed': 0,
                        'status': 'initializing',
                        'message': 'Monitoring thread starting, waiting for first cycle...'
                    }
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

# Global purchase manager and event loop (shared across app and monitoring)
# This ensures only ONE browser instance runs throughout the application
global_purchase_manager = None
global_event_loop = None
global_event_loop_thread = None

# Core monitoring using purchase manager only
realtime_validator = None

# CRITICAL FIX: Per-client SSE queues for proper multi-client broadcasting
# Single queue causes events to be distributed round-robin (each client gets 1/N of events)
# Dict of client_id -> queue ensures ALL clients receive ALL events
sse_client_queues = {}
sse_queue_lock = threading.Lock()

# Activity log persistence
ACTIVITY_LOG_FILE = 'logs/activity_log.pkl'

# ========== ACTIVITY LOG BACKGROUND PERSISTENCE ==========

class ActivityLogPersistenceWorker:
    """
    Background thread for activity log file persistence.
    Decouples file I/O from critical SSE broadcasting path.
    """
    def __init__(self, debounce_ms=100):
        self.save_queue = queue.Queue(maxsize=1000)  # Prevent runaway memory
        self.running = False
        self.thread = None
        self.debounce_seconds = debounce_ms / 1000.0
        self.last_save_time = 0
        self.save_count = 0
        self.error_count = 0

    def start(self):
        """Start the background persistence thread"""
        if self.running:
            print("[PERSISTENCE] Worker already running")
            return

        self.running = True
        self.thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="ActivityLogPersistenceWorker"
        )
        self.thread.start()
        print("[PERSISTENCE] Background persistence worker started")

    def stop(self, timeout=5):
        """Stop the worker and wait for pending saves"""
        if not self.running:
            return

        self.running = False

        # Signal shutdown
        try:
            self.save_queue.put_nowait("SHUTDOWN")
        except queue.Full:
            print("[PERSISTENCE] Warning: Queue full during shutdown")

        # Wait for thread to finish
        if self.thread:
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                print(f"[PERSISTENCE] Warning: Worker did not shut down cleanly in {timeout}s")
            else:
                print(f"[PERSISTENCE] Worker stopped cleanly ({self.save_count} saves, {self.error_count} errors)")

    def queue_save(self):
        """Queue a save request (non-blocking)"""
        try:
            self.save_queue.put_nowait("SAVE")
            return True
        except queue.Full:
            print("[PERSISTENCE] Warning: Save queue full, skipping save")
            self.error_count += 1
            return False

    def _worker_loop(self):
        """Main worker loop - runs in background thread"""
        print("[PERSISTENCE] Worker loop started")
        pending_save = False

        while self.running:
            try:
                # Wait for save request with timeout (for debouncing)
                try:
                    msg = self.save_queue.get(timeout=self.debounce_seconds)

                    if msg == "SHUTDOWN":
                        print("[PERSISTENCE] Shutdown signal received")
                        break
                    elif msg == "SAVE":
                        pending_save = True
                        # Don't save immediately - wait for debounce window
                        continue

                except queue.Empty:
                    # Timeout - if we have pending save, execute it now
                    if pending_save:
                        self._execute_save()
                        pending_save = False

            except Exception as e:
                print(f"[PERSISTENCE] Worker loop error: {e}")
                self.error_count += 1
                time.sleep(0.1)  # Prevent tight loop on persistent errors

        # Process final pending save on shutdown
        if pending_save:
            print("[PERSISTENCE] Processing final pending save on shutdown")
            self._execute_save()

        print("[PERSISTENCE] Worker loop exited")

    def _execute_save(self):
        """Execute the actual file save with retry logic"""
        max_retries = 3
        retry_delays = [0.1, 0.5, 2.0]  # Exponential backoff

        for attempt in range(max_retries):
            try:
                save_activity_log()
                self.save_count += 1
                self.last_save_time = time.time()

                # Log periodic stats (every 100 saves)
                if self.save_count % 100 == 0:
                    print(f"[PERSISTENCE] Stats: {self.save_count} saves, {self.error_count} errors")

                return  # Success

            except Exception as e:
                self.error_count += 1

                if attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    print(f"[PERSISTENCE] Save failed (attempt {attempt+1}/{max_retries}): {e}")
                    print(f"[PERSISTENCE] Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"[PERSISTENCE] Save failed after {max_retries} attempts: {e}")
                    print("[PERSISTENCE] Warning: Activity log may not be persisted to disk")

# Global persistence worker instance
activity_log_persistence_worker = ActivityLogPersistenceWorker(debounce_ms=100)

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
    """
    Save activity log to file with rotation (thread-safe).

    NOTE: This function is called from background thread only.
    Do NOT call directly from main threads - use activity_log_persistence_worker.queue_save()
    """
    try:
        os.makedirs('logs', exist_ok=True)
        with shared_data.lock:
            log_copy = shared_data.activity_log.copy()

        # PRODUCTION-GRADE: Check log size and rotate if needed
        if os.path.exists(ACTIVITY_LOG_FILE):
            file_size = os.path.getsize(ACTIVITY_LOG_FILE)
            if file_size > 10 * 1024 * 1024:  # 10MB rotation threshold
                rotate_activity_log()

        # FIX: Add thread ID to prevent race condition when multiple threads try to save
        temp_file = f"{ACTIVITY_LOG_FILE}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(temp_file, 'wb') as f:
            pickle.dump(log_copy, f)

        if os.path.exists(ACTIVITY_LOG_FILE):
            os.remove(ACTIVITY_LOG_FILE)
        os.rename(temp_file, ACTIVITY_LOG_FILE)
    except Exception as e:
        print(f"[WARN] Failed to save activity log: {e}")

def rotate_activity_log():
    """
    Rotate activity log files for production archival.

    NOTE: This can be slow (1-2s with many archives), but that's OK
    because it runs in background thread and doesn't block SSE events.
    """
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        archive_file = f"{ACTIVITY_LOG_FILE}.{timestamp}"

        if os.path.exists(ACTIVITY_LOG_FILE):
            os.rename(ACTIVITY_LOG_FILE, archive_file)
            print(f"[LOG_ROTATION] Archived activity log to {archive_file}")

            # Clean up old archives (keep last 10)
            log_dir = os.path.dirname(ACTIVITY_LOG_FILE) or '.'
            archive_pattern = os.path.basename(ACTIVITY_LOG_FILE)
            archives = []

            # OPTIMIZATION: Only scan logs/ directory, not entire codebase
            try:
                for file in os.listdir(log_dir):
                    # Match pattern: activity_log.pkl.TIMESTAMP or activity_log N.pkl
                    if (file.startswith(archive_pattern) and
                        file != os.path.basename(ACTIVITY_LOG_FILE) and
                        (file.endswith('.pkl') or '.pkl.' in file)):
                        archives.append(os.path.join(log_dir, file))
            except OSError as e:
                print(f"[LOG_ROTATION] Warning: Could not list archives: {e}")
                return

            # Sort by modification time and keep newest 10
            archives.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)

            removed_count = 0
            for old_archive in archives[10:]:
                try:
                    os.remove(old_archive)
                    removed_count += 1
                except OSError as e:
                    print(f"[LOG_ROTATION] Warning: Could not remove {old_archive}: {e}")

            if removed_count > 0:
                print(f"[LOG_ROTATION] Cleaned up {removed_count} old archive(s)")

    except Exception as e:
        print(f"[WARN] Failed to rotate activity log: {e}")

def add_activity_log(message, level="info", category="system"):
    """
    Add entry to activity log with timestamp and persistence (thread-safe).

    CRITICAL FIX: File I/O is decoupled from SSE broadcasting.
    - Entry added to shared_data.activity_log immediately (fast)
    - Save queued to background worker (non-blocking)
    - SSE event broadcast immediately (no waiting for file I/O)

    This ensures dashboard updates are never blocked by slow disk operations.
    """
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

    # STEP 1: Add to in-memory log (thread-safe, fast - ~50µs)
    with shared_data.lock:
        shared_data.activity_log.insert(0, entry)

        # Keep only last 500 entries
        if len(shared_data.activity_log) > 500:
            shared_data.activity_log = shared_data.activity_log[:500]

    # STEP 2: Queue save to background worker (non-blocking - ~20µs)
    # CRITICAL FIX: File I/O happens asynchronously, doesn't block this function
    activity_log_persistence_worker.queue_save()

    # STEP 3: Broadcast to SSE clients IMMEDIATELY (fast - ~100µs)
    # CRITICAL FIX: This happens BEFORE file save completes
    # Dashboard receives update in <1ms instead of 1-2000ms
    broadcast_sse_event('activity_log', entry)

    # STEP 4: Console logging (non-blocking)
    print(f"[{timestamp.strftime('%H:%M:%S')}] [{category.upper()}] {message}")

def initialize_global_purchase_manager():
    """Initialize global purchase manager and event loop - called once at startup"""
    global global_purchase_manager, global_event_loop, global_event_loop_thread

    if global_purchase_manager is not None:
        print("[SYSTEM] Global purchase manager already initialized")
        return

    import asyncio
    import concurrent.futures

    print("[SYSTEM] ═══════════════════════════════════════════════")
    print("[SYSTEM] Initializing global purchase manager...")
    print("[SYSTEM] ═══════════════════════════════════════════════")

    # Create event loop that will run forever in background thread
    global_event_loop = asyncio.new_event_loop()

    def run_event_loop(loop):
        """Background thread function that runs the event loop forever"""
        asyncio.set_event_loop(loop)
        print("[EVENT_LOOP] Starting global event loop in background thread...")
        loop.run_forever()
        print("[EVENT_LOOP] Event loop stopped")

    # Start event loop in dedicated thread
    global_event_loop_thread = threading.Thread(
        target=run_event_loop,
        args=(global_event_loop,),
        daemon=True,
        name="GlobalAsyncIOEventLoop"
    )
    global_event_loop_thread.start()
    print("[EVENT_LOOP] ✅ Global event loop thread started")

    # Create global purchase manager
    global_purchase_manager = BulletproofPurchaseManager(status_callback=purchase_status_callback)
    print("[SYSTEM] ✅ Global purchase manager created")

    # Initialize session asynchronously using the global event loop
    try:
        print("[SYSTEM] Initializing browser session (timeout: 90s)...")
        future = asyncio.run_coroutine_threadsafe(
            global_purchase_manager._ensure_session_ready(),
            global_event_loop
        )

        # Wait for initialization to complete
        session_ready = future.result(timeout=90)

        if session_ready:
            print("[SYSTEM] ✅ Browser session initialized successfully")
            add_activity_log("Global purchase manager initialized - browser ready", "success", "system")
        else:
            print("[SYSTEM] ⚠️  Browser session initialization failed - mock mode")
            add_activity_log("Browser session failed - using mock mode", "warning", "system")

    except concurrent.futures.TimeoutError:
        print("[SYSTEM] ❌ Browser session initialization timeout after 90s")
        print("[SYSTEM] ⚠️  Browser failed to launch - check Playwright installation")
        add_activity_log("Browser session timeout - check Playwright/Patchright installation", "error", "system")
    except Exception as e:
        print(f"[SYSTEM] ❌ Error initializing browser: {e}")
        import traceback
        traceback.print_exc()
        add_activity_log(f"Browser initialization error: {str(e)}", "error", "system")

    print("[SYSTEM] ═══════════════════════════════════════════════")

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
        # CRITICAL FIX: Broadcast to ALL clients
        with sse_queue_lock:
            clients_count = len(sse_client_queues)
            if clients_count == 0:
                print(f"[SSE_WARN] No clients connected for {event_type} event!")
            for client_id, client_queue in sse_client_queues.items():
                try:
                    client_queue.put(event_data, block=False)
                    print(f"[SSE_DEBUG] Sent {event_type} to client {client_id[:8]}... (queue size: {client_queue.qsize()})")
                except queue.Full:
                    print(f"[SSE] ⚠️ ERROR: Queue FULL for client {client_id} on {event_type} event!")
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

        # BUGFIX: Include activity log in atomic event (last 50 entries for dashboard)
        with shared_data.lock:
            activity_log = shared_data.activity_log[-50:] if shared_data.activity_log else []

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
                'summary': summary,
                'activity_log': activity_log  # BUGFIX: Include activity log for real-time dashboard updates
            }
        }

        # CRITICAL FIX: Broadcast to ALL connected clients, not just one
        with sse_queue_lock:
            clients_count = len(sse_client_queues)
            if clients_count == 0:
                print(f"[SSE] ⚠️ WARNING: No clients connected! Event {cycle_id} will not be delivered to any dashboard.")
                print(f"[SSE] ⚠️ This means dashboards won't update in real-time. Check if EventSource connection is established.")
            else:
                print(f"[SSE_DEBUG] About to broadcast atomic event {cycle_id} to {clients_count} client(s)")
                print(f"[SSE_DEBUG] Event contains {len(stock_updates)} stock updates, {len(activity_log)} activity log entries")
                for client_id, client_queue in sse_client_queues.items():
                    try:
                        queue_size_before = client_queue.qsize()
                        client_queue.put(atomic_event, block=False)
                        queue_size_after = client_queue.qsize()
                        print(f"[SSE_DEBUG] Client {client_id[:8]}... queue: {queue_size_before} → {queue_size_after}")
                    except queue.Full:
                        print(f"[SSE] ⚠️ ERROR: Queue FULL for client {client_id}, skipping event {cycle_id}!")
        print(f"[SSE] ✅ Broadcast atomic API cycle event {cycle_id} to {clients_count} client(s)")

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

        # Set timer immediately so dashboard shows countdown during warmup/initial wait
        # This prevents the dashboard from being stuck on "loading" state
        with self.shared_data.lock:
            self.shared_data.timer_start_time = time.time()
            self.shared_data.timer_duration = initial_cycle_duration
        print(f"[STOCK_MONITOR] Timer initialized for {initial_cycle_duration}s - dashboard will show countdown")

        # ULTRA-FAST WARMUP - Minimal delay for competitive bot performance
        # Browser launches in ~2-3 seconds with optimized settings
        # 5 second warmup is plenty for browser to be ready
        if not is_manual_refresh:
            warmup_seconds = 5
            print(f"[STOCK_MONITOR] [WARMUP] ⚡ FAST MODE: {warmup_seconds}s warmup for browser launch")
            print("[STOCK_MONITOR] [WARMUP] ⚡ Optimized for competitive bot speed")

            for i in range(warmup_seconds):
                if not self.running:
                    return
                time.sleep(1)

            print("[STOCK_MONITOR] [OK] ⚡ Ultra-fast warmup complete - ready for purchases!")

        # CRITICAL FIX: First stock check happens INSIDE timer loop to sync with dashboard
        print("[STOCK_MONITOR] ═══════════════════════════════════════════════════════")
        print("[STOCK_MONITOR] Entering timer-synchronized monitoring loop...")
        print("[STOCK_MONITOR] First stock check will happen after initial timer countdown")
        print("[STOCK_MONITOR] ═══════════════════════════════════════════════════════")

        first_cycle = True  # Flag to handle first iteration

        while self.running:
            try:
                # Generate new cycle duration for this iteration
                # TEST_MODE uses longer cycles to ensure purchase + cart clear completes
                if os.environ.get('TEST_MODE', 'false').lower() == 'true':
                    cycle_duration = random.randint(25, 35)  # Longer for TEST_MODE
                else:
                    cycle_duration = random.randint(15, 25)  # Production

                start_time = time.time()

                # Start timer countdown
                with self.shared_data.lock:
                    self.shared_data.timer_start_time = start_time
                    self.shared_data.timer_duration = cycle_duration

                # FIRST CYCLE: Do stock check immediately, THEN start timer
                # SUBSEQUENT CYCLES: Wait for timer first, THEN check stock
                if first_cycle:
                    print(f"[STOCK_MONITOR] ═══════════════════════════════════════════════════════")
                    print(f"[STOCK_MONITOR] FIRST CYCLE: Performing immediate stock check")
                    print(f"[STOCK_MONITOR] Subsequent cycles will wait for full timer countdown")
                    print(f"[STOCK_MONITOR] ═══════════════════════════════════════════════════════")
                    first_cycle = False
                    # Skip wait on first iteration - go straight to stock check below
                else:
                    print(f"[STOCK_MONITOR] ═══════════════════════════════════════════════════════")
                    print(f"[STOCK_MONITOR] Starting {cycle_duration}s countdown...")
                    print(f"[STOCK_MONITOR] Purchase will be processing during this countdown")
                    print(f"[STOCK_MONITOR] Next stock check will happen when timer hits 0")
                    print(f"[STOCK_MONITOR] ═══════════════════════════════════════════════════════")

                    # WAIT FIRST (let purchase happen during countdown)
                    # This ensures browser stays on cart page until timer expires
                    for i in range(cycle_duration):
                        if not self.running:
                            return
                        time.sleep(1)

                # THEN check stock when timer expires
                print(f"[STOCK_MONITOR] ═══════════════════════════════════════════════════════")
                print(f"[STOCK_MONITOR] Timer expired - performing stock check NOW...")
                print(f"[STOCK_MONITOR] ═══════════════════════════════════════════════════════")

                # Perform stock check with test mode awareness
                with self.shared_data.lock:
                    test_mode_active = (hasattr(self.shared_data, 'test_monitor') and
                                       self.shared_data.test_monitor is not None and
                                       self.shared_data.test_monitor.test_mode)
                    if test_mode_active:
                        scenario = self.shared_data.test_monitor.test_scenario
                        cycle_count = self.shared_data.test_monitor.test_cycle_count + 1
                        print(f"[STOCK_MONITOR] TEST MODE stock check (scenario: {scenario}, cycle: {cycle_count})")

                stock_data = self._check_stock()

                if stock_data:
                    print("[STOCK_MONITOR] [OK] Stock check complete - publishing to dashboard...")
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
                    print("[STOCK_MONITOR] [WARNING] Stock check failed, will retry next cycle")

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
                        add_activity_log("[WARNING] API circuit breaker activated - service degraded", "warning", "circuit_breaker")

            return None

class PurchaseManagerThread:
    """Dedicated thread for purchase management with atomic state transitions"""
    def __init__(self, event_bus, shared_data, purchase_manager=None, event_loop=None):
        self.event_bus = event_bus
        self.shared_data = shared_data

        # Use global purchase manager if provided, otherwise create new one
        if purchase_manager is not None:
            self.purchase_manager = purchase_manager
            self.using_global_manager = True
            print("[SYSTEM] Using global purchase manager instance")
        else:
            self.purchase_manager = BulletproofPurchaseManager(status_callback=purchase_status_callback)
            self.using_global_manager = False
            print("[SYSTEM] Created new purchase manager instance")

        self.running = False
        self.thread = None

        # Event loop management for async operations
        # Use global event loop if provided
        if event_loop is not None:
            self.event_loop = event_loop
            self.event_loop_thread = None  # Not managing the thread
            self.using_global_event_loop = True
            print("[SYSTEM] Using global event loop")
        else:
            self.event_loop = None
            self.event_loop_thread = None
            self.using_global_event_loop = False

        # Initialize real-time validation system
        # Removed test validation system - using core purchase manager only
        print("[SYSTEM] Core bulletproof monitoring active")

        # Subscribe to stock update events
        self.event_bus.subscribe('stock_updated', self._handle_stock_update)

    def _initialize_session_system(self):
        """Initialize persistent session system - called only once"""
        def run_event_loop(loop):
            """Background thread function that runs the event loop forever"""
            import asyncio
            asyncio.set_event_loop(loop)
            print("[EVENT_LOOP] Starting event loop in background thread...")
            loop.run_forever()
            print("[EVENT_LOOP] Event loop stopped")

        def session_init_task():
            try:
                print("[PURCHASE_THREAD] [INIT] ═══════════════════════════════════════════════")
                print("[PURCHASE_THREAD] [INIT] Starting persistent session system initialization...")
                print("[PURCHASE_THREAD] [INIT] ═══════════════════════════════════════════════")
                init_start_time = time.time()

                # Create event loop and start it in background thread
                import asyncio
                self.event_loop = asyncio.new_event_loop()

                # Start event loop in dedicated thread (MUST use run_forever() for run_coroutine_threadsafe to work)
                self.event_loop_thread = threading.Thread(
                    target=run_event_loop,
                    args=(self.event_loop,),
                    daemon=True,
                    name="AsyncIOEventLoop"
                )
                self.event_loop_thread.start()
                print("[EVENT_LOOP] ✅ Event loop thread started")

                # Run async session initialization using run_coroutine_threadsafe
                print("[PURCHASE_THREAD] [INIT] Calling _ensure_session_ready() (timeout: 30s)...")
                print("[PURCHASE_THREAD] [INIT] This will launch browser and navigate to Target.com...")
                future = asyncio.run_coroutine_threadsafe(
                    self.purchase_manager._ensure_session_ready(),
                    self.event_loop
                )

                # Wait for initialization to complete (30s timeout - browser should init in 3-10s)
                print("[PURCHASE_THREAD] [INIT] Waiting for session initialization to complete...")
                session_ready = future.result(timeout=30)

                init_duration = time.time() - init_start_time

                if session_ready:
                    print(f"[PURCHASE_THREAD] [INIT] ✅ SUCCESS! Session initialized in {init_duration:.1f}s")
                    print("[PURCHASE_THREAD] [INIT] Browser is now at Target.com and ready for purchases")
                    print("[PURCHASE_THREAD] [INIT] ═══════════════════════════════════════════════")
                    add_activity_log(f"Session initialized successfully ({init_duration:.1f}s) - browser ready", "success", "session")
                else:
                    print(f"[PURCHASE_THREAD] [INIT] ❌ FAILED after {init_duration:.1f}s - falling back to mock mode")
                    print("[PURCHASE_THREAD] [INIT] ═══════════════════════════════════════════════")
                    add_activity_log("Session initialization failed - using mock purchasing", "warning", "session")

            except concurrent.futures.TimeoutError:
                init_duration = time.time() - init_start_time
                print(f"[PURCHASE_THREAD] [INIT] ❌ TIMEOUT after {init_duration:.1f}s")
                print("[PURCHASE_THREAD] [INIT] Session initialization took too long - falling back to mock mode")
                print("[PURCHASE_THREAD] [INIT] ═══════════════════════════════════════════════")
                add_activity_log(f"Session initialization timeout ({init_duration:.1f}s) - using mock mode", "error", "session")
            except Exception as e:
                init_duration = time.time() - init_start_time
                print(f"[PURCHASE_THREAD] [INIT] ❌ ERROR after {init_duration:.1f}s: {e}")
                import traceback
                traceback.print_exc()
                print("[PURCHASE_THREAD] [INIT] ═══════════════════════════════════════════════")
                add_activity_log(f"Session initialization error: {str(e)}", "error", "session")

        # Run session initialization in background to avoid blocking startup
        threading.Thread(target=session_init_task, daemon=True).start()

    def start(self):
        """Start the purchase management thread"""
        self.running = True

        # Initialize session system ONCE at startup (only if NOT using global manager)
        if not self.using_global_manager:
            self._initialize_session_system()
        else:
            print("[SYSTEM] Skipping session initialization (using global manager)")

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
                    # Run async cleanup using existing event loop (if available)
                    if self.event_loop and self.event_loop.is_running():
                        print("[SESSION] Running cleanup via existing event loop...")
                        import asyncio
                        future = asyncio.run_coroutine_threadsafe(
                            self.purchase_manager.session_manager.cleanup(),
                            self.event_loop
                        )
                        future.result(timeout=10)
                        print("[SESSION] Session manager cleaned up")
                    else:
                        print("[SESSION] Event loop not available, skipping async cleanup")

                # Stop event loop if it's running
                if self.event_loop and self.event_loop.is_running():
                    print("[EVENT_LOOP] Stopping event loop...")
                    self.event_loop.call_soon_threadsafe(self.event_loop.stop)

                    # Wait for event loop thread to finish (with timeout)
                    if self.event_loop_thread and self.event_loop_thread.is_alive():
                        self.event_loop_thread.join(timeout=5)
                        if self.event_loop_thread.is_alive():
                            print("[EVENT_LOOP] Warning: Event loop thread did not stop gracefully")
                        else:
                            print("[EVENT_LOOP] Event loop thread stopped")

                    # Close the event loop
                    if self.event_loop:
                        self.event_loop.close()
                        print("[EVENT_LOOP] Event loop closed")

            except Exception as e:
                print(f"[SESSION] Cleanup error: {e}")
                import traceback
                traceback.print_exc()

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

            # CRITICAL FIX: Sync fresh stock data to shared_data for dashboard consistency
            # This ensures /api/status endpoint returns current data, not stale cache
            with shared_data.lock:
                shared_data.stock_data = stock_data.copy()
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] ✅ Synced fresh stock data to shared_data cache")

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

            # CRITICAL SAFETY CHECK: Wait for any background threads to finish
            # This prevents race condition where thread is completing but state not yet saved
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Checking for active background threads...")
            max_wait_cycles = 30  # 30 seconds max wait
            wait_cycle = 0
            while wait_cycle < max_wait_cycles:
                with self.purchase_manager._state_lock:
                    active_threads = dict(self.purchase_manager._active_purchases)

                if not active_threads:
                    if wait_cycle == 0:
                        print(f"[CYCLE ATOMIC CYCLE {cycle_id}] ✅ No active background threads")
                    break

                if wait_cycle == 0:
                    for tcin, info in active_threads.items():
                        elapsed = time.time() - info['started_at']
                        print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Waiting for thread: {tcin} (running {elapsed:.1f}s, status: {info['status']})")

                time.sleep(1)
                wait_cycle += 1

            if wait_cycle >= max_wait_cycles:
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] ⚠️ WARNING: Thread timeout after {max_wait_cycles}s")
                # Force-remove from active (safety fallback)
                with self.purchase_manager._state_lock:
                    if self.purchase_manager._active_purchases:
                        print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Force-clearing {len(self.purchase_manager._active_purchases)} stuck threads")
                        self.purchase_manager._active_purchases.clear()
            elif wait_cycle > 0:
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] ✅ Background threads completed after {wait_cycle}s wait")

            # TEST_MODE: Reset all completed purchases for continuous testing loop
            # PROD_MODE: Only reset OUT OF STOCK completed purchases (stock-aware reset)
            if os.environ.get('TEST_MODE', 'false').lower() == 'true':
                reset_count = self.purchase_manager.reset_completed_purchases_to_ready()
            else:
                reset_count = self.purchase_manager.reset_completed_purchases_by_stock_status(stock_data)

            # Verify the stock-aware reset completed correctly (only in PROD_MODE)
            # TEST_MODE resets ALL, so stock-aware verification doesn't apply
            if os.environ.get('TEST_MODE', 'false').lower() != 'true':
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

            # CRITICAL: Sync resets to shared_data BEFORE starting new purchases
            # This prevents race conditions where resets are invisible to dashboard
            with shared_data.lock:
                shared_data.purchase_states = self.purchase_manager.get_all_states()
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] Synced {reset_count} resets to shared_data")

            # CRITICAL FIX (TEST_MODE): Force immediate dashboard update for resets
            # Ensures dashboard shows "ready" status immediately after "purchased"
            if os.environ.get('TEST_MODE', 'false').lower() == 'true' and reset_count > 0:
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] [TEST_MODE] Broadcasting reset state changes to dashboard...")
                for tcin in reset_tcins:
                    state = self.purchase_manager.get_all_states().get(tcin, {})
                    if state.get('status') == 'ready':
                        purchase_status_callback(tcin, 'ready', state)
                print(f"[CYCLE ATOMIC CYCLE {cycle_id}] [TEST_MODE] ✅ Reset broadcasts sent for {reset_count} purchases")

            # STEP 2: Process stock data according to state rules
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] STEP 2: Processing stock data...")
            in_stock_tcins = [tcin for tcin, data in stock_data.items() if data.get('in_stock')]
            out_stock_tcins = [tcin for tcin, data in stock_data.items() if not data.get('in_stock')]
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] IN STOCK: {in_stock_tcins}")
            print(f"[CYCLE ATOMIC CYCLE {cycle_id}] OUT OF STOCK: {out_stock_tcins}")

            # FIX: Add IN STOCK/OUT OF STOCK messages to activity log for dashboard display
            # These messages were only going to console, not to the activity log that the dashboard reads
            if in_stock_tcins:
                add_activity_log(f"IN STOCK: {in_stock_tcins}", "success", "api_cycle")
            if out_stock_tcins:
                add_activity_log(f"OUT OF STOCK: {out_stock_tcins}", "info", "api_cycle")

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

            # Add activity log entry with detailed TCIN information
            in_stock_display = f"[{', '.join(in_stock_tcins)}]" if in_stock_tcins else "[]"
            out_stock_display = f"[{', '.join(out_stock_tcins)}]" if out_stock_tcins else "[]"

            add_activity_log(
                f"Stock Check: {summary['in_stock_count']} in stock {in_stock_display}, {len(out_stock_tcins)} out of stock {out_stock_display} • {summary['new_attempts_count']} attempts, {summary['resets_count']} resets",
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
    print("[MONITORING_LOOP] ═══════════════════════════════════════════════")
    print("[MONITORING_LOOP] Starting bulletproof event-driven architecture...")
    print("[MONITORING_LOOP] ═══════════════════════════════════════════════")

    # Initialize event-driven thread managers
    # Use global purchase manager and event loop to avoid duplicate browser instances
    print("[MONITORING_LOOP] Creating StockMonitorThread...")
    stock_thread = StockMonitorThread(event_bus, shared_data)
    print("[MONITORING_LOOP] [OK] StockMonitorThread created")

    print("[MONITORING_LOOP] Creating PurchaseManagerThread...")
    print(f"[MONITORING_LOOP] Using global_purchase_manager: {global_purchase_manager}")
    print(f"[MONITORING_LOOP] Using global_event_loop: {global_event_loop}")
    purchase_thread = PurchaseManagerThread(
        event_bus,
        shared_data,
        purchase_manager=global_purchase_manager,
        event_loop=global_event_loop
    )
    print("[MONITORING_LOOP] [OK] PurchaseManagerThread created")

    print("[MONITORING_LOOP] Setting monitor_running=True...")
    with shared_data.lock:
        shared_data.monitor_running = True
    print(f"[MONITORING_LOOP] [VERIFY] monitor_running: {shared_data.monitor_running}")

    # Start independent threads
    print("[MONITORING_LOOP] Starting stock_thread...")
    stock_thread.start()
    print(f"[MONITORING_LOOP] [OK] stock_thread started (is_alive: {stock_thread.thread.is_alive() if stock_thread.thread else 'N/A'})")

    print("[MONITORING_LOOP] Starting purchase_thread...")
    purchase_thread.start()
    print(f"[MONITORING_LOOP] [OK] purchase_thread started (is_alive: {purchase_thread.thread.is_alive() if purchase_thread.thread else 'N/A'})")

    print("[MONITORING_LOOP] ═══════════════════════════════════════════════")
    print("[MONITORING_LOOP] ✅ All threads started successfully")
    print("[MONITORING_LOOP] Entering health monitoring loop...")
    print("[MONITORING_LOOP] ═══════════════════════════════════════════════")

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
                # Reuse global purchase manager and event loop on restart
                purchase_thread = PurchaseManagerThread(
                    event_bus,
                    shared_data,
                    purchase_manager=global_purchase_manager,
                    event_loop=global_event_loop
                )
                purchase_thread.start()

    finally:
        # Clean shutdown
        print("[MONITOR] Shutting down threads...")
        stock_thread.stop()
        purchase_thread.stop()
        add_activity_log("Monitoring threads stopped", "info", "system")

def start_monitoring():
    """Start the background monitoring thread"""
    print("[START_MONITORING] ═══════════════════════════════════════════════")
    print("[START_MONITORING] Initializing monitoring system...")
    print("[START_MONITORING] ═══════════════════════════════════════════════")

    # Ensure global purchase manager is initialized
    print(f"[START_MONITORING] Checking global_purchase_manager: {global_purchase_manager}")
    if global_purchase_manager is None:
        print("[START_MONITORING] [WARNING] global_purchase_manager is None, initializing...")
        initialize_global_purchase_manager()
        print(f"[START_MONITORING] After init: {global_purchase_manager}")
    else:
        print("[START_MONITORING] [OK] global_purchase_manager already initialized")

    # Initialize test monitor for simulated stock data
    print("[START_MONITORING] Creating StockMonitor instance...")
    shared_data.test_monitor = StockMonitor()
    print("[START_MONITORING] [OK] Test monitor initialized for simulated stock data")

    # FIX #1: Pre-populate stock data on startup (CRITICAL)
    # This ensures /api/current-state has data when dashboard first loads
    print("[START_MONITORING] Running initial stock check to pre-populate data...")
    try:
        initial_stock_data = shared_data.test_monitor.check_stock()
        if initial_stock_data:
            with shared_data.lock:
                shared_data.stock_data = initial_stock_data
                from datetime import datetime
                shared_data.last_update_time = datetime.now()
            print(f"[START_MONITORING] ✓ Pre-populated stock data for {len(initial_stock_data)} products")
            in_stock = [tcin for tcin, info in initial_stock_data.items() if info.get('in_stock', False)]
            out_of_stock = [tcin for tcin, info in initial_stock_data.items() if not info.get('in_stock', False)]
            print(f"[START_MONITORING] Initial Stock Status - IN STOCK: {in_stock}")
            print(f"[START_MONITORING] Initial Stock Status - OUT OF STOCK: {out_of_stock}")
        else:
            print("[START_MONITORING] ⚠ Initial stock check returned no data")
    except Exception as e:
        print(f"[START_MONITORING] ⚠ Error during initial stock check: {e}")
        import traceback
        traceback.print_exc()

    # Start monitoring thread
    print("[START_MONITORING] Creating monitoring thread...")
    monitor_thread = threading.Thread(target=monitoring_loop, daemon=True, name="MonitoringLoop")
    print("[START_MONITORING] Starting monitoring thread...")
    monitor_thread.start()
    print(f"[START_MONITORING] [OK] Monitoring thread started: {monitor_thread.name} (is_alive: {monitor_thread.is_alive()})")

    # Wait a moment to let monitoring loop set monitor_running flag
    import time
    time.sleep(0.5)

    print(f"[START_MONITORING] [VERIFY] shared_data.monitor_running: {shared_data.monitor_running}")
    print("[START_MONITORING] ═══════════════════════════════════════════════")

    add_activity_log("Background monitoring started", "success", "system")
    print("[START_MONITORING] Monitoring system initialization complete")

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

@app.route('/v2')
def index_v2():
    """New modern dashboard route (v2)"""
    try:
        # Load product configuration
        stock_monitor = StockMonitor()
        config = stock_monitor.get_config()

        # Load catalog
        try:
            with open('config/product_catalog.json', 'r') as f:
                catalog_config = json.load(f)
        except Exception as e:
            print(f"[V2] Catalog load error: {e}")
            catalog_config = {'catalog': []}

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

        # Get list of TCINs actively being monitored
        active_tcins = [p.get('tcin') for p in config.get('products', [])]

        # Add active monitoring status to each catalog item
        for catalog_item in catalog_config.get('catalog', []):
            catalog_item['is_actively_monitored'] = catalog_item['tcin'] in active_tcins

        # SIMPLE FIX: Broadcast metric update immediately when page loads
        # This ensures when you edit config and refresh, other dashboards update instantly
        metric_update_event = {
            'type': 'metric_update',
            'timestamp': datetime.now().isoformat(),
            'data': {
                'total_products': len(config.get('products', [])),
                'in_stock_count': in_stock_count
            }
        }
        with sse_queue_lock:
            for client_id, client_queue in sse_client_queues.items():
                try:
                    client_queue.put(metric_update_event, block=False)
                except queue.Full:
                    pass

        return render_template('simple_dashboard_v2.html',
                             config=config,
                             catalog=catalog_config,
                             status=status,
                             activity_log=current_activity_log,
                             timestamp=datetime.now())
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[V2] Route error: {e}")
        print(error_details)
        return f"<h1>Error loading V2 dashboard</h1><pre>{error_details}</pre>", 500

@app.route('/api/stream')
def sse_stream():
    """Server-Sent Events stream for real-time updates with per-client queues"""
    def event_stream():
        # FIX #2: Use UUID instead of thread ID for reliable client identification
        # Thread IDs can be reused when Flask recycles threads from the pool
        import uuid
        client_id = str(uuid.uuid4())
        shared_data.connected_clients.add(client_id)

        # CRITICAL FIX: Create dedicated queue for this client
        client_queue = queue.Queue(maxsize=100)  # Prevent memory issues with max size
        with sse_queue_lock:
            sse_client_queues[client_id] = client_queue
            print(f"[SSE] Client {client_id} connected (total clients: {len(sse_client_queues)})")

        # FIX #3: Send initial connection event immediately to establish SSE stream
        # This prevents the generator from blocking on queue.get() before any data flows
        yield f"data: {json.dumps({'type': 'connected', 'client_id': client_id, 'timestamp': datetime.now().isoformat()})}\n\n"

        try:
            while True:
                try:
                    # CRITICAL FIX: Use short timeout (1s) to process events immediately
                    # Previous 30s timeout caused events to queue up without being sent
                    event = client_queue.get(timeout=1)

                    print(f"[SSE_GENERATOR] Client {client_id[:8]}... got event type={event.get('type')}, queue_remaining={client_queue.qsize()}")

                    # Format SSE data and yield immediately
                    yield f"data: {json.dumps(event)}\n\n"

                    # CRITICAL: Continue processing if more events are queued
                    # Don't wait for timeout if queue has items
                    drained = 0
                    while not client_queue.empty():
                        try:
                            event = client_queue.get_nowait()
                            drained += 1
                            yield f"data: {json.dumps(event)}\n\n"
                        except queue.Empty:
                            break

                    if drained > 0:
                        print(f"[SSE_GENERATOR] Client {client_id[:8]}... drained {drained} additional events")

                except queue.Empty:
                    # Send heartbeat every 1s to keep connection alive
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"

                except Exception as e:
                    print(f"[SSE] Error in event stream for client {client_id}: {e}")
                    break

        finally:
            # CRITICAL: Clean up client queue on disconnect
            with sse_queue_lock:
                if client_id in sse_client_queues:
                    del sse_client_queues[client_id]
                    print(f"[SSE] Client {client_id} disconnected (remaining: {len(sse_client_queues)})")
            shared_data.connected_clients.discard(client_id)

    response = Response(event_stream(), content_type='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    # FIX: Remove Connection header - Waitress rejects it as "hop-by-hop" header (PEP 3333)
    # Waitress handles keep-alive automatically, explicit header causes AssertionError
    # response.headers['Connection'] = 'keep-alive'  # REMOVED - incompatible with Waitress
    response.headers['Access-Control-Allow-Origin'] = '*'
    # FIX #1 (CRITICAL): Disable response buffering for SSE streaming
    response.headers['X-Accel-Buffering'] = 'no'
    return response

@app.route('/api/status')
def api_status():
    """API endpoint for current status"""
    with shared_data.lock:
        # Transform stock_data to match SSE event format
        # This ensures /api/current-state and SSE events use identical field names
        stock_updates = {}
        for tcin, data in shared_data.stock_data.items():
            stock_updates[tcin] = {
                'title': data.get('title', f'Product {tcin}'),
                'in_stock': data.get('in_stock', False),
                'status_detail': data.get('status_detail', 'UNKNOWN'),
                'last_checked': data.get('last_checked'),
                'is_preorder': data.get('is_preorder', False)
            }

        # FIX: Calculate in_stock_count for polling fallback UI updates
        # Frontend polling handler expects this field to update the in-stock count statistic
        in_stock_count = sum(1 for data in stock_updates.values() if data.get('in_stock', False))

        return jsonify({
            'stock_data': stock_updates,  # Use transformed data with consistent field names
            'purchase_states': shared_data.purchase_states,
            'monitoring': shared_data.monitor_running,
            'connected_clients': len(shared_data.connected_clients),
            'last_update': shared_data.last_update_time.isoformat() if shared_data.last_update_time else None,
            'activity_log': shared_data.activity_log[-100:] if shared_data.activity_log else [],  # Include activity log
            'in_stock_count': in_stock_count  # FIX: Add in_stock_count field for dashboard stats
        })

@app.route('/api/current-state')
def api_current_state():
    """API endpoint for current state (alias for /api/status for dashboard compatibility)"""
    return api_status()

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

        if not tcin or not tcin.isdigit() or len(tcin) < 8 or len(tcin) > 10:
            error_msg = "Invalid TCIN format (must be 8-10 digits)"
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

        # Validate TCIN (Target uses 8-10 digit TCINs)
        if not tcin or not tcin.isdigit() or len(tcin) < 8 or len(tcin) > 10:
            return jsonify({'success': False, 'error': 'Invalid TCIN format (must be 8-10 digits)'})

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

        # CRITICAL FIX: Broadcast to ALL client queues
        with sse_queue_lock:
            for client_id, client_queue in sse_client_queues.items():
                try:
                    client_queue.put(event_data, block=False)
                except queue.Full:
                    print(f"[SSE] Warning: Queue full for client {client_id}")

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

    # ========== START BACKGROUND PERSISTENCE WORKER ==========
    # CRITICAL: Must start before any add_activity_log() calls
    print("[SYSTEM] Starting activity log persistence worker...")
    activity_log_persistence_worker.start()

    add_activity_log("Bulletproof monitoring dashboard initialized", "info", "system")
    add_activity_log("Activity log persistence worker started", "success", "system")

    # Start background monitoring
    start_monitoring()

    # ========== GRACEFUL SHUTDOWN HANDLER ==========
    def shutdown_handler(signum=None, frame=None):
        """Graceful shutdown - ensure activity log is saved"""
        print("\n[SYSTEM] Shutting down gracefully...")
        add_activity_log("Application shutting down", "info", "system")

        # Stop persistence worker (waits for pending saves)
        activity_log_persistence_worker.stop(timeout=5)

        print("[SYSTEM] Shutdown complete")
        sys.exit(0)

    # Register shutdown handlers
    atexit.register(shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Run Flask app with Waitress (production-grade WSGI server)
    # FIX: Replace Flask dev server with Waitress for proper SSE streaming (no buffering)
    # Waitress handles Server-Sent Events correctly without response buffering
    print("[WAITRESS] Starting production WSGI server for SSE streaming...")
    print("[WAITRESS] Dashboard accessible at http://127.0.0.1:5001")
    serve(app, host='127.0.0.1', port=5001, threads=6, channel_timeout=300)