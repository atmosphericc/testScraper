# EXP-HARVEST-DURINGDROP — pre-registered readout + arming decision tree

Written 2026-11-08, **before** any during-drop run. Settles the one question that gates the
harvest-bank-survival fix: when the HUMAN/PX wall hits the bot's account harvest tabs during a
drop, is it **PROFILE-bound** (keys on our aged, logged-in account profiles) or **LINE/DEMAND-bound**
(keys on the home IP, or is Target-global for the window)?

Background: on 2026-11-05/06 the edge limiter opened at volume for the first time in the zero-night
era, but a PX wall drained the Shape bank at ~04:34, so all 273 admitted buyer shots fired
`page_signed` (0.3% cart) instead of `banked_replay` (29.7%). Keeping the bank alive through that
wall is the recoverable-units lever. The off-window probe on 2026-11-06 ruled OUT a *standing* line
block (fresh guest loaded the decoys 24/24 clean) but could not separate profile-bound from
demand/time-bound, because it ran off-window with the bot down. This settles it, live.

## Why this needs a during-drop run (and why it couldn't happen before)
`tools/experiments/harvest_px_probe.py` deliberately refused the 00–06 drop band
(`in_allowed_window`). The 11-06 wall hit at 04:34 — inside that refusal. The new `--during-drop`
flag relaxes **only** the clock window (every money-safety guard stays: fresh throwaway guest
profile, NO login / account files / add-to-cart / cart / checkout, attach-check before any nav,
isolated DevTools port range, kill-own on failure — pinned by
`tests/test_harvest_px_probe_safety.py::test_during_drop_relaxes_only_clock`).

## Procedure (operator, at the next drop)
1. Boot the bot to GREEN as usual and let it run.
2. When the window opens (the limiter starts admitting / the first `MISS-PROBE px=yes` appears in the
   run log), launch the probe on the same host (or, better, a separate device on the same line —
   zero resource competition):

   ```
   venv\Scripts\python.exe tools\experiments\harvest_px_probe.py --during-drop --loads 999 --cadence-s 30 --max-min 90
   ```

   `--loads 999` + `--max-min 90` ⇒ it loops the 3 decoys at a 30 s cadence until the 90-minute cap;
   widen `--max-min` to span a longer window. Keep it running across the account-wall.
3. Stop when the account-tab wall clears, or let `--max-min` end it. The readout JSON lands at
   `logs/experiments/harvest_px_probe_<stamp>.json` (`during_drop: true`).

## Readout inputs
- **A = guest fresh-profile wall rate** during the run: probe JSON `summary.overall_wall_rate` and
  `summary.per_decoy` (judged by the bot's OWN detector — `HARVEST_MISS_PROBE_JS` scored by
  `px_challenge.is_px_challenge`, byte-identical to a bot `MISS-PROBE px=yes`).
- **B = account harvest-tab wall presence** during the SAME wall-clock window, from the bot's run log:

  ```
  grep -c "MISS-PROBE px=yes" logs/runs/run_<id>.log          # was there a wall at all
  grep    "MISS-PROBE px=yes" logs/runs/run_<id>.log | less   # per-account, and that it SUSTAINED (a park, not a flicker)
  ```

  Cross-check the downstream consequence (the bank actually drained → shots fell to page_signed):

  ```
  venv\Scripts\python.exe tools\events\build.py               # rebuild events.sqlite for the run
  venv\Scripts\python.exe tools\events\q.py cred_wall2 --run run_<id>
  venv\Scripts\python.exe tools\events\q.py cred_source --run run_<id>
  ```

