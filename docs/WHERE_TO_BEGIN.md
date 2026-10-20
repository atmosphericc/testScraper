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

## Step 2 — arm the two ensure_armed flags (Tue daytime, one verifier each)

`ENSURE_ARMED_STATUS_FAILCLOSED=1` and `ENSURE_ARMED_ROWLOG=1`: `setx` (the tasks read the USER env, not the bat
env) + the bat line; prove with a task run behind a timed `state/ensure_armed.disable` exactly like 10-19 09:43.

## Step 3 — the only lever with data behind it: a second clean network line

Accounts on one IP pass or fail the limiter together; the home line alone has carted 0 on both hot nights. The
experiment is already armed: guest follower `tools/experiments/limkey_guest_follow.py` (tasks guest-follow 2340 /
0135). Run it on a phone hotspot (second network) Wed and Fri, read with `--readout`. If the second line passes the
limiter in-window, that is where the next buyer account goes (EXP-1002-NET). Off-window the limiter lets everything
through (C-1016-17), so only a real drop measures it.

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
