---
name: bot-investigate
description: Investigate a bot bug, failure, or open question with a parallel multi-agent fan-out — cheap agents for extraction, capable agents for code and data reasoning, adversarial verification before any conclusion is trusted. Use for a new issue, a regression, a mystery in the logs, or a competitor-parity question.
argument-hint: "[the issue or question]"
arguments: issue
---

# Investigate: $issue

**Read `.claude/agent-context.md` before anything else.** Its safety rules and
claim-tagging rules bind this whole workflow.

## Non-negotiables

- **Never launch the bot, a browser, a harvester, or a live checkout** to reproduce
  something. That is the user's call, always.
- **Never blanket-run `tests/`.** Only `python tests/run_offline_suite.py`.
- **Read-only until a conclusion survives verification.** No speculative edits.

---

## Step 1 — Frame it before you spend anything

Write down, in one line each:
- The claim or question in **falsifiable** form.
- What evidence would settle it, and whether that evidence **exists** in this repo.
- What you would do differently depending on the answer.

If nothing would change based on the answer, say so and stop. If the deciding
evidence does not exist, say that up front — an honest "this cannot be settled from
what we have" beats a confident answer built on a proxy measurement.

## Step 2 — Route to the right specialists

Pick only the agents whose beat the question actually touches. More agents is not
better; each one costs money and dilutes the signal.

| Beat | Agent | Model |
|---|---|---|
| Shape / HUMAN-PX / edge limiter / 401s / 403s / 429s | `antibot-analyst` | sonnet |
| ATC chain, checkout, locks, cadence, timers | `purchase-flow-engineer` | sonnet |
| Logs, timelines, funnels, post-mortems | `failure-forensics` | sonnet |
| Monitor, sweeps, proxies, dispatch latency | `stock-pipeline-analyst` | sonnet |
| Raw counts out of big logs | `log-miner` | **haiku** |
| Competitor docs, retailer APIs, vendor claims | `retailer-researcher` | **haiku** |

**Model discipline.** Anything that is extraction — counting, quoting, tallying,
transcribing — goes to haiku. Reserve sonnet for work that needs real code
comprehension or causal reasoning across sources. Never send a grep to a big model.

**Launch every independent agent in a single message** so they run concurrently.
Give each one its own narrow question, the exact files or line ranges to start
from, and the output format you want back. Vague prompts produce vague reports.

## Step 3 — Synthesise yourself

The orchestrator reasons; agents supply material. When two reports conflict, go to
primary evidence — never split the difference.

Tag every conclusion `[MEASURED]` / `[REPORTED]` / `[INFERRED]` /
`[NOT ESTABLISHED]` / `[REFUTED]`, always with n.

## Step 4 — Verify adversarially

Before anything is treated as settled, hand the bare claim — **with none of your
reasoning** — to a `claims-verifier` (sonnet). Run several in parallel for several
claims.

Take `REFUTED` seriously even when it is inconvenient. That is the entire point of
the step, and the reason it exists in this repo.

## Step 5 — Report

- The answer, in the first paragraph, with its confidence tag and n.
- The evidence, with `path:line` citations.
- **What was ruled OUT** — in this project that is routinely the more valuable half,
  because it stops a dead theory being re-proposed in three weeks.
- Open questions, and exactly what evidence would close each.

## Step 6 — Write back what changed (do not skip this)

1. **Update `.claude/state/CURRENT_STATE.md`** — bump the as-of date and HEAD,
   rewrite what changed, and **delete what was refuted** rather than annotating it.
2. Record durable findings in `docs/CLAIMS.md` and the memory directory, dated.
3. If a theory died here, write it as `[REFUTED]` with what killed it. This repo
   has re-litigated dead theories more than once for want of that one line.
4. **If you found that an existing doc, memory, code comment or agent definition
   carries a fact that is now wrong, fix it at the source and say that you did.**
   Leaving a stale fact in place because correcting it was not the assigned task is
   how the next investigation starts from a false premise.
