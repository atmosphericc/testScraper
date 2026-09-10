#!/usr/bin/env python3
"""RedSky read channel selector (2026-09-09).

Two RedSky aggregations return the same parser-relevant shape
(`data.product_summaries[].fulfillment.shipping_options.availability_status`,
`available_to_promise_quantity`, `item.relationship_type_code`,
`item.product_description`) -- verified 2026-09-09 from this machine:

  web      = /v1/web/product_summary_with_fulfillment_v1, fetched IN-PAGE by the
             sweep Chromes and the trusted-browser fallback. HUMAN/PerimeterX
             captcha-walls it on the Bright Data ISP prefixes: the 09-07 run was
             blind 94% of the time (docs/FAILURES.md 2026-09-09).
  apps_raw = /v1/apps/tcin_product_list_v2 read as RAW HTTP (urllib, no
             browser, no cookies) through each sweep session's local forwarder
             with the mobile-app header set. Probe 2026-09-09 19:3x through the
             walled exit 31.105.228.245: web = 403 captcha, apps_raw = HTTP 200
             product data (0.7 s). Two public monitors read Target this way.
             The apps URL from INSIDE a browser tab is still captcha'd (plain
             headers) or CORS-preflight-blocked (app headers), so the browser
             JS builders always keep the web URL.

RESILIENT_REDSKY_CHANNEL=web|apps_raw  (default web = exact prior behaviour;
`apps` is accepted as an alias of apps_raw).
"""
from __future__ import annotations

import os
import random
import time
import urllib.parse
from typing import Dict, Iterable, Optional

WEB_URL = ("https://redsky.target.com/redsky_aggregations/v1/web/"
           "product_summary_with_fulfillment_v1")
APPS_URL = ("https://redsky.target.com/redsky_aggregations/v1/apps/"
            "tcin_product_list_v2")
APPS_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"   # the key the app channel answered with
APPS_HEADERS: Dict[str, str] = {
    "x-channel-id": "APPS",
    "x-client-platform": "iPhone",
    "x-client-version": "2026.28.0",
    "user-agent": "Target/2026.28.0 iPhone15,2 iOS/26.4.1 CFNetwork/3860.500.112 Darwin/25.4.0",
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
}


def mode(env: Optional[Dict[str, str]] = None) -> str:
    e = os.environ if env is None else env
    v = str(e.get("RESILIENT_REDSKY_CHANNEL", "web") or "web").strip().lower()
    return "apps_raw" if v in ("apps_raw", "apps") else "web"


def is_raw(env: Optional[Dict[str, str]] = None) -> bool:
    return mode(env) == "apps_raw"


def bulk_url(env: Optional[Dict[str, str]] = None) -> str:
    """URL for IN-PAGE fetches. Always the web aggregation: the apps URL from a
    browser tab was captcha'd (plain headers) or preflight-blocked (app headers)
    in the 2026-09-09 probes, so only the raw reader uses it."""
    return WEB_URL


def extra_headers_js(env: Optional[Dict[str, str]] = None) -> str:
    """Extra in-page fetch headers (none: custom x-* headers force a CORS
    preflight that RedSky rejects). Kept for the JS builders' call sites."""
    return ""


def raw_headers() -> Dict[str, str]:
    return dict(APPS_HEADERS)


def raw_apps_url(tcins: Iterable[str], store_id: str, cache_bust: bool = False) -> str:
    params = {
        "key": APPS_KEY,
        "store_id": str(store_id),
        "pricing_store_id": str(store_id),
        "tcins": ",".join(str(t) for t in tcins),
    }
    if cache_bust:
        params["_"] = f"{int(time.time() * 1000)}{random.randint(0, 999999)}"
    return APPS_URL + "?" + urllib.parse.urlencode(params)
