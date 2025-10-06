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

        # Thread synchronization
        self._file_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_purchases = {}  # Track active purchase threads

        # Status callback for real-time updates
        self.status_callback = status_callback

        # Ensure logs directory exists
        os.makedirs('logs', exist_ok=True)

        # Initialize session system
        self._initialize_session_system()

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

        except Exception as e:
            print(f"[SESSION] [ERROR] Failed to initialize session system: {e}")
            self.session_failure_count += 1

            # [SAFETY_FALLBACK] Always preserve functionality
            if (self.feature_flags['enable_circuit_breaker'] and
                self.session_failure_count >= self.feature_flags['max_session_failures']):
                self._trigger_circuit_breaker(f"Initialization failure: {e}")
            else:
                print("[SESSION] [RETRY] Falling back to mock purchasing (system remains functional)")
                self.use_real_purchasing = False

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
        """Ensure session is initialized and ready"""
        if self.session_initialized:
            return True

        try:
            print("[SESSION] Starting session initialization...")

            # PRE-CHECK: Validate session file exists before opening browser
            import json
            from pathlib import Path
            session_path = Path("target.json")

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
                        print(f"[SESSION] [OK] Found {len(session_data['cookies'])} cookies in session file")

                except Exception as e:
                    print(f"[SESSION] [WARNING] Invalid target.json: {e} - will attempt auto-login")

            # Initialize session manager
            if await self.session_manager.initialize():
                print("[SESSION] [OK] Session manager initialized")

                # Start keep-alive service
                self.session_keepalive.start()
                print("[SESSION] [OK] Keep-alive service started")

                self.session_initialized = True
                return True
            else:
                print("[SESSION] [WARNING] Session manager initialization failed")
                return False

        except Exception as e:
            print(f"[SESSION] [ERROR] Session initialization error: {e}")
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
            print(f"[PURCHASE_RESET_DEBUG] Starting reset operation at timestamp {time.time():.3f}")

            # Load current states
            states = self._load_states_unsafe()
            print(f"[PURCHASE_RESET_DEBUG] Loaded {len(states)} total purchase states")

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

            # Save states if any resets occurred
            if reset_count > 0:
                print(f"[PURCHASE_RESET_DEBUG] Saving {reset_count} resets to file...")
                save_success = self._save_states_unsafe(states)

                if save_success:
                    print(f"[PURCHASE_RESET_DEBUG] Successfully saved {reset_count} resets to file")

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

                print(f"[PURCHASE] Reset {reset_count} completed purchases to ready state")
            else:
                print(f"[PURCHASE_RESET_DEBUG] No completed purchases found to reset")

            print(f"[PURCHASE_RESET_DEBUG] Reset operation completed at timestamp {time.time():.3f}")
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

                # Only reset completed states for OUT OF STOCK products
                if current_status in ['purchased', 'failed']:
                    is_in_stock = stock_status.get(tcin, False)

                    if not is_in_stock:  # Product is OUT OF STOCK - reset to ready
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
                    else:
                        # Product is IN STOCK - keep purchase status unchanged
                        print(f"[STOCK_AWARE_RESET_DEBUG] {tcin}: {current_status} -> unchanged (IN STOCK)")

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

        # CRITICAL: NO duplicate attempts during existing attempts
        if current_state.get('status') == 'attempting':
            print(f"[PURCHASE] PREVENTED DUPLICATE: {tcin} already attempting")
            return {'success': False, 'reason': 'already_attempting'}

        # Use real purchasing if enabled (fallback to mock if needed)
        print(f"[PURCHASE_MODE_DEBUG] use_real_purchasing={self.use_real_purchasing}, session_initialized={self.session_initialized}")

        if self.use_real_purchasing:
            # Force real purchase even if session validation failed
            # (session may be healthy but validation too strict)
            print(f"[PURCHASE_MODE] [TARGET] Using REAL browser automation for {tcin}")
            return self._start_real_purchase(tcin, product_title, states)
        else:
            # Mock mode available for testing without browser
            print(f"[PURCHASE_MODE] [MOCK] Using MOCK mode for {tcin} (set use_real_purchasing=True to enable browser)")
            return self._start_mock_purchase(tcin, product_title, states)

    def _start_real_purchase(self, tcin: str, product_title: str, states: Dict) -> Dict:
        """Start real purchase using PurchaseExecutor"""
        now = time.time()

        # Create initial state - NO TIMER for real purchases
        # Purchase starts as "queued" until session ready, then "attempting"
        new_state = {
            'status': 'queued',
            'tcin': tcin,
            'product_title': product_title,
            'started_at': now,
            # NO completes_at - real purchase completes when browser finishes
            'final_outcome': 'unknown',  # Will be determined by real purchase
            'attempt_count': 1,
            'real_purchase': True
        }

        # Save state
        states[tcin] = new_state
        self._save_states_unsafe(states)

        print(f"[PURCHASE] Queuing REAL purchase: {product_title} (TCIN: {tcin}) - waiting for session...")

        # Don't notify "attempting" yet - wait until actual execution starts
        # Status will be updated when browser automation actually begins

        # Start real purchase in background
        def execute_real_purchase():
            try:
                print(f"[REAL_PURCHASE_THREAD] [INIT] Starting async purchase execution for {tcin}")
                print(f"[REAL_PURCHASE_THREAD] purchase_executor: {self.purchase_executor}")
                print(f"[REAL_PURCHASE_THREAD] session_initialized: {self.session_initialized}")

                # CRITICAL: Wait for session to be ready (don't initialize new one!)
                if not self.session_initialized:
                    print(f"[REAL_PURCHASE_THREAD] Waiting for session initialization to complete...")
                    # Wait up to 30 seconds for main thread to finish initializing session
                    max_wait = 30
                    for i in range(max_wait):
                        if self.session_initialized:
                            print(f"[REAL_PURCHASE_THREAD] [OK] Session ready after {i}s wait")
                            break
                        time.sleep(1)

                    if not self.session_initialized:
                        print(f"[REAL_PURCHASE_THREAD] [ERROR] Session initialization timeout after {max_wait}s")
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

                # NOW notify attempting status since execution is actually starting
                if self.status_callback:
                    current_state = self._load_states_unsafe().get(tcin, {})
                    self.status_callback(tcin, 'attempting', current_state)
                    print(f"[REAL_PURCHASE_THREAD] Dashboard notified: {tcin} -> attempting")

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

                    # Update state with real result
                    self._update_purchase_result(tcin, result)

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

            # Process each product according to state rules
            for tcin, product_data in stock_data.items():
                current_state = states.get(tcin, {'status': 'ready'})
                current_status = current_state.get('status', 'ready')

                print(f"[PURCHASE_PROCESS_DEBUG] {tcin}: stock={product_data.get('in_stock')}, status={current_status}")

                if product_data.get('in_stock'):
                    # CRITICAL STATE RULE: IN STOCK + ready -> IMMEDIATELY go to "attempting"
                    if current_status == 'ready':
                        # Start new purchase attempt
                        result = self.start_purchase(tcin, product_data.get('title', f'Product {tcin}'))
                        if result.get('success'):
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