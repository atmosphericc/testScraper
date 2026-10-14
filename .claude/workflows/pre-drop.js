export const meta = {
  name: 'pre-drop',
  description: 'Read-only pre-drop readiness audit: deterministic preflight + event-store facts, parallel analysts routed to arming, code risk, money safety, monitor, regime and external research, blind verification (replicator who never sees the claimed value + refuter + neutral judge) of every GO/NO-GO claim, a critic whose gaps start the next round until a round adds nothing, then a scorecard. Never edits code, arms flags, runs validate_proxies.py or launches anything.',
  whenToUse: 'In the hours before a drop, after the operator names the window and the TCINs. args (all optional): {drop: "2026-10-09 02:00", tcins: ["1010892076", ...] or "a,b,c", note: "operator note", max_rounds: 2, max_claims: 2 (0 = no verification, for SHORT / LAST-CALL mode), research: true}. The main session decides GO / NO-GO, edits config, arms flags, runs the proxy proof and the offline suite; this workflow only establishes the facts and verifies them.',
  phases: [
    { title: 'Preflight', detail: 'tools/predrop/preflight.py + boot_check.py + event store facts + regime check' },
    { title: 'Audit', detail: 'arming, code risk, money safety, monitor, regime (+ antibot when the last run had shots, + external research)' },
    { title: 'Replicate', detail: 'blind re-measurement of each GO/NO-GO claim from a question that never reveals the claimed value' },
    { title: 'Refute', detail: 'adversarial check of the bare claim' },
    { title: 'Judge', detail: 'confirmed only when the blind measurement agrees and the refutation fails' },
    { title: 'Critic', detail: 'gaps that could flip GO/NO-GO or an operator action; none new = done' },
    { title: 'Scorecard', detail: 'deterministic merge: blocking actions, UNPROVEN LIVE flags, boot markers, readout rules' },
  ],
}

// Why this shape (operator, 2026-10-08: "make sure it will allow me to have my program 100%
// ready for the toughest challenge yet"):
// - DETERMINISTIC FIRST: tools/predrop/preflight.py measures every zero-browser fact (accounts,
//   config, wrapper flags, host power / Windows Update / GPU events, proxy state, the last boot)
//   the same way every night; agents reason on top of it instead of re-deriving it by hand.
// - EVERY PRE-LAUNCH MISS SINCE 09-16 HAS A CHECK: the 10-07 startup AttributeError (boot_check),
//   the 10-02 display-wake GPU crash (host.display), exit-87 guest loops (readiness + relogin
//   results), the absent TCIN (config.requested_absent), the wrong proxy channel (validate_cmd),
//   flags built and never decided (arming audit), Windows Update unpaused (host.wu_pause).
// - NO ANCHORING / NO SELF-GRADING / LOOP UNTIL DRY: the post-run workflow's verification
//   trio and critic rounds, unchanged, applied to the claims a GO / NO-GO rests on.
// - READ-ONLY: nothing here edits, arms or launches. validate_proxies.py launches 20 Chromes, so
//   it stays with the main session (the preflight prints the exact command with the wrapper env).

const A = args || {}
const DROP = A.drop ? String(A.drop) : ''
const TCINS = Array.isArray(A.tcins) ? A.tcins.map(String) : (A.tcins ? String(A.tcins).split(',').map(s => s.trim()).filter(Boolean) : [])
const NOTE = A.note ? String(A.note) : ''
const clampInt = (v, lo, hi, d) => { const n = Number(v); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, Math.round(n))) : d }
const MAX_ROUNDS = clampInt(A.max_rounds, 1, 4, 2)
const MAX_CLAIMS = clampInt(A.max_claims, 0, 4, 2)
const MAX_GAPS = clampInt(A.max_gaps, 0, 4, 3)
const RESEARCH = A.research === undefined ? true : !!A.research

const PREFLIGHT_CMD = `python tools/predrop/preflight.py --json${DROP ? ` --drop "${DROP}"` : ''}${TCINS.length ? ` --tcins ${TCINS.join(',')}` : ''}`

