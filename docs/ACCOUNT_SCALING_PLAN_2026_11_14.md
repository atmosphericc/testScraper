# Hot-SKU lever map + account scaling (2026-11-14)

Decision material for the operator; nothing here is armed. Supersedes the earlier framing that
called account count the *only* lever -- the adversarial verifier (C-1114-09) corrected that: the
hot-SKU picture rests on n=1 hot order, so we do not yet know the full ceiling. What IS solid is
below.

## The lever map (what moves hot-SKU conversion)

SETTLED -- NOT levers (2026-11-14):
- Faster / more re-shots per account: KILLED (WAVE_FIRST_EDGE=0, 663 re-shots -> 0 edge passes).
- Parallel dispatch of existing accounts: already parallel (2ms atc_t0 spread, MEASURED 09-30).
- Place-order chain speed: already minimal (nav removed 07-21); the 1.6-3.6s is Target server RTT.
- Cadence / cap / hold / qty / ride: not FAST_SELLING levers; riding converts 0 (10-29: 305 FS, 0).

ARMED but UNTESTED code levers (efficacy UNKNOWN -- need a hot cart to test, C-1114-09):
- TARGET_FASTLANE_PRE_RETRY=1 (bat:1115) -- "THE hot-SKU conversion fix" for the pre_checkout FS
  variant (Sept hot carts read atc=201, pre=429). Never tested on a binding hot pre=429 (09-30's
  pre passed, so the wall was at place-order, not pre). Live and waiting for data.
- TARGET_HELD_LINE_FLIP_STRIKE=1 (FX-1001-A) -- manufactures an early place-order on a held line.

STRUCTURAL lever -- account count (the leading one we can act on):
- Each logged-in account = ONE edge-limiter draw per window (the limiter admits ~only a window's
  FIRST shot within ~0.1s of the flip; C-0925-01). More accounts -> linearly more early-admitted
  201s -> more place-orders inside the ~2-3s FS gate. Us: 3 (2 live). Refract: 30-200.

THE DATA GAP that gates all of the above:
- Only ONE hot cart has reached a place-order decision since 10-01 (n=1). On 11-06 the wall was the
  empty harvest bank, not FS. **The Wall A bank fix (landed today) is what will generate hot carts
  to a checkout decision** -- that data is the prerequisite for knowing whether pre_retry /
  held-line-flip convert, and for characterizing Wall B beyond one night.

## The blocking unknown for account scaling -- settle before buying accounts

Does the edge limiter key on the IP (network line)?  [UNPROVEN -- C-0930-04 WEAKENED]
- If YES: more accounts on the ONE home IP share the per-IP bucket -> they add NO independent draws
  (each account would need its own IP / proxy to count).
- If NO (keys on the item, like FAST_SELLING): more accounts on the home IP DO add draws.
Pre-registered test = the in-window cross-line guest test (needs >=2 distinct lines); unrunnable now
(BD cancelled, one line left).

## One-machine constraints (honest)

1. SHARED DEVICE FINGERPRINT -- all accounts real Chrome on one machine (the top cross-account
   linker, 07-12). N accounts look like one device buying N times -> correlated bans. The
   per-account distinct-device feature (TARGET_FP_CHROMIUM) is OFF (09-03: 0/318, IP-confounded).
2. SHARED IP -- see the blocking unknown.
3. RESOURCES -- each account = a full real-Chrome + harvest tab + worker thread (~0.5-1 GB each);
   one machine realistically ~8-15 before limits. (Refract's 200+ are lighter HTTP workers.)
4. PER-ACCOUNT SETUP -- member login (hand-login; scripted is Shape-burned), payment, stored CVV.

## Recommended sequencing

1. KEEP THE BANK FULL -- DONE (Wall A re-confirm fix). Prerequisite + the data generator.
2. NEXT DROP(S): with the bank fixed, watch whether hot carts reach a place-order decision and
   whether pre_retry converts -- this is free data on the armed levers.
3. CHEAP TEST: add 1-2 member accounts and measure whether early-admitted-201 draws rise; this also
   probes the IP-key question (zero added 201s on the home IP => limiter likely keys on IP). Accept
   correlated-ban risk.
4. REAL VOLUME PLAY (budget permitting): proxies (distinct IP/account) + device diversity + 10+
   accounts -- the competitor setup; reinvest after a first success.
5. FIX BUSINESS LOGIN (human-gated) -- restores the 3rd buyer, a free +1 draw.
