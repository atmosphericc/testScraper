"""Walmart hybrid checkout client — GraphQL via `tab.evaluate(fetch(...))`.

Mirrors Target's OBSERVE-mode hybrid (`docs/RETAILERS/TARGET_CHECKOUT_API.md`).
Calls run from the browser's JS context so the live `_px3` + Akamai cookies
+ JA3 fingerprint apply automatically — Walmart can't distinguish from a
real user interaction.

Capture source: walmart/logs/checkout_capture_20260511_152144.jsonl (real
Place Order run, order pcid=8ee7d7bc-7521-4f40-ba15-e262a2fc6b41).

Implemented:
  - bump_quantity(qty)          — POST updateItems w/ quantity: N
  - place_order()               — POST CreateContract
  - read_cart_context()         — read cartId + lineItems + offerId from
                                  __NEXT_DATA__ on the cart tab

Not implemented (DOM-only for now until next capture lands the hashes):
  - reserve_slot(cheapest+earliest)
  - set_tip(0)

Env-gate: `WALMART_CHECKOUT_API=1` enables the hybrid path. Without it,
the DOM path runs as before.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("walmart.checkout_api")


# ── APQ (Apollo Persisted Query) fallback ────────────────────────────────
# Walmart rotates the SHA-256 persisted-query hashes ~weekly. When a hash
# goes stale the server returns:
#     HTTP 200 + {"errors": [{"message": "...", "extensions": {"code":
#                "PERSISTED_QUERY_NOT_FOUND"}}]}
# The CORRECT sentinel is `extensions.code == "PERSISTED_QUERY_NOT_FOUND"`
# (NOT the message text — per Apollo Client #10253, the message is not
# guaranteed stable but the extensions.code is). On detection, retry the
# same request with a `query` field carrying the full operation body;
# the server caches the new hash on success.
#
# Query bodies live in walmart/checkout_apq_queries.json so they can be
# updated independently of code when Walmart introduces new operations or
# changes argument shapes.

_APQ_QUERIES_PATH = Path(__file__).parent / "checkout_apq_queries.json"

# Loaded lazily so the module imports cheaply even if the file is missing
_APQ_QUERIES_CACHE: Optional[dict[str, str]] = None


def _load_apq_queries() -> dict[str, str]:
    """Read walmart/checkout_apq_queries.json once and cache.
    Returns empty dict if the file is missing or malformed (logged warn).
    """
    global _APQ_QUERIES_CACHE
    if _APQ_QUERIES_CACHE is not None:
        return _APQ_QUERIES_CACHE
    try:
        data = json.loads(_APQ_QUERIES_PATH.read_text(encoding="utf-8"))
        queries = data.get("queries") or {}
        if not isinstance(queries, dict):
            logger.warning("[APQ] checkout_apq_queries.json 'queries' is not a dict")
            queries = {}
        _APQ_QUERIES_CACHE = queries
        logger.info("[APQ] loaded %d query bodies", len(queries))
        return queries
    except FileNotFoundError:
        logger.warning("[APQ] checkout_apq_queries.json not found at %s — "
                       "fallback disabled", _APQ_QUERIES_PATH)
        _APQ_QUERIES_CACHE = {}
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[APQ] failed to parse checkout_apq_queries.json: %s", e)
        _APQ_QUERIES_CACHE = {}
        return {}


def apq_query_for(op_name: str) -> Optional[str]:
    """Return the full GraphQL query string for an operation, or None if
    not captured yet. Callers must handle None gracefully (skip retry)."""
    return _load_apq_queries().get(op_name)


def is_apq_miss(body: dict) -> bool:
    """Strict APQ-miss detection per Apollo Client #10253.

    Returns True iff body.errors[*].extensions.code includes the canonical
    sentinel. Does NOT match by message text — Walmart could change the
    message at any time and we'd misfire on unrelated errors.
    """
    if not isinstance(body, dict):
        return False
    errors = body.get("errors")
    if not isinstance(errors, list):
        return False
    for err in errors:
        if not isinstance(err, dict):
            continue
        ext = err.get("extensions") or {}
        if not isinstance(ext, dict):
            continue
        if ext.get("code") == "PERSISTED_QUERY_NOT_FOUND":
            return True
    return False


# Hashes captured 2026-05-11 from real Place Order + slot-reservation runs.
# Auto-discoverable via `python -m walmart.capture_analyzer --emit-py`.
# If Walmart rotates these, the bot logs HTTP 400 and the DOM path takes over.
UPDATE_ITEMS_HASH = "8f04790148c52a6bd70449c7c6c56d57f74fec0301878d6ffc50acc059343180"
CREATE_CONTRACT_HASH = "cc8455e5a9158dc86b9b96656595396c110f231e148863107287c46ec5aa9144"
GET_SLOTS_HASH = "284fc996d255acb14e392a7b83f3d8dc43caade8d921a518349fc20f44a11aa0"
RESERVE_SLOT_HASH = "d004e26443acf233d4b4d7df47c79252b79ca0cf9d7e07f7f1ab42fbee5da87f"
GET_BOOK_SLOT_PAGE_HASH = "5d4c1134a0da62a2647db0144076f4186c6fffc70bba62beccd2412a56c566a7"

# Boilerplate from the captured CreateContract payload. The server reads
# tip / slot / address / payment from cart state, so these are static.
SUPPORTED_PAYMENTS = [
    "AFFIRM", "CREDITCARD", "DIRECTED_SPEND", "EBT", "GIFTCARD", "INCOMM",
    "ONE_BNPL", "PAP_EBT", "PAYPAL_1X", "PAYPAL_BA", "SOLUTRAN", "CARECREDIT",
    "WMT_REWARDS", "WMTPC", "PAYBYBANK", "HSA_FSA", "WMT_CREDIT", "WIC",
    "ONEPAY_CREDITCARD", "NATIONS",
]

# Captured feature flags. Walmart serializes these into `features`/`enable*`
# fields on every request. Sending a stripped set causes 400 — the server
# expects all 70+ keys present (even if false). Keep verbatim from capture.
CREATE_CONTRACT_FEATURES = [
    "lmpdel", "mlrx", "vsrx", "sit", "sitprx", "sitsc", "sitsd", "gepmss",
    "cfsebt", "acctpref", "maappl", "wday", "mbc", "tipwat", "csc",
    "incrementalauth", "adjustmentchargeclarity", "policyvtwo",
    "paypalsplitallocation", "eoap", "potp", "byod", "ebtbmf", "getitnow",
    "multipromo", "pdr", "adr", "cfsds", "qsr", "tfd",
]

UPDATE_ITEMS_FEATURES = [
    "lmpdel", "mlrx", "vsrx", "maappl", "accfournudge", "potp", "byod",
    "vptires", "pdr", "gepmss", "dd", "qsr", "cbs", "tfd",
]


def is_enabled() -> bool:
    """True iff WALMART_CHECKOUT_API=1 (or any truthy value)."""
    v = os.environ.get("WALMART_CHECKOUT_API", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def is_observe() -> bool:
    """True iff WALMART_API_OBSERVE=1 — log API call but don't replace DOM."""
    v = os.environ.get("WALMART_API_OBSERVE", "").strip().lower()
    return v in ("1", "true", "yes", "on")


