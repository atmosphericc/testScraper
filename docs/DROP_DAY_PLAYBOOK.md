# Drop Day Playbook — Walmart Pokémon Wednesday (CAPTURE RUN)

Minute-by-minute for running the bot during a real drop. Rewritten 2026-08-17
for a **new operator running a capture dry-run** — the correct first drop for
someone with no real-drop data yet.

> Companion: `docs/POKEMON_DROP_READINESS.md` (architecture + gaps).

## Read this first — what this run is (and isn't)

**You are new and have no real-drop data.** So the goal of this drop is **NOT to
win — it's to LEARN.** The bot's checkout has never been validated against real
Walmart; a buy attempt would very likely 0-for anyway. Instead we run it to
*capture the real drop* and come back with the exact facts that make the NEXT
attempt real: the queue's true ticket shape, the live GraphQL checkout hashes,
the real queue timing, and precisely where the flow breaks.

**This run will NOT place an order.** It runs `FINAL_PURCHASE=NO`: the bot goes
all the way through monitor → queue → admission → add-to-cart → build checkout,
then stops at the place-order gate and clears the cart. It cannot buy.
`One real drop's logs is worth more than a month of laptop simulation.`

Realistic expectations, eyes open:
- Top bots (Refract/Stellar, thousands of residential proxies, aged SMS accounts)
  get maybe 30–60% of stock and STILL mostly lose Pokémon drops + eat cancels.
- This bot, first real run, capture-only: expect surprises and breakage. **That
  breakage, captured, is the whole point.** A "failed" capture run is a success
  if the logs are complete.
- You need to be at the keyboard watching logs.

---

## T-90 min — pre-flight

Run the readiness check. It tells you in plain language what's ready and what
will break, GREEN/RED, and exactly how to fix each red:

```bash
python walmart_preflight.py
```

Fix every 🔴 STOP before launching. The usual first-setup blockers:

1. **Credentials** — put a real Walmart account in `.env`:
   `WALMART_EMAIL=…`, `WALMART_PASSWORD=…`, `WALMART_CVV=…`
2. **Master login** — log in by hand once (creates the session the bot rides):
   ```bash
   python walmart_relogin.py
   ```
   Log in, wait until your name shows top-right, press Enter. Must be < 5 days old.
3. **Seed the sessions** from that login:
   ```bash
   python -m walmart.walmart_session_bootstrap
   ```

The 🟡 WARNs matter too — especially these three the script can't check for you:
- **SMS verification**: Walmart increasingly requires an SMS-verified account on
  drops. Unverified → `SMS Required` → the bot stops. Verify the phone first.
- **Walmart+**: Pokémon drops often give members early/priority access; non-members
  can be locked out. A W+ membership materially helps.
- **Proxy provenance**: the pool is labelled for Target. If drop night shows lots
  of `456 Access Denied`, that proxy class is burned for Walmart — swap in fresh.

## T-60 min — arm the real SKU + launch

**Arm this Wednesday's item.** Find the drop's product URL on walmart.com
(`walmart.com/ip/<name>/<ITEM_ID>`), take the trailing `ITEM_ID`, and in
`walmart/walmart_config.json` set `enabled: true` on that item (or add it). The
throwaway `320424995` is only useful for a pure plumbing test — to capture the
REAL queue + checkout you must arm the real SKU. Re-run `walmart_preflight.py`;
"Armed SKUs" should go GREEN.

**Launch the capture dry-run:**

```bash
./run_walmart_capture_dryrun.sh
```

That script re-runs pre-flight (refuses to launch on a STOP), then starts the
bot with the full capture envelope and **`FINAL_PURCHASE=NO` (will not buy)**:
`CHECKOUT_MODE=PRODUCTION FINAL_PURCHASE=NO WALMART_USE_RESILIENT=1
WALMART_QUEUE_CAPTURE=1 WALMART_CAPTURE_CHECKOUT=1 WALMART_CHECKOUT_API=1`.

Windows box equivalent: set those same vars, then `python -m walmart.walmart_app`.

## T-60 → T-0 — watch it settle

Within ~60s you should see the resilient stack launch its Chromes, log in, and
start stock-check heartbeats. Two log surfaces:
- `logs/walmart_app_<ts>.log` — app + purchase flow
- `logs/queue_events_<ts>.jsonl` — the structured queue timeline (one JSON/line)

Green boot signal in the app log: `[APP] Resilient stock checker started`.
If instead you see `Resilient checker start() failed` / `Manager start() failed`
/ `NOT LOGGED IN`, the monitor is dead — fix (usually the login) and relaunch.

## At drop time (T=0)

