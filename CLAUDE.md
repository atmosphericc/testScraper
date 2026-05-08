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

## Test Before Deploy
- `python test_app.py` must pass (both retailers)
- Use this before touching unified_app.py