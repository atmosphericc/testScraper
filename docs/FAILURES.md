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

### [2026-07-07] - ATC 401 _ERR_AUTH_DENIED across all accounts (stale write token) - Target
**Symptom**: Three restock windows (02:03–02:18, 03:17–03:27, 03:48–04:16) detected
perfectly and raced by all 3 accounts, but nearly every ATC POST returned
`401 T83072242 _ERR_AUTH_DENIED`. Sentinel reported `logged_in=True` every 5 min
through the entire failure. The one worker whose auth was live (fresh token minted by
a 03:22 browser restart) got straight through to the known demand-throttle walls
(ATC `DCO_RATE_LIMITED`, checkout 429 `RESERVATION_FAILURE` → `checkout_busy_retryable`
re-race worked as designed). Net effect: the 3-account race silently degraded to ~1
account — the exact single-account wall multi-account was built to break.
Secondary: at 08:22 `business`'s session died for real; the sentinel's last-resort
`full_signout → credential login` looped every 5 min with Shape blocking the password
submit, leaving the account signed out with a **guest** accessToken.
**Root Cause**: carts.target.com WRITES authenticate via the `accessToken` cookie
(member JWT, ~4h TTL, `eid` claim only on member tokens). Every bot request is a raw
page-context `fetch()` that bypasses Target's SPA HTTP client — the layer that owns
refresh-token-on-401 — so nothing ever refreshed the token. The sentinel's `/account`
DOM check renders fine from `login-session` alone (blind to a dead/guest/expired write
token), and the ATC 401 recovery only refreshed Shape headers. Browser restarts
re-injected the same dead token from disk.
**Fix Applied**: token-first stack (2026-07-07, `session_manager.py` +
`purchase_executor.py`):
1. `get_access_token_status` / `refresh_access_token` / `ensure_fresh_access_token` —
   decode the live accessToken JWT (member/guest/ttl) and re-mint via the site's own
   `token_refresh` endpoint (rung 1, ~0.5s, no nav), else delete accessToken/idToken
   cookies + full page load so the edge must mint fresh (rung 2, endpoint-agnostic;
   never touches login-session/refreshToken).
2. Sentinel rung 0 = token check+repair every 300s tick (keeps tokens hot 24/7);
   DOM nav is now the fallback, and its verdict requires a member token to pass.
3. ATC 401 path repairs the token (force, endpoint rung) before the Shape re-warm;
   second consecutive 401 unlocks the nav rung. Cart-clear DELETE 401 repairs too.
4. Warmup dummy POST (fires every 60–90s anyway) now reports its status as a
   write-auth heartbeat: 401 → `WRITE-AUTH DEAD` log + background self-repair
   (rate-gated 300s, skipped mid-purchase).
5. Destructive credential relogin capped: 2 tries/6h, 20-min cooldown, one
   `[AUTH_CRITICAL]` alert to `logs/error_log.txt`, cookies left alone when capped
   (wrapper/nightly `relogin_one.py all` is the real fixer).
Knobs: `TARGET_TOKEN_KEEPFRESH=0` (kill-switch → pre-07-07 behavior),
`TARGET_TOKEN_MIN_TTL_S` (default 1800), `TARGET_TOKEN_REFRESH_URL`,
`TARGET_RELOGIN_MAX_PER_6H` (default 2), `TARGET_RELOGIN_COOLDOWN_S` (default 1200).
**Confidence**: high on diagnosis (JWT decode of saved sessions: `business` held a
guest token minted at 08:27:56 = the signout loop; `primary`/`alt-1` tokens carried
4h TTLs that expired mid-window; sentinel passed throughout). Medium-high on rung 1
(gsp endpoint from training knowledge — rung 2 is endpoint-agnostic and covers it;
first night's `[TOKEN]` log lines will show which rung the live site honors).
**Outcome**: FIX VALIDATED LIVE (2026-07-07 PM, through the real BD-proxy app.py path):
- `[WARMUP] dummy POST status 424 — write-auth alive` and `WRITE-AUTH DEAD — dummy POST
  returned 401` both fire correctly (heartbeat works).
- `[TOKEN] primary: fresh member token minted via cookie-delete + /account reload` — the
  exact repair that did NOT happen at 03:17 this morning now fires through the proxy. ✅
- primary + business steady-state `[SENTINEL] logged_in=True`; boot → `monitoring active`.
- Hardening from the live run: deleting `idToken` alongside `accessToken` made the reload
  re-mint a GUEST token — now delete ONLY `accessToken`; poll the jar for the async SPA
  mint (≤8s) instead of a fixed sleep; nav to auth-gated /account.

**Follow-up gap found (NOT yet fixed) — mid-run guest-downgrade can't self-heal on the
proxy path**: an account whose login-session has aged/degraded re-mints a GUEST token
(member=False) instead of a member one; `_is_fresh_member` correctly rejects it, the
sentinel escalates nav→restart→credential-relogin, but the in-app credential relogin runs
through the worker's **BD proxy** and Shape-blocks (`[RELOGIN] … failed`), so the account
stays guest. Observed on alt-1 (validate-first re-harvested 2.5h earlier); primary+business
(fresh `--force` full logins ~20min earlier) minted member fine. Distinguisher looks like
**full fresh login vs stale re-harvest**, not IP. Mitigation in place for tonight: all 3
accounts `--force` full-relogged-in so they START from robust member sessions; keep-fresh
maintains them. Real fixes to schedule: (1) route the sentinel's rung-3 credential relogin
through the HOME IP (RELOGIN_SKIP_PROXY) like the nightly wrapper's proven flow, so a
degraded account self-heals mid-run; (2) investigate why a re-harvested login-session
re-mints guest while a full-login one re-mints member (session-robustness / TTL of member
mint capability). Until (1) lands, the guarantee rests on fresh full logins at boot — which
the nightly wrapper's `relogin_one.py all` provides (dead/guest accounts get a full login;
member accounts get re-harvested).
