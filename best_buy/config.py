"""Best Buy configuration constants — placeholders for future implementation."""

import os
from pathlib import Path

BBY_BASE_URL = "https://www.bestbuy.com"
BBY_PRODUCT_API = "https://api.bestbuy.com/v1/products"

CHECK_INTERVAL_MIN = 10  # seconds
CHECK_INTERVAL_MAX = 20  # seconds

LOGS_DIR = str(Path(__file__).parent / "logs")
CONFIG_PATH = str(Path(__file__).parent / "best_buy_config.json")
