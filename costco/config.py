"""Costco configuration constants — placeholders for future implementation."""

from pathlib import Path

COSTCO_BASE_URL = "https://www.costco.com"

CHECK_INTERVAL_MIN = 10
CHECK_INTERVAL_MAX = 20

LOGS_DIR = str(Path(__file__).parent / "logs")
CONFIG_PATH = str(Path(__file__).parent / "costco_config.json")