const DOCTRINE = `
Repo: C:\\Users\\elric\\Desktop\\testScraper (Windows; the Bash tool is Git Bash; never cd into a subfolder).
Read .claude/agent-context.md sections 1, 2, 2B, 2C and 6 first. HARD RULES: never launch the bot, a browser, a harvester, a login, a live checkout or validate_proxies.py (it starts 20 Chromes); never blanket-run tests/ and do not run the offline suite here (the main session does); never print config/proxyIps.json or any password / CVV value; read-only (write only under logs/analysis_*/ when told).
ALLOWED read-only commands: ${PREFLIGHT_CMD} (zero-browser; it runs check_session_readiness.py itself), python tools/predrop/boot_check.py --log <run log> --no-port, python tools/events/build.py --only <run_id>, python tools/events/q.py <regime|regime_tcin|monitor_hours|walls|windows|checkout|per_tcin|arms|coverage|status_changes|shadow206|gate_events|atc_net|fx1006|diag> --run <run_id> or q.py "<SQL>", git log / show / diff / status, grep.
Production truth is run_bot_with_nightly_restart.bat (a flag in code is not armed until the .bat sets it; app.py:51-65 setdefaults a few at import). Live facts with as-of dates: .claude/state/CURRENT_STATE.md (newest section on top; REGIME WATCH near the end; the STATE CARD first). Facts about adversary behaviour older than ~2 weeks are NOT ESTABLISHED.
Tag every claim [MEASURED]/[REPORTED]/[INFERRED]/[NOT ESTABLISHED]/[REFUTED] with n and path:line; report the unmatched remainder of every tally; report negative results.
${DROP ? `DROP WINDOW: ${DROP} (local time).` : 'DROP WINDOW: not given.'} ${TCINS.length ? `OPERATOR TCINS TONIGHT: ${TCINS.join(', ')}.` : ''}${NOTE ? `\nOPERATOR NOTE: ${NOTE}` : ''}
`

// ── schemas ─────────────────────────────────────────────────────────────────
const FACTS_SCHEMA = {
  type: 'object',
  properties: {
    preflight_verdict: { type: 'string', description: 'GO / GO-WITH-WARNINGS / NO-GO as printed by preflight.py' },
    preflight_mode: { type: 'string' },
    preflight_fail: { type: 'array', items: { type: 'string' }, description: 'every FAIL row: "id: detail -> fix"' },
    preflight_warn: { type: 'array', items: { type: 'string' } },
    since_last_boot: { type: 'string', description: 'the repo.since_last_boot row verbatim' },
    run_id: { type: 'string' }, boot_verdict: { type: 'string', description: 'boot_check verdict for the newest run log + the FAIL / WARN rows' },
    shots: { type: 'integer' }, carts: { type: 'integer' }, orders: { type: 'integer' },
    monitor_md: { type: 'string', description: 'q.py monitor_hours for the newest run AND the previous drop night, like-for-like hours' },
    regime_flags: { type: 'array', items: { type: 'string' } },
    tables_md: { type: 'string' },
    gaps: { type: 'array', items: { type: 'string' } },
  },
  required: ['preflight_verdict', 'preflight_fail', 'preflight_warn', 'run_id', 'boot_verdict', 'shots', 'regime_flags', 'tables_md'],
}

const CLAIM_ITEM = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    claim: { type: 'string', description: 'Bare, falsifiable, no reasoning attached.' },
    decides: { type: 'boolean', description: 'true only when a GO / NO-GO verdict or a blocking operator action rests on it' },
    check_against: { type: 'string', description: 'Files / log lines / queries where it can be checked. Pointers only - NO numbers, NO conclusions.' },
    kind: { type: 'string', enum: ['count', 'code', 'causal'] },
    question: { type: 'string', description: 'A question an independent agent can answer from primary evidence WITHOUT being told the claimed value or conclusion.' },
    expected: { type: 'string', description: 'The claim\'s own answer to that question (withheld from the replicator).' },
  },
  required: ['id', 'claim', 'decides', 'check_against', 'kind', 'question', 'expected'],
}

