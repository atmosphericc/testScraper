"""
Walmart bot configuration — constants and config loader.
"""

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Walmart API constants (shared with stock_check.py)
# ---------------------------------------------------------------------------

WALMART_SELLER_ID = "F55CDC31AB754BB68FE0851F0F1F2C96"

# GraphQL operation hash — may change when Walmart deploys a new frontend build.
# If stock_monitor.py starts getting 400/404 responses, update GRAPHQL_HASH here.
# stock_check.py imports this value from config — only one place to update.
GRAPHQL_HASH = "20d116c298a901b29763c37a4aaf8b37aeb1654e4f971cd11a7fe9de2ceab027"

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

WALMART_BASE_URL = "https://www.walmart.com"
WALMART_LOGIN_URL = "https://www.walmart.com/account/login"
WALMART_CART_URL = "https://www.walmart.com/cart"
WALMART_CHECKOUT_URL = "https://www.walmart.com/checkout"
WALMART_ACCOUNT_URL = "https://www.walmart.com/account"

# ---------------------------------------------------------------------------
# Stock monitoring
# ---------------------------------------------------------------------------

CHECK_INTERVAL_MIN = 10   # seconds
CHECK_INTERVAL_MAX = 20   # seconds — jitter applied between min and max

# ---------------------------------------------------------------------------
# Proxy pool sizes (out of 50 total proxies)
# ---------------------------------------------------------------------------

MONITOR_PROXY_POOL_SIZE = 38    # rotating, used for stock API calls
CHECKOUT_PROXY_POOL_SIZE = 12   # sticky, one proxy per checkout session

PROXY_COOLDOWN_SECONDS = 300    # bench a proxy for 5 min after 403/429
PROXY_ERROR_RATE_THRESHOLD = 0.10  # bench if error rate > 10% over last 100 reqs

# ---------------------------------------------------------------------------
# Browser / Patchright
# ---------------------------------------------------------------------------

HEADLESS = False          # Patchright recommendation: False reduces detection risk
BROWSER_CHANNEL = "chrome"  # Use real Chrome, not bundled Chromium
PROFILE_DIR = "./walmart-profile"
COOKIES_FILE = "./walmart-profile/cookies.json"
LOGS_DIR = "./walmart/logs"

# ---------------------------------------------------------------------------
# Session / PerimeterX
# ---------------------------------------------------------------------------

# _px3 clearance cookie expires in ~60s on high-security Walmart pages.
# Re-warm the session if the cookie is older than this threshold.
PX3_MAX_AGE_SECONDS = 50

# Keep-alive: validate session every N seconds when idle
SESSION_VALIDATE_INTERVAL = 600   # 10 minutes
SESSION_MAX_IDLE = 1800           # 30 minutes before forced re-login

# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

QUEUE_POLL_INTERVAL = 5     # seconds between queue status checks
QUEUE_TIMEOUT = 1800        # 30 minutes — abandon if not passed through

# ---------------------------------------------------------------------------
# Purchase safety
# ---------------------------------------------------------------------------

# Set CHECKOUT_MODE=PRODUCTION and FINAL_PURCHASE=YES in environment to actually
# place orders. Defaults to test mode (stops before Place Order).
CHECKOUT_MODE = os.environ.get("CHECKOUT_MODE", "TEST")
FINAL_PURCHASE = os.environ.get("FINAL_PURCHASE", "NO")

# CVV for saved card — override via environment variable
CARD_CVV = os.environ.get("WALMART_CVV", "")


# ---------------------------------------------------------------------------
# Environment variable accessor functions (read at call time, not import time)
# ---------------------------------------------------------------------------

def get_checkout_mode() -> str:
    return os.environ.get("CHECKOUT_MODE", "TEST")


def get_final_purchase() -> str:
    return os.environ.get("FINAL_PURCHASE", "NO")


def get_card_cvv() -> str:
    return os.environ.get("WALMART_CVV", "")

# Circuit breaker: pause purchase attempts for this many seconds after N failures
CIRCUIT_BREAKER_FAILURES = 3
CIRCUIT_BREAKER_PAUSE = 60

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_CONFIG_PATHS = [
    "walmart/walmart_config.json",
    "./walmart_config.json",
]

_config_lock = threading.Lock()


def get_config() -> dict:
    """Load walmart_config.json, returning {'products': []} if not found."""
    with _config_lock:
        for path in _CONFIG_PATHS:
            p = Path(path)
            if p.exists():
                with open(p, "r") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get("products"), list):
                    logger.warning(
                        "[CONFIG] get_config: invalid structure in %s — expected dict with 'products' list; using fallback",
                        path,
                    )
                    return {"products": []}
                return data
    return {"products": []}


def save_config(data: dict) -> None:
    """Persist updated config back to walmart_config.json."""
    with _config_lock:
        for path in _CONFIG_PATHS:
            p = Path(path)
            if p.exists():
                with open(p, "w") as f:
                    json.dump(data, f, indent=2)
                _verify_write(p)
                return
        # Write to first path if none exist yet
        p = Path(_CONFIG_PATHS[0])
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        _verify_write(p)


def _verify_write(p: Path) -> None:
    """Read the just-written file back and confirm it parses as valid JSON."""
    try:
        with open(p, "r") as f:
            json.load(f)
    except Exception:
        logger.critical(
            "[CONFIG] save_config write verification failed — disk may be full"
        )


def get_enabled_products() -> list[dict]:
    """Return only products with enabled=True."""
    return [p for p in get_config().get("products", []) if p.get("enabled", True)]
