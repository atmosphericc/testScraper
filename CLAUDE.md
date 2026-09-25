# Retail Bot

## Stack
- Backend (current): `app.py` — Target prod via `USE_RESILIENT_STACK=1`
- Backend (test): `test_app.py` (stops before the real purchase)
- Stock-check pipeline: `src/monitoring/{stock_check_resilient,tab_dispatcher,proxy_preflight,stock_monitor}.py`, `src/session/multi_session_pool.py`, `src/proxy/{local_forwarder,proxy_state}.py`
- Browser: zendriver/nodriver — `from zendriver import cdp`. N persistent Chromes pinned 1:1 to Bright Data ISP IPs
- Frontend: Jinja2 — `dashboard/templates/simple_dashboard_v2.html` (`/v2`, current), `simple_dashboard.html` (`/`, older)
- Anti-bot focus: **Target / F5 Shape + HUMAN/PerimeterX** — JA3/JA4 via browser-native dispatch (`tab.evaluate(fetch(...))`). No curl_cffi in the request path. Never call Target's vendor Akamai.
- **Walmart was removed 2026-09-21** (`walmart/`, `src/stack/`, `unified_app.py`, 13 tests, 6 docs). Target is the only retailer. The code is recoverable from git history; the cross-retailer anti-bot findings stay in `docs/ANTIBOT_ARCHIVE.md` / `docs/FAILURES_ARCHIVE.md`.

## Architecture — Resilient Stack (Round 2, validated 2026-05-13)
Browser-native dispatcher: N persistent Chromes (one per BD ISP IP) fire bulk RedSky via `tab.evaluate(fetch(...))`. **99.95% over 60 min @ 3 RPS / 3 IPs / 33 TCINs** (zero 403s, 5 self-healed timeouts).

- Architecture doc: `docs/RESILIENT_STACK.md`
- Operational findings: `docs/RESILIENT_STACK_OPERATIONAL_NOTES.md`
- Production: `USE_RESILIENT_STACK=1 python app.py` — rate via `RESILIENT_TARGET_RPS` (alias `TARGET_SWEEPS_PER_SEC`)
- Smoke test: `python test_resilient_stack.py` — knobs: `RESILIENT_TEST_DURATION_S`, `RESILIENT_TEST_NUM_IPS`, `RESILIENT_TEST_RPS`, `RESILIENT_TEST_BEHAVIORAL`, `CHROME_STAGGER_TOTAL_S`
- Fast smoke: `RESILIENT_TEST_DURATION_S=120 RESILIENT_TEST_NUM_IPS=1 python test_resilient_stack.py`
- TCIN chunking is dynamic — chunked at 28 client-side (Target hard-caps `product_summary_with_fulfillment_v1` at 30/req). Per-TCIN refresh = `RPS / ceil(N/28)`.
- Behavioral mixin default OFF — `behavioral_mix_ratio=0.10` triggered a 7×403 burst at ~4 min in. Re-enable ≤0.02 only.
- Legacy `src/session/cookie_harvester.py` is deprecated (Round 1 curl_cffi path). Each session in MultiSessionPool now owns its own live cookies.

## Repo Layout
```
/
├── app.py                       # Target prod entry (USE_RESILIENT_STACK=1)
├── test_app.py                  # Test env
├── run_bot_with_nightly_restart.bat  # production wrapper (arms all flags)
├── test_resilient_stack.py      # Round 2 smoke test
├── src/
│   ├── monitoring/              # stock_check_resilient, tab_dispatcher, proxy_preflight, stock_monitor (legacy)
│   ├── session/                 # multi_session_pool, session_manager, purchase_executor (Target), cookie_harvester (deprecated)
│   ├── purchasing/              # bulletproof_purchase_manager (Target checkout)
│   └── proxy/                   # local_forwarder, proxy_state
├── tools/analysis/              # readout + forensics scripts
├── config/                      # proxyIps.json, product_config.json
├── state/                       # proxy_state.json, session_profiles/, smoke_profiles/
├── dashboard/templates/         # simple_dashboard_v2.html (/v2), simple_dashboard.html (/)
└── docs/
    ├── RESILIENT_STACK.md
    ├── RESILIENT_STACK_OPERATIONAL_NOTES.md
    ├── ANTIBOT.md / ANTIBOT_ARCHIVE.md
    ├── FAILURES.md / FAILURES_ARCHIVE.md
    ├── FLOW.md
    ├── CODEBASE.md
    └── RETAILERS/ (target.md, TARGET_CHECKOUT_API.md)
```

## Task Routes

