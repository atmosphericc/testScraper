# Findings — hot-SKU 0-for: accounts, proxies, and what to actually check

**Date:** 2026-08-10. One-file summary of a full investigation into "should I make
more Target accounts to beat the hot-SKU misses." Written to be read from your main
computer. **Read this top-to-bottom, then do the two checks in §7 — they decide
everything and I could not run them from the Mac (your live config + drop logs are on
the Windows box).**

---

## 0. The question you started with
> The bot is fast and works, but Target 0-for's a couple of the HOTTEST SKUs. Do I
> need more accounts (unique address/name/card each)? Is Privacy.com the way?

## 1. The big reversal (read this first)
After tracing your code, docs, git history, and — decisively — your **own comments in
`config/proxyIps.json`**, the conclusion flipped:

**Your problem is almost certainly NOT "too few accounts," and probably NOT "shared
IP" either. The evidence points at a wall ABOVE the account/IP layer** — a SKU-wide
`FAST_SELLING` throttle and/or member-token (401 write-auth) freshness at the drop
instant. Both are things your recent commits already fight. **More accounts / more
proxies likely won't fix it.**

## 2. Your symptom, precisely
- Only the couple of **hottest** SKUs fail; **regular purchases succeed**.
- On those: **order never places** ("doesn't add to cart"), you think it's a **429**.
- You believe **all 3 accounts fail together**, **instantly**.

## 3. The four possible walls, and which one fits
Only ONE is fixed by more accounts, and it's the one you're NOT seeing:

| Wall | What you'd see | Fits your symptom? | More accounts fix it? |
|---|---|---|---|
| **Account soft-ban / reseller** | order PLACES then cancels later; per-account | ❌ you said "never places" | ✅ yes (but not your case) |
| **F5/Shape request block** | HTTP **403** / challenge | ❌ your own 07-12 note: "we PASS Shape, 424 never 403" | ❌ no |
| **Member-token stale (write-auth)** | ATC **401** | ✅ plausible; "all fail together" | ❌ no — it's token freshness |
| **Inventory / FAST_SELLING throttle** | **429** / 424 RESERVATION, SKU-wide | ✅ best fit (you said 429) | ❌ no — SKU-wide, hits all |

Your own 2026-07-12 note in `run_bot_with_nightly_restart.bat`, verbatim:
> "our inline fetch() re-signs Shape per request, so we PASS Shape (424, never a 403
> block) — the ATC 401 is the WRITE-AUTH/member-token layer, not Shape. Token
> freshness is THE #1 lever."

→ You already **disproved** "it's Shape security" back in July. The real walls are
**FAST_SELLING (inventory)** and **ATC-401 (token)** — neither is an account problem.

## 4. Two Chrome fleets, two proxy systems (this caused the confusion)
- **Stock fleet (resilient stack):** auto-pulls all 16 IPs from `config/proxyIps.json`,
  1 IP : 1 Chrome, health-tracked. Port band 22000/24000. **99.95%/60min, zero 403s**
  (CLAUDE.md). This is the "unique proxy per Chrome" you remembered — it's REAL, for
  stock.
- **Purchase fleet (WorkerPool):** exit IP comes ONLY from the `proxy_url` field per
  account in `config/target_accounts.json` (`worker_pool.py:110`). Empty ⇒ home IP.
  Port band 23000. Does NOT auto-pull from the 16-IP pool.

## 5. "It wouldn't purchase via proxy" — resolved
Your memory is real but is most likely about **login**, not **purchase**:
- **Purchase THROUGH the BD proxy WORKS** — write-auth POST returned **424 (Shape
  passed)** on the real BD-proxy path, validated live 2026-07-07 PM (`docs/FAILURES.md:506`).
- **LOGIN through the BD proxy FAILS** — Shape-blocks the login endpoint
  (`[RELOGIN] … failed`, `docs/FAILURES.md:520`). That's why login is pinned to the
  HOME IP (`RELOGIN_SKIP_PROXY=1`). Login ≠ purchase; different requests.
- Design is therefore **login=home IP, purchase=BD proxy**, on purpose.

## 6. The decisive find: your purchase IPs ARE assigned (by design)
`config/proxyIps.json` contains your own notes:
- `_comment_active`: *"The 3 account PURCHASE IPs are held out of this list on purpose
  (isolation)."*
- `_comment_purchase`: *"Account purchase exits (NOT swept): **primary=31.105.133.83,
  business=31.98.158.87, alt-1=92.112.18.99** — see config/target_accounts.json."*

