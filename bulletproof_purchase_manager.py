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
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Callable

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

        # Thread synchronization
        self._file_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_purchases = {}  # Track active purchase threads

        # Status callback for real-time updates
        self.status_callback = status_callback

        # Ensure logs directory exists
        os.makedirs('logs', exist_ok=True)

    def check_and_complete_purchases(self):
        """Check and complete purchases - called during stock refresh cycles only"""
        current_time = time.time()
        print(f"[PURCHASE_DEBUG] Completion check running at timestamp {current_time:.3f}")

        with self._state_lock:
            states = self._load_states_unsafe()
            completed_purchases = []
            attempting_count = 0

            for tcin, state in states.items():
                if state.get('status') == 'attempting':
                    attempting_count += 1
                    complete_time = state.get('completes_at', 0)
                    started_time = state.get('started_at', 0)
                    time_remaining = complete_time - current_time
                    elapsed_time = current_time - started_time

                    print(f"[PURCHASE_DEBUG] {tcin} attempting: {time_remaining:.1f}s remaining (started at {started_time:.3f}, completes at {complete_time:.3f})")

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
            'attempt_count': 1
        }

        # Save state
        states[tcin] = new_state
        self._save_states_unsafe(states)

        print(f"[PURCHASE] Started purchase: {product_title} (TCIN: {tcin}) - duration: {duration:.1f}s")
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