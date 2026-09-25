---
name: pre-drop
description: Get the bot into the best possible position before a known drop window, then hand off a boot checklist. Verifies accounts and TCIN visibility, runs a regime check, lands only verified flag-gated changes behind the offline suite, proves the proxy pool through the production path, and pre-registers the readout. Use in the hours before a drop, restock or launch.
argument-hint: "[drop time and/or TCINs, optional]"
arguments: drop-target
---

# Pre-drop: best fighting chance, no new risk

Drop target: `$drop-target` — if blank, ask for the time and the TCINs.

**Read `.claude/agent-context.md` before anything else.** Its safety rules bind
this whole workflow, and §2B (the four lever checks) is the thing that stops this
skill from shipping a confident mistake.

## Non-negotiables

- **Never start the bot.** Starting is the user's call, always. This skill ends at
  a boot checklist, never at a launch.
- **The gate is `python tests/run_offline_suite.py`.** Never a blanket `tests/`.
- **Every change ships flag-gated with a byte-identical off-state**, or it does
  not ship. "It's only a log line" is not an exemption — log lines have exact-match
  assertions in `test_dx_logs`.
- **Verify before arming.** Hand the bare claim, with none of your reasoning, to
  `claims-verifier`. A `REFUTED` or `NOT ESTABLISHED` verdict kills the change —
  *or* kills the reason you were not making it. It cuts both ways.
- **"Play it safe" is not automatically safe.** Declining a verified, gated fix
  before a drop is itself a choice with a cost. Say what the cost is.

---

## Phase 0 — What is the actual state, right now

Do not infer state from logs when you can read it. On 2026-09-21 a wrapper line
reading `chrome target sign-out done` was read as "the accounts are signed out";
`check_session_readiness.py` then showed all three healthy. That script signs out
the *operator's personal* Chrome, not the bot jars.

- `git log -1`, `git status`, current branch.
- Is anything running? (python process, port 5001, Chrome count.)
- `venv\Scripts\python.exe check_session_readiness.py` — zero-browser, read-only,
  safe to run any time. Want 3/3 MEMBER.
- Session age vs the 32-46 h rot window, projected **to the drop moment**, not now.

## Phase 1 — Regime check

Open `.claude/state/CURRENT_STATE.md` → **REGIME WATCH** and compare the last run
against the baselines *before* deciding anything. A metric at exactly zero over a
meaningful n is the highest-priority signal — that is what was missed for six
weeks in 2026-08. Log anything new in `docs/TARGET_CHANGES.md`.

## Phase 2 — Arming: what is armed vs what was built

Flags get built and never armed (`MULTI_SKU_DISPATCH` sat at 0 for a week). Dump
every `TARGET_*` the wrapper sets and reconcile it against every `TARGET_*` the
code reads. Three outcomes matter:

- built, not armed → decide deliberately
- armed, not read → dead flag, note it
- armed by `app.py` rather than the wrapper → `app.py:51-65` `setdefault`s five
  flags at import, ahead of every module. Absent from the .bat does **not** mean off.

Then apply §2B of the agent context to every lever you are tempted to move.

## Phase 3 — Config

- Confirm every requested TCIN is actually **in** `config/product_config.json`.
  "Enable these" may mean "add this one" — on 09-21 one of ten was absent entirely
  and had to be restored from a backup.
- Keep enabled TCINs **at or under 30**. Above that the unchunked ground-truth read
  fails every cycle and visibility is UNKNOWN all night.
- **Config list order is NOT a real priority lever** (`docs/CLAIMS.md` C-0924-02,
  verified 2026-09-24). Every go-live reaches the purchase manager as its own
  single-TCIN event, in sweep read order (0 of 906 `[STOCK] IN STOCK:` lines ever named
  two TCINs); the list-order sort (`bulletproof_purchase_manager.py`, the `sorted_tcins`
  line in `process_stock_data`) only acts on level re-arm and blind-pool tab-fetch
  events. Whoever flips first takes the accounts, and a TCIN that flips while every
  account is racing another is **not re-raced while it stays in stock** (C-0924-01).
  Do not spend pre-drop time reordering.
- Check the `qty` key per TCIN. Missing/0 = **no pin** = the qty-2 policy, live only
  while `TARGET_QTY_PER_TCIN=1`. Do not assume a hot SKU is pinned.
- Back up to `config/product_config_backup_pre_<date>_drop.json` first.

## Phase 4 — Prove the proxy pool

`venv\Scripts\python.exe validate_proxies.py` (`VALIDATE_POOL=all`,
`VALIDATE_DURATION_S=180`). It drives the **production** browser-native path with a
throwaway state dir, so it cannot park a real exit or touch warmed profiles, and no
purchase manager is wired.

- **Pass it the wrapper's monitor env, or it validates the wrong path.** The script
  inherits only your shell's env, and `RESILIENT_REDSKY_CHANNEL` defaults to `web`
  in code — the in-page channel HUMAN/PX walls on the BD prefixes — while
  production reads `apps_raw`. Export every `RESILIENT_*` / `STOCK_*` /
  `TARGET_SWEEPS_PER_SEC` / `USE_RESILIENT_STACK` the .bat sets, plus
  `CHROME_STAGGER_TOTAL_S=30` (an `app.py` setdefault). Found on 09-22.
- **Never judge a BD exit with raw Python `requests`.** Shape blocks the Python TLS
  handshake regardless of IP; a 2026-05-14 audit declared 28 IPs burned and all 28
  returned 200 through the production path.
- Run it with `python -u` or read progress from the throwaway
  `state/_proxy_validation_tmp/proxy_state.json` — stdout is pipe-buffered.
- Snapshot that state file before the script exits; it deletes its temp dir.
- This doubles as a **host load test** for the pool size you are about to boot.

## Phase 5 — Pre-register the readout

Write `tools/analysis/readout_<date>.py` **before** the run, with PASS/FAIL criteria
fixed in advance, one rule per change you armed. This is `docs/CLAIMS.md` rule 3 and
it is what stops tomorrow's narrative from being fitted to tomorrow's data.

Smoke it against a past log so you know the regexes match real lines. Get the
formats from the actual log, not from the code — print-only markers reach only
`logs/runs/run_<boot>.log`, never `package.log`.

## Phase 6 — Gate and record

- `python tests/run_offline_suite.py` — 26 files, ~3.5 min. Must be green.
- Batch files: **CRLF, ASCII, no `% ! | & < > ^` in any REM line**, every changed
  variable assigned exactly once and last.
- Update `.claude/state/CURRENT_STATE.md` — delete lines that turned out wrong
  rather than caveating them — and write the session memory. The machine restarts.
- **Offer to commit.** Do not commit unasked. Say plainly that uncommitted
  reasoning is lost if the host reboots, which it does.

## Phase 7 — Hand off a boot checklist

End with what the user should see, in order, in the first ~4 minutes:

1. `=== drop-readiness check ===` → three ✅ MEMBER
2. `[MULTI_SESSION] started -- N/N sessions ready` → N = the pool size you armed
3. `[GROUND-TRUTH] pool cache-bust ok: in_stock=[] (N TCINs)` → N = TCINs enabled
4. no `[TCIN-VISIBILITY]` banner naming a TCIN they intend to buy — **no banner is
   the good case**; a newly armed TCIN reads PARTIAL until the bot verifies it
5. first `[STOCK STATS]` clean — 200s, `403=0`

Then name the single command that reads the run out afterwards, and stop.
