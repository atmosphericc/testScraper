"""
RetailerAdapter — the contract a retailer-specific module must implement to
plug into the resilient stack framework.

Each retailer adapter provides:
  - Constants describing the retailer's tolerances (per-IP RPS, stagger window)
  - Cookie freshness rules (which cookies define a "warm" session, max ages)
  - Stock-check fetch construction (URL, headers, request body for GraphQL)
  - Response parsing (retailer JSON → standard ItemStatus dataclass)
  - Block detection (status code + body patterns that indicate detection)
  - Pre-flight probe (cheap "is this IP clean" check)
  - (Phase 5) Checkout call construction

The framework owns: session pool lifecycle, dispatcher, sweep loop, backpressure,
proxy state machine, watchdog, stats. The adapter owns: everything retailer-specific.

Two retailers in scope right now: Target (existing legacy implementation, not
yet ported) and Walmart (the implementation this framework is built for).
Future retailers (BestBuy, GameStop, Costco) should be a ~200 line adapter
each.

Naming convention: methods that return strings/JS use `build_*`; methods that
parse responses use `parse_*`; methods that test conditions use `is_*`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class ItemStatus:
    """Standard stock-check output across all retailers.

    Adapters parse their native response shape (RedSky JSON, GraphQL data,
    REST API, etc.) into this common shape so the framework's dispatcher,
    cloaking alarm, and on_in_stock callback can stay retailer-agnostic.
    """
    item_id: str                           # TCIN, item_id, sku — whatever the retailer uses
    in_stock: bool
    title: Optional[str] = None
    price: Optional[float] = None
    availability_status: Optional[str] = None  # retailer-specific status string for debugging
    last_checked_at: float = 0.0           # epoch seconds


@dataclass
class FetchResult:
    """Standard fetch-from-inside-Chrome-tab result.

    The dispatcher calls `tab.evaluate(adapter.build_fetch_js(...))` which
    returns a JSON dict the adapter then parses. This wraps the response.
    """
    http_status: int
    body_json: Optional[dict] = None       # parsed JSON if response was JSON
    body_text: Optional[str] = None        # raw text fallback (for block-page HTML)
    elapsed_ms: float = 0.0
    error: Optional[str] = None            # set if fetch itself threw


@runtime_checkable
class RetailerAdapter(Protocol):
    """Protocol every retailer adapter implements.

    Implementation pattern: a class with these attributes/methods. The
    framework imports a singleton instance and never modifies it. Adapter
    instances should be stateless (any per-session/per-IP state belongs in
    the framework's session/proxy state objects).
    """

    # ── retailer identity ────────────────────────────────────────────────
    name: str                              # "target", "walmart", "bestbuy"
    base_url: str                          # "https://www.walmart.com"

    # ── session bootstrap ────────────────────────────────────────────────
    # True if the retailer needs an authenticated session for stock checks
    # to behave sensibly. Walmart=True (rate limits are gentler for logged-in
    # users), Target=False (RedSky is anonymous).
    needs_login: bool

    # URLs each session visits during warmup to accumulate behavioral trust
    # before stock dispatch begins. Order matters; sessions visit serially.
    warmup_urls: list[str]

    # Cookie keys that define "session is fresh enough to dispatch".
    # If any of these is missing or older than its max_age, the session is
    # not eligible for picking until the heartbeat refreshes it.
    # e.g. Walmart: ["_px3", "_abck", "bm_sz"]; Target: ["visitor_id"]
    cookie_freshness_keys: list[str]
    cookie_max_age_seconds: dict[str, int]

    # ── stock-check load profile ─────────────────────────────────────────
    # How many items the retailer accepts in a single bulk request.
    # Target = 28 (RedSky cap is 30, we use 28 with safety margin).
    # Walmart = 1 (GraphQL is per-item).
    chunk_size: int

    # Maximum requests-per-second per IP the retailer's anti-bot tolerates
    # before scoring up. Used by the framework to validate that
    # (target_aggregate_rps / num_sessions) <= per_ip_rps_ceiling at startup.
    per_ip_rps_ceiling: float

    # Seconds to spread the launch of N persistent Chromes over, so they don't
    # appear as a coordinated burst arrival to the retailer's session-arrival
    # detector. Target=600 (Shape), Walmart=1200 (PerimeterX is tighter).
    chrome_stagger_seconds: int

    # Maximum age (seconds) any session's freshness-keyed cookies can have
    # at dispatch time. The framework computes per-session refresh interval
    # = max_session_cookie_age_seconds * N, so with smaller N each session
    # refreshes more often (there are fewer sessions round-robining the
    # heartbeat slot).
    #
    # Walmart: ~50s (one 10s safety margin under _px3's ~60s TTL on
    #               checkout-sensitive pages — at N=2 that's a refresh
    #               every 100s, at N=16 every 800s, _px3 stays fresh
    #               either way)
    # Target:  ~1800s (visitor_id is stable for hours; Akamai cookies
    #                  rotate slowly via tab traffic; no _px3 analog)
    max_session_cookie_age_seconds: int

    # ── per-IP park/burn policy ──────────────────────────────────────────
    # Different anti-bot systems have different block-recovery semantics.
    # Shape/Akamai (Target): observed natural recovery ~3 hours after a
    # mass-burn event; conservative parking minimizes IP churn.
    # PerimeterX (Walmart): observed 14-min recovery in dev gate; can
    # park aggressively + unpark fast.
    #
    # Consecutive 401/403s before an IP is parked.
    park_after_403_streak: int

    # Seconds an IP stays parked before background loop retests it.
    park_duration_seconds: int

    # After this many park cycles on the same IP, mark it permanently
    # burned (no longer retested by the background loop).
    burn_after_parks: int

    # ── stock-check fetch construction ───────────────────────────────────
    def build_fetch_js(self, items: list[str]) -> str:
        """Return the JavaScript string the dispatcher will run inside the
        long-lived tab via tab.evaluate(). The JS must:
          - Construct the retailer's stock-check request
          - await fetch(...) it (inheriting the tab's cookies + JA3/JA4)
          - Return an object: {__http_status, __body_json, __body_text}

        items is a list of length up to chunk_size.
        """
        ...

    def parse_response(self, result: FetchResult, items: list[str]) -> list[ItemStatus]:
        """Translate the retailer's response JSON into ItemStatus list.

        Called regardless of HTTP status — adapter decides how to interpret
        non-200s (some retailers return 200 with error-shaped body, some
        return 403 with valid item data).
        """
        ...

    def is_blocked_response(self, result: FetchResult) -> bool:
        """True if the response indicates detection / block (challenge page,
        /blocked redirect, captcha, account-flag rate limit). The framework
        uses this to trigger park-the-IP logic via ProxyState.
        """
        ...

    # ── pre-flight ───────────────────────────────────────────────────────
    def preflight_probe_url(self) -> str:
        """URL of a lightweight retailer endpoint used to verify a proxy IP
        is clean at startup. Should be cheap (HEAD-able if possible, GET
        otherwise). Walmart: /api/v3/product/locate or similar; Target: a
        RedSky bulk with one TCIN.
        """
        ...

    def is_preflight_clean(self, http_status: int, body_text: str) -> bool:
        """Decide if a pre-flight response indicates a usable IP. Adapter
        knows what "clean" looks like for its retailer (200 vs 403/456/redirect).
        """
        ...

    # ── checkout (Phase 5 — optional for adapters that don't yet support
    # hybrid checkout). Adapters that haven't implemented these should
    # raise NotImplementedError; the framework will fall back to DOM
    # checkout in the retailer's existing executor when these aren't ready.
    # ────────────────────────────────────────────────────────────────────
    def build_atc_js(self, item_id: str, qty: int, cart_context: dict) -> str:
        """JS for adding to cart via API mutation, inside a checkout session
        tab. cart_context contains retailer-specific state (cartId, offerId,
        etc.) the adapter populated from `read_cart_context`.
        """
        ...

    def build_place_order_js(self, order_context: dict) -> str:
        """JS for the final place-order mutation."""
        ...

    def apq_full_query(self, operation_name: str) -> Optional[str]:
        """For GraphQL retailers using Apollo persisted queries: return the
        full query body for the named operation. Used as fallback when the
        cached persisted-query hash returns PersistedQueryNotFound.
        Returns None if the retailer doesn't use APQ.
        """
        ...
