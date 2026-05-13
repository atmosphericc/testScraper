# Resilient Stock-Check Stack (Refract-pattern)

## Status
Validated 2026-05-13 — **100% success rate sustained over 5 min** (567/567 200s,
zero IP burns during the run, 20 active IPs holding steady at ~1.9 RPS).
30-min extended test in progress.

## Purpose
Hit Target's RedSky stock-status API continuously without detection or rate
limiting, so the bot can monitor 20+ TCINs 24/7 leading into a drop. Uses the
proven Refract bot architecture (the leading Target/Pokemon bot in 2026) where
a browser provides session trust and curl_cffi tasks fire fast HTTP through
proxies.

## Components

### 1. Local CONNECT forwarder — `src/proxy/local_forwarder.py`
Solves the Bright Data proxy auth problem cleanly. Chrome/curl_cffi connect to
`127.0.0.1:<port>` without auth; the forwarder opens the upstream CONNECT to
`brd.superproxy.io:33335` with the proper `Proxy-Authorization: Basic <b64>`
header. No TLS interception, no extension hacks, no Chrome rejection of
embedded creds.

Multi-port mode: each BD IP gets its own local port. Workers pick a port to
choose which BD IP they exit from. Production main pool uses ports
**24000+**, pre-flight uses **23000+**.

### 2. Per-IP state — `src/proxy/proxy_state.py`
Atomic-persisted state per BD-pinned IP:
- `visitor_id` — stable Target-style 32-hex GUID, generated once per IP
- `consec_403` — current consecutive-403 streak
- `parked_until` — epoch when IP becomes usable again
- `park_count` — how many times we've parked (BURN after 4)
- success / 403 counts, last status, etc.

Policy:
- **PARK_AFTER_403_STREAK = 2** consecutive 403s → park
- **PARK_DURATION_S = 600** (10 min) — soft-flagged IPs recover this fast
- **BURN_AFTER_PARKS = 4** park cycles → mark hard-burned, never retry

### 3. Pre-flight validator — `src/monitoring/proxy_preflight.py`
At startup, probes every configured proxy once. Returns only the subset that
returned a clean 200 + parseable response. **Eliminates "discovery-phase
403s"** — workers never see a 403 from a known-bad IP.

In practice today: ~7 of 27 enabled proxies fail preflight (the 31.105.x
cluster that's been burned through test pressure). After preflight, the
remaining 20 IPs sustain 100% over 5+ minutes.

### 4. Stock-check workers — `src/monitoring/stock_check_resilient.py`
Three async workers, round-robin across (tcin × verified-clean IP) tuples.
Each worker:
- Picks the next (tcin, proxy) tuple from `ProxyState.active_entries()`
- Builds curl_cffi request with:
  - `impersonate="chrome131"` → real Chrome JA3/JA4
  - Modern endpoint `product_fulfillment_and_variation_hierarchy_v1`
  - Per-IP stable `visitor_id` param
  - Full sec-fetch-* / sec-ch-ua-* headers + `is_bot=false`
  - `channel=WEB`, `page=/p/A-{tcin}`, geo params
  - Cookies from `state/cookies_jar.json` (if present from harvester)
- Records result → `ProxyState.record_status()` triggers auto-park if needed

Background loops:
- **`_parked_retest_loop`** every 5 min unparks IPs whose park has expired
- **`_cloaking_alarm_loop`** every 30s — if ALL TCINs simultaneously report
  OOS, alarms (real-world OOS cannot synchronize across unrelated products)
- **`_stats_loop`** every 30s — heartbeat log of pool health + RPS

### 5. Cookie harvester — `src/session/cookie_harvester.py` (optional)
zendriver headed browser through ONE local forwarder, navigates to
`target.com` every 20 min, dumps `_px2`, `_abck`, `_pxvid`, `bm_sz` etc to
`state/cookies_jar.json` atomically. Workers attach these cookies on each
request for additional trust scoring.

**NOTE:** Validated stack runs without the harvester at 100% — cookies are
defense-in-depth, not required for the current rate.

### 6. App integration — `app.py`
Opt-in via env var:
```
USE_RESILIENT_STACK=1 python app.py
USE_RESILIENT_STACK=1 RESILIENT_TARGET_RPS=2 python app.py
```
Falls back to legacy proxy_workers path on any startup error.

## Why this works against Shape Security

Shape's primary detector is JA3/JA4 TLS fingerprint. Python `requests` /
`urllib3` produces a known-bot fingerprint regardless of headers or UA. The
`curl_cffi` library wraps `curl-impersonate`, which patches curl to produce
the **exact** TLS handshake of a real Chrome 131 browser — same cipher order,
extension order, HTTP/2 SETTINGS, ALPN, etc.

When the request envelope matches Chrome (sec-fetch-*, sec-ch-ua-*, is_bot=
false, visitor_id, channel, page) AND the JA3 matches Chrome 131, Shape
treats the request as a legitimate browser request. Per-IP reputation does
not ratchet down on these requests — which is why we sustained 100% with
zero IP burns over 5 minutes.

The disabled/burned IPs (27 currently in `disabled_proxies`) are hard-blocked
at Shape's edge from accumulated prior reputation damage. Pre-flight filters
them out at startup so the worker pool only uses verified-clean IPs.

## Tuning knobs

| Env var | Default | What it does |
|---|---|---|
| `USE_RESILIENT_STACK` | `0` | Set to `1` to enable; `0` = legacy threaded workers |
| `RESILIENT_TARGET_RPS` | `3.0` | Aggregate stock-check rate across all workers |

In code:
- `ResilientStockChecker(... preflight=False)` to skip pre-flight (not
  recommended, but useful for diagnosing pool issues)
- `ResilientStockChecker(... cookies_jar_path=Path("custom.json"))` to use a
  different cookie jar location

## Operational notes

- **Initial pre-flight takes ~2 seconds** (parallelism=5, 27 IPs)
- **Per-cycle latency ~1.9 RPS actual** (target 3.0; gap is curl_cffi sync
  handshake overhead in the to_thread bridge)
- **State persists** in `state/proxy_state.json` — restarts pick up the same
  per-IP visitor_ids and park history
- **Background retest** every 5 min unparks soft-flagged IPs that have served
  their 10-min cooldown
- **Cloaking alarm** fires when all 200-status TCINs simultaneously report
  OOS for 2 consecutive cycles
