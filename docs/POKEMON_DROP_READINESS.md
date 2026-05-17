# Pokemon Drop Readiness

What's tested vs. what we're betting on hope for.

## Last updated
2026-05-17

## TL;DR for drop day

The bot is **not** validated against a real queue. The dev gate (2026-05-16)
proved the resilient stack works against a permissive baseline product (notebook).
Pokemon drops are a different operating mode (always-queue, elevated PerimeterX
scoring, sub-second checkout window after admission). Going into a real drop
you should expect failures in code paths that haven't been exercised against
the queue interstitial.

What we've code-prepared but not validated under live drop conditions:
- Queue interstitial detection in monitoring fetch (Walmart adapter)
- 2026 ticket-API queue handler (state, likelihood, expiration parsing)
- Hybrid checkout via GraphQL mutations (`walmart/checkout_api.py`)

What we have NOT done that real drops need:
- Multi-session queue racing (sketched below, not implemented)
- `_px3` refresh strategy during a 1-2 hour queue wait
- Verified queue-then-ATC sub-second timing
- Verified hybrid checkout works post-queue admission
- Residential proxies (we're on ISP — top bots are on residential for checkout)

## What's queued, what isn't

| Product type | Queue probability |
|---|---|
| Pokemon TCG drop (Wednesday 9 PM ET) | ~100% |
| Pokemon TCG restock (random) | ~70% (depends on inventory size) |
| Console drops (Xbox, PS5, GPU) | ~100% during launch windows |
| Normal Pokemon products outside drop window | ~5% |
| Throwaway notebook (item 320424995) | 0% |

Our laptop dev gate ran against the notebook. The validation tells us nothing
about queue behavior.

## Queue mechanics summary (researched 2026-05-17)

Walmart runs its own in-house queue (not Queue-it):

- **URL pattern**: `walmart.com/qp?qpdata=<URL-encoded JSON>`
- **Ticket API host**: `api.waiting-room.walmart.com`
- **Endpoints**: `issueTicket`, `checkTicket`, `refreshTicket`, `validateTickets`
- **States**: `pending` → `valid` (admitted) | `expired` (evicted)
- **Polling cadence**: server-dictated via `nextRefreshRelativeTime`, typically 20-40s
- **Critical signal**: `customMetadata.admissionLikelihood` = `"likely"` or `"unlikely"`
  — Walmart's confidence stock will remain by your turn
- **Typical wait**: 1.5 to 2 hours for popular Pokemon drops
- **Bypass history**: "Frequently bought together" widget exploited Prismatic
  Evolutions (May 2025, Refract ~75% of stock); patched. No current bypasses
  publicly documented.

## What our code handles now (laptop unit-tested)

### Queue detection in monitoring fetch
`walmart/walmart_adapter.py` — the resilient stack's monitoring loop emits
`ItemStatus(in_stock=True, availability_status="QUEUED:...")` when a fetch
returns either:
- (A) Redirect to `/qp?qpdata=...` (URL contains `/qp` or `qpdata=`)
- (B) HTML body containing `api.waiting-room.walmart.com`, `issueTicket`, or `checkTicket` signatures

The `in_stock=True` flag triggers the purchase manager's `on_in_stock`
callback, which navigates to the product page and runs the queue handler.

Unit tests: `test_walmart_framework_unit.py` (18 queue-detection cases).

### Queue handler (purchase-side)
`walmart/queue_handler.py` — rewritten 2026-05-17 for the 2026 model.

Detection:
- `is_queue_url(url)` — true if URL contains `/qp`
- `is_ticket_api_url(url)` — true if URL is `api.waiting-room.walmart.com/{checkTicket|issueTicket|refreshTicket}`
- Legacy DOM overlay fallback for older drop UIs

Pure-functional parsers:
- `parse_qpdata(qpdata_str) → QueueTicket` — extracts queue_id + item metadata
- `parse_ticket_response(body) → QueueTicket` — parses state, likelihood, refresh interval

Async API:
- `QueueHandler.detect() → Optional[QueueTicket]` — one-shot check
- `QueueHandler.detect_and_wait(timeout, max_unlikely_streak)` — full wait loop:
  - Returns ticket with `state=valid` on admission
  - Returns ticket with `state=expired` on eviction
  - Returns ticket early if `admissionLikelihood=unlikely` observed N times in a row
  - Returns None or stale ticket on timeout

CDP listener: attaches `Network.responseReceived` handler that watches for
ticket API responses and updates `self._last_ticket`. We do NOT poll the
API ourselves — we let Walmart's queue page JS poll at its dictated cadence
and we observe.

Unit tests: `test_walmart_queue_handler.py` (41 cases).

## What our code does NOT handle (gaps for drop day)

### 1. Multi-session queue racing — DESIGN ONLY

The resilient stack has N=16 sessions monitoring. When a drop fires and
all 16 sessions detect `availability_status="QUEUED"`, the current
architecture would fire 16 `on_in_stock` callbacks, each starting an
independent purchase flow on its session. That's actually what we want
conceptually, but the current `WalmartPurchaseManager` (existing single-
session code) doesn't support concurrent purchases on different sessions.

**Design sketch** (to implement when Phase 2 cutover lands):

```
ResilientChecker.on_in_stock(status) {
    if status.availability_status.startswith("QUEUED:"):
        # Race: dispatch the queue+purchase flow on all idle sessions
        # in the pool. They each enter the queue, get their own ticket,
        # poll independently. First to reach state=valid wins.
        for session in pool.all_ready_sessions():
            mark session.in_queue = True   # tells keepalive to skip
            asyncio.create_task(
                queue_then_purchase(session, status.item_id)
            )
    else:
        # Normal restock: single-session purchase (existing flow)
        purchase_manager.handle(status)
}
```

Open questions for this design:
- **One-account-many-sessions limit**: Walmart caps one checkout per
  account per SKU. If all 16 sessions are the same logged-in user, only
  one can actually place an order. We'd need either multiple accounts
  (we have one) or accept that only one of 16 wins per drop.
- **`_px3` survival across N=16 concurrent queue waits**: PerimeterX may
  score the "16 sessions of the same account joining queue simultaneously"
  pattern as bot behavior even if individual sessions look clean.
- **Mid-queue keepalive skip**: SessionEntry needs an `in_queue` flag
  that the pool's `_heartbeat_one` respects, otherwise the keepalive
  loop will navigate a queueing tab back to the homepage and discard
  the ticket. (Documented in `queue_handler.py` TODO.)

### 2. `_px3` refresh during long queue waits

The queue's own page JS continuously runs PerimeterX challenges and
refreshes `_px3`. So in principle the session's cookies stay fresh
without our intervention.

In practice we don't know if BD ISP proxies survive the cumulative
1-2 hours of PX scoring at elevated drop scrutiny. The dev gate ran for
15 min on a non-queueing product; queue-day load profile is different.

Mitigation if cookies expire mid-queue: the resilient stack's
`ResilientChecker._record_status` would record 403s from the affected
session, ProxyState would park the IP after 2 consecutive 403s (Walmart
adapter: 10-min park), and other sessions in the pool would continue.

Not mitigation enough: a parked IP loses its queue ticket. If 14 of 16
sessions park during the queue wait, we've effectively lost 14 queue
slots and only the 2 surviving sessions race.

### 3. Hybrid checkout post-queue admission

`walmart/checkout_api.py` is fully wired with captured GraphQL hashes
for `updateItems`, `CreateContract`, `reserveSlotMutation`. The hybrid
path is enabled via `WALMART_CHECKOUT_API=1`.

Open question: does the API path work *after* a queue passthrough?
The hashes were captured during a normal-flow checkout, not a post-
queue one. Walmart may serve different hashes during high-demand drops
(observed pattern: weekly deploys often coincide with Pokemon drop days).

Mitigation: APQ full-query fallback (not implemented — would need full
query bodies, which we don't have captured). If a hash returns
`PersistedQueryNotFound`, the hybrid path falls back to DOM checkout
which costs 5-15 extra seconds.

### 4. Sub-second window after admission

When `state=valid` arrives, every session that races has roughly 200ms
to 2 seconds before stock sells out (depending on drop size). Our ATC
+ cart + checkout flow uses real Chrome and CDP-driven clicks — we
measure these in seconds, not milliseconds.

This is intrinsic to using real-browser automation rather than direct
HTTP POSTs to the checkout API. Top bots that win sub-second windows
use TLS-spoofed HTTP clients (curl-impersonate, tls-client) firing
direct GraphQL POSTs — they don't render UI.

Our best window-close strategy is hybrid checkout (already implemented
in `walmart/purchase_executor.py`), which collapses ATC + qty + checkout
into 2-3 GraphQL POSTs from inside the browser tab. Sub-second is still
unlikely; 2-3 seconds is achievable.

### 5. PerimeterX elevated drop scoring

PX is more aggressive during high-demand windows. The May 2026
"new CAPTCHA update" (per @FlipFlip tweet) suggests rules tightened
recently. Our dev gate ran during a normal weekday — drop-day scoring
is unknown.

If we see widespread 403s or `/blocked` redirects during a real drop,
the only mitigations available without new spend:
- Lower aggregate RPS (already conservative at 0.375 RPS/IP)
- Wait for PX scores to decay (hours, not minutes)
- Accept the loss for that drop, retry next Wednesday with fresher IPs

## Drop-day operational checklist

Pre-drop (1-2 hours before):
1. Confirm master Walmart login is fresh: `python walmart_relogin.py`
   if `walmart-profile-login/` was created more than ~7 days ago.
2. Seed sessions: `python -m walmart.walmart_session_bootstrap`
   (all N=16 in prod, or `--first 2` for laptop dry-run).
3. Confirm all sessions verify clean.
4. Edit `walmart/walmart_config.json` to add the target Pokemon item_id.
5. Run the resilient stack: `WALMART_USE_RESILIENT=1 python walmart_app.py`
   (NOTE: Phase 2 env-gate not yet implemented — currently you'd run
    `python -m walmart.walmart_stock_resilient` and the purchase manager
    integration is unwired.)

During drop:
- Monitor logs for `availability_status="QUEUED:..."` — that's the
  drop-fire signal.
- Watch for `state=valid` admission logs (from `queue_handler`).
- Watch for `state=expired` on any session — those sessions should
  be re-bootstrapped between drops.
- Watch `admissionLikelihood=unlikely` — if persistent, drop is gone.

Post-drop:
- Inspect `state/walmart_proxy_state.json` for parked IPs.
- If many IPs parked, wait 1 hour minimum before next drop attempt.
- If hybrid checkout returned `PersistedQueryNotFound`, the hashes
  are stale — re-run `walmart/checkout_capture.py` capture + analyzer
  to get new hashes for the next drop.

## What would meaningfully improve drop readiness

In rough priority order:

1. **Wire `WALMART_USE_RESILIENT=1` in `walmart_app.py`** (Phase 2 cutover)
   — without this, the resilient stack doesn't actually drive a real
   purchase flow.
2. **Multi-session queue race in `ResilientChecker.on_in_stock`** — exploits
   N=16 advantage instead of single-session waiting.
3. **`in_queue` flag on `SessionEntry`** — pool keepalive must skip
   queueing sessions or they lose their tickets.
4. **APQ full-query fallback for checkout hashes** — survives Walmart's
   weekly GraphQL hash rotation. Needs full query bodies captured first.
5. **Validate against a real drop** — pick a low-stakes Pokemon Wednesday,
   run prod stack with N=16, observe everything. The data from one real
   drop is worth more than 10 hours of laptop simulation.

## Honest summary

Going into a Pokemon drop with the current code, expected outcomes:
- Resilient stack will detect the queue interstitial (✓ unit-tested)
- Bot will navigate to the product page and enter the queue (✓ unit-tested)
- Bot will wait until admitted or evicted (✓ unit-tested)
- Bot will execute hybrid checkout post-admission (✓ wired, not validated post-queue)
- Bot will win ~1 of N attempts at drop scale (uncertain — depends on
  how much faster top bots are on residential proxies + tls-client)

The realistic expectation is "we won't be in the worst tier of botters,
but we won't be in the top tier either." For collectibles with limited
stock, that means we might not win consistently. For drops with deeper
inventory, we should win at least some units.
