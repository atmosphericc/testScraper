"""
WalmartAdapter — implements the RetailerAdapter protocol for Walmart.com.

The adapter encapsulates everything Walmart-specific that the resilient stack
framework (src/stack/) needs to know:
  - Stock-check transport: HTML page load + __NEXT_DATA__ extraction
  - Cookie freshness: _px3, _abck, bm_sz with their TTLs
  - Block detection: /blocked redirect, HTTP 4xx patterns
  - Behavioral tolerances: 0.5 RPS/IP ceiling, 1200s Chrome stagger
  - Checkout (Phase 5): GraphQL mutations via walmart/checkout_api.py

================================================================================
TRANSPORT DECISION: HTML scrape, NOT GraphQL — researched 2026-05-16
================================================================================

The stock-check transport is `GET /ip/<item_id>` (HTML page load) followed
by extracting __NEXT_DATA__ from the response HTML, NOT a direct POST to
/orchestra/pdp/graphql/ItemByIdBtf/<hash>. This is a deliberate choice
matching what every documented commercial Walmart bot does:

  - PhoenixBot (open source, the reference implementation everyone forks):
    requests.get(product_url) → parse <script id="item"> JSON → check for
    "add to cart". Pure HTML scrape.
  - bird-bot (another widely-forked open implementation): identical pattern.
  - Every 2026 scraping guide (Scrapfly, Decodo, Scrapingdog, ScrapeOps,
    Oxylabs) recommends parsing __NEXT_DATA__. None recommend hitting the
    /orchestra/pdp/graphql/ family directly.
  - Refract, Stellar AIO, MEKAIO, Wrath, Hayha, Valor, Tohru, Cybersole,
    Splashforce, Project Destroyer, Koi, Ominous, Fluid: no public evidence
    (changelog, Discord leak, decompile, Reddit) of any of them hitting the
    GraphQL endpoint directly for monitoring. Their docs use abstracted
    vocabulary ("PID monitoring", "Offer ID monitoring") that's consistent
    with HTML-page-scrape behavior.

Why everyone converged on HTML scrape:
  1. The persisted-query hash rotates weekly. APQ-fallback infra is
     expensive to build robustly; HTML has no analogous problem.
  2. The HTML page IS the GraphQL response, pre-rendered. Walmart's SSR
     runs the GraphQL query server-side and embeds the result in
     __NEXT_DATA__. You get the same data either way.
  3. HTML page-loads look like real users browsing. Lower detection cost
     per request than a cold GraphQL POST, especially on ISP proxies.
  4. The actual restock-detection signal (`availabilityStatusV2.value`)
     is in both responses. Zero speed advantage from GraphQL for the
     fields the bot actually uses.

Where this implementation goes BEYOND what top bots publicly do: multi-session
pool with N=16 Chromes pinned 1:1 to proxies, each session running
tab.evaluate(fetch('/ip/<id>')) instead of a single browser or tls-client
loop. This gives real JA3/JA4 + real cookies on every fetch, with per-IP
rate at 0.375 RPS (vs the ~0.5 PerimeterX comfort ceiling).

See docs/STACK_FRAMEWORK.md for the full architectural rationale.
================================================================================

Per MEMORY.md (project memory), Walmart's 45-fix audit from 2026-05-11 landed
behavioral patches but was never validated under multi-session load. This
adapter is the foundation for that validation — N=16 Chromes pinned 1:1 to
the active proxy pool, 6 RPS aggregate = 0.375 RPS/IP, well under the 0.5
PerimeterX threshold.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from src.stack.retailer_adapter import FetchResult, ItemStatus

logger = logging.getLogger(__name__)


# Walmart's first-party seller ID — items from this seller go through normal
# Walmart fulfillment, third-party marketplace sellers don't.
WALMART_SELLER_ID = "F55CDC31AB754BB68FE0851F0F1F2C96"


class WalmartAdapter:
    """Walmart.com adapter — implements src.stack.retailer_adapter.RetailerAdapter."""

    # ── retailer identity ────────────────────────────────────────────────
    name: str = "walmart"
    base_url: str = "https://www.walmart.com"

    # ── session bootstrap ────────────────────────────────────────────────
    # Walmart needs login for the gentler rate limits and the saved-card
    # checkout path. The session-bootstrap script (walmart/walmart_session_bootstrap.py)
    # handles one-time login per session profile.
    needs_login: bool = True

    # URLs each session visits during warmup, in order. Mix of Walmart and
    # external sites to accumulate behavioral trust. Per the 2026-04-30
    # audit, fixed warmup-site ordering was a detection signal — sessions
    # randomize 3-5 from this pool at runtime, not the full list every time.
    # (The framework's session pool implements the random subset selection.)
    warmup_urls: list[str] = [
        "https://www.walmart.com/",
        "https://www.walmart.com/cp/electronics/3944",
        "https://www.walmart.com/cp/trading-cards/4795",
        "https://www.google.com/",
        "https://www.amazon.com/",
        "https://www.reddit.com/r/PokemonTCG/",
        "https://www.youtube.com/",
        "https://www.bestbuy.com/",
    ]

    # Cookies that define "fresh enough to dispatch". A session whose _px3 is
    # older than 50s (the ~60s TTL with 10s safety) is not picked until its
    # heartbeat refreshes the cookie via a real interaction.
    cookie_freshness_keys: list[str] = ["_px3", "_abck", "bm_sz"]

    cookie_max_age_seconds: dict[str, int] = {
        "_px3": 50,        # ~60s TTL on checkout pages, 10s safety margin
        "_abck": 600,      # _abck rotates less aggressively, 10 min usable
        "bm_sz": 1800,     # bm_sz is set per session and stable for ~30min
    }

    # ── stock-check load profile ─────────────────────────────────────────
    # Walmart's __NEXT_DATA__ scrape is per-item — no bulk endpoint analog
    # to Target's RedSky bulk. chunk_size=1 means the dispatcher fires one
    # fetch per item per sweep (the JS template uses Promise.allSettled to
    # fan out within the tab, but each fetch is independent).
    chunk_size: int = 1

    # PerimeterX is more aggressive than Shape at per-IP rate detection.
    # 0.5 RPS/IP = one fetch per 2s per IP is the comfort ceiling. At
    # N=16 sessions and 6 RPS aggregate, per-IP rate = 0.375 — well under.
    per_ip_rps_ceiling: float = 0.5

    # PerimeterX's first-touch trust model penalizes burst session arrivals
    # harder than Shape. Stretch the launch window so N=16 Chromes warm
    # serially over 20 min instead of clustering at startup.
    chrome_stagger_seconds: int = 1200

    # _px3 has ~60s TTL on Walmart's checkout pages. We need every dispatch
    # to use a _px3 that's <60s old, so the framework computes per-session
    # refresh interval = max_session_cookie_age_seconds * N. At N=2 that's
    # one refresh per session every 100s; at N=16 every 800s. Either way
    # _px3 stays fresh because there are always (N-1) sessions firing
    # fetches during your own session's refresh window.
    max_session_cookie_age_seconds: int = 50

    # ── PerimeterX park/burn policy ──────────────────────────────────────
    # During Phase 1c dev gate (2026-05-16) both IPs hit 200+ consec 403s
    # in the failed first attempt, parked, then verified clean again within
    # ~14 min of the run ending. Suggests PerimeterX block-decay is fast.
    # park_duration=600s (10 min) is conservative against that observation;
    # the parked_retest background loop retests every 5 min so a healthy
    # IP returns to active pool within 10-15 min of parking.
    #
    # 401/403 streak=2 matches Target (consistent with both retailers'
    # typical behavior — one 403 is usually a flaky signal, two confirms).
    # burn_after_parks=4 preserves Target's tolerance.
    park_after_403_streak: int = 2
    park_duration_seconds: int = 600        # 10 min (vs Target's 3h)
    burn_after_parks: int = 4

    # ── stock-check fetch construction ───────────────────────────────────
    # JS template — fetches /ip/<item_id> as a navigation request, extracts
    # __NEXT_DATA__ from the HTML, returns the product subtree.
    # Mirrors walmart/stock_monitor.py:_FETCH_JS_TEMPLATE — proven in production
    # for normal-load monitoring.
    #
    # QUEUE DETECTION (added 2026-05-17 after Pokemon-drop research):
    # During high-demand drops (Pokemon Wednesdays, etc.) Walmart serves a
    # waiting-room interstitial instead of the product PDP. Two observed shapes:
    #   (A) 302 redirect to /qp?qpdata=<URL-encoded JSON>
    #   (B) 200 on /ip/<id> with the body being the queue interstitial HTML
    #       (no __NEXT_DATA__ shape, but body mentions waiting-room API or
    #        qpdata token)
    # The JS detects both, extracts the qpdata token if present, and emits
    # error="QUEUED" with the queue info so the framework treats this as a
    # near-in-stock signal that should trigger purchase-side queue handling.
    _FETCH_JS_TEMPLATE = """
    (async () => {{
        const ids = {item_ids_json};
        const results = await Promise.allSettled(
            ids.map(async (id) => {{
                const t0 = performance.now();
                try {{
                    const resp = await fetch('/ip/' + id, {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'text/html',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'navigate',
                            'Sec-Fetch-Dest': 'document',
                            'Referer': 'https://www.walmart.com/',
                            'Cache-Control': 'max-age=0'
                        }}
                    }});
                    const ms = performance.now() - t0;
                    const final_url = resp.url || '';
                    if (resp.redirected && final_url.includes('/blocked')) {{
                        return {{ item_id: id, error: 'BLOCKED', ms, http_status: resp.status }};
                    }}
                    // ── Queue detection: shape (A) redirect to /qp ────────
                    if (final_url.includes('/qp') || final_url.includes('qpdata=')) {{
                        let queue_info = null;
                        try {{
                            const m = final_url.match(/qpdata=([^&]+)/);
                            if (m) queue_info = JSON.parse(decodeURIComponent(m[1]));
                        }} catch (e) {{}}
                        return {{
                            item_id: id, error: 'QUEUED', ms,
                            http_status: resp.status,
                            queue_info: queue_info,
                            queue_source: 'redirect_url',
                        }};
                    }}
                    if (!resp.ok) {{
                        return {{ item_id: id, error: 'HTTP_' + resp.status, ms, http_status: resp.status }};
                    }}
                    const html = await resp.text();
                    // ── Queue detection: shape (B) interstitial in body ───
                    // Body mentions the ticket API or has a qpdata token even
                    // though URL didn't redirect. Indicators (ordered by
                    // specificity — first wins):
                    //   1. "api.waiting-room.walmart.com" — direct reference
                    //   2. "qpdata" token in body or page state
                    //   3. "issueTicket" or "checkTicket" endpoint names
                    if (html.includes('api.waiting-room.walmart.com') ||
                        html.includes('issueTicket') ||
                        html.includes('checkTicket')) {{
                        let queue_info = null;
                        try {{
                            const qm = html.match(/qpdata["'=:\\s]+([^"'&<\\s]+)/);
                            if (qm && qm[1]) queue_info = JSON.parse(decodeURIComponent(qm[1]));
                        }} catch (e) {{}}
                        return {{
                            item_id: id, error: 'QUEUED', ms,
                            http_status: resp.status,
                            queue_info: queue_info,
                            queue_source: 'body_signature',
                        }};
                    }}
                    const m = html.match(/<script id="__NEXT_DATA__"[^>]*>(.*?)<\\/script>/);
                    if (!m) {{
                        return {{ item_id: id, error: 'NO_NEXT_DATA', ms, http_status: resp.status }};
                    }}
                    const data = JSON.parse(m[1]);
                    const product = data?.props?.pageProps?.initialData?.data?.product;
                    if (!product) {{
                        return {{ item_id: id, error: 'NO_PRODUCT', ms, http_status: resp.status }};
                    }}
                    if (!product.usItemId) product.usItemId = id;
                    return {{ item_id: id, product, ms, http_status: resp.status }};
                }} catch (e) {{
                    return {{ item_id: id, error: e.message?.substring(0, 60) || 'unknown', ms: performance.now() - t0 }};
                }}
            }})
        );
        const out = results.map(r => r.status === 'fulfilled' ? r.value : {{ item_id: '?', error: r.reason?.message || 'rejected' }});
        // Aggregate worst HTTP status for the framework to see — first BLOCKED
        // wins, then first non-200, then 200 if all clean. QUEUED is a 200
        // (the page loaded fine, it's just the queue interstitial) so it
        // doesn't change agg_status — the framework treats QUEUED as an
        // operational state, not a transport failure.
        let agg_status = 200;
        for (const r of out) {{
            if (r.error === 'BLOCKED') {{ agg_status = 999; break; }}
            if (r.http_status && r.http_status >= 400) agg_status = r.http_status;
        }}
        return {{ __http_status: agg_status, __body_json: {{ results: out }}, __body_text: null }};
    }})()
    """

    def build_fetch_js(self, items: list[str]) -> str:
        """Construct the JS the framework dispatcher runs via tab.evaluate().

        Returns JS that fetches each item's /ip/<id> page in parallel,
        extracts __NEXT_DATA__, returns {__http_status, __body_json, __body_text}
        matching the FetchResult contract.
        """
        return self._FETCH_JS_TEMPLATE.format(item_ids_json=json.dumps(items))

    def parse_response(
        self, result: FetchResult, items: list[str]
    ) -> list[ItemStatus]:
        """Translate the fetch result's per-item subarray into ItemStatus list.

        Walmart's response is an array of {item_id, product?, error?, ms}
        objects (one per requested item). We extract availability + price.

        Special case: error="QUEUED" indicates the item is behind Walmart's
        virtual waiting room (typical during Pokemon drops). We treat this
        as in_stock=True with availability_status="QUEUED" so the purchase
        manager fires its on_in_stock callback — the purchase flow then
        encounters the queue interstitial and the queue_handler takes over.
        """
        out: list[ItemStatus] = []
        if not result.body_json:
            return out

        per_item = (result.body_json or {}).get("results", [])
        for entry in per_item:
            item_id = entry.get("item_id", "?")
            err = entry.get("error")

            # Queue detection — emit as in_stock=True so downstream fires
            # the purchase flow. The purchase flow then runs through
            # walmart/queue_handler.py to get a real ATC opportunity.
            if err == "QUEUED":
                qinfo = entry.get("queue_info") or {}
                qsrc = entry.get("queue_source", "unknown")
                # qpdata.customMetadata.item carries product title + price
                # if Walmart embedded them in the queue payload.
                meta = (qinfo.get("customMetadata") or {}).get("item") or {}
                title = meta.get("name") or None
                price_str = meta.get("currentPrice") or ""
                price = None
                if isinstance(price_str, str):
                    try:
                        # "$499.00" → 499.00
                        price = float(price_str.replace("$", "").replace(",", "").strip())
                    except (ValueError, AttributeError):
                        price = None
                out.append(
                    ItemStatus(
                        item_id=item_id if item_id != "?" else (items[0] if items else "?"),
                        in_stock=True,
                        title=title,
                        price=price,
                        availability_status=f"QUEUED:{qsrc}",
                    )
                )
                continue

            if item_id == "?" or err:
                # Generic error case — no-stock so framework doesn't fire
                # in_stock callback.
                out.append(
                    ItemStatus(
                        item_id=item_id if item_id != "?" else (items[0] if items else "?"),
                        in_stock=False,
                        availability_status=err or "ERROR",
                    )
                )
                continue

            product = entry.get("product") or {}
            parsed = self._extract_product(item_id, product)
            if parsed is None:
                out.append(
                    ItemStatus(
                        item_id=item_id,
                        in_stock=False,
                        availability_status="PARSE_FAIL",
                    )
                )
                continue

            out.append(
                ItemStatus(
                    item_id=item_id,
                    in_stock=parsed["in_stock"],
                    title=parsed["name"],
                    price=parsed["price"],
                    availability_status=parsed["availability"],
                )
            )

        return out

    @staticmethod
    def _extract_product(item_id: str, product: dict) -> Optional[dict]:
        """Walmart-specific product extraction — mirrors stock_monitor._extract_product.

        in_stock requires BOTH availability=IN_STOCK/PRE_ORDER_SELLABLE AND
        showAtc=True AND walmart_direct (not third-party seller). Marketplace
        items are silently treated as OOS — we don't buy from third parties.
        """
        if not isinstance(product, dict) or product.get("usItemId") != item_id:
            return None

        seller_id = product.get("sellerId", "") or ""
        seller_name = product.get("sellerName", "") or ""
        is_direct = (
            seller_id.upper() == WALMART_SELLER_ID
            or seller_name.lower() == "walmart.com"
        )
        price = product.get("priceInfo", {}).get("currentPrice", {}).get("price")
        availability = product.get("availabilityStatus", "UNKNOWN")
        show_atc = product.get("showAtc", False)
        in_stock = (
            availability in ("IN_STOCK", "PRE_ORDER_SELLABLE")
            and show_atc
            and is_direct
        )
        return {
            "name": product.get("name", "Unknown"),
            "price": price,
            "availability": availability,
            "show_atc": show_atc,
            "walmart_direct": is_direct,
            "in_stock": in_stock,
        }

    def is_blocked_response(self, result: FetchResult) -> bool:
        """A 999 aggregate status means at least one per-item fetch redirected
        to /blocked. The framework uses this to feed ProxyState's 403-streak.
        """
        if result.http_status == 999:
            return True
        # Per-item errors of HTTP_403 / HTTP_456 also count as block signals.
        if result.body_json:
            for entry in result.body_json.get("results", []):
                err = entry.get("error", "")
                if err in ("BLOCKED", "HTTP_403", "HTTP_456"):
                    return True
        return False

    # ── pre-flight ───────────────────────────────────────────────────────
    def preflight_probe_url(self) -> str:
        """Walmart's homepage is the lightest cheap probe — no auth needed,
        returns 200 + Akamai cookies on a clean IP, 403/456 or redirect to
        /blocked on a flagged one. The framework probes once at startup
        before pinning a session to the IP.
        """
        return "https://www.walmart.com/"

    def is_preflight_clean(self, http_status: int, body_text: str) -> bool:
        """A clean preflight returns 200 with normal Walmart HTML.
        Anything else (403, 456, /blocked redirect signaled via body) is dirty.
        """
        if http_status != 200:
            return False
        # /blocked redirects often return 200 on the destination page —
        # check the body for the block-page signature.
        if "robot or human" in body_text.lower():
            return False
        if "px-captcha" in body_text.lower():
            return False
        return True

    # ── checkout (Phase 5) ───────────────────────────────────────────────
    # These delegate to the existing walmart/checkout_api.py which already
    # has the captured GraphQL hashes (UPDATE_ITEMS, CREATE_CONTRACT,
    # RESERVE_SLOT, GET_BOOK_SLOT_PAGE) from the 2026-05-11 capture session.
    # Phase 5 will wire these properly; for now they raise so anyone trying
    # to use them gets a clear pointer to where the work lands.

    def build_atc_js(self, item_id: str, qty: int, cart_context: dict) -> str:
        raise NotImplementedError(
            "WalmartAdapter.build_atc_js is Phase 5 work — wire through "
            "walmart/checkout_api.py UPDATE_ITEMS_HASH mutation."
        )

    def build_place_order_js(self, order_context: dict) -> str:
        raise NotImplementedError(
            "WalmartAdapter.build_place_order_js is Phase 5 work — wire "
            "through walmart/checkout_api.py CREATE_CONTRACT_HASH mutation."
        )

    def apq_full_query(self, operation_name: str) -> Optional[str]:
        """Walmart uses Apollo persisted queries with weekly hash rotation.
        Phase 3 work: capture the full GraphQL query body for ItemByIdBtf
        (and the checkout mutations) so APQ full-query fallback can recover
        when a hash goes stale.

        Returns None for now — framework treats None as "APQ not supported
        for this operation" and skips the fallback retry.
        """
        # TODO Phase 3: return full ItemByIdBtf query body when operation_name
        # == "ItemByIdBtf". Capture via walmart/checkout_capture.py first.
        return None
