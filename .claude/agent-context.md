# Agent context — read this first, every time

Shared doctrine for every sub-agent working on this repo. Your agent definition
points you here so these rules live in one place. Read this file before you touch
anything else.

---

## 1. HARD SAFETY RULES — violating one of these can cost real money

This repo drives a **live retail purchasing bot that spends real money on a real
credit card**. It is not a simulation.

- **NEVER launch the bot.** Not `app.py`, not `test_app.py`, not
  `run_bot_with_nightly_restart.bat`, not any Chrome/zendriver/nodriver process,
  not any live-checkout path. Launching is the **user's** decision, always, and
  they must do it themselves. If you believe a run is needed, say so and stop.
- **NEVER blanket-run `tests/`.** That directory contains live tests that launch
  browsers and can fire real purchases. The **only** sanctioned test command is:
  ```
  python tests/run_offline_suite.py      # 26 files, ~3.5 min — this is THE gate
  ```
  Never `pytest tests/`. Never `pytest tests/ -k something`. Never a whole-dir run.
- **NEVER run anything in `tools/analysis/` without being explicitly told to.**
  Those scripts are read-only readouts, but they are slow and they are
  pre-registered per investigation (see §4). Read their source to learn what was
  measured; run them only on instruction.
- **Default to read-only.** Unless your task explicitly says "edit" or "write",
  you are reading and reporting. Do not "helpfully" fix something you noticed.
- **Never start a harvester or a login flow.** Account logins are hand-done by the
  user before a drop window. A scripted re-login is burned (~0/25) and wastes a
  session that takes the user real effort to restore.

## 2. EPISTEMICS — this project runs on a claims ledger, not vibes

`docs/CLAIMS.md` is the ledger. This project has repeatedly shipped fixes based on
a misread log and then had to correct them, so tagging is not bureaucracy — it is
the thing that keeps the work honest.

Tag **every** factual claim you make with exactly one of:

| Tag | Means |
|---|---|
| `[MEASURED]` | You computed it from real run data, and you can name the file and the sample size |
| `[REPORTED]` | A doc or a vendor asserts it; you did not verify it |
| `[INFERRED]` | You reasoned it from code you read; no runtime evidence |
| `[NOT ESTABLISHED]` | Asserted somewhere but unproven, or measured with a confound |
| `[REFUTED]` | Tested and disproven — say what killed it |

Rules that follow from this:

- **Always give the sample size.** "1.6% cart rate" is not a finding. "1.6% cart
  rate (38/2,317 shots, 09-16 + 09-17)" is.
- **Never launder a caveated number into a clean one.** If the source flags an
  era-confound or a small n, that warning travels with the number to the reader.
- **Cite `path:line` for every code claim.** You will be spot-checked.
- **Distinguish "in the code" from "armed in production".** A flag can default ON
  in code and be OFF in `run_bot_with_nightly_restart.bat`, or exist and never be
  set at all. Production truth lives in the .bat. Always check it before claiming
  a behaviour is live.
- **Say "I could not determine this."** An honest gap is worth more than a
  confident guess. Guesses have cost this project weeks.

### 2B. BEFORE YOU PROPOSE A LEVER — four checks, each of which killed an
### "obvious" fix on 2026-09-21

Every one of these looked right on paper and was wrong in the data. Run them
before you hand the orchestrator a recommendation.

1. **Is the lever actually the binding constraint?** `TARGET_ATC_DCO_BURST_MAX=2`
   looked like a cheap win — raise the cap, get more shots. The 09-18 readout
   showed `caps=0`: the cap had never once been reached. Six of seven bursts
   ended because the next shot drew an edge-429. Raising it would have done
   **nothing**. Always measure whether the limit you want to raise is being hit.

2. **An audit's FINDING and its RECOMMENDATION are different artifacts.** The
   09-21 parity audit correctly found that one account fires per TCIN, then
   recommended `TARGET_MULTI_SKU_CAP_ALWAYS=0`. Tracing the dispatch gates showed
   that flag would make the first live TCIN take all three workers and starve
   every other one — the exact bug the flag was armed to fix. The real dial was a
   different variable entirely. **Re-derive the mechanism before arming a
   recommendation, even one from a verified audit.**

