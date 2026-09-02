# Resilient Stack: Operational Limits

## Round 2 — browser-native dispatch (2026-05-13 evening)

### Headline finding
The browser-native stack (real Chrome firing `tab.evaluate(fetch(...))` from
inside its own target.com tab) **sustains 99.95% success at 3 RPS over 60
minutes / 10K+ dispatches** with zero Shape-related failures and full self-
healing on session glitches. This beats Round 1's 15.7-min mass-burn ceiling
by 4×.

### Test data
- **60-min stress, 3 fresh IPs / 33 TCINs (chunked) / 3 RPS / no behavioral**:
  - **10,585 / 10,590 = 99.95% success at last measurement (t=3570s)**
  - **Zero 403s** — Shape never blocked across the full hour
  - 5 timeouts total — all on one session (s2) in a single burst at t≈51 min
  - Self-heal triggered at 5 consec errors → state=crashed → watchdog → new
    Chrome → cookies=11/visitor_id ready in ~30s end-to-end
  - The 5 timeouts were a zendriver internal bug (CDP `StopIteration` during
    response routing), not Shape: the underlying RedSky fetches actually
    returned 200s with valid product_summaries data — zendriver's response
    listener just crashed delivering them back to the awaiter
- **20-min sustained (prior, same config)**: 3562/3562 = 100.0%
- **5-min behavioral=0.10 follow-up**:
  - 645/652 = 98.9%, 7 403s, 0 other
  - All 7 403s in one ~2s burst on session s2 at t=228s (contained, no cascade)
  - **Conclusion**: behavioral=0.10 too aggressive at 3 RPS (PDP nav every 3.3s
    aggregate). Default is now 0.0 (off). Re-enable cautiously at ≤0.02 if
    disguise is needed.

### Known issues from stress (cosmetic — don't block operation)
- **Shutdown hangs**: 60-min run finished cleanly but `checker.stop()` never
  returned — had to force-kill. Likely `forwarder_pool.stop_all()` or browser
  teardown deadlock. Workaround: SIGKILL via Task Manager. Zombie Chromes
  may need manual cleanup. Tracked as a follow-up.
- **proxy_state.json `WinError 5` on save**: observed once during stress —
  `os.replace(tmp, state/proxy_state.json)` blocked, likely concurrent save
  attempts. In-memory state was unaffected. Tracked as a follow-up.
- **zendriver listener tracebacks during recycle**: `InvalidStateError` /
  `StopIteration` exceptions logged from the dead Chrome's CDP listener after
  teardown. Cosmetic — the new (recycled) Chrome works fine.

### What works
- `tab.evaluate(fetch(...))` from a long-lived target.com tab — JA3, cookies,
  visitor_id, headers all match a real React-app fetch
- TCIN chunking at MAX=28 (Target hard-caps `product_summary_with_fulfillment_v1`
  at 30 TCINs — confirmed via explicit error body in 400 response)
- Local CONNECT forwarder for BD upstream auth
- Per-IP visitor_id, persistent profile dirs, watchdog recycle
- 100% sustained for window ≥ 20 min at 3 RPS (Round 1 trip threshold)

### Not yet exercised
- 30+ min sustain at 3 RPS — Round 1's 30-min test mass-burned at 15.7 min, so
  20 min already crosses that threshold but a 60+ min run would be a stronger
  proof of stability.
- Higher RPS (5+, 10+) — current validation is 3 RPS only.
- N=22 full pool launch — only 3 IPs validated; 22-Chrome stagger logic exists
  but is untested in steady state.
- Behavioral=0.02 long sustain — would let us know if low-rate disguise is
  viable without triggering the burst seen at 0.10.

## Round 1 — curl_cffi worker pool (2026-05-13 morning, archived)

### Headline finding
The curl_cffi-based stack achieved **100% success rate for ~15 minutes at 3 RPS
sustained**, then tripped Shape's account-level threshold and **all** active
IPs simultaneously flipped to 403. The flag persists 15+ minutes after stopping.

### Test data
- **5-min test at 3 RPS**: 567/567 success (100%), 0 IPs burned
- **30-min test at 3 RPS**: 0–15.7 min: 100%; 15.7–16.0 min: ALL 20 IPs hit
  403 in a 4-min cascade; 15.7–30 min: pool=0 active, 20 parked. Final: 80%.
- **Post-test 1 RPS test (recovery probe)**: pre-flight 0/27 verified clean —
  account-level cooldown > 15-30 min.

