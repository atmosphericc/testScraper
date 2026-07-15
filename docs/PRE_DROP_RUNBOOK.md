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

## T-30 min — clear competing sessions (kills churn sources you control)
```
venv/Scripts/python.exe chrome_target_signout.py          # Chrome + Edge + Brave
venv/Scripts/python.exe diagnose_token_churn.py --close   # confirm: no local session
```
- The guard runs at every boot too, but run it now to be sure.
- **Sign `elricomon` OUT of the Target app on your phone** (and any other device).
  A second live session anywhere rotates that account's token out from under the bot.
- `diagnose_token_churn.py --close` should end with **"No local browser holds a
  Target session."** If it names one, that browser/profile is a leak — sign out there.

## T-15 min — verify write-auth (the check that actually matters)
```
venv/Scripts/python.exe verify_multi_account_live.py
```
Read section **[2.5] WRITE-AUTH per account**. You want, for all three:
- `member=True`, `token_ttl` > ~10m, `write_probe` ≠ 401 → **✅ WRITE-AUTH OK**

And section **[3/3] EGRESS IP** — each account must exit its **own BD ISP IP**
(never the home IP; a home-IP purchase ate a 24× instant-429 storm on 06-30).

**If any account shows ❌ WRITE-AUTH DEAD:** it will 401 every ATC at the drop.
Recover it *now*, unhurried (a relogin is destructive and rate-capped — do it here,
never mid-drop):
- restart the bot so its boot relogin re-mints, **or** run the nightly wrapper's
  login, **or** sign that account in manually in its own bot Chrome.
- then re-run `verify_multi_account_live.py` and confirm it flips to ✅.
- Do this as close to the expected drop as you can — the rebuilds re-churn in
  ~30–70 min, so verifying late = starting hotter.

## T-0 — start the bot and watch the boot
Start `run_bot_with_nightly_restart.bat`. In `logs/runs/run_<ts>.log` confirm:
- `[RELOGIN]` … 3/3 accounts logged in, member token MINTED ✅
- pool **LIVE** with 3 BD exits, **write-auth ×3**, `[SENTINEL] timer thread started`
- first `STOCK STATS` clean (200s, **0 403s**)

If a `TOKEN CHURN`/`AUTH_CRITICAL` alarm fires for an account before the drop, that
account is going cold — it's the expected rebuild behavior, not a new bug.

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

## Kill switches / knobs (in `run_bot_with_nightly_restart.bat` or env)
- `CHROME_SIGNOUT_SKIP=1` — skip the personal-browser signout guard.
- `TARGET_SIGNOUT_BROWSERS=chrome,edge` — restrict which browsers the guard sweeps.
- `RELOGIN_SKIP_PROXY=1` (set) — logins run on the HOME IP (BD-IP login is Shape-blocked).
- `TARGET_RETRY_WHILE_IN_STOCK_BUDGET_S=110` / `..._MAX=24` — per-wave retry persistence.
