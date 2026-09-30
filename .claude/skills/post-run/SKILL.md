---
name: post-run
description: Post-mortem the most recent bot run end to end, then fix what it found. Runs the saved `post-run` Workflow (facts from the event store, analysts routed to the wall that lost units, fresh-context verification of every load-bearing claim, completeness critic), then lands only verified, flag-gated fixes behind the offline suite and records the corrections. Use after every drop, restock or overnight run.
argument-hint: "[run id or operator note, optional]"
arguments: run-target
---

# Post-run: find what went wrong, verify it, then fix it

Target run / note: `$run-target` — blank = the newest `logs/runs/run_*.log` over 1 MB.

**Read `.claude/agent-context.md` before anything else.** Its safety rules bind this
whole procedure. Rebuilt 2026-09-30 after the operator's "post run … sucks": the
analysis is now a deterministic Workflow script, and facts come from one tested parser
instead of a new regex every night.

## Non-negotiables

- **Never launch the bot** (or a browser, harvester, login or live checkout) to "check"
  something. A question only a live run can answer becomes a pre-registered experiment.
- **The gate is `python tests/run_offline_suite.py`.** Never a blanket `tests/` run.
- **Nothing gets armed on a theory.** Every fix traces to a claim a fresh-context
  verifier did not refute. A PARTIALLY CONFIRMED claim supports only its narrowed version.
- **Auto-mode classifier denials are final for that outcome.** If an edit or command is
  denied, do not re-route it through another tool, script or agent. Restore the tree,
  record the blocked fix in CURRENT_STATE / CLAIMS, and hand the decision to the operator.

## Step 1 — Run the workflow (do not re-do its work by hand)

```
Workflow({ name: "post-run", args: { run: "<run id, optional>", note: "<operator note, optional>" } })
```

It is `.claude/workflows/post-run.js` (v2, 2026-09-30: "loop until complete, and be
confident without inherent bias"). It runs in ROUNDS until a round adds nothing:
1. **Facts** (log-miner, Sonnet): `tools/events/build.py` + the saved queries
   `regime`, `regime_tcin`, `monitor_hours`, `walls`, `windows`, `checkout`, `per_tcin`, `arms`
   → a schema'd fact sheet and the regime flags against CURRENT_STATE's REGIME WATCH. A
   rate flag must survive per-TCIN / leave-one-TCIN-out and like-for-like hours, or it is
   composition, not a regime change.
2. **Investigate** — round 1 routes only to what lost units: no restock →
   `stock-pipeline-analyst` (did we miss one?); restock → `antibot-analyst`; any cart →
   `purchase-flow-engineer` (did TARGET or OUR limits end it?); any regime flag →
   `failure-forensics`. Later rounds: one agent per critic gap.
3. **Verify every load-bearing claim three ways, none of them by its author:**
   a **blind replicator** gets only a question (never the claim or its number) and
   measures it — counting questions go to Sonnet, a different tier than the Opus
   analyst, to decorrelate errors; a **refuter** gets only the bare claim; a **neutral
   judge** confirms only when the blind measurement agrees and the refutation fails.
4. **Critic** — gaps that could change units lost, a fix decision or a verdict. Each new
   gap becomes the next round's question; no new gap = complete. `max_rounds` (default
   3) bounds cost and every unfinished gap is logged, never dropped silently.
5. **Calibration (optional args):** `canaries` = known-false claims mixed in under
   neutral ids — a confirmed canary means verification is rubber-stamping and the run's
   verdicts are not to be trusted; `answer_key` = known findings, graded for recall.
   Both are shown to nobody but the grader. Use them whenever the workflow itself
   changes (the 09-30 run is the reference case: see the 09-30 section of CURRENT_STATE).

The result lists every claim with its three checks, `fix_specs` with an `eligible`
flag (true only when every claim a spec rests on was judged CONFIRMED or PARTIALLY
CONFIRMED), `complete` / `open_gaps`, and the calibration.

Wait for the completion notification. Save the result under
`logs/analysis_<date>/postrun/` before reading it (it is large).

If the event store is missing or broken, fix the store first (it is analysis code with
its own offline test, `tests/test_events_parser.py`); do not fall back to a one-off
readout script.

## Step 2 — Decide (the main session, not an agent)

- **Regime first.** If the facts or the critic flag a regime change, say so at the top
  of the report and append `docs/TARGET_CHANGES.md` with the detection gap.
- **Rank by expected units recovered**, not by interest or ease. Separate Target's
  limits from OUR behaviour — 09-30's only cart was ended by our own caps.
- A REFUTED verdict kills the fix. Disagreements go back to primary evidence; never
  average two agents.

## Step 3 — Fix (only what survived Step 2)

- **Flag-gated and surgical**; the default reproduces current behaviour.
- **Scope uncertain changes to one account** (`<FLAG>_ACCOUNTS=alt-1` style) so the other
  accounts are same-window controls. Fleet-wide arming confounds the change with the
  night (Target's per-SKU, per-night security level) — the main reason past fixes could
  not be read.
- **Pre-register the readout as SQL** in `tools/events/queries/` (a named query plus the
  worked / failed / inconclusive rule and the n it needs), not as a new readout script.
- Arm in `run_bot_with_nightly_restart.bat` (CRLF). Run the offline suite; report the
  real result.

## Step 4 — Record, and correct what this run proved wrong

1. `.claude/state/CURRENT_STATE.md`: bump the as-of line; put this run's section at the
   top; **delete refuted lines** (no caveats bolted onto stale facts); update the REGIME
   WATCH rows.
2. Fix every stale belief at its source (the critic's `stale_facts_to_correct`).
3. `docs/CLAIMS.md` (CRLF): each claim, n, verdict, date. `docs/FAILURES.md` (CRLF): the entry.
4. Memory (the machine restarts): a session file + the MEMORY.md RESUME line.
5. Date-stamp everything; era-stamp every rate; say plainly what is UNPROVEN LIVE and
   when each flag was armed.

## Closing report to the operator

Lead with units lost and the single biggest cause (Target's wall or ours). Then the
ranked, verified list; what was armed; what was blocked and needs their decision; what
remains open. No padding.
