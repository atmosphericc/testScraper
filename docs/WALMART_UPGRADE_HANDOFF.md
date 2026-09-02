# Walmart Bot Upgrade — RESUME HERE

**Trigger:** if the user says *"continue the walmart bot upgrade"* (or similar), read this file first, then `docs/WALMART_FAST_MONITOR.md`, then the memory `reference_walmart_competitive_architecture` + `session_2026_08_19_walmart_first_drop_0for`.

_Last worked: 2026-08-19 late night, after the first Walmart live-drop 0-for._

---

## The goal
Make Walmart winnable for a **solo, 1-account, ISP-proxy, no-Walmart+** setup — starting by **proving the pipeline works with 1 account**, then scaling. The centerpiece build is a **fast anonymous monitor** that can hit ~3 checks/sec/SKU on the existing ~20 BD ISP proxies without cooking the account.

## Locked decisions (user)
- **1 Walmart account** until we prove it works (then scale accounts).
- **No residential proxies** — keep the ~20 BD ISP proxies.
- **No Walmart+** (fine — standard Wed 9pm ET queue drops don't require it).
- **Checkout runs on the HOME IP** (residential, un-flagged); ISP proxies do monitoring + queue seats.
- Reuse what we have; take it slow; prove each piece before combining.

## The architecture (see docs/WALMART_FAST_MONITOR.md for the full spec + diagram)
- **Monitor** = anonymous raw-HTTP (`curl_cffi`) on ISP proxies. No login, no `_px3` needed so far. Disposable — cook it freely, it never touches the account.
- **Buyer** = the ONE logged-in account, browser (zendriver), on the **home IP**. Separate from the monitor.
- Decoupling the monitor from the account is the fix for the thing that killed 2026-08-19 (3-RPS monitor cooked the account).

## What's DONE (all committed to the working tree, import/compile-clean)
1. **`docs/WALMART_FAST_MONITOR.md`** — the build spec (phases 0-4, throughput math, constraints).
2. **`walmart/http_stock_checker.py`** — `curl_cffi` stock checker; fetches `/ip/<id>`, parses `__NEXT_DATA__`, reuses `WalmartAdapter._extract_product`. **Smoke-passed** (anon request → 200, parsed correctly). Run: `python -m walmart.http_stock_checker <item_id> [proxy_url]`.
3. **`walmart/fast_monitor.py`** — Phase-3 monitor loop: rate-controlled, pool-distributed, anonymous, per-IP block accounting, `on_signal` on in_stock/QUEUED. Run: `python -m walmart.fast_monitor --duration 120 --rps 6 [--max-proxies N]`.
4. **`walmart/queue_handler.py`** — FIXED the false-admission bug from the drop (homepage/`/blocked` bounce was read as "admitted"). Added `is_admission_url` / `is_bounce_url`; bounce → `EXPIRED`. Validated 9/9 vs real URLs; **not yet live-tested**.
5. **`walmart_preflight.py`** — fixed 2 bugs (placeholder-cred false GO; cookie-path check).
6. Config: `walmart/walmart_config.json` armed with 4 SKUs (Ascended Heroes Tin `20497167347`, Destined Rivals ETB `19965460207`, First Partner S3 `20413908978`, First Partner S2 `19952559023`). `config/proxyIps.json` proxy order was rotated (cooked-6 to back) during the drop.

## Key findings (honest — read before trusting numbers)
- **Raw-HTTP transport works and gets through PerimeterX anonymously** — no browser, no account, no `_px3`. This is real and repeatable.
- **It is FAR more tolerant than the browser** (browser cooked at 0.5/IP tonight and stayed cooked).
- **BUT the sustained per-IP budget is IP-variable, NOT "14/sec everywhere."** One reserve IP (`31.105.63.137`) did 160 reqs @ up to 14/sec clean; but a 60s soak at only ~1.5/IP across other IPs hit **50-65% blocks**. Single probes pass on 18/20 IPs, yet sustained load cooks many within a minute. Recovery appears fast (cooked IPs re-probe clean shortly after).
- **Conclusion:** the transport is solved; the real constraint is **per-IP tolerance + IP health over time**. The monitor MUST route by IP health (probe → use clean → park on blocks → recover after cooldown), and the true sustained budget must be measured **on RESTED IPs** (tonight's are cooked).
- Subnet hint (weak, small sample): `31.105.x` looked more tolerant than `168.158.x`, but the dominant effect is cooked-vs-rested, not subnet.

## NEXT STEPS (ordered)
1. **Add per-IP health routing to `fast_monitor.py`** (NOT built yet): preflight-probe each IP; dispatch only to healthy IPs; park an IP after N consecutive blocks for a cooldown; auto-recover. (The per-IP block accounting is already there — `self._ip_blocks` — extend it into a router.) This is pure code, buildable without live drops.
2. **Measure the real sustained budget on RESTED IPs** — rerun the soak tomorrow once tonight's cooking has decayed (hours), to get the true per-IP sustained rate and how many good IPs we actually have.
3. **Phase 0 — prove CHECKOUT** (separate, high-value): home-IP, `FINAL_PURCHASE=NO` dry-run on a cheap **in-stock** item, to the Place-Order button. Needs the account COOLED (it's flagged from tonight). Simplest path: single-Chrome manager (non-resilient = home IP), `CHECKOUT_MODE=PRODUCTION FINAL_PURCHASE=NO`, one cheap in-stock SKU. Confirm ATC → CVV → place-order gate → clear cart.
4. **Wire monitor → buyer** (Phase 4): fast_monitor `on_signal` hands off to the home-IP logged-in buyer (never the monitor sessions).
5. Later: 1P-vs-3P availability (the `/ip/` view can be the 3P listing — `_extract_product` forces walmart_direct, but a locale/store cookie may be needed for reliable 1P), then scale accounts once 1 proves out.

## ⚠ Cooling required before live tests
Tonight's drop + measurements **cooked the account and many IPs** (PerimeterX scores persist hours). Do NOT run live checkout or sustained soaks until they've rested — otherwise you just measure the flag, not the system. Code work (step 1) is safe to do anytime.

## Handy commands
```
# anon transport smoke (1 request)
venv/Scripts/python.exe -m walmart.http_stock_checker 20497167347
# monitor soak (gentle) on first N proxies
venv/Scripts/python.exe -m walmart.fast_monitor --duration 120 --rps 6 --max-proxies 8
# classify pool health (1 probe/IP) — inline script pattern in this session's history
```
Bot start remains **user-only** for the real (buying) app. The monitor soak + probes are anonymous and safe.
