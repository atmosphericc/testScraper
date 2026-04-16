# Codebase Map
## Last Updated: 2026-04-08 (code-quality pass)

## Backend (Python)

### Root-Level Production Apps

**app.py** — Primary Target.com dashboard & purchasing engine
- Flask app with real-time SSE for UI updates
- `ThreadSafeData` — shared state management with locks
- Routes: `/`, `/api/stream` (SSE), `/api/status`, `/api/products`, `/add-product`, `/remove-product`
- Features: Timer persistence, circuit breaker for API failures, cache TTL management
- Checks `os.environ.get('TEST_MODE')` to skip final purchase step

**test_app.py** — Test mode wrapper for app.py
- Sets `TEST_MODE=true` before importing app
- Prevents actual purchases, enables endless loop testing with cart reset

**unified_app.py** — Multi-retailer AIO dashboard entry point
- Mounts Target routes at `/`, Walmart blueprint at `/walmart/`
- Loads `.env` (WALMART_EMAIL, WALMART_PASSWORD, WALMART_CVV)
- Prevents system sleep on Windows and macOS

### Target.com Backend (src/)

**src/session/session_manager.py**
- `SessionManager` — browser lifecycle, cookie management, validation
- Uses zendriver (undetected Chrome), persistent nodriver profile
- CDP cookie interception, context recreation counter, validation failure tracking

**src/session/purchase_executor.py** — DO NOT MODIFY
- `PurchaseExecutor` — full Target.com cart/checkout/payment automation
- CVV loaded from `CARD_CVV` constant (move to `.env` via `TARGET_CVV` if not already done)
- CDP header interception for auth, cached auth headers, retry logic

**src/session/session_keepalive.py**
- `SessionKeepAlive` — background health check thread
- Configurable intervals, automatic session re-establishment

**src/monitoring/stock_monitor.py**
- `StockMonitor` — Target RedSky bulk API polling via 50-proxy round-robin (~3 checks/sec)
- Each proxy fires every 15s, staggered at 0.3s intervals = ~3 bulk checks per second across all TCINs
- API: `redsky.target.com/redsky_aggregations/v1/web/product_summary_with_fulfillment_v1`
- Multiple API keys for redundancy, proxy rotation, test mode scenario generators

**src/purchasing/bulletproof_purchase_manager.py**
- `BulletproofPurchaseManager` — stock→queue→purchase orchestration
- File-locking (fcntl/msvcrt), TTL-based state cleanup, race condition prevention
- Auto-resets stuck "attempting" (>60s) and "queued" (>5s) states
- `_PurchaseLogTee` tees console output to purchase log files

**src/utils/target_login.py**
- Target.com login via zendriver — handles passkey bypass, "Keep me signed in"
- Credentials read from `TARGET_EMAIL` and `TARGET_PASSWORD` env vars (defaults to empty string if unset)

**src/utils/save_login.py**
- Exports Target session cookies via CDP to target.json

### Walmart.com Backend (walmart/)

**walmart/purchase_executor.py**
- `WalmartPurchaseExecutor` — patchright-based add-to-cart, checkout, payment
- Handles press-and-hold CAPTCHA via challenge solver

**walmart/session_manager.py**
- `WalmartSessionManager` — patchright browser session
- Dual-tab strategy (warmup tab + main tab), proxy rotation, challenge handling

**walmart/stock_monitor.py**
- `WalmartStockMonitor` — browser fetch architecture (fetch() inside real Chrome tab)
- Config: `GRAPHQL_HASH` in walmart/config.py (auto-discovers `GRAPHQL_HASH_ATF` at runtime)
- `proxy_manager=None` and `page=None` accepted for call-site compatibility, unused internally

**walmart/purchase_manager.py**
- `WalmartPurchaseManager` — purchase state machine orchestration

**walmart/queue_handler.py**
- `QueueHandler` — priority-based queue with purchase state tracking

**walmart/blueprint.py**
- Flask Blueprint mounted at `/walmart/` in unified_app.py
- Routes: `/`, `/api/stream` (SSE), `/api/status`, `/add-product`, `/test/enable|disable`
- Independent from Target globals — safe in single process

**walmart/walmart_app.py** — Standalone Walmart-only Flask app (port 5001)
- Self-contained alternative to using the unified dashboard
- Same routes as blueprint.py but runs as an independent Flask process
- Use `unified_app.py` instead for combined Target + Walmart operation