So each purchase account was **designed to exit its OWN dedicated BD IP**, held out of
the sweep pool. If your real `target_accounts.json` has these filled (very likely —
your comment says so), then:
- The **shared-home-IP theory is WRONG** for your live setup.
- **3 distinct IPs failing together on a 429 ⇒ the wall is NOT IP-keyed** ⇒ it's
  SKU-wide throttle or the token tier ⇒ **more accounts/IPs won't help.**
- Caveat: 2 of your 3 purchase IPs are `31.x` (same BD range as most sweep IPs); if
  that subnet got flagged they could fail together — the drop-log check settles it.

## 7. DO THESE TWO CHECKS (Windows box) — they decide the fix
Everything hinges on these; I can't run them from the Mac.

**Check A — are the purchase proxy_url fields actually filled?**
Open `config\target_accounts.json` and look at the 3 `proxy_url` fields:
- Filled with `...-ip-31.105.133.83...` / `31.98.158.87` / `92.112.18.99` ⇒ purchase
  fleet already on distinct IPs (expected).
- Empty `""` ⇒ they're on the HOME IP after all ⇒ shared-IP theory back in play ⇒
  fill them (free; purchasing-via-proxy is proven to work).

**Check B — on the next hot-SKU miss, grep the real drop log:**
```
findstr /I "Exit proxy" logs\run_*.log
findstr /I "DCO_RATE FAST_SELLING 429 401 RESERVATION order_id placed CANCEL HOME" logs\run_*.log
```
Interpret:
- 3 distinct `Exit proxy: 127.0.0.1:23001/2/3` **+ simultaneous 429/FAST_SELLING** ⇒
  **SKU-wide throttle** ⇒ fix is FAST_SELLING tuning, NOT accounts. (See §8.)
- **ATC 401** ⇒ **member-token not fresh at drop instant** ⇒ fix is token-freshness
  hardening, NOT accounts.
- **403** ⇒ genuinely Shape (would be surprising) ⇒ different track.
- **order_id then a cancel** ⇒ THEN it's a real soft-ban ⇒ accounts/jigging apply
  (that whole track is in the other session docs).
- **`HOME IP` ×3** ⇒ Check A was empty ⇒ fill proxy_url first, re-measure.

## 8. If it's the SKU-wide FAST_SELLING throttle (most likely)
You've already built most of the fix. Relevant knobs (all in the launcher):
- `TARGET_FAST_SELLING_COOLDOWN_S=45` — back off instead of re-shooting into the
  limiter (07-21 log: re-shooting FEEDS it, answered 0 of ~20 re-shoots).
- `TARGET_FAST_SELLING_HOLD_CART=1` / `HOLD_MAX_S=75` — hold the won cart through the
  cooldown. **Verify HOLD_MAX_S actually covers the observed ~2-min limiter clear** —
  if a log shows the window closing before the limiter reopens, bump it.
- `TARGET_FAST_LANE=1` — ATC→pre_checkout→place-order as one in-browser chain (~0.85s).
- Being fully WARM at the drop minute (token member-fresh, cart tab primed) matters
  more than any account count.

## 9. Is 3 accounts enough?
**Yes.** Refract (the top paid bot) explicitly self-caps at 10 and says *"Target is
not a game of scale."* You are not identity-limited (no place-then-cancel), and the
evidence says you're likely not IP-limited either. **Do not spend on more accounts,
Circle 360 ($99/yr, one-per-household anyway), or SMS numbers until a drop log shows
`order_id`-then-cancel.** The money would not buy a single extra hot-SKU win against a
SKU-wide throttle or a token-freshness race.

## 10. Bottom line
- The bot isn't the bottleneck; account count isn't either.
- Purchase-via-proxy works; login-via-proxy doesn't (already handled).
- Your purchase IPs are assigned per-account by design — so "all fail together" most
  likely = a **non-IP wall** (SKU-wide FAST_SELLING throttle and/or ATC-401 token
  freshness).
- **Next: run Check A + Check B (§7). That single look tells us which wall, and the
  fix is free either way.** Send me those log lines and I'll give you the exact change.

---
*Backing detail (other docs from this session, not pushed): `HOT_SKU_DIAGNOSIS.md`,
`PURCHASE_PROXY_AUDIT.md`, `ACCOUNT_SCALING_STRATEGY.md`, `TARGET_JIGGING_AND_CARDS.md`,
`ACCOUNT_SCALING_PLAYBOOK.md`.*
