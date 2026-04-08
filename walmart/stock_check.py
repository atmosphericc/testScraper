"""
Walmart stock/price checker using the Orchestra GraphQL API.
Uses the exact headers and POST body captured from the browser.

DEV-ONLY DIAGNOSTIC TOOL: This file uses raw `requests` (Python TLS fingerprint),
which Akamai hard-blocks in production. It is only useful for local development
and cookie inspection. Do not rely on it for live stock checks.

Usage:
  python walmart/stock_check.py
"""

import logging
import os
import requests
import json
import concurrent.futures

logger = logging.getLogger(__name__)

try:
    from .config import GRAPHQL_HASH, WALMART_SELLER_ID
except ImportError:
    # Fallback for running as standalone script: python walmart/stock_check.py
    GRAPHQL_HASH = "20d116c298a901b29763c37a4aaf8b37aeb1654e4f971cd11a7fe9de2ceab027"
    WALMART_SELLER_ID = "F55CDC31AB754BB68FE0851F0F1F2C96"


def load_cookies() -> dict:
    """Load Walmart session cookies from the persistent profile."""
    cookie_path = os.path.join(os.path.dirname(__file__), "..", "walmart-profile", "cookies.json")
    cookie_path = os.path.normpath(cookie_path)
    if not os.path.exists(cookie_path):
        raise FileNotFoundError(
            f"Walmart cookie file not found: {cookie_path}. Run walmart_relogin.py first."
        )
    with open(cookie_path) as f:
        raw = json.load(f)
    # Cookie file may be a list of {name, value} dicts (Playwright format) or a flat dict
    if isinstance(raw, list):
        return {c["name"]: c["value"] for c in raw if "name" in c and "value" in c}
    return raw


def parse_cookies(raw: str) -> dict:
    cookies = {}
    for part in raw.strip().split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies


HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US",
    "baggage": "trafficType=customer,deviceType=desktop,renderScope=CSR,webRequestSource=Browser,pageName=itemPage",
    "calltype": "CLIENT",
    "content-type": "application/json",
    "device_profile_ref_id": "ehwbstrvqezcjmdsog69hosdqykj1dvdzelw",
    "origin": "https://www.walmart.com",
    "referer": "https://www.walmart.com/ip/Pokemon-Trading-Card-Games-Scarlet-Violet-9-Journey-Together-Booster-Bundle/15042474261",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "tenant-id": "elh9ie",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "wm_mp": "true",
    "x-apollo-operation-name": "ItemByIdBtf",
    "x-enable-server-timing": "1",
    "x-latency-trace": "1",
    "x-o-bu": "WALMART-US",
    "x-o-ccm": "server",
    "x-o-gql-query": "query ItemByIdBtf",
    "x-o-mart": "B2C",
    "x-o-platform": "rweb",
    "x-o-platform-version": "usweb-1.251.0-c88ea9c8137ed74a73df2e810aff797c113bc48a-3192043r",
    "x-o-segment": "oaoh",
}


def build_body(item_id: str) -> dict:
    return {
        "variables": {
            "isMobile": False,
            "layout": ["itemPageThreeGridDesktop2"],
            "channel": "WWW",
            "version": "v1",
            "postProcessingVersion": 1,
            "p13nCls": {
                "pageId": item_id,
                "skipPtcFetch": True,
                "p13NCallType": "BTF",
            },
            "fetchP13N": True,
            "fMrkDscrp": False,
            "pageType": "ItemPageGlobalDesktop",
            "fIdml": False,
            "fRev": False,
            "iId": item_id,
            "bbe": True,
            "fSId": True,
            "eSb": True,
            "enableDetailedBeacon": False,
            "enableMultiSave": False,
            "enableClickTrackingURL": False,
            "eCc": True,
            "fIdmlOrMrkDscrp": False,
            "tenant": "WM_GLASS",
            "epsv": True,
            "enableRxDrugScheduleModal": False,
            "enablePromotionMessages": False,
            "enableSignInToSeePrice": False,
            "enableOptimisticWeightUpdate": False,
        }
    }


