"""
Configuration settings for Target Dashboard
"""
import os

# Flask Configuration
FLASK_SECRET_KEY = 'target-dashboard-secret-key-2025'
FLASK_DEBUG = False
FLASK_HOST = '0.0.0.0'
FLASK_PORT = 5000

# Target API Configuration
TARGET_API_BASE_URL = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
TARGET_API_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
STORE_ID = "865"
PRICING_STORE_ID = "865"

# Rate Limiting
API_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT = 10

# Business-critical TCINs for inventory monitoring
MONITORED_TCINS = [
    "1001304528",  # Expected: third-party, in stock
    "94300069",    # Expected: target, out of stock (street date)
    "93859727",    # Expected: target, out of stock (street date)
    "94694203",    # Expected: target, in stock
    "1004021929",  # New: Pokemon SV10 Destined Rivals Sleeved Booster Pack
    "94881750",
    "94693225",
    "14777416"
]

# Debug TCINs (for enhanced logging)
DEBUG_TCINS = ["93859727", "94693225", "94300069", "94694203", "94881750"]

# File Paths
DATA_FILE = 'target_products.json'
TEMPLATES_DIR = 'templates'
DASHBOARD_TEMPLATE = 'dashboard.html'

# Headers for Target API requests
def get_target_headers(tcin: str) -> dict:
    """Generate headers for Target API requests"""
    return {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.target.com",
        "referer": f"https://www.target.com/p/A-{tcin}",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    }