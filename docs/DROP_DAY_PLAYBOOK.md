# Drop Day Playbook — Pokemon Wednesday (or any Walmart queue drop)

Concrete minute-by-minute instructions for running the bot during a real drop.
Companion to `docs/POKEMON_DROP_READINESS.md` (architecture + gap analysis).

Last updated: 2026-05-17.

## Realistic expectations before you start

Read this so you go in eyes open:

- **Top tier bots (Refract/Stellar with residential proxies + many accounts)** get 30-60% of stock. **Hobby single-session bots** get 1-3%. **You are between tiers** — closer to top than hobby because of the resilient stack, but not at the top because ISP proxies are weaker than residential and you have one account.
- **Realistic outcome per Pokemon Wednesday**: 1-3 units when stock is reasonable, 0 units when stock is tiny (like Special Collection drops of <5k units).
- **The queue handler has never run against a real queue.** Unit tests pass. Mock harness validates basic plumbing. The full ticket-API listener path was not validatable from the laptop. Expect first-drop surprises.
- **You'll need to be at the keyboard.** This is not a fire-and-forget system. Logs need watching, failures need triage decisions, and recovery between drops requires manual intervention.

## T-90 minutes (1.5 hours before drop)

### 1. Confirm master Walmart login is fresh

```bash
ls -la walmart-profile-login/Default/Cookies 2>&1
```

If the file is older than 5 days, re-run login:
```bash
python walmart_relogin.py
```

Manually log in. Wait until you see your account name top-right. Press Enter in terminal.

### 2. Verify proxy pool health

```bash
python3 -c "
import json
d = json.load(open('config/proxyIps.json'))
print(f'Active: {len(d[\"proxies\"])}')
print(f'Reserve: {len(d[\"reserve_proxies\"])}')
print(f'Warn: {len(d[\"warn_proxies\"])}')
"
```

You want ≥10 active proxies. If under, promote reserve_proxies entries.

### 3. Add Pokemon item_ids to walmart_config.json

```bash
python3 -c "
import json
cfg = json.load(open('walmart/walmart_config.json'))
print('Currently enabled:', [p['item_id'] for p in cfg['products'] if p.get('enabled')])
"
```

Edit `walmart/walmart_config.json` if needed — set `enabled: true` for the drop items.

### 4. Bootstrap sessions (Phase 1c flow)

```bash
python -m walmart.walmart_session_bootstrap
```

Expected: `Seed: N/N succeeded`, `Verify: N/N succeeded`. If any verify fails:
- Note which session ID failed
- Edit `config/proxyIps.json` to swap that proxy with a reserve
- Run `python -m walmart.walmart_session_bootstrap --session sN --force` for just that one

## T-60 minutes

### 5. Start the bot in MONITORING ONLY mode

NOTE: As of 2026-05-17, Phase 2 cutover is not wired. So you have two
options:

**Option A: Test resilient stack standalone (recommended for monitor-only)**
```bash
WALMART_RESILIENT_NUM_CHROMES=16 WALMART_RESILIENT_RPS=6 \
    python -m walmart.walmart_stock_resilient
```

This monitors but does NOT trigger purchases (no purchase_manager wired).
Use for confidence that monitoring sees the queue.

**Option B: Existing single-Chrome bot with checkout (purchase-capable)**
```bash
CHECKOUT_MODE=PRODUCTION FINAL_PURCHASE=YES WALMART_CHECKOUT_API=1 \
    python walmart/walmart_app.py
```

This is the current production code path with hybrid checkout enabled.
Single Chrome session. Lacks the resilient stack's multi-session queue
racing but has the validated end-to-end purchase flow you used for the
notebook.

**Pick B if you want a real purchase attempt.** Pick A if you want to
observe queue behavior without spending money.

### 6. Watch logs settle

Within 60 seconds you should see:
- Pool startup (Option A): `launching N persistent Chromes`
- Stock check heartbeats: `[WALMART] stats: rps=6.0 sweeps=...`
- For each enabled item: regular stock checks at ~0.4 RPS/IP

If you see:
- `BLOCKED` in stats → an IP is hot. Check `state/walmart_proxy_state.json`.
- `tab_evaluate_timeout` → a Chrome crashed. Watchdog should recycle.
- Repeated `HTTP_400` or `PARSE_FAIL` → Walmart structure changed; expect failure during drop.

## T-15 minutes

### 7. Final readiness check

Open a second terminal:
```bash
tail -f walmart/logs/*.log
# or wherever the bot writes logs
```

Confirm you can see live activity. This is your dashboard during the drop.

### 8. Mental checklist

- Master login is fresh ✓
- All 16 sessions seeded + verified ✓
- Pokemon item enabled in walmart_config.json ✓
- Bot is running and monitoring ✓
- You can see logs ✓
- You know what `availability_status="QUEUED:..."` looks like in the log ✓
- You know what `state=valid` (admission) and `state=expired` (eviction) look like ✓

## At drop time (T=0)

### What you should see

**First 5-10 seconds**: log entries showing the configured Pokemon item suddenly returning `availability_status="QUEUED:redirect_url"` instead of `OUT_OF_STOCK`. This means Walmart has activated the queue on that SKU.

In Option B (single-Chrome with purchases): the bot navigates to the product, encounters the queue interstitial, the QueueHandler logs:
```
[QUEUE] Detected queue via /qp URL: QueueTicket(state=pending, ...)
[QUEUE] Starting wait loop (timeout=1800s)
```

In Option A (monitor only): the log just shows the queue detection but no purchase attempt.

