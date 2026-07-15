# Pre-Drop Runbook — be confident before you commit

**Purpose:** turn "I hope the accounts are hot" into "I verified the accounts are hot."
The 2026-07-14 overnight drop converted only 1 of 9 waves because 2 of 3 accounts
held **dead write-auth** (ATC 401 `_ERR_AUTH_DENIED`) at the drop moments — token
churn from Target-side treatment of the rebuilt accounts. You cannot stop that
churn in code, but you CAN verify state before a drop and start every account hot.

See `memory/session_2026_07_14_overnight_churn_postmortem.md` for the full analysis.

---

## The honest model (set expectations)
- **primary (`elricomon@msn.com`)** — the established account. Reliable floor; it won
  the last drop. Expect ~1 order (qty per Target's per-account limit).
- **business / alt-1 (Gmail rebuilds)** — churn hard (Target rotates their member
  token within minutes). They're *upside* — they buy only if they happen to be hot
  at a wave. The steps below maximize that; new/clean accounts are the real fix.
- **The host machine** is the top uncontrolled risk — a hard freeze = a multi-hour
  blind window no software covers. A drop is only as good as an up machine.

---

## The launcher does the runbook for you
`run_bot_with_nightly_restart.bat` already automates the whole pre-drop sequence,
in order, every time you start it — **no separate commands needed**:
1. **Sign personal browsers out of Target** — `chrome_target_signout.py` (Chrome+Edge+Brave).
2. **Log in all accounts** — `relogin_one.py all` (validate-first; relogins only dead ones).
3. **Drop-readiness echo** — `check_session_readiness.py` prints a per-account
   MEMBER verdict so a cold/guest account is visible immediately (non-blocking).
4. **Hold tokens hot 24/7** — `TARGET_TOKEN_KEEPFRESH=1`; the sentinel re-checks
   write-auth every ~5 min, so it's drop-ready whenever you started it.
5. **Self-heal** — a not-logged-in boot (exit 87) re-runs the account login.

So on drop night: **just start the bat and watch the startup output.** You want:
- `=== drop-readiness check ===` → **all three ✅ MEMBER**
- `[RELOGIN]` 3/3 logged in, member token MINTED ✅
- pool **LIVE** 3 BD exits, **write-auth ×3**, `[SENTINEL] timer thread started`
- first `STOCK STATS` clean (200s, **0 403s**)

A `TOKEN CHURN`/`AUTH_CRITICAL` alarm on a rebuild before the drop is expected
behavior, not a new bug.

## The one thing that's NOT automated
- **Sign `elricomon` out of the Target app on your phone** — the one churn source
  the bat can't reach. Minor (idle phone session churns little), free insurance.

## Do you need `verify_multi_account_live.py`? — No, if you're starting the bot
`app.py` runs the **same** live carts-write probe at startup (that IS the
`write-auth ×3` / `WRITE-AUTH DEAD` log line) and repeats it every ~5 min via
`TARGET_TOKEN_KEEPFRESH`, and reports `pool LIVE 3 BD exits` (egress proof). So
starting the bot already does everything verify does — verify is just a one-shot
snapshot of the same checks. **Run it only when you want a health check WITHOUT
starting the bot**, or to re-confirm exit IPs after a proxy change. Don't run it
while the bot is up (it launches the account browsers a second time).

**Never** trigger a manual relogin mid-drop — it's destructive and can leave an
account signed out; recover pre-drop only.

---

## During the drop — what's normal, what's not
- **Normal:** bursts of ATC 401 on business/alt-1 (churn). The retry-while-in-stock
  loop re-fires ~24× over 110s per wave with fresh Shape — a churned account can
  still catch a hot moment on a later wave (that's how primary won: 401 → repair → 201).
- **Normal:** `MAX_PURCHASE_LIMIT_EXCEEDED` / `tcin_throttled_cooldown` on an account
  that already bought — correct, it can't exceed its per-account limit.
- **Watch for:** `rate_limited_429` on the *hottest* item (Target demand-throttle,
  separate from auth) — nothing to do live; it's an IP/account-spread problem.
- **Do NOT** trigger a manual relogin mid-drop — it wipes the cookie jar and can
  leave the account signed OUT if the login is Shape-blocked. Recover pre-drop only.

## Post-drop
- `logs/purchase_states.json` + `logs/runs/package.log` (cross-run source of truth).
- Per-account race outcomes: `grep "\[RACE\].*3/3 accounts done" logs/runs/run_<ts>.log`.

---

## Tools at a glance
- `check_session_readiness.py` — zero-browser persisted-session check (30s, anytime).
- `verify_multi_account_live.py` — live write-auth + egress-IP probe (launches browsers).
- `diagnose_token_churn.py [--close]` — find competing local browser sessions.
- `chrome_target_signout.py` — clear personal Target sessions (Chrome+Edge+Brave).

## Kill switches / knobs (in `run_bot_with_nightly_restart.bat` or env)
- `CHROME_SIGNOUT_SKIP=1` — skip the personal-browser signout guard.
- `TARGET_SIGNOUT_BROWSERS=chrome,edge` — restrict which browsers the guard sweeps.
- `RELOGIN_SKIP_PROXY=1` (set) — logins run on the HOME IP (BD-IP login is Shape-blocked).
- `TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S=110` / `..._MAX=24` — per-wave retry persistence.
