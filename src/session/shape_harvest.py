#!/usr/bin/env python3
"""Real-click Shape sensor harvesting + banked replay (2026-09-03).

WHY (docs/FAILURES.md 08-28 + the 09-03 vendor research, memory
`reference_target_winning_bot_architecture_2026`): every hot-SKU window since
08-07 died 100% `401 _ERR_AUTH_DENIED` on the shots that passed the edge
limiter. Winning Target bots (Refract / Stellar / Hidden AIO / Shikari) never
sign a programmatic request: a HARVESTER drives Target's real add-to-cart on an
in-stock product, intercepts the outgoing `cart_items` POST, saves the Shape
header set that genuine interaction produced, and tasks REPLAY one banked set per
shot ("each protected request consumes one cookie"; bank ~3/task; TTL 5-15 min).
Our shot is an in-page `fetch()` re-signed by the Shape VM of a tab parked on
/account with zero interaction: the byte-audit found our main-tab ATC has carried
the `-a0` behavioral chunk 0 / 53,950 times and we have never once fired a real
page-button add. This module closes exactly that gap, flag-gated:

  * a HARVEST tab per identity parks on an in-stock PDP (TARGET_HARVEST_TCINS)
    and performs a human-trajectory CDP click on the real Add-to-cart button;
  * the tab's CDP interceptor captures the page-signed header set and FAILS the
    request (BlockedByClient) before it leaves Chrome -> nothing lands, the
    single-use Shape uuid is unspent, the set goes into a small LIFO bank;
  * on the MAIN tab, the fast-lane ATC POST is paused at the request stage and
    `Fetch.continueRequest` swaps the page-signed `X-<prefix>-*` headers for the
    freshest banked set (this rewrite happens AFTER the in-page hook, so unlike
    header injection into fetch() -- inert since 07-10 -- it reaches the wire);
  * a boot self-test replays banked sets on the warmup tab's dummy POST and
    auto-disables replay for the run if the server rejects them, so a broken
    mechanism can never make the night worse than the proven page-signed path.

Pure helpers here are browser-free (unit-tested in tests/test_shape_harvest.py);
zendriver is imported lazily inside the async functions only.

Flags (all read at executor init; bat pins them):
  TARGET_SHAPE_HARVEST=1        master switch (default 0 = exact prior behaviour)
  TARGET_HARVEST_TCINS=a,b,c    in-stock, ship-eligible, CHEAP PDP TCINs to click
  TARGET_HARVEST_BANK=3         banked sets per identity
  TARGET_HARVEST_TTL_S=300      banked set max age
  TARGET_HARVEST_INTERVAL_S=40  idle cadence when the bank is full (jittered)
  TARGET_HARVEST_REPLAY=1       swap banked headers onto main-tab ATC shots
  TARGET_HARVEST_SELFTEST=1     boot replay self-test on the warmup dummy POST
  TARGET_HARVEST_IN_WINDOW=1    keep refilling while a purchase is live
"""
from __future__ import annotations

import math
import os
import random
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #
_ENV_MASTER = "TARGET_SHAPE_HARVEST"


def is_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get(_ENV_MASTER, "0")).strip() == "1"


def harvest_tcins(env: Optional[Dict[str, str]] = None) -> List[str]:
    e = os.environ if env is None else env
    raw = str(e.get("TARGET_HARVEST_TCINS", "") or "")
    out: List[str] = []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip()
        if tok.isdigit() and tok not in out:
            out.append(tok)
    return out