### What you should NOT see

- `NO_NEXT_DATA` or `PARSE_FAIL` repeating — means our queue detection missed Walmart's interstitial shape. Stop the bot, capture a screenshot of the `/qp` page if you can, file as bug.
- Bot stuck at `Detected queue via /qp URL` for >2 minutes with no `[QUEUE]` heartbeat lines — listener may not be firing. URL-watcher should kick in around the time admission would happen.
- ALL sessions showing `BLOCKED` — proxy class is being scored too aggressively. Nothing to do mid-drop except wait it out.

### During the queue wait

Typical times: 5 minutes to 2 hours. During this time:
- If `admissionLikelihood: unlikely` appears repeatedly in CDP logs, drop will likely be empty by your turn
- Bot's URL-watcher catches admission via tab navigation away from `/qp`
- After admission, the bot navigates back to the product page (the existing flow) and proceeds to ATC

### After admission

`[PURCHASE]` log lines start firing rapidly:
1. `Add to Cart` — succeeds if showAtc is still true on the live PDP
2. `Reviewing cart` (in non-FAST_DROP_MODE; skip if you set FAST_DROP_MODE=1)
3. `step=bookslot detected` — bot handles via hybrid `reserveSlotMutation` if `WALMART_CHECKOUT_API=1`, else falls back to DOM drawer
4. `Confirm shipping` (instant if saved address)
5. `Reviewing shipping`
6. `CVV entered` — character by character via CDP keystroke events
7. `Place Order clicked` (DOM path) OR `Hybrid place-order: cartId=...` (API path)
8. `ORDER PLACED — pcid=...` ← success signal

If you see ORDER PLACED with a pcid, **you bought it**. Confirmation email comes within minutes from order-info@walmart.com.

## Failure recovery during the drop

### "All sessions parked"

Both IPs hit 2+ consecutive 403s during the queue and ProxyState parked them (10 min default for Walmart adapter). They auto-unpark after 10 minutes. If the drop window is still active, sessions will retry automatically. If the drop window is over, accept the loss.

### "Bot detected queue but admission never fired"

Most likely cause: the CDP Network listener isn't firing in our zendriver build (the limitation the mock harness exposed). The URL-watcher fallback is the safety net — it polls `document.location.href` every second and admits when the page navigates away from `/qp`. If even that didn't fire, the page may be stuck (CSP issue, JS error, etc.).

Manual recovery: kill the bot, fresh bootstrap, re-run. Lose ~5-10 minutes.

### "Queue admitted, ATC clicked, but cart is empty"

The drop sold out between admission and ATC. Walmart still let you through the queue but inventory is gone. Nothing to recover — try again next Wednesday.

### "Order placed but didn't receive confirmation email"

Check `walmart.com/orders` directly. Walmart sometimes delays the email by hours but the order is captured server-side once pcid is issued.

### "Bot crashed mid-purchase"

Inspect `walmart/logs/purchases/`. The activity log will tell you what step failed. Restart the bot — sessions are persistent, you don't need to re-login. If the same crash repeats, it's a code bug — capture the traceback and the failing item_id for post-drop investigation.

## Between drops (Wednesday → Wednesday)

After each drop, no matter the outcome:

1. **Check `state/walmart_proxy_state.json`** — any IPs with `status: "burned"` need to be moved to `warn_proxies` or `disabled_proxies` in `config/proxyIps.json`. They won't recover automatically.

2. **Verify the master login is still good**. If you see `auth-id` cookie missing in any session's profile dir, log back in via `walmart_relogin.py`.

3. **Capture lessons**:
   - Did the queue detection fire correctly?
   - Did the URL-watcher fallback need to engage?
   - How long was the actual queue wait vs. estimated?
   - Were any captured GraphQL hashes stale?

   Write these in `docs/POKEMON_DROP_READINESS.md` so future-you can refine timing/strategy.

## Comparison: what the bot is and isn't doing

To set expectations:

| Thing | Status |
|---|---|
| Monitor stock at high frequency | ✓ Working (Phase 1c validated: 100% over 15 min) |
| Detect Walmart's queue interstitial | ✓ Unit-tested, never seen against real queue |
| Enter the queue and wait through | ✓ Code path exists, never exercised on real queue |
| Solve PerimeterX Press-and-Hold | ✓ In purchase_executor.py from prior work |
| ATC after admission | ✓ Working (validated on notebook) |
| Hybrid GraphQL checkout | ✓ Wired with captured hashes; falls back to DOM if hashes go stale |
| Win against top-tier competition | ✗ Top bots have residential proxies + tls-client direct HTTP |
| Auto-retry from fresh session on queue eviction | ✗ Not implemented; manual restart needed |
| Multi-session queue racing | ✗ Designed but not wired (Phase 2 work) |

## After the first real drop

The single most valuable data point you can capture is: **did the queue handler's `[QUEUE]` log entries match what we designed it to do?**

Specifically:
1. Did `Detected queue via /qp URL` log? (Validates queue detection)
2. Did `CDP captured ticket` log? (Validates the listener — this is the big unknown)
3. Did the bot exit the queue via `Admitted — ticket state=valid` or via `URL navigated away from /qp`? (Tells us which path actually works)
4. How long was the wait? Did `admissionLikelihood=unlikely` ever appear?

If you can answer these after a real drop, we go from 75% confidence to 90%+ on the queue handler. **One real drop's logs is worth more than 4 more hours of laptop simulation.**

Even if the drop wasn't a purchase success, the LOGS are the gold. Save them.
