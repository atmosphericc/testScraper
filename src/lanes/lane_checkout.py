"""LaneCheckout — hands a 201 cart from a lane shooter to the account's Chrome.

When a lane's curl_cffi shooter gets a 201 (cart created), the cart exists
server-side for that account. The account's existing Chrome browser then
runs pre_checkout -> CVV -> place_order via tab.evaluate(), exactly like
the existing fast-lane chain but skipping the ATC step (the cart is
already created).

This is the bridge between the lane architecture (parallel curl_cffi
shooters) and the existing checkout pipeline (browser-based, per-account).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Dict, Optional


def _checkout_js(tcin: str, quantity: int, cvv_digits: str) -> str:
    """JS chain for pre_checkout -> CVV -> place_order on an existing cart.

    This is the second half of _api_fast_lane, adapted to run standalone
    on a cart that was created by the lane shooter.
    """
    pre_url = ('https://carts.target.com/web_checkouts/v1/pre_checkout'
               '?cart_type=REGULAR&field_groups=CART,CART_ITEMS,DELIVERY_WINDOWS,'
               'PAYMENT_INSTRUCTIONS,PROMOTION_CODES,SUMMARY,ADDRESSES')
    po_url = ('https://carts.target.com/web_checkouts/v1/checkout'
              '?cart_type=REGULAR'
              '&field_groups=ADDRESSES%2CCART%2CCART_ITEMS%2CFINANCE_PROVIDERS'
              '%2CPAYMENT_INSTRUCTIONS%2CPICKUP_INSTRUCTIONS%2CPROMOTION_CODES%2CSUMMARY'
              '&key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14')

    cvv_first = 'true' if cvv_digits else 'false'

    return f"""(async () => {{
        const mk = (ref) => ({{
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Origin': 'https://www.target.com',
            'Referer': ref,
            'x-application-name': 'web',
        }});
        const out = {{
            pre: {{status: 0, n: 0, tcins: [], pi: [], cart_id: '', body: ''}},
            po:  {{status: 0, body: '', fired: false}},
            cvv: {{put: -1, first: {cvv_first}, reshot: false, po1: 0}},
            skip: '',
            source: 'lane_checkout'
        }};

        // 1. pre_checkout — hydrate the cart
        try {{
            const r2 = await fetch('{pre_url}', {{
                method: 'POST', credentials: 'include',
                headers: mk('https://www.target.com/cart'),
                body: JSON.stringify({{cart_type: 'REGULAR'}})
            }});
            const t2 = await r2.text();
            out.pre.status = r2.status;
            if (r2.status < 200 || r2.status > 299) {{
                out.pre.body = String(t2 || '').slice(0, 300);
            }}
            try {{
                const p2 = JSON.parse(t2);
                if (p2 && p2.cart_id) out.pre.cart_id = p2.cart_id;
                const items = (p2 && p2.cart_items) || [];
                out.pre.n = items.length;
                out.pre.tcins = items.map(i => i && i.tcin);
                const pis = (p2 && p2.payment_instructions) || [];
                out.pre.pi = pis.slice(0, 3).map(pi => ({{
                    id: pi && (pi.payment_instruction_id || pi.id),
                    type: pi && pi.payment_type,
                    cvv: pi && (pi.cvv_required !== undefined ? pi.cvv_required
                         : pi.requires_cvv !== undefined ? pi.requires_cvv : null)
                }}));
                // Quantity guard
                let _qs = 0;
                for (const _it of items) {{
                    if (!_it || String(_it.tcin) !== '{tcin}') continue;
                    const _qv = Number(_it.quantity);
                    if (Number.isFinite(_qv)) _qs += _qv;
                }}
                out.pre.qty = _qs;
            }} catch(_) {{}}
        }} catch(e) {{
            out.pre.status = 0;
        }}

        if (out.pre.status !== 200 && out.pre.status !== 201) {{
            out.skip = 'pre_' + out.pre.status; return out;
        }}

        // Foreign-item guard (stringify both sides — pre_checkout may return a
        // numeric tcin; a strict !== against the injected string would reject
        // EVERY legit cart. Matches the qty guard above and the proven path.)
        if (out.pre.tcins.some(t => t && String(t) !== '{tcin}')) {{
            out.skip = 'foreign_cart_item'; return out;
        }}

        // Lower-bound gate: our line must actually be present (matches the proven
        // path's cart_empty / cart_qty_unknown — never place-order a cart we
        // can't confirm holds our item).
        if (!(out.pre.n >= 1)) {{
            out.skip = 'cart_empty'; return out;
        }}
        if (out.pre.qty === null || out.pre.qty === undefined || !(out.pre.qty >= 1)) {{
            out.skip = 'cart_qty_unknown'; return out;
        }}

        // Quantity guard (upper bound)
        if (out.pre.qty !== null && out.pre.qty !== undefined && out.pre.qty > {int(quantity)}) {{
            out.skip = 'cart_qty_over'; return out;
        }}

        // 2. CVV PUT
        const CVV = '{cvv_digits}';
        const cvvPut = async () => {{
            const pi0 = out.pre.pi && out.pre.pi[0];
            const piId = pi0 && pi0.id;
            if (!CVV || !piId) return -1;
            try {{
                const rc = await fetch(
                    'https://carts.target.com/checkout_payments/v1/payment_instructions/'
                    + piId + '?key=e59ce3b531b2c39afb2e2b8a71ff10113aac2a14',
                    {{
                        method: 'PUT', credentials: 'include',
                        headers: {{
                            'Content-Type': 'application/json',
                            'Accept': 'application/json',
                            'Origin': 'https://www.target.com',
                            'x-application-name': 'web'
                        }},
                        body: JSON.stringify({{
                            card_details: {{cvv: CVV}},
                            cart_id: out.pre.cart_id || undefined,
                            payment_type: 'CARD',
                            wallet_mode: 'NONE'
                        }})
                    }});
                await rc.text();
                return rc.status;
            }} catch(e) {{ return 0; }}
        }};
        if (out.cvv.first) {{
            out.cvv.put = await cvvPut();
        }}

        // 3. Place order
        out.po.fired = true;
        try {{
            const r3 = await fetch('{po_url}', {{
                method: 'POST', credentials: 'include',
                headers: mk('https://www.target.com/checkout'),
                body: JSON.stringify({{cart_type: 'REGULAR', channel_id: '10'}})
            }});
            const t3 = await r3.text();
            out.po.status = r3.status;
            out.po.body = t3.slice(0, 2000);
        }} catch(e) {{
            out.po.status = 0; out.po.body = String(e);
        }}

        // CVV recovery: place-order 400 + no prior CVV PUT
        if (out.po.status === 400 && CVV && out.cvv.put === -1) {{
            out.cvv.po1 = out.po.status;
            out.cvv.put = await cvvPut();
            if (out.cvv.put >= 200 && out.cvv.put < 300) {{
                out.cvv.reshot = true;
                try {{
                    const r4 = await fetch('{po_url}', {{
                        method: 'POST', credentials: 'include',
                        headers: mk('https://www.target.com/checkout'),
                        body: JSON.stringify({{cart_type: 'REGULAR', channel_id: '10'}})
                    }});
                    const t4 = await r4.text();
                    out.po.status = r4.status;
                    out.po.body = t4.slice(0, 2000);
                }} catch(e) {{
                    out.po.status = 0; out.po.body = String(e);
                }}
            }}
        }}
        return out;
    }})()"""


class LaneCheckout:
    """Executes checkout on an account's Chrome tab after a lane ATC success.

    Each account gets one LaneCheckout instance. When any lane for this
    account gets a 201 cart, it calls checkout() which submits the
    pre_checkout -> CVV -> place_order chain to the account's Worker's
    asyncio loop.
    """

    def __init__(self, account_id: str, cvv: str = ''):
        self.account_id = account_id
        self.cvv = cvv
        self.total_checkouts = 0
        self.total_orders = 0
        self.total_failures = 0

    async def checkout_async(
        self,
        tab: Any,
        tcin: str,
        quantity: int,
        cart_id: str = '',
        pe: Any = None,
    ) -> Dict[str, Any]:
        """Run pre_checkout -> place_order on the account's Chrome tab.

        Parameters
        ----------
        tab : zendriver tab
            The account's live Chrome tab (from the Worker's SessionManager).
        tcin : str
            The product that was carted.
        quantity : int
            Units to buy.
        cart_id : str
            Cart ID from the 201 response (informational).

        Returns
        -------
        dict with pre:{status, ...}, po:{status, body, fired}, cvv:{...}, skip:str
        """
        self.total_checkouts += 1
        t0 = time.time()

        print(f"[LANE_CHECKOUT] {self.account_id} starting checkout for "
              f"{tcin} (cart_id={cart_id[:12]}...)")

        try:
            js = _checkout_js(tcin, quantity, self.cvv)
            # Hold the account's page lock (the main path's PurchaseExecutor
            # _page_lock, wired onto the SessionManager as _drop_guard_lock) so a
            # lane checkout and a main-path checkout/ATC never drive the SAME
            # account's one Chrome tab concurrently — interleaved Runtime.evaluate
            # chains on one CDP session can cross responses. The claim keys on
            # (account, TCIN), so two DIFFERENT TCINs on one account can still
            # reach this tab at once without this lock. Runs on the worker's loop
            # (same loop that owns the tab and the lock), so acquisition is valid.
            _lk = getattr(pe, '_page_lock', None)
            if _lk is not None:
                async with _lk:
                    raw = await asyncio.wait_for(
                        tab.evaluate(js, await_promise=True), timeout=15.0)
            else:
                raw = await asyncio.wait_for(
                    tab.evaluate(js, await_promise=True), timeout=15.0)

            if isinstance(raw, str):
                result = json.loads(raw)
            elif isinstance(raw, dict):
                result = raw
            else:
                result = {'pre': {'status': 0}, 'po': {'status': 0, 'body': str(raw), 'fired': False}, 'skip': 'bad_eval'}

            elapsed = time.time() - t0
            po_status = result.get('po', {}).get('status', 0)
            po_fired = result.get('po', {}).get('fired', False)
            # Ambiguous-commit (matches the proven path's _po_ambiguous): the
            # place-order POST LEFT but we got no definitive answer (status 0 =
            # fetch threw / timed out, 408, or 5xx). Target's checkout has no
            # idempotency key, so it MAY have committed. Treat as terminal and
            # possibly-ordered — NOT a retriable failure — so the caller never
            # re-fires this account x TCIN (prevents a double charge).
            ambiguous = bool(po_fired) and (
                po_status == 0 or po_status == 408 or (500 <= po_status <= 599))
            result['ambiguous'] = ambiguous

            if po_status in (200, 201) and po_fired:
                self.total_orders += 1
                print(f"[LANE_CHECKOUT] {self.account_id} ORDER PLACED! "
                      f"status={po_status} tcin={tcin} elapsed={elapsed:.2f}s")
            elif ambiguous:
                print(f"[LANE_CHECKOUT] {self.account_id} AMBIGUOUS place-order "
                      f"(fired, status={po_status}) tcin={tcin} elapsed={elapsed:.2f}s "
                      f"— treating as possibly-ordered, NO retry")
            elif result.get('skip'):
                print(f"[LANE_CHECKOUT] {self.account_id} skipped: "
                      f"{result['skip']} tcin={tcin} elapsed={elapsed:.2f}s")
            else:
                self.total_failures += 1
                print(f"[LANE_CHECKOUT] {self.account_id} checkout failed: "
                      f"pre={result.get('pre', {}).get('status')} "
                      f"po={po_status} fired={po_fired} "
                      f"tcin={tcin} elapsed={elapsed:.2f}s")

            result['checkout_elapsed_s'] = round(elapsed, 3)
            result['account'] = self.account_id
            return result

        except asyncio.TimeoutError:
            elapsed = time.time() - t0
            print(f"[LANE_CHECKOUT] {self.account_id} TIMEOUT "
                  f"tcin={tcin} elapsed={elapsed:.2f}s — AMBIGUOUS, no retry")
            # The JS chain timed out; the place-order POST may have fired and
            # committed. Treat as ambiguous (possibly-ordered), NOT a retriable
            # failure, so the caller does not re-fire this account x TCIN.
            return {
                'pre': {'status': 0}, 'po': {'status': 0, 'body': 'timeout', 'fired': True},
                'ambiguous': True, 'skip': '', 'checkout_elapsed_s': round(elapsed, 3),
                'account': self.account_id,
            }
        except Exception as e:
            self.total_failures += 1
            elapsed = time.time() - t0
            print(f"[LANE_CHECKOUT] {self.account_id} error: "
                  f"{type(e).__name__}: {e}")
            return {
                'pre': {'status': 0}, 'po': {'status': 0, 'body': str(e), 'fired': False},
                'skip': 'checkout_error', 'checkout_elapsed_s': round(elapsed, 3),
                'account': self.account_id,
            }

    def checkout(
        self,
        worker: Any,
        tcin: str,
        quantity: int,
        cart_id: str = '',
    ) -> Dict[str, Any]:
        """Synchronous checkout via the Worker's asyncio loop.

        Submits the checkout coroutine to the Worker's event loop and
        blocks until it completes. Called from the lane's thread.
        """
        tab = worker.session_manager._active_tab
        if tab is None:
            return {
                'pre': {'status': 0}, 'po': {'status': 0, 'body': 'no_tab', 'fired': False},
                'skip': 'no_tab', 'account': self.account_id,
            }

        pe = getattr(worker, 'purchase_executor', None)
        coro = self.checkout_async(tab, tcin, quantity, cart_id, pe=pe)

        if hasattr(worker, 'run_async'):
            future = worker.run_async(coro)
            try:
                return future.result(timeout=20.0)
            except FuturesTimeoutError:
                # The worker loop did not return in time, and future.result does
                # NOT cancel the coroutine — the place-order POST can still fire
                # on the worker loop AFTER we give up here. Treat as ambiguous
                # (possibly-ordered), never a retriable failure.
                print(f"[LANE_CHECKOUT] {self.account_id} run_async TIMEOUT "
                      f"tcin={tcin} — AMBIGUOUS, no retry")
                return {
                    'pre': {'status': 0},
                    'po': {'status': 0, 'body': 'future_timeout', 'fired': True},
                    'ambiguous': True, 'skip': '', 'account': self.account_id,
                }
        else:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

    def stats(self) -> Dict[str, Any]:
        return {
            'account': self.account_id,
            'checkouts': self.total_checkouts,
            'orders': self.total_orders,
            'failures': self.total_failures,
        }
