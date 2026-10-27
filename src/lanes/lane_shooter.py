"""LaneShooter — curl_cffi ATC POST using harvested Shape tokens + account auth.

Each shooter is bound to one Lane (account x proxy). When the race loop
calls fire(), the shooter:
  1. Withdraws one ShapeToken from the harvester's bank
  2. Reads the account's current auth cookies from the session file
  3. Fires the ATC POST via curl_cffi with:
     - Shape x-* headers from the token (the fingerprint/sensor data)
     - Account cookies (for authentication)
     - TLS impersonation matching the harvester Chrome's version
     - Proxy routing through the same IP as the harvester
  4. Returns the result for the lane's cadence engine

The curl_cffi request has no CDP round-trip overhead — it's a direct
HTTP POST, typically completing in 200-800ms vs 1-3s for tab.evaluate.

Validated by E-SHOOTER (2026-10-23): curl_cffi from the same IP as the
harvester Chrome passes Shape.  The token is bound to the IP, not the
cookie set.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .shape_harvester import ShapeToken, ShapeTokenBank

_ROOT = Path(__file__).resolve().parents[2]

ATC_URL = ("https://carts.target.com/web_checkouts/v1/cart_items"
           "?field_groups=CART%2CCART_ITEMS%2CSUMMARY"
           "&key=9f36aeafbe60771e321a7cc95a78140772ab3e96")


SHAPE_COOKIES = frozenset({
    '_abck', 'bm_sv', 'bm_sz', 'bm_mi', 'ak_bmsc', '_bm_dih',
})

AUTH_COOKIES = frozenset({
    'accessToken', 'login-session', 'idToken', 'refreshToken',
    'fiatsCookie', 'ci_pixmgr', 'mystate',
})


def _merge_cookies(harvested: str, account: str) -> str:
    """Merge harvested (Shape) cookies with account auth cookies.

    Shape cookies (_abck, bm_sv etc) come from the harvester Chrome and are
    part of the Shape signature.  Auth cookies (accessToken, login-session etc)
    come from the account's session file and identify the buyer.  Both must be
    present: Shape validates its own cookies, Target validates the auth ones.
    """
    combined = {}
    for raw in (harvested, account):
        for pair in (raw or '').split(';'):
            pair = pair.strip()
            if '=' in pair:
                n, _, v = pair.partition('=')
                combined[n.strip()] = v.strip()
    return '; '.join(f'{n}={v}' for n, v in combined.items())


def _patch_body(captured_body: str, tcin: str, quantity: int) -> str:
    """Patch the captured ATC body with the target TCIN and quantity.

    Shape may sign the body, so we keep the original structure and only
    change the fields that need to differ for the actual shot.
    """
    try:
        data = json.loads(captured_body)
        if 'cart_item' in data:
            data['cart_item']['tcin'] = str(tcin)
            data['cart_item']['quantity'] = int(quantity)
        return json.dumps(data)
    except Exception:
        return _build_atc_body(tcin, quantity)


def _build_atc_body(tcin: str, quantity: int) -> str:
    return json.dumps({
        "cart_item": {
            "item_channel_id": "10",
            "tcin": str(tcin),
            "quantity": int(quantity),
        },
        "cart_type": "REGULAR",
        "channel_id": "10",
        "shopping_context": "DIGITAL",
    })


def _impersonate_for(ua: str) -> str:
    """Pick the best curl_cffi Chrome impersonation for the harvester's UA."""
    m = re.search(r"Chrome/(\d+)", ua or "")
    major = int(m.group(1)) if m else 0
    try:
        from curl_cffi import requests as cr
        names = [x.name for x in cr.BrowserType] if hasattr(cr, "BrowserType") else []
    except Exception:
        names = []
    cands = sorted({
        int(re.sub(r"\D", "", n) or 0): n
        for n in names if n.startswith("chrome") and re.sub(r"\D", "", n)
    }.items())
    best = None
    for num, name in cands:
        if num <= major:
            best = name
    return best or "chrome"


def _read_account_cookies(session_path: str) -> str:
    """Read the account's auth cookies from its session file.

    Returns a Cookie header string: "name1=val1; name2=val2; ..."
    """
    try:
        fp = _ROOT / session_path if not os.path.isabs(session_path) else Path(session_path)
        data = json.loads(fp.read_text(encoding='utf-8'))
        cookies = data.get('cookies') if isinstance(data, dict) else data
        if not cookies:
            return ''
        pairs = []
        for c in cookies:
            if not isinstance(c, dict):
                continue
            domain = str(c.get('domain', ''))
            if 'target.com' not in domain:
                continue
            name = c.get('name', '')
            value = c.get('value')
            if name and value is not None:
                pairs.append(f"{name}={value}")
        return '; '.join(pairs)
    except Exception:
        return ''


