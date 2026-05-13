# Resilient Stack: Operational Limits (validated 2026-05-13)

## Headline finding

The stack achieves **100% success rate for ~15 minutes at 3 RPS sustained**,
then trips Shape's account-level threshold and **all** active IPs simultaneously
flip to 403. The flag persists 15+ minutes after stopping. This is not a
per-IP limit — it's an aggregate-session signal at Shape's edge that detects
sustained, structured polling from one BD-account / one endpoint.

## Test data

### 5-min test at 3 RPS (the headline result)
- 567/567 success (100%)
- 0 IPs burned during run
- Pool stable at 20 active

### 30-min test at 3 RPS (the breaking point)
- 0-15.7 min: 100% (1666 successes)
- 15.7-16.0 min: ALL 20 IPs hit 403 in a 4-min cascade
- 15.7-30 min: pool=0 active, 20 parked. Retest after 10 min still 403 → re-park
- Final: 80% over 30 min (1667/2090)

### Post-test 1 RPS test (recovery probe)
- Pre-flight: 0/27 verified clean — still 403
- Confirmed account-level cooldown > 15-30 min

## What works
- curl_cffi(impersonate=chrome131) → real Chrome JA3/JA4 — Shape's primary detector passes
- Local CONNECT forwarder for BD upstream auth — clean integration
- Per-IP visitor_id, modern endpoint, full envelope — checks all boxes
- Auto-park / auto-retest / cloaking detector — fire correctly when conditions warrant
- 100% sustained for **window <= 15 min at 3 RPS**

## What's gated by Shape's account-level threshold
- **Continuous 3 RPS** to `redsky.target.com` from one BD account triggers a
  session-level flag at ~15 min / ~1700 requests cumulative
- All exit IPs in that BD account's pool are flagged simultaneously
- Cooldown is > 15 min, < ? — exact persistence unknown without longer test

## Recommended operating modes

### Mode A: Conservative monitoring (most reliable for 24/7)
```bash
USE_RESILIENT_STACK=1 RESILIENT_TARGET_RPS=0.5 python app.py
```
- ~0.5 RPS = ~30 req/min = ~1800/hr
- Each TCIN refreshed every ~40s — fast enough for drop detection
- Per-IP rate: 0.025/sec = ~1 hit per 40s per IP across 20-IP pool
- Estimated time-to-trip: hours, potentially indefinite
- **First choice for tonight's 7-hour run.**

### Mode B: Drop-window burst (high rate for short windows)
```bash
USE_RESILIENT_STACK=1 RESILIENT_TARGET_RPS=3.0 python app.py
```
- Use ONLY during the actual drop window (when stock could flip imminently)
- Expect ~15 min of clean operation before throttling
- Pair with manual cooldown periods otherwise

### Mode C: Hybrid (programmatic switching)
- Default to 0.5 RPS for monitoring
- Bump to 3 RPS in the 15 min before announced drop time
- Drop back to 0.5 RPS after stock detected + purchase fires
- Not implemented yet — would require dynamic rate control

## Recovery strategy after a trip

If the bot reports mass 403s, the operational playbook is:

1. **Stop ALL traffic** to RedSky from your BD account for at least 30 min.
2. **Run `python audit_all.py`** to see if proxies have recovered.
3. **Restart bot at lower RPS** (0.5 or 1.0 max).
4. **Monitor for re-trip** — if it happens again, double the cooldown and halve
   the rate.

## What would push the threshold higher

Not implemented in this branch but useful for future work:
- **Cookie harvester** (`src/session/cookie_harvester.py` — built, not yet
  integrated). Real Chrome session cookies bump trust scoring, may delay the
  threshold trip. Worth wiring in for next iteration.
- **Behavioral diversification**: mix in non-RedSky requests (e.g., periodic
  product page HTML loads, sapphire-api calls) so the polling pattern doesn't
  dominate the request mix.
- **Multi-account proxy rotation**: split the rate across multiple BD accounts
  / zones. Each account has its own threshold; aggregate capacity = N × per-
  account limit.
- **Variable cadence**: random pauses (5-30s gaps every few minutes) to look
  less like a deterministic poller.

## Bottom line

The Refract pattern is correctly implemented and works. The 100% / 15-min ceiling
at 3 RPS is a real Shape limit, not a bug. **For 24/7 operation at this level
of resilience, the maximum sustainable rate is ~0.5-1 RPS through one BD
account.** Higher rates are achievable in bursts but require cooldown.
