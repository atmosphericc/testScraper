# Retail Bot

## Stack
- Backend (current): `app.py` — Target prod via `USE_RESILIENT_STACK=1`
- Backend (test): `test_app.py` (stops before the real purchase)
- Stock-check pipeline: `src/monitoring/{stock_check_resilient,tab_dispatcher,proxy_preflight,stock_monitor}.py`, `src/session/multi_session_pool.py`, `src/proxy/{local_forwarder,proxy_state}.py`
- Browser: zendriver/nodriver — `from zendriver import cdp`. N persistent Chromes pinned 1:1 to Bright Data ISP IPs
- Frontend: Jinja2 — `dashboard/templates/simple_dashboard_v2.html` (`/v2`, current), `simple_dashboard.html` (`/`, older)
- Anti-bot focus: **Target / F5 Shape + HUMAN/PerimeterX** — JA3/JA4 via browser-native dispatch (`tab.evaluate(fetch(...))`). No curl_cffi in the request path. Never call Target's vendor Akamai.
- **Walmart was removed 2026-09-21** (`walmart/`, `src/stack/`, `unified_app.py`, 13 tests, 6 docs). Target is the only retailer. The code is recoverable from git history; the cross-retailer anti-bot findings stay in `docs/ANTIBOT_ARCHIVE.md` / `docs/FAILURES_ARCHIVE.md`.

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
├── test_app.py                  # Test env
├── run_bot_with_nightly_restart.bat  # production wrapper (arms all flags)
├── test_resilient_stack.py      # Round 2 smoke test
├── src/
│   ├── monitoring/              # stock_check_resilient, tab_dispatcher, proxy_preflight, stock_monitor (legacy)
│   ├── session/                 # multi_session_pool, session_manager, purchase_executor (Target), cookie_harvester (deprecated)
│   ├── purchasing/              # bulletproof_purchase_manager (Target checkout)
│   └── proxy/                   # local_forwarder, proxy_state
├── tools/analysis/              # readout + forensics scripts
├── config/                      # proxyIps.json, product_config.json
├── state/                       # proxy_state.json, session_profiles/, smoke_profiles/
├── dashboard/templates/         # simple_dashboard_v2.html (/v2), simple_dashboard.html (/)
└── docs/
    ├── RESILIENT_STACK.md
    ├── RESILIENT_STACK_OPERATIONAL_NOTES.md
    ├── ANTIBOT.md / ANTIBOT_ARCHIVE.md
    ├── FAILURES.md / FAILURES_ARCHIVE.md
    ├── FLOW.md
    ├── CODEBASE.md
    └── RETAILERS/ (target.md, TARGET_CHECKOUT_API.md)
```

## Task Routes

| Task | Start | Then |
|------|-------|------|
| Stock check (current) | docs/RESILIENT_STACK.md | src/monitoring/stock_check_resilient.py, tab_dispatcher.py |
| Multi-session pool / proxy | docs/RESILIENT_STACK.md | src/session/multi_session_pool.py, src/proxy/{local_forwarder,proxy_state}.py |
| Target purchase flow | docs/FLOW.md → docs/RETAILERS/target.md | src/session/purchase_executor.py, src/purchasing/bulletproof_purchase_manager.py |
| Target anti-bot | docs/ANTIBOT.md | src/session/purchase_executor.py (Shape headers, warmup POST) |
| Frontend | dashboard/templates/simple_dashboard_v2.html | app.py |
| Failures | docs/FAILURES.md (or FAILURES_ARCHIVE.md for history) | specific file |

## Context Rules
- **Resilient stack / Target work:** Read `src/monitoring/`, `src/session/multi_session_pool.py`, `src/proxy/`.
- **Never load:** entire repo; use specific paths above.

## Sub-Agents (when applicable)
- @antibot-analyst → docs/ANTIBOT.md, src/session/purchase_executor.py
- @purchase-flow-engineer → docs/FLOW.md, src/session/purchase_executor.py, src/purchasing/bulletproof_purchase_manager.py
- @failure-forensics → docs/FAILURES.md → specific file
- @retailer-researcher → docs/RETAILERS/

## Forbidden
- /__pycache__, /.git, /venv, /node_modules, /dist, /.pytest_cache

## File Access
- No source files are off-limits. `src/session/purchase_executor.py` is editable (overrides prior "DO NOT modify" guidance from the AIO refactor memory).

## Test Before Deploy
- **Offline suite is the gate:** `python tests/run_offline_suite.py` (26 files, ~3.5 min). Never blanket-run `tests/` — it holds live tests that launch browsers and can fire real purchases.
- Target resilient stack: `RESILIENT_TEST_DURATION_S=120 RESILIENT_TEST_NUM_IPS=1 python test_resilient_stack.py` (fast smoke) or `python test_resilient_stack.py` (default 30 min).