### What worked (in Round 1)
- curl_cffi(impersonate=chrome131) → real Chrome JA3/JA4 — Shape's primary
  detector passes
- Local CONNECT forwarder, per-IP visitor_id, modern endpoint, full envelope
- 100% sustained for window ≤ 15 min at 3 RPS

### What was gated by Shape's account-level threshold
- **Continuous 3 RPS** to `redsky.target.com` from one BD account triggered a
  session-level flag at ~15 min / ~1700 requests cumulative
- All exit IPs in that BD account's pool were flagged simultaneously
- Cooldown was > 15 min, < 60 min — exact persistence unknown

## Recommended operating modes (Round 2)

### Mode A: Conservative monitoring (most reliable for 24/7)
```bash
USE_RESILIENT_STACK=1 RESILIENT_TARGET_RPS=0.5 python app.py
```
- ~0.5 RPS = ~30 req/min = ~1800/hr
- Each TCIN refreshed every ~9s via 2 chunks
- Per-IP rate: very low, indistinguishable from a real user
- **First choice for any long unattended run.**

### Mode B: Drop-window monitoring
```bash
USE_RESILIENT_STACK=1 RESILIENT_TARGET_RPS=3.0 python app.py
```
- Use during the actual drop window (when stock could flip imminently)
- Round 2 has cleared 20 min at this rate; longer untested
- If sustained-burst monitoring is needed beyond 20 min, drop to ~1 RPS once
  drop is confirmed and you've placed orders

### Mode C: Hybrid (programmatic switching)
- Default to 0.5 RPS for monitoring
- Bump to 3 RPS in the 15 min before announced drop time
- Drop back to 0.5 RPS after stock detected + purchase fires
- Not implemented yet — would require dynamic rate control

## Recovery strategy after a 403 burst

If the bot reports per-session 403 bursts (one IP getting hit hard):

1. **proxy_state's auto-park kicks in** at 2 consecutive 403s, parking 3h
2. The session's watchdog will recycle the Chrome at CONSECUTIVE_ERROR_RECYCLE_THRESHOLD=5 consecutive errors
3. Other sessions continue serving
4. After 3h park, the IP retests; if it succeeds, it auto-recovers