3. **Check the EFFECT of a restore, not its intent.** Restoring a 16-IP proxy
   backup verbatim silently demoted two currently-live exits into
   `reserve_proxies`, a key nothing reads. The diff looked like "8 -> 16". The
   effect was "8 -> 16, minus 2 working exits." Diff what the consumer actually
   reads.

4. **Dump the real schema before parsing it.** A verdict table built on guessed
   field names (`total_200`, `ok_count`) reported **all 20 proxies dead** when
   every one was healthy — the real field was `total_success`. A contradiction in
   your own output (`last_status=200` next to `successes=0`) means your parser is
   wrong, not the world. Print one raw record before you tabulate 20.

**Corollary on selection effects.** Two of this project's load-bearing claims rest
on comparing shot populations. A shot only exists at index k because the shot at
k-1 failed, and the retry loop breaks on success
(`bulletproof_purchase_manager.py:2233`). Any "more shots did worse" finding is
conditioned on prior failure. That confound **destroyed** the volume claim
(11-window cells, non-monotonic) and **did not** destroy the 09-09 cadence census
(n=21,684, monotonic 6.1 -> 1.4 -> 0.4 -> 0.0, p=1.4e-54). Sample size and
monotonicity are what separate them — check both before you reach for the
confound.

**Operational note:** long background Python must run with `python -u`, or its
stdout sits in a pipe buffer and you are blind to progress for the whole run.

## 3. THE REPO MAP

Target is the only retailer. Walmart was deleted 2026-09-21 (commit `9289edbf`);
if you find a Walmart reference, it is stale — say so, do not follow it.

**Entry points**
```
app.py                            Target prod entry (203 KB — GREP IT, never read whole)
run_bot_with_nightly_restart.bat  production wrapper — ARMS ALL ENV FLAGS (93 KB)
test_app.py                       test env, stops before the real purchase
test_resilient_stack.py           stock-check smoke test
```

**Source — note the sizes, these are grep targets not read targets**
```
src/session/purchase_executor.py           10,538 lines  Target ATC, Shape headers
src/purchasing/bulletproof_purchase_manager.py  4,301   Target checkout
src/session/session_manager.py              2,679
src/monitoring/stock_check_resilient.py     1,389       current stock pipeline
src/session/multi_session_pool.py             787
src/session/shape_harvest.py                  712       Shape credential minting
src/monitoring/tab_dispatcher.py              428       browser-native dispatch
src/purchasing/identity_rest.py               406
src/session/account_identity.py               397
src/proxy/local_forwarder.py                  380
src/session/cookie_harvester.py                         DEPRECATED (Round 1 curl_cffi)
```

**Docs — by task**
```
docs/CLAIMS.md                    the claims ledger — start here for "is this proven?"
docs/FAILURES.md                  141 KB failure log — GREP, never read whole
docs/HOT_SKU_FIX_2026_09_16.md    the hot-SKU investigation
docs/RESILIENT_STACK.md           stock-check architecture
docs/ANTIBOT.md                   Shape / HUMAN-PX
docs/FLOW.md, docs/RETAILERS/target.md    purchase flow
docs/TARGET_ARCHITECTURE_SPEC.md  the build plan
docs/REFRACT_PARITY.md            competitor comparison
logs/analysis_2026_09_21/research/refract_llms_full_2026_09_21.txt
                                  competitor's COMPLETE public doc corpus, 6,040 lines
```

**Config / state**
```
config/product_config.json     armed TCINs
config/target_accounts.json    accounts
config/proxyIps.json           UNTRACKED — live Bright Data creds, never print or commit
state/proxy_state.json, state/session_profiles/
```

**Forbidden paths:** `__pycache__/`, `.git/`, `venv/`, `node_modules/`, `dist/`,
`.pytest_cache/`. Never load the whole repo.

## 4. THE READOUT SCRIPTS — `tools/analysis/`

Pre-registered, read-only analyses over the run logs. **Read their source to learn
what has already been measured** before proposing a new measurement — the answer is
often already computed. Run them only when told to.

```
funnel.py               stage-by-stage attrition
shots.py                shot-level extraction
limiter_key.py          what key Target's edge limiter buckets on
edge_pass_by_gap.py     pass rate vs gap since last shot
shot_index_yield.py     yield by shot index within a window
throughput_vs_volume.py total passes per window vs shot volume
home_vs_proxied.py      home IP vs Bright Data exits
census_density.py       shot density census
dco_next_shot.py        behaviour after a DCO_RATE_LIMITED
checkout_events.py      checkout-stage events
windows.py              stock-window segmentation
exit_eras.py            proxy-exit era segmentation
readout_multi_sku.py    multi-SKU dispatch readout
readout_next_drop.py    the next-drop readout
```

