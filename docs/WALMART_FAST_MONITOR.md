# Walmart Fast Monitor — build spec

**Goal:** reach Target-class per-SKU check speed (**up to ~3 checks/sec/SKU on a focused watchlist**) on Walmart, within our real constraints: **1 Walmart account, ~20 Bright Data ISP proxies, one home IP, no residential, no Walmart+.**

Status: **spec + transport scaffold** (started 2026-08-19, after the first live-drop 0-for). Not yet wired into the resilient stack.

---

## Why the current stack can't do it

- Walmart has **no bulk stock endpoint** (Target's RedSky checks 28 items/request; Walmart is 1 item/request — see `docs/RETAILERS/walmart.md` + `reference_walmart_competitive_architecture` memory). So N SKUs at 3/sec = **3N requests/sec**, unbatched.
- Our monitor uses **full browser page loads** via `tab.evaluate(fetch())` — 5–10× heavier than a raw HTTP request, and it runs **on the logged-in account's sessions**, so hammering it **cooks the account** (exactly what killed the 2026-08-19 drop: 3 RPS → all 6 sessions PX-blocked in 2 min).

## The two unlocks

1. **Raw-HTTP transport (`curl_cffi`, Chrome-impersonating), not a browser.** Fetch `/ip/<id>`, parse `__NEXT_DATA__` — same data, a fraction of the cost, so each IP sustains a much higher rate. (This is what every winning Walmart bot does — research 2026-08-19.)
2. **Decouple the monitor from the account.** Stock-checking needs only a `_px3` device-clearance cookie, **not a login.** So:
   - **Monitor = anonymous, disposable.** Runs on the ISP proxies with per-IP `_px3` (no account). We can push it hard — even **burst to 3/sec/SKU at the drop second** — and if those IPs cook, it costs the account **nothing**.
   - **Buyer = logged-in, protected.** One account on the clean **home IP**, completely separate from the monitor's aggression.

## Architecture

```
                 ┌─────────────────────────────────────────┐
   ISP proxies   │  _px3 HARVESTER (browser, anonymous)     │  mints + refreshes
   (~20, x1 px3  │  one lightweight Chrome per IP, ~hourly  │  _px3 per IP (~1h TTL,
    each)        └───────────────┬─────────────────────────┘  IP+fingerprint bound)
                                 │ _px3 per IP
                                 v
                 ┌─────────────────────────────────────────┐
                 │  RAW-HTTP MONITOR (curl_cffi)            │  fires /ip/<id> per SKU
                 │  anonymous, per-IP px3, burstable        │  parses __NEXT_DATA__
                 │  → in_stock / QUEUED / BLOCKED           │  (delegated to adapter)
                 └───────────────┬─────────────────────────┘
                                 │ on_in_stock / QUEUED signal
                                 v
   HOME IP       ┌─────────────────────────────────────────┐
   (residential, │  LOGGED-IN BUYER (browser, 1 account)    │  queue + ATC + checkout
    un-flagged)  │  never touched by monitor load           │  home IP = best PX trust
                 └─────────────────────────────────────────┘
```

## Throughput math (our ~20 ISP IPs) — MEASURED 2026-08-19

The research-based estimate below was **way too pessimistic** — it was the *browser* number. Live measurement of the raw-HTTP transport through a single **anonymous** BD ISP reserve IP (no `_px3`, no account):

- **40 reqs @ ~1.9 req/s/IP → 40/40 clean, 0 blocks.**
- **120 reqs @ 14 req/s/IP (8 concurrent) → 120/120 clean, 0 blocks.** Ceiling not found.

vs. the browser transport, which cooked at **0.5 req/s/IP** tonight. Raw HTTP is **~28× more tolerant per IP** because it never runs the PX sensor JS — there's no behavioral fingerprint to score, just a clean TLS-impersonated request.

Reframed math: even **1 IP ≥14 req/s** → covers ~4 SKUs at 3/s. **20 IPs → ~280 req/s aggregate** (if linear) → **3/s/SKU on up to ~90 SKUs.** The user's watchlist is a rounding error against that. **3/s/SKU is achievable with enormous headroom on the existing gear.** The "we'd need thousands of proxies" fear was a browser-transport artifact.

⚠ **Still to validate:** these were ~9–20 s bursts, not sustained monitoring over the pre-drop hour under live-drop PX pressure; cumulative scoring or drop-time aggression could lower the sustained ceiling. And it's one IP/one item. But the core capability — hit 3/s/SKU on our IPs without a browser — is proven.

_(Superseded estimate: sustainable ≈0.25/s/IP, burst ≈1/s/IP — that was the pre-measurement guess; keep for history, ignore for planning.)_

## Build phases

- **Phase 0 — prove checkout first** (separate track): home-IP `FINAL_PURCHASE=NO` dry-run on a cheap in-stock item. No point being fast if the buy doesn't complete. *(do before trusting any of this live)*
- **Phase 1 — HTTP transport** (`walmart/http_stock_checker.py`): `curl_cffi` fetch of `/ip/<id>` + parse, reusing `WalmartAdapter` classification. ✅ **BUILT + smoke-passed 2026-08-19** — one anonymous request (home IP, no account, no `_px3`) returned `http=200`, not blocked, parsed the item correctly (name/price/availability) in ~375ms. Proves impersonation gets through the edge and the parse is a true drop-in. (Caveat: one anon request works; sustained rate needs Phase-2 `_px3`, and the `/ip/` view can be the 3P listing — `_extract_product` already forces `walmart_direct`, but reliable 1P availability may need a locale cookie in Phase 3.)
- **Phase 2 — `_px3` harvester**: ⚠ **may be UNNECESSARY** — the 2026-08-19 measurement ran 160 anonymous requests through one IP with **no `_px3` at all** and zero blocks. Build this only if a *sustained* (pre-drop-hour) test shows anonymous requests eventually flag; otherwise skip it entirely and monitor stateless. (One lightweight browser per IP mints/refreshes `_px3` if needed.)
- **Phase 3 — monitor loop + burst**: rate controller (gentle sustained → burst at drop), per-IP px3 attach, `/blocked` → rotate/re-mint, emit `on_in_stock`/`QUEUED`.
- **Phase 4 — monitor/buyer split wiring**: monitor signal → hand off to the home-IP logged-in buyer (never the monitor sessions). Retire the account-coupled browser monitor for drops.

## Constraints & open questions

- **`_px3` is IP+fingerprint bound, ~1h TTL** — harvester must mint per-IP and refresh; a monitor request must use the px3 minted on *its* IP.
- **curl_cffi is BANNED from the Target/Shape path** (`CLAUDE.md`) — this is the **Walmart** monitor only; keep it out of any Target code.
- **Open:** does one anonymous px3 sustain 0.25/s/IP cleanly, or cook faster? (measure in Phase 3). Does the buyer's home-IP session need its own px3 harvest, or does the browser mint it inline? (it mints inline today).
- **Not a queue-odds fix.** This is detection speed. Winning the queue still needs more accounts later — but that's Phase-5+, after 1 account is proven.
