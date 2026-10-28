"""ShapeHarvester — per-IP guest Chrome that continuously produces Shape tokens.

The harvester runs a guest (no-account) Chrome through a specific proxy IP,
navigates to a Target PDP, and repeatedly fires add-to-cart fetches via
tab.evaluate(). Each fetch is intercepted at the CDP Fetch.RequestPaused
stage: the outgoing headers (which contain Shape's x-* sensor tokens) are
captured and the request is aborted so nothing is actually bought.

These captured header sets are stored in a token bank with TTL tracking.
Shooters (lane_shooter.py) draw one token set per ATC attempt.

Architecture validated by E-SHOOTER (2026-10-23): curl_cffi replay of
harvested Shape tokens from the same IP passes Shape (424 ITEM_NOT_READY
on the control TCIN, not 401 keyless).

Each unique exit IP in the lane pool gets ONE ShapeHarvester instance.
Multiple lanes on the same IP share the harvester's token bank.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]

ATC_URL = ("https://carts.target.com/web_checkouts/v1/cart_items"
           "?field_groups=CART%2CCART_ITEMS%2CSUMMARY"
           "&key=9f36aeafbe60771e321a7cc95a78140772ab3e96")

CONTROL_TCIN = "81926151"

HEADER_ORDER = [
    "sec-ch-ua", "x-application-name", "sec-ch-ua-mobile", "user-agent",
    "content-type", "accept", "sec-ch-ua-platform", "origin",
    "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "referer",
    "accept-encoding", "accept-language", "cookie", "priority",
]


# ── Proxy-type + relaunch helpers (pure, unit-testable) ───────────────────
# C-1023-12 (VERIFIED 2026-10-23): a guest Chrome through an IPRoyal residential
# session stops answering CDP at age 17-21 min (home 0/2, Bright Data 0/2 over
# 30 min). C-1023-20: there is no code fix for the freeze itself; the mitigation
# is to relaunch the proxied Chrome BEFORE onset. So a residential harvester must
# never let a Chrome reach the freeze window, whatever max-age is configured.
RESI_CHROME_MAX_AGE_CEILING_S = 720.0   # 12 min — safely below the 17-min onset

# Reactive-relaunch rate cap (FS-W-D; mirrors the guest follower in
# tools/experiments/limkey_guest_follow.py). Proxied lines stall every
# ~15-50 min by Chrome age, so a persistently dead IP must not thrash Chrome:
# the cap is a RATE over a trailing hour, not a whole-run total.
MAX_REACTIVE_RELAUNCH = 6
REACTIVE_RELAUNCH_WINDOW_S = 3600.0


def _is_resi_proxy(proxy_url: str) -> bool:
    """True for an IPRoyal residential proxy URL (the only residential provider
    in config/proxyIps.json). Bright Data ISP and the home line return False."""
    return 'iproyal' in (proxy_url or '').lower()


def _proxy_has_credentials(proxy_url: str) -> bool:
    """True when the URL embeds Basic-auth creds (``scheme://user:pass@host``).
    Both BD ISP and IPRoyal residential do; such a proxy must be fronted by the
    local forwarder so Chrome never sees a 407 proxy sign-in prompt."""
    url = proxy_url or ''
    if '://' not in url:
        return False
    after = url.split('://', 1)[1]
    return '@' in after and ':' in after.split('@', 1)[0]


def safe_chrome_max_age(configured_s: float, proxy_url: str) -> float:
    """Clamp a residential harvester's Chrome max-age below the IPRoyal freeze
    onset, regardless of the configured/env value. BD and home are unchanged."""
    try:
        v = float(configured_s)
    except (TypeError, ValueError):
        v = 780.0
    if _is_resi_proxy(proxy_url):
        return min(v, RESI_CHROME_MAX_AGE_CEILING_S)
    return v


def relaunch_budget_exceeded(times, now, max_n=MAX_REACTIVE_RELAUNCH,
                             window_s=REACTIVE_RELAUNCH_WINDOW_S):
    """True when more than max_n reactive relaunches fall inside the trailing
    window_s. ``times`` is pruned in place. (Mirrors the guest follower's
    FS-W-D rolling-hour budget — C-1023.)"""
    times[:] = [t for t in times if now - t < window_s]
    return len(times) > max_n


def _atc_js(tcin: str, qty: int = 2) -> str:
    ref = f"https://www.target.com/p/-/A-{tcin}"
    return f"""(async () => {{
    const t0 = performance.now();
    try {{
        const r = await fetch('{ATC_URL}', {{
            method: 'POST', credentials: 'include',
            referrer: '{ref}', referrerPolicy: 'no-referrer-when-downgrade',
            headers: {{'Content-Type': 'application/json', 'Accept': 'application/json',
                       'Origin': 'https://www.target.com', 'Referer': '{ref}',
                       'x-application-name': 'web'}},
            body: JSON.stringify({{cart_item: {{item_channel_id: '10', tcin: '{tcin}', quantity: {qty}}},
                                   cart_type: 'REGULAR', channel_id: '10', shopping_context: 'DIGITAL'}})
        }});
        let b = ''; try {{ b = await r.text(); }} catch (e) {{}}
        return JSON.stringify({{s: r.status, n: b.length, b: b.slice(0, 160), ms: Math.round(performance.now() - t0)}});
    }} catch (e) {{
        return JSON.stringify({{s: 0, n: -1, b: String(e).slice(0, 160), ms: Math.round(performance.now() - t0)}});
    }}
}})()"""


def order_headers(h: dict) -> dict:
    """Reorder captured headers to match Chrome's wire order for a cross-site fetch."""
    low = {k.lower(): v for k, v in h.items()}
    low.setdefault("sec-fetch-site", "same-site")
    low.setdefault("sec-fetch-mode", "cors")
    low.setdefault("sec-fetch-dest", "empty")
    low.setdefault("accept-language", "en-US,en;q=0.9")
    low.setdefault("accept-encoding", "gzip, deflate, br, zstd")
    low.setdefault("priority", "u=1, i")
    out = {}
    shape_keys = [k for k in low if k.startswith("x-") and k != "x-application-name"]
    for k in HEADER_ORDER:
        if k == "accept":
            out["accept"] = low.get("accept", "application/json")
            for s in shape_keys:
                out[s] = low[s]
            continue
        if k in low:
            out[k] = low[k]
    for k, v in low.items():
        if k not in out and not k.startswith(":"):
            out[k] = v
    return out


@dataclass
class ShapeToken:
    """One captured set of Shape headers, usable for one ATC shot."""
    headers: Dict[str, str]
    body: str
    user_agent: str
    harvested_at: float
    ttl_s: float = 300.0

    @property
    def expired(self) -> bool:
        return (time.time() - self.harvested_at) > self.ttl_s

    @property
    def age_s(self) -> float:
        return time.time() - self.harvested_at


class ShapeTokenBank:
    """Thread-safe FIFO bank of ShapeTokens for one exit IP."""

    def __init__(self, ttl_s: float = 300.0, max_size: int = 200,
                 newest_first: Optional[bool] = None,
                 max_use_age_s: Optional[float] = None):
        self._bank: Deque[ShapeToken] = deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._ttl_s = ttl_s
        # Usable-freshness cap (S2). The TTL above is BANK RETENTION; this is the
        # max age at which a token may still be FIRED. The proven path refuses its
        # own cached Shape once age >= 90 s (purchase_executor use_cached age<90),
        # so a 300 s-retained token drawn oldest-first could be replayed 3x older
        # than production will touch. When set, withdraw() skips (and available
        # excludes) tokens older than this, so a STALLED harvester reports
        # no-token instead of firing wire-stale Shape. Default OFF in code; the
        # bat sets TARGET_LANE_TOKEN_MAX_USE_AGE_S=90 when lanes arm (with
        # TARGET_LANE_BANK_NEWEST_FIRST=1, the freshest token is seconds old
        # during active harvesting, so this only bites on a stall). Arg wins.
        if max_use_age_s is None:
            try:
                max_use_age_s = float(os.environ.get('TARGET_LANE_TOKEN_MAX_USE_AGE_S', '0'))
            except Exception:
                max_use_age_s = 0.0
        self._max_use_age_s = max_use_age_s if (max_use_age_s and max_use_age_s > 0) else None
        # Draw order. Default FIFO (oldest-first) = current behavior. Set
        # TARGET_LANE_BANK_NEWEST_FIRST=1 to draw freshest-first, matching the
        # proven production bank (shape_harvest.pop_fresh) — a hot burst then
        # replays the token least likely to be wire-stale. Flip this ON when
        # lanes are armed. Explicit arg wins (for tests).
        if newest_first is None:
            newest_first = os.environ.get(
                'TARGET_LANE_BANK_NEWEST_FIRST', '0').strip().lower() in ('1', 'true', 'yes', 'on')
        self._newest_first = bool(newest_first)
        self.total_produced = 0
        self.total_consumed = 0
        self.total_expired = 0
        self.total_too_stale = 0

    def deposit(self, headers: Dict[str, str], body: str, user_agent: str) -> None:
        token = ShapeToken(
            headers=order_headers(headers),
            body=body,
            user_agent=user_agent,
            harvested_at=time.time(),
            ttl_s=self._ttl_s,
        )
        with self._lock:
            self._bank.append(token)
            self.total_produced += 1

    def withdraw(self) -> Optional[ShapeToken]:
        """Take one usable token from the bank. Oldest-first by default, or
        freshest-first when newest_first is set. Skips TTL-expired tokens, and
        (when max_use_age_s is set) tokens too stale to FIRE. Returns None if
        nothing usable."""
        now = time.time()
        with self._lock:
            while self._bank:
                token = self._bank[-1] if self._newest_first else self._bank[0]
                age = now - token.harvested_at
                if age > token.ttl_s:
                    # TTL-expired: drop and keep looking.
                    self._bank.pop() if self._newest_first else self._bank.popleft()
                    self.total_expired += 1
                    continue
                if self._max_use_age_s is not None and age > self._max_use_age_s:
                    # Not TTL-expired, but too stale to fire. Freshest-first: the
                    # candidate is the youngest, so every other token is staler →
                    # nothing usable. Oldest-first: drop the stale head, keep looking.
                    if self._newest_first:
                        return None
                    self._bank.popleft()
                    self.total_too_stale += 1
                    continue
                self._bank.pop() if self._newest_first else self._bank.popleft()
                self.total_consumed += 1
                return token
        return None

    @property
    def available(self) -> int:
        with self._lock:
            now = time.time()
            cap = self._max_use_age_s
            return sum(
                1 for t in self._bank
                if (now - t.harvested_at) <= t.ttl_s
                and (cap is None or (now - t.harvested_at) <= cap)
            )

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._bank)

    def stats(self) -> Dict[str, Any]:
        return {
            'available': self.available,
            'total_in_bank': self.size,
            'produced': self.total_produced,
            'consumed': self.total_consumed,
            'expired': self.total_expired,
            'too_stale': self.total_too_stale,
            'ttl_s': self._ttl_s,
            'max_use_age_s': self._max_use_age_s,
        }


