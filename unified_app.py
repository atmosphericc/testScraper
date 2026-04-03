#!/usr/bin/env python3
"""
Unified AIO Bot Dashboard — single Flask server on port 5000.

Combines Target (app.py) and Walmart (walmart/blueprint.py) into one process.
Run this instead of app.py or walmart_app.py.

Usage:
    python unified_app.py

Environment variables:
    WALMART_EMAIL      Walmart account email
    WALMART_PASSWORD   Walmart account password
    WALMART_CVV        Card CVV for Walmart checkout
    CHECKOUT_MODE      TEST (default) or PRODUCTION
    FINAL_PURCHASE     YES to actually place orders (requires PRODUCTION mode)
"""

import ctypes
import logging
import os
import platform
import sys

# Load .env file if present (WALMART_EMAIL, WALMART_PASSWORD, WALMART_CVV, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

# ---------------------------------------------------------------------------
# Prevent system sleep (Windows + macOS)
# ---------------------------------------------------------------------------
_caffeinate_proc = None
if platform.system() == "Windows":
    _ES_CONTINUOUS      = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
elif platform.system() == "Darwin":
    import subprocess as _subprocess
    _caffeinate_proc = _subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

# ---------------------------------------------------------------------------
# Import Target Flask app (all Target routes mount at /)
# ---------------------------------------------------------------------------
from app import app  # noqa: E402  (sleep prevention must run before imports)

# ---------------------------------------------------------------------------
# Import and register Walmart Blueprint (routes mount at /walmart/)
# ---------------------------------------------------------------------------
from walmart.blueprint import walmart_bp, start_manager as _start_walmart_manager

app.register_blueprint(walmart_bp)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import datetime

    print("=" * 60)
    print("  AIO BOT — UNIFIED DASHBOARD")
    print("=" * 60)
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Platform: {platform.system()}")
    print(f"  Sleep prevention: {'caffeinate (macOS)' if _caffeinate_proc else 'SetThreadExecutionState (Windows)' if platform.system() == 'Windows' else 'none'}")
    print("  Retailers: Target (/) | Walmart (/walmart/)")
    print(f"  Checkout mode: {os.environ.get('CHECKOUT_MODE', 'TEST')}")
    print("=" * 60)

    # Start Target background workers
    # (app.py's __main__ block normally does this; we replicate those calls here)
    from app import (  # type: ignore
        load_activity_log,
        start_monitoring,
        activity_log_persistence_worker,
    )
    load_activity_log()
    activity_log_persistence_worker.start()
    start_monitoring()
    logger.info("[UNIFIED] Target background workers started")

    # Start Walmart background manager
    _start_walmart_manager()
    logger.info("[UNIFIED] Walmart manager started")

    # Serve
    try:
        from waitress import serve
        logger.info("[UNIFIED] Serving on http://0.0.0.0:5000")
        serve(app, host="0.0.0.0", port=5000, threads=8)
    except ImportError:
        logger.warning("[UNIFIED] waitress not installed — falling back to Flask dev server")
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        import os as _os
        print("\n[UNIFIED] Shutting down...")
        _os._exit(0)
