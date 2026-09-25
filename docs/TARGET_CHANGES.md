# TARGET_CHANGES — the adversary's changelog

**Target is a moving opponent. This file tracks what *they* changed, separately
from what *we* changed.** `docs/FAILURES.md` records our failures;
`docs/CLAIMS.md` records our beliefs. Neither records the opponent's moves, and
conflating "our fix stopped working" with "their defense changed" has cost this
project weeks.

## How to use this file

- **Append an entry whenever behaviour changes and our code did not.** A step
  change in pass rate, a new error string, a new status code, a challenge that
  did not exist before, a rate limit that behaves differently.
- **Every entry carries a date, the evidence, and a confidence tag.** Suspected is
  fine — say suspected. An undated suspicion is worthless; a dated one is a lead.
- **Record the date we NOTICED separately from the date it CHANGED.** The gap
  between those two numbers is the thing to drive down. It was 6 weeks once.
- When an entry is later explained or refuted, edit it in place and say so.

Entry format:

```
### YYYY-MM-DD — short name
- **Changed:** what their side started doing
- **Noticed:** date we spotted it, and the detection gap
- **Evidence:** numbers with n, and the file
- **Our response:** what we did, or "none"
- **Confidence:** [MEASURED] / [SUSPECTED] / [REFUTED]
```

---

## Entries

### 2026-08-06 — the conversion collapse
- **Changed:** unknown. Conversion went to zero across **both** SKU classes with no
  change to our code path.
- **Noticed:** 2026-09-20 via `tools/analysis/funnel.py` era split.
  **Detection gap: ~6 weeks.** This is the single most expensive miss in the
  project's history and the reason the regime watch in `/post-run` exists.
- **Evidence:** ordinary SKUs at the stock edge 19/122 (15.6%) before vs 0/135
  (0.0%) after; Fisher p = 3.2e-07. Whole LOSE era: 2,273 ordinary chains → 1 cart,
  0 orders. Last order of any kind: 2026-08-04. [MEASURED 09-20]
- **Candidate causes, none confirmed:**
  - Two of five Bright Data exits (AS20012, Chiller City Corp, a colo) **entered
    service 2026-08-07** — one day into the window. [REGISTRY 09-20]
  - The armed target list shifted toward hyped TCINs over the same period.
  - A Target-side scoring change we have no visibility into.
- **Our response:** buyers moved to the home IP (`679275d9`, 09-20). UNPROVEN LIVE.
- **Confidence:** [MEASURED] that it happened; [NOT ESTABLISHED] why.
- ⚠️ Under adversarial verification as of 09-21 — the open attack is whether the
  hot/ordinary classification is applied retroactively.

### 2026-09-07 — HUMAN/PerimeterX "Press & Hold" appears on top of Shape
- **Changed:** a second anti-bot vendor's challenge began appearing on the RedSky
  path, in addition to F5 Shape.
- **Noticed:** same day.
- **Evidence:** `docs/HUMAN_PX_DIAGNOSIS_2026_09_08.md`.
- **Our response:** captcha park; `TARGET_HARVEST_MISS_PROBE` + PX park armed.
- **Confidence:** [MEASURED]

### 2026-09-21 — pool-wide 429 tarpit on the monitor
- **Changed:** a 43-minute window (03:21-04:04) in which the entire IP pool took
  429s on the sweep.
- **Noticed:** next morning — and only because someone looked. **429 had no handler
  anywhere in the sweep**, so it fell into an `other` bucket and was invisible.
- **Evidence:** ~99% of that run's loss in one window. [MEASURED 09-21]
- **Our response:** `551b1807` split 429 out, log-only. No backoff, deliberately.
- **Confidence:** [MEASURED]