const ANALYSIS_SCHEMA = {
  type: 'object',
  properties: {
    report_markdown: { type: 'string', description: 'Lead with the answer; tagged claims with n; at most ~1,200 words.' },
    claims: { type: 'array', items: CLAIM_ITEM, description: 'The load-bearing claims; mark at most 2 as decides=true.' },
    operator_actions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          action: { type: 'string', description: 'One imperative line the operator can do before the window.' },
          why: { type: 'string' },
          blocking: { type: 'boolean', description: 'true = the bot cannot buy, or money is at risk, until this is done' },
          rests_on_claims: { type: 'array', items: { type: 'string' } },
        },
        required: ['action', 'why', 'blocking'],
      },
    },
    unproven_live_flags: {
      type: 'array',
      items: { type: 'object', properties: { flag: { type: 'string' }, kill: { type: 'string' }, first_exercise_marker: { type: 'string', description: 'the log line that proves the flag ran for real' } }, required: ['flag', 'kill', 'first_exercise_marker'] },
    },
    boot_markers: { type: 'array', items: { type: 'string' }, description: 'exact log strings the first 5 minutes must (or must not) show, beyond what boot_check.py already checks' },
    readout_rules: {
      type: 'array',
      items: { type: 'object', properties: { name: { type: 'string' }, query: { type: 'string', description: 'tools/events/q.py saved query or SQL, or the grep' }, pass: { type: 'string' }, fail: { type: 'string' } }, required: ['name', 'query', 'pass', 'fail'] },
    },
    stale_facts: { type: 'array', items: { type: 'string' }, description: 'file:line of any doc, state line, memory or comment this audit found wrong' },
  },
  required: ['report_markdown', 'claims', 'operator_actions', 'unproven_live_flags', 'boot_markers', 'readout_rules', 'stale_facts'],
}

const REPLICATION_SCHEMA = {
  type: 'object',
  properties: {
    answer: { type: 'string' }, method: { type: 'string' }, evidence: { type: 'string' },
    status: { type: 'string', enum: ['measured', 'partial', 'could_not_measure'] },
  },
  required: ['answer', 'method', 'evidence', 'status'],
}
const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    claim_id: { type: 'string' },
    verdict: { type: 'string', enum: ['CONFIRMED', 'PARTIALLY CONFIRMED', 'REFUTED', 'UNVERIFIABLE'] },
    narrowed_claim: { type: 'string' }, evidence: { type: 'string' }, what_would_settle: { type: 'string' },
  },
  required: ['claim_id', 'verdict', 'narrowed_claim', 'evidence'],
}
const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    claim_id: { type: 'string' },
    verdict: { type: 'string', enum: ['CONFIRMED', 'PARTIALLY CONFIRMED', 'REFUTED', 'UNVERIFIABLE'] },
    replication_agrees: { type: 'string', enum: ['yes', 'partly', 'no', 'no_data'] },
    narrowed_claim: { type: 'string' }, basis: { type: 'string' },
  },
  required: ['claim_id', 'verdict', 'replication_agrees', 'narrowed_claim', 'basis'],
}
const AGENTS = ['antibot-analyst', 'purchase-flow-engineer', 'stock-pipeline-analyst', 'failure-forensics', 'log-miner', 'retailer-researcher']
const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    gaps: {
      type: 'array',
      description: 'ONLY a question whose answer could flip a GO / NO-GO verdict, add or remove a BLOCKING operator action, or change which flag must be killed before boot. Everything else goes to research_questions. Empty when the decision is settled.',
      items: { type: 'object', properties: { question: { type: 'string' }, why: { type: 'string' }, agent: { type: 'string', enum: AGENTS } }, required: ['question', 'why', 'agent'] },
    },
    research_questions: { type: 'array', items: { type: 'object', properties: { question: { type: 'string' }, why: { type: 'string' } }, required: ['question', 'why'] } },
    disagreements: { type: 'array', items: { type: 'string' } },
    actions_on_weak_claims: { type: 'array', items: { type: 'string' }, description: 'blocking operator actions whose claims were not CONFIRMED' },
    stale_facts_to_correct: { type: 'array', items: { type: 'string' } },
  },
  required: ['gaps', 'disagreements', 'actions_on_weak_claims', 'stale_facts_to_correct'],
}

// ── prompts ─────────────────────────────────────────────────────────────────
const clip = (s, n) => { const t = String(s == null ? '' : s); return t.length > n ? t.slice(0, n) + ' …[clipped]' : t }

const replicatePrompt = (c) => DOCTRINE + `
You are given a QUESTION, not a claim. Answer it yourself from primary evidence (the code, the .bat, the raw logs, the event store, the preflight / boot_check output). Nobody has told you what answer to expect; do not look for another agent's report or docs/CLAIMS.md / CURRENT_STATE.md to copy from. Say exactly what you measured, how (command / SQL / regex / path:line), n, and the unmatched remainder. If you cannot measure it, say so (status could_not_measure).

QUESTION: ${c.question}

WHERE TO LOOK: ${c.check_against}`