def fetch_item(item_id: str, session: requests.Session) -> dict:
    url = f"https://www.walmart.com/orchestra/pdp/graphql/ItemByIdBtf/{GRAPHQL_HASH}/ip/{item_id}"
    headers = {**HEADERS, "x-o-item-id": item_id}
    body = build_body(item_id)

    logger.debug("[REQUEST] POST %s", item_id)
    resp = session.post(url, headers=headers, json=body, timeout=10)
    logger.debug("[RESPONSE] %s -> %s", item_id, resp.status_code)

    if resp.status_code != 200:
        logger.warning("[RESPONSE] %s -> HTTP %s | %.400s", item_id, resp.status_code, resp.text)
        return {"item_id": item_id, "error": resp.status_code, "raw": resp.text[:400]}
    return {"item_id": item_id, "data": resp.json()}


def parse_item(result: dict) -> "dict | None":
    if "error" in result:
        return result

    item_id = result["item_id"]
    data = result["data"]

    modules = data.get("data", {}).get("contentLayout", {}).get("modules", [])

    # The SoftBundles module contains the target item as the first product
    # with full price, availability, and seller data
    for module in modules:
        if module.get("type") != "SoftBundles":
            continue
        products = module.get("configs", {}).get("products", [])
        for product in products:
            if product.get("usItemId") != item_id:
                continue
            seller_id = product.get("sellerId", "")
            seller_name = product.get("sellerName", "")
            is_direct = (
                seller_id.upper() == WALMART_SELLER_ID
                or seller_name.lower() == "walmart.com"
            )
            price = product.get("priceInfo", {}).get("currentPrice", {}).get("price")
            # availabilityStatus + showAtc are the reliable signals here.
            # availabilityStatusV2 comes from the SoftBundles widget context and can be stale.
            availability = product.get("availabilityStatus", "UNKNOWN")
            show_atc = product.get("showAtc", False)
            return {
                "item_id": item_id,
                "name": product.get("name", "Unknown"),
                "price": price,
                "availability": availability,
                "show_atc": show_atc,
                "in_stock": availability in ("IN_STOCK", "PRE_ORDER_SELLABLE") and show_atc,
                "walmart_direct": is_direct,
                "seller_id": seller_id,
                "seller_name": seller_name,
                "order_limit": product.get("orderLimit"),
            }

    return {"item_id": item_id, "error": "no_matching_product_in_softbundles"}


def check_items(item_ids: list[str]) -> list[dict]:
    cookies = parse_cookies(COOKIES_RAW)
    logger.debug("[SESSION] Loaded %d cookies", len(cookies))

    with requests.Session() as session:
        session.cookies.update(cookies)

        if len(item_ids) == 1:
            results = [fetch_item(item_ids[0], session)]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                futures = {pool.submit(fetch_item, iid, session): iid for iid in item_ids}
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

    return results


if __name__ == "__main__":
    test_ids = [
        "15042474261",  # Pokemon Journey Together Booster Bundle
    ]

    print("=== FETCHING ===")
    raw_results = check_items(test_ids)

    print("\n=== PARSED ===")
    for r in raw_results:
        parsed = parse_item(r)
        if not parsed:
            continue
        if "error" in parsed:
            print(f"  [{parsed['item_id']}] ERROR {parsed['error']}")
        else:
            tag = "WALMART DIRECT" if parsed["walmart_direct"] else "3RD PARTY"
            stock = "IN STOCK" if parsed["in_stock"] else "OUT OF STOCK"
            print(f"  [{tag}] [{stock}] {parsed['name']}")
            print(f"    Price: ${parsed['price']} | Seller: {parsed['seller_name']} | Order limit: {parsed['order_limit']}")