If the bot reports a mass-403 cascade across all IPs (like Round 1's failure):
1. **Stop ALL traffic** to RedSky from your BD account for at least 30 min.
2. Run a small preflight to see if proxies have recovered.
3. Restart at lower RPS (0.5 or 1.0 max).

## What would push the threshold higher
- **Lower behavioral_mix_ratio** (0.01-0.02) — gives some disguise without the
  high-frequency PDP nav signal
- **Multi-account proxy rotation**: split rate across multiple BD accounts /
  zones. Each account has its own threshold; aggregate capacity = N × per-
  account limit.
- **Variable cadence**: random pauses (5-30s gaps every few minutes) to look
  less like a deterministic poller.

## Bottom line
Round 2 (browser-native dispatch) is functionally working and beats Round 1's
ceiling. **For 24/7 operation, 0.5-3 RPS through one BD account is sustainable**
based on current data. Higher rates and longer durations remain untested.

## TCIN visibility state + alert (2026-08-25)
Motivation: on 2026-08-24 four of the 13 armed TCINs were absent from every
RedSky bulk response all night (unpublished on Target) and the only signal was
an every-2.5-min `logger.warning` nobody read. The checker now makes that loud
and persists it for the pre-drop scripts. Purchase path untouched.

### Timing (real, from run_20260824_231920.log)
The banner / state write can only happen on a ground-truth read, and the first
ground-truth read follows the pool build. The stats loop and the ground-truth
loop start together with a 30 s first wait, so the `[TCIN-VISIBILITY]` banner
fires in the same second as the first `[STOCK STATS] t=30.0s` line (30 s after `[MULTI_SESSION] started -- N/N sessions ready`, ~3-4 min after launch; 08-24: launch 23:19:20 -> pool ready 23:22:33 -> STATS + banner 23:23:03) — NOT "~60 s after boot". No banner is the GOOD case (it prints only when something is invisible) -- confirm it positively: the `[GROUND-TRUTH] pool cache-bust ok: in_stock=[] (N TCINs)` line in that same second must show N == the number of armed TCINs (08-24 showed `(9 TCINs)` for 13 armed = THE finding). If that line is missing too, look for `[GROUND-TRUTH] pool cache-bust read FAILED` / `no ready session` lines; after ~5 min of failed reads the bot prints `[TCIN-VISIBILITY] UNKNOWN -- N consecutive ground-truth reads failed` and stamps the state file `verified=false`.

### Ordering / recovery (round 3)
- The visibility block in `_ground_truth_probe_loop` runs AFTER the C0
  cold/stale fire loop — bookkeeping can never delay a purchase trigger. The
  state write (`write_state_atomic`) retries immediately on failure, with no
  sleep on the loop thread. Stamps (`_invisible_alerted_at` /
  `_visible_again_at`) are set BEFORE any I/O; alert order is stamps ->
  logger -> on_alert -> print, and every print goes through
  `ResilientStockChecker._safe_print` (staticmethod, swallows exceptions).
- `_note_ground_truth_failure(reason)` stamps `_gt_fail_since` on the first
  failure; at 10 consecutive failures (then once per REALERT_S) it writes the
  state file with `verified=false` (`_write_visibility_state(now,
  verified=False)`), `logger.error`, `on_alert("tcin_visibility_unknown",
  "warning", msg)`, then prints.
- On the next successful parse the loop resets `_gt_fail_streak=0`,
  `_gt_fail_since=None`, `_vis_unknown_alerted_at=0.0` and, if the streak had
  reached the threshold, logs `[TCIN-VISIBILITY] verification RESUMED after N
  consecutive failed ground-truth reads` — so a second blind period alerts
  immediately instead of waiting out the REALERT TTL.
- "seen" / "visible" means RedSky returned a product summary carrying that
  TCIN in some 200 response within GRACE_S — NOT proof of sellability. The
  legacy every-5th-cycle `[GROUND-TRUTH] N configured TCIN(s) absent` warning
  is the RAW, undebounced list from a single read and can name more TCINs
  than the state file does.

### Env flags (read at checker construction, all default ON)
- `RESILIENT_TCIN_VISIBILITY_ALERT=1` — `[TCIN-VISIBILITY]` banner + `logger.error`
  + `on_alert` callback on first detection; `=0` silences the banner / `logger.error` / `on_alert` (incl. the UNKNOWN
  alert); the existing every-5th-cycle `logger.warning` stays regardless, and the
  state file plus its `verified=false` blind marker still follow
  `RESILIENT_TCIN_VISIBILITY_STATE`, so the pre-drop readers stay honest even
  with alerts silenced (round-3 review).
- `RESILIENT_TCIN_INVISIBLE_GRACE_S=90` (floor 30) — debounce. A TCIN is
  INVISIBLE only if it is absent from the cache-bust read AND has not been seen
  by ANY 200 response (sweep or ground-truth; `checker._last_seen_at[tcin]` is
  stamped by `_ingest_bulk_response` and by the ground-truth read) within
  GRACE_S. A partial/odd 200 body therefore cannot fake "13 of 13 invisible";
  an unpublished TCIN (never seen) is invisible.
- A 200 whose body parses to 0 TCINs is a FAILED read — visibility is not
  updated that cycle.
- `RESILIENT_TCIN_INVISIBLE_REALERT_S=3600` (floor 60) — TTL gate per TCIN in
  BOTH directions: the first-ever sighting fires immediately
  (`_invisible_alerted_at` default 0.0, never popped), re-flaps inside the TTL
  are quiet; `NOW VISIBLE` (kind `tcin_visible_again`, level success) fires only
  for a TCIN that had an invisible alert and is gated via `_visible_again_at`.
  Bounded to <=2 alerts per TCIN per REALERT_S even if it flaps.
- `RESILIENT_TCIN_VISIBILITY_STATE=1` — write the state file; `=0` = never write.
- `on_alert(kind, level, message)`: kind in `{tcin_invisible, tcin_visible_again,
  tcin_visibility_unknown}`, level in `{error, success, warning}`; the checker
  swallows any exception it raises. app.py routes it to the dashboard activity
  feed (error → `logs/error_log.txt`).
- `tcin_visibility_unknown` (level warning): `checker._note_ground_truth_failure(reason)`
  counts consecutive failed ground-truth reads (no ready session / non-200 /
  0-TCIN parse); at 10 consecutive (~5 min) and then once per REALERT_S it
  prints + `logger.error` + `on_alert`, so silence is never mistaken for
  all-visible.
- **>30-TCIN limitation**: `dispatch_verify` sends the full configured list
  UNCHUNKED and RedSky caps `product_summary_with_fulfillment_v1` at 30/req, so
  above 30 the ground-truth read fails every cycle and visibility goes UNKNOWN
  (`verified=false`). The sweep chunks at 28 and is unaffected. Keep <=30 armed;
  `check_session_readiness.py` and `preflight_fp_drop.py` warn up front
  when >30 TCINs are enabled.
- `_note_tcin_visibility(missing, now, force_write=False) -> dict` with keys
  `newly_missing, became_visible, invisible, alerted, visible_alerted, changed`.
- The banner prints the real state path (`f"{state_dir / STATE_FILENAME}"`) and
  `venv\Scripts\python.exe check_session_readiness.py`.

### State file: `state/tcin_visibility.json` (checker `state_dir`; atomic tmp + replace)
Written on change, on `force_write` (every 5th ground-truth cycle = ~2.5 min),
AND on the first successful read (`checker._tcin_vis_written`). `updated_at_unix`
is the freshness key the readers use (stale after 24 h); `updated_at` is derived
from the same `now`. `state/tcin_visibility*` is gitignored.
All keys always present (schema 1; the round-3 keys are additive):
- `schema` (1), `updated_at` (local naive ISO), `updated_at_unix`,
  `run_started_at_unix` (float or null)
- `configured` — every TCIN the checker was started with, sorted
- `visible` — configured TCINs NOT classed invisible: present in the latest
  full-list cache-bust read, OR seen by any 200 sweep / ground-truth response
  within `RESILIENT_TCIN_INVISIBLE_GRACE_S`; sorted. "Seen" = RedSky returned
  a product summary carrying that TCIN, not proof of sellability.
- `invisible` — configured TCINs absent from the cache-bust read AND unseen by
  any 200 response for > GRACE_S (the debounced set); sorted. The legacy
  `[GROUND-TRUTH] ... absent` logger.warning is the raw undebounced list and
  can name more TCINs than this.
- `last_seen_unix` — `{tcin: float|null}`; last time each configured TCIN appeared
  in ANY 200 RedSky response this run (sweep or ground-truth); null = never
- `verified` (bool) — `true` on every normal write; `false` = the run's
  ground-truth reads were failing when this was written (10+ consecutive) and
  `visible` / `invisible` are the LAST KNOWN lists, not current. Readers treat
  `false` as visibility UNKNOWN (`check_session_readiness.py` verdict UNKNOWN,
  `preflight_fp_drop.py` WARN).
- `gt_fail_streak` (int) — consecutive failed ground-truth reads at write time
  (0 on normal writes)
- `verification_failed_since_unix` (float|null) — when the current failure
  streak began; null on normal writes

### Reader API: `src/monitoring/tcin_visibility.py`
- `STATE_FILENAME = "tcin_visibility.json"`
- `write_state_atomic(path, payload) -> bool` — never raises; per-pid tmp
  (`<name>.<pid>.tmp`), retries once immediately (no sleep — it runs on the
  loop thread), removes the tmp on failure, logs WARNING at most once per 10 min
- `load_state(state_dir) -> dict | None` — None if missing / unreadable / schema != 1
- `summarize(state, enabled_tcins, now, stale_after_s=86400.0) -> VisibilitySummary`
  with `age_s`, `configured`, `visible`, `invisible_enabled` (enabled TCINs in
  `invisible`), `unchecked_enabled` (enabled TCINs not in `configured`, i.e. added
  since that run), `stale` (age_s > stale_after_s), plus (round 3) `verified`
  (bool; True when the key is missing), `gt_fail_streak` (int),
  `verification_failed_since_unix` (float|None)
- `format_invisible_warning(summary) -> list[str]` — ASCII-only lines for scripts
  to print; empty when there is nothing to warn about. When `verified` is False
  it emits a "could NOT verify visibility" line and is therefore non-empty even
  if nothing is invisible / unchecked.
- Consumers: `check_session_readiness.py` (inside `run_bot_with_nightly_restart.bat`
  and `hand_login_all.bat`) and `preflight_fp_drop.py` [8b] — both WARN, never block.
  `check_session_readiness.py` verdict is four-state: OK (all enabled TCINs
  verified visible by a fresh run) / PARTIAL (unchecked — added since the last
  run — or stale >24 h) / WARNING (an enabled TCIN is invisible) / UNKNOWN (no
  state file, unreadable config, or `verified=false`). Both scripts also warn
  when >30 TCINs are enabled (the sweep chunks at 28; the unchunked ground-truth
  read is what fails above 30).
