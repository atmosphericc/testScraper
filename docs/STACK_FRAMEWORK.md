# Resilient Stack Framework (`src/stack/`)

## Status
**Phase 1b complete 2026-05-16.** Framework + Walmart adapter implemented
and wired end-to-end. Dispatcher + ResilientChecker bodies done. Bootstrap
+ entry point + smoke test in place. Verified clean imports, factory
constructs without network. Next: run actual smoke tests on the laptop
(N=2, 15 min) then prod (N=16, 60 min).

## Purpose
Retailer-agnostic resilient stock-monitoring infrastructure. Target's
proven 99.95%-over-60-min pattern (validated 2026-05-13, see
`docs/RESILIENT_STACK.md`) generalized so any retailer can plug in with
a ~200 line adapter.

## Layout

```
src/stack/
├── __init__.py
├── retailer_adapter.py        # RetailerAdapter Protocol + ItemStatus + FetchResult
├── dispatcher.py              # Dispatcher (stub) — pick session, run fetch JS, return result
├── resilient_checker.py       # ResilientChecker (stub) — sweep loop + background loops
├── multi_session_pool.py      # Copy of src/session/multi_session_pool.py
├── local_forwarder.py         # Copy of src/proxy/local_forwarder.py
└── proxy_state.py             # Copy of src/proxy/proxy_state.py

walmart/
└── walmart_adapter.py         # WalmartAdapter implementing the Protocol

src/{session,proxy,monitoring}/  # Target's legacy implementation, UNTOUCHED
```

## Why duplicated infrastructure (`multi_session_pool.py` etc.)
Constraint from user: "do not touch any target bot code related". So
Target's working `src/session/multi_session_pool.py`,
`src/proxy/local_forwarder.py`, `src/proxy/proxy_state.py` stay verbatim,
and the framework gets its own copies in `src/stack/` to evolve from.

Initial duplication is 3 files. When Walmart's needs diverge (per-session
personalities, faster heartbeat for `_px3`, etc.), changes land only in
the `src/stack/` copies. Target stays on its working code.

When Target eventually migrates onto this framework, the three duplicate
files at `src/stack/` become the canonical version and Target's originals
get deleted. That's a future, separately-scoped migration.

## The Adapter Protocol

A retailer adapter is a class satisfying the `RetailerAdapter` Protocol
(`src/stack/retailer_adapter.py`). The framework imports a singleton
instance and never modifies it.

Surface:

**Identity**:
- `name: str` — used for state file naming (`state/<name>_proxy_state.json`)
- `base_url: str`

**Session bootstrap**:
- `needs_login: bool`
- `warmup_urls: list[str]`
- `cookie_freshness_keys: list[str]` + `cookie_max_age_seconds: dict[str, int]`

**Load profile**:
- `chunk_size: int` — items per fetch (Target=28, Walmart=1)
- `per_ip_rps_ceiling: float` — framework validates aggregate RPS / N stays under this
- `chrome_stagger_seconds: int` — initial launch spread
- `session_heartbeat_seconds: int` — between cookie-refresh interactions

**Fetch construction & parsing**:
- `build_fetch_js(items) -> str` — JS the dispatcher runs via tab.evaluate
- `parse_response(result, items) -> list[ItemStatus]`
- `is_blocked_response(result) -> bool`
- `preflight_probe_url() -> str`, `is_preflight_clean(status, body) -> bool`

**Checkout (Phase 5, optional)**:
- `build_atc_js(item_id, qty, cart_context) -> str`
- `build_place_order_js(order_context) -> str`
- `apq_full_query(operation_name) -> Optional[str]`

## Data contracts

`ItemStatus` (output of parse, input to on_in_stock callback):
```python
@dataclass
class ItemStatus:
    item_id: str
    in_stock: bool
    title: Optional[str] = None
    price: Optional[float] = None
    availability_status: Optional[str] = None
    last_checked_at: float = 0.0
```

