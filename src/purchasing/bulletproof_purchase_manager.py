#!/usr/bin/env python3
"""
Bulletproof Purchase Manager - Thread-safe with atomic operations and real-time updates
Prevents all race conditions, handles infinite purchase loops, and provides real-time status
"""

import json
import time
import random
import os
import threading
import asyncio
import concurrent.futures
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Callable

# Import session management components
from ..session import SessionManager, SessionKeepAlive, PurchaseExecutor

# Cross-platform file locking
import platform
if platform.system() == 'Windows':
    import msvcrt
    HAS_MSVCRT = True
    HAS_FCNTL = False
else:
    import fcntl
    HAS_MSVCRT = False
    HAS_FCNTL = True

class BulletproofPurchaseManager:
    def __init__(self, status_callback: Optional[Callable] = None):
        self.state_file = 'logs/purchase_states.json'
        self.lock_file = 'logs/purchase_states.lock'
        self.config = {
            'duration_min': 3.0,
            'duration_max': 3.0,
            'success_rate': 0.7,
            'cooldown_seconds': 0  # No cooldown - immediate retry on next stock check
        }

        # [FLAG] FEATURE FLAGS AND SAFETY MEASURES
        self.feature_flags = {
            # Main persistent session feature flag (environment controlled)
            'enable_persistent_session': os.environ.get('ENABLE_PERSISTENT_SESSION', 'true').lower() == 'true',

            # Safety rollback flag (can disable persistent session if issues arise)
            'force_mock_mode': os.environ.get('FORCE_MOCK_MODE', 'false').lower() == 'true',

            # Debug mode for detailed session logging
            'debug_session_system': os.environ.get('DEBUG_SESSION_SYSTEM', 'false').lower() == 'true',

            # Circuit breaker - disable persistent session after X consecutive failures
            'enable_circuit_breaker': True,
            'max_session_failures': int(os.environ.get('MAX_SESSION_FAILURES', '3')),  # Reduced from 5 to 3
        }

        # Persistent session management with safety tracking
        self.session_manager = None
        self.session_keepalive = None
        self.purchase_executor = None
        self.session_initialized = False
        self.session_failure_count = 0  # Track consecutive failures
        self.session_circuit_open = False  # Circuit breaker state
        self.use_real_purchasing = self.feature_flags['enable_persistent_session'] and not self.feature_flags['force_mock_mode']

        # DIAGNOSTIC LOGGING: Show configuration at startup
        print("=" * 80)
        print("[INIT] BulletproofPurchaseManager Configuration:")
        print(f"[INIT]   enable_persistent_session: {self.feature_flags['enable_persistent_session']}")
        print(f"[INIT]   force_mock_mode: {self.feature_flags['force_mock_mode']}")
        print(f"[INIT]   use_real_purchasing: {self.use_real_purchasing}")
        print(f"[INIT]   TEST_MODE: {os.environ.get('TEST_MODE', 'false')}")
        print("=" * 80)

        # Thread synchronization
        self._file_lock = threading.Lock()
        self._state_lock = threading.RLock()  # CRITICAL: RLock allows same thread to acquire multiple times
        self._active_purchases = {}  # Track active purchase threads

        # Status callback for real-time updates
        self.status_callback = status_callback

        # Ensure logs directory exists
        os.makedirs('logs', exist_ok=True)

        # Clean up stale purchase states from previous runs
        self._cleanup_stale_states()

        # Initialize session system
        self._initialize_session_system()

    def _cleanup_stale_states(self):
        """Clean up stale purchase states on startup (prevents old states from blocking new purchases)"""
        try:
            with self._state_lock:
                states = self._load_states_unsafe()
                if not states:
                    print("[STARTUP_CLEANUP] No existing purchase states to clean up")
                    return

                current_time = time.time()
                stale_states = []
                cleaned_count = 0

                print(f"[STARTUP_CLEANUP] Checking {len(states)} purchase states for stale entries...")

                for tcin, state in states.items():
                    status = state.get('status')
                    started_at = state.get('started_at', 0)

                    # AGGRESSIVE CLEANUP: Clean up "queued" states older than 5 seconds
                    # "queued" should quickly become "attempting" (within 1-2s)
                    # If still queued after 5s, something is broken - reset it
                    if status == 'queued' and started_at:
                        age_seconds = current_time - started_at
                        if age_seconds > 5:  # Changed from 120 to 5
                            stale_states.append({
                                'tcin': tcin,
                                'status': status,
                                'age_seconds': age_seconds
                            })
                            # Reset to ready
                            states[tcin] = {'status': 'ready'}
                            cleaned_count += 1
                            print(f"[STARTUP_CLEANUP] Cleaned up stale QUEUED state: {tcin} was queued for {age_seconds:.1f}s (should be <5s)")

                    # Clean up "attempting" states older than 120 seconds
                    elif status == 'attempting' and started_at:
                        age_seconds = current_time - started_at
                        if age_seconds > 120:
                            stale_states.append({
                                'tcin': tcin,
                                'status': status,
                                'age_seconds': age_seconds
                            })
                            # Reset to ready
                            states[tcin] = {'status': 'ready'}
                            cleaned_count += 1
                            print(f"[STARTUP_CLEANUP] Cleaned up stale ATTEMPTING state: {tcin} was attempting for {age_seconds:.1f}s")

                if cleaned_count > 0:
                    self._save_states_unsafe(states)
                    print(f"[STARTUP_CLEANUP] ✅ Cleaned up {cleaned_count} stale purchase state(s)")
                else:
                    print("[STARTUP_CLEANUP] ✅ No stale purchase states found")

        except Exception as e:
            print(f"[STARTUP_CLEANUP] ⚠️ Cleanup error (non-fatal): {e}")

    def _initialize_session_system(self):
        """Initialize persistent session management system with safety measures"""

        # [SAFETY_CHECK_1] Feature flag control
        if not self.feature_flags['enable_persistent_session']:
            print("[SESSION] [LOCKED] Persistent session DISABLED by feature flag - using mock purchasing")
            self.use_real_purchasing = False
            return

        if self.feature_flags['force_mock_mode']:
            print("[SESSION] [LOCKED] FORCE_MOCK_MODE enabled - using mock purchasing")
            self.use_real_purchasing = False
            return

        # [SAFETY_CHECK_2] Circuit breaker check
        if self.session_circuit_open:
            print("[SESSION] [LOCKED] Session circuit breaker OPEN - using mock purchasing")
            self.use_real_purchasing = False
            return

        try:
            if self.feature_flags['debug_session_system']:
                print("[SESSION] [INIT] Initializing persistent session system (DEBUG MODE)...")
                print(f"[SESSION] Feature flags: {self.feature_flags}")
            else:
                print("[SESSION] [INIT] Initializing persistent session system...")

            # Create session manager
            self.session_manager = SessionManager(session_path="target.json")

            # Create session keep-alive service with enhanced callback
            def session_status_callback(event, data):
                if self.feature_flags['debug_session_system']:
                    print(f"[SESSION] [STATS] {event}: {data}")
                else:
                    print(f"[SESSION] {event}: {data}")

                # Track session failures for circuit breaker
                if event in ['session_validation_failed', 'keep_alive_failed']:
                    self.session_failure_count += 1
                    if (self.feature_flags['enable_circuit_breaker'] and
                        self.session_failure_count >= self.feature_flags['max_session_failures']):
                        self._trigger_circuit_breaker(f"Too many {event} events")

                elif event in ['session_validated', 'keep_alive_completed']:
                    # Reset failure count on success
                    self.session_failure_count = 0

            self.session_keepalive = SessionKeepAlive(
                self.session_manager,
                status_callback=session_status_callback
            )

            # Create purchase executor with enhanced callback
            def purchase_status_callback(data):
                # Forward purchase status to main callback
                if self.status_callback and 'tcin' in data:
                    self.status_callback(data['tcin'], data['status'], data)

                # Track purchase executor failures
                if data.get('status') == 'failed' and 'session' in data.get('reason', '').lower():
                    self.session_failure_count += 1
                    if (self.feature_flags['enable_circuit_breaker'] and
                        self.session_failure_count >= self.feature_flags['max_session_failures']):
                        self._trigger_circuit_breaker("Purchase executor session failures")

            self.purchase_executor = PurchaseExecutor(
                self.session_manager,
                status_callback=purchase_status_callback
            )

            print("[SESSION] [OK] Session system components created successfully")
            print("[SESSION] [INFO] Browser will be launched when _ensure_session_ready() is called")
            # NOTE: Don't set session_initialized here - let _ensure_session_ready() set it
            # after browser actually launches. This ensures browser opens on startup.

        except Exception as e:
            print(f"[SESSION] [ERROR] Failed to initialize session system: {e}")
            self.session_failure_count += 1

            # [SAFETY_FALLBACK] Always preserve functionality
            if (self.feature_flags['enable_circuit_breaker'] and
                self.session_failure_count >= self.feature_flags['max_session_failures']):
                self._trigger_circuit_breaker(f"Initialization failure: {e}")
            else:
                print("[SESSION] [WARNING] Session component initialization had error")
                print("[SESSION] [INFO] Browser will be launched on-demand when _ensure_session_ready() is called")
                # DON'T disable use_real_purchasing - let _ensure_session_ready() try to launch browser

    def _trigger_circuit_breaker(self, reason: str):
        """Trigger circuit breaker to disable persistent sessions"""
        self.session_circuit_open = True
        self.use_real_purchasing = False
        print(f"[SESSION] [ALERT] CIRCUIT BREAKER TRIGGERED: {reason}")
        print(f"[SESSION] [LOCKED] Persistent sessions DISABLED after {self.session_failure_count} failures")
        print(f"[SESSION] [RETRY] Falling back to MOCK PURCHASING mode (simulated purchases)")
        print("[SESSION] [TIP] Dashboard will continue working, but purchases will be simulated")
        print("[SESSION] [TIP] To fix: Check browser/session issues and restart application")

        if self.status_callback:
            self.status_callback('system', 'circuit_breaker_open', {
                'reason': reason,
                'failure_count': self.session_failure_count,
                'fallback_mode': 'mock_purchasing',
                'message': 'Session system disabled - using mock mode'
            })

    def get_system_status(self) -> dict:
        """Get current system status for monitoring"""
        return {
            'feature_flags': self.feature_flags,
            'use_real_purchasing': self.use_real_purchasing,
            'session_initialized': self.session_initialized,
            'session_failure_count': self.session_failure_count,
            'circuit_breaker_open': self.session_circuit_open,
            'session_manager_available': self.session_manager is not None,
            'purchase_mode': 'persistent_session' if self.use_real_purchasing else 'mock_fallback'
        }

    async def _ensure_session_ready(self):
        """Ensure session is initialized and ready - GUARANTEES browser launch"""
        print(f"[SESSION_DEBUG] _ensure_session_ready() called")
        print(f"[SESSION_DEBUG] session_initialized={self.session_initialized}")
        print(f"[SESSION_DEBUG] session_manager={self.session_manager}")

        # CRITICAL FIX: Check if browser is ACTUALLY running, not just if components exist
        # Previous logic: if flag is True, return early (prevented browser launch)
        # New logic: if browser context exists, session is ready; otherwise launch browser
        if self.session_manager and hasattr(self.session_manager, 'browser'):
            print(f"[SESSION_DEBUG] session_manager.browser={self.session_manager.browser}")
            if self.session_manager.browser and self.session_manager.session_active:
                print("[SESSION] ✅ Browser already running - session ready")
                return True

        # If components exist but browser isn't running, FORCE initialization
        print("[SESSION] ⚡ Browser NOT running - launching now (GUARANTEED)...")
        self.session_initialized = False  # Reset to allow initialization

        try:
            print("[SESSION] ═══════════════════════════════════════════════")
            print("[SESSION] Starting session initialization...")
            print("[SESSION] ═══════════════════════════════════════════════")
            session_start_time = time.time()

            # PRE-CHECK: Validate session file exists before opening browser
            import json
            from pathlib import Path
            session_path = Path("target.json")

            print("[SESSION] [STEP 1/3] Checking session file...")
            if not session_path.exists():
                print("[SESSION] [WARNING] target.json not found - will attempt auto-login during initialization")
                # Don't return False - let SessionManager initialize and auto-login
            else:
                try:
                    with open(session_path, 'r') as f:
                        session_data = json.load(f)

                    # Check if cookies exist
                    if 'cookies' not in session_data or not session_data['cookies']:
                        print("[SESSION] [WARNING] target.json has no cookies - will attempt auto-login")
                    else:
                        print(f"[SESSION] [OK] ✅ Found {len(session_data['cookies'])} cookies in session file")

                except Exception as e:
                    print(f"[SESSION] [WARNING] Invalid target.json: {e} - will attempt auto-login")

            # Initialize session manager
            print("[SESSION] [STEP 2/3] ⚡ Initializing SessionManager (ultra-fast mode)...")
            print("[SESSION] ⚡ This will:")
            print("[SESSION] ⚡   - Launch Chromium browser (~2-3 seconds)")
            print("[SESSION] ⚡   - Skip initial navigation (on-demand only)")
            print("[SESSION] ⚡   - Be ready for purchases immediately")
            print("[SESSION] ⚡ Optimized for competitive bot speed...")

            init_start = time.time()
            if await self.session_manager.initialize():
                init_duration = time.time() - init_start
                print(f"[SESSION] [OK] ✅ SessionManager initialized in {init_duration:.1f}s")

                # DISABLED: Keep-alive not needed - stock monitoring maintains session activity
                # Stock monitor makes API calls every 15-22 seconds, which keeps session alive
                # Keep-alive was causing race conditions (navigating to /account during purchases)
                # self.session_keepalive.start()
                # print("[SESSION] [OK] Keep-alive service started")

                print("[SESSION] [STEP 3/3] Keep-alive disabled - stock API calls maintain session")

                self.session_initialized = True
                total_duration = time.time() - session_start_time
                print("[SESSION] ═══════════════════════════════════════════════")
                print(f"[SESSION] ✅ Session ready in {total_duration:.1f}s total")
                print("[SESSION] Browser is at Target.com and ready for purchases")
                print("[SESSION] ═══════════════════════════════════════════════")
                return True
            else:
                init_duration = time.time() - init_start
                print(f"[SESSION] [ERROR] ❌ SessionManager initialization failed after {init_duration:.1f}s")
                print("[SESSION] ═══════════════════════════════════════════════")
                return False

        except Exception as e:
            total_duration = time.time() - session_start_time
            print(f"[SESSION] [ERROR] ❌ Session initialization error after {total_duration:.1f}s: {e}")
            print("[SESSION] ═══════════════════════════════════════════════")
            import traceback
            traceback.print_exc()
            return False

    def check_and_complete_purchases(self):
        """Check and complete purchases - called during stock refresh cycles only"""
        current_time = time.time()
        print(f"[PURCHASE_DEBUG] Completion check running at timestamp {current_time:.3f}")

        with self._state_lock:
            states = self._load_states_unsafe()
            completed_purchases = []
            attempting_count = 0

            for tcin, state in states.items():
                if state.get('status') in ['attempting', 'queued']:
                    attempting_count += 1

                    # CRITICAL: Real purchases don't have completes_at - they complete when browser finishes
                    is_real_purchase = state.get('real_purchase', False)

                    if is_real_purchase:
                        # Real purchase - only check for timeout (no force-completion on timer)
                        started_time = state.get('started_at', 0)
                        elapsed_time = current_time - started_time

                        status = state.get('status')
                        print(f"[PURCHASE_DEBUG] {tcin} REAL purchase {status}: {elapsed_time:.1f}s elapsed (no timer)")

                        # Only force-fail real purchases if they've been running for more than 120 seconds
                        if elapsed_time > 120:
                            print(f"[PURCHASE_FORCE_COMPLETE] {tcin} REAL purchase timeout after {elapsed_time:.1f}s, forcing completion")
                            final_outcome = 'failed'
                            self._finalize_purchase_unsafe(tcin, state, final_outcome, states)
                            completed_purchases.append((tcin, final_outcome))
                    else:
                        # Mock purchase - has timer
                        complete_time = state.get('completes_at', 0)
                        started_time = state.get('started_at', 0)
                        time_remaining = complete_time - current_time
                        elapsed_time = current_time - started_time

                        print(f"[PURCHASE_DEBUG] {tcin} MOCK purchase attempting: {time_remaining:.1f}s remaining (started at {started_time:.3f}, completes at {complete_time:.3f})")

                        # Force completion if purchase is overdue by more than 30 seconds (safety mechanism)
                        if time_remaining < -30:
                            print(f"[PURCHASE_FORCE_COMPLETE] {tcin} is severely overdue ({time_remaining:.1f}s), forcing completion")
                            final_outcome = 'failed'  # Force failed if severely overdue
                            self._finalize_purchase_unsafe(tcin, state, final_outcome, states)
                            completed_purchases.append((tcin, final_outcome))
                        elif current_time >= complete_time:
                            # Normal completion
                            final_outcome = state.get('final_outcome', 'failed')
                            print(f"[PURCHASE_DEBUG] {tcin} COMPLETING with outcome: {final_outcome} (was attempting for {elapsed_time:.1f}s)")
                            self._finalize_purchase_unsafe(tcin, state, final_outcome, states)
                            completed_purchases.append((tcin, final_outcome))

            print(f"[PURCHASE_DEBUG] Completion check found {attempting_count} attempting purchases, completed {len(completed_purchases)}")

            if completed_purchases:
                print(f"[PURCHASE_DEBUG] Completed {len(completed_purchases)} purchases this cycle")

            return completed_purchases

    def get_completed_purchase_tcins(self):
        """Get list of TCINs that are in completed states (purchased/failed)"""
        with self._state_lock:
            states = self._load_states_unsafe()
            completed_tcins = []

            for tcin, state in states.items():
                if state.get('status') in ['purchased', 'failed']:
                    completed_tcins.append(tcin)

            return completed_tcins

    def reset_completed_purchases_to_ready(self):
        """Reset all completed purchases to ready state for next cycle"""
        with self._state_lock:
            # Check if running in TEST_MODE
            test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'
            mode_label = "[TEST_MODE]" if test_mode else "[PROD_MODE]"

            print(f"[PURCHASE_RESET_DEBUG] {mode_label} Starting reset operation at timestamp {time.time():.3f}")
            if test_mode:
                print(f"[PURCHASE_RESET_DEBUG] {mode_label} TEST MODE: Resetting ALL completed purchases for endless loop")
                print(f"[TEST_MODE_RESET] ════════════════════════════════════════════════")
                print(f"[TEST_MODE_RESET] ENDLESS LOOP: Resetting purchases to 'ready'")
                print(f"[TEST_MODE_RESET] This allows the same product to be purchased again")
                print(f"[TEST_MODE_RESET] ════════════════════════════════════════════════")

            # Load current states
            states = self._load_states_unsafe()
            print(f"[PURCHASE_RESET_DEBUG] Loaded {len(states)} total purchase states")

            # DIAGNOSTIC: Show ALL states for debugging
            if test_mode:
                print(f"[TEST_MODE_DIAGNOSTIC] ALL STATES BEFORE RESET:")
                for tcin, state in states.items():
                    status = state.get('status', 'unknown')
                    completed_at = state.get('completed_at', 'N/A')
                    started_at = state.get('started_at', 'N/A')
                    age = f"{time.time() - started_at:.1f}s ago" if isinstance(started_at, (int, float)) and started_at > 0 else "N/A"
                    print(f"[TEST_MODE_DIAGNOSTIC]   {tcin}: status='{status}', started={age}, completed={completed_at}")

            # Log current state before reset
            completed_states = {tcin: state for tcin, state in states.items() if state.get('status') in ['purchased', 'failed']}
            print(f"[PURCHASE_RESET_DEBUG] Found {len(completed_states)} completed purchases to reset:")
            for tcin, state in completed_states.items():
                print(f"[PURCHASE_RESET_DEBUG]   {tcin}: {state.get('status')} (completed at: {state.get('completed_at', 'unknown')})")

            reset_count = 0
            reset_details = []

            for tcin, state in states.items():
                current_status = state.get('status')

                # Reset any completed states (purchased or failed)
                if current_status in ['purchased', 'failed']:
                    old_order = state.get('order_number', 'N/A')
                    old_completed_at = state.get('completed_at', 'unknown')

                    # Reset to ready state
                    states[tcin] = {'status': 'ready'}
                    reset_count += 1

                    reset_info = {
                        'tcin': tcin,
                        'old_status': current_status,
                        'order_number': old_order,
                        'completed_at': old_completed_at
                    }
                    reset_details.append(reset_info)

                    print(f"[PURCHASE_RESET_DEBUG] {tcin}: {current_status} -> ready (was order: {old_order})")
                    if test_mode:
                        print(f"[TEST_MODE_RESET] ✅ {tcin}: '{current_status}' → 'ready' (will re-attempt next cycle)")

                # ALSO reset very old attempting states (older than 60 seconds) as they're likely stuck
                elif current_status == 'attempting':
                    started_at = state.get('started_at', 0)
                    if started_at and (time.time() - started_at) > 60:  # Older than 60 seconds
                        states[tcin] = {'status': 'ready'}
                        reset_count += 1

                        reset_info = {
                            'tcin': tcin,
                            'old_status': current_status,
                            'order_number': 'N/A',
                            'completed_at': 'stuck_timeout'
                        }
                        reset_details.append(reset_info)

                        print(f"[PURCHASE_RESET_DEBUG] {tcin}: {current_status} -> ready (stuck timeout after 60s)")

                # ALSO reset very old queued states (older than 5 seconds) as they're likely stuck
                # This handles the bug where status wasn't saved before purchase execution
                elif current_status == 'queued':
                    started_at = state.get('started_at', 0)
                    if started_at and (time.time() - started_at) > 5:  # Older than 5 seconds (reduced from 30)
                        states[tcin] = {'status': 'ready'}
                        reset_count += 1

                        reset_info = {
                            'tcin': tcin,
                            'old_status': current_status,
                            'order_number': 'N/A',
                            'completed_at': 'stuck_queued'
                        }
                        reset_details.append(reset_info)

                        print(f"[PURCHASE_RESET_DEBUG] {tcin}: {current_status} -> ready (stuck in queued after 5s)")
                        if test_mode:
                            print(f"[TEST_MODE_RESET] ✅ {tcin}: 'queued' → 'ready' (stuck state cleared)")

            # Save states if any resets occurred
            if reset_count > 0:
                print(f"[PURCHASE_RESET_DEBUG] Saving {reset_count} resets to file...")
                save_success = self._save_states_unsafe(states)

                if save_success:
                    print(f"[PURCHASE_RESET_DEBUG] Successfully saved {reset_count} resets to file")
                    if test_mode and reset_count > 0:
                        print(f"[TEST_MODE_RESET] ════════════════════════════════════════════════")
                        print(f"[TEST_MODE_RESET] ✅ Reset complete: {reset_count} purchases → 'ready'")
                        print(f"[TEST_MODE_RESET] Next cycle will re-attempt these products if in stock")
                        print(f"[TEST_MODE_RESET] ════════════════════════════════════════════════")

                    # Verify the save by re-loading and checking
                    verification_states = self._load_states_unsafe()
                    verification_errors = []

                    for reset_info in reset_details:
                        tcin = reset_info['tcin']
                        if tcin in verification_states:
                            actual_status = verification_states[tcin].get('status')
                            if actual_status != 'ready':
                                verification_errors.append(f"{tcin} expected 'ready' but got '{actual_status}'")
                        else:
                            verification_errors.append(f"{tcin} missing from saved states")

                    if verification_errors:
                        print(f"[PURCHASE_RESET_ERROR] Verification failed after save:")
                        for error in verification_errors:
                            print(f"[PURCHASE_RESET_ERROR]   {error}")
                    else:
                        print(f"[PURCHASE_RESET_DEBUG] Verification passed - all {reset_count} resets confirmed in file")

                else:
                    print(f"[PURCHASE_RESET_ERROR] Failed to save resets to file!")

                print(f"[PURCHASE] {mode_label} Reset {reset_count} completed purchases to ready state")
                if test_mode:
                    print(f"[PURCHASE] {mode_label} ✅ Purchases reset - ready for next endless loop iteration")
            else:
                print(f"[PURCHASE_RESET_DEBUG] No completed purchases found to reset")

            print(f"[PURCHASE_RESET_DEBUG] {mode_label} Reset operation completed at timestamp {time.time():.3f}")
            if test_mode and reset_count > 0:
                print(f"[PURCHASE] {mode_label} ✅ TEST MODE LOOP: {reset_count} purchases ready to restart")
            return reset_count

    def reset_completed_purchases_by_stock_status(self, stock_data):
        """Stock-aware reset: only reset completed purchases for products that are OUT OF STOCK"""
        with self._state_lock:
            print(f"[STOCK_AWARE_RESET_DEBUG] Starting stock-aware reset operation at timestamp {time.time():.3f}")

            # Load current states
            states = self._load_states_unsafe()
            print(f"[STOCK_AWARE_RESET_DEBUG] Loaded {len(states)} total purchase states")

            # Create stock status lookup (handle different stock_data formats)
            stock_status = {}

            print(f"[STOCK_AWARE_RESET_DEBUG] Raw stock_data type: {type(stock_data)}")
            print(f"[STOCK_AWARE_RESET_DEBUG] Raw stock_data (first 200 chars): {str(stock_data)[:200]}")

            try:
                # Handle different possible formats of stock_data
                if isinstance(stock_data, dict):
                    # If it's a dict, convert to list format
                    products = []
                    for tcin, data in stock_data.items():
                        if isinstance(data, dict):
                            product = {'tcin': tcin, 'in_stock': data.get('in_stock', False)}
                        else:
                            product = {'tcin': tcin, 'in_stock': bool(data)}
                        products.append(product)
                    stock_data = products
                elif isinstance(stock_data, list):
                    # Already in expected format
                    products = stock_data
                else:
                    print(f"[STOCK_AWARE_RESET_ERROR] Unexpected stock_data format: {type(stock_data)}")
                    return 0

                for product in products:
                    if isinstance(product, dict):
                        tcin = product.get('tcin')
                        in_stock = product.get('in_stock', False)
                        stock_status[tcin] = in_stock
                    else:
                        print(f"[STOCK_AWARE_RESET_ERROR] Unexpected product format: {type(product)} - {product}")

                print(f"[STOCK_AWARE_RESET_DEBUG] Stock status for {len(stock_status)} products: {stock_status}")

            except Exception as e:
                print(f"[STOCK_AWARE_RESET_ERROR] Error processing stock_data: {e}")
                return 0

            # Find completed purchases that should be reset (only for OUT OF STOCK products)
            completed_states = {tcin: state for tcin, state in states.items() if state.get('status') in ['purchased', 'failed']}
            out_of_stock_completed = {}

            for tcin, state in completed_states.items():
                is_in_stock = stock_status.get(tcin, False)
                if not is_in_stock:  # Product is OUT OF STOCK
                    out_of_stock_completed[tcin] = state

            print(f"[STOCK_AWARE_RESET_DEBUG] Found {len(out_of_stock_completed)} completed purchases for OUT OF STOCK products to reset:")
            for tcin, state in out_of_stock_completed.items():
                in_stock_status = "IN STOCK" if stock_status.get(tcin, False) else "OUT OF STOCK"
                print(f"[STOCK_AWARE_RESET_DEBUG]   {tcin}: {state.get('status')} -> reset to ready ({in_stock_status})")

            reset_count = 0
            reset_details = []

            for tcin, state in states.items():
                current_status = state.get('status')

                # CORRECTED LOGIC: Reset based on status and stock combination
                if current_status in ['purchased', 'failed']:
                    is_in_stock = stock_status.get(tcin, False)

                    if is_in_stock:  # Product is IN STOCK
                        # IN STOCK + COMPLETED (purchased or failed) -> Reset to ready for immediate re-purchase
                        old_order = state.get('order_number', 'N/A')
                        old_failure = state.get('failure_reason', 'unknown')
                        old_completed_at = state.get('completed_at', 'unknown')

                        states[tcin] = {'status': 'ready'}
                        reset_count += 1

                        reset_info = {
                            'tcin': tcin,
                            'old_status': current_status,
                            'order_number': old_order if current_status == 'purchased' else 'N/A',
                            'completed_at': old_completed_at,
                            'reason': 'in_stock_repeat_purchase'
                        }
                        reset_details.append(reset_info)

                        if current_status == 'purchased':
                            print(f"[STOCK_AWARE_RESET_DEBUG] {tcin}: {current_status} -> ready (IN STOCK, repeat purchase - was order: {old_order})")
                        else:
                            print(f"[STOCK_AWARE_RESET_DEBUG] {tcin}: {current_status} -> ready (IN STOCK + FAILED, retry - was: {old_failure})")
                    else:
                        # Product is OUT OF STOCK - reset everything to ready for next cycle
                        old_order = state.get('order_number', 'N/A')
                        old_completed_at = state.get('completed_at', 'unknown')

                        # Reset to ready state
                        states[tcin] = {'status': 'ready'}
                        reset_count += 1

                        reset_info = {
                            'tcin': tcin,
                            'old_status': current_status,
                            'order_number': old_order,
                            'completed_at': old_completed_at,
                            'reason': 'out_of_stock'
                        }
                        reset_details.append(reset_info)

                        print(f"[STOCK_AWARE_RESET_DEBUG] {tcin}: {current_status} -> ready (OUT OF STOCK, was order: {old_order})")

                # Also reset very old attempting states (older than 60 seconds) regardless of stock
                elif current_status == 'attempting':
                    started_at = state.get('started_at', 0)
                    if started_at and (time.time() - started_at) > 60:  # Older than 60 seconds
                        states[tcin] = {'status': 'ready'}
                        reset_count += 1

                        reset_info = {
                            'tcin': tcin,
                            'old_status': current_status,
                            'order_number': 'N/A',
                            'completed_at': 'stuck_timeout',
                            'reason': 'timeout'
                        }
                        reset_details.append(reset_info)

                        print(f"[STOCK_AWARE_RESET_DEBUG] {tcin}: {current_status} -> ready (stuck timeout after 60s)")

            # Save states if any resets occurred
            if reset_count > 0:
                print(f"[STOCK_AWARE_RESET_DEBUG] Saving {reset_count} resets to file...")
                save_success = self._save_states_unsafe(states)

                if save_success:
                    print(f"[STOCK_AWARE_RESET_DEBUG] Successfully saved {reset_count} resets to file")

                    # Verify the save by re-loading and checking
                    verification_states = self._load_states_unsafe()
                    verification_errors = []

                    for reset_info in reset_details:
                        tcin = reset_info['tcin']
                        if tcin in verification_states:
                            actual_status = verification_states[tcin].get('status')
                            if actual_status != 'ready':
                                verification_errors.append(f"{tcin} expected 'ready' but got '{actual_status}'")
                        else:
                            verification_errors.append(f"{tcin} missing from saved states")

                    if verification_errors:
                        print(f"[STOCK_AWARE_RESET_ERROR] Verification failed after save:")
                        for error in verification_errors:
                            print(f"[STOCK_AWARE_RESET_ERROR]   {error}")
                    else:
                        print(f"[STOCK_AWARE_RESET_DEBUG] Verification passed - all {reset_count} resets confirmed in file")

                else:
                    print(f"[STOCK_AWARE_RESET_ERROR] Failed to save resets to file!")

                print(f"[PURCHASE] Stock-aware reset: {reset_count} completed purchases reset to ready (OUT OF STOCK only)")
            else:
                print(f"[STOCK_AWARE_RESET_DEBUG] No completed purchases for OUT OF STOCK products found to reset")

            print(f"[STOCK_AWARE_RESET_DEBUG] Stock-aware reset operation completed at timestamp {time.time():.3f}")
            return reset_count

    def _acquire_file_lock(self):
        """Acquire exclusive file lock to prevent corruption"""
        if HAS_MSVCRT:
            # Windows file locking using msvcrt
            try:
                lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_TRUNC | os.O_RDWR)
                msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                return lock_fd
            except (OSError, IOError):
                return None
        elif HAS_FCNTL:
            # Unix/Linux file locking using fcntl
            try:
                lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_TRUNC | os.O_RDWR)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock_fd
            except (OSError, IOError):
                return None
        else:
            # Fallback - should not reach here
            return True

    def _release_file_lock(self, lock_fd):
        """Release file lock"""
        if HAS_MSVCRT:
            # Windows file unlocking using msvcrt
            try:
                if lock_fd:
                    msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    os.close(lock_fd)
            except:
                pass
        elif HAS_FCNTL:
            # Unix/Linux file unlocking using fcntl
            try:
                if lock_fd:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
            except:
                pass

    def _load_states_unsafe(self) -> Dict:
        """Load states without external locking (assumes caller has lock)"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    # Validate data structure
                    if isinstance(data, dict):
                        return data
                    else:
                        print(f"[PURCHASE] Invalid state file format, resetting")
                        return {}
            else:
                return {}
        except (json.JSONDecodeError, IOError) as e:
            print(f"[PURCHASE] Failed to load states: {e}, resetting")
            return {}

    def _save_states_unsafe(self, states: Dict):
        """Atomic save states without external locking (assumes caller has lock)"""
        try:
            # Atomic write using temp file
            temp_file = f"{self.state_file}.tmp.{os.getpid()}"
            with open(temp_file, 'w') as f:
                json.dump(states, f, indent=2)

            # Atomic rename
            if os.path.exists(self.state_file):
                os.remove(self.state_file)
            os.rename(temp_file, self.state_file)
            return True

        except Exception as e:
            print(f"[PURCHASE] Failed to save states: {e}")
            # Clean up temp file
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
            return False

    def load_states(self) -> Dict:
        """Thread-safe load states with file locking"""
        with self._file_lock:
            lock_fd = self._acquire_file_lock()
            try:
                return self._load_states_unsafe()
            finally:
                self._release_file_lock(lock_fd)

    def save_states(self, states: Dict) -> bool:
        """Thread-safe save states with file locking"""
        with self._file_lock:
            lock_fd = self._acquire_file_lock()
            try:
                return self._save_states_unsafe(states)
            finally:
                self._release_file_lock(lock_fd)

    def get_purchase_status(self, tcin: str) -> str:
        """Get current purchase status for a TCIN (thread-safe)"""
        with self._state_lock:
            states = self._load_states_unsafe()
            state = states.get(tcin, {'status': 'ready'})
            return state.get('status', 'ready')

    def can_start_purchase(self, tcin: str) -> bool:
        """Check if a purchase can be started for this TCIN (thread-safe)"""
        return self.get_purchase_status(tcin) == 'ready'

    def start_purchase(self, tcin: str, product_title: str) -> Dict:
        """Start a new purchase attempt with duplicate prevention (assumes caller has lock)"""
        states = self._load_states_unsafe()
        current_state = states.get(tcin, {'status': 'ready'})

        # RACE CONDITION FIX: Check for ANY active purchase (not just this TCIN)
        active_purchase_tcin = None
        for check_tcin, check_state in states.items():
            if check_state.get('status') in ['attempting', 'queued']:
                active_purchase_tcin = check_tcin
                break

        if active_purchase_tcin:
            if active_purchase_tcin == tcin:
                print(f"[PURCHASE] PREVENTED DUPLICATE: {tcin} already attempting")
                return {'success': False, 'reason': 'already_attempting'}
            else:
                print(f"[PURCHASE] PREVENTED CONCURRENT: {tcin} blocked, {active_purchase_tcin} already active")
                return {'success': False, 'reason': 'another_purchase_active', 'active_tcin': active_purchase_tcin}

        # Use real purchasing if enabled (fallback to mock if needed)
        print("=" * 80)
        print(f"[PURCHASE_TRIGGER] Product IN STOCK: {product_title} (TCIN: {tcin})")
        print(f"[PURCHASE_MODE_DEBUG] use_real_purchasing={self.use_real_purchasing}")
        print(f"[PURCHASE_MODE_DEBUG] session_initialized={self.session_initialized}")
        print(f"[PURCHASE_MODE_DEBUG] session_manager={self.session_manager}")
        print(f"[PURCHASE_MODE_DEBUG] purchase_executor={self.purchase_executor}")
        print("=" * 80)

        if self.use_real_purchasing:
            # Force real purchase even if session validation failed
            # (session may be healthy but validation too strict)
            print(f"[PURCHASE_MODE] ✅ [REAL] Using REAL browser automation for {tcin}")
            print(f"[PURCHASE_MODE] ✅ Browser should open and begin checkout process...")
            return self._start_real_purchase(tcin, product_title, states)
        else:
            # Mock mode available for testing without browser
            print(f"[PURCHASE_MODE] ⚠️  [MOCK] Using MOCK mode for {tcin}")
            print(f"[PURCHASE_MODE] ⚠️  To enable browser: set use_real_purchasing=True")
            return self._start_mock_purchase(tcin, product_title, states)

    def _start_real_purchase(self, tcin: str, product_title: str, states: Dict) -> Dict:
        """Start real purchase using PurchaseExecutor"""
        now = time.time()

        # Create initial state - NO TIMER for real purchases
        # FIX: Purchase starts as "attempting" immediately (not "queued")
        # This prevents dashboard showing stale status during session wait
        new_state = {
            'status': 'attempting',  # Changed from 'queued' to 'attempting'
            'tcin': tcin,
            'product_title': product_title,
            'started_at': now,
            # NO completes_at - real purchase completes when browser finishes
            'final_outcome': 'unknown',  # Will be determined by real purchase
            'attempt_count': 1,
            'real_purchase': True
        }

        # Save state immediately
        states[tcin] = new_state
        self._save_states_unsafe(states)

        print(f"[PURCHASE] Starting REAL purchase: {product_title} (TCIN: {tcin}) - status: attempting")

        # Notify callback immediately with 'attempting' status
        if self.status_callback:
            self.status_callback(tcin, 'attempting', new_state)
            print(f"[PURCHASE] Dashboard notified immediately: {tcin} -> attempting")

        # Start real purchase in background
        def execute_real_purchase():
            try:
                print(f"[REAL_PURCHASE_THREAD] [INIT] Starting async purchase execution for {tcin}")
                print(f"[REAL_PURCHASE_THREAD] purchase_executor: {self.purchase_executor}")
                print(f"[REAL_PURCHASE_THREAD] session_initialized: {self.session_initialized}")

                # CRITICAL: Wait for session to be ready (don't initialize new one!)
                if not self.session_initialized:
                    print(f"[REAL_PURCHASE_THREAD] Waiting for session initialization to complete...")
                    # Wait up to 60 seconds for main thread to finish initializing session
                    # Poll every 0.2s for faster response (was 1s)
                    max_wait = 60
                    polls = 0
                    max_polls = max_wait * 5  # 5 polls per second
                    while polls < max_polls:
                        if self.session_initialized:
                            print(f"[REAL_PURCHASE_THREAD] [OK] Session ready after {polls * 0.2:.1f}s wait")
                            break
                        # Show progress every 50 polls (10 seconds)
                        if polls > 0 and polls % 50 == 0:
                            print(f"[REAL_PURCHASE_THREAD] Still waiting for session... ({polls * 0.2:.1f}s elapsed)")
                        time.sleep(0.2)
                        polls += 1

                    if not self.session_initialized:
                        print(f"[REAL_PURCHASE_THREAD] [ERROR] Session initialization timeout after {polls * 0.2:.1f}s")
                        failed_result = {
                            'success': False,
                            'tcin': tcin,
                            'reason': 'session_init_timeout',
                            'error': f'Session not ready after {max_wait}s wait'
                        }
                        self._update_purchase_result(tcin, failed_result)
                        return

                # Session is ready, proceed with purchase using subprocess (bypasses asyncio threading issues)
                print(f"[REAL_PURCHASE_THREAD] Session ready - NOW starting actual purchase execution...")

                # BUGFIX: Set purchase lock to prevent session validation during purchase
                if self.session_manager:
                    self.session_manager.set_purchase_in_progress(True)

                # CRITICAL: Register thread in active purchases tracking
                # This prevents race condition where next cycle starts before state is saved
                with self._state_lock:
                    self._active_purchases[tcin] = {
                        'thread': threading.current_thread(),
                        'started_at': time.time(),
                        'status': 'executing'
                    }
                    print(f"[REAL_PURCHASE_THREAD] Registered thread in active purchases: {tcin}")

                # NOTE: Status already set to 'attempting' when purchase was queued
                # No need to update again here - prevents race condition with dashboard

                # Use existing PurchaseExecutor with thread-safe async execution
                print(f"[REAL_PURCHASE_THREAD] Executing purchase using existing session...")

                try:
                    # Use submit_async_task to safely call async method from thread
                    future = self.session_manager.submit_async_task(
                        self.purchase_executor.execute_purchase(tcin)
                    )

                    # Wait for result with timeout
                    result = future.result(timeout=60)

                    print(f"[REAL_PURCHASE_THREAD] [OK] Purchase execution completed: {result}")

                    # CRITICAL: Update state ATOMICALLY with lock held
                    # This prevents race condition where next cycle sees stale "attempting" status
                    with self._state_lock:
                        # Mark as completing to block new cycles
                        if tcin in self._active_purchases:
                            self._active_purchases[tcin]['status'] = 'completing'
                            print(f"[REAL_PURCHASE_THREAD] Marked thread as completing: {tcin}")

                        # Update state file immediately
                        self._update_purchase_result(tcin, result)

                        print(f"[REAL_PURCHASE_THREAD] ✅ State updated atomically, safe for next cycle")

                except TimeoutError:
                    print(f"[REAL_PURCHASE_THREAD] [ERROR] Purchase execution timed out after 60s")
                    failed_result = {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'execution_timeout',
                        'error': 'Purchase execution timed out'
                    }
                    self._update_purchase_result(tcin, failed_result)
                    return

                except Exception as e:
                    print(f"[REAL_PURCHASE_THREAD] [ERROR] Purchase execution failed: {e}")
                    raise

            except Exception as e:
                print(f"[PURCHASE] [ERROR] Real purchase failed for {tcin}: {e}")
                import traceback
                traceback.print_exc()
                # Mark as failed
                failed_result = {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'execution_error',
                    'error': str(e)
                }
                self._update_purchase_result(tcin, failed_result)

            finally:
                # BUGFIX: Always clear purchase lock when purchase completes (success or failure)
                if self.session_manager:
                    self.session_manager.set_purchase_in_progress(False)

                # CRITICAL: Remove from active purchases tracking
                # This signals to next cycle that thread has completed
                with self._state_lock:
                    if tcin in self._active_purchases:
                        print(f"[REAL_PURCHASE_THREAD] Removing {tcin} from active purchases")
                        del self._active_purchases[tcin]
                    else:
                        print(f"[REAL_PURCHASE_THREAD] Note: {tcin} already removed from active purchases")

        # Start purchase thread
        import threading
        purchase_thread = threading.Thread(target=execute_real_purchase, daemon=True)
        purchase_thread.start()

        return {
            'success': True,
            'tcin': tcin,
            'duration': 60,  # Max expected duration
            'status': 'attempting',
            'real_purchase': True
        }

    def _start_mock_purchase(self, tcin: str, product_title: str, states: Dict) -> Dict:
        """Start mock purchase (fallback when real purchasing not available)"""
        # Random timing from specific values (5, 15, 20, 25 seconds) and 70% success rate
        duration = random.choice([5, 15, 20, 25])
        will_succeed = random.random() < self.config['success_rate']

        now = time.time()
        new_state = {
            'status': 'attempting',
            'tcin': tcin,
            'product_title': product_title,
            'started_at': now,
            'completes_at': now + duration,
            'final_outcome': 'purchased' if will_succeed else 'failed',
            'order_number': f"ORD-{random.randint(100000, 999999)}-{random.randint(10, 99)}" if will_succeed else None,
            'price': round(random.uniform(15.99, 89.99), 2) if will_succeed else None,
            'failure_reason': random.choice([
                'out_of_stock', 'payment_failed', 'cart_timeout',
                'captcha_required', 'price_changed', 'shipping_unavailable'
            ]) if not will_succeed else None,
            'attempt_count': 1,
            'real_purchase': False
        }

        # Save state
        states[tcin] = new_state
        self._save_states_unsafe(states)

        print(f"[PURCHASE] Started MOCK purchase: {product_title} (TCIN: {tcin}) - duration: {duration:.1f}s")
        print(f"[PURCHASE_DEBUG] {tcin} STARTING at timestamp {now:.3f}, will complete at {new_state['completes_at']:.3f}, expected outcome: {new_state['final_outcome']}")

        # Notify real-time updates
        if self.status_callback:
            self.status_callback(tcin, 'attempting', new_state)

        return {
            'success': True,
            'tcin': tcin,
            'duration': duration,
            'will_succeed': will_succeed,
            'status': 'attempting'
        }

    def _update_purchase_result(self, tcin: str, result: Dict):
        """Update purchase state with real purchase result"""
        with self._state_lock:
            states = self._load_states_unsafe()
            current_state = states.get(tcin, {})

            if current_state.get('status') != 'attempting':
                print(f"[PURCHASE] Warning: {tcin} not in attempting state, ignoring result")
                return

            now = time.time()

            if result['success']:
                # Purchase successful
                final_state = {
                    **current_state,
                    'status': 'purchased',
                    'completed_at': now,
                    'final_outcome': 'purchased',
                    'execution_time': result.get('execution_time', 0),
                    'order_number': f"REAL-{random.randint(100000, 999999)}",  # Placeholder
                    'price': round(random.uniform(15.99, 89.99), 2)  # Placeholder
                }

                print(f"[PURCHASE]  REAL purchase completed: {tcin}")

                if self.status_callback:
                    self.status_callback(tcin, 'purchased', final_state)

            else:
                # Purchase failed
                final_state = {
                    **current_state,
                    'status': 'failed',
                    'completed_at': now,
                    'final_outcome': 'failed',
                    'execution_time': result.get('execution_time', 0),
                    'failure_reason': result.get('reason', 'unknown_error'),
                    'error_details': result.get('error', '')
                }

                print(f"[PURCHASE]  REAL purchase failed: {tcin} - {result.get('reason', 'unknown')}")

                if self.status_callback:
                    self.status_callback(tcin, 'failed', final_state)

            states[tcin] = final_state
            self._save_states_unsafe(states)

    def _finalize_purchase_unsafe(self, tcin: str, state: Dict, final_outcome: str, states: Dict):
        """Finalize a completed purchase (assumes caller has lock)"""
        now = time.time()

        # Update state with completion
        state.update({
            'status': final_outcome,
            'completed_at': now
        })

        states[tcin] = state
        self._save_states_unsafe(states)

        product_title = state.get('product_title', f'Product {tcin}')

        if final_outcome == 'purchased':
            print(f"[PURCHASE] Purchase successful: {product_title} - Order: {state.get('order_number')}")
        else:
            print(f"[PURCHASE] Purchase failed: {product_title} - Reason: {state.get('failure_reason')}")

        # Notify real-time updates
        if self.status_callback:
            self.status_callback(tcin, final_outcome, state)

    def _reset_to_ready_unsafe(self, tcin: str, states: Dict):
        """Reset purchase state to ready after cooldown (assumes caller has lock)"""
        if tcin in states:
            old_status = states[tcin].get('status', 'unknown')
            states[tcin] = {'status': 'ready'}
            self._save_states_unsafe(states)
            print(f"[PURCHASE] Reset {tcin} from {old_status} to ready (cooldown complete)")

            # Notify real-time updates
            if self.status_callback:
                self.status_callback(tcin, 'ready', {'status': 'ready'})

    def get_all_states(self) -> Dict:
        """Get all purchase states for dashboard display (thread-safe)"""
        with self._state_lock:
            states = self._load_states_unsafe()
            result = {}

            for tcin, state in states.items():
                result[tcin] = {
                    'status': state.get('status', 'ready'),
                    'attempt_count': state.get('attempt_count', 0),
                    'order_number': state.get('order_number'),
                    'failure_reason': state.get('failure_reason'),
                    'last_attempt': state.get('started_at'),
                    'completed_at': state.get('completed_at'),
                    'completes_at': state.get('completes_at'),  # For countdown
                    'product_title': state.get('product_title')
                }

            return result

    def process_stock_data(self, stock_data: Dict) -> list:
        """Process stock data with CRITICAL STATE RULES - atomic transitions only"""
        results = []

        with self._state_lock:
            states = self._load_states_unsafe()
            print(f"[PURCHASE_PROCESS_DEBUG] Starting stock data processing with {len(stock_data)} products")

            # Initialize states for any new TCINs that don't exist yet
            states_modified = False
            for tcin in stock_data.keys():
                if tcin not in states:
                    states[tcin] = {'status': 'ready'}
                    states_modified = True
                    print(f"[PURCHASE] Initialized new TCIN {tcin} with ready status")

            # Save states if we added new TCINs
            if states_modified:
                self._save_states_unsafe(states)

            # DEFENSIVE PROGRAMMING: Check for any "purchased" or "failed" states that should have been reset
            stuck_completed_states = []
            for tcin, state in states.items():
                status = state.get('status')
                if status in ['purchased', 'failed']:
                    completed_at = state.get('completed_at', 0)
                    time_since_completion = time.time() - completed_at if completed_at else float('inf')

                    # If purchase completed more than 30 seconds ago, it should have been reset by now
                    if time_since_completion > 30:
                        stuck_completed_states.append({
                            'tcin': tcin,
                            'status': status,
                            'completed_at': completed_at,
                            'time_since_completion': time_since_completion
                        })

            if stuck_completed_states:
                print(f"[PURCHASE_PROCESS_ERROR] Found {len(stuck_completed_states)} stuck completed states that should have been reset:")
                for stuck in stuck_completed_states:
                    print(f"[PURCHASE_PROCESS_ERROR]   {stuck['tcin']}: {stuck['status']} (completed {stuck['time_since_completion']:.1f}s ago)")

                # FORCE RESET these stuck states as a safety mechanism
                print(f"[PURCHASE_PROCESS_FORCE] Force-resetting {len(stuck_completed_states)} stuck states...")
                for stuck in stuck_completed_states:
                    tcin = stuck['tcin']
                    old_status = stuck['status']
                    states[tcin] = {'status': 'ready'}
                    print(f"[PURCHASE_PROCESS_FORCE] Force-reset {tcin}: {old_status} -> ready")

                self._save_states_unsafe(states)
                print(f"[PURCHASE_PROCESS_FORCE] Force-reset completed and saved")

            # BUG FIX #1: Check for active purchases - enforce single purchase at a time
            # BUT also check if active purchase is stuck and force-reset if needed
            active_purchase = None
            for tcin, state in states.items():
                if state.get('status') in ['attempting', 'queued']:
                    started_at = state.get('started_at', 0)
                    elapsed = time.time() - started_at if started_at else 0

                    # If active purchase is stuck (>60s), force-reset and allow new purchase
                    if elapsed > 60:
                        print(f"[PURCHASE_CONCURRENCY] ⚠️ Force-resetting stuck purchase: {tcin} ({elapsed:.1f}s in status '{state.get('status')}')")
                        states[tcin] = {'status': 'ready'}
                        self._save_states_unsafe(states)
                        print(f"[PURCHASE_CONCURRENCY] ✅ Stuck purchase reset, continuing to check for new purchases...")
                        # Don't set active_purchase - allow new purchase to start
                    else:
                        active_purchase = tcin
                        print(f"[PURCHASE_CONCURRENCY] Active purchase detected: {tcin} (status: {state.get('status')}, running {elapsed:.1f}s)")
                        break

            # CRITICAL: Also check RUNTIME state (background threads)
            # Prevents race condition where thread is completing but file status not yet updated
            if not active_purchase:
                # DEADLOCK FIX: Don't re-acquire lock - we're already inside self._state_lock from line 1220
                if self._active_purchases:
                    active_tcin = list(self._active_purchases.keys())[0]
                    thread_info = self._active_purchases[active_tcin]
                    elapsed = time.time() - thread_info['started_at']
                    active_purchase = active_tcin
                    print(f"[PURCHASE_CONCURRENCY] Background thread still active: {active_purchase} (running {elapsed:.1f}s, status: {thread_info['status']})")

                    # RACE CONDITION FIX: If thread is marked as 'completing' (in cleanup phase),
                    # poll until it finishes before starting new purchase (cart clearing takes 20-30s)
                    if thread_info.get('status') == 'completing':
                        print(f"[PURCHASE_CONCURRENCY] Thread is completing (cleanup phase) - waiting up to 30s...")

                        max_wait = 30.0  # 30 second maximum
                        poll_interval = 0.5  # Check every 0.5 seconds (was 2s)
                        waited = 0.0

                        while waited < max_wait:
                            time.sleep(poll_interval)
                            waited += poll_interval

                            # Check if thread has completed
                            if active_tcin not in self._active_purchases:
                                print(f"[PURCHASE_CONCURRENCY] Thread completed after {waited:.1f}s - safe to start new purchase")
                                active_purchase = None  # Clear flag to allow new purchase
                                break

                            print(f"[PURCHASE_CONCURRENCY] Still waiting for cleanup... ({waited:.1f}s / {max_wait}s)")
                        else:
                            # Timeout - thread still active after 30s
                            elapsed_total = time.time() - thread_info['started_at']
                            print(f"[PURCHASE_CONCURRENCY] Thread still active after {max_wait}s wait ({elapsed_total:.1f}s total) - will skip new purchases this cycle")
                            # Keep active_purchase set to block new purchases

            # BUG FIX #2: Load product priority order from config
            try:
                import json
                config_path = 'config/product_config.json'
                with open(config_path, 'r') as f:
                    config = json.load(f)
                # Config format: {"products": [{"tcin": "...", "name": "..."}, ...]}
                products_list = config.get('products', [])
                product_priority_order = [p['tcin'] for p in products_list if 'tcin' in p]
                print(f"[PURCHASE_PRIORITY] Loaded priority order: {len(product_priority_order)} products")
            except Exception as e:
                print(f"[PURCHASE_PRIORITY_ERROR] Could not load product priority: {e}")
                import traceback
                traceback.print_exc()
                # Fallback to stock_data order
                product_priority_order = list(stock_data.keys())

            # BUG FIX #2: Sort stock_data by priority order
            def get_priority_index(tcin):
                try:
                    return product_priority_order.index(tcin)
                except ValueError:
                    return 999999  # Unknown products go to end

            sorted_tcins = sorted(stock_data.keys(), key=get_priority_index)
            print(f"[PURCHASE_PRIORITY] Processing {len(sorted_tcins)} products in priority order")

            # Process each product according to state rules (in priority order)
            for tcin in sorted_tcins:
                product_data = stock_data[tcin]
                current_state = states.get(tcin, {'status': 'ready'})
                current_status = current_state.get('status', 'ready')

                print(f"[PURCHASE_PROCESS_DEBUG] {tcin}: stock={product_data.get('in_stock')}, status={current_status}")

                if product_data.get('in_stock'):
                    # CRITICAL STATE RULE: IN STOCK + ready -> IMMEDIATELY go to "attempting"
                    if current_status == 'ready':
                        # BUG FIX #1: Only start if no active purchase
                        if active_purchase:
                            print(f"[PURCHASE_CONCURRENCY] Skipping {tcin} - purchase already active for {active_purchase}")
                            continue

                        # Start new purchase attempt
                        result = self.start_purchase(tcin, product_data.get('title', f'Product {tcin}'))
                        if result.get('success'):
                            # Mark as active to prevent other purchases this cycle
                            active_purchase = tcin

                            # Reload states to get updated completion info
                            updated_states = self._load_states_unsafe()
                            state = updated_states.get(tcin, {})
                            results.append({
                                'tcin': tcin,
                                'action': 'purchase_started',
                                'title': product_data.get('title'),
                                'duration': result.get('duration', 'unknown'),
                                'completes_at': state.get('completes_at'),
                                'final_outcome': state.get('final_outcome')
                            })
                            print(f"[PURCHASE] CRITICAL RULE: {tcin} IN STOCK + ready -> attempting ({result.get('duration', 'unknown')}s)")
                        else:
                            print(f"[PURCHASE_ERROR] Failed to start purchase for {tcin}: {result}")
                    elif current_status in ['purchased', 'failed']:
                        # This should NOT happen if reset worked properly - this is an error condition
                        print(f"[PURCHASE_ERROR] {tcin} is IN STOCK but still has completed status '{current_status}' - reset failed!")
                        # Emergency force reset
                        states[tcin] = {'status': 'ready'}
                        self._save_states_unsafe(states)
                        print(f"[PURCHASE_EMERGENCY] Emergency reset {tcin} to ready and will retry purchase next cycle")
                    else:
                        # Already attempting - let it continue
                        print(f"[PURCHASE] {tcin} IN STOCK but status={current_status} - let continue")
                else:
                    # OUT OF STOCK: Status managed by reset_completed_purchases_to_ready()
                    print(f"[PURCHASE] {tcin} OUT OF STOCK - status={current_status} (no action)")

            print(f"[PURCHASE_PROCESS_DEBUG] Stock processing completed, generated {len(results)} new purchase actions")

        return results

    def shutdown(self):
        """Clean shutdown"""
        pass  # No background threads to shutdown

def main():
    """Test the bulletproof purchase manager"""
    manager = BulletproofPurchaseManager()

    def status_callback(tcin, status, state):
        print(f"[REALTIME] {tcin} status changed to: {status}")

    manager.status_callback = status_callback

    print("Testing Bulletproof Purchase Manager...")

    # Test concurrent purchases
    import threading

    def test_purchase(tcin, name):
        result = manager.start_purchase(tcin, name)
        print(f"Thread {tcin}: {result}")

    threads = []
    for i in range(3):
        tcin = f"1234567{i}"
        thread = threading.Thread(target=test_purchase, args=(tcin, f"Test Product {i}"))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    print("Waiting for completions...")
    time.sleep(5)

    print("Final states:")
    print(manager.get_all_states())

    manager.shutdown()

if __name__ == '__main__':
    main()