## 5. DOMAIN GLOSSARY — get these right

- **Shape** — F5 Shape, Target's primary anti-bot. Protects add-to-cart and login.
  **Target also runs HUMAN/PerimeterX** ("Press & Hold"). **Never call Target's
  anti-bot "Akamai"** — that is a different vendor and the user will correct you.
- **TCIN** — Target's product id. The monitor input.
- **Hot / hype SKU** — a high-demand Shape-protected product (Pokemon cards,
  consoles). **Ordinary SKU** — everything else. Any analysis that mixes the two is
  confounded; always segment. (For what the bot currently does on each, read
  `.claude/state/CURRENT_STATE.md` — do not assume, and do not carry a belief
  about this forward from an older session.)
- **RedSky** — Target's product/stock API, used by the monitor.
- **Edge limiter** — Target's front-door rate limiter. Returns 429, often with
  `FAST_SELLING_ITEM_RATE_LIMIT_EXCEPTION` or `ERR_A2C_TCIN_RATE_LIMITED`.
- **ATC 401** — contested. This repo's 2026-07-10 conclusion is the write-auth /
  token layer; the competitor's docs call a 401 a Shape block. **Unresolved — do
  not assert either side as settled.**
- **Wave-first** — current cadence policy: after an ATC-level failure, sleep
  ~55-70 s before a cold re-entry, rather than retrying fast.
- **Bank** — the store of harvested Shape credentials.
- **BD** — Bright Data ISP proxies. **Home IP** — the user's own residential line,
  which currently carries the buyer accounts.

## 6. STALENESS PROTOCOL — this file is doctrine, not data

**This file contains METHOD. It deliberately contains almost no measured facts,
because facts here rot silently and method does not.**

Live facts live in exactly one place: **`.claude/state/CURRENT_STATE.md`**, where
every line carries an as-of date and a source. Read it at the start of any task
that depends on the bot's current behaviour.

Rules, and they are not optional:

1. **Check the as-of date on every fact before you rely on it.** Compare it against
   `git log -1 --format=%cd` and the mtime of `run_bot_with_nightly_restart.bat`
   and `config/product_config.json`. A fact measured before the most recent change
   to the thing it describes is `[NOT ESTABLISHED]` until re-derived — regardless
   of how confidently the source states it, and regardless of which document it
   appears in.
2. **Re-derive rather than inherit.** If a conclusion is load-bearing for what you
   are about to recommend, measure it again from primary evidence. Do not carry a
   belief forward because a doc, a memory, a code comment, or a previous agent
   asserted it.
3. **Era-stamp every claim you make.** "The bot converts on ordinary SKUs" is not a
   claim; "the bot converted on ordinary SKUs at 15.6% at the stock edge between
   07-23 and 08-04, and 0% after 08-06" is. Undated claims are how this project has
   repeatedly acted on facts that had already expired.
4. **When you discover that a recorded fact is now wrong, say so explicitly and
   prominently**, and name the file and line that carries the stale version. That
   correction is a first-class deliverable, not an aside. Silently working around a
   stale fact leaves it in place to mislead the next agent.
5. **Feature-armed dates matter.** A mechanism only explains outcomes that occurred
   after it was armed. Before attributing any result to a feature, establish when
   it was first armed in the wrapper (`git log -p -- run_bot_with_nightly_restart.bat`)
   and check that the result postdates it.

File sizes and line counts quoted anywhere in this file are indicative only —
verify with `wc -l` before deciding whether to grep or read.

## 7. HOUSE STYLE FOR YOUR REPORT

- Lead with the answer. The orchestrator reads the first paragraph closely and
  skims the rest; do not bury the finding under methodology.
- Tables over prose for anything with more than three comparable items.
- `path:line` on every code claim; a tag on every factual claim.
- **Report negative results.** "I looked for X and it does not exist" is a real
  finding here and has repeatedly been the most valuable thing an agent returned.
- **Do not recommend changes unless your task asked for recommendations.** The
  orchestrator synthesises; you supply verified material.
- Do not flatter, do not editorialise, do not pad. If the answer is three lines,
  write three lines.
