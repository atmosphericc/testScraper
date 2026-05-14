# Resilient Stock-Check Stack (Refract-pattern + browser-native dispatch)

## Status
**Round 2 validated 2026-05-13** — **100% success sustained 20 min at 3 RPS / 3 IPs / 33 TCINs**
(3562/3562 200s, zero 403s, zero "other"). This beats Round 1's 15.7-min ceiling
that mass-burned all IPs at the same rate.

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
