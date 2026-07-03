# Multi-Account Login & Session Harvesting

**Goal:** run N owned Target accounts so a hot drop yields N units instead of 1.
Each account carries its *own* `DCO_RATE_LIMITED` budget (cures the documented
single-account 429) and its *own* 1-per-account purchase limit.

---

## OPERATING MODEL (read this first) — how the whole thing works

**One command:** `run_bot_with_nightly_restart.bat`. It does, in order:
1. `python relogin_one.py all` — for every account in `config/target_accounts.json`:
   validate-first (if the saved session is still logged in, just refresh its
   cookies); otherwise completely sign out → re-login (username-first with the
   account's email+password, keep-me-signed-in, `form.requestSubmit()`, 3× retry)
   → **save** that account's session to its own file (`target.json`,
   `target-2.json`, …). PROVEN reliable (2/2 first-try).
2. `python app.py` — the bot. `BulletproofPurchaseManager` calls
   `WorkerPool.auto()`, which reads the SAME accounts file and launches **one
   purchase Chrome per account** (Worker 1 = legacy `target.json`/`nodriver-profile`;
   alts = `target-N.json`/`nodriver-profile-N`), each loading its saved session.

**What you provide (the ONLY credentials):** per account in `config/target_accounts.json`,
just **`username` (email) + `password`**. Everything else is automatic:
- **Cookies/session** — minted by the login, saved to `target-N.json`, kept warm
  by the bot (token auto-refresh every ~30s + the Session Sentinel). You never
  manage cookies by hand.
- **Device fingerprint** — derived deterministically from `account_id`
  (`account_identity.py`), applied at BOTH login and purchase so each account
  looks like a consistent, distinct device.
- **Exit IP (optional)** — set `proxy_url` per account (a Bright Data ISP URL) to
  give each account its own IP; the local forwarder handles BD auth. Empty =
  home IP. Login and purchase use the SAME IP (coherence).

**There is no single "main purchase Chrome" anymore.** The bot runs N isolated
browsers (one per account), each its own profile + session + fingerprint + IP.

**On a hot drop (the 429 cure):** `_start_real_purchase` fans the in-stock TCIN
out to ALL ready accounts **concurrently** (racing). Each account hits add-to-cart
on its own session/loop/IP, so each has its own `DCO_RATE_LIMITED` budget — N
accounts ≈ N× the ATC throughput before any one throttles, and N units instead of 1.
Each account tries **qty = min(2, limit)** and self-heals to qty=1 on a true
limit-1 SKU (`422/409 PURCHASE_LIMIT` retry). Knobs: `TARGET_QTY_CEILING` (2),
`TARGET_QTY_OPTIMISTIC` (1), `TARGET_RACE_ALL_WORKERS` (1).

**How this mirrors Refract/Stellar:** one "task" per account = its own
account + proxy IP + fingerprint, all racing the drop. Top bots scale by *account
count*, not qty depth — which is exactly this design.

**To add accounts (scale 1→X):** add an entry to `config/target_accounts.json`
(email+password, optional proxy_url). The login farm and the purchase fleet both
size off that file automatically — no code change.

**Honest limits:** the 429 is reduced, not eliminated — with N accounts + N IPs you
get N independent budgets, but a hot drop can still throttle. Per-account IPs
(`proxy_url`) matter for the IP-keyed part of the throttle; without them all
accounts share the home IP. Realistic ceiling on one machine ≈ 5 accounts before
shared-fingerprint/IP linkage and payment-fraud linkage dominate (distinct
cards+addresses per account remain essential).

---

**Decisions on record (2026-06-22):** near-term 3 accounts → scale toward 10+ as
more are made; distinct virtual card + jigged address per account; 2FA **off**
(so credential login is fully auto-fillable, modulo a one-time new-device
challenge at enrolment).

---

## Status