`FetchResult` (output of dispatcher's tab.evaluate, input to parse):
```python
@dataclass
class FetchResult:
    http_status: int
    body_json: Optional[dict] = None
    body_text: Optional[str] = None
    elapsed_ms: float = 0.0
    error: Optional[str] = None
```

The fetch JS the adapter constructs must return:
```js
{ __http_status: number, __body_json: object|null, __body_text: string|null }
```

The framework wraps this into `FetchResult` automatically.

## Walmart Adapter — Key Design Decisions

### Transport: HTML scrape, NOT GraphQL
**Decision: 2026-05-16.** Researched what top commercial Walmart bots
(Refract, Stellar, MEKAIO, Cybersole, etc.) actually use. Concrete evidence
points to HTML page scrape + `__NEXT_DATA__` extraction. None of the named
bots have public evidence of hitting `/orchestra/pdp/graphql/` directly
for monitoring.

Reasons everyone (us, top bots, scraping vendors) converged on HTML:
1. GraphQL persisted-query hash rotates weekly; APQ fallback infra is heavy
2. The HTML page IS the SSR'd GraphQL response — same data either way
3. HTML page-loads look more like real users than cold GraphQL POSTs
4. `availabilityStatusV2.value` is the restock signal; available in both

Where this implementation exceeds public commercial bots: multi-session
Chrome pool with per-IP pinning. Most commercial bots run a single
monitor session (or a tls-client loop). N=16 Chromes each with real
JA3/JA4 + accumulated cookies is genuinely beyond what's documented.

Sources (researched 2026-05-16):
- PhoenixBot/sites/walmart.py (GitHub Strip3s)
- bird-bot/sites/walmart.py (GitHub natewong1313)
- Scrapfly 2026 Walmart guide
- Decodo 2026 Walmart guide
- Refract help docs (help.refractbot.com/modules/walmart)
- Stellar AIO guides (guides.stellaraio.com)

### Sizing for the resilient stack
- **N=16 active sessions** = full active proxy pool from `config/proxyIps.json`
  (matches what Target uses). Reserves stay as reserves for manual swap.
- **Dev override**: `WALMART_RESILIENT_NUM_CHROMES=2` for laptop testing
- **Aggregate RPS=6** in prod (= 0.375 RPS/IP, under 0.5 PerimeterX ceiling)
- **Dev override**: `WALMART_RESILIENT_RPS=1` for laptop
- **Chrome stagger=1200s** (PerimeterX is more sensitive to burst arrivals
  than Shape; Target uses 600s)
- **Heartbeat=900s** (15 min); `_px3` is 60s TTL but heartbeat is for
  monitoring sessions, not checkout — fresh `_px3` only needs to exist at
  the moment of dispatch, which heartbeat-induced interaction handles

### Per-retailer state file
`ResilientChecker.proxy_state_path = state_dir / f"{adapter.name}_proxy_state.json"`

So Walmart writes `state/walmart_proxy_state.json`, separate from Target's
`state/proxy_state.json`. A Walmart 403 won't burn an IP for Target's
RedSky and vice versa.

## Implementation phases

### Phase 1a — Framework scaffolding (DONE 2026-05-16)
- `src/stack/` directory + Protocol + stubs + duplicate infra files
- `walmart/walmart_adapter.py` implementing the Protocol
- Verified clean imports both sides (framework loads, Target legacy still works)

### Phase 1b — Bodies + bootstrap + entry point (DONE 2026-05-16)
Implemented:
- `src/stack/dispatcher.py:Dispatcher` — picks session via `pool.pick_session()`,
  acquires `busy_lock`, runs `adapter.build_fetch_js()` inside the tab, wraps
  the JS return value as `FetchResult`. Chunk rotation built in.
- `src/stack/resilient_checker.py:ResilientChecker` — sweep loop (jittered
  cadence), `_dispatch_one` task model, `_record_status` → ProxyState,
  `_ingest_response` → adapter.parse_response → on_in_stock callback,
  background loops (parked_retest, stats heartbeat). Per-retailer state
  file at `state/{adapter.name}_proxy_state.json`. Per-retailer profile
  root at `state/{adapter.name}_session_profiles/`. Sets
  `CHROME_STAGGER_TOTAL_S` env from `adapter.chrome_stagger_seconds`
  before pool launch (safe since Target/Walmart never run concurrently).
- `walmart/walmart_session_bootstrap.py` — one-time login script,
  per-session manual auth via zendriver + local CONNECT forwarder. Each
  of N sessions gets a distinct profile, fingerprint, and Akamai history.
  CLI flags: `--session sN`, `--first N`, `--base-port`, `--skip-warmup`.
- `walmart/walmart_stock_resilient.py` — entry point. `build_walmart_checker()`
  factory for programmatic use; `python -m walmart.walmart_stock_resilient`
  CLI for smoke runs. Env vars: `WALMART_RESILIENT_NUM_CHROMES`,
  `WALMART_RESILIENT_RPS`, `WALMART_RESILIENT_FIRST_PORT`.
- `test_walmart_resilient.py` — soak test mirroring `test_resilient_stack.py`.
  Env: `WALMART_TEST_DURATION_S`, `WALMART_TEST_NUM_IPS`, `WALMART_TEST_RPS`,
  `WALMART_TEST_ITEM`.

Verified: framework + Walmart adapter import clean; `build_walmart_checker`
factory constructs without network; Target's legacy imports still intact.

### Phase 1c — Validation soak (NEXT)
Run actual stack on live Walmart. Two gates:
- **Dev smoke (laptop)**: bootstrap 2 sessions, then
  `WALMART_TEST_DURATION_S=900 WALMART_TEST_NUM_IPS=2 WALMART_TEST_RPS=1 \
      python test_walmart_resilient.py` — 15 min, ≥98% 200s, zero IP burns
- **Prod soak (64 GB box)**: bootstrap all 16 sessions, then
  `WALMART_TEST_DURATION_S=3600 WALMART_TEST_NUM_IPS=16 WALMART_TEST_RPS=6 \
      python test_walmart_resilient.py` — 60 min, ≥98% 200s, zero IP burns,
  `_px3` stays fresh on all 16 sessions through the run

If both pass, Phase 2 (env-gated cutover in `walmart_app.py`) is safe.

### Phase 2 — Env-gated cutover
`WALMART_USE_RESILIENT=1` switches `walmart_app.py` to the new path;
existing `WalmartStockMonitor` stays as fallback. Same pattern as
Target's `USE_RESILIENT_STACK`.

### Phase 3 — GraphQL APQ fallback (deferred)
Originally Phase 3 in the plan, but the 2026-05-16 transport research
revealed Walmart bots don't use GraphQL for monitoring. So APQ fallback
is now Phase 5 territory (only relevant for checkout, where GraphQL
mutations are the real path).

### Phase 4 — Per-session personalities + real-interaction heartbeat
Each session gets a persistent "personality" config in its profile dir.
Heartbeat upgrades from page reload to real interactions (scroll, hover,
mouse-move).

### Phase 5 — Hybrid checkout end-to-end
Wire `walmart/checkout_api.py` (already has hashes captured 2026-05-11)
through `WalmartAdapter.build_atc_js` / `build_place_order_js`. APQ
fallback lives here for the checkout-side hashes.

## Adding a new retailer (future)

The pattern is:

1. Create `<retailer>/<retailer>_adapter.py` implementing `RetailerAdapter`
2. Capture the retailer's stock-check fetch JS (open devtools, watch what
   their React client does on PDP load, mimic the fetch from inside a tab)
3. Capture their checkout mutations (`<retailer>/checkout_capture.py`-style)
4. Run `walmart/walmart_session_bootstrap.py`-equivalent to seed profiles
5. Add an entry point in the retailer's app: `ResilientChecker(adapter=<RetailerAdapter>(), ...)`

Estimated effort for retailer #3: ~1-2 days vs ~11 days for Walmart
because the framework absorbs all the infrastructure work.
