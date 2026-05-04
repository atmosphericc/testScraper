# Retail Bot

## Stack
- Python backend: unified_app.py (prod), app.py (Target-only), test_app.py (stress/sim)
- Frontend: Jinja2 (dashboard/templates/unified_dashboard.html)
- Automation: zendriver/nodriver (Target AND Walmart) — note: docs previously said "patchright (Walmart)" but the codebase migrated to zendriver; imports in `walmart/session_manager.py` and `walmart/purchase_executor.py` use `from zendriver import cdp`
- Anti-bot: F5/Shape (Target), Akamai + PerimeterX/HUMAN (Walmart)

## Docs (read before working on relevant area)
- @docs/CODEBASE.md — file map and architecture
- @docs/FLOW.md — purchase flow and status states
- @docs/ANTIBOT.md — detections and mitigations
- @docs/FAILURES.md — failure history and fixes
- @docs/RETAILERS/ — per-retailer profiles

## Sub-Agents
- @antibot-analyst — audits code for detectability
- @purchase-flow-engineer — flow reliability, antibot patches
- @backend-engineer — Python quality, API structure
- @frontend-engineer — Jinja2/CSS across retailer tabs
- @code-quality — refactoring, dead code, test/prod consistency
- @failure-forensics — diagnoses failures, outputs patches
- @retailer-researcher — new retailer flows and anti-bot stacks