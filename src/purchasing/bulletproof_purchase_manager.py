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
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Callable

# Import session management components
from ..session import SessionManager, SessionKeepAlive, PurchaseExecutor
from .state_store import StateStore
from .worker import Worker, WorkerConfig
from .worker_pool import WorkerPool

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

class _PurchaseLogTee:
    """Tees sys.stdout to a purchase log file so every attempt is saved regardless of console scroll."""
    def __init__(self, original_stdout, log_path: Path):
        self._orig = original_stdout
        self._file = open(log_path, 'w', encoding='utf-8', buffering=1)
        self._lock = threading.Lock()

    def write(self, data):
        with self._lock:
            self._orig.write(data)
            try:
                self._file.write(data)
            except Exception:
                pass

    def flush(self):
        self._orig.flush()
        try:
            self._file.flush()
        except Exception:
            pass

    def close(self):
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._orig, name)


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

        # Persistent session management with safety tracking. The Worker
        # pool owns N browsers; at TARGET_WORKER_POOL_SIZE=1 (default) the
        # pool holds one Worker and behavior matches Phase 5 exactly.
        # session_manager/session_keepalive/purchase_executor are aliased
        # to the *primary* Worker's components after build_all() runs, so
        # the rest of this class continues to use them directly.
        self.worker_pool: Optional[WorkerPool] = None
        self.worker: Optional[Worker] = None     # alias to worker_pool.primary
        # Per-account exit-proxy forwarder (lazy; only when an account has a
        # Bright-Data auth proxy_url). Runs on its own asyncio loop+thread.
        self._forwarder_pool = None
        self._forwarder_loop = None
        self._forwarder_thread = None
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
        self._purchase_tee: Optional['_PurchaseLogTee'] = None  # Active purchase log tee
        self._warmup_cycle_counter: int = 0
        # Per-account Session Sentinel: periodically validates each worker is
        # genuinely logged in and escalates (refresh -> restart -> credential
        # re-login) if not. _account_health surfaces each account's last verdict
        # so a dead session is caught BEFORE a drop, not during.
        self._sentinel_cycle_counter: int = 0
        self._account_health: Dict[str, Dict] = {}
        self._sentinel_lock = threading.Lock()

        # Status callback for real-time updates
        self.status_callback = status_callback

        # Ensure logs directory exists
        os.makedirs('logs', exist_ok=True)

        # In-memory state store with background disk flush. The legacy
        # _load_states_unsafe / _save_states_unsafe shims (further down) now
        # route through this so every read is a microsecond in-memory hit
        # instead of a JSON parse off disk.
        self.state_store = StateStore(self.state_file)
        self.state_store.load_from_disk()
        self.state_store.start_flush_thread()

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

                    # Clean up "attempting" states older than 60 seconds
                    elif status == 'attempting' and started_at:
                        age_seconds = current_time - started_at
                        if age_seconds > 60:
                            stale_states.append({
                                'tcin': tcin,
                                'status': status,
                                'age_seconds': age_seconds
                            })
                            # Reset to ready
                            states[tcin] = {'status': 'ready'}
                            cleaned_count += 1
                            print(f"[STARTUP_CLEANUP] Cleaned up stale ATTEMPTING state: {tcin} was attempting for {age_seconds:.1f}s (threshold: 60s)")

                    # Always reset "interrupted" states — app was killed during a purchase, safe to retry
                    elif status == 'interrupted':
                        states[tcin] = {'status': 'ready'}
                        cleaned_count += 1
                        print(f"[STARTUP_CLEANUP] Cleaned up INTERRUPTED state: {tcin} (killed mid-purchase, retrying)")

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

            # Build session_manager + session_keepalive + purchase_executor
            # via Worker. At N=1 (today) the defaults preserve the legacy
            # paths: target.json + nodriver-profile.

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

            # Phase 6: build the Worker pool. Prefer config/target_accounts.json
            # (one Worker per enabled account, the same file the harvester logs
            # in) so the fleet auto-sizes 1->X with no env var; fall back to
            # TARGET_WORKER_POOL_SIZE when that file is absent. At N=1 the pool
            # holds the legacy Worker 1 with the legacy target.json +
            # nodriver-profile paths — no behavior change.
            self.worker_pool = WorkerPool.auto()
            # Per-account exit proxy: rewrite each worker's BD-auth proxy_url into
            # a live local forwarder address BEFORE build_all() constructs the
            # SessionManagers (they read cfg.proxy_url at init). No-op when no
            # account has a proxy configured (workers stay on the home IP).
            self._setup_purchase_forwarders()
            self.worker_pool.build_all(
                session_status_callback=session_status_callback,
                purchase_status_callback=purchase_status_callback,
            )

            # Alias the *primary* Worker's components onto self for back-compat
            # with the rest of this class — many call sites (and app.py via
            # global_purchase_manager.session_manager) read these directly.
            self.worker = self.worker_pool.primary
            self.session_manager = self.worker.session_manager
            self.session_keepalive = self.worker.session_keepalive
            self.purchase_executor = self.worker.purchase_executor

            pool_label = self.worker_pool.label()
            worker_label = self.worker.label()
            print(f"[SESSION] [OK] Session system components created successfully ({pool_label}, primary={worker_label})")
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
        """Ensure session is initialized and ready - GUARANTEES browser launch.

        At N=1 this initializes the primary Worker. At N>1 it launches every
        Worker's browser concurrently via WorkerPool.ensure_all_ready, where
        each Worker uses its own event loop on a dedicated thread.
        """
        print(f"[SESSION_DEBUG] _ensure_session_ready() called")
        print(f"[SESSION_DEBUG] session_initialized={self.session_initialized}")
        print(f"[SESSION_DEBUG] session_manager={self.session_manager}")

        # CRITICAL FIX: Check if browser is ACTUALLY running, not just if components exist
        if self.session_manager and hasattr(self.session_manager, 'browser'):
            print(f"[SESSION_DEBUG] session_manager.browser={self.session_manager.browser}")
            if self.session_manager.browser and self.session_manager.session_active:
                print("[SESSION] ✅ Browser already running - session ready")
                return True

        print("[SESSION] ⚡ Browser NOT running - launching now (GUARANTEED)...")
        self.session_initialized = False

        if self.worker_pool is None:
            print("[SESSION] [ERROR] ❌ WorkerPool not initialized — cannot launch browser")
            return False

        try:
            print("[SESSION] ═══════════════════════════════════════════════")
            print(f"[SESSION] Starting session initialization ({self.worker_pool.label()})...")
            print("[SESSION] ═══════════════════════════════════════════════")
            session_start_time = time.time()

            # Per-worker session-file pre-check (best-effort logging only).
            from pathlib import Path
            print("[SESSION] [STEP 1/3] Checking per-worker session files...")
            for w in self.worker_pool.workers:
                p = Path(w.cfg.session_path)
                if not p.exists():
                    print(f"[SESSION] [WARNING] {p} not found for {w.label()} — auto-login may be attempted")
                    continue
                try:
                    import json as _json
                    with open(p, 'r', encoding='utf-8') as f:
                        sdata = _json.load(f)
                    n_cookies = len(sdata.get('cookies') or [])
                    if n_cookies == 0:
                        print(f"[SESSION] [WARNING] {p} has no cookies for {w.label()} — auto-login may be attempted")
                    else:
                        print(f"[SESSION] [OK] ✅ {w.label()}: {n_cookies} cookies in {p}")
                except Exception as e:
                    print(f"[SESSION] [WARNING] Invalid {p}: {e} — auto-login may be attempted")

            print("[SESSION] [STEP 2/3] ⚡ Launching all worker browsers concurrently...")
            print(f"[SESSION] ⚡ Workers: {[w.label() for w in self.worker_pool.workers]}")

            # Bind the primary Worker to the *current* (global) event loop so
            # legacy callers that submit_async_task with global_event_loop —
            # notably the stock monitor's `run_coroutine_threadsafe(sm.get_page(),
            # global_loop)` path in app.py — keep working at N=1. Alt workers
            # (2..N) lazily spawn their own loops on first run_async.
            current_loop = asyncio.get_running_loop()
            primary = self.worker_pool.primary
            if primary.loop is None:
                primary.bind_external_loop(current_loop)
                print(f"[SESSION] ⚡ {primary.label()}: bound to global event loop")

            # Run the blocking pool launch off the calling event loop so the
            # caller's loop thread isn't blocked while per-worker loops do work.
            init_start = time.time()
            results = await current_loop.run_in_executor(
                None,
                self.worker_pool.ensure_all_ready,
            )
            init_duration = time.time() - init_start

            ok_workers = [lbl for lbl, r in results.items() if r.get('ok')]
            failed_workers = [(lbl, r.get('error')) for lbl, r in results.items() if not r.get('ok')]

            for lbl, r in results.items():
                if r.get('ok'):
                    print(f"[SESSION] [OK] ✅ {lbl}: init {r['init_seconds']}s, warmup {r['warmup_seconds']}s")
                else:
                    print(f"[SESSION] [ERROR] ❌ {lbl}: {r.get('error')}")

            print("[SESSION] [STEP 3/3] Keep-alive disabled - stock API calls maintain session")

            # Primary must be up; alts can fail-soft (manager will keep going,
            # but those workers will fall back to primary on dispatch).
            primary_ok = self.worker_pool.primary.label() in ok_workers
            if not primary_ok:
                print(f"[SESSION] [ERROR] ❌ Primary worker failed to initialize")
                print("[SESSION] ═══════════════════════════════════════════════")
                return False

            self.session_initialized = True
            total_duration = time.time() - session_start_time
            print("[SESSION] ═══════════════════════════════════════════════")
            print(f"[SESSION] ✅ {len(ok_workers)}/{len(results)} workers ready in {total_duration:.1f}s total (concurrent init)")
            if failed_workers:
                print(f"[SESSION] ⚠️  {len(failed_workers)} worker(s) failed: {failed_workers}")
            print("[SESSION] ═══════════════════════════════════════════════")
            return True

        except Exception as e:
            print(f"[SESSION] [ERROR] ❌ Session initialization error: {e}")
            print("[SESSION] ═══════════════════════════════════════════════")
            import traceback
            traceback.print_exc()
            return False

    def check_and_complete_purchases(self):
        """Check and complete purchases - called during stock refresh cycles only"""
        current_time = time.time()

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


                        # Force completion if purchase is overdue by more than 30 seconds (safety mechanism)
                        if time_remaining < -30:
                            print(f"[PURCHASE_FORCE_COMPLETE] {tcin} is severely overdue ({time_remaining:.1f}s), forcing completion")
                            final_outcome = 'failed'  # Force failed if severely overdue
                            self._finalize_purchase_unsafe(tcin, state, final_outcome, states)
                            completed_purchases.append((tcin, final_outcome))
                        elif current_time >= complete_time:
                            # Normal completion
                            final_outcome = state.get('final_outcome', 'failed')
                            self._finalize_purchase_unsafe(tcin, state, final_outcome, states)
                            completed_purchases.append((tcin, final_outcome))


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
            test_mode = os.environ.get('TEST_MODE', 'false').lower() == 'true'
            mode_label = "[TEST_MODE]" if test_mode else "[PROD_MODE]"

            # Load states and check if any work needs doing. The reset loop fires
            # every cycle (~600ms); printing the banner + per-TCIN diagnostic on
            # every call drowns out the actual purchase log.
            states = self._load_states_unsafe()
            now = time.time()
            has_work = any(
                s.get('status') in ('purchased', 'failed')
                or (s.get('status') == 'attempting' and isinstance(s.get('started_at'), (int, float)) and now - s['started_at'] > 60)
                or (s.get('status') == 'queued' and isinstance(s.get('started_at'), (int, float)) and now - s['started_at'] > 5)
                for s in states.values()
            )
            if not has_work:
                return 0  # Silent no-op — nothing to reset, nothing to log.

            print(f"[PURCHASE_RESET_DEBUG] {mode_label} Starting reset operation at timestamp {now:.3f}")
            if test_mode:
                print(f"[PURCHASE_RESET_DEBUG] {mode_label} TEST MODE: Resetting ALL completed purchases for endless loop")
                print(f"[TEST_MODE_RESET] ════════════════════════════════════════════════")
                print(f"[TEST_MODE_RESET] ENDLESS LOOP: Resetting purchases to 'ready'")
                print(f"[TEST_MODE_RESET] This allows the same product to be purchased again")
                print(f"[TEST_MODE_RESET] ════════════════════════════════════════════════")
            print(f"[PURCHASE_RESET_DEBUG] Loaded {len(states)} total purchase states")

            if test_mode:
                print(f"[TEST_MODE_DIAGNOSTIC] ALL STATES BEFORE RESET:")
                for tcin, state in states.items():
                    status = state.get('status', 'unknown')
                    completed_at = state.get('completed_at', 'N/A')
                    started_at = state.get('started_at', 'N/A')
                    age = f"{time.time() - started_at:.1f}s ago" if isinstance(started_at, (int, float)) and started_at > 0 else "N/A"
                    print(f"[TEST_MODE_DIAGNOSTIC]   {tcin}: status='{status}', started={age}, completed={completed_at}")

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
            # Load current states
            states = self._load_states_unsafe()

            # Create stock status lookup (handle different stock_data formats)
            stock_status = {}

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


            reset_count = 0
            reset_details = []

            for tcin, state in states.items():
                current_status = state.get('status')

                # CORRECTED LOGIC: Reset based on status and stock combination
                if current_status in ['purchased', 'failed']:
                    is_in_stock = stock_status.get(tcin, False)

                    if is_in_stock:  # Product is IN STOCK
                        old_order = state.get('order_number', 'N/A')
                        old_failure = state.get('failure_reason', 'unknown')
                        old_completed_at = state.get('completed_at', 0)

                        # Tiered Error Delay (replaces blanket 15s gate).
                        # Refract's published Target default is 3500ms; Stellar
                        # documents Checkout Delay ≤3000ms. The 15s gate was
                        # locking us out of every flash drop. Now we scope by
                        # failure type:
                        #   - Shape signal (403 / 401 / shape_block):     10s
                        #   - DCO_RATE_LIMITED / 429 ("just retry"):       3.5s
                        #   - generic transient (timeout / cart / exec):   3.5s
                        # The Shape branch stays longer because retrying inside
                        # a true Shape block burns more cookies for no gain
                        # (Stellar: full Shape block needs hours of rest).
                        if current_status == 'failed':
                            time_since = time.time() - old_completed_at if old_completed_at else 999
                            failure_reason = (state.get('failure_reason') or '').lower()
                            error_details = (state.get('error_details') or '').lower()
                            shape_signal = (
                                'shape' in failure_reason
                                or 'shape' in error_details
                                or '403' in failure_reason
                                or '401' in failure_reason
                                or 'auth_denied' in failure_reason
                            )
                            gate_s = 10.0 if shape_signal else 3.5
                            if time_since < gate_s:
                                print(
                                    f"[RESET] {tcin}: failed {time_since:.1f}s ago, "
                                    f"waiting for {gate_s:.1f}s Error Delay "
                                    f"(reason={failure_reason or 'unknown'}, shape={shape_signal})"
                                )
                                continue

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


            # Save states if any resets occurred
            if reset_count > 0:
                save_success = self._save_states_unsafe(states)

                if not save_success:
                    print(f"[STOCK_AWARE_RESET_ERROR] Failed to save resets to file!")
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
            except (OSError, IOError):
                pass
        elif HAS_FCNTL:
            # Unix/Linux file unlocking using fcntl
            try:
                if lock_fd:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
            except (OSError, IOError):
                pass

    def _load_states_unsafe(self) -> Dict:
        """Snapshot the in-memory state. Disk is only read once at startup.

        Kept under its legacy name for back-compat with ~20 call sites that
        all do `states = self._load_states_unsafe()` — each gets an
        independent dict copy so mutations stay local until the matching
        `_save_states_unsafe(states)` call writes them back.
        """
        return self.state_store.get_all()

    def _save_states_unsafe(self, states: Dict):
        """Replace the in-memory state dict. Disk persistence is async.

        Kept under its legacy name for back-compat. The background flush
        thread persists changes to `self.state_file` ~4× per second; an
        atomic write is also forced at shutdown.
        """
        try:
            self.state_store.update(states)
            return True
        except Exception as e:
            print(f"[PURCHASE] Failed to save states: {e}")
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

    def start_purchase(self, tcin: str, product_title: str, max_qty: int = 1) -> Dict:
        """Start a new purchase attempt with duplicate prevention (assumes caller has lock)

        max_qty: max quantity to add to cart (sourced from RedSky purchase limit /
        ATP via stock monitor). Defaults to 1 so callers that don't pass it stay
        on the legacy single-unit behavior.
        """
        states = self._load_states_unsafe()
        current_state = states.get(tcin, {'status': 'ready'})

        # PER-TCIN LOCK (replaces former cross-TCIN scan).
        #
        # Old behavior: any TCIN being `attempting` or `queued` blocked every
        # other TCIN. When two SKUs went in stock within seconds of each
        # other, the second was rejected with `another_purchase_active` and
        # dropped — by the time the first finished, the second was usually
        # gone.
        #
        # New behavior: only THIS tcin's status blocks itself. Different
        # TCINs route freely. Concurrency at the session/browser layer is
        # still serial today (single browser, single executor) and
        # downstream code in `execute_real_purchase` handles that — but the
        # dispatch path is unblocked, so signals for distinct SKUs no
        # longer drop.
        #
        # When the worker pool ships (Phase 6), the dispatcher uses the
        # StateStore CAS directly and this code path is bypassed.
        current_status = current_state.get('status', 'ready')
        if current_status in ('attempting', 'queued'):
            print(f"[PURCHASE] PREVENTED DUPLICATE: {tcin} already {current_status}")
            return {'success': False, 'reason': 'already_attempting'}

        # Open per-attempt log file and tee stdout into it
        _logs_dir = Path(__file__).parent.parent.parent / 'logs' / 'purchases'
        _logs_dir.mkdir(parents=True, exist_ok=True)
        _log_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        _log_path = _logs_dir / f'purchase_{tcin}_{_log_ts}.log'
        self._purchase_tee = _PurchaseLogTee(sys.stdout, _log_path)
        sys.stdout = self._purchase_tee

        # Use real purchasing if enabled (fallback to mock if needed)
        print("=" * 80)
        print(f"[PURCHASE_TRIGGER] Product IN STOCK: {product_title} (TCIN: {tcin})")
        print(f"[PURCHASE_LOG] Saving to: {_log_path}")
        print("=" * 80)

        if self.use_real_purchasing:
            # Force real purchase even if session validation failed
            # (session may be healthy but validation too strict)
            print(f"[PURCHASE_MODE] ✅ [REAL] Using REAL browser automation for {tcin}")
            return self._start_real_purchase(tcin, product_title, states, max_qty=max_qty)
        else:
            # Mock mode available for testing without browser
            print(f"[PURCHASE_MODE] ⚠️  [MOCK] Using MOCK mode for {tcin}")
            print(f"[PURCHASE_MODE] ⚠️  To enable browser: set use_real_purchasing=True")
            return self._start_mock_purchase(tcin, product_title, states)

    def _start_real_purchase(self, tcin: str, product_title: str, states: Dict, max_qty: int = 1) -> Dict:
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
        def execute_real_purchase(pinned_worker=None, state_key=None, race_agg=None):
            # Racing (multi-account) parameters:
            #   pinned_worker — when set, this thread buys on EXACTLY this worker
            #     (one account) instead of the sticky acquire_for_tcin path. The
            #     fan-out below launches one thread per ready worker so N accounts
            #     hit ATC concurrently — each its own 429 budget, each its own
            #     1-(or-2)-per-account unit. Distinct accounts buying their own
            #     units is NOT a double-buy.
            #   state_key — cross-cycle tracking key. tcin for single buys (legacy);
            #     f"{tcin}#W{id}" per worker when racing so the N threads don't
            #     clobber each other's _active_purchases entry.
            #   race_agg — shared aggregator (see _start_real_purchase) that merges
            #     the N per-worker results into one dashboard state and refcounts
            #     the global stock-monitor suspend/resume. None for single buys.
            state_key = state_key or tcin
            _race_wlbl = pinned_worker.label() if pinned_worker is not None else None
            try:
                _wlbl = _race_wlbl or 'auto'
                print(f"[REAL_PURCHASE_THREAD] [INIT] Starting async purchase execution for {tcin} ({_wlbl})")

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
                        time.sleep(random.uniform(0.15, 0.35))
                        polls += 1

                    if not self.session_initialized:
                        print(f"[REAL_PURCHASE_THREAD] [ERROR] Session initialization timeout after {polls * 0.2:.1f}s")
                        failed_result = {
                            'success': False,
                            'tcin': tcin,
                            'reason': 'session_init_timeout',
                            'error': f'Session not ready after {max_wait}s wait'
                        }
                        self._update_purchase_result(tcin, failed_result, race_agg=race_agg, worker_label=_race_wlbl)
                        return

                # Session is ready, proceed with purchase using subprocess (bypasses asyncio threading issues)

                # Phase 6: pick the Worker bound to this TCIN. If the assigned
                # Worker failed init (no live browser), fall back to primary so
                # we never silently lose purchases on alt-worker init failure.
                assigned_worker = None
                if pinned_worker is not None:
                    # Racing: this thread is bound to one specific (already-ready)
                    # account. No sticky acquire, no fallback — the fan-out only
                    # pins workers it pre-checked via WorkerPool.ready_workers().
                    assigned_worker = pinned_worker
                elif self.worker_pool is not None:
                    assigned_worker = self.worker_pool.acquire_for_tcin(tcin)
                    sm = assigned_worker.session_manager
                    if not (sm and getattr(sm, 'browser', None) and getattr(sm, 'session_active', False)):
                        primary = self.worker_pool.primary
                        print(
                            f"[DISPATCH] {assigned_worker.label()} not ready — falling back to "
                            f"{primary.label()} for {tcin}"
                        )
                        assigned_worker = primary
                # Final fallback: legacy single-worker path (shouldn't happen post-Phase 6)
                worker = assigned_worker or self.worker
                target_session_manager = worker.session_manager if worker else self.session_manager
                target_purchase_executor = worker.purchase_executor if worker else self.purchase_executor

                if worker is not None:
                    print(f"[DISPATCH] {tcin} → {worker.label()}")

                # BUGFIX: Set purchase lock to prevent session validation during purchase
                if target_session_manager:
                    target_session_manager.set_purchase_in_progress(True)
                # Suspend proxy stock-check workers for the duration of the purchase
                # so they don't compete with ATC for CPU and don't broadcast a
                # parallel-request pattern Shape can fingerprint. When racing, only
                # the FIRST worker to start suspends (refcounted via race_agg); the
                # LAST to finish resumes — so the monitor isn't resumed out from
                # under racers still mid-buy.
                _stock_monitor = getattr(self, 'stock_monitor', None)
                _do_suspend = True
                if race_agg is not None:
                    with race_agg['lock']:
                        race_agg['started'] += 1
                        _do_suspend = (race_agg['started'] == 1)
                if _do_suspend and _stock_monitor is not None and hasattr(_stock_monitor, 'set_suspended'):
                    try:
                        _stock_monitor.set_suspended(True)
                    except Exception as _sm_err:
                        print(f"[PURCHASE] [WARN] stock_monitor.set_suspended(True) failed: {_sm_err}")

                # CRITICAL: Register thread in active purchases tracking
                # This prevents race condition where next cycle starts before state is saved
                with self._state_lock:
                    self._active_purchases[state_key] = {
                        'thread': threading.current_thread(),
                        'started_at': time.time(),
                        'status': 'executing',
                        'worker': worker.label() if worker else 'primary',
                    }

                # NOTE: Status already set to 'attempting' when purchase was queued
                # No need to update again here - prevents race condition with dashboard

                # Use existing PurchaseExecutor with thread-safe async execution

                try:
                    # Floor at 1. The executor trusts qty > 1 as authoritative
                    # (RedSky already extracted purchase_limit at detection time)
                    # and only falls back to a PDP poll when qty == 1.
                    qty = max(1, int(max_qty or 1))
                    if qty != 1:
                        print(f"[REAL_PURCHASE_THREAD] [QTY] qty={qty} from RedSky purchase_limit (executor will skip PDP poll)")
                    # --- Retry-while-in-stock (deferred bug C3) ------------------
                    # A single transient ATC failure (atc_failed_api_mode / 401 /
                    # rate-limit / race) used to abandon the drop, even though the
                    # item typically stays buyable for minutes (06-02 ETB stayed
                    # in stock ~19.5 min after one failed shot, never retried).
                    # The 401 is INTERMITTENT — 05-29 the SAME session got ATC 201
                    # on the ETB while the bundle 401'd — so re-firing the API ATC
                    # across a bounded window converts cases a one-shot abandons.
                    # 100% API path — no DOM clicking, no architecture change.
                    _retry_on = os.environ.get('TARGET_RETRY_WHILE_IN_STOCK', '1') == '1'
                    # 110s default (06-05 tuning): sits just under the 120s force-
                    # complete watchdog so the loop uses the full in-stock window.
                    # Safe now that API-only retries fast-bail in ~2-3s (executor
                    # non-PDP hoist) instead of ~28s, so 110s buys ~30+ ATC shots
                    # against a 429/401 throttle instead of ~4.
                    _retry_budget_s = float(os.environ.get('TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S', '110'))
                    _retry_max = int(os.environ.get('TARGET_RETRY_WHILE_IN_STOCK_MAX', '24'))
                    _retry_deadline = time.time() + _retry_budget_s
                    # Reasons worth re-firing on. SAFETY (drop-readiness audit 2026-06-04):
                    # retry ONLY on failures that provably occur BEFORE any place-order POST,
                    # so a retry can never double-buy. Removed the two POST-submit reasons
                    # 'checkout_navigation_failed' and 'lock_timeout' — a committed-but-
                    # ambiguous order (lost/slow confirmation, or a place-order hang that
                    # surfaces as lock_timeout) returns one of those, and re-firing would buy
                    # a SECOND real unit. The intermittent 401 _ERR_AUTH_DENIED still lands as
                    # 'atc_failed_api_mode' (pre-cart), so C3's whole purpose is preserved.
                    # Replaced the dead key 'atc_button_not_ready' (the executor never returns
                    # it) with 'button_not_found', the real pre-submit string — recovers a
                    # DOM-fallback missed-conversion without any double-buy exposure.
                    # 'cdp_wedged_pre_atc' (06-05 02:10 fix): the executor's CDP
                    # health-check returns BEFORE the ATC POST ever fires, so it is
                    # provably pre-submit — retrying after a browser restart cannot
                    # double-buy. On 06-05 a tab wedged right after the 02:06 win
                    # lost Booster Bundle #2 outright. The pre-retry step below
                    # restarts the browser when the failure was a dead websocket.
                    _transient_reasons = {
                        'atc_failed_api_mode', 'rate_limited_429',
                        'atc_evaluate_timeout', 'page_not_ready', 'button_not_found',
                        'cdp_wedged_pre_atc',
                    }
                    # 2026-06-30 drop fix — persist through Target's checkout demand-
                    # throttle. The executor emits 'checkout_busy_retryable' ONLY when
                    # the checkout POST was server-REJECTED (HTTP 429/424, or a
                    # RESERVATION_FAILURE / rate-limit error-key) or the "Checkout is
                    # busy — please keep trying" queue page was shown, AND no order_id
                    # was captured — i.e. provably BEFORE any order commit, so
                    # re-firing cannot double-buy. The executor clears the cart before
                    # returning, so the re-race re-ATCs cleanly (no unit stacking).
                    # Kill-switch: TARGET_RETRY_CHECKOUT_BUSY=0 restores one-shot.
                    if os.environ.get('TARGET_RETRY_CHECKOUT_BUSY', '1') != '0':
                        _transient_reasons.add('checkout_busy_retryable')
                    # Substrings that mean the item is genuinely gone — stop now.
                    _terminal_tokens = ('oos', 'out_of_stock', 'sold_out', 'reservation', 'unavailable')

                    _attempt_n = 0
                    while True:
                        _attempt_n += 1
                        # Submit to the Worker's own loop so N workers run concurrently.
                        if worker is not None:
                            future = worker.run_async(
                                target_purchase_executor.execute_purchase(tcin, quantity=qty)
                            )
                        else:
                            future = target_session_manager.submit_async_task(
                                target_purchase_executor.execute_purchase(tcin, quantity=qty)
                            )

                        # Wait for result with timeout
                        result = future.result(timeout=150)

                        print(f"[REAL_PURCHASE_THREAD] [OK] Purchase execution completed "
                              f"(attempt {_attempt_n}): {result}")

                        if not _retry_on or result.get('success'):
                            break
                        _reason = str(result.get('reason', '')).lower()
                        _err = str(result.get('error', '')).lower()
                        if any(t in _reason or t in _err for t in _terminal_tokens):
                            print(f"[REAL_PURCHASE_THREAD] Terminal reason '{_reason}' — item gone, not retrying")
                            break
                        if _reason not in _transient_reasons:
                            print(f"[REAL_PURCHASE_THREAD] Reason '{_reason}' not transient — not retrying")
                            break
                        if _attempt_n >= _retry_max or time.time() >= _retry_deadline:
                            print(f"[REAL_PURCHASE_THREAD] Retry-while-in-stock budget spent "
                                  f"(attempts={_attempt_n}, deadline_hit={time.time() >= _retry_deadline}) "
                                  f"— finalizing as '{_reason}'")
                            break
                        # Transient failure, presumed still in stock. Force a REAL
                        # warmup-tab reload so Target's app can refresh the expired
                        # write token (the documented 401 remedy), then re-fire.
                        print(f"[REAL_PURCHASE_THREAD] Transient ATC failure '{_reason}' "
                              f"(attempt {_attempt_n}) — force re-warm + retry while in stock "
                              f"(t-left={max(0.0, _retry_deadline - time.time()):.0f}s)")
                        # Dead-websocket / wedged-tab recovery (06-05 02:10 fix):
                        # a tab wedged by the prior cycle (classically right after a
                        # successful order) can't be re-warmed — warm_shape_headers
                        # would just fail on the dead socket. Restart the browser
                        # first; refresh_session() rebuilds the browser + warmup tab
                        # and the next execute_purchase re-warms Shape itself.
                        _needs_restart = (
                            'cdp_wedged' in _reason
                            or any(s in _err for s in ('websocket', 'keepalive ping timeout',
                                                       'connection closed', '1011'))
                        )
                        if _needs_restart:
                            print(f"[REAL_PURCHASE_THREAD] Wedged/dead websocket before retry — restarting browser")
                            try:
                                if worker is not None:
                                    worker.run_async(
                                        target_session_manager.refresh_session()
                                    ).result(timeout=60)
                                else:
                                    target_session_manager.submit_async_task(
                                        target_session_manager.refresh_session()
                                    ).result(timeout=60)
                                print(f"[REAL_PURCHASE_THREAD] Browser restarted before retry")
                            except Exception as _rs_err:
                                print(f"[REAL_PURCHASE_THREAD] Pre-retry browser restart failed: {_rs_err}")
                        try:
                            if worker is not None:
                                worker.run_async(
                                    target_purchase_executor.warm_shape_headers(force_fresh=True)
                                ).result(timeout=25)
                            else:
                                target_session_manager.submit_async_task(
                                    target_purchase_executor.warm_shape_headers(force_fresh=True)
                                ).result(timeout=25)
                        except Exception as _warm_err:
                            print(f"[REAL_PURCHASE_THREAD] Pre-retry re-warm failed: {_warm_err}")
                        time.sleep(random.uniform(1.5, 3.0))

                    # CRITICAL: Update state ATOMICALLY with lock held
                    # This prevents race condition where next cycle sees stale "attempting" status
                    with self._state_lock:
                        # Mark as completing to block new cycles
                        if state_key in self._active_purchases:
                            self._active_purchases[state_key]['status'] = 'completing'
                            print(f"[REAL_PURCHASE_THREAD] Marked thread as completing: {state_key}")

                        # Update state file immediately
                        self._update_purchase_result(tcin, result, race_agg=race_agg, worker_label=_race_wlbl)

                        print(f"[REAL_PURCHASE_THREAD] ✅ State updated atomically, safe for next cycle")

                    # Detect dead WebSocket and restart browser before retry loop kicks in
                    if not result.get('success') and result.get('error', ''):
                        err_lower = result['error'].lower()
                        if any(sig in err_lower for sig in ('1011', 'keepalive ping timeout', 'connection closed', 'websocket')):
                            print(f"[REAL_PURCHASE_THREAD] WebSocket dead — triggering browser restart before retry")
                            try:
                                if worker is not None:
                                    refresh_future = worker.run_async(
                                        target_session_manager.refresh_session()
                                    )
                                else:
                                    refresh_future = target_session_manager.submit_async_task(
                                        target_session_manager.refresh_session()
                                    )
                                refresh_future.result(timeout=60)
                                print(f"[REAL_PURCHASE_THREAD] Browser restarted successfully")
                            except Exception as refresh_err:
                                print(f"[REAL_PURCHASE_THREAD] Browser restart failed: {refresh_err}")

                except TimeoutError:
                    print(f"[REAL_PURCHASE_THREAD] [ERROR] Purchase execution timed out after 150s — cancelling coroutine")
                    future.cancel()  # CRITICAL: cancels the asyncio Task, releasing _page_lock
                    failed_result = {
                        'success': False,
                        'tcin': tcin,
                        'reason': 'execution_timeout',
                        'error': 'Purchase execution timed out'
                    }
                    self._update_purchase_result(tcin, failed_result, race_agg=race_agg, worker_label=_race_wlbl)
                    return

                except Exception as e:
                    print(f"[REAL_PURCHASE_THREAD] [ERROR] Purchase execution failed: {e}")
                    try:
                        future.cancel()  # Cancel to avoid orphan coroutines holding _page_lock
                    except Exception:
                        pass
                    raise

            except Exception as e:
                print(f"[PURCHASE] [ERROR] Real purchase failed for {tcin}: {e}")
                _tb_str = traceback.format_exc()
                traceback.print_exc()
                try:
                    os.makedirs('logs', exist_ok=True)
                    with open('logs/error_log.txt', 'a', encoding='utf-8') as _f:
                        _f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [PURCHASE_THREAD] Real purchase failed for {tcin}: {type(e).__name__}: {e}\n{_tb_str}\n")
                except Exception:
                    pass
                # Mark as failed
                failed_result = {
                    'success': False,
                    'tcin': tcin,
                    'reason': 'execution_error',
                    'error': str(e)
                }
                self._update_purchase_result(tcin, failed_result, race_agg=race_agg, worker_label=_race_wlbl)

            finally:
                # BUGFIX: Always clear purchase lock when purchase completes (success or failure).
                # Phase 6: clear on whichever worker handled the dispatch (may be an
                # alt worker, not primary). target_session_manager is None if dispatch
                # never picked one (very early exit path), in which case fall back to
                # primary for backwards compat.
                _sm_to_release = locals().get('target_session_manager') or self.session_manager
                if _sm_to_release:
                    _sm_to_release.set_purchase_in_progress(False)
                # Resume proxy stock-check workers. When racing, only the LAST
                # worker to finish resumes (refcounted) so the monitor stays
                # suspended while any racer is still buying.
                _stock_monitor = getattr(self, 'stock_monitor', None)
                _do_resume = True
                if race_agg is not None:
                    with race_agg['lock']:
                        race_agg['finished'] += 1
                        _do_resume = (race_agg['finished'] >= race_agg['total'])
                if _do_resume and _stock_monitor is not None and hasattr(_stock_monitor, 'set_suspended'):
                    try:
                        _stock_monitor.set_suspended(False)
                    except Exception as _sm_err:
                        print(f"[PURCHASE] [WARN] stock_monitor.set_suspended(False) failed: {_sm_err}")

                # CRITICAL: Remove from active purchases tracking (per-worker key
                # when racing). This signals to next cycle that thread has completed.
                with self._state_lock:
                    if state_key in self._active_purchases:
                        print(f"[REAL_PURCHASE_THREAD] Removing {state_key} from active purchases")
                        del self._active_purchases[state_key]
                    else:
                        print(f"[REAL_PURCHASE_THREAD] Note: {state_key} already removed from active purchases")

                # Close purchase log and restore stdout
                _tee = self._purchase_tee
                if _tee is not None:
                    self._purchase_tee = None
                    if sys.stdout is _tee:
                        sys.stdout = _tee._orig
                    _tee.close()

        # ----- Dispatch: race the fleet, or single-worker (legacy) -------------
        # RACING fires one thread per READY account concurrently at this drop.
        # Each account = own session/loop/IP-budget, so each gets its own 429
        # budget and buys its own up-to-limit units — the documented cure for the
        # single-account DCO_RATE_LIMITED that killed past drops. Activates ONLY
        # when >1 worker is actually ready (i.e. you've seeded multiple accounts),
        # so single-account setups are byte-for-byte unchanged. Kill switch:
        # TARGET_RACE_ALL_WORKERS=0.
        ready_workers = self.worker_pool.ready_workers() if self.worker_pool is not None else []
        race_on = (
            os.environ.get('TARGET_RACE_ALL_WORKERS', '1') != '0'
            and len(ready_workers) > 1
        )

        if race_on:
            race_agg = {
                'lock': threading.Lock(),
                'total': len(ready_workers),
                'started': 0,
                'finished': 0,
                'results': {},   # worker_label -> result dict
                'units': 0,      # total units bought across accounts
            }
            print(f"[RACE] {tcin}: racing {len(ready_workers)} accounts → "
                  f"{[w.label() for w in ready_workers]}")
            for w in ready_workers:
                sk = f"{tcin}#W{w.cfg.worker_id}"
                threading.Thread(
                    target=execute_real_purchase,
                    kwargs={'pinned_worker': w, 'state_key': sk, 'race_agg': race_agg},
                    daemon=True,
                ).start()
        else:
            # Legacy single-worker path — unchanged.
            purchase_thread = threading.Thread(target=execute_real_purchase, daemon=True)
            purchase_thread.start()

        return {
            'success': True,
            'tcin': tcin,
            'duration': 60,  # Max expected duration
            'status': 'attempting',
            'real_purchase': True,
            'racing': race_on,
            'accounts': len(ready_workers) if race_on else 1,
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

    def _update_purchase_result(self, tcin: str, result: Dict, race_agg: Optional[Dict] = None,
                                worker_label: Optional[str] = None):
        """Update purchase state with real purchase result.

        race_agg (set only when racing the fleet) merges this worker's result
        into one aggregated dashboard state for the TCIN instead of overwriting —
        so N accounts buying N units show as one "2/3 bought" record rather than
        clobbering each other. None preserves the legacy single-worker behavior
        exactly (the body below).
        """
        if race_agg is not None:
            return self._record_race_result(tcin, result, race_agg, worker_label)
        with self._state_lock:
            states = self._load_states_unsafe()
            current_state = states.get(tcin, {})

            current_status = current_state.get('status')
            if current_status != 'attempting':
                # State was reset by the stuck-timeout while purchase was still running.
                # Still honour the result so successful purchases are recorded.
                print(f"[PURCHASE] Warning: {tcin} state is '{current_status}' (not 'attempting') — purchase thread finished late. Still applying result.")
                # Re-create a minimal state so the result can be saved properly
                current_state = {'status': 'attempting', 'tcin': tcin}

            now = time.time()

            if result['success']:
                # Purchase successful
                # order_id is parsed from the confirmation URL query param (?orderId=...) inside
                # purchase_executor.py _complete_checkout — it must be added to the return dict there.
                # Until that is done, extract it from result['confirmation_url'] if present, otherwise
                # fall back to result['order_id'] / result['order_number']. Never generate a fake ID.
                raw_order_id = result.get('order_id') or result.get('order_number')
                conf_url = result.get('confirmation_url', '')
                if not raw_order_id:
                    if 'orderId=' in conf_url:
                        raw_order_id = conf_url.split('orderId=')[1].split('&')[0]
                if not raw_order_id:
                    # TEST_MODE intentionally lands on /cart (no order placed),
                    # so a missing order_id is expected there — don't spam the warning.
                    if not conf_url.rstrip('/').endswith('/cart'):
                        print(f"[PURCHASE] WARNING: no order_id in executor result for {tcin} — executor must return order_id from confirmation URL (?orderId= param). Storing None.")
                    raw_order_id = None

                final_state = {
                    **current_state,
                    'status': 'purchased',
                    'completed_at': now,
                    'final_outcome': 'purchased',
                    'execution_time': result.get('execution_time', 0),
                    'order_number': raw_order_id,
                    'price': result.get('price')
                }

                print(f"[PURCHASE]  REAL purchase completed: {tcin} — order_id={raw_order_id}")

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

    # Chrome can't take inline proxy auth, and BD ISP IPs require it. Treat a
    # proxy_url as "needs forwarder" when it carries credentials / is a BD host.
    @staticmethod
    def _proxy_needs_forwarder(url: str) -> bool:
        low = url.lower()
        return ('@' in url) or ('superproxy' in low) or ('brd.' in low)

    # Local port band for the PURCHASE forwarders. Stock stack uses 22000+i;
    # keep purchase exits on a distinct band so the two never collide.
    PURCHASE_FORWARDER_PORT_BASE = 23000

    def _setup_purchase_forwarders(self) -> None:
        """Start a local CONNECT forwarder for each worker whose proxy_url is a
        BD auth URL, and rewrite that worker's cfg.proxy_url to the local
        127.0.0.1:port Chrome can use. Plain host:port proxies pass through
        untouched; workers with no proxy_url stay on the home IP.

        No-op (and no forwarder thread) when no worker needs one — so single-
        account / no-proxy setups are completely unaffected.

        Every decision is ALSO appended to logs/forwarder.log so a drop
        post-mortem can PROVE which account exited which IP. (2026-06-30: app.py
        stdout wasn't captured, so the forwarder bind was unverifiable after the
        fact — we could not confirm isolation was even active.)
        """
        import re as _re, datetime as _dt, os as _os

        def _flog(msg):
            try:
                _os.makedirs('logs', exist_ok=True)
                with open('logs/forwarder.log', 'a', encoding='utf-8') as _f:
                    _f.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            except Exception:
                pass

        def _exit_ip(u):
            m = _re.search(r'-ip-([0-9.]+)[:@]', u or '')
            return m.group(1) if m else '?'

        pool = self.worker_pool
        if pool is None:
            return
        needing = [w for w in pool.workers
                   if w.cfg.proxy_url and self._proxy_needs_forwarder(w.cfg.proxy_url)]
        # Record the full intended egress map up front (home-IP workers included).
        _flog("=== purchase forwarder setup ===")
        for w in pool.workers:
            if w.cfg.proxy_url and self._proxy_needs_forwarder(w.cfg.proxy_url):
                _flog(f"  intent: {w.label()} worker_id={w.cfg.worker_id} → BD:{_exit_ip(w.cfg.proxy_url)}")
            else:
                _flog(f"  intent: {w.label()} worker_id={w.cfg.worker_id} → HOME IP (no BD proxy_url)")
        if not needing:
            # Plain host:port proxies (if any) are already Chrome-usable; nothing to do.
            _flog("no workers need a forwarder — all on HOME IP / plain proxy")
            return

        try:
            from src.proxy.local_forwarder import ForwarderPool
        except Exception as e:
            print(f"[FORWARDER] [WARN] could not import ForwarderPool ({e}); "
                  f"purchase browsers will use the HOME IP.")
            _flog(f"[ERROR] ForwarderPool import FAILED ({e}) → ALL BD workers reverted to HOME IP")
            for w in needing:
                w.cfg.proxy_url = None
            return

        # Dedicated asyncio loop+thread so the forwarder servers keep accepting
        # connections for the life of the manager (mirrors the Worker loop model).
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run():
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        t = threading.Thread(target=_run, daemon=True, name="PurchaseForwarderLoop")
        t.start()
        ready.wait()

        fwd = ForwarderPool()
        for w in needing:
            port = self.PURCHASE_FORWARDER_PORT_BASE + w.cfg.worker_id
            bd_url = w.cfg.proxy_url
            try:
                fwd.add_upstream(bd_url, port)
                w.cfg.proxy_url = f"127.0.0.1:{port}"   # what Chrome actually uses
                print(f"[FORWARDER] {w.label()} → 127.0.0.1:{port} (exit via account proxy)")
            except Exception as e:
                print(f"[FORWARDER] [WARN] {w.label()} proxy unparseable ({e}); "
                      f"falling back to HOME IP.")
                w.cfg.proxy_url = None

        if not fwd.upstreams:
            # Everything failed to parse — tear the loop back down, no forwarder.
            loop.call_soon_threadsafe(loop.stop)
            return

        try:
            fut = asyncio.run_coroutine_threadsafe(fwd.start_all(), loop)
            fut.result(timeout=15)
        except Exception as e:
            print(f"[FORWARDER] [ERROR] start_all failed ({e}); reverting workers to HOME IP.")
            for w in needing:
                w.cfg.proxy_url = None
            loop.call_soon_threadsafe(loop.stop)
            return

        self._forwarder_pool = fwd
        self._forwarder_loop = loop
        self._forwarder_thread = t
        print(f"[FORWARDER] purchase forwarder pool live: {len(fwd.upstreams)} exit(s)")

    def _shutdown_purchase_forwarders(self) -> None:
        """Stop the purchase forwarder pool + its loop/thread. Safe if never started."""
        fwd, loop = self._forwarder_pool, self._forwarder_loop
        if fwd is not None and loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(fwd.stop_all(), loop).result(timeout=5)
            except Exception:
                pass
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        self._forwarder_pool = None
        self._forwarder_loop = None
        self._forwarder_thread = None

    # How often the sentinel runs, in monitoring cycles. ~2s/cycle -> 150 ≈ 5 min.
    # Well under the 24h access-token life and the months-long refresh-token life,
    # so a silently-logged-out account is caught and re-logged-in long before a drop.
    SENTINEL_CYCLE_INTERVAL = int(os.environ.get('TARGET_SENTINEL_CYCLE_INTERVAL', '150'))

    def _maybe_run_session_sentinel(self) -> None:
        """Every SENTINEL_CYCLE_INTERVAL cycles, validate each worker is logged in
        and self-heal if not. Non-blocking: each check runs on the worker's own
        loop and records its verdict into _account_health. Skips workers mid-
        purchase (don't disrupt an in-flight ATC) and when racing is disabled.
        Disable entirely with TARGET_SESSION_SENTINEL=0."""
        if os.environ.get('TARGET_SESSION_SENTINEL', '1').lower() in ('0', 'false'):
            return
        if self.worker_pool is None:
            return
        self._sentinel_cycle_counter += 1
        if not (self._sentinel_cycle_counter == 5
                or self._sentinel_cycle_counter % self.SENTINEL_CYCLE_INTERVAL == 0):
            return

        for w in self.worker_pool.workers:
            sm = getattr(w, 'session_manager', None)
            if sm is None:
                continue
            # Never disturb a worker that's mid-purchase.
            if getattr(sm, 'purchase_in_progress', False):
                continue
            label = w.label()

            def _record(fut, _label=label):
                ok = False
                err = None
                try:
                    ok = bool(fut.result())
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                with self._sentinel_lock:
                    self._account_health[_label] = {
                        'logged_in': ok,
                        'checked_at': time.time(),
                        'error': err,
                    }
                tag = "OK" if ok else "FAILED"
                print(f"[SENTINEL] {_label}: logged_in={ok} ({tag})"
                      + (f" err={err}" if err else ""))

            try:
                fut = w.run_async(sm.ensure_logged_in())
                fut.add_done_callback(_record)
            except Exception as e:
                print(f"[SENTINEL] {label}: could not queue check: {e}")

    def get_account_health(self) -> Dict[str, Dict]:
        """Snapshot of each account's last Session Sentinel verdict (for the
        dashboard / pre-drop readiness). {worker_label: {logged_in, checked_at, error}}."""
        with self._sentinel_lock:
            return {k: dict(v) for k, v in self._account_health.items()}

    @staticmethod
    def _decide_target_qty(redsky_max_qty) -> "tuple[int, str]":
        """Resolve the per-account add-to-cart quantity from the RedSky hint.

        Returns (qty, reason). Env knobs:
          TARGET_FORCE_QTY_1=true   -> always 1 (hard rollback).
          TARGET_QTY_CEILING=N      -> per-order cap (default 2; free-ship + TCG cap).
          TARGET_QTY_OPTIMISTIC=0   -> when RedSky omits the limit, fall back to 1
                                       instead of optimistically targeting the ceiling.

        Optimistic-2 (2026-06-22): the bulk RedSky feed usually OMITS the per-
        customer limit, so the hint defaults to 1 even on limit-2 TCG SKUs — which
        clamped min(2,1)=1 and shipped qty=1 every drop (744/744). When the limit
        is unreported (<=1) we target the ceiling; the executor self-heals a TRUE
        limit-1 via its 422/409 PURCHASE_LIMIT retry (qty 2->1, keeps the unit).
        A genuinely-reported limit is always respected (min with ceiling).
        """
        if os.environ.get('TARGET_FORCE_QTY_1', 'false').lower() == 'true':
            return 1, "TARGET_FORCE_QTY_1"
        try:
            ceiling = int(os.environ.get('TARGET_QTY_CEILING', '2'))
        except ValueError:
            ceiling = 2
        ceiling = max(1, ceiling)
        try:
            redsky_qty = max(1, int(redsky_max_qty or 1))
        except (TypeError, ValueError):
            redsky_qty = 1
        optimistic = os.environ.get('TARGET_QTY_OPTIMISTIC', '1').lower() in ('1', 'true')
        if redsky_qty > 1:
            return min(ceiling, redsky_qty), f"reported per-order limit (min(ceiling={ceiling}, RedSky={redsky_qty}))"
        if optimistic:
            return ceiling, f"RedSky limit unreported — optimistic ceiling={ceiling} (executor 422/409 self-heals true limit-1)"
        return 1, "RedSky limit unreported, optimistic disabled"

    def _record_race_result(self, tcin: str, result: Dict, race_agg: Dict, worker_label: Optional[str]):
        """Merge one racing worker's result into the shared aggregate and write
        the combined TCIN state. Called once per worker (one terminal result each).

        While racers are still in flight the TCIN stays 'attempting' so the
        same-TCIN dispatch guard (status check in process loop) keeps blocking a
        second drop dispatch. When the last worker reports, the state finalizes to
        'purchased' (if any account bought) or 'failed', carrying units_bought,
        a per-account breakdown, and every order number captured.
        """
        wl = worker_label or f"w{len(race_agg['results']) + 1}"
        with race_agg['lock']:
            race_agg['results'][wl] = result
            if result.get('success'):
                race_agg['units'] += int(result.get('quantity') or result.get('qty') or 1)
            results_snapshot = dict(race_agg['results'])
            units = race_agg['units']
            total = race_agg['total']

        recorded = len(results_snapshot)
        successes = [r for r in results_snapshot.values() if r.get('success')]
        any_success = bool(successes)
        all_done = recorded >= total

        # Collect order numbers from successful accounts (same parse as the
        # single-worker path: result field, else ?orderId= from confirmation URL).
        orders = []
        for r in successes:
            oid = r.get('order_id') or r.get('order_number')
            if not oid:
                cu = r.get('confirmation_url', '') or ''
                if 'orderId=' in cu:
                    oid = cu.split('orderId=')[1].split('&')[0]
            if oid:
                orders.append(oid)

        breakdown = {
            w: ('purchased' if r.get('success') else (r.get('reason') or 'failed'))
            for w, r in results_snapshot.items()
        }

        with self._state_lock:
            states = self._load_states_unsafe()
            cur = states.get(tcin, {}) or {}
            now = time.time()
            status = ('attempting' if not all_done
                      else ('purchased' if any_success else 'failed'))
            final_state = {
                **cur,
                'status': status,
                'tcin': tcin,
                'real_purchase': True,
                'race': True,
                'race_breakdown': breakdown,
                'units_bought': units,
                'accounts_total': total,
                'accounts_done': recorded,
                'final_outcome': ('purchased' if (all_done and any_success)
                                  else ('failed' if all_done else 'unknown')),
            }
            if all_done:
                final_state['completed_at'] = now
                if orders:
                    final_state['order_number'] = orders[0]
                    final_state['order_numbers'] = orders
                if not any_success:
                    reasons = [r.get('reason') for r in results_snapshot.values() if r.get('reason')]
                    final_state['failure_reason'] = reasons[0] if reasons else 'all_accounts_failed'
            states[tcin] = final_state
            self._save_states_unsafe(states)
            if all_done and self.status_callback:
                self.status_callback(tcin, status, final_state)

        print(f"[RACE] {tcin}: {recorded}/{total} accounts done, units_bought={units}, "
              f"breakdown={breakdown}")

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
            order_num = state.get('order_number')
            if order_num:
                print(f"[PURCHASE] Purchase successful: {product_title} - Order: {order_num}")
            elif os.environ.get('TEST_MODE', 'false').lower() == 'true':
                print(f"[PURCHASE] [TEST_MODE] Cycle complete (no real order): {product_title}")
            else:
                print(f"[PURCHASE] Purchase successful: {product_title} - Order: unknown")
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
                        break

            # CRITICAL: Also check RUNTIME state (background threads)
            # Prevents race condition where thread is completing but file status not yet updated
            if not active_purchase:
                # DEADLOCK FIX: Don't re-acquire lock - we're already inside self._state_lock from line 1220
                # First, clean up any dead threads that failed to remove themselves
                dead_threads = [t for t, info in self._active_purchases.items()
                                if not info['thread'].is_alive()]
                for dead_tcin in dead_threads:
                    elapsed = time.time() - self._active_purchases[dead_tcin]['started_at']
                    print(f"[PURCHASE_CONCURRENCY] Cleaning up dead thread for {dead_tcin} (ran {elapsed:.1f}s, never cleaned up)")
                    del self._active_purchases[dead_tcin]
                    # Also reset the file state if it's stuck in attempting
                    if states.get(dead_tcin, {}).get('status') == 'attempting':
                        states[dead_tcin] = {'status': 'ready'}
                        self._save_states_unsafe(states)
                        print(f"[PURCHASE_CONCURRENCY] Reset stuck attempting state for dead thread: {dead_tcin}")

                if self._active_purchases:
                    active_tcin = list(self._active_purchases.keys())[0]
                    thread_info = self._active_purchases[active_tcin]
                    elapsed = time.time() - thread_info['started_at']
                    active_purchase = active_tcin
                    print(f"[PURCHASE_CONCURRENCY] Background thread still active: {active_purchase} (running {elapsed:.1f}s, status: {thread_info['status']})")

                    # RACE CONDITION FIX: If thread is marked as 'completing' (in cleanup phase),
                    # poll until it finishes before starting new purchase.
                    # DEADLOCK FIX (2026-05-07): we are holding self._state_lock here, but
                    # the worker thread's finally block also needs that lock to del
                    # _active_purchases[tcin]. With cycles now ~2s, the cleanup thread can
                    # be mid-finally when we arrive — sleeping under the lock causes a
                    # 30s timeout deadlock. Release the lock during sleep, re-take briefly
                    # to re-check membership.
                    if thread_info.get('status') == 'completing':
                        print(f"[PURCHASE_CONCURRENCY] Thread is completing (cleanup phase) - waiting up to 30s...")

                        max_wait = 30.0
                        poll_interval = 0.1  # Check every 100ms (cleanup is now <500ms)
                        waited = 0.0
                        completed = False

                        while waited < max_wait:
                            self._state_lock.release()
                            try:
                                time.sleep(poll_interval)
                            finally:
                                self._state_lock.acquire()
                            waited += poll_interval

                            if active_tcin not in self._active_purchases:
                                print(f"[PURCHASE_CONCURRENCY] Thread completed after {waited:.1f}s - safe to start new purchase")
                                active_purchase = None  # Clear flag to allow new purchase
                                completed = True
                                break

                        if not completed:
                            elapsed_total = time.time() - thread_info['started_at']
                            print(f"[PURCHASE_CONCURRENCY] Thread still active after {max_wait}s wait ({elapsed_total:.1f}s total) - will skip new purchases this cycle")
                            # Keep active_purchase set to block new purchases

            # BUG FIX #2: Load product priority order from config
            try:
                import json
                config_path = 'config/product_config.json'
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                # Config format: {"products": [{"tcin": "...", "name": "..."}, ...]}
                products_list = config.get('products', [])
                product_priority_order = [p['tcin'] for p in products_list if 'tcin' in p]
            except Exception as e:
                print(f"[PURCHASE_PRIORITY_ERROR] Could not load product priority: {e}")
                import traceback
                traceback.print_exc()
                # Fallback to stock_data order
                product_priority_order = list(stock_data.keys())

            # Sort stock_data by explicit priority field (lower number = higher priority)
            # Falls back to config list order if priority field is missing
            priority_map = {}
            try:
                for i, p in enumerate(products_list):
                    if 'tcin' in p:
                        # Use the priority field if present, otherwise use list position
                        priority_map[p['tcin']] = p.get('priority', i + 1)
            except Exception:
                pass

            def get_priority_index(tcin):
                return priority_map.get(tcin, 999999)

            sorted_tcins = sorted(stock_data.keys(), key=get_priority_index)

            # Refresh Shape headers every 30 cycles (~30s) to keep auth tokens fresh.
            # Auth tokens go stale in ~40-50s — 30s interval ensures the direct fetch ATC
            # stays within the valid window and avoids the slow button-click fallback path.
            self._warmup_cycle_counter += 1
            if (self._warmup_cycle_counter == 1 or self._warmup_cycle_counter % 30 == 0) and self.worker_pool is not None:
                # Phase 6: cycle-warm every worker's executor on its own loop, not just primary's.
                for w in self.worker_pool.workers:
                    ex = w.purchase_executor
                    if ex is None:
                        continue
                    headers_age = time.time() - getattr(ex, '_cached_cart_headers_ts', 0)
                    in_progress = getattr(ex, '_warmup_in_progress', False)
                    print(f"[WARMUP_CYCLE] Cycle {self._warmup_cycle_counter} {w.label()}: "
                          f"headers_age={headers_age:.0f}s, in_progress={in_progress}")
                    if not in_progress and headers_age > 5:
                        print(f"[WARMUP_CYCLE] {w.label()}: queuing Shape header refresh...")
                        try:
                            w.run_async(ex.warm_shape_headers())
                        except Exception as e:
                            print(f"[WARMUP_CYCLE] {w.label()}: could not queue warmup: {e}")
                    elif not in_progress:
                        print(f"[WARMUP_CYCLE] {w.label()}: skipping — headers fresh ({headers_age:.0f}s old)")

            # Per-account Session Sentinel — deeper than warmup: validates each
            # account is genuinely logged in and self-heals (refresh -> restart ->
            # credential re-login) so EVERY account is drop-ready, not just primary.
            self._maybe_run_session_sentinel()

            # Process each product according to state rules (in priority order)
            for tcin in sorted_tcins:
                product_data = stock_data[tcin]
                current_state = states.get(tcin, {'status': 'ready'})
                current_status = current_state.get('status', 'ready')


                if product_data.get('in_stock'):
                    # CRITICAL STATE RULE: IN STOCK + ready -> IMMEDIATELY go to "attempting"
                    if current_status == 'ready':
                        # BUG FIX #1: Only start if no active purchase
                        if active_purchase:
                            print(f"[PURCHASE_CONCURRENCY] Skipping {tcin} - purchase already active for {active_purchase}")
                            continue

                        # Start new purchase attempt.
                        #
                        # Quantity policy (2026-06-05, FINALIZED DEFAULT).
                        # qty = min(ceiling, RedSky limit), ceiling default 2. Two units
                        # of a $29.99 SKU = $59.98 > Target's $35 free-ship threshold, and
                        # 2 is Target's standard per-order cap on TCG SKUs — so on a
                        # limit-2 SKU this is fully within policy (no cancellation) AND
                        # dodges shipping. This is how a single Refract/Stellar task is
                        # configured per account (qty = item limit, capped); top bots
                        # scale by account count, not qty depth. See docs/ANTIBOT.md:53 +
                        # docs/DROP_DAY_PLAYBOOK.md:12.
                        #
                        # The ceiling is LOAD-BEARING. stock_monitor's max_qty can be
                        # polluted by ATP (warehouse qty) when RedSky omits the
                        # per-customer limit, so the raw value can be large (the old
                        # reverted path clamped to [1,10] and got orders auto-cancelled).
                        # Capping at 2 prevents that over-order; the executor re-clamps
                        # to the same ceiling as a hard invariant. On a true limit-1 SKU
                        # the executor's 422/400 PURCHASE_LIMIT self-heal drops 2->1.
                        # Rollback: set TARGET_FORCE_QTY_1=true to restore qty=1.
                        # Known residual: a limit-1 SKU whose ATC permissively accepts 2
                        # can be cancelled post-checkout to 0 (rare; bounded by the cap).
                        max_qty, _qty_reason = self._decide_target_qty(product_data.get('max_qty', 1))
                        if max_qty != 1:
                            print(f"[QTY] {tcin}: targeting qty={max_qty} — {_qty_reason}")
                        result = self.start_purchase(tcin, product_data.get('title', f'Product {tcin}'), max_qty=max_qty)
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
                    elif current_status in ['purchased', 'failed', 'interrupted']:
                        # 'interrupted' means the app was killed mid-purchase — always safe to retry.
                        # 'failed'/'purchased' here means the reset cycle hasn't run yet.
                        print(f"[PURCHASE_ERROR] {tcin} is IN STOCK but still has completed status '{current_status}' - reset failed!")
                        # Emergency force reset
                        states[tcin] = {'status': 'ready'}
                        self._save_states_unsafe(states)
                        print(f"[PURCHASE_EMERGENCY] Emergency reset {tcin} to ready and will retry purchase next cycle")
                    else:
                        pass  # Already attempting - let it continue
                else:
                    pass  # OUT OF STOCK: Status managed by reset_completed_purchases_to_ready()


        return results

    def shutdown(self):
        """Clean shutdown — flush in-memory state to disk, stop flush thread.

        The shutdown handler in `app.py` writes `interrupted` for any
        `attempting` rows BEFORE this is called (so the disk file ends up
        consistent), then calls this to drain pending writes and stop the
        background thread.
        """
        try:
            self.state_store.shutdown(timeout=3.0)
        except Exception as e:
            print(f"[PURCHASE] StateStore shutdown error: {e}")
        # Tear down per-account exit-proxy forwarders (no-op if none started).
        try:
            self._shutdown_purchase_forwarders()
        except Exception as e:
            print(f"[PURCHASE] Forwarder shutdown error: {e}")

def main():
    """Test the bulletproof purchase manager"""
    manager = BulletproofPurchaseManager()

    def status_callback(tcin, status, state):
        print(f"[REALTIME] {tcin} status changed to: {status}")

    manager.status_callback = status_callback

    print("Testing Bulletproof Purchase Manager...")

    # Test concurrent purchases
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