You should see the armed SKU flip to `availability_status="QUEUED:…"` within
seconds, then the queue timeline start writing to `queue_events_*.jsonl`:

```
kind=queue_entered   session_id=s1
kind=ticket          state=pending  likelihood=likely  ticket_num=…
kind=race_start      racers=2
...
kind=admitted        via=ticket_state | url_redirect
kind=race_won        session_id=s1
```

Then the purchase flow runs to the dry-run stop:
`[PURCHASE] DRY RUN — CHECKOUT_MODE=PRODUCTION but FINAL_PURCHASE≠YES, clearing
cart`. That line means it worked as intended — it reached checkout and stopped.

Do NOT restart a task that's in queue (you forfeit its position). Let it run.

## After the drop — the payoff

This is why you did the run. Analyze what was captured:

```bash
python analyze_queue_capture.py
```

It prints four things, all actionable:
1. **Timeline** — reconstructed drop, incl. how long you actually sat in queue
   and the real admitted→checkout duration (the finite window nobody's measured).
2. **Ticket shape** — the REAL `api.waiting-room.walmart.com` response vs. what
   `queue_handler.parse_ticket_response` assumes; flags fields to fix.
3. **GraphQL hashes** — live persisted-query hashes seen in checkout POSTs vs.
   the hardcoded ones in `checkout_api.py`. **A mismatch here is exactly why
   real checkout 400s** — paste the live value in. (Note: `updateItems`,
   `getSlots`, `reserveSlot` are captured reliably; `CreateContract` fires
   inside place-order, so in a `FINAL_PURCHASE=NO` run it's best-effort — caught
   only if Walmart's own page JS fired it. Everything else is solid.)
4. **Failures** — every `456`/PerimeterX block, tallied.

**Save the raw files** (`logs/queue_events_*.jsonl`, `logs/queue_capture_*.jsonl`,
`logs/walmart_app_*.log`) — they're the gold, win or lose.

Then record the answers in `docs/POKEMON_DROP_READINESS.md`:
- Did queue detection fire? Did the CDP ticket listener catch tickets, or did
  the URL-watcher fallback carry admission?
- How long was the real wait? Did `admissionLikelihood=unlikely` show?
- Which GraphQL hashes were stale?

## What the bot IS and ISN'T doing (current, corrected 2026-08-17)

| Thing | Status |
|---|---|
| Monitor stock at high frequency | ✓ Working |
| Detect Walmart's queue interstitial | ✓ Coded; validating on THIS drop |
| Multi-session queue racing | ✓ **Wired** (queue_race.py) — races all seeded sessions |
| In-queue keepalive guard (don't lose ticket) | ✓ **Wired** (SessionEntry.in_queue, both sides) |
| Enter queue + wait through | ✓ Coded; validating on THIS drop |
| Structured capture (queue + checkout + hashes) | ✓ **New** (queue_events + analyze_queue_capture) |
| ATC after admission | ✓ Working on the notebook flow |
| Hybrid GraphQL checkout | ⚠ Hashes UNVALIDATED — this run captures the real ones |
| Place a real order | ✗ Disabled this run on purpose (FINAL_PURCHASE=NO) |
| Unattended relogin on session death | ✗ login() exists but isn't wired — manual re-login |
| Win vs top-tier competition | ✗ Not the goal of a capture run |

## Failure recovery during the run

- **All sessions `BLOCKED`/`456`** — proxy class is scored hot for Walmart.
  Nothing to do mid-drop; note it and swap proxies before next Wednesday.
- **Detected queue but admission never fired** — the CDP listener may not fire
  in this zendriver build; the URL-watcher (`document.location.href` poll) is the
  fallback. If neither fired, the page may be wedged — the `queue_events` log
  shows exactly how far it got. Kill, re-run; lose a few minutes.
- **`NOT LOGGED IN` at boot** — master login is stale/dead. `walmart_relogin.py`,
  re-seed, relaunch.
- **Bot crashed mid-flow** — the launcher (if you used the nightly-restart one)
  relaunches; here just re-run the script. Sessions are persistent (no re-login).
  Save the traceback + the failing `item_id`.

## Between drops (Wednesday → Wednesday)

1. Run `analyze_queue_capture.py` and fix what it found (stale hashes → paste live
   values into `checkout_api.py` + the APQ bodies in `checkout_apq_queries.json`).
2. Check `state/walmart_proxy_state.json` for burned IPs; move them out of
   `config/proxyIps.json`.
3. Re-verify the master login is still good (`walmart_preflight.py`).
4. Once the checkout hashes + ticket shape are confirmed from a real capture,
   the NEXT drop can be a real `FINAL_PURCHASE=YES` attempt.
```
