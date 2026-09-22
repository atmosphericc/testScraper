---
name: post-run
description: Post-mortem the most recent bot run end to end, then fix what it found. Fans out log forensics, purchase-chain and anti-bot analysis in parallel, verifies every finding from a fresh context, and lands flag-gated fixes behind the offline suite. Use after every drop, restock or overnight run.
argument-hint: "[run date or log path, optional]"
arguments: run-target
---

# Post-run: find everything that went wrong, then fix it

Target run: `$run-target` — if blank, use the most recent run in `logs/`.

**Read `.claude/agent-context.md` before anything else.** Its safety rules bind
this whole workflow.

## Non-negotiables

- **Never launch the bot to "check" something.** Launching is the user's call. If a
  question can only be answered by a live run, write it down as an open question.
- **The gate is `python tests/run_offline_suite.py`.** Never a blanket `tests/` run.
- **Nothing gets armed on a theory.** Every fix traces to a verified finding.

---

## Phase 0 — REGIME CHECK (do this first, every time, before any other analysis)

**Target is an adversary that changes without telling us.** This phase exists
because ordinary-SKU conversion collapsed on 2026-08-06 and was not noticed for six
weeks, while the project kept optimising a different tier.

Open `.claude/state/CURRENT_STATE.md` → **REGIME WATCH**, and compute this run's
value for each baseline metric. Then ask one question: **did the world move?**

Declare a regime change when a rate moves more than ~3x, a status-code
distribution shifts materially, a new error string appears, or **any metric goes to
zero over a meaningful n** — that last one is the signal that was missed.

If a regime change is detected:
1. Say so at the top of the report, before anything else. It outranks every other
   finding, because tuning inside a changed regime optimises against the wrong world.
2. Append an entry to `docs/TARGET_CHANGES.md`, including the **detection gap** —
   how long it took us to notice. Driving that number down is the point.
3. Ask whether recent fixes were evaluated inside the old regime, and re-open any
   conclusion that was.

Also watch for **recovery**. Something starting to work again is as informative as
a collapse, and just as easy to miss.

## Phase 1 — Establish what happened (parallel, cheap where possible)

Fan these out **in one message** so they run concurrently. Do not do this reading
yourself; context spent here is context unavailable for the reasoning later.

| Agent | Model | Job |
|---|---|---|
| `log-miner` | haiku | Raw tallies: shots, status-code distribution, per-TCIN, per-account, per-IP, timeline. **Must report the unmatched remainder.** |
| `failure-forensics` | sonnet | Timeline and funnel with n at every stage; name the single highest-loss stage |
| `purchase-flow-engineer` | sonnet | Every cart won: trace it to its death or its order, with elapsed times per hop |
| `antibot-analyst` | sonnet | Block classification — Shape vs HUMAN/PX vs edge limiter vs write-auth, with n each |

Add `stock-pipeline-analyst` (sonnet) only if detection or monitor behaviour is
implicated; detection has been measured as not the bottleneck, so it is off the
critical path by default.

**Segment hot vs ordinary SKU in every single one.** A pooled number is a useless
number here — the bot converts on ordinary SKUs and not on hype ones, so anything
averaged across both describes neither.

## Phase 2 — Synthesise (the orchestrator does this, not an agent)

Build the ranked loss list: for each failure, how many units it plausibly cost,
and what evidence supports that. Rank by expected units recovered, not by how
interesting or how easy the fix is.

**Reconcile before you rank.** Where two agents disagree, that disagreement is
itself a finding — resolve it against primary evidence or carry it forward as an
open contradiction. Do not average two agents into a middle answer.

## Phase 3 — Verify (parallel, fresh context, mandatory)

For every finding you intend to act on, spawn a `claims-verifier` (sonnet) with
**the claim alone and none of your reasoning**. Run them concurrently.

A `REFUTED` verdict kills the fix. No exceptions, no "but the other evidence
still points that way" — that reasoning is exactly how this project has shipped
wrong fixes before.

## Phase 4 — Fix

Only for findings that survived Phase 3.

- **Flag-gated and surgical.** One env flag per behaviour change, defaulting to the
  current behaviour so the change is reversible without a revert.
- **Arm in `run_bot_with_nightly_restart.bat`** — a flag that only defaults ON in
  code is not live. Batch files here **must be CRLF**.
- **Pre-register the readout.** Before arming anything, write or name the script in
  `tools/analysis/` that will tell you next run whether it worked, and say what
  result would count as failure. A change with no pre-registered readout does not
  get armed.
- Run `python tests/run_offline_suite.py` and report the real result.

## Phase 5 — Record, and correct what this run proved wrong

This phase is what keeps the whole system from going stale. Skipping it means the
next session inherits today's beliefs with none of today's corrections.

1. **Update `.claude/state/CURRENT_STATE.md`.** Bump the as-of line to today's date
   and HEAD. Rewrite every line this run changed. **Delete lines this run refuted —
   do not leave a stale fact in place with a caveat bolted on;** a future agent
   will read the fact and skim the caveat.
2. **Name every belief this run invalidated, explicitly**, with the file and line
   that still carries the old version. Fix those too. A correction that lives only
   in this session's chat is a correction that did not happen.
3. Append to `docs/CLAIMS.md`: each finding, its tag, its n, its verdict, its date.
4. Append to `docs/FAILURES.md`: what failed and why.
5. Update the memory directory per the user's standing rule — the machine restarts,
   and memory is the only thing that survives it.
6. **Date-stamp everything and era-stamp every rate.** "Converts on ordinary SKUs"
   is not a claim. "Converted at 15.6% at the stock edge, 07-23→08-04, 0% after
   08-06" is.
7. State plainly what is **UNPROVEN LIVE**. Everything shipped here is unproven
   until a real drop says otherwise, and saying so is accuracy, not hedging.
8. **Record when each newly-armed flag was armed.** A mechanism only explains
   outcomes after its arming date, and this project has already been caught
   attributing results to a feature that was not live at the time.

## Closing report to the user

Lead with units lost and the single biggest cause. Then the ranked list with
verdicts, then what was armed, then what remains open. Do not pad it with what
went right.
