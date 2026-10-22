# The industry-standard Target hot-SKU bot vs ours — research + gap analysis + cost (2026-10-22)

Operator /goal (10-22 ~09:30): "I'm trying to mimic Refract for a Target bot. I'm ok now with purchasing residential or ISP
proxies. Research the leading industry standard. Are we incorporating it? Sticky sessions? Providers Refract suggests?
Monthly cost? Are we missing anything?"

Method: three Sonnet researchers (Refract 10-19 corpus verbatim + 09-30 diff; proxy providers and 2026 pricing; F5 Shape /
Target community / other bots), plus the E2 guest experiment run this morning (six arms, money-free) and the 10-21 post-run
(C-1021-01..11). Every number below is tagged: [REPORTED] = a vendor or third-party doc, [MEASURED] = our logs,
[INFERRED] = arithmetic or reasoning. Verbatim quotes carry the corpus line (N = `logs/analysis_2026_10_19/outside/
refract_llms_full_2026_10_19.txt`). Prices were read on 2026-10-22 through a summarising fetch; re-read the checkout page
before paying.

## 1. Answer first

1. **What the standard is (Refract, 10-19 docs).** Many Target accounts, one task per account per product, each task on its
   own US residential proxy; in-bot Chromium harvesters on residential proxies banking ~3 Shape cookies per running task;
   ISP proxies only on the stock monitor; 3.5 s retry; "Hype Product ON"; keep submitting the order through the rate limit;
   aged accounts with a real Gmail and a real bank card each. Sizes: "Windows: 30-50 tasks ... Mac: 200+" (N3286-3287).
   The 09-30 "starter: 10 tasks, 2 harvesters, no proxies, home IP" tier was DELETED on 10-19; the current doctrine is
   residential at every size (N3268, N3346) because "Harvester proxies matter most. Tasks present the harvester proxy to
   Shape for add to cart and login" (N3349).
2. **Are we incorporating it?** Partly. We match the mechanics that matter (real Chrome, intercept-and-abort Shape harvest,
   LIFO replay, ~4 s re-shots, submit loop, ISP monitor, aged accounts). We do NOT match the two things the doctrine is
   built on: independent network lines per account (ours: 3 accounts on one home IP) and scale (3 vs 30-50). See §3.
3. **Sticky sessions.** Required for a buyer line: the Shape cookie, the login-session and the cart are presented from one
   IP for minutes to days. Every residential vendor sells sticky sessions by a username suffix; durations run from 10 min
   (Oxylabs default) to 24 h (Decodo) to 7 days (IPRoyal). Refract gives no Target sticky rule; its Walmart page says
   "sticky/static (not rotating), and US-based only" (N4440). See §4.
4. **Providers Refract suggests: none for Target, on purpose.** "We do not name proxy providers" (N304); the one exception
   is One Stop Proxies "for Best Buy only" (N314). Nothing public names a provider that carts Target in 2026.
5. **Cost.** A right-sized version of the standard for one household (3 accounts, each on its own residential sticky
   session, armed on drop nights only) is **about $40-90 per month** in proxy data; armed every night it is **$170-330**.
   The literal Refract Windows tier (30-50 accounts on residential, Refract licence) is **$1,999 up front + $130-330 per
   month**, and it is a reseller shape that Target's per-customer limit makes pointless for one address. See §5.
