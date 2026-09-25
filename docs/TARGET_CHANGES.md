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

---

## Known-unknowns about the opponent

Things we would need to see their side to answer. Listed so they are not
re-litigated from first principles every few weeks:

- Whether one client's own retry rate lowers its admission odds at the high-demand
  limiter (the wave-first premise). Our hot-SKU data cannot settle it either way
  (docs/CLAIMS.md C-0923-02, 2026-09-23); being tested live from 09-23 with
  TARGET_WAVE_FIRST_EDGE=0 and a pre-registered kill rule. 09-23 also showed
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