### 2026-09-25 — RedSky answers the monitor's bulk read with HTTP 206 in bursts
- **Changed:** from 02:10 to 04:49 CDT, 17 bursts of 1.5-18 min in which 40-95% of
  monitor reads failed (63.9% inside bursts, 8,563/13,402); whole-run loss 11.8%
  (8,969/75,799) vs 0.05-0.10% on 09-22/09-23. The ground-truth reads, same function
  and URL as the sweep, failed 94x with **HTTP 206** (1-3 a night on 13 earlier runs).
  Every exit subnet was hit (17 exits on two /16s at 63-68%, the lone third /16 at 29%).
  Target-side: the no-proxy canary saw 206 in June, and single-TCIN VERIFY reads got
  206 too, so it is not one bad TCIN in the batch. Clean after 04:50.
- **Noticed:** live, within ~1 min (02:11), by the session's log watcher. Detection
  gap ~1 min.
- **Cost:** zero tonight: all 8 stock windows fell in clean gaps. Inside a burst the
  read age reached 25 s (p90 ~8 s), so a burst over a stock flip would have cost seconds.
- **Evidence:** docs/CLAIMS.md C-0925-03; `logs/analysis_2026_09_25/`.
  [MEASURED for the ground-truth 206s; INFERRED that the sweep's `other` is 206 — the
  sweep does not log per-request status]
- **Our response:** the bot read every 206 body and threw it away, so its shape is
  unknown. `RESILIENT_206_LOG=1` (armed 2026-09-25) logs one summary a minute. An
  ingest is deliberately NOT built yet: today's parser reads missing fulfillment as out
  of stock, so a naive ingest could mark everything out of stock.
- **Confidence:** [MEASURED] that it happened; cause [NOT ESTABLISHED].

### 2026-09-25 — member-token re-mint stops working for every account
- **Changed:** the bot's token repair minted a fresh member token 5/5 times
  00:48-03:54. From 06:01 every attempt failed on all three accounts
  (`could NOT mint ... present=False`), including a session only 42 min old. The
  signature is new: 4,241 of 4,368 earlier failure lines were `present=True
  member=False` on one rotted account while the others minted fine. The narrowest
  window for the change is 03:54-06:01 (no attempt in between).
- **Noticed:** live, 06:01, by the log watcher. Gap ~0.
- **What it is not:** Target did not kill the sessions — its cart service kept
  accepting each token right up to the moment the BOT'S OWN repair deleted it (the
  repair deletes the token before a replacement exists). [REFUTED: "sessions killed"]
- **Evidence:** docs/CLAIMS.md C-0925-04; `logs/runs/run_20260925_004139.log`.
- **Our response:** see C-0925-04 (repair safety) — the bot turned a mint outage into
  dead accounts. Target's answer to the mint request itself has never been logged.
- **Confidence:** [MEASURED] that mints stopped; why [NOT ESTABLISHED] — a Target-side
  change to the mint path vs re-mint disabled for these accounts or this IP after the
  drop's volume. The deciding observation is the mint request's own response.

---

## Known-unknowns about the opponent

Things we would need to see their side to answer. Listed so they are not
re-litigated from first principles every few weeks:

- Whether one client's own retry rate lowers its admission odds at the high-demand
  limiter (the wave-first premise). Our hot-SKU data cannot settle it either way
  (docs/CLAIMS.md C-0923-02, 2026-09-23). **Tested live on 09-25**
  (TARGET_WAVE_FIRST_EDGE=0, 2.7 s median re-shot gap): 0 of 663 re-shots at window
  age 2-120 s were admitted; all 5 admissions were window-opening shots fired within
  0.1 s of the flip (first shots 5/20 vs 1/10 on 09-23, p=0.33 — no change). So retries
  bought no admissions at either cadence tried; whether they COST anything is still
  unmeasured. The kill rule tripped and the flag went back to 1 (C-0925-01). 09-23 also showed
  9 of 10 window-opening shots rejected after 24-47 min of zero fleet shots on the
  TCIN -- not what an idle-refilled per-account bucket predicts.
- What fraction of requests the high-demand rate limiter admits. Their own vendor
  docs say nobody outside Target knows.
- Whether a Target ATC 401 is a Shape block or the write-auth layer. Unresolved,
  and it decides where credential effort goes.
- Whether the edge limiter scores IP reputation or simply favours lowest latency.
  Confounded in all existing data.
- Whether Bright Data ASNs are specifically scored by Target/F5. No public evidence
  in either direction.
