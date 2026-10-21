# Where to begin — next enhancement session

Written 2026-10-19 ~23:35 by the readiness-audit session (head `f9f04e55` + this record). Read this, then
`.claude/state/CURRENT_STATE.md` (as-of line + the "RE-AUDIT 2026-10-19" section). Live facts live there, not here.

## State at hand-off

- Branch `feat_refract_arch_v1`; offline suite 69/69 (`python tests/run_offline_suite.py`, ~8 min; never blanket-run `tests/`).
- Two readiness audits (10-18, 10-19; blind replicators, refuters, canaries 3/3 each) both said READY_WITH_ITEMS. Every
  build item is merged. **Nothing in code is the binding term for units** — the edge limiter on the home line is
  (10-09: 686/690 shots 429, 10-16: 1,208/1,208, 0 carts both nights; `docs/CLAIMS.md` C-1015-01, C-0930-03/04).
- Built, verified (Opus evaluator), **UNARMED**: `ENSURE_ARMED_STATUS_FAILCLOSED`, `ENSURE_ARMED_ROWLOG`
  (user env via `setx` + the bat) and `TARGET_SIGINT_ATTRIB` (bat line + a GREEN boot). Claims C-1018-31/37.
- **Console Ctrl+C kills of the wrapper (4 since 10-18): RESOLVED** — the operator confirmed 23:4x that the
  23:20:14 one was their manual stop (C-1018-38); the earlier three (10-18 19:05, 10-18 23:15:37, 10-19 08:02:11)
  are presumed the same. Rule: a console Ctrl+C on the wrapper is an operator stop until shown otherwise — ask
  before investigating. `python tools/ops/bot_ctl.py stop` is the attributable way (stamp + `logs/bot_ctl.log`).

## Step 0 — read first (15 min)

1. `tail -n 30 logs/ensure_armed.log` — did the 01:30 / 23:15 boots reach GREEN; `cat logs/bot_ctl.log` — who stopped what.
2. `python tools/ops/bot_ctl.py status --json`; `python tools/predrop/boot_check.py --log <newest run>`.
3. `python tools/predrop/preflight.py --drop "<next drop>" --tcins ...` (read-only).
4. `docs/CLAIMS.md` C-1018-30..38, `docs/TARGET_CHANGES.md` 10-19, the parked list in CURRENT_STATE.
5. Start the real work at Step 2 (arm the two auto-arm flags) and Step 3 (second network line).

## Step 1 — (resolved) the console Ctrl+C kills were the operator's own stops

Nothing to build. `TARGET_SIGINT_ATTRIB` stays built and optional (it would label a hand stop UNATTRIBUTED with a
small `idle_ms`); the no-focus launch idea and the dummy-console experiment (decision 9) are dropped. If a kill
ever happens with the operator asleep or away, that is the moment to arm the flag.

## Step 2 — (done 2026-10-20 23:4x) the three flags are ARMED

`ENSURE_ARMED_STATUS_FAILCLOSED=1`, `ENSURE_ARMED_ROWLOG=1` (setx + bat) and `TARGET_SIGINT_ATTRIB=1` (bat) were
armed after two Opus pre-arming verifiers (both PARTIALLY CONFIRMED, no blocker), commit `001ddff0`, first boot
`run_20261020_234434` GREEN. Proof still owed: the next task run must log `[FLAGS] ENSURE_ARMED_STATUS_FAILCLOSED=1 ->
status_failclosed=on` (ROWLOG prints no FLAGS line; it shows only as `[BOOTROWS]` on a non-GREEN verdict).

## Step 3 — the only lever with data behind it: a second clean network line

Accounts on one IP pass or fail the limiter together; the home line alone has carted 0 on both hot nights. The
experiment is already armed: guest follower `tools/experiments/limkey_guest_follow.py` (tasks guest-follow 2340 /
0135). Run it on a phone hotspot (second network) Wed and Fri, read with `--readout`. If the second line passes the
limiter in-window, that is where the next buyer account goes (EXP-1002-NET). Off-window the limiter lets everything
through (C-1016-17), so only a real drop measures it.

**EXP-LINE (built + merged 2026-10-20, `637e0a54`):** the follower now takes `--proxy proxies:<i>|reserve:<i>|url`,
`--tag <name>` and `--lock-name <name>`, proves its egress differs from home before any shot (fail-closed), and
`--readout` over mixed-tag files prints the cross-line verdict LINE-DIFFERS / SAME-FATE-ALL-LINES / INCONCLUSIVE
plus per-line wall-2 (Shape) outcomes. Before Friday: a daytime smoke on the Bright Data reserve exit
(`--simulate <tcin> --sim-s 30 --tag bd --lock-name guest_follow_bd --proxy reserve:1`, allowed 06:00-23:05), then
register the second task `guest-follow-bd 2340` / `0135` with `--scheduled --tag bd --lock-name guest_follow_bd
--proxy reserve:1`. The hotspot arm (`--tag hotspot`, no proxy, after `hotspot_egress_setup.ps1` + `egress_check.py`)
and a Bright Data RESIDENTIAL zone arm (`--tag resi --proxy <url>`) are the operator's to provide; the readout
decides between "residential-trust lines are the lever" and "TCIN-global state, only timing can matter".

## Step 4 — operator decisions still open (details in CURRENT_STATE "Operator decisions")

Windows Update pause extension before Wed evening (elevated:
`powershell -ExecutionPolicy Bypass -File tools\ops\wu_drop_guard.ps1 -Days 14`); host on + signed in Tue 22:00 →
Fri 06:30, no console input, no Parsec connect between a boot and 06:30; purchase-limit / qty decision (qty 1 on
limit-1 TCINs); Ditto list + the 26-28 TCIN sweep-size proof; Security audit policy (elevated auditpol);
order-history glance after any drop; delete the 24 `.build_wt/*` worktrees; Tue daytime restart for session freshness.

## Step 5 — parked code items (after Friday; each needs its measurement first)

- 424-after-strike → `held_cart_release_failed` non-transient (C-1018-34): measure the cart-read shape first.
- No-line `pre_429` strike rides to the deadline; wall-3 test retry delay 0; event store lacks `cart_qty` and
  pre-v5 harvest status. Full list: CURRENT_STATE "RE-AUDIT 2026-10-19", parked.

## Standing rules (do not relearn these)

The config TCIN list IS the target list. Never force-login a MEMBER account. Never re-arm token keep-fresh. No
restart / login / relaunch after ~01:30 on a drop night. Bat is CRLF and never edited while the wrapper runs. The
23:15 / 01:30 tasks restart a stopped bot unless `state/ensure_armed.disable` exists. Verify before arming
(claims-verifier, bare claim only). Commit nothing secret: the repo is public.