**walmart/self_healing_agent.py**
- `HtmlSelectorPatcher`, `FailureAnalyzer`, `SelfHealingAgent`
- Captures HTML → diagnoses failure → patches selectors → restarts
- Failure categories: WRONG_SELECTOR, ANTIBOT_BLOCK, QUEUE_TIMEOUT, OUT_OF_STOCK, LOGIN_FAILURE, NETWORK_TIMEOUT, UNKNOWN
- Only modifies walmart/ files; validates Python syntax before patching

**walmart/config.py**
- Walmart product list, proxy pool sizes, GraphQL hash

**walmart/proxy_manager.py**
- `ProxyManager` — proxy rotation, health tracking, cooldown

**walmart/stock_check.py**
- Direct Walmart GraphQL API stock check utility (no full monitoring loop)
- Standalone script: `python walmart/stock_check.py`

### Standalone Utilities

**clearPort.py** — Kills processes on ports 5000-5003 (cross-platform)

**relogin.py** — Manual Target session login & cookie export to target.json

**walmart_relogin.py** — Manual Walmart session login & cookie export

## Frontend

### Dashboard Templates (dashboard/templates/)

**unified_dashboard.html** — Current AIO dashboard (Target + Walmart tabs)
- Dark theme CSS variables, embedded JS
- Per-tab: stock list, purchase state tracker, activity log, test mode toggle
- SSE connection via `connectSSE()`, real-time UI updates, timer countdown

No separate CSS or JS files — all styling/logic embedded in HTML templates.

## Retailer Directories

| Retailer | Directory | Automation | Anti-Bot |
|----------|-----------|-----------|---------|
| Target | `src/` | zendriver (nodriver) | F5/Shape Security |
| Walmart | `walmart/` | patchright (Playwright) | Akamai + press-hold CAPTCHA |

## Config & Data Files

| File | Purpose |
|------|---------|
| `config/product_config.json` | Target product monitoring list (TCIN, name, status) |
| `config/product_catalog.json` | Extended product metadata with priority levels |
| `walmart/walmart_config.json` | Walmart product list (IDs, enabled flags, retry counts) |
| `target.json` | Target session cookies (~27KB, captured via CDP) |
| `walmart-profile/cookies.json` | Walmart session cookies |
| `config/proxyIps.json` | Proxy server list `{ "proxies": [{ "ip", "port" }] }` |
| `config/pokemon_price_cache.json` | Pokemon TCG price cache (~57KB, 24hr TTL) |
| `logs/error_log.txt` | Runtime error log |
| `logs/purchase_states.json` | Purchase attempt history |
| `logs/purchases/` | Per-purchase execution logs |

## State Machine

```
in_stock → queued → attempting → success
                              ↘ failure
```
- Stuck "queued" (>5s) auto-resets
- Stuck "attempting" (>60s) auto-resets
- Only ONE item can be "attempting" at a time (hard constraint)

## Known Issues

### Security
- `src/session/purchase_executor.py`: `CARD_CVV` hardcoded — move to `.env` as `TARGET_CVV` (off-limits file, pending owner action)
- `walmart/stock_check.py`: ~~`COOKIES_RAW` hardcoded~~ FIXED (2026-04-08) — cookies now loaded from `walmart-profile/cookies.json` at runtime. Note: file uses raw `requests` (Python TLS fingerprint) which Akamai hard-blocks; this is a dev-only diagnostic tool, not suitable for live stock checks

### Naming Inconsistencies
- Target logic lives in `src/` while Walmart logic lives in `walmart/` — asymmetric naming

### Unused Parameters
- `walmart/stock_monitor.py`: `proxy_manager=None` and `page=None` accepted but unused (kept for call-site compatibility)

### Deprecated Templates
- `simple_dashboard.html` and `simple_dashboard_v2.html` are superseded by `unified_dashboard.html`

## Retailers Onboarded
- [x] Target — `src/` + root apps (RedSky API monitoring, zendriver/nodriver purchasing) — see `docs/RETAILERS/target.md`
- [x] Walmart — `walmart/` (browser-fetch monitoring, patchright purchasing, self-healing agent) — see `docs/RETAILERS/walmart.md`