class LaneShooter:
    """Fires ATC POSTs via curl_cffi using Shape tokens from a harvester bank.

    One LaneShooter per Lane (account x proxy). The bank is shared across
    lanes on the same IP.
    """

    def __init__(
        self,
        bank: ShapeTokenBank,
        proxy_url: str,
        session_path: str,
        account_id: str,
        ip_label: str = '',
        cookie_refresh_s: float = 60.0,
    ):
        self.bank = bank
        self.proxy_url = proxy_url
        self.session_path = session_path
        self.account_id = account_id
        self.ip_label = ip_label or 'home'
        self._cookie_refresh_s = cookie_refresh_s

        self._cached_cookies: str = ''
        self._cookies_last_read: float = 0.0

        # Stats
        self.total_shots = 0
        self.total_no_token = 0
        self.total_no_cookies = 0
        self.total_errors = 0

    def _refresh_cookies(self) -> str:
        now = time.time()
        if (now - self._cookies_last_read) >= self._cookie_refresh_s or not self._cached_cookies:
            self._cached_cookies = _read_account_cookies(self.session_path)
            self._cookies_last_read = now
        return self._cached_cookies

    def fire(self, tcin: str, quantity: int) -> Dict[str, Any]:
        """Fire one ATC POST via curl_cffi.

        Returns:
            {
                'atc_status': int,    # HTTP status (429/401/201/424/0)
                'body_hint': str,     # first 200 chars of body
                'success': bool,      # True if 200 or 201
                'cart_id': str,       # from the 201 body, or ''
                'error': str,         # error message if any
                'token_age_s': float, # age of the Shape token used
                'rt_ms': int,         # round-trip time in ms
                'source': 'shooter',  # identifies this as a lane shot
            }
        """
        self.total_shots += 1

        # 1. Get a Shape token from the bank
        token = self.bank.withdraw()
        if token is None:
            self.total_no_token += 1
            return {
                'atc_status': 0,
                'body_hint': 'NO_SHAPE_TOKEN',
                'success': False,
                'cart_id': '',
                'error': 'no_shape_token',
                'token_age_s': 0,
                'rt_ms': 0,
                'source': 'shooter',
            }

        # 2. Get account cookies
        cookies = self._refresh_cookies()
        if not cookies:
            self.total_no_cookies += 1
            return {
                'atc_status': 0,
                'body_hint': 'NO_ACCOUNT_COOKIES',
                'success': False,
                'cart_id': '',
                'error': 'no_account_cookies',
                'token_age_s': token.age_s,
                'rt_ms': 0,
                'source': 'shooter',
            }

        # 3. Build headers: merge Shape cookies with account auth cookies
        headers = dict(token.headers)
        harvested_cookie = ''
        for k in list(headers.keys()):
            if k.lower() == 'cookie':
                harvested_cookie = headers.pop(k)
                break
        merged = _merge_cookies(harvested_cookie, cookies)
        headers['cookie'] = merged
        imp = _impersonate_for(token.user_agent)

        # 4. Build proxy config
        proxies = None
        if self.proxy_url:
            proxies = {"http": self.proxy_url, "https": self.proxy_url}

        # 5. Fire — use captured body (Shape may sign it), patching only the TCIN
        body = _patch_body(token.body, tcin, quantity) if token.body else _build_atc_body(tcin, quantity)
        t0 = time.perf_counter()
        try:
            from curl_cffi import requests as cr
            r = cr.post(
                ATC_URL,
                headers=headers,
                data=body,
                impersonate=imp,
                proxies=proxies,
                timeout=25,
                http_version=None,
            )
            rt_ms = int((time.perf_counter() - t0) * 1000)
            rh = {k.lower(): v for k, v in r.headers.items()}
            status = r.status_code
            resp_body = r.text[:200] if r.text else ''
            cart_id = ''
            try:
                parsed = json.loads(r.text)
                if parsed and parsed.get('cart_id'):
                    cart_id = str(parsed['cart_id'])
            except Exception:
                pass

            success = status in (200, 201)
            key = rh.get('tgt-cart-error-key', '')
            body_hint = key if key else resp_body[:200]

            return {
                'atc_status': status,
                'body_hint': body_hint,
                'success': success,
                'cart_id': cart_id,
                'error': '',
                'token_age_s': round(token.age_s, 1),
                'rt_ms': rt_ms,
                'source': 'shooter',
                'hop': 'x-ssx-hop' in rh,
                'impersonate': imp,
            }

        except Exception as e:
            rt_ms = int((time.perf_counter() - t0) * 1000)
            self.total_errors += 1
            return {
                'atc_status': 0,
                'body_hint': f'{type(e).__name__}: {str(e)[:100]}',
                'success': False,
                'cart_id': '',
                'error': str(e),
                'token_age_s': round(token.age_s, 1),
                'rt_ms': rt_ms,
                'source': 'shooter',
            }

    def stats(self) -> Dict[str, Any]:
        return {
            'account': self.account_id,
            'ip': self.ip_label,
            'shots': self.total_shots,
            'no_token': self.total_no_token,
            'no_cookies': self.total_no_cookies,
            'errors': self.total_errors,
        }
