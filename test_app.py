#!/usr/bin/env python3
"""
Test Mode Dashboard - Stops before final purchase button for safe testing
Same as app.py but sets TEST_MODE=true automatically
"""

import os
import sys

# Set TEST_MODE before importing app
os.environ['TEST_MODE'] = 'true'

# Import module with alias to avoid conflict with Flask app named 'app'
import app as app_module
from app import *

if __name__ == '__main__':
    print("=" * 60)
    print("TEST MODE DASHBOARD - SAFE TESTING ENABLED")
    print("=" * 60)
    print("[MODE] TEST - Will stop before final purchase button")
    print("[SAFETY] No actual purchases will be completed")
    print("[TESTING] Full flow tested: cart -> checkout -> payment -> STOP")
    print("[FEATURES] Real-time updates, infinite purchase loops")
    print("[REALTIME] Server-Sent Events for immediate UI updates")
    print("=" * 60)
    print()
    print("INFO: For PRODUCTION mode (actual purchases), use: python app.py")
    print("=" * 60)

    # Initialize
    load_activity_log()
    add_activity_log("TEST MODE: Dashboard initialized (safe testing mode)", "info", "system")

    # NOTE: Using REAL API calls to Target for live stock data
    # TEST_MODE behavior:
    # - Stops before clicking final "Place Order" button (safe testing)
    # - Clears cart after reaching final step
    # - Resets ALL completed purchases to "ready" on every cycle
    # - This allows endless loop testing with real products:
    #   * If product in stock: purchases it (stops at final button), clears cart, resets to ready
    #   * Next cycle: if still in stock, purchases again → endless loop!
    print("[TEST_MODE] Using REAL API calls for live stock data")
    print("[TEST_MODE] Endless loop enabled: purchases reset after each iteration")
    add_activity_log("TEST MODE: Using real API with endless loop (resets all purchases)", "info", "system")

    # CRITICAL: Initialize session and check login BEFORE starting monitoring
    print("\n" + "=" * 60)
    print("SESSION CHECK - Verifying login status")
    print("=" * 60)

    import asyncio
    import signal
    import sys
    import concurrent.futures
    import threading

    # Background initialization function (runs in separate thread)
    def background_init():
        """Initialize browser and session in background thread - doesn't block Flask startup"""
        try:
            # Update status
            with shared_data.lock:
                shared_data.initialization_status = "Initializing browser..."
            print("[BACKGROUND] ═══════════════════════════════════════════════")
            print("[BACKGROUND] Starting background initialization...")
            print("[BACKGROUND] ═══════════════════════════════════════════════")

            # Initialize global purchase manager (this will initialize session system)
            print("[BACKGROUND] [STEP 1/4] Initializing global purchase manager...")
            init_error = None
            try:
                initialize_global_purchase_manager()
            except concurrent.futures.TimeoutError as e:
                print(f"[BACKGROUND] [WARNING] Purchase manager initialization timed out: {e}")
                print(f"[BACKGROUND] [WARNING] Will continue with monitoring in degraded mode")
                init_error = "timeout"
            except Exception as e:
                print(f"[BACKGROUND] [ERROR] Purchase manager initialization failed: {e}")
                import traceback
                traceback.print_exc()
                print(f"[BACKGROUND] [WARNING] Will continue with monitoring anyway")
                init_error = str(e)

            # Verify global purchase manager was created
            print(f"[BACKGROUND] [VERIFY] global_purchase_manager: {global_purchase_manager}")
            print(f"[BACKGROUND] [VERIFY] global_event_loop: {app_module.global_event_loop}")

            if global_purchase_manager:
                print(f"[BACKGROUND] [VERIFY] session_initialized: {global_purchase_manager.session_initialized}")
                print(f"[BACKGROUND] [VERIFY] use_real_purchasing: {global_purchase_manager.use_real_purchasing}")
                print(f"[BACKGROUND] [VERIFY] session_manager: {global_purchase_manager.session_manager}")
            else:
                print("[BACKGROUND] [ERROR] global_purchase_manager is None!")

            # Update status
            with shared_data.lock:
                shared_data.initialization_status = "Checking login status..."
            print("[BACKGROUND] [STEP 2/4] Checking login status...")

            # Check login status using global event loop
            async def check_login():
                """Verify login status using global purchase manager"""
                try:
                    # Access through app_module to get current value
                    purchase_mgr = app_module.global_purchase_manager
                    if not purchase_mgr or not purchase_mgr.session_manager:
                        print("[ERROR] Session manager not available")
                        return False

                    # Check if logged in by validating session
                    is_logged_in = await purchase_mgr.session_manager._validate_session(
                        attempt_recovery=False,  # Don't auto-login
                        skip_initial_navigation=True  # Already at target.com
                    )

                    return is_logged_in

                except Exception as e:
                    print(f"[ERROR] Login check failed: {e}")
                    import traceback
                    traceback.print_exc()
                    return False

            # Run login check (skip if initialization failed)
            is_logged_in = False
            login_check_error = None

            if not init_error and app_module.global_event_loop:
                try:
                    print("[BACKGROUND] [STEP 2/4] Running async login check (timeout: 90s)...")
                    future = asyncio.run_coroutine_threadsafe(check_login(), app_module.global_event_loop)
                    is_logged_in = future.result(timeout=90)
                    print(f"[BACKGROUND] [STEP 2/4] Login check result: {is_logged_in}")
                except concurrent.futures.TimeoutError as e:
                    print(f"[BACKGROUND] [WARNING] Login check timed out after 90s: {e}")
                    print(f"[BACKGROUND] [WARNING] Will continue with monitoring anyway")
                    login_check_error = "timeout"
                except Exception as e:
                    print(f"[BACKGROUND] [ERROR] Login check failed: {e}")
                    import traceback
                    traceback.print_exc()
                    login_check_error = str(e)
            else:
                print(f"[BACKGROUND] [STEP 2/4] Skipping login check (init_error={init_error})")
                login_check_error = init_error

            # CRITICAL: Start monitoring REGARDLESS of login status
            # Even if not logged in, monitoring can run in mock mode
            print("[BACKGROUND] [STEP 3/4] Starting monitoring system...")
            print("[BACKGROUND] ═══════════════════════════════════════════════")

            if is_logged_in:
                print("✅ LOGGED IN TO TARGET.COM")
                print("Starting monitoring with REAL purchase mode...")
            else:
                print("⚠️  NOT LOGGED IN TO TARGET.COM")
                print("Starting monitoring anyway (will use MOCK mode)...")
                print("Browser window is open - you can log in manually")

            # Start monitoring (this will work even if not logged in)
            print("[BACKGROUND] Calling start_monitoring()...")
            start_monitoring()
            print("[BACKGROUND] [OK] start_monitoring() completed")

            # Verify monitoring started
            print(f"[BACKGROUND] [VERIFY] monitor_running: {shared_data.monitor_running}")

            # Update status based on login state
            print("[BACKGROUND] [STEP 4/4] Finalizing initialization...")

            if is_logged_in:
                with shared_data.lock:
                    shared_data.initialization_complete = True
                    shared_data.initialization_status = "Ready - monitoring active (logged in)"
                add_activity_log("System ready - monitoring active with real purchases", "success", "system")
                print("=" * 60)
                print("✅ INITIALIZATION COMPLETE - LOGGED IN")
                print("=" * 60)
            else:
                with shared_data.lock:
                    shared_data.initialization_complete = True
                    shared_data.initialization_status = "Monitoring active (not logged in - mock mode)"
                    if login_check_error:
                        shared_data.initialization_error = f"Login check failed: {login_check_error}"
                add_activity_log("Monitoring active in mock mode - log in to enable real purchases", "warning", "system")
                print("=" * 60)
                print("✅ INITIALIZATION COMPLETE - MOCK MODE")
                print("⚠️  Please log in manually to enable real purchases")
                print("=" * 60)

            print("[BACKGROUND] Background initialization finished successfully")
            print("[BACKGROUND] ═══════════════════════════════════════════════")

        except Exception as e:
            print(f"[BACKGROUND] [ERROR] Initialization error: {e}")
            import traceback
            traceback.print_exc()

            # FALLBACK: Still try to start monitoring even if initialization failed
            print("[BACKGROUND] [FALLBACK] Attempting to start monitoring despite error...")
            try:
                start_monitoring()
                print("[BACKGROUND] [FALLBACK] Monitoring started successfully")

                with shared_data.lock:
                    shared_data.initialization_complete = True
                    shared_data.initialization_status = "Monitoring active (initialization had errors)"
                    shared_data.initialization_error = str(e)

                add_activity_log(f"Monitoring started despite initialization error: {str(e)}", "warning", "system")
            except Exception as monitor_error:
                print(f"[BACKGROUND] [FATAL] Could not start monitoring: {monitor_error}")
                traceback.print_exc()

                with shared_data.lock:
                    shared_data.initialization_complete = True
                    shared_data.initialization_status = "Error - monitoring failed"
                    shared_data.initialization_error = f"Init error: {str(e)}, Monitor error: {str(monitor_error)}"

                add_activity_log(f"Fatal error - monitoring failed: {str(e)}", "error", "system")

    # ── Graceful shutdown ──────────────────────────────────────────────────────
    import atexit
    from src.session.session_manager import _chrome_pid as _initial_chrome_pid

    def _kill_browser_now():
        """Kill the entire Chrome process tree — works from signal handlers and atexit."""
        import subprocess, sys as _sys
        from src.session import session_manager as _sm_mod

        # Try via SessionManager first (has the process ref)
        sm = global_purchase_manager.session_manager if global_purchase_manager else None
        if sm:
            sm.close_browser_sync()
            return

        # Fallback: use the module-level PID we stored at browser launch
        pid = _sm_mod._chrome_pid
        if pid and _sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5
                )
                print(f"[CLEANUP] taskkill /F /T /PID {pid} (fallback)")
            except Exception as e:
                print(f"[CLEANUP] fallback taskkill failed: {e}")

    atexit.register(_kill_browser_now)   # fires when sys.exit() is called

    def graceful_shutdown(signum, frame):
        """Save session, close browser, and exit when Ctrl+C / SIGTERM is received."""
        print("\n\n" + "=" * 60)
        print("SHUTTING DOWN - Saving session and closing browser...")
        print("=" * 60)

        loop = app_module.global_event_loop
        sm   = global_purchase_manager.session_manager if global_purchase_manager else None

        # Save session via async (best effort, skip if loop not running)
        if sm and loop and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    sm.save_session_state(), loop
                )
                future.result(timeout=5)
                print("[OK] Session saved")
            except Exception as e:
                print(f"[WARNING] Session save error: {e}")

        # Kill browser synchronously — uses taskkill /F /T to kill entire Chrome tree
        _kill_browser_now()

        print("=" * 60)
        print("Goodbye!")
        print("=" * 60)
        # os._exit bypasses Waitress cleanup that could block sys.exit
        os._exit(0)

    # Register signal handlers for Ctrl+C and other termination signals
    signal.signal(signal.SIGINT,  graceful_shutdown)   # Ctrl+C
    signal.signal(signal.SIGTERM, graceful_shutdown)   # kill / Task Manager
    if sys.platform == "win32":
        try:
            signal.signal(signal.SIGBREAK, graceful_shutdown)  # Ctrl+Break on Windows
        except (AttributeError, OSError):
            pass

    # Start background initialization thread (doesn't block Flask startup)
    print("[STARTUP] Starting background initialization thread...")
    print("[STARTUP] Flask will start IMMEDIATELY - dashboard accessible at http://127.0.0.1:5001")
    print("[STARTUP] Browser initialization continues in background...")
    print("=" * 60)

    init_thread = threading.Thread(target=background_init, daemon=True, name="BackgroundInit")
    init_thread.start()

    # Start Flask IMMEDIATELY (don't wait for initialization)
    # Background thread will initialize browser, check login, and start monitoring
    # FIX: Use Waitress for proper SSE streaming (no buffering)
    print("[FLASK] Starting Flask server NOW...")
    print("[WAITRESS] Starting production WSGI server for SSE streaming...")
    print("[WAITRESS] Dashboard accessible at http://127.0.0.1:5001")
    from waitress import serve
    serve(app, host='127.0.0.1', port=5001, threads=6, channel_timeout=300)
