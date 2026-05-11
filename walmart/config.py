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

# GraphQL operation hash — auto-updated at runtime by the session harvester via CDP.
# This value is the static fallback used until the first ItemByIdBtf request is intercepted.
# Override via WALMART_GRAPHQL_HASH env var when the static default goes stale (HTTP 400 on stock checks).
GRAPHQL_HASH = os.environ.get(
    "WALMART_GRAPHQL_HASH",
    "20d116c298a901b29763c37a4aaf8b37aeb1654e4f971cd11a7fe9de2ceab027",
)

# ATF (Above The Fold) hash — auto-discovered at runtime by the session harvester.
# Starts as None; stock_monitor uses it as a fallback when BTF returns no product data
# (common for preorder items). No manual update needed.
GRAPHQL_HASH_ATF: "str | None" = None

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

WALMART_BASE_URL = "https://www.walmart.com"
WALMART_LOGIN_URL = "https://www.walmart.com/account/login"
WALMART_CART_URL = "https://www.walmart.com/cart"
WALMART_CHECKOUT_URL = "https://www.walmart.com/checkout"
WALMART_ACCOUNT_URL = "https://www.walmart.com/account"

# ---------------------------------------------------------------------------
# Proxy pool sizes
# ---------------------------------------------------------------------------

# All proxies are used for monitoring (one worker per proxy, staggered).
# A subset is reserved sticky for checkout sessions.
CHECKOUT_PROXY_POOL_SIZE = 12   # max sticky checkout proxies carved out of the loaded pool
MONITOR_PROXY_POOL_SIZE = 50    # soft limit reference — actual pool size = all loaded proxies

PROXY_COOLDOWN_SECONDS = 300    # bench a proxy for 5 min after 403/429
PROXY_ERROR_RATE_THRESHOLD = 0.10  # bench if error rate > 10% over last 100 reqs

# ---------------------------------------------------------------------------
# Browser / Patchright
# ---------------------------------------------------------------------------

HEADLESS = False          # Patchright recommendation: False reduces detection risk
BROWSER_CHANNEL = "chrome"  # Use real Chrome, not bundled Chromium
_WALMART_DIR = Path(__file__).parent
PROFILE_DIR = str(_WALMART_DIR.parent / "walmart-profile")
COOKIES_FILE = str(_WALMART_DIR.parent / "walmart-profile" / "cookies.json")
LOGS_DIR = str(_WALMART_DIR / "logs")

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

# Fast-drop mode: eliminates mandatory human think-time delays during checkout.
# Set FAST_DROP_MODE=1 for high-demand drops where seconds matter (Pokemon Wednesday, GPU releases).
# Default 0 keeps human-like think-times for stealth in normal operation.
FAST_DROP_MODE = os.environ.get("FAST_DROP_MODE", "0") == "1"

# CVV for saved card — loaded from environment
CARD_CVV = os.environ.get("WALMART_CVV", "")

# Account credentials — only used for browser-restart re-login fallback.
# Normal startup uses saved cookies from walmart_relogin.py (no env vars needed).
# Set these only if you want automatic re-login on browser crash recovery.
WALMART_EMAIL = os.environ.get("WALMART_EMAIL", "")
WALMART_PASSWORD = os.environ.get("WALMART_PASSWORD", "")


# ---------------------------------------------------------------------------
# Environment variable accessor functions (read at call time, not import time)
# ---------------------------------------------------------------------------

# Lock for the GraphQL-hash setter/getter pair. CDP RequestWillBeSent
# handlers fire on zendriver's internal thread pool, not the main asyncio
# loop, so two concurrent intercepts of different hash values can race on
# the read-modify-write of the module-level globals. Without the lock,
# both threads pass the "!=" check, both write (last writer wins), and
# both log the INFO line — producing a duplicate-update entry and silently
# discarding one of the values.
_graphql_hash_lock = threading.Lock()


def set_graphql_hash_atf(hash_value: str) -> None:
    """Called by the session harvester when it intercepts an ItemByIdAtf request."""
    global GRAPHQL_HASH_ATF
    if not hash_value:
        return
    with _graphql_hash_lock:
        if GRAPHQL_HASH_ATF != hash_value:
            GRAPHQL_HASH_ATF = hash_value
            logger.info("[CONFIG] ATF GraphQL hash auto-updated: %s", hash_value)


def get_graphql_hash_atf() -> "str | None":
    with _graphql_hash_lock:
        return GRAPHQL_HASH_ATF


def set_graphql_hash_btf(hash_value: str) -> None:
    """Called by the session harvester when it intercepts an ItemByIdBtf request."""
    global GRAPHQL_HASH
    if not hash_value:
        return
    with _graphql_hash_lock:
        if GRAPHQL_HASH != hash_value:
            GRAPHQL_HASH = hash_value
            logger.info("[CONFIG] BTF GraphQL hash auto-updated: %s", hash_value)


def get_graphql_hash_btf() -> str:
    with _graphql_hash_lock:
        return GRAPHQL_HASH


def get_checkout_mode() -> str:
    return os.environ.get("CHECKOUT_MODE", "TEST")


def get_final_purchase() -> str:
    return os.environ.get("FINAL_PURCHASE", "NO")


def get_card_cvv() -> str:
    """Return CVV at call time so a late-set WALMART_CVV env var is picked up."""
    return os.environ.get("WALMART_CVV", "") or CARD_CVV

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
    """Load walmart_config.json, returning {'products': []} if not found or corrupted."""
    with _config_lock:
        for path in _CONFIG_PATHS:
            p = Path(path)
            if p.exists():
                try:
                    with open(p, "r") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    # Corrupted file (e.g., killed mid-write before the atomic-rename
                    # patch landed) — log + fall through to fallback rather than crash
                    # the dashboard's initial render.
                    logger.error(
                        "[CONFIG] get_config: %s is unreadable (%s) — using empty fallback",
                        path, e,
                    )
                    return {"products": []}
                if not isinstance(data, dict) or not isinstance(data.get("products"), list):
                    logger.warning(
                        "[CONFIG] get_config: invalid structure in %s — expected dict with 'products' list; using fallback",
                        path,
                    )
                    return {"products": []}
                return data
    return {"products": []}


def _atomic_write_json(p: Path, data: dict) -> None:
    """
    Write `data` to `p` atomically via write-to-temp + os.replace.
    A kill mid-write cannot corrupt `p`: either the rename completes and the
    file is intact, or the rename never runs and the tempfile is orphaned
    (cleaned up on next save). The fsync ensures the bytes hit disk before
    the rename swaps the inode.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        # Tempfile may be orphaned — best-effort cleanup so /tmp / cwd doesn't grow
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def save_config(data: dict) -> None:
    """Persist updated config back to walmart_config.json (atomic write)."""
    with _config_lock:
        for path in _CONFIG_PATHS:
            p = Path(path)
            if p.exists():
                _atomic_write_json(p, data)
                _verify_write(p)
                return
        # Write to first path if none exist yet
        p = Path(_CONFIG_PATHS[0])
        _atomic_write_json(p, data)
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