const refutePrompt = (c) => `Verify this claim from a fresh context. Assume it is FALSE until primary evidence (the code, run_bot_with_nightly_restart.bat, the raw logs, the event store, the read-only tools under tools/predrop/) forces otherwise. Do not look for the reasoning that produced it; docs, CURRENT_STATE.md, CLAIMS.md and other agents' reports are not evidence. Hunt the counter-case: the code path where it does not hold, the flag the .bat does not arm, the sample where the number reverses, the confound. Never run the bot, a browser, a login or validate_proxies.py. Repo: C:\\Users\\elric\\Desktop\\testScraper. The Grep tool skips logs/runs/ when rooted at logs/; print-only markers are often glued onto the next logger line.

CLAIM ${c.id}: ${c.claim}

Where it can be checked: ${c.check_against}

Return the verdict, the narrowest version the evidence supports, the evidence with path:line / log:line and n, and what would settle it if unverifiable.`

const judgePrompt = (c, rep, ref) => `You are a neutral judge. You did not produce this claim and have no stake in it. Two independent checks were made: a REPLICATION by an agent that saw only the question (never the claim or its value), and an ADVERSARIAL CHECK by an agent that saw only the bare claim and tried to refute it.

CLAIM ${c.id}: ${c.claim}
REPLICATION QUESTION: ${c.question}
THE CLAIM'S OWN ANSWER TO THAT QUESTION: ${c.expected}

INDEPENDENT REPLICATION: ${rep ? clip(JSON.stringify(rep), 6000) : '(the replicator returned nothing)'}

ADVERSARIAL CHECK: ${ref ? clip(JSON.stringify(ref), 6000) : '(the refuter returned nothing)'}

Rules:
- CONFIRMED only if the replication independently measured a value that agrees with the claim's answer AND the adversarial check found no counter-evidence that survives.
- PARTIALLY CONFIRMED when a narrower version is supported; write that narrower version.
- REFUTED when the replication materially disagrees, or the adversarial check's counter-evidence holds.
- UNVERIFIABLE when neither check could measure it.
- If the two checks conflict, inspect primary evidence yourself, read-only (repo C:\\Users\\elric\\Desktop\\testScraper; never launch anything), to break the tie; say what you checked.
Quote the claimed value and the replicated value in basis.`

const pickAgent = (name) => (AGENTS.includes(name) ? name : 'failure-forensics')

// Model split (operator 2026-10-14: "not all subagents should use expensive models"): Sonnet
// counts, reads code and judges (the judge compares two finished checks); Opus only where the
// claim is causal. The analysts and the critic stay on their Opus agent definitions.
const verifyClaim = (c, tag) => {
  const kind = ['count', 'code', 'causal'].includes(c.kind) ? c.kind : 'causal'
  const deep = kind === 'causal' ? {} : { model: 'sonnet' }
  const repOpts = kind === 'count'
    ? { label: `replicate:${tag}:${c.id}`, phase: 'Replicate', agentType: 'log-miner', model: 'sonnet', schema: REPLICATION_SCHEMA, effort: 'high' }
    : Object.assign({ label: `replicate:${tag}:${c.id}`, phase: 'Replicate', agentType: 'claims-verifier', schema: REPLICATION_SCHEMA, effort: 'high' }, deep)
  return parallel([
    () => agent(replicatePrompt(c), repOpts),
    () => agent(refutePrompt(c), Object.assign({ label: `refute:${tag}:${c.id}`, phase: 'Refute', agentType: 'claims-verifier', schema: VERDICT_SCHEMA, effort: 'high' }, deep)),
  ]).then(([rep, ref]) =>
    agent(judgePrompt(c, rep, ref), { label: `judge:${tag}:${c.id}`, phase: 'Judge', agentType: 'claims-verifier', model: 'sonnet', schema: JUDGE_SCHEMA, effort: 'high' })
      .then(j => ({ claim: c, replication: rep, refutation: ref, judgment: j })))
}

