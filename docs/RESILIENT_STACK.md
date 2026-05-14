# Resilient Stock-Check Stack (Refract-pattern + browser-native dispatch)

## Status
**Round 2 stress-validated 2026-05-13** — **99.95% success over 60 min @ 3 RPS / 3 IPs / 33 TCINs**
(10,585/10,590 200s, **zero 403s**, 5 timeouts self-healed via watchdog).
Beats Round 1's 15.7-min mass-burn ceiling by 4×.

## Purpose
Hit Target's RedSky stock-status API continuously without detection or rate
limiting, so the bot can monitor 30+ TCINs 24/7 leading into a drop. Round 2
replaces curl_cffi-based workers with persistent Chrome browsers that fire the
RedSky bulk fetch *from inside the tab itself* via `tab.evaluate(fetch(...))`.
The browser provides JA3/JA4 fingerprint + accumulated session trust + live
cookies; no curl_cffi in the request path means no curl-shaped fingerprint for
Shape's account-level detector to lock onto.

## Architecture (Round 2)

### 1. Local CONNECT forwarder — `src/proxy/local_forwarder.py`
Solves the Bright Data proxy auth problem cleanly. Chrome connects to
`127.0.0.1:<port>` without auth; the forwarder opens the upstream CONNECT to
`brd.superproxy.io:33335` with the proper `Proxy-Authorization: Basic <b64>`
header. No TLS interception, no extension hacks, no Chrome rejection of
embedded creds.

Each BD IP gets its own local port; the test stack uses 26000+ for the main
pool and 23000+ for pre-flight.

