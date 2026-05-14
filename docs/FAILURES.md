# Failure Log

> **Pivot note (2026-05-14):** post-pivot to the Round 2 resilient stack, the prior
> Walmart audit log (2026-05-11 PM-PM8) and pre-pivot Target entries have been
> moved to `docs/FAILURES_ARCHIVE.md`. This file is a clean slate for failures
> against the current architecture — see `docs/RESILIENT_STACK.md`.

## Archive Policy
Move entries older than 30 days where Outcome is confirmed resolved to
`docs/FAILURES_ARCHIVE.md`. Keep only:
- unresolved issues
- recent fixes (< 30 days)
- failures with "Root Fix Still Needed" notes

## Open Actions
_(none — last open action closed 2026-05-06: executor now returns `order_id` + `confirmation_url`
at `src/session/purchase_executor.py:1217-1239`; manager consumes them at
`src/purchasing/bulletproof_purchase_manager.py:1197-1204`.)_

---

## Format
### [DATE] - Failure Type - Retailer
**Symptom**:
**Root Cause**:
**Fix Applied**:
**Confidence**: high/medium/low
**Outcome**:

---

## Entries

_(no active entries — see `docs/FAILURES_ARCHIVE.md` for history)_
