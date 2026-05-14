# Resilient Stack: Operational Limits

## Round 2 — browser-native dispatch (2026-05-13 evening)

### Headline finding
The browser-native stack (real Chrome firing `tab.evaluate(fetch(...))` from
inside its own target.com tab) **sustains 100% success at 3 RPS for at least
20 minutes**, beating Round 1's 15.7-min ceiling at the same RPS.

### Test data
- **20-min sustained, 3 IPs / 33 TCINs (chunked) / 3 RPS / no behavioral**:
  - 3562/3562 = 100.0% success, 0 403s, 0 other
  - All 3 sessions stayed `ready` throughout
- **5-min behavioral=0.10 follow-up (same config)**:
  - 645/652 = 98.9%, 7 403s, 0 other
  - All 7 403s in one ~2s burst on session s2 at t=228s
  - Burst contained — did not cascade to s1/s3
  - **Conclusion**: behavioral=0.10 too aggressive at 3 RPS (PDP nav every 3.3s
    aggregate). Default is now 0.0 (off). Re-enable cautiously at ≤0.02 if
    disguise is needed for very long-duration runs.

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