## Pre-registered verdict (over the window where the probe ran AND the accounts were challenged)
Require ≥ ~10 guest loads answered during the account-wall window (the probe's `MIN_ANSWERED=6` is
the floor; aim higher), and the account wall must be a **sustained park**, not a single flicker (the
probe's own 2.5 s re-check already distinguishes the two).

| Verdict | Condition | Fix |
|---|---|---|
| **PROFILE-BOUND** | B: accounts walled (≥1 sustained park/account) **AND** A ≤ 0.10 with ≥1 clean load per decoy | Fresh-profile harvest for the account tabs — **E2**. Arm on ONE account at the next drop. |
| **LINE/DEMAND-BOUND** | B: accounts walled **AND** A ≥ 0.50 | Second network line (**EXP-LINE**); profile rotation cannot help. Escalate the parked 2nd-line item. |
| **INCONCLUSIVE** | accounts did NOT wall during the run (no wall to discriminate) **OR** 0.10 < A < 0.50 **OR** < 6 guest loads answered **OR** mostly probe/Target errors | Re-run `--during-drop` at the next walled drop. No arming. |

## Arming decision tree
- **PROFILE-BOUND → E2** (harvest-tab PX-survival mitigation), flag-gated, ONE account, default-off.
  The exact mechanism is set by the Q1 verdict below (is a guest-minted Shape sensor portable onto a
  logged-in account's buyer shot, or is it browser/session-bound?).
- **LINE/DEMAND-BOUND → EXP-LINE** — provision a second line / mobile hotspot for one buyer account's
  harvest, then re-measure. (This is the ≥37-day-parked second-line item; this verdict is what
  unblocks spending on it.)
- **INCONCLUSIVE → re-run** at the next walled drop. Never arm on an inconclusive read.

## E2 FIX MECHANISM — Q1 RESOLVED to a gate (antibot-analyst, 2026-11-08)
Q1 was: *does a Shape sensor minted in a fresh GUEST browser pass wall-2 when it is swapped onto a
logged-in ACCOUNT's ATC shot, or is it bound to the minting browser/session?*

**Q1 VERDICT: UNKNOWN_FROM_DATA — and the architecture leans BROWSER/COOKIE-BOUND.** This is the
load-bearing result for the whole profile-bound branch, so read it before building anything:

- The account harvest replay (`merge_replay_headers`, `src/session/shape_harvest.py:266-296`) swaps
  ONLY the `X-<prefix>-*` sensor headers and keeps every other header — **including the `Cookie`
  header with the account's `_abck` / `bm_*` Shape cookies** — verbatim from the buyer tab. Today
  that is coherent ONLY because the account harvest tab and the buyer tab live in the **same browser /
  profile / cookie jar** (`purchase_executor.py:4332-4453`): the sensor and the Shape cookies come
  from one place.
- Therefore the naive "guest-fallback" (mint in a fresh guest profile, drop the set into the account
  bank, reuse `merge_replay_headers`) is **KNOWN-BROKEN**: it would pair a **guest** `X-*` sensor with
  the **account's** `_abck`/`bm_*` — exactly the cross-browser/cross-IP mismatch the lane team rewrote
  `_merge_cookies` (`src/lanes/lane_shooter.py:60-84`, C-1023-22..24) specifically to avoid. It would
  most likely just reproduce `page_signed`'s ~0.35% reach-cart. **Do not ship it.**
- A fresh-ACCOUNT-profile / re-auth path is **barred**: scripted login is Shape-burned (~0/25,
  agent-context §1) and a fresh profile discards the aged HUMAN cookies that make the account browser
  readable at all.
- The ONLY mechanism with a chance is **cookie-coherent**: swap the guest set's OWN `_abck`/`bm_*`
  alongside its `X-*` sensor and keep only the account's AUTH cookies (porting the lane's
  `_merge_cookies` split onto the account replay path). **Whether guest-sensor + account-AUTH passes
  Shape wall-2 is the never-run E-SHOOTER gap** — E-SHOOTER (C-1023-22..24) only ever validated a
  guest sensor with a *guest* buyer; it never paired one with an account buyer. So this is UNKNOWN,
  and it CANNOT be built on faith into the live purchase path.

**Consequence: even a PROFILE-BOUND verdict does not license a quick code fix.** The profile-bound
branch is gated on first settling Q1 with a dedicated test (below). Nothing touches
`purchase_executor.py` until Q1 is PORTABLE.

### Q1 portability test (the next build after a PROFILE-BOUND verdict) — two framings
**Preferred — money-free, no drop (extend `tools/experiments/shape_replay_probe.py`, the E-SHOOTER
tool; already offline-tested by `tests/test_shape_replay_probe_1023.py`):** mint a sensor in a FRESH
GUEST profile on the home line; replay it with `curl_cffi` against the CONTROL TCIN (81926151 —
nothing is ever bought) carrying the guest set's OWN `_abck`/`bm_*` + a **fresh** alt-1 AUTH cookie
(member token, 4 h TTL). 424 ITEM_NOT_READY ⇒ **PORTABLE**; keyless 401 `_ERR_AUTH_DENIED` ⇒
**BOUND**. (This is E-SHOOTER arm 8 redone with a live token — arm 8 was inconclusive only because the
saved token had expired.) Needs: one operator hand-login of alt-1 first; runs off-window, bot down.
This must get the SAME money-safety gate harvest_px_probe got (offline test + a fresh-context
claims-verifier CONFIRMED) before it runs — build it deliberately, do not rush it.
**Alternative — live one-account drop arm:** mint in a fresh guest profile, feed alt-1's bank, carry
the guest set's own `_abck`/`bm_*` (port `_merge_cookies`), keep alt-1 AUTH; read
`cred_wall2_acct -p acct=alt-1`. PORTABLE iff banked_replay `past_limiter ≥ 20` AND `reached_pct` in
~29-43%; BOUND iff `reached_pct ≈ 0` / `w2_denied ≈ 100%`. This one DOES touch the live purchase path,
so it is the fallback, not the first choice.

### E2 spec (only if Q1 → PORTABLE), for the record
- **Seam:** the `if px:` branch of `_harvest_miss_probe` (`purchase_executor.py:4965-4966`) that today
  calls `_harvest_px_park(desc)`. Insert a flag-gated branch there; default falls through to
  `_harvest_px_park` byte-identically.
- **Flags (default = exact current park):** `TARGET_HARVEST_PX_GUEST_FALLBACK=0` (master);
  `TARGET_HARVEST_PX_GUEST_FALLBACK_ACCOUNTS=alt-1` (one-account scope, same pattern as
  `TARGET_HARVEST_SKIP`); `TARGET_HARVEST_GUEST_REPLAY_COOKIES=1` (gates the cookie-coherent swap —
  required, else it degrades to the known-broken form).
- **Readout:** `cred_wall2_acct -p acct=alt-1` (this doc's query); WORKED/FAILED rule in that file.
- **Arming trigger:** during a drop, `cred_wall2_acct` returns CONFOUNDED for the armed account
  (banked_replay `past_limiter < 5` while page_signed is admitted in volume) AND the harvest log shows
  the account's bank draining to 0 under `MISS-PROBE px=yes` during an OPEN-limiter window — the exact
  04:34 / 11-06 signature. Only then does the lever even apply.

Caveat from the analyst: it read only the harvest-adjacent flags of the 1,947-line launcher, not the
`TARGET_SHAPE_HARVEST` master-arming tail — do a wrapper audit before arming E2.

## Safety note — the during-drop posture change
The probe is read-only: it fires NO add-to-cart, so it does not touch the per-TCIN A2C limiter the
buyer shots fight, and its isolated profile + own DevTools port range cannot collide with the bot's
Chromes. It does add a few decoy PDP loads/min to the home line while the bot runs — but the bot's
own harvest already loads these exact decoys ~4×/min, so the marginal traffic is small. Still, this
is a conscious outward-facing choice: launch it deliberately. Prefer a separate device on the same
line when one is available.