| Task | Start | Then |
|------|-------|------|
| Stock check (current) | docs/RESILIENT_STACK.md | src/monitoring/stock_check_resilient.py, tab_dispatcher.py |
| Multi-session pool / proxy | docs/RESILIENT_STACK.md | src/session/multi_session_pool.py, src/proxy/{local_forwarder,proxy_state}.py |
| Target purchase flow | docs/FLOW.md → docs/RETAILERS/target.md | src/session/purchase_executor.py, src/purchasing/bulletproof_purchase_manager.py |
| Target anti-bot | docs/ANTIBOT.md | src/session/purchase_executor.py (Shape headers, warmup POST) |
| Frontend | dashboard/templates/simple_dashboard_v2.html | app.py |
| Failures | docs/FAILURES.md (or FAILURES_ARCHIVE.md for history) | specific file |

## Context Rules
- **Resilient stack / Target work:** Read `src/monitoring/`, `src/session/multi_session_pool.py`, `src/proxy/`.
- **Never load:** entire repo; use specific paths above.

## Agentic Orchestration
All botting work — new bugs, post-run fixes, parity questions — runs through the
agent roster in `.claude/agents/`. **Every agent reads `.claude/agent-context.md`
first**; that file holds the hard safety rules (never launch the bot, never blanket-run
`tests/`), the claim-tagging discipline, the repo map and the domain glossary.

| Agent | Model | Beat |
|---|---|---|
| `antibot-analyst` | **opus** | Shape / HUMAN-PX / edge limiter; 401s, 403s, 429s, credential quality |
| `purchase-flow-engineer` | **opus** | ATC → pre_checkout → place-order chain; locks, cadence, timers |
| `failure-forensics` | **opus** | Drop post-mortems; timelines and funnels from the run logs |
| `stock-pipeline-analyst` | **opus** | Resilient stack, RedSky sweeps, dispatch latency, proxy pool |
| `claims-verifier` | **opus** | Adversarial fresh-context verification; assumes the claim is false |
| `log-miner` | sonnet | Mechanical counts out of big logs; reports the unmatched remainder |
| `retailer-researcher` | sonnet | Competitor docs, retailer APIs, vendor claims; verbatim extraction |

**Model discipline (operator, 2026-09-25: "use the new models 5.5"):** reasoning —
code comprehension, causal analysis, adversarial verification — goes to `opus`
(= Opus 5.5); extraction — counting, quoting, tallying — goes to `sonnet` (= Sonnet 5),
not haiku: extraction slips here have been costly (a 208-of-211 remainder reported as
"0 FAILED", a timestamp filter that reported 0 `[WAVE_FIRST]` for a night with 36).
Aliases track the newest model of each tier. Launch independent agents in a single
message so they run concurrently.

**Workflows** (`.claude/skills/`):
- `/pre-drop [time/TCINs]` — best position before a known drop: readiness, regime
  check, arming audit, proxy proof, pre-registered readout, boot checklist. Never starts the bot.
- `/post-run [run]` — post-mortem the last run, verify findings, land flag-gated fixes
- `/bot-investigate [issue]` — fan-out investigation of a bug or open question

**Always verify before arming.** Hand the bare claim, with none of your reasoning,
to `claims-verifier`. A `REFUTED` verdict kills the fix.

**Live facts live in `.claude/state/CURRENT_STATE.md` — nowhere else.** Agent
definitions and `agent-context.md` hold method only, so facts rot in one visible
place. Check each fact's as-of date against `git log -1` and the wrapper's mtime
before relying on it; anti-bot facts have a ~2-week half-life. `/post-run` opens
with a **regime check** against the baselines in that file, because Target changes
without telling us — a collapse went unnoticed for six weeks once.
`docs/TARGET_CHANGES.md` is the adversary's changelog, separate from our own.

**Competitor corpus:** `logs/analysis_2026_09_21/research/refract_llms_full_2026_09_21.txt`
— Refract's complete public doc set (6,040 lines, 43 pages). Target module ≈ L3062-4351.

## Forbidden
- /__pycache__, /.git, /venv, /node_modules, /dist, /.pytest_cache

## File Access
- No source files are off-limits. `src/session/purchase_executor.py` is editable (overrides prior "DO NOT modify" guidance from the AIO refactor memory).

## Test Before Deploy
- **Offline suite is the gate:** `python tests/run_offline_suite.py` (26 files, ~3.5 min). Never blanket-run `tests/` — it holds live tests that launch browsers and can fire real purchases.
- Target resilient stack: `RESILIENT_TEST_DURATION_S=120 RESILIENT_TEST_NUM_IPS=1 python test_resilient_stack.py` (fast smoke) or `python test_resilient_stack.py` (default 30 min).