6. **What we are missing (ranked by evidence).** (a) Independent lines: every account on its own residential-class line
   (the only untested branch, C-1021-08/09; Friday's E1 measures wall 1 per line). (b) Cadence discipline: this morning's E2
   showed Shape flips a product to keyless-401 after about five admitted shots and keeps it flipped for 16+ minutes across
   profiles and lines; our fast cadence fires 16 shots per account per minute. (c) Device diversity: E2 cannot tell "global
   per product" from "per product x this PC's device fingerprint"; Refract's Mac doctrine and F5's 2026 "device
   intelligence" both say the device is scored. One run from any second device settles it before buying anything.
   (d) Bright Data residential is now KYC-gated to verified companies; the plan must use a vendor that sells to a person.

## 2. The standard, verbatim (Refract 10-19; Stellar and Valor where they differ)

| Component | Refract says (line) | Stellar / Valor |
|---|---|---|
| Scale | "**Windows: 30-50 tasks**, on 30-50 unique Target accounts, one task per account per product." "**Mac: 200+ tasks**" (N3286-3287). "10 tasks does not cut it anymore: Target is a submit order lottery, and only about 1% of carts turn into an order. The median running task count is 12" (N3498). "5-account setups do not work" (N3647) | Stellar: "one Target account per active Shipping or Pickup task" |
| Proxies, tasks + harvesters | "**Resis on tasks and harvesters, ISPs on the monitor.** At 30-50 tasks on Windows or 200+ on a Mac, use residential proxies on tasks and especially on harvesters" (N3268). "resis have been passing Shape far better than ISPs. A couple of local harvesters can generate on your home IP because it is already residential, but that is not a substitute" (N3346). "**Harvester proxies matter most.** Tasks present the harvester proxy to Shape for add to cart and login" (N3349). "Ideally, 1 task per proxy" (N1632) | Stellar: checkout proxies "clean, high-quality ISP or residential"; Valor: "Using proxies is highly recommended", "run unsaturated proxy pools" |
| Proxies, monitor | "Use ISP proxies on the monitor" (N3378); "at least 1 proxy per monitor input when running multi-SKU" (N3290) | Stellar monitor delay 8,000-15,000 ms |
| ISP vs residential (background page) | "Residential (resi): Rotates through a large pool of real home IPs. Harder to detect and ban. Per GB of bandwidth" / "ISP: Static datacenter IPs that present as home connections. Faster, but a flagged IP stays flagged. Per IP" (N288-289). "Target when Shape security is high" favours residential; "Target most of the time" runs fine on ISP (N295-296) | — |
| Harvest / cookies | "About 3 cookies banked per running task" (N3265); "Cookie Expiry (seconds): set between 300 and 600 ... most Shape cookies last a maximum of about 10 minutes" (N4035); "for around 90-120 cookies you will probably need about 6 browsers" (N3716); "Each protected request consumes one cookie" (N3672); "It does not matter what product you harvest on, as long as it is in stock and available for shipping" (N3489) | Stellar: "Cookies Per Task: Start with 3", expiry 300,000 ms |
| Shape scoring | "Think of the cookie as a test that has to score 90% or higher to pass": "Proxy trust score / Device trust score / Browser / How many cookies you are generating / The product's current security level" (N3669-3682). "**Accounts do not affect Shape blocks.**" (N3267). "Each product has its own security level based on how much traffic it is getting. The more a product gets spammed, the higher its security climbs" (N3684) | Stellar: "favors Edge, Brave, Firefox, and Safari over Chrome" (conflicts with Refract's Chrome+Brave) |
| Device | "1. Mac mini, local or in a datacenter (best for Shape, 200+ tasks) 2. Local Windows (30-50 tasks) 3. Windows server (avoid, heavily flagged)" (N3736-3738). "Macs share a common hardware fingerprint ... a Windows VM is the worst case" (N3740) | Stellar: "not recommend to use a server for ... Target" |
| Cadence | "Retry Delay 3500 (recommended)"; "Some users run as low as 1000, some as high as 4000" (N3425, N3292). "Be careful as too low delays will result in proxy/IP bans" (N1407) | Valor: "Rate limited ... is perfectly normal ... like a 'queue'" |
| Rate limit | "The response is a 429 and usually includes ... FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION / ERR_A2C_TCIN_RATE_LIMITED" (N3831-3834). "It is a site-wide traffic throttle" (N3824). "spamming submit order to force the order through the rate limit. Every bot has to brute-force it this way ... it is why the number of accounts you run matters so much" (N3838). "We do not know what percentage of requests Target allows through" (N3841) | Nothing public outside Refract mentions these strings (Shape researcher, two exact-string searches) |
| Login | "Target accounts log in with cookies"; "Task logins last days or weeks" (N3441, N3469); "Login with OTP ... pair it with IMAP" (N3436) | Valor: pre-login sessions, OTP sessions "around 4 hours" |
| Accounts / cancels | "Account age is the biggest factor" (N3862); "Use a real Gmail per account, never a catchall" (N3863); "Real bank cards stick far better than virtual cards, one card per account" (N3864); "Target ... links orders by more than the account alone: shipping address, payment method, and other identity signals" (N3854); "addresses need little to no jigging, and the one field worth varying per profile is the phone number" (N3865) | TCG Drop Radar (affiliate): "Buying more than 2 of any SKU. Target's anti-scalper logic cancels orders silently" |
| Checkout | "Hype Product ON for every Shape-protected (high-demand) drop" (N3258); $35 free-shipping filler "cancels JUST THE LEGO after checkout" (N3437); price cap whole dollars (N3392) | — |
| Price | Renewal "$50 / mo" (N484); refractbot.com 10-22: "$1,999" up front for the first 3 months, then $50/mo, "+$50/mo per device" | Stellar $499 first quarter then $150/quarter; Valor $1,999.99 + $49.99/mo (waitlist); Hidden AIO $499.99 first term (waitlist) |

What the docs do NOT say (negative results): a provider for Target; a GB budget for Target (only Walmart: "2-4 GB per 200
tasks per drop", N4441); sticky vs rotating for Target; what Hype Product changes on the wire; any limiter key beyond
"site-wide"; a Target task cap; CVV handling; anything Pokémon-specific.

## 3. Us vs the standard — the gap table

| Component | Standard | Ours (bat as of 10-21, CURRENT_STATE) | Gap | Effect on units | Cost to close |
|---|---|---|---|---|---|
| Lines per account | 1 task : 1 residential proxy; harvester proxy = task proxy | 3 accounts, 1 home IP (Comcast), no proxy on buyers or harvest | **The binding gap.** Accounts on one IP pass or fail the limiter together (C-1015-01 VERIFIED); within-race on 09-15 the verdict followed the line (C-1021-08) | Wall 1 draws scale with independent lines (if the line is in the key: E1 Friday); wall 2 trust is per line | $13-30/account/month data (§5) |
| Scale | 30-50 accounts (Windows) | 3 aged accounts, 1 address, 1 person | A reseller shape; Target's per-customer limit links by address/payment (N3854), so more accounts at one address earn cancels, not units | ~0 for a one-household buyer | out of boundary beyond 1 account per proven line |
| Harvest | In-bot Chromium harvesters on resi, ~3 cookies/task, 300-600 s TTL, 6 browsers per ~100 cookies | In-account Chrome harvest, bank 6/account, TTL 300 s, 40 s interval, intercept-and-abort, LIFO replay | Matched in mechanics; ours harvests on the (residential) home line | — | none |
| Cadence | 3,500 ms retry; "too low delays will result in proxy/IP bans" | `TARGET_FAST_CADENCE_IDENTS=all` → 3.9 s re-shots, 16 shots/account/min, 3 accounts on one line = ~48/min on one product | **E2 today: a product flips to keyless 401 after ~5 admitted shots and stays flipped 16+ min for every profile and line we tried.** Fast cadence buys nothing at wall 1 (0/19 races, C-1021-06) and burns wall 2 | Negative at wall 2 whenever anything passes wall 1 | $0: revert to the 55-70 s re-entry (the 09-11..10-09 config) |
| Device | Mac best; local Windows OK at 30-50; "device trust score" is a Shape input | One Windows 11 desktop; every account, profile and guest arm shares its hardware fingerprint | E2 cannot separate "global per product" from "per product x device". If per device, a second device gets its own admits | Unknown until one run from another device | $0 test (any laptop/phone browser); Mac mini M6 $899 one-time [REPORTED] |
| Monitor | ISP proxies, 1 per input | 18 Bright Data ISP exits, apps_raw, 3 sweeps/s, 0 403/429 for weeks | Matched; ours is faster | — | none |
| Submit loop | "keep submitting ... let it ride" | Won-cart loop (caps 40 tickets, F1 exemption armed, RTO) | Matched; never exercised since 10-01 (no cart) | — | none |
| Login | Cookie logins lasting days/weeks; OTP + IMAP option | Login-sessions 20-28 d, scripted forced login 4/4 since 10-14; no IMAP/OTP | Equivalent for 3 accounts | — | none |
| Hype toggle | Hype Product ON | Fast lane + Shape headers on every hot shot | Equivalent (we do not know what the toggle changes on the wire) | — | — |
| $35 filler | Add Extra Product for items under $35 | None (qty 2 of a $15-20 tin can fall under $35; Target then charges shipping, it does not block) | Minor; our orders have shipped before | — | optional |
| Sticky sessions | Required (Walmart page explicit) | n/a today; the follower (`--proxy`) and the bot (`proxy_url` → forwarder) both pin whatever session the URL carries | Must use a `session`-pinned URL with a lifetime that covers login + drop | — | — |

## 4. Proxies: types, sticky mechanics, vendors, KYC, prices (read 2026-10-22, [REPORTED])

**Types.** Residential = rotating pool of real home/mobile IPs, sold per GB, sticky by session id. ISP ("static
residential") = IPs registered to an ISP but hosted in a datacenter, sold per IP, unlimited or fair-use bandwidth; "a
flagged IP stays flagged" (N289). Our 18 monitor exits and both reserve exits are Bright Data ISP. F5 Labs on
residential proxies: "Residential IP proxies have become a must-have for bot operators"; non-residential "cloud hosting,
TOR exit nodes, or VPS networks may be flagged as suspicious" (2025 report). Our own data: in the same September races,
home (true residential) was admitted by Shape 20/28 past-limiter shots while Bright Data ISP member shots were refused
97/97 (C-1021-08).

**Sticky mechanics.** Username suffix pins a peer; if the peer drops, most vendors silently substitute a new IP unless a
fail-closed flag is set (Bright Data `-const` → 502; Oxylabs `sessid_oneip` → 502; IPRoyal `_killswitch-1` → 410).
Durations: Bright Data idle 5 min; Oxylabs default 10 min, `sesstime` up to 1,440 min; Decodo default 10 min, up to 24 h;
IPRoyal `_lifetime-` up to 7 days; Rayobyte up to 120 min; PacketStream 60 min. A static ISP IP has no peer to drop.

| Vendor | Residential price (small) | Sticky | Targeting | Gate | Notes |
|---|---|---|---|---|---|
| **Bright Data** (our account) | PAYG shown $8 → $4/GB promo 3 mo; $499/141 GB | `-session-<id>`, idle 5 min | country/state/city/zip/ASN | **KYC: residential "available only to verified companies"; personal-email accounts "not eligible"; "Personal projects ... not approved"** (zones after 2026-07-07) | ISP needs no KYC: 10 shared IPs $18/mo, dedicated $3.5/IP |
| **Decodo** (ex-Smartproxy) | PAYG $4/GB; 3 GB $11.25; 10 GB $35; 25 GB $81; 100 GB $275 | 1/10/30/60 min, custom to 24 h | continent/country/state/city/zip/ASN | none stated | 14-day money-back on ISP; ISP dedicated 10 IPs $29 |
| **IPRoyal** | 1 GB $7; 2 GB $5.95/GB; 10 GB $5.25/GB; traffic never expires | `_session-<8>_lifetime-<n>` up to 7 d; `_killswitch-1` | country/state/city | "KYC policy exists" | ISP $2.40-2.70/IP |
| **Oxylabs** | $6/GB to 5 GB, $5/GB to 20 GB; Starter $30/mo | `-sessid-`, `sesstime` to 1,440 min | country/state/city/zip/ASN | KYC for restricted targets | **ISP $1.5/IP, carriers "Lumen, Comcast, BT, Orange, Cox"**; shared with up to 3 users |
| **Rayobyte** | $3.50/GB PAYG | up to 120 min | country/state/city/zip/ASN | "unusual use triggers ID checks" | ISP $5/IP |
| **DataImpulse** | $1/GB; 5 GB for $5 | 30-120 min | country; others extra | none stated | quality unknown |
| **PacketStream** | $1/GB, $50 minimum | 60 min | country only | none stated | peer-to-peer app network |
| SOAX | Sandbox $5/GB (US) | yes, duration not stated | country/region/city/ISP | business verification above sandbox | |
| Webshare | $3.50/GB; 10 GB $2.75/GB | not described | country/state/city/zip/ASN | "some websites restricted" | |
| NetNut | — | — | — | **domains seized by the FBI 2026-07-02** | do not use |
| Oculus | — | — | — | ISP page: "web scraping only ... excludes e-commerce purchases" | do not use |
| Hype Proxies (sneaker ISP) | — | static | Verizon / AT&T / Astound, Ashburn + Dallas | — | 50 IPs $65/mo |

Community reports on which type carts Pokémon at Target in fall 2026: **found nothing** (Reddit/X/Discord unreachable to
the research tools). Affiliate pages: "residential or ISP proxies ... essentially required", "datacenter proxies are
largely ineffective on Target" (cook-groups.com, 2026-07-07). Nothing public says Target or F5 block Bright Data or
Oxylabs ASNs.

## 5. Monthly cost [INFERRED unless a price is quoted]

Data per account per armed night: a cold PDP 2.5-4 MB, a harvest cycle 0.5-1.5 MB, cycles at our 40 s interval ≈ 90/h,
1,000 add-to-cart POSTs ≈ 3-20 MB. Over a 7.5 h armed band (23:15-06:30): **~0.35-1.0 GB per account per night**, harvest
page loads dominate. If the account Chrome stayed on the residential line 24 h, 1.1-3.2 GB/day — which is why the design
below arms residential only for the armed band (the bot restarts nightly anyway) or on drop nights only.

| Setup | What runs | Recurring per month | One-time |
|---|---|---|---|
| **R1 — right-sized standard** (recommended first) | 3 existing accounts, each on its own US residential sticky session for the armed band on drop nights (~8/month); home line stays the 4th guest/follower line; monitor unchanged | 3 × 8 × 0.5 GB = 12 GB ≈ **$42** (Decodo $35/10 GB + PAYG) to **$63** (IPRoyal $5.25/GB); + $0 licence (own bot) | $0 |
| R1 every night | same, armed all 30 nights | 3 × 30 × 0.5 GB = 45 GB ≈ **$125** (Decodo 50 GB $150 tier) to **$235** (IPRoyal) | $0 |
| R2 — + a Comcast/Cox-ASN ISP line per account as the always-on carrier (harvest 24 h on ISP, residential only in-window) | needs a test that ISP-Comcast clears Shape better than Bright Data ISP did (it may not) | Oxylabs ISP 3 × $1.5 = **$4.50** + R1 data | $0 |
| R3 — second device | one run of E2 from another machine; if "per device", a Mac mini M6 as a second buyer host | $0 | **$899** (Mac mini M6 16 GB, [REPORTED] macworld 2026-09-22) |
| Literal Refract Windows tier | Refract licence + 30-50 accounts on residential + 6-10 harvesters | $50 licence + 56-180 GB ≈ **$130-330**; accounts need 30-50 real Gmail + real cards + addresses, which Target links and cancels (N3854) | **$1,999** up front (refractbot.com 10-22) |

Sunk: 18 Bright Data ISP monitor exits (unchanged). Not costed: accounts (one person, one address: the per-customer limit
caps useful orders per drop at the limit regardless of account count).

## 6. What our own data add (10-21 post-run + E2 today)

- Wall 1 (edge limiter) is where every unit since 10-02 died: 686/690 on 10-09, 1,208/1,208 on 10-16, all home line. More
  accounts on that line add ~nothing (C-1015-01). The line-vs-TCIN-global key is untested in-window; Friday's E1 (home
  guest vs Bright Data guests) measures it. Residential-trust vs ISP at wall 1: no own data.
- Wall 2 (Shape keyless 401), E2 this morning [MEASURED, logs/analysis_2026_10_22/e2/, verifier pending]: arm A (home,
  5 s cadence, OOS hot TCIN) 5 × 424 admitted then 15 × 401 from shot 6 at 27 s; arm B (home, 60 s cadence, second TCIN)
  6/6 admitted; arm C (fresh profile, home, TCIN A) 401 from shot 1, 11 min later; arm D (fresh profile, Bright Data
  31.105.x) 401 from shot 1; arm G (fresh profile, Bright Data 168.158.x) 401 from shot 1; arm E (fresh profile, home,
  TCIN B, 12 min after B) 401 from shot 1 and its control decoy 401 once (controls 11/12 admitted overall). Reading: the
  401 state is **per product**, raised after ~5 admitted shots, persistent ≥16 min, not scoped to profile or line —
  either global per product or per product × this machine's device fingerprint (one PC cannot separate them). Refract's
  own sentence: "The more a product gets spammed, the higher its security climbs" (N3684). The 10-01 member switch (6
  admits then 70/70 401 across all three home accounts, C-1001-12) is the same shape in-window.
- Consequence for cadence: 48 member shots per minute on one product from one line raises its level inside the first
  seconds of every window; the 55-70 s re-entry of the 09-11..10-09 era stayed under it (arm B). Fast cadence has no
  measured wall-1 benefit (0/19 races vs 4/136, p=1.0). → revert for Friday as the alternating-night arm (the post-run's
  option b), with the volley readout pre-registered.
- Consequence for the "ISP not good enough" belief: BD member shots were refused 97/97 in-window in September while home
  was admitted 20/28 in the same races — that is per-line trust at a high product level, which is exactly Refract's
  "resis have been passing Shape far better than ISPs". Off-window a fresh BD guest page passes Shape 6 × before the
  product flips (C-1021-01), so the line is not blacklisted; it scores lower when the product is hot.

## 7. Recommendation and order of operations (take-control plan)

**Buy (operator, today):** one residential plan that sells to a person and supports long sticky sessions — Decodo 10 GB
($35, sessions to 24 h, state/city targeting) or IPRoyal 10 GB ($52.50, sessions to 7 days, `_killswitch-1`). US only,
state = our state. Skip Bright Data residential unless the account can pass company KYC. Do not buy ISP IPs for buyers
yet (September data says ISP lines score low at wall 2); do not buy a Mac before the second-device test.

**Thursday (today, main session):** E2 persistence arm F (~10:35: is the product still flipped 50 min later?); verifier
on the E2 claim; revert `TARGET_FAST_CADENCE_IDENTS` to empty with the wrapper stopped (bot is down since 07:52), offline
suite, manifest; when the residential URL is in `config/proxyIps.json` `residential_proxies[0]`: guest smoke
`--tag resi --lock-name guest_follow_resi --proxy resi:0`, then the operator registers the resi follower task beside bd0/bd1.

**Friday 02:00 CT (E1):** home guest + bd0 + bd1 (+ resi) followers in-window; members on the home line with the slow
cadence. Readout per tag: LINE-DIFFERS → lines are the lever; wall 2 per tag: which line class Shape admits in-window.

**After Friday:** if any non-home line passed wall 1 where home did not, move alt-1 onto that line class (E3: `proxy_url`
= the sticky residential URL, daytime boot proof, primary + business as controls) for the next drop; add one more
residential line per existing account after that. If every line failed wall 1 together, the key is TCIN-global or per
device: run E2 from a second device before any further network spend.

**Not recommended:** more accounts at this address (Target links them: N3854); Bright Data ISP as buyer lines (97/97);
Windows server / VM; buying Refract ($1,999 + $50/mo) to run a 3-account setup its own docs call "does not work".

## 8. Unknowns that would change the plan

- Whether the edge limiter keys on the line (E1 Friday). If TCIN-global, network spend buys wall-2 trust only.
- Whether Shape's product escalation is global or per device (one run from another device).
- Whether a login-session survives an IP change (sticky residential IPs rotate within days). Daytime boot proof answers it.
- Real data use per account per night (measure on the first resi night: the forwarder can count bytes).
- Whether ISP IPs in a consumer ASN (Comcast/Cox via Oxylabs) score like home at wall 2 — a $1.50 test.

## 9. Sources

Refract corpus 10-19 (`logs/analysis_2026_10_19/outside/refract_llms_full_2026_10_19.txt`, lines cited above) and 09-30
diff; Stellar corpus (`stellar_llms_full_p1_2026_10_19.txt`); refractbot.com, valoraio.com, whop.com (10-22); vendor
pricing pages: brightdata.com/pricing/proxy-network/residential-proxies, docs.brightdata.com/proxy-networks/residential/
network-access, decodo.com/proxies/residential-proxies/pricing, iproyal.com/residential-proxies, oxylabs.io/products/
isp-proxies, rayobyte.com, dataimpulse.com, packetstream.io, soax.com/pricing, webshare.io/residential-proxy;
krebsonsecurity.com (NetNut seizure, 2026-07-02); F5: docs.cloud.f5.com security-events-reference, malicious-users,
plan-bot-defense; f5.com/labs 2025 Advanced Persistent Bots Report; F5 press release 2026-09-15 (device intelligence);
greynoise.io residential-proxy abuse report (2026-04-02); cook-groups.com, unknownproxies.com, tcgdr playbook
(affiliate, low weight); our logs: `logs/analysis_2026_10_22/e2/`, `logs/analysis_2026_10_21/postrun/`, `docs/CLAIMS.md`.