class WalmartHybridCheckout:
    """Walmart checkout via GraphQL POSTs from the browser tab.

    Usage:
        api = WalmartHybridCheckout(checkout_tab)
        ctx = await api.read_cart_context()        # cartId, lineItems
        await api.bump_quantity(ctx, qty=10)       # one POST instead of 9 clicks
        await api.place_order(ctx)                 # one POST instead of clicks

    All methods execute via `tab.evaluate(...)` so the `_px3` cookie and
    Akamai TLS fingerprint are real-browser-grade.
    """

    def __init__(self, tab):
        self._tab = tab

    async def read_cart_context(
        self, timeout_s: float = 6.0,
    ) -> Optional[dict[str, Any]]:
        """Pull cartId + lineItems[] from __NEXT_DATA__ on the cart page.

        Walmart's /cart is CSR-hydrated; __NEXT_DATA__ ships with an empty
        cartLines array for several seconds while the cart-state fetch
        completes. We poll for up to ``timeout_s`` until both cartId AND
        at least one fully-populated line item (with offerId) are present.

        Returns None on timeout or unrecoverable state.
        """
        import asyncio
        import time as _time

        read_js = """
            (() => {
                const nd = window.__NEXT_DATA__;
                if (!nd) return { error: 'no_next_data' };
                const cart = nd?.props?.pageProps?.initialData?.data?.cart
                          || nd?.props?.pageProps?.cart;
                if (!cart) return { error: 'no_cart_in_next_data' };
                const lines = cart.cartLines || cart.lineItems || cart.items || [];
                return {
                    cartId: cart.id || cart.cartId,
                    line_count: Array.isArray(lines) ? lines.length : 0,
                    lineItems: lines.map(li => ({
                        usItemId: li.usItemId || li.itemId || li.id,
                        offerId: li.offerId,
                        lineItemId: li.lineItemId || li.id,
                        quantity: li.quantity || li.qty || 1,
                        name: (li.productName || li.name || '').slice(0, 120),
                        fulfillmentPreference: li.fulfillmentPreference
                            || li.fulfillmentSelection
                            || 'DELIVERY',
                        availableFulfillmentOptions: li.availableFulfillmentOptions
                            || ['SCHEDULED_DELIVERY', 'SCHEDULED_PICKUP', 'UNSCHEDULED_PICKUP'],
                        isPharmacyPrescription: !!li.isPharmacyPrescription,
                    })),
                };
            })()
        """

        deadline = _time.monotonic() + timeout_s
        last_reason = "unknown"
        poll_count = 0
        while _time.monotonic() < deadline:
            poll_count += 1
            try:
                raw = await self._tab.evaluate(read_js)
            except Exception as e:
                last_reason = f"evaluate_raised:{e}"
                await asyncio.sleep(0.2)
                continue
            if not isinstance(raw, dict):
                last_reason = f"non_dict:{type(raw).__name__}"
                await asyncio.sleep(0.2)
                continue
            if raw.get("error"):
                last_reason = raw["error"]
                await asyncio.sleep(0.2)
                continue
            if not raw.get("cartId"):
                last_reason = "no_cartId"
                await asyncio.sleep(0.2)
                continue
            lines = raw.get("lineItems") or []
            valid = [li for li in lines
                     if li.get("usItemId") and li.get("offerId")]
            if valid:
                logger.info(
                    "[HYBRID] Cart context ready in %d polls: cartId=%s, "
                    "%d line(s), first=usItemId %s qty=%s",
                    poll_count, raw["cartId"], len(valid),
                    valid[0]["usItemId"], valid[0]["quantity"],
                )
                return {"cartId": raw["cartId"], "lineItems": valid}
            # Have cartId but no valid line yet — keep polling
            last_reason = (
                f"cartId_present_no_valid_lines (raw_line_count={raw.get('line_count')})"
            )
            await asyncio.sleep(0.2)

        logger.warning(
            "[HYBRID] read_cart_context timeout after %.1fs (%d polls) — last reason: %s",
            timeout_s, poll_count, last_reason,
        )
        return None

    async def bump_quantity(self, ctx: dict, qty: int) -> Optional[dict]:
        """POST updateItems to set the first cart line to ``qty``.

        One GraphQL POST replaces the entire stepper-click loop. Walmart's
        server-side cap (orderLimit / MultipackQuantity) still applies —
        the response's lineItems[0].quantity is the actual committed value.

        Returns the parsed response dict on success, None on error.
        """
        if qty < 1:
            qty = 1
        line = ctx["lineItems"][0]
        item_payload = {
            "offerId": line["offerId"],
            "usItemId": line["usItemId"],
            "quantity": qty,
            "lineItemId": line["lineItemId"],
            "fulfillmentPreference": line.get("fulfillmentPreference") or "DELIVERY",
            "preferredItemLevelIntent": line.get("fulfillmentPreference") or "DELIVERY",
            "availableFulfillmentOptions": line.get(
                "availableFulfillmentOptions",
                ["SCHEDULED_DELIVERY", "SCHEDULED_PICKUP", "UNSCHEDULED_PICKUP"],
            ),
            "name": line.get("name", ""),
            "isPharmacyPrescription": bool(line.get("isPharmacyPrescription")),
        }

        payload = {
            "variables": {
                "getDetailedAccesspoint": False,
                "input": {
                    "enableLiquorBox": True,
                    "cartId": ctx["cartId"],
                    "items": [item_payload],
                    "isGiftOrder": None,
                    "intentSource": "Item_Level_Control",
                    "enableCartSplitClarity": False,
                    "features": UPDATE_ITEMS_FEATURES,
                },
                "includePartialFulfillmentSwitching": True,
                "enableAEBadge": False,
                "includeExpressSla": True,
                "includeQueueing": False,
                "enableCartBookslotShortcut": False,
                "enableACCScheduling": True,
                "enableWalmartPlusFreeDiscountedExpress": True,
                "enableDiscountedOrHolidayExpress": True,
                "enableBenefitSavings": False,
                "enableUnifiedBadges": False,
                "enableCartLevelMSI": False,
                "enablePickupNotAvailable": False,
                "enableReturnsLabel": False,
                "enableStarRatings": False,
                "enableSpendLimit": False,
                "enableMsiMci": True,
                "enableTaxBreakdown": False,
                "enableI18nWave1": True,
                "enableWplusPetBenefit": False,
                "enableCartLevelPromotions": True,
                "enableOrderCutOffTime": True,
                "enableHotCartFeature": False,
                "enableMOQ": False,
                "enableMOQVariants": False,
                "enablePetRxManualRefill": True,
                "enableItemLevelTE": False,
            }
        }

        url = f"/orchestra/cartxo/graphql/updateItems/{UPDATE_ITEMS_HASH}"
        return await self._post_graphql("updateItems", url, payload, "updateItems")

    async def get_slots(self, ctx: dict) -> Optional[list[dict]]:
        """GET /orchestra/cartxo/graphql/getSlots/... — return list of
        slot dicts (each has id, slotMetadata, price, fulfillmentType,
        startTime/endTime, available, isSelectable, slaInMins).
        """
        # getSlots is a GET with `variables=<url-encoded JSON>` query string
        variables = {
            "cartId": ctx["cartId"],
            "fulfillmentOption": "DELIVERY",
            "cartFulfillmentOption": "DELIVERY",
            "isGuest": False,
            "isExpressSla": True,
            "enableDeliveryAddressFromSlotData": True,
            "enableWalmartPlusFreeDiscountedExpress": True,
            "maxAvailableSlotsCount": 15,
            "requestSource": "CART_SHORTCUT",
            "itemFulfillmentTypes": ["UNKNOWN"],
            "enableMultipleInhomeAddresses": True,
            "enableCartCustomerContext": True,
            "enableInstaCartSlots": False,
            "enableAccSlotExpansion": True,
            "enableColdChainExpansion": True,
            "enableHolidayFreeExpressDelivery": True,
            "enableExpressPricing": True,
            "enablePromotionClaimStatus": True,
            "enablePromotionType": True,
            "enablePreferredStore": True,
            "enableExpressUnavailableBanner": True,
            "enableMemberInfoFromCartCustomerContext": True,
            "enableDynamicExpressSlotType": False,
            "enableOffPeakHoursDelivery": True,
            "enableSparkStore": True,
            "enableCustomizableItemsPhase1": True,
            "features": ["lmpdel", "mlrx", "vsrx", "maappl", "byod", "pdr", "dd", "qsr"],
        }
        import urllib.parse as _up
        var_q = _up.quote(json.dumps(variables, separators=(",", ":")))
        url = f"/orchestra/cartxo/graphql/getSlots/{GET_SLOTS_HASH}?variables={var_q}"
        body = await self._get_graphql("getSlots", url, "getSlots")
        if not body:
            return None
        try:
            days = body["data"]["slots"]["slotDays"]
        except (KeyError, TypeError):
            logger.warning("[HYBRID] getSlots response shape unexpected")
            return None
        flat = []
        for day in days:
            for slot in day.get("eachDaySlots") or []:
                flat.append(slot)
        logger.info("[HYBRID] getSlots returned %d total slot(s)", len(flat))
        return flat

    @staticmethod
    def pick_cheapest_earliest_slot(
        slots: list[dict], avoid_express: bool = True,
    ) -> Optional[dict]:
        """Pick the cheapest+earliest available delivery slot.

        Default: avoid EXPRESS_DELIVERY (those carry +$5-$10 fees).
        Returns the slot dict or None if no viable slot exists.
        """
        viable = []
        for s in slots:
            if not s.get("available") or not s.get("isSelectable"):
                continue
            ft = s.get("fulfillmentType", "")
            if avoid_express and ft == "EXPRESS_DELIVERY":
                continue
            price = (s.get("price") or {}).get("total") or {}
            price_val = price.get("value")
            if price_val is None:
                continue
            viable.append({
                "id": s.get("id"),
                "fulfillmentType": ft,
                "price": float(price_val),
                "startTime": s.get("startTime") or "9999",
                "slaInMins": s.get("slaInMins"),
                "raw": s,
            })
        if not viable:
            # Fallback: relax avoid_express
            if avoid_express:
                logger.info(
                    "[HYBRID] No non-Express slots viable — retrying with Express allowed"
                )
                return WalmartHybridCheckout.pick_cheapest_earliest_slot(
                    slots, avoid_express=False,
                )
            logger.warning("[HYBRID] No viable slots at all")
            return None
        viable.sort(key=lambda x: (x["price"], x["startTime"], x["slaInMins"] or 999))
        pick = viable[0]
        logger.info(
            "[HYBRID] Picked slot: $%s %s startTime=%s id=%s",
            pick["price"], pick["fulfillmentType"], pick["startTime"], pick["id"],
        )
        return pick["raw"]

    async def reserve_slot(self, ctx: dict, slot: dict) -> Optional[dict]:
        """POST reserveSlotMutation — bind ``slot`` to the cart.

        ``slot`` must be a slot dict from get_slots() response (has
        ``slotMetadata`` + all other fields). Server reads everything
        from the slot dict — no need to compute fees etc. ourselves.
        """
        payload = {
            "variables": {
                "cartId": ctx["cartId"],
                "slotMetadata": slot.get("slotMetadata", ""),
                "selectedSlot": slot,
            }
        }
        url = f"/orchestra/cartxo/graphql/reserveSlotMutation/{RESERVE_SLOT_HASH}"
        return await self._post_graphql(
            "reserveSlotMutation", url, payload, "reserveSlotMutation"
        )

    async def reserve_cheapest_slot(self, ctx: dict) -> Optional[dict]:
        """Convenience: get_slots + pick cheapest+earliest + reserve_slot.

        One method call replaces the entire Reserve-a-time drawer
        interaction. Returns the reserve_slot response on success.
        """
        slots = await self.get_slots(ctx)
        if not slots:
            return None
        pick = self.pick_cheapest_earliest_slot(slots, avoid_express=True)
        if not pick:
            return None
        result = await self.reserve_slot(ctx, pick)
        if result and result.get("data", {}).get("reserveSlot", {}).get("checkoutable"):
            logger.info("[HYBRID] Slot reserved + cart checkoutable")
        return result

    async def submit_cvv_via_pie(self, cvv: str) -> Optional[dict]:
        """Encrypt the CVV via Walmart's PIE.js scheme and POST it to
        /api/checkout-customer/encrypted-pan, bypassing DOM keystroke entry.

        Why: DOM CVV entry (typing one char at a time into an iframe input)
        is the single most-scrutinized form in Walmart's checkout for
        PerimeterX behavioral biometrics. Encrypting + POSTing the CVV
        directly removes the keystroke timing signal entirely.

        Flow:
          1. fetch_pie_key(tab) — pulls getkey.js via tab.evaluate, parses
             the RSA modulus + key_id.
          2. encrypt_cvv(key, cvv) — pure-Python RSA-PKCS1v15 encryption.
          3. tab.evaluate(fetch POST /api/checkout-customer/encrypted-pan)
             with the ciphertext payload. Server returns {cardToken: ...}.

        Returns the server response dict on success, None on any failure
        (caller falls back to DOM CVV entry — preserves regression-free).

        Env gate `WALMART_PIE_CVV=1` should be checked by the caller
        before invoking this; this method always tries when called.
        """
        from walmart.pie import fetch_pie_key, encrypt_cvv

        # 1. Fetch the public key
        key = await fetch_pie_key(self._tab)
        if key is None:
            logger.warning("[HYBRID] PIE: could not fetch public key — falling back")
            return None

        # 2. Encrypt in pure Python (no JS runtime needed)
        try:
            encrypted = encrypt_cvv(key, cvv)
        except ValueError as e:
            logger.warning(f"[HYBRID] PIE: encrypt_cvv rejected input: {e}")
            return None
        except Exception as e:
            logger.warning(f"[HYBRID] PIE: encrypt_cvv raised: {type(e).__name__}: {e}")
            return None

        # 3. POST the encrypted payload via tab.evaluate
        payload = encrypted.to_dict()
        body_json = json.dumps(payload, separators=(",", ":"))
        body_literal = json.dumps(body_json)
        url = "/api/checkout-customer/encrypted-pan"
        js = f"""
            (async () => {{
                const t0 = performance.now();
                try {{
                    const resp = await fetch({json.dumps(url)}, {{
                        method: 'POST',
                        credentials: 'include',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'X-O-Bu': 'WALMART-US',
                            'X-O-Mart': 'B2C',
                            'X-O-Platform': 'rweb',
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Dest': 'empty',
                            'Referer': location.origin + '/checkout',
                        }},
                        body: {body_literal},
                    }});
                    const ms = performance.now() - t0;
                    const text = await resp.text();
                    let json_ = null;
                    try {{ json_ = JSON.parse(text); }} catch(_) {{}}
                    return {{ok: resp.ok, status: resp.status, ms, text: text.slice(0, 2000), json: json_}};
                }} catch (e) {{
                    return {{ok: false, status: 0, ms: performance.now()-t0, error: String(e).slice(0, 200)}};
                }}
            }})()
        """
        try:
            res = await self._tab.evaluate(js, await_promise=True)
        except TypeError:
            res = await self._tab.evaluate(js)
        except Exception as e:
            logger.warning(f"[HYBRID] PIE submit raised: {e}")
            return None

        if not isinstance(res, dict) or not res.get("ok"):
            logger.warning(
                f"[HYBRID] PIE submit failed: status={(res or {}).get('status')} "
                f"err={(res or {}).get('error') or (res or {}).get('text', '')[:200]!r}"
            )
            return None

        body = res.get("json")
        if not body:
            logger.warning("[HYBRID] PIE submit: response not JSON")
            return None

        logger.info(
            f"[HYBRID] PIE submit OK status={res.get('status')} ms={res.get('ms'):.0f} "
            f"token={(body.get('cardToken') or '?')[:24]}"
        )
        return body

    async def place_order(self, ctx: dict) -> Optional[dict]:
        """POST CreateContract — Walmart's Place Order mutation.

        Server reads tip / slot / address / payment from cart state. Returns
        the parsed response dict with `data.createPurchaseContract.id` =
        the order's `pcid` (matches the /thankyou?pcid=... URL parameter).
        """
        payload = {
            "variables": {
                "createContractInput": {
                    "cartId": ctx["cartId"],
                    "consumerContext": {
                        "supportedPayments": SUPPORTED_PAYMENTS,
                    },
                    "isClarityInSignupEnabled": True,
                    "features": CREATE_CONTRACT_FEATURES,
                },
                "promosEnable": True,
                "wplusEnabled": True,
                "isACCEnabled": True,
                "charityOfChoiceEnabled": True,
                "enablePhotoMigration": True,
                "wplusSplashSignupEnabled": True,
                "enableTYPinDrop": True,
                "enablePaidSignupBanner": True,
                "enableAccessPoint": False,
                "enableMsiMci": True,
                "enableCartLevelMSI": False,
                "enableTaxBreakdown": False,
                "enableCashiCashback": False,
                "enableRewardsBanner": True,
                "enableInvoicing": False,
                "enableWholeDollarDonation": True,
                "orgContextEnabled": False,
                "enableSpendLimit": False,
                "enableI18n": True,
                "enablePFS": True,
                "enableAOSBuyNow": True,
                "allowSuggestedSlotsACC": True,
                "enableWFSGlobal": False,
                "enableGEP": True,
                "enablePhoneCountryIso": True,
                "enableCountryCode": True,
                "enableIsEligibleForFreeTrialV1": True,
                "enableUpstreamErrorCode": False,
                "enableGEPForPilot": True,
                "enablePaymentMethodPromotion": False,
                "enable3pEGiftCardPersonalization": True,
                "enableAOSRearchitect": False,
                "enableUnscheduledSlaGroups": False,
                "enableCCAFlow": False,
                "enableAppleCareFreeTrials": True,
                "enableAOSModuleAttribute": True,
                "includeFitment": False,
                "enableFFGroupPickupPerson": False,
                "enableCustomIncludedOffer": False,
                "enablePostPayTobacco": False,
                "enableDestinationTax": True,
                "enableItemTypeAttributes": False,
                "enablePrescriptionDetails": False,
                "enableSubscriptionsInTransactionDiscount": False,
                "enableTEEnhancement": False,
                "enableItemLevelTE": False,
                "enablePayWithPoints": False,
                "enablePayWithPointsRedemptionCheck": False,
                "enableDsClarity": True,
                "enableLoyaltyRedeemPoints": False,
                "enableOutOfCountry": False,
                "enableE2EPickupEnhancement": True,
                "enableCheckoutNonConfigBundles": False,
                "enableFisPayments": False,
                "enableExpressSlot2": False,
                "enablePriceClarity": False,
                "enableSavingsBreakup": True,
                "enableSubUnsupportedPayments": False,
                "enableWplusSubscribeAndSave": False,
                "enableResilientDependencies": False,
                "enableCXOExpressPickupPhase1": False,
                "enableTheFarmersDog": True,
                "enableMOQVariants": False,
            }
        }
        url = f"/orchestra/cartxo/graphql/CreateContract/{CREATE_CONTRACT_HASH}"
        return await self._post_graphql("CreateContract", url, payload, "CreateContract")

    async def _get_graphql(
        self, op_name: str, url: str, apollo_name: str,
    ) -> Optional[dict]:
        """Execute a GraphQL GET via `tab.evaluate(fetch(...))`."""
        js = f"""
            (async () => {{
                const t0 = performance.now();
                try {{
                    const resp = await fetch({json.dumps(url)}, {{
                        method: 'GET',
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json',
                            'Content-Type': 'application/json',
                            'Accept-Language': 'en-US',
                            'X-APOLLO-OPERATION-NAME': {json.dumps(apollo_name)},
                            'X-O-Bu': 'WALMART-US',
                            'X-O-Mart': 'B2C',
                            'X-O-Platform': 'rweb',
                            'X-O-Segment': 'oaoh',
                            'X-O-Ccm': 'server',
                            'X-O-Gql-Query': 'query ' + {json.dumps(apollo_name)},
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Dest': 'empty',
                            'Referer': location.origin + '/cart',
                        }},
                    }});
                    const ms = performance.now() - t0;
                    const text = await resp.text();
                    let json_ = null;
                    try {{ json_ = JSON.parse(text); }} catch(_) {{}}
                    return {{
                        ok: resp.ok,
                        status: resp.status,
                        ms: ms,
                        text: text.slice(0, 50000),
                        json: json_,
                    }};
                }} catch (e) {{
                    return {{
                        ok: false,
                        status: 0,
                        ms: performance.now() - t0,
                        error: (e && e.message || String(e)).slice(0, 200),
                    }};
                }}
            }})()
        """
        try:
            res = await self._tab.evaluate(js, await_promise=True)
        except TypeError:
            res = await self._tab.evaluate(js)
        except Exception as e:
            logger.warning("[HYBRID] %s GET raised: %s", op_name, e)
            return None
        if not isinstance(res, dict):
            return None
        if not res.get("ok"):
            logger.warning(
                "[HYBRID] %s GET failed: status=%s err=%r",
                op_name, res.get("status"), res.get("error") or res.get("text", "")[:200],
            )
            return None
        body = res.get("json")
        if body is None:
            logger.warning("[HYBRID] %s GET returned non-JSON", op_name)
            return None

        if body.get("errors"):
            # APQ miss? Retry with full-query body via POST (GET-side APQ
            # convention varies; POST with extensions.persistedQuery is the
            # safest universal fallback path for Apollo servers).
            if is_apq_miss(body):
                logger.warning(
                    "[APQ] %s GET: PERSISTED_QUERY_NOT_FOUND — retrying via POST with full query body",
                    apollo_name,
                )
                retry_body = await self._get_graphql_full_query(
                    op_name, url, apollo_name,
                )
                if retry_body is not None:
                    return retry_body
                logger.warning("[APQ] %s GET full-query retry also failed", apollo_name)
                return None

            logger.warning(
                "[HYBRID] %s GET returned errors: %s",
                op_name, (body or {}).get("errors", "no-json"),
            )
            return None
        logger.info(
            "[HYBRID] %s GET ok: status=%s ms=%.0f",
            op_name, res.get("status"), res.get("ms"),
        )
        return body

    async def _get_graphql_full_query(
        self, op_name: str, url: str, apollo_name: str,
    ) -> Optional[dict]:
        """GET-side APQ fallback. Real Apollo Server accepts both GET (with
        query in URL) and POST (with query in body). We use POST to keep
        the wire shape consistent with the POST-side fallback and avoid
        URL-length limits for big queries.

        Strategy: extract `variables` from the original GET URL, build the
        POST body with extensions.persistedQuery + query + variables.
        """
        query_body = apq_query_for(apollo_name)
        if query_body is None:
            logger.warning("[APQ] %s GET: no captured query body", apollo_name)
            return None

        # Parse variables out of the GET URL
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        variables_raw = (qs.get("variables") or ["{}"])[0]
        try:
            variables = json.loads(variables_raw)
        except (json.JSONDecodeError, TypeError):
            variables = {}

        # Strip query params from the base URL — POST goes to the bare
        # /orchestra/...graphql/<op>/<hash> path
        post_url = urllib.parse.urlunparse(parsed._replace(query=""))

        payload = {
            "query": query_body,
            "variables": variables,
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": parsed.path.rsplit("/", 1)[-1] if "/" in parsed.path else "",
                },
            },
        }

        body_json = json.dumps(payload, separators=(",", ":"))
        body_literal = json.dumps(body_json)
        js = f"""
            (async () => {{
                const t0 = performance.now();
                try {{
                    const resp = await fetch({json.dumps(post_url)}, {{
                        method: 'POST',
                        credentials: 'include',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'X-APOLLO-OPERATION-NAME': {json.dumps(apollo_name)},
                            'X-O-Bu': 'WALMART-US',
                            'X-O-Mart': 'B2C',
                            'X-O-Platform': 'rweb',
                            'X-O-Segment': 'oaoh',
                            'X-O-Ccm': 'server',
                            'X-O-Gql-Query': 'query ' + {json.dumps(apollo_name)},
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Dest': 'empty',
                            'Referer': location.origin + '/cart',
                        }},
                        body: {body_literal},
                    }});
                    const ms = performance.now() - t0;
                    const text = await resp.text();
                    let json_ = null;
                    try {{ json_ = JSON.parse(text); }} catch(_) {{}}
                    return {{ok: resp.ok, status: resp.status, ms, text: text.slice(0, 8000), json: json_}};
                }} catch (e) {{
                    return {{ok: false, status: 0, ms: performance.now()-t0, error: String(e).slice(0, 200)}};
                }}
            }})()
        """
        try:
            res = await self._tab.evaluate(js, await_promise=True)
        except TypeError:
            res = await self._tab.evaluate(js)
        except Exception as e:
            logger.warning("[APQ] %s GET retry raised: %s", apollo_name, e)
            return None

        if not isinstance(res, dict) or not res.get("ok"):
            return None
        body = res.get("json")
        if not body or body.get("errors"):
            return None
        logger.info(
            "[APQ] %s GET-retry-via-POST OK status=%s ms=%.0f",
            apollo_name, res.get("status"), res.get("ms"),
        )
        return body

    async def _post_graphql(
        self, op_name: str, url: str, payload: dict, apollo_name: str,
    ) -> Optional[dict]:
        """Execute a GraphQL POST via `tab.evaluate(fetch(...))`.

        Headers mirror what real Walmart pages send so the request is
        indistinguishable from a UI-triggered call.
        """
        body_json = json.dumps(payload, separators=(",", ":"))
        # Embed body via JSON.parse to avoid escape headaches in template
        body_literal = json.dumps(body_json)
        js = f"""
            (async () => {{
                const t0 = performance.now();
                try {{
                    const resp = await fetch({json.dumps(url)}, {{
                        method: 'POST',
                        credentials: 'include',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'Accept-Language': 'en-US',
                            'X-APOLLO-OPERATION-NAME': {json.dumps(apollo_name)},
                            'X-O-Bu': 'WALMART-US',
                            'X-O-Mart': 'B2C',
                            'X-O-Platform': 'rweb',
                            'X-O-Segment': 'oaoh',
                            'X-O-Platform-Version': 'main-1.198.0-022d4d',
                            'X-O-Ccm': 'server',
                            'X-O-Gql-Query': 'mutation ' + {json.dumps(apollo_name)},
                            'X-Enable-Server-Timing': '1',
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Dest': 'empty',
                            'Referer': location.origin + '/cart',
                        }},
                        body: {body_literal},
                    }});
                    const ms = performance.now() - t0;
                    const text = await resp.text();
                    let json_ = null;
                    try {{ json_ = JSON.parse(text); }} catch(_) {{}}
                    return {{
                        ok: resp.ok,
                        status: resp.status,
                        ms: ms,
                        text: text.slice(0, 8000),  // truncate for log sanity
                        json: json_,
                    }};
                }} catch (e) {{
                    return {{
                        ok: false,
                        status: 0,
                        ms: performance.now() - t0,
                        error: (e && e.message || String(e)).slice(0, 200),
                    }};
                }}
            }})()
        """

        try:
            res = await self._tab.evaluate(js, await_promise=True)
        except TypeError:
            # Some zendriver versions don't accept await_promise
            res = await self._tab.evaluate(js)
        except Exception as e:
            logger.warning("[HYBRID] %s POST raised: %s", op_name, e)
            return None

        if not isinstance(res, dict):
            logger.warning("[HYBRID] %s POST returned non-dict: %r", op_name, res)
            return None

        status = res.get("status")
        ms = res.get("ms")
        ok = res.get("ok")

        if not ok:
            err = res.get("error") or res.get("text", "")[:300]
            logger.warning(
                "[HYBRID] %s POST failed: status=%s ms=%.0f err=%r",
                op_name, status, ms or 0, err,
            )
            return None

        body = res.get("json")
        if body is None:
            logger.warning(
                "[HYBRID] %s POST status=%s ms=%.0f but body not JSON: %r",
                op_name, status, ms, res.get("text", "")[:200],
            )
            return None

        if body.get("errors"):
            # APQ miss? Try the full-query fallback once.
            if is_apq_miss(body):
                logger.warning(
                    "[APQ] %s POST: PERSISTED_QUERY_NOT_FOUND — retrying with full query body",
                    apollo_name,
                )
                retry_body = await self._post_graphql_full_query(
                    op_name, url, payload, apollo_name,
                )
                if retry_body is not None:
                    return retry_body
                logger.warning(
                    "[APQ] %s POST: full-query retry also failed; falling back to DOM",
                    apollo_name,
                )
                return None

            logger.warning(
                "[HYBRID] %s POST returned GraphQL errors: %s",
                op_name, body["errors"][:3] if isinstance(body["errors"], list) else body["errors"],
            )
            return None

        logger.info(
            "[HYBRID] %s POST ok: status=%s ms=%.0f",
            op_name, status, ms,
        )
        return body

    async def _post_graphql_full_query(
        self, op_name: str, url: str, payload: dict, apollo_name: str,
    ) -> Optional[dict]:
        """APQ fallback POST — sends the request again with the `query`
        field included carrying the full operation body. On success the
        server caches the new hash; we don't have to manually update
        constants in code because the next normal POST will hit the
        warm cache.

        Returns the GraphQL response body on success, None on any failure.
        """
        query_body = apq_query_for(apollo_name)
        if query_body is None:
            logger.warning(
                "[APQ] %s: no captured query body — cannot retry. "
                "Capture via walmart/apq_capture.py and add to "
                "walmart/checkout_apq_queries.json.",
                apollo_name,
            )
            return None

        # Apollo APQ retry shape: include the same persistedQuery extensions
        # block AND a `query` field with the full operation body. The
        # server uses the query body to compute and cache the new hash.
        retry_payload = dict(payload)
        retry_payload["query"] = query_body

        body_json = json.dumps(retry_payload, separators=(",", ":"))
        body_literal = json.dumps(body_json)
        js = f"""
            (async () => {{
                const t0 = performance.now();
                try {{
                    const resp = await fetch({json.dumps(url)}, {{
                        method: 'POST',
                        credentials: 'include',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'X-APOLLO-OPERATION-NAME': {json.dumps(apollo_name)},
                            'X-O-Bu': 'WALMART-US',
                            'X-O-Mart': 'B2C',
                            'X-O-Platform': 'rweb',
                            'X-O-Segment': 'oaoh',
                            'X-O-Ccm': 'server',
                            'X-O-Gql-Query': 'mutation ' + {json.dumps(apollo_name)},
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Dest': 'empty',
                            'Referer': location.origin + '/cart',
                        }},
                        body: {body_literal},
                    }});
                    const ms = performance.now() - t0;
                    const text = await resp.text();
                    let json_ = null;
                    try {{ json_ = JSON.parse(text); }} catch(_) {{}}
                    return {{ok: resp.ok, status: resp.status, ms, text: text.slice(0, 8000), json: json_}};
                }} catch (e) {{
                    return {{ok: false, status: 0, ms: performance.now()-t0, error: String(e).slice(0, 200)}};
                }}
            }})()
        """
        try:
            res = await self._tab.evaluate(js, await_promise=True)
        except TypeError:
            res = await self._tab.evaluate(js)
        except Exception as e:
            logger.warning("[APQ] %s retry raised: %s", apollo_name, e)
            return None

        if not isinstance(res, dict) or not res.get("ok"):
            logger.warning("[APQ] %s retry failed: %r",
                           apollo_name, (res or {}).get("error") or (res or {}).get("text", "")[:200])
            return None

        body = res.get("json")
        if body is None:
            logger.warning("[APQ] %s retry got non-JSON body", apollo_name)
            return None
        if body.get("errors"):
            # The retry shouldn't get APQ_NOT_FOUND again (we sent the query
            # body). If we still get errors, it's a real server-side issue
            # (bad query syntax, schema mismatch, etc).
            logger.warning("[APQ] %s retry returned errors: %s",
                           apollo_name, body["errors"][:3])
            return None
        logger.info(
            "[APQ] %s retry OK status=%s ms=%.0f — hash should now be cached server-side",
            apollo_name, res.get("status"), res.get("ms"),
        )
        return body

    @staticmethod
    def parse_update_items(body: dict) -> Optional[dict]:
        """Extract committed qty + cart status from updateItems response.

        Returns {committed_qty, checkoutable, line_count} or None.
        """
        try:
            ui = body["data"]["updateItems"]
            lines = ui.get("lineItems") or []
            committed = lines[0]["quantity"] if lines else 0
            return {
                "committed_qty": committed,
                "checkoutable": bool(ui.get("checkoutable")),
                "line_count": len(lines),
            }
        except (KeyError, TypeError, IndexError):
            return None

    @staticmethod
    def parse_create_contract(body: dict) -> Optional[dict]:
        """Extract order id (pcid) + status from CreateContract response.

        Returns {pcid, order_status, amount_paid, payment_last4} or None.
        """
        try:
            pc = body["data"]["createPurchaseContract"]
            payments = pc.get("payments") or []
            return {
                "pcid": pc.get("id"),
                "order_status": (pc.get("order") or {}).get("status"),
                "amount_paid": payments[0].get("amountPaid") if payments else None,
                "payment_last4": payments[0].get("lastFour") if payments else None,
            }
        except (KeyError, TypeError, IndexError):
            return None
