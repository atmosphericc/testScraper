"""Pokemon Center configuration constants — placeholders for future implementation."""

from pathlib import Path

PC_BASE_URL = "https://www.pokemoncenter.com"

CHECK_INTERVAL_MIN = 10
CHECK_INTERVAL_MAX = 20

LOGS_DIR = str(Path(__file__).parent / "logs")
CONFIG_PATH = str(Path(__file__).parent / "pokemon_center_config.json")
