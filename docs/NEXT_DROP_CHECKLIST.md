# Next-drop checklist — surfaced by /pre-drop

STATUS: PENDING
<!-- /pre-drop (tools/predrop/preflight.py check_next_drop_actions) prints the ACTIONS block below as a WARN while
     this says PENDING. Set STATUS: DONE after the drop readout to quiet it. -->

The add-to-cart limiter is a site-wide lottery whose win-probability p is set by Target per window; it was ~0 on the
last 3 drops and 30% the one night we carted (C-1023-26, docs/PLAN_FIRST_PURCHASE_2026_10_23.md section 8). We cannot
force p>0. These actions position us to cart when the gate opens and MEASURE the one open question: is a 0-admit
night closed for everyone, or only our home IP throttled? If a residential/Bright-Data line admits while home gets 0
in the same window, the gate is line-specific and residential shooting is worth building; if every line is 0, it is
global and no line/account/volume change helps.

## ACTIONS AT THE NEXT DROP
- ARMED, no action: TARGET_FAST_CADENCE_IDENTS=alt-1 (R-FAST-1023) — alt-1 fast (3-4.5 s), primary/business slow (55-70 s), same-race A/B. Just read it out after.
- At ~01:35 CT on the drop night, launch the 4 extra guest-follower lines from the main session (the home follower auto-runs via its nightly task):
-   python tools/experiments/limkey_guest_follow.py --proxy reserve:0 --tag bd0   --lock-name guest_follow_bd0   --proactive-relaunch-s 720
-   python tools/experiments/limkey_guest_follow.py --proxy reserve:1 --tag bd1   --lock-name guest_follow_bd1   --proactive-relaunch-s 720
-   python tools/experiments/limkey_guest_follow.py --proxy resi:2    --tag resiC --lock-name guest_follow_resiC --proactive-relaunch-s 720
-   python tools/experiments/limkey_guest_follow.py --proxy resi:3    --tag resiD --lock-name guest_follow_resiD --proactive-relaunch-s 720
- LIMITER-BYPASS ARM (BUY NOW subchannel vs plain, home line, same windows): launch alongside the above:
-   python tools/experiments/limkey_guest_follow.py --buynow --tag buynow --lock-name guest_follow_buynow
- DO NOT create accounts for the limiter (one-IP accounts share its fate, C-1015-01). Add accounts only if the line measurement shows a residential line admits while home does not.
- After the drop, run the readout:
-   python tools/events/build.py --only <run_id>
-   python tools/events/q.py walls --run <run_id>     (per-ident wall-1 pass: fast alt-1 vs slow home)
-   python tools/events/q.py admits --run <run_id>    (per-window admits, first-admit timing, cadence)
-   BUYNOW vs plain: limkey --readout the buynow + home follower JSONs, compare limited% per window
-   python tools/experiments/limkey_guest_follow.py --readout logs/experiments/guest_follow_*_<stamp>.json
-   python tools/experiments/e1_readout.py --run <run_id>
- Then set STATUS: DONE here.

## LIMITER-BYPASS EXPERIMENTS TO TRY (money-free, before building anything)
The research into getting through the limiter writes its findings here and into docs/PLAN_FIRST_PURCHASE_2026_10_23.md.
TESTABLE NOW (money-free, armed): the BUY NOW subchannel (cart_subchannel BUYNOW) — a `--buynow` guest-follower arm
(above) fires it on the hot TCINs alongside the plain home follower; compare their edge-429 rate in the same windows.
BUYNOW confirmed mechanically valid 2026-10-23 (returns 424 on the control, like plain). Lower 429 on BUYNOW at a
hot window = a real bypass -> build it into the buy path. Weaker/phone-only: the native-app cart (no public capture,
needs an operator phone + the mobile Shape SDK). Passive: watch Target's web bundle for the 'drawing' strings.
Full research + ranking: docs/PLAN_FIRST_PURCHASE_2026_10_23.md section 9, claim C-1023-27.

## WHY (one line)
We cannot open Target's gate; this positions us to cart when it opens and tells us whether a residential line ever
sees an open gate when home does not — the only result that would change the architecture.