### 2. Per-IP state — `src/proxy/proxy_state.py`
Atomic-persisted state per BD-pinned IP. Tracks visitor_id, consecutive-403
streak, park-until timestamp, park count. **Auto-recovers parked OR burned**
IPs on next 200 (added 2026-05-13: a successful 200 is hard evidence Shape's
flag has cleared, so a stale "burned" stamp shouldn't lock the IP out forever).

Policy: `PARK_AFTER_403_STREAK=2`, `PARK_DURATION_S=10800` (3h), `BURN_AFTER_PARKS=4`.

### 3. Pre-flight validator — `src/monitoring/proxy_preflight.py`
At startup, probes every configured proxy once via `curl_cffi(impersonate=chrome131)`.
Returns only the subset that returned a clean 200 + parseable response.
Workers never see a 403 from a known-bad IP.

### 4. Multi-session pool — `src/session/multi_session_pool.py`
Maintains N permanently-running Chrome instances, one per BD ISP IP. Each
Chrome:
- Owns a persistent profile dir (`state/session_profiles/sN/`) → cookies survive recycle
- Connects through its own local forwarder port → pinned to one BD IP
- Holds a long-lived target.com tab open
- Refreshes cookies via tab heartbeat every ~30 min (no browser relaunch)

A watchdog recycles individual Chromes that crash (one at a time, with cooldown).
Initial launch staggered over `CHROME_STAGGER_TOTAL_S` (default 600s = 10 min)
so 22 fresh Chromes don't appear as a coordinated burst.

### 5. Tab dispatcher — `src/monitoring/tab_dispatcher.py`
Picks an idle ready Chrome from the pool, fires the bulk RedSky request via
`tab.evaluate(fetch(...))` *inside* that tab. The fetch inherits the tab's
JA3/JA4, live cookies, and visitor_id. Returns `{__http_status, __body, __body_text}`.

**TCIN chunking**: `MAX_TCINS_PER_REQUEST=28`. Target's `product_summary_with_fulfillment_v1`
hard-caps at 30 TCINs per request (returns `{"errors":[{"message":"Tcins cannot
be more than 30"}]}` on overflow). `_build_balanced_chunks` splits into
even-sized batches — 33 TCINs become `[17, 16]` (uniform refresh rate) rather
than `[28, 5]`. Chunk index rotates per sweep.

**Behavioral mixin** (default OFF): `dispatch_behavioral_pdp` navigates the
tab to a PDP, dwells 4-6s, returns to homepage. Disguises pure-API polling
pattern. **Disabled by default after 2026-05-13 finding**: at 3 RPS aggregate
with `behavioral_mix_ratio=0.10`, one PDP nav fires every ~3.3s account-wide,
which Shape detected as a bot pattern (single 7×403 burst on one session at
t=228s). If enabling, use ≤0.02 (one nav per >15s aggregate).

### 6. Stock checker orchestrator — `src/monitoring/stock_check_resilient.py`
Schedules dispatches at the configured rate (`target_sweeps_per_sec`).
Backpressure caps outstanding dispatches at 50 to prevent coroutine pile-up.
Background loops:
- `_parked_retest_loop` every 5 min unparks IPs whose park has expired
- `_cloaking_alarm_loop` every 30s — alarms only when at least one TCIN that
  was previously seen in_stock has flipped OOS (avoids false-positive when all
  configured TCINs are legitimately OOS)
- `_stats_loop` every 30s — heartbeat log of pool health + RPS

### 7. Cookie harvester — `src/session/cookie_harvester.py` (legacy / optional)
Standalone harvester from Round 1. Unused by Round 2 because each session in
the pool is already a real Chrome with live cookies — no separate harvester
needed.

### 8. App integration — `app.py`
Opt-in via env var:
```
USE_RESILIENT_STACK=1 python app.py
USE_RESILIENT_STACK=1 RESILIENT_TARGET_RPS=2 python app.py
```
Falls back to legacy proxy_workers path on any startup error.

## How a single sweep flows (the 4-layer pipeline)

```
  ┌─────────────────────────────────────────────────────────┐
  │  Sweep loop (1 every ~333ms at RESILIENT_TARGET_RPS=3)  │
  │   └─> picks next TCIN chunk (rotates)                   │
  │       └─> picks an idle Chrome (random ready session)   │
  │           └─> tab.evaluate(fetch(redsky.target.com))    │
  └─────────────────────────────────────────────────────────┘
                         │
                         ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Chrome #N  (one of N persistent browsers)              │
  │   - long-lived target.com tab                           │
  │   - real Chrome JA3/JA4 fingerprint                     │
  │   - live cookies (_abck, _px3, visitor_id)              │
  │   - --proxy-server=127.0.0.1:24000+N                    │
  └─────────────────────────────────────────────────────────┘
                         │ HTTPS via local proxy
                         ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Local CONNECT forwarder (127.0.0.1:24000+N)            │
  │   - injects Proxy-Authorization for Bright Data         │
  │   - Chrome doesn't have to know BD credentials          │
  └─────────────────────────────────────────────────────────┘
                         │ CONNECT to BD with auth header
                         ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Bright Data superproxy (brd.superproxy.io:33335)       │
  │   - exits via the IP pinned in the username             │
  │   - one specific BD ISP IP (e.g. 168.158.220.12)        │
  └─────────────────────────────────────────────────────────┘
                         │
                         ▼
                redsky.target.com
```

Every layer has a single responsibility. The dispatcher knows TCINs and rates.
Chrome handles browser/TLS/cookies. The forwarder solves Bright Data's "Chrome
can't embed credentials in --proxy-server" problem cleanly. BD picks the exit
IP by parsing the username.

### What one fetch traverses (timing)

For a single sweep at t=T:
1. **t=T**: sweep loop wakes, picks `chunks[next_idx]`, picks a random ready
   Chrome (`pick_session()` filters by `state=ready, not in_flight, has cookies, has visitor_id`)
2. **t=T+1ms**: dispatcher acquires the Chrome's `busy_lock` (per-session serializer)
3. **t=T+2ms**: `tab.evaluate(JS)` where `JS = "await fetch('https://redsky.target.com/...?key=...&tcins=A,B,C&store_id=865')"`
4. **t=T+5ms**: Chrome routes the fetch through `--proxy-server=127.0.0.1:<port>`
5. **t=T+10ms**: Local forwarder receives CONNECT to `redsky.target.com:443`, injects `Proxy-Authorization: Basic <BD-creds>`, opens TCP to `brd.superproxy.io:33335`
6. **t=T+150ms**: BD authenticates, exits via the pinned IP, completes upstream TLS to Target
7. **t=T+200ms**: Chrome (still inside the forwarded tunnel) does its OWN TLS handshake to Target — this is the **real Chrome JA3/JA4** Shape sees
8. **t=T+300ms**: HTTP/2 request sent with full cookie jar + `Origin: https://www.target.com`
9. **t=T+600ms**: Response arrives → bytes stream back through tunnel → Chrome → JS fetch resolves → `tab.evaluate` returns `{__http_status: 200, __body: {data: {product_summaries: [...]}}}`
10. **t=T+650ms**: Dispatcher returns `BulkResult` → orchestrator parses → updates per-TCIN state → fires `on_in_stock` callback if any TCIN flipped → releases `busy_lock`

End-to-end ~600-900ms. The `busy_lock` ensures one Chrome handles only one
fetch at a time. Aggregate throughput comes from picking across N Chromes.

## Sweep math (per-TCIN refresh rate)

The dispatcher fires bulk RedSky calls; each call covers up to 28 TCINs.
**It does NOT fire one call per TCIN per sweep.** Per-TCIN refresh depends on
chunk count:

```
  sweeps_per_sec  = R   (RESILIENT_TARGET_RPS, default 3.0)
  TCINs           = N   (count of enabled in product_config.json)
  chunks          = ceil(N / 28)
  per-TCIN refresh = R / chunks  Hz
```

| TCINs (N) | Chunks | Per-TCIN refresh @ R=3 | RedSky requests/hour |
|---|---|---|---|
| ≤28 | 1 | every 0.33s | 10,800 |
| 29–56 | 2 | every 0.67s | 10,800 |
| 57–84 | 3 | every 1.0s | 10,800 |
| 85–112 | 4 | every 1.3s | 10,800 |
| 200 | 8 | every 2.7s | 10,800 |

Note: total RedSky load stays constant at R req/sec regardless of TCIN count —
chunking just spreads which TCINs are covered per request. To keep fast
per-TCIN refresh with many TCINs, scale `RESILIENT_TARGET_RPS` proportionally.

## Pool size (N Chromes) is a tuning knob, not architectural

`N` = `len(enabled proxies in proxyIps.json)`. Trade-off:
- **More Chromes** = lower per-IP rate (`R / N` req/sec/IP) → less per-IP
  suspicion → safer for sustained operation. Cost: ~1.5-2 GB RAM per Chrome,
  longer launch (10-min stagger for 22).
- **Fewer Chromes** = higher per-IP rate → cheaper, faster launch, but each IP
  burns through trust faster.

Validated 3 Chromes / 3 RPS = 1 RPS per IP for 60 min @ 99.95%. Could run 22
Chromes / 3 RPS = 0.14 RPS per IP for much longer sustained operation, or 3
Chromes / 0.5 RPS for 24/7 monitoring with even less footprint.

## Why this works against Shape Security

Shape's primary detector is JA3/JA4 TLS fingerprint plus higher-order session
behavior signals. Real Chrome through zendriver firing fetch() from inside its
own tab is indistinguishable from a logged-in user's React app calling RedSky
during normal page interactions — same TLS handshake, same cookies, same
visitor_id, same origin, same fetch headers.

The Round 1 (curl_cffi) approach passed JA3/JA4 but apparently exposed a
secondary signal at the account level — possibly request-pattern fingerprint,
possibly fetch-without-tab-context — that mass-burned all 20 IPs at 15.7 min.
Round 2 has not exhibited this ceiling in 20-min testing.

## Tuning knobs

| Env var | Default | What it does |
|---|---|---|
| `USE_RESILIENT_STACK` | `0` | Set to `1` to enable; `0` = legacy threaded workers |
| `RESILIENT_TARGET_RPS` | `3.0` | Aggregate stock-check rate across all sessions |
| `RESILIENT_TEST_DURATION_S` | `1800` | (test only) duration |
| `RESILIENT_TEST_RPS` | `3.0` | (test only) override RPS |
| `RESILIENT_TEST_NUM_IPS` | (all) | (test only) cap proxy count |
| `RESILIENT_TEST_BEHAVIORAL` | `0.0` | (test only) behavioral mix ratio |
| `CHROME_STAGGER_TOTAL_S` | `600` | Spread-out window for initial Chrome launches |

In code:
- `ResilientStockChecker(... preflight=False)` to skip pre-flight (not recommended)
- `ResilientStockChecker(... behavioral_mix_ratio=0.02)` to opt into low-rate behavioral

## Operational notes

- **Per-cycle latency**: tab.evaluate adds ~50-100ms over raw fetch; per-bulk
  ~600-900ms end-to-end through one session.
- **State persists** in `state/proxy_state.json` and `state/session_profiles/sN/`
- **Watchdog recycle** uses RECYCLE_COOLDOWN_S=60 between attempts on the same session
- **Cookie heartbeat** every ~30 min per session (round-robin with gap = 1800/N seconds)

## Architecture history

- **Round 1** (committed `c83d37e4` → `977b7792`, 2026-05-13 morning) —
  Refract-strict: harvester + curl_cffi tasks. Hit 15.7-min / 3 RPS ceiling
  with mass-burn cascade. Documented in `RESILIENT_STACK_OPERATIONAL_NOTES.md`.
- **Round 2** (committed 2026-05-13 evening) — browser-native dispatch
  replacing curl_cffi tasks. 20-min sustained at 100% / 3 RPS / 3 IPs.