def _int(e, key, default, lo, hi):
    try:
        v = int(str(e.get(key, default)).strip())
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _float(e, key, default, lo, hi):
    try:
        v = float(str(e.get(key, default)).strip())
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def config(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    e = os.environ if env is None else env
    return {
        "enabled": is_enabled(e),
        "tcins": harvest_tcins(e),
        "bank": _int(e, "TARGET_HARVEST_BANK", 3, 1, 10),
        "ttl_s": _float(e, "TARGET_HARVEST_TTL_S", 300.0, 30.0, 900.0),
        "interval_s": _float(e, "TARGET_HARVEST_INTERVAL_S", 40.0, 10.0, 600.0),
        "replay": str(e.get("TARGET_HARVEST_REPLAY", "1")).strip() != "0",
        "selftest": str(e.get("TARGET_HARVEST_SELFTEST", "1")).strip() != "0",
        "in_window": str(e.get("TARGET_HARVEST_IN_WINDOW", "1")).strip() != "0",
    }


# --------------------------------------------------------------------------- #
# Shape header helpers (pure)
# --------------------------------------------------------------------------- #
_NON_SHAPE_X = {"x-application-name", "x-requested-with", "x-request-id", "x-api-key"}


def shape_prefix(headers: Dict[str, str]) -> Optional[str]:
    """Return the lowercase Shape header prefix, e.g. 'x-gyjwza5z-', from a
    header dict that carries `X-<id>-a` (the main sensor). None if absent."""
    for name in headers or {}:
        n = str(name).lower()
        if n in _NON_SHAPE_X or not n.startswith("x-"):
            continue
        parts = n.split("-")
        # x-<id>-a  (id = 6+ alnum chars); -a0 is accepted as an anchor too
        if len(parts) >= 3 and parts[-1] in ("a", "a0") and len(parts[1]) >= 6:
            return "-".join(parts[:-1]) + "-"
    return None


def shape_tokens(headers: Dict[str, str], prefix: Optional[str] = None) -> Dict[str, str]:
    """The `X-<prefix>-*` subset of `headers` (original casing preserved)."""
    p = prefix or shape_prefix(headers)
    if not p:
        return {}
    return {k: v for k, v in (headers or {}).items() if str(k).lower().startswith(p)}


def merge_replay_headers(request_headers: Dict[str, str],
                         banked_headers: Dict[str, str]) -> List[Tuple[str, str]]:
    """Build the full header list for `Fetch.continueRequest`.

    Every non-Shape request header (Cookie, UA, sec-ch-ua, Content-Type, ...)
    is kept verbatim and in place; the page-signed `X-<prefix>-*` tokens are
    replaced by the banked set (all of it, incl. `-a0` when present), inserted
    where the first page-signed token sat so header order barely moves. If the
    request carried no Shape tokens the banked set is appended. Never raises;
    returns the original headers unchanged when the bank set has no tokens."""
    req = dict(request_headers or {})
    bank_tokens = shape_tokens(banked_headers)
    if not bank_tokens:
        return list(req.items())
    req_prefix = shape_prefix(req)
    bank_prefix = shape_prefix(bank_tokens)
    out: List[Tuple[str, str]] = []
    inserted = False
    for k, v in req.items():
        kl = str(k).lower()
        is_shape = (bool(req_prefix and kl.startswith(req_prefix))
                    or bool(bank_prefix and kl.startswith(bank_prefix)))
        if is_shape:
            if not inserted:
                out.extend(bank_tokens.items())
                inserted = True
            continue
        out.append((k, v))
    if not inserted:
        out.extend(bank_tokens.items())
    return out


# --------------------------------------------------------------------------- #
# The bank
# --------------------------------------------------------------------------- #
class ShapeBank:
    """LIFO bank of harvested header sets with TTL pruning."""

    def __init__(self, size: int = 3, ttl_s: float = 300.0):
        self.size = max(1, int(size))
        self.ttl_s = float(ttl_s)
        self._items: Deque[Dict[str, Any]] = deque()
        self.harvested = 0
        self.replayed = 0
        self.expired = 0

    def prune(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        before = len(self._items)
        self._items = deque(i for i in self._items if now - i["ts"] <= self.ttl_s)
        dropped = before - len(self._items)
        self.expired += dropped
        return dropped

    def push(self, headers: Dict[str, str], meta: Optional[Dict[str, Any]] = None,
             now: Optional[float] = None) -> bool:
        toks = shape_tokens(headers)
        if not toks:
            return False
        now = time.time() if now is None else now
        self.prune(now)
        entry = {"headers": dict(headers), "tokens": toks, "ts": now,
                 "a0": any(str(k).lower().endswith("-a0") for k in toks),
                 "meta": dict(meta or {})}
        self._items.append(entry)
        while len(self._items) > self.size:
            self._items.popleft()          # drop the OLDEST when over capacity
            self.expired += 1
        self.harvested += 1
        return True

    def pop_fresh(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        now = time.time() if now is None else now
        self.prune(now)
        if not self._items:
            return None
        entry = self._items.pop()          # LIFO: freshest set for the shot
        self.replayed += 1
        return entry

    def count(self, now: Optional[float] = None) -> int:
        self.prune(now)
        return len(self._items)

    def need(self, now: Optional[float] = None) -> int:
        return max(0, self.size - self.count(now))

    def newest_age(self, now: Optional[float] = None) -> Optional[float]:
        now = time.time() if now is None else now
        self.prune(now)
        if not self._items:
            return None
        return now - self._items[-1]["ts"]

    def summary(self, now: Optional[float] = None) -> str:
        age = self.newest_age(now)
        age_s = "-" if age is None else f"{age:.0f}s"
        return (f"bank={self.count(now)}/{self.size} newest_age={age_s} "
                f"harvested={self.harvested} replayed={self.replayed} expired={self.expired}")


# --------------------------------------------------------------------------- #
# Human-trajectory click (pure path generator + async dispatcher)
# --------------------------------------------------------------------------- #
def bezier_path(x0: float, y0: float, x1: float, y1: float,
                rng: Optional[random.Random] = None) -> List[Tuple[float, float, float]]:
    """Quadratic-Bezier pointer path from (x0,y0) to (x1,y1) as
    [(x, y, dt_seconds_before_this_move), ...]; last point == target.
    Ported from walmart/purchase_executor._realistic_click (velocity-weighted
    sin(pi*t) timing, perpendicular control-point bend, gaussian jitter)."""
    rng = rng or random
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    offset_mag = min(80.0, max(15.0, dist * 0.18))
    if dist > 1.0:
        px, py = -dy / dist, dx / dist
    else:
        px, py = 0.0, 0.0
    sign = rng.choice((-1.0, 1.0))
    cp_x = (x0 + x1) / 2 + sign * offset_mag * px + rng.uniform(-offset_mag * 0.4, offset_mag * 0.4)
    cp_y = (y0 + y1) / 2 + sign * offset_mag * py + rng.uniform(-offset_mag * 0.4, offset_mag * 0.4)
    steps = max(4, min(9, int(dist / 80) + rng.randint(3, 5)))
    pts: List[Tuple[float, float, float]] = []
    for i in range(1, steps + 1):
        t = i / (steps + 1)
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cp_x + t ** 2 * x1 + rng.gauss(0, 0.6)
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cp_y + t ** 2 * y1 + rng.gauss(0, 0.6)
        dt = 0.055 - 0.043 * math.sin(math.pi * t) + rng.uniform(-0.005, 0.012)
        pts.append((bx, by, max(0.004, dt)))
    pts.append((float(x1), float(y1), rng.uniform(0.03, 0.11)))   # settle + dwell
    return pts


async def human_click(tab, x: float, y: float,
                      start: Optional[Tuple[float, float]] = None) -> Tuple[float, float]:
    """Move along a Bezier path and click (trusted CDP input events). Returns
    the final pointer position for the next call."""
    import asyncio
    from zendriver.cdp import input_ as cdp_input
    sx, sy = start or (random.uniform(200, 900), random.uniform(150, 500))
    for bx, by, dt in bezier_path(sx, sy, x, y):
        await tab.send(cdp_input.dispatch_mouse_event(
            type_="mouseMoved", x=int(bx), y=int(by), pointer_type="mouse"))
        await asyncio.sleep(dt)
    await tab.send(cdp_input.dispatch_mouse_event(
        type_="mousePressed", x=x, y=y, button=cdp_input.MouseButton.LEFT,
        buttons=1, click_count=1, pointer_type="mouse"))
    await asyncio.sleep(random.uniform(0.06, 0.13))
    await tab.send(cdp_input.dispatch_mouse_event(
        type_="mouseReleased", x=x, y=y, button=cdp_input.MouseButton.LEFT,
        buttons=0, click_count=1, pointer_type="mouse"))
    return (x, y)


# --------------------------------------------------------------------------- #
# PDP button finder (runs in-page; returns a JSON-able dict)
# --------------------------------------------------------------------------- #
FIND_ATC_BUTTON_JS = """(() => {
    const sels = [
        'button[data-test="shippingButton"]',
        'button[id^="addToCartButtonOrTextIdFor"]',
        'button[data-test="addToCartButton"]',
        'button[data-testid="addToCartButton"]',
        '[data-testid*="add-to-cart"]',
        'button[data-test*="addToCart"]',
        'button[data-test="shipItButton"]',
    ];
    const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const disabledOf = (el) => !!(el.disabled || el.getAttribute('aria-disabled') === 'true');
    let el = null, via = '';
    for (const sel of sels) {
        const cand = document.querySelector(sel);
        if (cand && visible(cand)) { el = cand; via = sel; break; }
    }
    if (!el) {
        const re = /(add to cart|ship it)/i;
        for (const b of document.querySelectorAll('button')) {
            if (visible(b) && re.test(b.textContent || '')) { el = b; via = 'text'; break; }
        }
    }
    const ready = document.readyState;
    if (!el) {
        const body_txt = (document.body && document.body.innerText) || '';
        return {found: false, disabled: true, x: 0, y: 0, w: 0, h: 0, via: '', text: '',
                ready: ready, oos: /out of stock|sold out/i.test(body_txt)};
    }
    try { el.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (e) {}
    const r = el.getBoundingClientRect();
    return {found: true, disabled: disabledOf(el), x: r.left, y: r.top, w: r.width, h: r.height,
            via: via, text: (el.textContent || '').trim().slice(0, 40), ready: ready, oos: false};
})()"""


def click_point(rect: Dict[str, Any], rng: Optional[random.Random] = None) -> Tuple[float, float]:
    """A jittered point inside the button rect (never the exact centre)."""
    rng = rng or random
    w = float(rect.get("w") or 0.0)
    h = float(rect.get("h") or 0.0)
    x = float(rect.get("x") or 0.0) + w / 2 + rng.uniform(-min(8.0, w * 0.2), min(8.0, w * 0.2))
    y = float(rect.get("y") or 0.0) + h / 2 + rng.uniform(-min(4.0, h * 0.2), min(4.0, h * 0.2))
    return (x, y)


def pdp_url(tcin: str) -> str:
    return f"https://www.target.com/p/-/A-{tcin}"