// ── Phase 1: preflight facts ────────────────────────────────────────────────
phase('Preflight')
const facts = await agent(DOCTRINE + `
YOUR TASK: establish tonight's readiness facts. Do not interpret; measure and transcribe.
1. Run ${PREFLIGHT_CMD} and transcribe its verdict, mode, every FAIL row and every WARN row (id: detail -> fix), and the repo.since_last_boot row verbatim.
2. Run python tools/predrop/boot_check.py --no-port --json on the newest run log (the preflight names it): record the verdict and every FAIL / WARN row.
3. Build the event store for that run (python tools/events/build.py --only <run_id>) and run q.py regime, monitor_hours (for this run AND the previous run that had a drop window, same clock hours), diag and coverage for it; shots / carts / orders from q.py regime; put the tables in tables_md (trim to 40 rows and say so).
4. Compare against CURRENT_STATE.md REGIME WATCH: list in regime_flags every metric that moved more than ~3x, went to zero over a meaningful n, or showed a new error string, with both numbers. A rate flag must survive per TCIN, or it is composition.
Return the schema; anything the tools could not answer goes in gaps.`,
  { label: 'preflight', phase: 'Preflight', agentType: 'log-miner', model: 'sonnet', schema: FACTS_SCHEMA })
if (!facts) {
  log('preflight facts agent returned nothing - stopping (read the journal)')
  return { facts: null }
}
log(`preflight ${facts.preflight_verdict} (${facts.preflight_mode || '?'}): ${(facts.preflight_fail || []).length} FAIL, ${(facts.preflight_warn || []).length} WARN; last run ${facts.run_id} boot ${clip(facts.boot_verdict, 40)}, shots=${facts.shots}; regime flags ${(facts.regime_flags || []).length}`)

const FACTS_BLOCK = `\nPREFLIGHT FACTS (tools/predrop/preflight.py, deterministic; re-derive anything load-bearing):\nverdict=${facts.preflight_verdict} mode=${facts.preflight_mode || '?'}\nFAIL: ${(facts.preflight_fail || []).join(' | ') || '-'}\nWARN: ${(facts.preflight_warn || []).join(' | ') || '-'}\nsince last boot: ${facts.since_last_boot || '-'}\nlast run ${facts.run_id}: boot ${clip(facts.boot_verdict, 600)}; shots=${facts.shots} carts=${facts.carts} orders=${facts.orders}\nregime flags: ${(facts.regime_flags || []).join('; ') || 'none'}\n${clip(facts.monitor_md, 5000)}\n${clip(facts.tables_md, 8000)}\n`

const CLAIM_RULES = `
CLAIMS: for each load-bearing claim give kind, decides (true only for a claim a GO / NO-GO or a BLOCKING operator action rests on; at most 2 of those), a replication QUESTION an independent agent can answer from primary evidence WITHOUT being told your number or conclusion, and your own answer (expected). check_against holds pointers only. OPERATOR ACTIONS: one imperative line each, blocking only when the bot cannot buy or money is at risk until it is done; cite the claim ids they rest on. Also return the UNPROVEN LIVE flags you know of (flag, kill switch, first-exercise marker), extra boot markers, pre-registered readout rules (saved query + pass / fail), and stale facts (file:line).`