class ShapeHarvester:
    """Runs a guest Chrome on a proxy IP and continuously harvests Shape tokens.

    The harvester lifecycle:
    1. Launch guest Chrome through the proxy
    2. Navigate to a Target PDP (the control TCIN)
    3. Enable CDP Fetch interception for carts.target.com
    4. Loop: fire ATC JS → intercept → capture headers → abort → wait → repeat
    5. Relaunch Chrome before the CDP stall window (13 min for IPRoyal, 45 min for BD)

    Proxy auth: any credentialed proxy (BD ISP *and* IPRoyal residential) is
    fronted by a local ForwarderPool (pre-auth, Chrome sees 127.0.0.1), so
    Chrome never shows a 407 proxy sign-in prompt (fixed 2026-10-26).

    Thread model: the harvester runs an asyncio event loop in its own thread.
    The token bank is thread-safe and can be read by any lane's shooter thread.
    """

    _next_fwd_port = 18100
    _fwd_port_lock = threading.Lock()

    def __init__(
        self,
        proxy_url: str,
        ip_label: str = '',
        token_ttl_s: float = 300.0,
        harvest_interval_s: float = 4.0,
        chrome_max_age_s: float = 780.0,
        bank_max_size: int = 200,
    ):
        self.proxy_url = proxy_url
        self.ip_label = ip_label or self._extract_ip_label(proxy_url)
        self.harvest_interval_s = harvest_interval_s
        self.chrome_max_age_s = safe_chrome_max_age(chrome_max_age_s, proxy_url)
        self._is_resi = _is_resi_proxy(proxy_url)
        self._stall_fails = int(os.environ.get('TARGET_LANE_STALL_FAILS', '3'))
        self._px_probe = os.environ.get(
            'TARGET_LANE_PX_PROBE', '1').strip().lower() not in ('0', 'false', 'no', 'off', '')

        self.bank = ShapeTokenBank(ttl_s=token_ttl_s, max_size=bank_max_size)

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = threading.Event()
        self._chrome_start_time: float = 0.0
        self._guest = None
        self._route = None
        self._fwd_pool = None
        self._fwd_port: int = 0
        self._running = False
        self._total_harvests = 0
        self._total_failures = 0
        self._total_relaunches = 0
        self._reactive_relaunches = 0
        self._consecutive_fail = 0
        self._reactive_relaunch_times: List[float] = []
        self._unhealthy = False
        self._px_walled = False

    @staticmethod
    def _extract_ip_label(proxy_url: str) -> str:
        if not proxy_url:
            return 'home'
        m = re.search(r'-ip-(\d+\.\d+\.\d+\.\d+)', proxy_url)
        if m:
            return m.group(1)
        m = re.search(r'session-([a-f0-9]+)', proxy_url)
        if m:
            return f'resi_{m.group(1)[:8]}'
        return 'proxy'

    def start(self) -> None:
        if self._running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True,
            name=f"harvester-{self.ip_label}")
        self._thread.start()
        self._running = True
        print(f"[HARVESTER] {self.ip_label} started "
              f"(interval={self.harvest_interval_s}s, "
              f"chrome_max_age={self.chrome_max_age_s}s, "
              f"token_ttl={self.bank._ttl_s}s)")

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)
        print(f"[HARVESTER] {self.ip_label} stopped "
              f"(harvested={self._total_harvests}, "
              f"failures={self._total_failures}, "
              f"relaunches={self._total_relaunches})")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._harvest_loop())
        except Exception as e:
            print(f"[HARVESTER] {self.ip_label} loop crashed: {e}")
        finally:
            try:
                self._loop.run_until_complete(self._stop_forwarder())
            except Exception:
                pass
            self._loop.close()

    async def _harvest_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._ensure_chrome()
                if self._guest and self._guest.tab:
                    ok = await self._do_harvest()
                    if ok:
                        self._consecutive_fail = 0
                        self._unhealthy = False
                    else:
                        self._consecutive_fail += 1
                else:
                    self._consecutive_fail += 1
                    await asyncio.sleep(2.0)
            except Exception as e:
                print(f"[HARVESTER] {self.ip_label} harvest error: "
                      f"{type(e).__name__}: {e}")
                self._total_failures += 1
                self._consecutive_fail += 1
                await self._close_chrome()
                await asyncio.sleep(5.0)

            # Reactive relaunch: a run of consecutive harvest failures means the
            # Chrome has most likely frozen (C-1023-12 resi CDP stall) before its
            # max-age. Relaunch now, but rate-cap it (FS-W-D) so a dead IP cannot
            # thrash Chrome every few seconds.
            if (self._consecutive_fail >= self._stall_fails
                    and self._guest is not None):
                now = time.time()
                if relaunch_budget_exceeded(self._reactive_relaunch_times, now):
                    if not self._unhealthy:
                        print(f"[HARVESTER] {self.ip_label} UNHEALTHY — "
                              f">{MAX_REACTIVE_RELAUNCH} reactive relaunches in the "
                              f"last hour; backing off 30s")
                    self._unhealthy = True
                    await self._close_chrome()
                    await asyncio.sleep(30.0)
                    self._consecutive_fail = 0
                else:
                    self._reactive_relaunch_times.append(now)
                    self._reactive_relaunches += 1
                    self._total_relaunches += 1
                    print(f"[HARVESTER] {self.ip_label} reactive relaunch "
                          f"(stall: {self._consecutive_fail} consecutive fails; "
                          f"{len(self._reactive_relaunch_times)}/{MAX_REACTIVE_RELAUNCH} "
                          f"in the last hour)")
                    await self._close_chrome()
                    self._consecutive_fail = 0

            # Proactive relaunch: stay ahead of the freeze onset by age alone.
            if (self._chrome_start_time > 0
                    and (time.time() - self._chrome_start_time) >= self.chrome_max_age_s):
                print(f"[HARVESTER] {self.ip_label} proactive relaunch at "
                      f"{time.time() - self._chrome_start_time:.0f}s")
                await self._close_chrome()
                self._total_relaunches += 1
                self._consecutive_fail = 0

            if not self._stop_event.is_set():
                await asyncio.sleep(self.harvest_interval_s)

    def _is_bd_proxy(self) -> bool:
        return 'brd.superproxy.io' in self.proxy_url or 'superproxy' in self.proxy_url

    async def _ensure_forwarder(self) -> int:
        """Start a local forwarder for a credentialed proxy (BD or IPRoyal),
        return the local port. The forwarder parses user:pass@host:port
        generically, so it fronts either provider."""
        if self._fwd_pool and self._fwd_port:
            return self._fwd_port

        from src.proxy.local_forwarder import ForwarderPool
        # Allocate a distinct forwarder port atomically — harvester threads create
        # their forwarders concurrently, and an unlocked class-var increment could
        # hand two harvesters the same port (one bind fails → that Chrome gets no
        # proxy). (minor, 2026-10-27)
        with ShapeHarvester._fwd_port_lock:
            port = ShapeHarvester._next_fwd_port
            ShapeHarvester._next_fwd_port += 1

        pool = ForwarderPool()
        pool.add_upstream(self.proxy_url, port=port)
        await pool.start_all()
        self._fwd_pool = pool
        self._fwd_port = port
        print(f"[HARVESTER] {self.ip_label} forwarder on 127.0.0.1:{port}")
        return port

    async def _stop_forwarder(self) -> None:
        if self._fwd_pool:
            try:
                await self._fwd_pool.stop_all()
            except Exception:
                pass
            self._fwd_pool = None
            self._fwd_port = 0

    async def _ensure_chrome(self) -> None:
        """Launch or relaunch the guest Chrome if needed."""
        if self._guest is not None:
            return

        try:
            import zendriver as zd
            from zendriver import cdp

            args = [
                '--no-first-run', '--disable-default-apps',
                '--disable-features=TranslateUI',
                '--disable-backgrounding-occluded-windows',
            ]
            if self.proxy_url:
                if _proxy_has_credentials(self.proxy_url):
                    # BD ISP *and* IPRoyal residential both carry Basic-auth
                    # creds. Route through the local forwarder so Chrome connects
                    # to 127.0.0.1 with no auth. This avoids Chrome's native 407
                    # proxy sign-in prompt (observed 2026-10-26 on residential):
                    # the old CDP Fetch.authRequired handler suppressed the prompt
                    # only for the first navigation, then fetch.disable() left
                    # later connections to re-prompt and the PDP stalled.
                    fwd_port = await self._ensure_forwarder()
                    args.append(f'--proxy-server=http://127.0.0.1:{fwd_port}')
                else:
                    args.append(f'--proxy-server={self.proxy_url}')

            import tempfile
            profile = tempfile.mkdtemp(prefix=f'harvester_{self.ip_label}_')
            config = zd.Config()
            for a in args:
                config.add_argument(a)
            config.user_data_dir = profile

            browser = await zd.start(config)
            tab = await browser.get('about:blank')

            await tab.get(f'https://www.target.com/p/-/A-{CONTROL_TCIN}')
            await asyncio.sleep(6.0)

            # PX / HUMAN wall check (read-only DOM snapshot). C-1023-14: a fresh
            # guest through the resi session loads px_wall=0, so the wall is
            # profile-bound, not IP-bound — if one ever appears we relaunch a
            # fresh guest (rate-capped) rather than harvest tainted/zero tokens.
            if self._px_probe:
                try:
                    from src.session.px_challenge import (
                        is_px_challenge, describe as _px_describe, PX_MARKERS_JS)
                    markers = await asyncio.wait_for(
                        tab.evaluate(PX_MARKERS_JS), timeout=8.0)
                    if is_px_challenge(markers):
                        # Confirm the wall persists before spending a relaunch. A
                        # real HUMAN challenge stays until solved; a px-captcha
                        # placeholder that only flashes during load (seen on the
                        # control TCIN's "Product not found" page, 2026-10-26)
                        # clears on its own and must not trip a relaunch.
                        await asyncio.sleep(2.5)
                        markers = await asyncio.wait_for(
                            tab.evaluate(PX_MARKERS_JS), timeout=8.0)
                    if is_px_challenge(markers):
                        self._px_walled = True
                        now = time.time()
                        if relaunch_budget_exceeded(self._reactive_relaunch_times, now):
                            print(f"[HARVESTER] {self.ip_label} PX WALL and reactive "
                                  f"budget spent — backing off 30s "
                                  f"({_px_describe(markers)})")
                            self._unhealthy = True
                            await self._close_chrome()
                            await asyncio.sleep(30.0)
                        else:
                            self._reactive_relaunch_times.append(now)
                            self._reactive_relaunches += 1
                            self._total_relaunches += 1
                            print(f"[HARVESTER] {self.ip_label} PX WALL on PDP — "
                                  f"relaunching fresh guest "
                                  f"({len(self._reactive_relaunch_times)}/{MAX_REACTIVE_RELAUNCH} "
                                  f"in the last hour; {_px_describe(markers)})")
                            await self._close_chrome()
                        return
                    self._px_walled = False
                except Exception:
                    pass

            # Enable Fetch interception for cart_items only (harvest mode)
            self._captured: Optional[Dict[str, Any]] = None

            async def _on_paused(ev):
                try:
                    rq = ev.request
                    if "cart_items" in str(rq.url) and str(rq.method).upper() == "POST":
                        self._captured = {
                            'headers': dict(rq.headers),
                            'body': rq.post_data,
                            'ua': dict(rq.headers).get('User-Agent') or dict(rq.headers).get('user-agent', ''),
                        }
                        await tab.send(cdp.fetch.fail_request(
                            ev.request_id, cdp.network.ErrorReason.ABORTED))
                        return
                    await tab.send(cdp.fetch.continue_request(ev.request_id))
                except Exception:
                    pass

            tab.add_handler(cdp.fetch.RequestPaused, _on_paused)
            await tab.send(cdp.fetch.enable(
                patterns=[cdp.fetch.RequestPattern(
                    url_pattern='*carts.target.com*',
                    request_stage=cdp.fetch.RequestStage.REQUEST)]))

            self._guest = type('Guest', (), {'browser': browser, 'tab': tab, 'profile': profile})()
            self._chrome_start_time = time.time()
            print(f"[HARVESTER] {self.ip_label} Chrome launched")

        except Exception as e:
            print(f"[HARVESTER] {self.ip_label} Chrome launch failed: "
                  f"{type(e).__name__}: {e}")
            self._guest = None

    async def _do_harvest(self) -> bool:
        """Fire one ATC fetch, capture the intercepted Shape headers.

        Returns True if a token was captured and deposited, False otherwise
        (an evaluate timeout or an empty capture — the signals the loop uses to
        detect a frozen Chrome)."""
        tab = self._guest.tab
        self._captured = None

        try:
            raw = await asyncio.wait_for(
                tab.evaluate(_atc_js(CONTROL_TCIN, 2), await_promise=True),
                timeout=20.0)
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[HARVESTER] {self.ip_label} evaluate failed: "
                  f"{type(e).__name__}: {e}")
            self._total_failures += 1
            return False

        if self._captured and self._captured.get('headers'):
            hdrs = self._captured['headers']
            body = self._captured.get('body', '')
            ua = self._captured.get('ua', '')
            n_shape = len([k for k in hdrs if k.lower().startswith('x-')
                          and k.lower() != 'x-application-name'])

            self.bank.deposit(hdrs, body, ua)
            self._total_harvests += 1

            if self._total_harvests % 25 == 0:
                print(f"[HARVESTER] {self.ip_label} "
                      f"harvested={self._total_harvests} "
                      f"bank={self.bank.available} "
                      f"shape_tokens={n_shape}")
            return True

        self._total_failures += 1
        return False

    async def _close_chrome(self) -> None:
        if self._guest:
            try:
                await self._guest.browser.stop()
            except Exception:
                pass
            self._guest = None
            self._chrome_start_time = 0.0

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def status(self) -> Dict[str, Any]:
        chrome_age = (time.time() - self._chrome_start_time
                      if self._chrome_start_time > 0 else 0)
        return {
            'ip': self.ip_label,
            'resi': self._is_resi,
            'running': self.is_running,
            'chrome_alive': self._guest is not None,
            'chrome_age_s': round(chrome_age, 1),
            'max_age_s': self.chrome_max_age_s,
            'harvests': self._total_harvests,
            'failures': self._total_failures,
            'relaunches': self._total_relaunches,
            'reactive_relaunches': self._reactive_relaunches,
            'consecutive_fail': self._consecutive_fail,
            'unhealthy': self._unhealthy,
            'px_walled': self._px_walled,
            'bank': self.bank.stats(),
        }
