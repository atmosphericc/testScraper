"""
Retailer plugin interface.

Every retailer module must implement RetailerBase to plug into the
unified dashboard. Stub implementations return safe no-op values.
"""

from abc import ABC, abstractmethod
from typing import Optional, Callable


class RetailerBase(ABC):
    """Abstract base class for all retailer automation modules."""

    # Identity (must be set as class attributes in subclasses)
    retailer_id: str    # "target", "walmart", "best_buy", "costco", "pokemon_center"
    display_name: str   # Display name shown in the dashboard tab
    url_prefix: str     # Flask Blueprint prefix e.g. "/best_buy"
    is_stub: bool = False  # True for placeholder/not-yet-implemented retailers

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def start(self) -> None:
        """Start monitoring and purchase automation in background threads."""

    @abstractmethod
    def stop(self) -> None:
        """Gracefully stop all background work."""

    # ------------------------------------------------------------------
    # Dashboard data
    # ------------------------------------------------------------------

    @abstractmethod
    def get_status(self) -> dict:
        """
        Return a JSON-serializable status dict.
        Required keys:
            running: bool
            products: list of {
                id: str,
                name: str,
                state: str,   # MONITORING | PURCHASING | SUCCESS | FAILED | IN_QUEUE
                priority: int,
                last_check: str | None,
                in_stock: bool,
            }
            circuit_open: bool (optional)
            test_mode: bool (optional)
        """

    @abstractmethod
    def get_activity_log(self) -> list[dict]:
        """Return list of {"time": str, "message": str} dicts, newest first."""

    # ------------------------------------------------------------------
    # Product management
    # ------------------------------------------------------------------

    @abstractmethod
    def add_product(self, product_id: str, name: str,
                    max_price: Optional[float], priority: int) -> dict:
        """Add a product. Returns {"ok": bool, "error": str|None}."""

    @abstractmethod
    def remove_product(self, product_id: str) -> dict:
        """Remove a product. Returns {"ok": bool, "error": str|None}."""

    # ------------------------------------------------------------------
    # Test mode
    # ------------------------------------------------------------------

    @abstractmethod
    def set_test_mode(self, enabled: bool) -> None:
        """Enable or disable test mode (stops before Place Order when True)."""

    # ------------------------------------------------------------------
    # SSE integration
    # ------------------------------------------------------------------

    def register_sse_callback(self, callback: Callable[[str, dict], None]) -> None:
        """
        Register a function called on every status event:
            callback(event_type: str, data: dict)

        event_type examples: "activity", "stock_update", "state_change"
        """
        self._sse_callback = callback


class StubRetailer(RetailerBase):
    """
    Base class for retailers that are not yet implemented.
    Shows as 'Coming Soon' in the dashboard with all controls disabled.
    Subclasses only need to set retailer_id, display_name, and url_prefix.
    """

    is_stub = True

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_status(self) -> dict:
        return {
            "running": False,
            "stub": True,
            "products": [],
            "circuit_open": False,
            "test_mode": False,
        }

    def get_activity_log(self) -> list[dict]:
        return []

    def add_product(self, product_id: str, name: str,
                    max_price: Optional[float], priority: int) -> dict:
        return {"ok": False, "error": "Not implemented"}

    def remove_product(self, product_id: str) -> dict:
        return {"ok": False, "error": "Not implemented"}

    def set_test_mode(self, enabled: bool) -> None:
        pass
