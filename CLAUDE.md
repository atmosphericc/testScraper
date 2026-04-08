# Retail Bot - Project Overview

## What This Is
Automated competitive purchaser for Target.com and Walmart.com.
Monitors stock, detects in-stock items, and executes purchase flows.

## Tech Stack
- **Backend**: Python (unified_app.py = prod AIO, app.py = Target-only, test_app.py = stress test mode)
- **Frontend**: Jinja2 unified dashboard with tabs per retailer (dashboard/templates/unified_dashboard.html)
- **Automation**: zendriver/nodriver (Target), patchright/Playwright (Walmart)
- **Anti-bot targets**: F5/Shape Security (Target), Akamai + PerimeterX/HUMAN (Walmart)

## Two Modes
- **test_app.py**: Simulates purchase, clears cart, loops endlessly
- **app.py**: Completes real purchase, waits on confirmation page

## Purchase Flow
1. Stock monitor detects in-stock item
2. Priority queue picks highest-priority item (top-down)
3. Single "attempting" lock — only ONE item at a time
4. Browser navigates → add to cart → checkout → success/failure
5. Next item in queue picks up

## Status States
in_stock → queued → attempting → success | failure

## Key Constraints
- Single-threaded purchasing (one attempting at a time)
- Browser locked during attempting
- Top-listed items always take priority

## Shared Docs (read before working)
- @docs/CODEBASE.md — file map and architecture
- @docs/ANTIBOT.md — known detections and mitigations
- @docs/FLOW.md — current purchase flow state
- @docs/FAILURES.md — failure history and fixes applied
- @docs/RETAILERS/ — per-retailer profiles

## Sub-Agents Available
@antibot-analyst — audits automation code for detectability
@purchase-flow-engineer — fixes flow reliability and patches antibot findings
@backend-engineer — Python quality, standardization, API structure
@frontend-engineer — Jinja2/CSS uniformity across retailer tabs
@code-quality — refactoring, dead code, test/prod consistency
@failure-forensics — diagnoses failures from logs and outputs patches
@retailer-researcher — researches new retailer checkout flows and anti-bot stacks
