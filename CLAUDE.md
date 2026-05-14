# Retail Bot

## Stack
- Backend (current): `app.py` — Target prod via `USE_RESILIENT_STACK=1`
- Backend (legacy): `unified_app.py` (Target+Walmart dashboard), `test_app.py` (test env)
- Stock-check pipeline: `src/monitoring/{stock_check_resilient,tab_dispatcher,proxy_preflight,stock_monitor}.py`, `src/session/multi_session_pool.py`, `src/proxy/{local_forwarder,proxy_state}.py`
- Browser: zendriver/nodriver — `from zendriver import cdp`. N persistent Chromes pinned 1:1 to Bright Data ISP IPs
- Frontend: Jinja2 — `dashboard/templates/simple_dashboard_v2.html` (app.py), `unified_dashboard.html` (unified_app.py)
- Anti-bot focus: **Target / F5 Shape** — JA3/JA4 via browser-native dispatch (`tab.evaluate(fetch(...))`). No curl_cffi in the request path.
- Walmart code under `walmart/` (Akamai + PerimeterX + Cloudflare) is still in the repo but **not the active focus** post-pivot. See `docs/ANTIBOT_ARCHIVE.md` / `docs/FAILURES_ARCHIVE.md` for the 2026-05-11 Walmart audit history.

## Architecture — Resilient Stack (Round 2, validated 2026-05-13)
Browser-native dispatcher: N persistent Chromes (one per BD ISP IP) fire bulk RedSky via `tab.evaluate(fetch(...))`. **99.95% over 60 min @ 3 RPS / 3 IPs / 33 TCINs** (zero 403s, 5 self-healed timeouts).

- Architecture doc: `docs/RESILIENT_STACK.md`
- Operational findings: `docs/RESILIENT_STACK_OPERATIONAL_NOTES.md`
- Production: `USE_RESILIENT_STACK=1 python app.py` — rate via `RESILIENT_TARGET_RPS` (alias `TARGET_SWEEPS_PER_SEC`)
- Smoke test: `python test_resilient_stack.py` — knobs: `RESILIENT_TEST_DURATION_S`, `RESILIENT_TEST_NUM_IPS`, `RESILIENT_TEST_RPS`, `RESILIENT_TEST_BEHAVIORAL`, `CHROME_STAGGER_TOTAL_S`
- Fast smoke: `RESILIENT_TEST_DURATION_S=120 RESILIENT_TEST_NUM_IPS=1 python test_resilient_stack.py`
- TCIN chunking is dynamic — chunked at 28 client-side (Target hard-caps `product_summary_with_fulfillment_v1` at 30/req). Per-TCIN refresh = `RPS / ceil(N/28)`.
- Behavioral mixin default OFF — `behavioral_mix_ratio=0.10` triggered a 7×403 burst at ~4 min in. Re-enable ≤0.02 only.
- Legacy `src/session/cookie_harvester.py` is deprecated (Round 1 curl_cffi path). Each session in MultiSessionPool now owns its own live cookies.

## Repo Layout
```
/
├── app.py                       # Target prod entry (USE_RESILIENT_STACK=1)
├── unified_app.py               # Legacy unified Target+Walmart dashboard
├── test_app.py                  # Test env
├── test_resilient_stack.py      # Round 2 smoke test
├── src/
│   ├── monitoring/              # stock_check_resilient, tab_dispatcher, proxy_preflight, stock_monitor (legacy)
│   ├── session/                 # multi_session_pool, session_manager, purchase_executor (Target), cookie_harvester (deprecated)
│   ├── purchasing/              # bulletproof_purchase_manager (Target checkout)
│   └── proxy/                   # local_forwarder, proxy_state
├── walmart/                     # Walmart module (in repo, not active focus)
├── config/                      # proxyIps.json, product_config.json
├── state/                       # proxy_state.json, session_profiles/, smoke_profiles/
├── dashboard/templates/         # simple_dashboard_v2.html (app.py), unified_dashboard.html (unified_app.py)
└── docs/
    ├── RESILIENT_STACK.md
    ├── RESILIENT_STACK_OPERATIONAL_NOTES.md
    ├── ANTIBOT.md / ANTIBOT_ARCHIVE.md
    ├── FAILURES.md / FAILURES_ARCHIVE.md
    ├── FLOW.md
    ├── CODEBASE.md
    └── RETAILERS/ (target.md, walmart.md, TARGET_CHECKOUT_API.md, WALMART_CHECKOUT_API.md)
```

## Task Routes

| Task | Start | Then |
|------|-------|------|
| Stock check (current) | docs/RESILIENT_STACK.md | src/monitoring/stock_check_resilient.py, tab_dispatcher.py |
| Multi-session pool / proxy | docs/RESILIENT_STACK.md | src/session/multi_session_pool.py, src/proxy/{local_forwarder,proxy_state}.py |
| Target purchase flow | docs/FLOW.md → docs/RETAILERS/target.md | src/session/purchase_executor.py, src/purchasing/bulletproof_purchase_manager.py |
| Target anti-bot | docs/ANTIBOT.md | src/session/purchase_executor.py (Shape headers, warmup POST) |
| Walmart flow (legacy) | docs/RETAILERS/walmart.md | walmart/purchase_executor.py |
| Frontend (Target) | dashboard/templates/simple_dashboard_v2.html | app.py |
| Frontend (unified) | dashboard/templates/unified_dashboard.html | unified_app.py |
| Failures | docs/FAILURES.md (or FAILURES_ARCHIVE.md for history) | specific file |

## Context Rules
- **Resilient stack / Target work:** Read `src/monitoring/`, `src/session/multi_session_pool.py`, `src/proxy/`. Skip `walmart/`.
- **Walmart legacy work:** Read `walmart/`. Skip `src/monitoring/stock_check_resilient.py`.
- **Never load:** entire repo; use specific paths above.

## Sub-Agents (when applicable)
- @antibot-analyst → docs/ANTIBOT.md, src/session/purchase_executor.py (Target) or walmart/* (legacy)
- @purchase-flow-engineer → docs/FLOW.md, src/session/purchase_executor.py, src/purchasing/bulletproof_purchase_manager.py
- @failure-forensics → docs/FAILURES.md → specific file
- @retailer-researcher → docs/RETAILERS/

## Forbidden
- /__pycache__, /.git, /venv, /node_modules, /dist, /.pytest_cache

## File Access
- No source files are off-limits. `src/session/purchase_executor.py` is editable (overrides prior "DO NOT modify" guidance from the AIO refactor memory).

## Test Before Deploy
- Target resilient stack: `RESILIENT_TEST_DURATION_S=120 RESILIENT_TEST_NUM_IPS=1 python test_resilient_stack.py` (fast smoke) or `python test_resilient_stack.py` (default 30 min).
- Full app: `python test_app.py` before touching `unified_app.py`.
