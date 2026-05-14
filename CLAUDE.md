# Retail Bot

## Stack
- Backend: unified_app.py (prod), app.py (Target-only legacy), test_app.py (testing)
- Frontend: Jinja2 (dashboard/templates/unified_dashboard.html)
- Browser: zendriver/nodriver (both retailers; `from zendriver import cdp`)
- Anti-bot: Target (F5/Shape), Walmart (Akamai + PerimeterX/HUMAN)

## Structure
```
/
├── unified_app.py              # Main entry point
├── app.py                      # Legacy (Target-only)
├── test_app.py                 # Test environment
├── dashboard/templates/unified_dashboard.html  # Jinja2 UI
├── target/                     # Target module (session_manager, purchase_executor, antibot_handler, selectors, constants)
├── walmart/                    # Walmart module (same structure as target/)
├── core/                       # Shared (payment, auth, http_client, logging, exceptions)
└── docs/
    ├── CODEBASE.md
    ├── FLOW.md
    ├── ANTIBOT.md
    ├── FAILURES.md
    └── RETAILERS/ (TARGET.md, WALMART.md)
```

## Task Routes

| Task | Start | Then | Skip |
|------|-------|------|------|
| Target flow | @docs/FLOW.md | target/purchase_executor.py | walmart/ |
| Walmart flow | @docs/FLOW.md | walmart/purchase_executor.py | target/ |
| Target anti-bot | @docs/ANTIBOT.md | target/antibot_handler.py | walmart/ |
| Walmart anti-bot | @docs/ANTIBOT.md | walmart/antibot_handler.py | target/ |
| Shared code | core/ | test_app.py | retailer-specific |
| Frontend | unified_dashboard.html | unified_app.py | — |
| Failures | @docs/FAILURES.md | specific file | — |

## Context Rules
- **Target work:** Read target/, skip walmart/
- **Walmart work:** Read walmart/, skip target/
- **Core changes:** Test both retailers with test_app.py
- **Never load:** Entire repo; use specific paths above

## Sub-Agents
- @antibot-analyst → antibot_handler.py (both), @docs/ANTIBOT.md
- @purchase-flow-engineer → unified_app.py, purchase_executor.py (both), @docs/FLOW.md
- @backend-engineer → unified_app.py, core/
- @frontend-engineer → unified_dashboard.html, unified_app.py
- @code-quality → app.py vs unified_app.py, refactor
- @failure-forensics → @docs/FAILURES.md → specific file
- @retailer-researcher → @docs/RETAILERS/

## Forbidden
- /__pycache__, /.git, /venv, /node_modules, /dist, /.pytest_cache

## File Access
- No source files are off-limits for now. `src/session/purchase_executor.py` is editable (overrides prior "DO NOT modify" guidance from the AIO refactor memory).

## Resilient Stock Stack (Round 2 — 2026-05-13)
- Browser-native dispatcher: N persistent Chromes (one per BD ISP IP) fire bulk RedSky via `tab.evaluate(fetch(...))`. No curl_cffi in request path. Stress-validated 99.95% over 60 min @ 3 RPS / 3 IPs / 33 TCINs (zero 403s, 5 self-healed timeouts).
- Code: `src/monitoring/{stock_check_resilient,tab_dispatcher}.py`, `src/session/multi_session_pool.py`, `src/proxy/{local_forwarder,proxy_state}.py`. Architecture doc: `docs/RESILIENT_STACK.md`. Operational findings: `docs/RESILIENT_STACK_OPERATIONAL_NOTES.md`.
- Smoke test: `python test_resilient_stack.py` — env knobs `RESILIENT_TEST_DURATION_S`, `RESILIENT_TEST_NUM_IPS`, `RESILIENT_TEST_RPS`, `RESILIENT_TEST_BEHAVIORAL`, `CHROME_STAGGER_TOTAL_S`.
- Production: `USE_RESILIENT_STACK=1 python app.py`. Adjust rate via `RESILIENT_TARGET_RPS` (alias: `TARGET_SWEEPS_PER_SEC`).
- TCIN handling is dynamic — chunked at 28 (Target hard-caps `product_summary_with_fulfillment_v1` at 30/req). Any TCIN count works; per-TCIN refresh = `RPS / ceil(N/28)`.
- Behavioral mixin defaults OFF: `behavioral_mix_ratio=0.10` triggered a 7×403 burst on one session at ~4 min in. Re-enable ≤0.02 only.
- Legacy `src/session/cookie_harvester.py` is deprecated (Round 1 curl_cffi path). Each session in MultiSessionPool now owns its own live cookies.

## Test Before Deploy
- `python test_app.py` must pass (both retailers)
- Use this before touching unified_app.py
- For resilient stack changes: `python test_resilient_stack.py` (default 30 min) or short `RESILIENT_TEST_DURATION_S=120 RESILIENT_TEST_NUM_IPS=1 python test_resilient_stack.py` for a fast smoke.