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

2026-09-16 hot-sku plan P8 (HV-1), every flag default OFF = exact prior behaviour:
  TARGET_HARVEST_SKIP_DISABLES_REPLAY=1  a TARGET_HARVEST_SKIP account also turns banked
                                replay (and so the manager's bank gate) off -- its bank
                                can never fill, so the gate was a dead 8 s wait
  TARGET_HARVEST_MISS_PROBE=1   on the first buttonless load per navigation, snapshot the
                                page (PX markers + buttons/fulfilment/text/hint), log
                                `MISS-PROBE`, capped screenshots; a HUMAN challenge parks
                                the harvest tab (no nav, no click) for PX_PARK_S
  TARGET_HARVEST_MISS_SHOTS_MAX=10  MISS-PROBE screenshots per run (0 = none)
  TARGET_HARVEST_PX_PARK_S=300  harvest park after a PX verdict on the harvest tab
  TARGET_HARVEST_MISS_RENAV_LIVE=1  BUILT, NOT ARMED: a buttonless page may be re-navigated
                                while a purchase is live (never during a won-cart loop or a
                                held cart); also arms the bad-load back-off below
  TARGET_HARVEST_BADLOAD_BACKOFF_S=300  3 buttonless loads within 600 s -> no harvest nav
                                or click for this long (renav flag only)
  TARGET_HARVEST_FLUSH_ON_RELAUNCH=1  banked sets minted by a killed Chrome are dropped
                                (ShapeBank.clear) instead of replayed on the new one
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


def harvest_skip(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Account ids (lowercased) that should NOT run the harvest loop — they fire
    page-signed shots instead. Mirrors TARGET_FP_CHROMIUM_SKIP. 2026-09-04: worker
    1 (primary) shares the global event loop with the 16-IP stock sweep, so its
    CDP Input.dispatchMouseEvent times out under sweep load (18/18 clicks failed
    live); skipping it stops that contention with its real purchase shots."""
    e = os.environ if env is None else env
    raw = str(e.get("TARGET_HARVEST_SKIP", "") or "")
    return [t.strip().lower() for t in raw.replace(";", ",").split(",") if t.strip()]


def enabled_for(account_id: Optional[str], env: Optional[Dict[str, str]] = None) -> bool:
    """Master switch AND this account is not in TARGET_HARVEST_SKIP."""
    if not is_enabled(env):
        return False
    return (account_id or "").strip().lower() not in harvest_skip(env)


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
        "skip": harvest_skip(e),
        # 2026-09-09 hidden-tab root cause (see human_click): probe visibility and
        # re-activate the harvest tab before each click; abort a click as soon as
        # one mouse move stalls (0 = never abort).
        "vis_guard": str(e.get("TARGET_HARVEST_VIS_GUARD", "1")).strip() != "0",
        "move_abort_ms": _int(e, "TARGET_HARVEST_MOVE_ABORT_MS", 2500, 0, 20000),
        # 2026-09-09 audit #10: select the Shipping fulfillment cell once per PDP nav
        # so the harvested add is a SHIPPING add (captures were STORE_PICKUP).
        "prefer_shipping": str(e.get("TARGET_HARVEST_PREFER_SHIPPING", "1")).strip() != "0",
        # 2026-09-13 (09-11 forensics, docs/FAILURES.md): EVERY historical win used a
        # SMALL first-click Shape set (no -a0). A long-lived harvest tab piles synthetic
        # click/move telemetry into the sensor until it overflows the 7,900-char -a
        # chunk into -a0 (~13 min after each page load); those bloated sets went 0/14
        # on limiter-passing shots on 09-11 and trip the edge's 431 header cap, while
        # the night's only 201 rode a set captured 2 s after a PDP reload. fresh_page
        # reloads the PDP before every harvest click so each banked set is a
        # first-click set. Kill: TARGET_HARVEST_FRESH_PAGE=0 (exact prior behaviour).
        "fresh_page": str(e.get("TARGET_HARVEST_FRESH_PAGE", "0")).strip() == "1",
        # Reloads are also allowed while a purchase is live (wave re-entries 55-70 s
        # apart need fresh sets too); =0 keeps the "never navigate mid-purchase" rule.
        "fresh_page_live": str(e.get("TARGET_HARVEST_FRESH_PAGE_LIVE", "1")).strip() != "0",
        # Never reload more often than this (a PDP load is real traffic on the exit).
        "fresh_page_min_gap_s": _float(e, "TARGET_HARVEST_FRESH_PAGE_MIN_GAP_S", 15.0, 0.0, 600.0),
        # At shot time prefer the freshest replayable set WITHOUT -a0 over a fresher
        # bloated one (belt-and-braces next to fresh_page).
        "prefer_no_a0": str(e.get("TARGET_HARVEST_PREFER_NO_A0", "0")).strip() == "1",
        # 2026-09-16 hot-sku plan P8 (HV-1). All default OFF (see module docstring).
        # 09-16: alt-1's harvest PDP came back buttonless on 25/166 drop-hour loads
        # (2/700 otherwise) and nothing recorded WHAT the page was; a no-op live
        # rotate + a click-gated reload left it stuck 71.9 min; 49 x 8 s bank-gate
        # waits; TARGET_HARVEST_SKIP left replay + the gate on; a replayed set was
        # minted by the Chrome killed 3 s earlier (verdicts H-01/02/03/06, INFRA-4).
        "skip_disables_replay": str(e.get("TARGET_HARVEST_SKIP_DISABLES_REPLAY", "0")).strip() == "1",
        "miss_probe": str(e.get("TARGET_HARVEST_MISS_PROBE", "0")).strip() == "1",
        "miss_shots_max": _int(e, "TARGET_HARVEST_MISS_SHOTS_MAX", 10, 0, 100),
        "px_park_s": _float(e, "TARGET_HARVEST_PX_PARK_S", 300.0, 30.0, 3600.0),
        "miss_renav_live": str(e.get("TARGET_HARVEST_MISS_RENAV_LIVE", "0")).strip() == "1",
        "badload_backoff_s": _float(e, "TARGET_HARVEST_BADLOAD_BACKOFF_S", 300.0, 30.0, 3600.0),
        "flush_on_relaunch": str(e.get("TARGET_HARVEST_FLUSH_ON_RELAUNCH", "0")).strip() == "1",
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


def sensor_a_len(headers: Dict[str, str], prefix: Optional[str] = None) -> int:
    """Length of the `X-<prefix>-a` sensor value (0 if absent). Four public
    captures of real Target adds show `-a` capped at 7,900 chars with `-a0`
    carrying only the overflow, and Target's current build mostly emits no
    -a0 at all -- so a missing -a0 next to a short -a is the normal shape of a
    genuine click, not a broken harvest (research 2026-09-09)."""
    p = prefix or shape_prefix(headers)
    if not p:
        return 0
    for k, v in (headers or {}).items():
        if str(k).lower() == p + "a":
            return len(str(v))
    return 0


def header_bytes(headers: Any, prefix: Optional[str] = None) -> Dict[str, int]:
    """Approximate wire size of a request header set (dict or (name, value) pairs):
    total = name + value + ': ' + CRLF per header, plus the Cookie value, the whole
    Shape X-* set and the -a / -a0 chunks on their own. 2026-09-13: Target's edge
    answered 431 (Request Header Fields Too Large) on 380 cart_items POSTs on 09-11,
    one of them a limiter-passing real shot, and the banked sensor is the only size
    knob we hold -- so every capture, replay and 431 now logs these numbers. Pure."""
    items = headers.items() if hasattr(headers, "items") else list(headers or [])
    hdrs = {str(k): str(v) for k, v in items}
    p = prefix or shape_prefix(hdrs)
    out = {"total": 0, "cookie": 0, "shape": 0, "a": 0, "a0": 0, "n": 0}
    for k, v in hdrs.items():
        out["total"] += len(k) + len(v) + 4
        out["n"] += 1
        kl = k.lower()
        if kl == "cookie":
            out["cookie"] += len(v)
        elif p and kl.startswith(p):
            out["shape"] += len(v)
            if kl == p + "a":
                out["a"] = len(v)
            elif kl == p + "a0":
                out["a0"] = len(v)
    return out


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
        self.stale = 0           # sets discarded because they were past the replay cap

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

    @staticmethod
    def max_replay_age() -> float:
        """TARGET_HARVEST_MAX_REPLAY_AGE_S (default 100 s; 0 = no cap). A set
        older than the Shape token-rotation window (~90-120 s) is likely dead
        on the wire even inside the 300 s bank TTL, and replaying a dead set
        makes the shot WORSE than a fresh page-signed one."""
        try:
            return max(0.0, float(os.environ.get("TARGET_HARVEST_MAX_REPLAY_AGE_S", "100")))
        except (TypeError, ValueError):
            return 100.0

    def pop_fresh(self, now: Optional[float] = None,
                  prefer_no_a0: bool = False) -> Optional[Dict[str, Any]]:
        now = time.time() if now is None else now
        self.prune(now)
        if not self._items:
            return None
        _max_age = self.max_replay_age()
        if prefer_no_a0:
            # 2026-09-13: the freshest REPLAYABLE set without -a0 (a first-click set)
            # beats a fresher bloated one; plain LIFO below when every replayable
            # set carries -a0. A stale-only bank still falls through to the discard.
            for idx in range(len(self._items) - 1, -1, -1):
                it = self._items[idx]
                if not it["a0"] and (_max_age <= 0 or (now - it["ts"]) <= _max_age):
                    del self._items[idx]
                    self.replayed += 1
                    return it
        if _max_age > 0 and (now - self._items[-1]["ts"]) > _max_age:
            # 2026-09-09 (audit finding #1): the freshest set is past the replay
            # cap, so every older one is too. Until today they STAYED in the bank,
            # which kept need()==0, so the loop never re-clicked and every shot
            # for ~200 s of each 300 s cycle went page-signed while logging
            # "bank EMPTY" at 3/3. Discard them so the refill fires.
            n = len(self._items)
            self._items.clear()
            self.stale += n          # counted as stale, not double-counted as expired
            return None
        entry = self._items.pop()          # LIFO: freshest set for the shot
        self.replayed += 1
        return entry

    def clear(self) -> int:
        """Drop every banked set (counted as expired) and return how many were
        dropped. 2026-09-16 HV-1 (TARGET_HARVEST_FLUSH_ON_RELAUNCH): sets minted
        by a Chrome that was just killed must not be replayed on its successor
        (INFRA-4: a 62 s-old set from the previous Chrome rode a real shot)."""
        n = len(self._items)
        self._items.clear()
        self.expired += n
        return n

    def refill_wanted(self, now: Optional[float] = None) -> bool:
        """True when the loop should click: room in the bank, OR the freshest
        set is already past HALF the replay cap -- so a fresh set is banked
        before the cap bites instead of after a shot finds nothing replayable."""
        now = time.time() if now is None else now
        if self.need(now) > 0:
            return True
        age = self.newest_age(now)
        cap = self.max_replay_age()
        return bool(cap > 0 and age is not None and age > cap * 0.5)

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
                f"harvested={self.harvested} replayed={self.replayed} expired={self.expired} "
                f"stale={self.stale}")


# --------------------------------------------------------------------------- #
# Human-trajectory click (pure path generator + async dispatcher)
# --------------------------------------------------------------------------- #
def bezier_path(x0: float, y0: float, x1: float, y1: float,
                rng: Optional[random.Random] = None) -> List[Tuple[float, float, float]]:
    """Quadratic-Bezier pointer path from (x0,y0) to (x1,y1) as
    [(x, y, dt_seconds_before_this_move), ...]; last point == target.
    Originally ported from the (since-removed) Walmart executor's
    _realistic_click: velocity-weighted sin(pi*t) timing, perpendicular
    control-point bend, gaussian jitter."""
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
    # TARGET_HARVEST_CLICK_MOVES bounds the intermediate moves (default 9 = the
    # natural path for any realistic distance). 2026-09-09 CORRECTION: the earlier
    # default of 2 was built on a wrong diagnosis ("the warmup interceptor
    # saturates the account browser's single CDP websocket"). zendriver gives
    # every tab its OWN websocket, the interceptor averaged <0.5 round trips/s,
    # and the 09-07 run log shows the real cause: every click failure streak
    # began right after a `[WARMUP] Opening warmup tab` (Target.createTarget =
    # a NEW FOREGROUND TAB in desktop Chrome, always), which pushed the harvest
    # tab into the background. Chromium queues mouseMoved as rAF-aligned input
    # and a hidden tab produces no frames, so each move is only released by the
    # 5 s fallback timer (kMaxRafDelay) -> ANY move count blows ANY sane budget.
    # Fewer moves never fixed that; keeping the harvest tab VISIBLE does (see
    # human_click's per-move timing + the executor's visibility guard).
    try:
        _cap = int(os.environ.get('TARGET_HARVEST_CLICK_MOVES', '9'))
    except (TypeError, ValueError):
        _cap = 9
    _cap = max(1, min(9, _cap))
    steps = max(1, min(_cap, int(dist / 80) + rng.randint(1, 3)))
    pts: List[Tuple[float, float, float]] = []
    for i in range(1, steps + 1):
        t = i / (steps + 1)
        bx = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cp_x + t ** 2 * x1 + rng.gauss(0, 0.6)
        by = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cp_y + t ** 2 * y1 + rng.gauss(0, 0.6)
        dt = 0.055 - 0.043 * math.sin(math.pi * t) + rng.uniform(-0.005, 0.012)
        pts.append((bx, by, max(0.004, dt)))
    pts.append((float(x1), float(y1), rng.uniform(0.03, 0.11)))   # settle + dwell
    return pts


class HarvestTabNotPainting(RuntimeError):
    """One CDP mouseMoved took longer than the abort threshold. Chromium queues
    mouseMoved as rAF-aligned input (MainThreadEventQueue::IsRafAlignedEvent);
    a tab that is not painting (background tab, minimized window) produces no
    main frame, so the event is released only by the 5 s fallback timer
    (kMaxRafDelay, "eg. Tab gets hidden"). A slow move is therefore a
    diagnosis — the tab must be re-activated — not something to wait out."""


# Runs in-page: visibilityState + whether ONE animation frame arrives within
# 700 ms. A painting tab answers in ~16-50 ms; a hidden tab never fires rAF.
VISIBILITY_PROBE_JS = """(() => new Promise((resolve) => {
    const t0 = performance.now();
    let done = false;
    const out = (raf_ms) => {
        if (done) return;
        done = true;
        resolve({vis: document.visibilityState, focus: document.hasFocus(), raf_ms: raf_ms});
    };
    try { requestAnimationFrame(() => out(Math.round(performance.now() - t0))); } catch (e) { out(-2); }
    setTimeout(() => out(-1), 700);
}))()"""


def visibility_verdict(info: Any) -> Tuple[str, str]:
    """('visible' | 'hidden' | 'stalled', detail) from a VISIBILITY_PROBE_JS
    result. hidden = document.visibilityState != 'visible' (background tab or
    minimized window -> re-activate); stalled = visible per the DOM but no
    animation frame within 700 ms (let the per-move abort decide)."""
    if not isinstance(info, dict):
        return ("stalled", f"probe returned {type(info).__name__}")
    vis = str(info.get("vis") or "?")
    try:
        raf = int(info.get("raf_ms", -1))
    except (TypeError, ValueError):
        raf = -1
    detail = f"vis={vis} raf_ms={raf} focus={'yes' if info.get('focus') else 'no'}"
    if vis != "visible":
        return ("hidden", detail)
    if raf < 0:
        return ("stalled", detail)
    return ("visible", detail)


async def human_click(tab, x: float, y: float,
                      start: Optional[Tuple[float, float]] = None,
                      stats: Optional[Dict[str, Any]] = None,
                      move_abort_ms: Optional[int] = None) -> Tuple[float, float]:
    """Move along a Bezier path and click (trusted CDP input events). Returns
    the final pointer position for the next call.

    `stats` (optional dict) receives 'sends': [(kind, ms), ...] as each CDP
    send completes, so a caller whose overall click budget expires still sees
    which sends finished and how long each took. `move_abort_ms` (default
    TARGET_HARVEST_MOVE_ABORT_MS=2500, 0 = off) raises HarvestTabNotPainting
    the moment ONE mouseMoved exceeds it — a painting tab acks a move in tens
    of ms, a hidden one only when Chromium's 5 s rAF fallback fires."""
    import asyncio
    from zendriver.cdp import input_ as cdp_input
    if move_abort_ms is None:
        try:
            move_abort_ms = int(os.environ.get('TARGET_HARVEST_MOVE_ABORT_MS', '2500'))
        except (TypeError, ValueError):
            move_abort_ms = 2500
    sends: List[Tuple[str, int]] = stats.setdefault('sends', []) if stats is not None else []

    async def _send(kind: str, cmd) -> None:
        t0 = time.monotonic()
        await tab.send(cmd)
        ms = int((time.monotonic() - t0) * 1000)
        sends.append((kind, ms))
        if kind == 'move' and move_abort_ms > 0 and ms > move_abort_ms:
            n_moves = sum(1 for k, _ in sends if k == 'move')
            raise HarvestTabNotPainting(
                f"mouseMoved #{n_moves} took {ms} ms (> {move_abort_ms} ms abort): "
                f"the harvest tab is not painting")

    sx, sy = start or (random.uniform(200, 900), random.uniform(150, 500))
    for bx, by, dt in bezier_path(sx, sy, x, y):
        await _send('move', cdp_input.dispatch_mouse_event(
            type_="mouseMoved", x=int(bx), y=int(by), pointer_type="mouse"))
        await asyncio.sleep(dt)
    await _send('press', cdp_input.dispatch_mouse_event(
        type_="mousePressed", x=x, y=y, button=cdp_input.MouseButton.LEFT,
        buttons=1, click_count=1, pointer_type="mouse"))
    await asyncio.sleep(random.uniform(0.06, 0.13))
    await _send('release', cdp_input.dispatch_mouse_event(
        type_="mouseReleased", x=x, y=y, button=cdp_input.MouseButton.LEFT,
        buttons=0, click_count=1, pointer_type="mouse"))
    return (x, y)


def click_stats_summary(stats: Optional[Dict[str, Any]]) -> str:
    """Compact per-send timing line for the log, e.g.
    'sends=5 moves=3 max_move_ms=18 press_ms=4 release_ms=3'."""
    sends = list((stats or {}).get('sends') or [])
    moves = [ms for k, ms in sends if k == 'move']
    press = [ms for k, ms in sends if k == 'press']
    rel = [ms for k, ms in sends if k == 'release']
    return (f"sends={len(sends)} moves={len(moves)} max_move_ms={max(moves) if moves else '-'} "
            f"press_ms={press[0] if press else '-'} release_ms={rel[0] if rel else '-'}")


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


# 2026-09-16 hot-sku plan P8 (HV-1, TARGET_HARVEST_MISS_PROBE): read-only snapshot
# of a harvest PDP that came back WITHOUT a usable Add-to-cart button. On 09-16
# alt-1 logged 298 "ATC button absent (ready=complete oos=False)" misses and the
# page itself was never recorded, so a HUMAN "Press & Hold" page, a redirect, an
# error page and a buy box that never rendered all looked the same (verdict H-03).
# ONE combined IIFE: the first seven keys duplicate px_challenge.PX_MARKERS_JS
# (same selectors + regexes, so px_challenge.is_px_challenge / describe classify
# it) and the rest describe the page. querySelector/innerText/location only -- no
# events, no clicks, nothing dispatched into the page.
HARVEST_MISS_PROBE_JS = r"""(() => {
  const q = (s) => { try { return !!document.querySelector(s); } catch (e) { return false; } };
  const cnt = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return -1; } };
  const raw = (document.body && document.body.innerText) || '';
  const txt = raw.slice(0, 20000);
  let path = '';
  try { path = String(location.pathname || ''); } catch (e) { path = ''; }
  let visBtn = 0;
  try {
    for (const b of document.querySelectorAll('button')) {
      const r = b.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) visBtn++;
    }
  } catch (e) { visBtn = -1; }
  const fulfil = cnt('[data-test*="fulfillment" i], [data-testid*="fulfillment" i]');
  const pxContainer = q('#px-captcha, [id^="px-captcha"], [class*="px-captcha"]');
  const pxIframe = q('iframe[src*="px-cdn.net"], iframe[src*="px-cloud.net"], iframe[src*="/captcha/"]');
  const pressHold = /press\s*(&|&amp;|and)\s*hold/i.test(txt);
  const denied = /access to this page has been denied|verify (that )?you are (a )?human|are you a human\??/i.test(txt);
  let hint = 'buybox_without_button';
  if (!raw.trim()) hint = 'blank_body';
  else if (pxContainer || pxIframe || pressHold || denied) hint = 'px_markers';
  else if (!/\/A-\d+/.test(path)) hint = 'not_a_pdp_url';
  else if (/something went wrong|page (was )?not found|can.t find (that|this|the) page|temporarily unavailable/i.test(txt)) hint = 'error_copy';
  else if (/out of stock|sold out|no longer available/i.test(txt)) hint = 'unavailable_copy';
  else if (fulfil === 0) hint = 'no_fulfillment_block';
  return {
    url: String(location.href || ''),
    title: String(document.title || ''),
    px_container: pxContainer,
    px_iframe: pxIframe,
    press_hold: pressHold,
    denied: denied,
    ready: String(document.readyState || ''),
    vis: String(document.visibilityState || ''),
    buttons: cnt('button'),
    buttons_vis: visBtn,
    fulfil: fulfil,
    text: raw.replace(/\s+/g, ' ').trim().slice(0, 160),
    hint: hint
  };
})()"""


def miss_probe_fields(info: Any) -> str:
    """The page half of the `MISS-PROBE` log line from a HARVEST_MISS_PROBE_JS
    result, e.g. "ready=complete vis=visible buttons=41 buttons_vis=12 fulfil=0
    hint=no_fulfillment_block text='...'". Pure; never raises."""
    if not isinstance(info, dict):
        return f"probe_result={type(info).__name__}"

    def _n(k):
        try:
            return int(info.get(k))
        except (TypeError, ValueError):
            return '?'
    text = str(info.get('text') or '')[:160]
    return (f"ready={info.get('ready') or '?'} vis={info.get('vis') or '?'} "
            f"buttons={_n('buttons')} buttons_vis={_n('buttons_vis')} fulfil={_n('fulfil')} "
            f"hint={info.get('hint') or '-'} text={text!r}")


# Runs in-page once per PDP nav (2026-09-09 audit #10): the captured page adds
# were STORE_PICKUP because the PDP defaulted to the profile's store, while the
# shot replays a SHIPPING add. Clicks the Shipping fulfillment cell (a UI
# toggle -- never the Add-to-cart button) when it is present and not selected.
SELECT_SHIPPING_JS = """(() => {
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
    const cands = [];
    for (const el of document.querySelectorAll('button, [role="radio"], [role="tab"], label')) {
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const txt = norm(el.getAttribute('aria-label') || el.textContent).slice(0, 80);
        const dt = norm((el.getAttribute('data-test') || '') + ' ' + (el.getAttribute('data-testid') || ''));
        // NEVER the Add-to-cart / Ship-it BUTTON itself (data-test="shippingButton",
        // "shipItButton", "addToCartButton…"): those are the real add, not a cell.
        if (/button$/.test(dt) || dt.includes('addtocart') || dt.includes('shipit')) continue;
        if (el.tagName === 'BUTTON' && /\\bship it\\b/.test(txt)) continue;
        const isCell = dt.includes('fulfillment') || dt.includes('shipping') || txt.startsWith('shipping');
        if (!isCell) continue;
        if (txt.includes('add to cart') || txt.includes('free shipping') || txt.includes('ship it')) continue;
        const selected = el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-checked') === 'true' ||
                         el.getAttribute('aria-selected') === 'true';
        cands.push({el: el, txt: txt, dt: dt, selected: selected});
    }
    const pick = cands.find(c => c.txt.includes('ship') || c.dt.includes('ship'));
    if (!pick) return {found: false, clicked: false, selected: false, seen: cands.slice(0, 4).map(c => c.txt)};
    if (pick.selected) return {found: true, clicked: false, selected: true, label: pick.txt};
    try { pick.el.click(); } catch (e) { return {found: true, clicked: false, selected: false, error: String(e)}; }
    return {found: true, clicked: true, selected: false, label: pick.txt};
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