### Built & offline-validated (this branch, not yet committed)
| File | What |
|------|------|
| `src/session/account_identity.py` | **Per-account device fingerprint.** Deterministic from `account_id`; distinct UA build / platform / timezone / viewport / hardware / canvas-noise per account, applied via CDP `Emulation.*` before any Target nav. Fixes the shared-fingerprint cluster-linker (the existing fingerprint dict at `session_manager.py:95-138` was generated but never applied). |
| `harvest_accounts.py` | **Batch login + harvest/refresh.** `relogin.py` generalised to N accounts: own profile, own optional proxy, own fingerprint; 3-tier (silent refresh → credential auto-fill → manual challenge). |
| `config/target_accounts.example.json` | Template. Copy to `config/target_accounts.json` (gitignored) and fill creds. |
| `tests/test_harvest_accounts_smoke.py` | No-network smoke test (5/5 passing). |

Verified offline: `py_compile` clean; identity determinism + cross-account
distinctness; config routing + collision guards; session-health verdicts.
**Not yet run against Target** (do that present, to clear any challenge).

### Usage
```bash
# Safe anywhere — no browser/network:
python harvest_accounts.py --dry-run     # validate config + show fingerprints
python harvest_accounts.py --check       # report each session's health/age

# Run these yourself, present (drive real logins):
python harvest_accounts.py --manual                      # log in by hand, harvest all
python harvest_accounts.py --auto                        # auto-fill creds, harvest all
python harvest_accounts.py --manual --account alt-1      # one account
```
Then: `set TARGET_WORKER_POOL_SIZE=3` and launch `app.py`. The existing
`WorkerPool` already reads `target-2.json` / `nodriver-profile-2` etc.

The harvester is also the **re-login manager**: run `--auto` (or wire it into
`run_bot_with_nightly_restart.bat` before `app.py`) and it silently refreshes
live sessions and only re-logs-in dead ones — replacing the manual `relogin.py`
step before each drop.

---

## Done (this branch, offline-validated, not yet committed)

### ✅ File-driven fleet sizing
`WorkerPool.auto()` / `from_accounts_file()` size the live fleet from
`config/target_accounts.json` (one Worker per enabled account; falls back to
`TARGET_WORKER_POOL_SIZE` when the file is absent). `BulletproofPurchaseManager`
uses `auto()`. Test: `tests/test_worker_pool_accounts_smoke.py`.

### ✅ Auto re-login in the nightly wrapper
`run_bot_with_nightly_restart.bat` runs `harvest_accounts.py --auto` ONCE before
the restart loop (silent-refresh living sessions, credential-login dead ones).
Not per-relaunch — avoids a crash-loop login storm.

### ✅ 2. Racing dispatch (the 429 cure)
`_start_real_purchase` fans the drop out to **all** ready workers concurrently
(`WorkerPool.ready_workers()`), each pinned to one account → own 429 budget, own
units. Aggregated into one TCIN state (`units_bought`, breakdown, order numbers)
via `_record_race_result`. Activates only at >1 ready worker; `TARGET_RACE_ALL_WORKERS=0`
disables. Test: `tests/test_race_dispatch_smoke.py`.

### ✅ 1. Per-account proxy on the PURCHASE path
`WorkerConfig.proxy_url` flows from the accounts file → `SessionManager` →
`--proxy-server`. `BulletproofPurchaseManager._setup_purchase_forwarders()` runs a
`ForwarderPool` on its own loop/thread for BD-auth proxies (port band **23000+
worker_id**; stock uses 22000+), rewriting each worker's `proxy_url` to
`127.0.0.1:port` before `build_all()`. Plain host:port pass through; no proxy =
home IP. Torn down in `shutdown()`. Test: `tests/test_purchase_proxy_smoke.py`.

### ✅ Per-account IP COHERENCE (login-IP == purchase-IP)
`harvest_accounts._setup_harvest_forwarders()` runs the same `ForwarderPool`
(port band **24000+**) during harvest, so each account **logs in through its own
BD IP** — the same IP the purchase path exits. Closes the login-IP≠purchase-IP
mismatch that would otherwise flag sessions. Test: `tests/test_harvest_forwarder_smoke.py`.