// ── round 1 routing ─────────────────────────────────────────────────────────
const round1 = [
  { key: 'arming', agentType: 'purchase-flow-engineer', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the ARMING audit. Production truth is run_bot_with_nightly_restart.bat. (1) Every flag whose value changed since the last live boot (the since-last-boot row above; also git log -p on the .bat): the mechanism, the kill switch, the exact log marker that proves it exercised for real, and whether a flags-off state is byte-identical to the prior behaviour. (2) Flags built in code and armed by nothing: list them with the recorded decision from CURRENT_STATE (declined / deferred / never decided) - a never-decided one is a decision the operator must take before boot (09-30: F1 sat undecided through a restock). (3) app.py setdefaults vs the .bat. (4) REM headers that contradict the set line below them (e.g. a "NOT ARMED" header above =1) and REMs that restate facts the state file has since refuted. Do not recommend new levers.` + CLAIM_RULES },
  { key: 'code-risk', agentType: 'purchase-flow-engineer', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the CODE-RISK audit. Is HEAD the exact tree that booted GREEN on the last run? (git rev-list -1 --before=<boot time> HEAD vs HEAD; git status for dirty files under app.py / src / tools.) If it differs: diff every startup-path change (app.py monitoring_loop, StockMonitorThread.start, PurchaseManagerThread, worker_pool boot, session init, the stock handler) and say for each new statement whether an exception there is caught and printed or would kill a thread silently (10-07: an AttributeError in a diagnostics start killed MonitoringLoop before purchase_thread.start(); the process looked alive and could not buy). If HEAD is the booted tree, say so plainly with the run id. Also: any flag-gated change armed since that boot whose on-path has never run live.` + CLAIM_RULES },
  { key: 'money', agentType: 'purchase-flow-engineer', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the MONEY-SAFETY audit for tonight's enabled TCINs and accounts. (1) Spend exposure: units per order (qty pins + TARGET_QTY_PER_TCIN), orders per account per race, accounts per race, and what bounds repeats across windows (the purchased row resets on the next in-stock event; the FX-1006 bought record is memory-only). (2) Every persisted latch or state the purchase path reads at boot (logs/purchase_states.json, state/ambiguous_commit_latch.json, state/boot_skip_accounts.json, state/cvv_challenge_<acct>.flag, the boot cart audit) - what the CURRENT value does tonight. (3) Wrong-item guards: the config TCIN is the only thing the add-to-cart uses; marketplace (VPC) listings never fire; fail-open cases of the first-shot chain guard. (4) Payment prerequisites per account (presence only, never values; a cvv equal to the hardcoded default is a WARN). Write the R-MONEY readout rule (order history: no qty > 2, no TCIN twice by one account in one window, no item not raced).` + CLAIM_RULES },
  { key: 'monitor', agentType: 'stock-pipeline-analyst', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the MONITOR audit. From the last run and the event store: sweep loss by clock hour in the drop slot vs the same hours on the last two drop nights (q.py monitor_hours), 206 storms and the shadow agreement (q.py shadow206), per-exit loss ([STOCK][EXITS] / [STOCK][OTHER]), sessions ready / crashed over the run, [HEALTH] rss and chrome_procs trend (what does RSS reach at the drop if the bot has been up N hours?). state/proxy_state.json: parked / burned / park_count carried into tonight and what the boot does with them. The enabled-TCIN count vs the 28-per-chunk rule at the armed sweeps/s. Decide, with the rule stated, whether the pool must be re-proven tonight with validate_proxies.py (the preflight prints the exact command; NEVER run it yourself - it launches 20 Chromes; the main session runs it).` + CLAIM_RULES },
  { key: 'regime', agentType: 'failure-forensics', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the REGIME + CARRY-FORWARD audit. (1) The regime flags above: real (re-derive from the raw log)? Target-side or ours? Draft the docs/TARGET_CHANGES.md entry text if real (do not write the file). (2) Carry-forward: read the newest two sections of CURRENT_STATE.md and the STATE CARD's "Blocked / operator decisions" row; list every "operator must / before the next run / needs a decision" item and whether it has been done (check the evidence: git log, the .bat, state files, the logs). 09-28 booted with an account the previous post-run had said to hand-login first. (3) The last run's end: wrapper exit line or none, and what that means for leftover state.` + CLAIM_RULES },
]
if ((facts.shots || 0) > 0) {
  round1.push({ key: 'antibot', agentType: 'antibot-analyst', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the last run had shots. For every shot class (account x first/later x network): edge 429 (ERR_A2C), keyless 401 at the SSX hop, FAST_SELLING, carts - vs the CURRENT_STATE baselines. Say whether the walls look the same as the last drop night or moved, per TCIN. No levers.` + CLAIM_RULES })
}
if (RESEARCH) {
  round1.push({ key: 'research', agentType: 'retailer-researcher', prompt: DOCTRINE + `
YOUR TASK: external facts for tonight, extraction only, every row [REPORTED] with URL and date. (1) Refract's live Target docs (help.refractbot.com/llms-full.txt) vs the newest local copy under logs/analysis_*/research/: quote what changed. (2) Community reports (trackers, X, tracker blogs) about the drop window${DROP ? ` around ${DROP}` : ' tonight'}: time, items, published TCINs, early-access gating, purchase limits, any new queue / drawing / verification step. (3) Target bot-mitigation changes reported in the last 2 weeks. Separate confirmed (Target / Pokemon Company) from rumoured. Candidate TCINs go in the report as a list the OPERATOR may add; never say they should be added. Return the schema with claims decides=false and no operator action other than "decide whether to add these TCINs".` + CLAIM_RULES })
}

// ── rounds: audit -> (replicate || refute) -> judge -> critic ───────────────
const reports = []
const results = []
const investigated = []
const seenGaps = new Set()
const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
let jobs = round1
let round = 1
let critic = null
let openGaps = []
const research = []

while (round <= MAX_ROUNDS && jobs.length > 0) {
  const R = round
  log(`round ${R}: ${jobs.map(j => j.key).join(', ')}`)
  jobs.forEach(j => investigated.push(`r${R}:${j.key}${j.question ? ' - ' + j.question : ''}`))
  const analysed = await pipeline(
    jobs,
    (j) => agent(j.prompt, { label: `r${R}:${j.key}`, phase: 'Audit', agentType: j.agentType, schema: ANALYSIS_SCHEMA, effort: j.key === 'research' ? 'medium' : 'high' }),
    (res, j) => {
      if (!res) return []
      reports.push({ round: R, key: j.key, res })
      const cl = (res.claims || []).map((c, i) => Object.assign({}, c, { id: `${j.key}.${c.id || i + 1}` }))
      const deciders = cl.filter(c => c.decides)
      const toVerify = deciders.slice(0, MAX_CLAIMS)
      if (deciders.length > MAX_CLAIMS) log(`r${R}:${j.key}: ${deciders.length} deciding claims; verifying ${MAX_CLAIMS}; NOT verified: ${deciders.slice(MAX_CLAIMS).map(c => c.id).join(', ')}`)
      if (MAX_CLAIMS === 0 && deciders.length) log(`r${R}:${j.key}: verification off (max_claims=0); ${deciders.length} deciding claim(s) unverified`)
      return parallel(toVerify.map(c => () => verifyClaim(c, `r${R}`)))
        .then(vs => vs.filter(Boolean).map(v => Object.assign({ round: R, source: j.key }, v)))
    },
  )
  analysed.filter(Boolean).forEach(arr => (arr || []).forEach(v => results.push(v)))

  phase('Critic')
  const digest = reports.map(r => `## r${r.round}:${r.key}\n${clip(r.res.report_markdown, 6000)}\nBLOCKING ACTIONS: ${(r.res.operator_actions || []).filter(a => a.blocking).map(a => `${a.action} (rests on ${(a.rests_on_claims || []).join(', ') || '-'})`).join('; ') || '-'}`).join('\n\n')
  const verdicts = results.map(v =>
    `- ${v.claim.id} [${v.source}]: ${v.claim.claim} => ${v.judgment ? `${v.judgment.verdict} (replication ${v.judgment.replication_agrees}; ${clip(v.judgment.narrowed_claim, 400)})` : 'NO JUDGMENT'}`).join('\n')
  critic = await agent(DOCTRINE + FACTS_BLOCK + `
YOUR TASK: completeness critic after round ${R} of a PRE-DROP audit. Below: every report so far and each deciding claim's verdict (a blind replication + an adversarial check + a neutral judge). Put in gaps ONLY a question whose answer could flip a GO / NO-GO verdict, add or remove a BLOCKING operator action, or change which flag must be killed before boot - each as one answerable question with the agent best placed to answer it. Everything else goes to research_questions and does NOT start another round. The drop is at a fixed time; the loop exists to settle the boot decision, not to exhaust the topic. Do NOT repeat anything already investigated:
${investigated.join('\n')}
Also list disagreements between reports / verdicts / the preflight, blocking actions resting on claims that are not CONFIRMED, and file:line of any doc, state line, memory or comment the audit contradicts. Return an empty gaps list when the boot decision is settled.

REPORTS:
${digest}

VERDICTS:
${verdicts || '(none)'}`, { label: `critic:r${R}`, phase: 'Critic', agentType: 'claims-verifier', schema: CRITIC_SCHEMA, effort: 'high' })

  const fresh = ((critic && critic.gaps) || []).filter(g => {
    const k = norm(g.question)
    if (!k || seenGaps.has(k)) return false
    seenGaps.add(k)
    return true
  })
  ;((critic && critic.research_questions) || []).forEach(q => { if (!research.some(x => norm(x.question) === norm(q.question))) research.push(q) })
  if (fresh.length === 0) { log(`round ${R}: the critic found no decision-changing gap - complete`); openGaps = []; break }
  if (fresh.length > MAX_GAPS) log(`round ${R}: ${fresh.length} new gaps; taking ${MAX_GAPS}; deferred: ${fresh.slice(MAX_GAPS).map(g => g.question).join(' | ')}`)
  openGaps = fresh
  jobs = fresh.slice(0, MAX_GAPS).map((g, i) => ({
    key: `gap${R}.${i + 1}`, agentType: pickAgent(g.agent), question: g.question,
    prompt: DOCTRINE + FACTS_BLOCK + `
A completeness critic reviewing this pre-drop audit asked a question nobody has answered yet:
QUESTION: ${g.question}
WHY IT MATTERS: ${g.why}
Answer it from primary evidence. Report what you found, including a negative result.` + CLAIM_RULES,
  }))
  round++
}
if (round > MAX_ROUNDS && openGaps.length) log(`stopped at max_rounds=${MAX_ROUNDS}; OPEN GAPS NOT INVESTIGATED: ${openGaps.map(g => g.question).join(' | ')}`)

// ── Scorecard (deterministic) ───────────────────────────────────────────────
phase('Scorecard')
const verdictOf = (id) => { const r = results.find(v => v.claim.id === id); return r && r.judgment ? r.judgment.verdict : 'NOT VERIFIED' }
const dedupe = (arr, keyFn) => { const seen = new Set(); return arr.filter(x => { const k = keyFn(x); if (!k || seen.has(k)) return false; seen.add(k); return true }) }
const actions = dedupe(reports.flatMap(r => (r.res.operator_actions || []).map(a => {
  const ids = (a.rests_on_claims || []).map(x => (String(x).startsWith(r.key + '.') ? String(x) : `${r.key}.${x}`))
  const vs = ids.map(verdictOf)
  return { source: `r${r.round}:${r.key}`, action: a.action, why: a.why, blocking: !!a.blocking,
    rests_on: ids.map((x, i) => `${x}=${vs[i]}`),
    supported: ids.length === 0 ? 'unverified' : (vs.every(v => v === 'CONFIRMED' || v === 'PARTIALLY CONFIRMED') ? 'verified' : (vs.some(v => v === 'REFUTED') ? 'refuted' : 'unverified')) }
})), a => norm(a.action))
const blocking = actions.filter(a => a.blocking && a.supported !== 'refuted')
const unproven = dedupe(reports.flatMap(r => r.res.unproven_live_flags || []), f => norm(f.flag))
const bootMarkers = dedupe(reports.flatMap(r => r.res.boot_markers || []).map(s => ({ s })), x => norm(x.s)).map(x => x.s)
const readoutRules = dedupe(reports.flatMap(r => r.res.readout_rules || []), x => norm(x.name))
const staleFacts = dedupe(reports.flatMap(r => r.res.stale_facts || []).concat((critic && critic.stale_facts_to_correct) || []).map(s => ({ s })), x => norm(x.s)).map(x => x.s)
const preflightNoGo = String(facts.preflight_verdict || '').toUpperCase() === 'NO-GO'
log(`scorecard: preflight ${facts.preflight_verdict}; ${blocking.length} blocking action(s) (${blocking.filter(a => a.supported === 'verified').length} verified); ${unproven.length} UNPROVEN LIVE flag(s); ${results.length} claims judged; open gaps ${openGaps.length}`)

return {
  drop: DROP, tcins: TCINS,
  preflight: { verdict: facts.preflight_verdict, mode: facts.preflight_mode, fail: facts.preflight_fail, warn: facts.preflight_warn, since_last_boot: facts.since_last_boot },
  last_run: { run_id: facts.run_id, boot: facts.boot_verdict, shots: facts.shots, carts: facts.carts, orders: facts.orders, regime_flags: facts.regime_flags },
  decision_inputs: {
    preflight_no_go: preflightNoGo,
    blocking_actions: blocking,
    other_actions: actions.filter(a => !a.blocking),
    unproven_live_flags: unproven,
    boot_markers: bootMarkers,
    readout_rules: readoutRules,
    stale_facts: staleFacts,
  },
  rounds_run: Math.min(round, MAX_ROUNDS),
  complete: openGaps.length === 0,
  open_gaps: openGaps.map(g => g.question),
  research_questions: research,
  reports: reports.map(r => ({ round: r.round, key: r.key, report: r.res.report_markdown })),
  results: results.map(v => ({
    round: v.round, source: v.source, id: v.claim.id, kind: v.claim.kind, claim: v.claim.claim,
    verdict: v.judgment ? v.judgment.verdict : 'NO JUDGMENT',
    replication_agrees: v.judgment ? v.judgment.replication_agrees : 'no_data',
    narrowed: v.judgment ? v.judgment.narrowed_claim : '',
    basis: v.judgment ? v.judgment.basis : '',
    replication: v.replication ? clip(v.replication.answer, 1500) : '',
    refutation: v.refutation ? `${v.refutation.verdict}: ${clip(v.refutation.narrowed_claim, 800)}` : '',
  })),
  critic,
}