### ✅ Purchase-tab fingerprint matches harvest
`SessionManager(account_id, timezone, apply_fingerprint)` re-applies the same
deterministic `account_identity` CDP fingerprint on the purchase tab (before any
Target nav and before the live-UA read), so the purchase browser presents the
identity the account was harvested under. Gated to file-driven (multi-account)
configs via `WorkerConfig.apply_fingerprint`; legacy single-account is untouched.

---

### ✅ qty=2 optimistic (grab 2/account)
`_decide_target_qty()`: when the bulk feed omits the per-customer limit (hint <=1)
target the ceiling (default 2) instead of clamping to 1; a genuinely-reported
limit is respected (min with ceiling). The executor self-heals a TRUE limit-1 via
its 422/409 PURCHASE_LIMIT retry (qty 2->1, keeps the unit). Knobs:
`TARGET_QTY_CEILING` (default 2), `TARGET_QTY_OPTIMISTIC=0` (disable),
`TARGET_FORCE_QTY_1=true` (hard 1). Executor success now returns `quantity` so the
racing aggregate sums real units. Test: `tests/test_qty_decision_smoke.py`.

### ✅ Session Sentinel (keep every account logged-in 100%)
The ~30s warmup already refreshes tokens (navigates target.com → Target renews the
24h accessToken from the months-long refreshToken → `save_session_state` persists).
The Sentinel adds the missing recovery + visibility: every ~5 min per worker
(`_maybe_run_session_sentinel`, skips mid-purchase), `SessionManager.ensure_logged_in()`
runs an escalation ladder — **nav-refresh → browser restart → credential re-login**
(`_credential_relogin` reuses the harvester's login with creds from the accounts
file). Per-account verdicts land in `get_account_health()`. Knobs:
`TARGET_SESSION_SENTINEL=0` (disable), `TARGET_SENTINEL_CYCLE_INTERVAL`. Test:
`tests/test_session_sentinel_smoke.py`. Caveat: credential re-login drives a real
login — first time per new IP may hit a new-device challenge (needs `--manual` once).

**Token lifecycle (verified):** accessToken/idToken = 24h JWT, auto-renewed every
~30s while running; refreshToken = months (only its death / Target invalidation
forces a real re-login, which the Sentinel does automatically when creds exist).

---

## Still needed (require your testing / real accounts — deliberately not done unattended)

### Real-world enrolment + first-drop measurement
- First login per account = `--manual`, present (first login through a new BD IP
  will likely trip Target's new-device challenge; `--auto` can't clear it).
- Treat the first multi-account drop as a MEASUREMENT: log per-account ATC timing
  and the exact 429 onset to confirm the throttle key (account vs IP vs fingerprint)
  before scaling. The whole design assumes account+IP — verify it.

### Honest anti-detect ceiling (strategic, not code)
One-machine CDP emulation shares WebGL/audio/canvas/fonts across accounts; ~5
coherent accounts is the realistic ceiling. Past that needs antidetect browsers /
separate machines + distinct cards+addresses (fraud-linkage at checkout).

### (Optional) Cold-boot auto-refresh in app.py
`app.py:3908` halts with "run relogin.py" when the boot login-check fails — *before*
trying the refresh it already has. Calling `SessionManager._trigger_token_refresh()`
(`session_manager.py:1058`) there first would self-heal most "not logged in at boot"
cases without a manual relogin. Low-risk, additive.

---

## Honest ceiling
Engineering gets ~2-3 accounts safely now, ~5 with proxy+fingerprint, more with
forwarder+warming. The real limiters past ~5 are **not code**: shared-payment fraud
linkage (distinct cards/addresses help — the jigging plan is right), only 16 BD ISP
IPs today (buy more), one-time new-device challenges, and Target ToS / order-forfeiture
risk. "More accounts" pays off up to ~5 cleanly; beyond that every account needs its
own fingerprint **and** IP **and** card or the cluster gets linked.

See the design + research backing this in the workflow synthesis (Refract/Stellar/F5
sourced): Shape protects Target's *login* (not just ATC), the browser must be *visible*
(no headless/API login), and sessions are kept warm rather than token-refreshed headlessly.
