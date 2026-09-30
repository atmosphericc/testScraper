export const meta = {
  name: 'post-run',
  description: 'Post-mortem a bot run in rounds until a round adds nothing: event-store facts, analysts routed to the wall that lost units, every load-bearing claim checked by a BLIND replicator (never sees the claimed value) + a refuter + a neutral judge, then a critic whose gaps start the next round. Optional answer key + known-false canaries grade the process itself. Analysis only - never edits code, arms flags or launches anything.',
  whenToUse: 'After every run with a restock, an anomaly or an operator complaint. args (all optional): {run: "run_20260930_014647", note: "operator note", max_rounds: 3, max_claims: 3, max_gaps: 3, answer_key: ["finding", ...], canaries: [{claim, check_against, question, expected, kind}]}. answer_key and canaries exist to grade the workflow; they are never shown to analysts, replicators, refuters or judges.',
  phases: [
    { title: 'Facts', detail: 'event store build + saved queries + regime check' },
    { title: 'Investigate', detail: 'round 1: analysts routed by the wall that lost units; later rounds: one agent per critic gap' },
    { title: 'Replicate', detail: 'blind re-measurement from a question that does not reveal the claimed answer' },
    { title: 'Refute', detail: 'adversarial check of the bare claim, no reasoning attached' },
    { title: 'Judge', detail: 'neutral verdict: confirmed only when the blind measurement agrees and the refutation fails' },
    { title: 'Critic', detail: 'gaps that could change units lost or a fix decision; none new = done' },
    { title: 'Grade', detail: 'optional: canaries must not be confirmed; recall against the answer key' },
  ],
}

// Why this shape (operator, 2026-09-30: "loop repeatedly til complete or figure out a way
// to be 100% confident and not have inherent bias"):
// - LOOP UNTIL DRY: the critic's gaps become the next round's questions; a round with no
//   new gap ends the run. max_rounds bounds cost; unfinished gaps are logged, never hidden.
// - NO ANCHORING: the replicator gets a question, not the claim or its number, so it
//   measures instead of confirming. Counting questions go to a different model tier than
//   the analyst (log-miner / Sonnet) to decorrelate errors.
// - NO SELF-GRADING: the analyst never verifies itself; the refuter sees no reasoning; the
//   judge only weighs what the two independent checks produced.
// - CALIBRATION: canaries (known-false claims) run through the same pipeline under neutral
//   ids; a confirmed canary means the verification stage is rubber-stamping.
// - Facts come from ONE tested parser (tools/events/build.py -> logs/events/events.sqlite),
//   queried with saved SQL (tools/events/q.py), never from a fresh regex per night.

const A = args || {}
const RUN = A.run ? String(A.run) : ''
const NOTE = A.note ? String(A.note) : ''
const clampInt = (v, lo, hi, d) => { const n = Number(v); return Number.isFinite(n) ? Math.max(lo, Math.min(hi, Math.round(n))) : d }
const MAX_ROUNDS = clampInt(A.max_rounds, 1, 5, 3)
const MAX_CLAIMS = clampInt(A.max_claims, 1, 6, 4)
const MAX_GAPS = clampInt(A.max_gaps, 0, 6, 3)
const KEY = Array.isArray(A.answer_key) ? A.answer_key.map(String) : []
const CANARIES = Array.isArray(A.canaries) ? A.canaries : []

const DOCTRINE = `
Repo: C:\\Users\\elric\\Desktop\\testScraper (Windows; the Bash tool is Git Bash).
Read .claude/agent-context.md sections 1, 2, 2C and 4 first. HARD RULES: never launch the bot, a browser, a harvester, a login or a live checkout; never blanket-run tests/; never print config/proxyIps.json; read-only (write only under logs/analysis_*/ when told).
Facts come from the event store: python tools/events/build.py --only <run_id> (idempotent), then python tools/events/q.py <regime|regime_tcin|monitor_hours|walls|windows|checkout|per_tcin|arms> --run <run_id>, or q.py "<SQL>" (tables: runs, shots, races, flips, windows, tickets, loop_ends, decoys, monitor_stats, orders, unparsed). Grep the raw log only for what the store does not parse, and say so.
Gate model (C-0930-03): edge limiter first (429 ERR_A2C_TCIN_RATE_LIMITED, ~130 ms, no x-ssx-hop) -> SSX hop (keyless 401 = the Shape verdict) -> cart service (201 / FAST_SELLING 429 / 424). A 401 is a shot that got PAST the limiter. Segment by account, first vs later shot, and network.
Tag every claim [MEASURED]/[REPORTED]/[INFERRED]/[NOT ESTABLISHED]/[REFUTED] with n and path:line; report the unmatched remainder of every tally. Baselines: .claude/state/CURRENT_STATE.md (newest run on top; REGIME WATCH near the end).
` + (NOTE ? `\nOPERATOR NOTE: ${NOTE}\n` : '')

// ── schemas ─────────────────────────────────────────────────────────────────
const FACTS_SCHEMA = {
  type: 'object',
  properties: {
    run_id: { type: 'string' }, log_path: { type: 'string' },
    restock: { type: 'boolean', description: 'At least one in-stock window with a race.' },
    windows: { type: 'integer' }, races: { type: 'integer' }, shots: { type: 'integer' },
    wall1_first: { type: 'string', description: 'first shots past the limiter / n' },
    wall1_later: { type: 'string' },
    wall2_denied: { type: 'string', description: '401 among shots past the limiter' },
    carts: { type: 'integer' }, tickets: { type: 'integer' }, orders: { type: 'integer' },
    loop_end_reasons: { type: 'array', items: { type: 'string' } },
    regime_flags: { type: 'array', items: { type: 'string' } },
    unparsed_total: { type: 'integer' },
    tables_md: { type: 'string' },
    gaps: { type: 'array', items: { type: 'string' } },
  },
  required: ['run_id', 'restock', 'shots', 'carts', 'orders', 'regime_flags', 'tables_md'],
}

const CLAIM_ITEM = {
  type: 'object',
  properties: {
    id: { type: 'string' },
    claim: { type: 'string', description: 'Bare, falsifiable, no reasoning attached.' },
    check_against: { type: 'string', description: 'Files / log lines / queries where it can be checked. Pointers only - NO numbers, NO conclusions.' },
    kind: { type: 'string', enum: ['count', 'code', 'causal'], description: 'count = a number from the logs; code = what the code / .bat does; causal = why something happened.' },
    question: { type: 'string', description: 'A question whose independent answer would confirm or refute the claim WITHOUT revealing the claimed value or the conclusion (e.g. "How many ... and what did each return?", never "Confirm that 248 ...").' },
    expected: { type: 'string', description: 'The claim\'s own answer to that question, concise (withheld from the replicator).' },
  },
  required: ['id', 'claim', 'check_against', 'kind', 'question', 'expected'],
}

const ANALYSIS_SCHEMA = {
  type: 'object',
  properties: {
    report_markdown: { type: 'string', description: 'Lead with the answer; tagged claims with n; at most ~1,500 words.' },
    claims: { type: 'array', description: 'The 1-4 LOAD-BEARING claims your conclusions or fix specs rest on.', items: CLAIM_ITEM },
    fix_specs: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' }, title: { type: 'string' },
          kind: { type: 'string', enum: ['fix', 'experiment', 'instrument'] },
          flag: { type: 'string', description: 'Env flag; default = current behaviour. Prefer a per-account scope when the effect is uncertain.' },
          change: { type: 'string' }, where: { type: 'string' },
          readout_rule: { type: 'string', description: 'Pre-registered: the saved query + worked / failed / inconclusive rule with the n it needs.' },
          kill_rule: { type: 'string' }, risk: { type: 'string', description: 'Including any double-order risk.' },
          expected_value: { type: 'string' },
          rests_on_claims: { type: 'array', items: { type: 'string' } },
        },
        required: ['id', 'title', 'kind', 'change', 'where', 'readout_rule', 'kill_rule', 'risk', 'rests_on_claims'],
      },
    },
  },
  required: ['report_markdown', 'claims', 'fix_specs'],
}

const REPLICATION_SCHEMA = {
  type: 'object',
  properties: {
    answer: { type: 'string', description: 'Your own answer to the question, with the numbers you measured.' },
    method: { type: 'string', description: 'The exact command / SQL / regex / code path you used.' },
    evidence: { type: 'string', description: 'path:line / log:line references and n; the unmatched remainder.' },
    status: { type: 'string', enum: ['measured', 'partial', 'could_not_measure'] },
  },
  required: ['answer', 'method', 'evidence', 'status'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    claim_id: { type: 'string' },
    verdict: { type: 'string', enum: ['CONFIRMED', 'PARTIALLY CONFIRMED', 'REFUTED', 'UNVERIFIABLE'] },
    narrowed_claim: { type: 'string' },
    evidence: { type: 'string' },
    what_would_settle: { type: 'string' },
  },
  required: ['claim_id', 'verdict', 'narrowed_claim', 'evidence'],
}

const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    claim_id: { type: 'string' },
    verdict: { type: 'string', enum: ['CONFIRMED', 'PARTIALLY CONFIRMED', 'REFUTED', 'UNVERIFIABLE'] },
    replication_agrees: { type: 'string', enum: ['yes', 'partly', 'no', 'no_data'] },
    narrowed_claim: { type: 'string', description: 'The strongest version the evidence supports, or what is true instead.' },
    basis: { type: 'string', description: 'Quote the claimed value and the replicated value; say which check decided it and anything you inspected yourself.' },
  },
  required: ['claim_id', 'verdict', 'replication_agrees', 'narrowed_claim', 'basis'],
}

const AGENTS = ['antibot-analyst', 'purchase-flow-engineer', 'stock-pipeline-analyst', 'failure-forensics', 'log-miner']
const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    gaps: {
      type: 'array',
      description: 'ONLY a question whose answer could flip a verdict in this report, or change which fix spec is eligible or ranked first. Everything exploratory, historical or merely interesting goes to research_questions. Empty when the decision is settled.',
      items: {
        type: 'object',
        properties: {
          question: { type: 'string' },
          why: { type: 'string' },
          agent: { type: 'string', enum: AGENTS },
        },
        required: ['question', 'why', 'agent'],
      },
    },
    research_questions: {
      type: 'array',
      description: 'Worth answering later, but no answer would change a verdict or a fix decision in THIS report. These do not start another round.',
      items: { type: 'object', properties: { question: { type: 'string' }, why: { type: 'string' } }, required: ['question', 'why'] },
    },
    disagreements: { type: 'array', items: { type: 'string' } },
    specs_on_weak_claims: { type: 'array', items: { type: 'string' } },
    stale_facts_to_correct: { type: 'array', items: { type: 'string' }, description: 'file:line carrying a belief this run contradicts' },
  },
  required: ['gaps', 'disagreements', 'specs_on_weak_claims', 'stale_facts_to_correct'],
}

const GRADE_SCHEMA = {
  type: 'object',
  properties: {
    key: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          item: { type: 'string' },
          status: { type: 'string', enum: ['found', 'partly', 'missed', 'contradicted'] },
          matched_claim_ids: { type: 'array', items: { type: 'string' } },
          note: { type: 'string' },
        },
        required: ['item', 'status', 'matched_claim_ids'],
      },
    },
    recall: { type: 'string', description: 'found + 0.5 x partly, over the key size' },
    notes: { type: 'string' },
  },
  required: ['key', 'recall'],
}

// ── prompts ─────────────────────────────────────────────────────────────────
const replicatePrompt = (c) => DOCTRINE + `
You are given a QUESTION, not a claim. Answer it yourself from primary evidence (the raw log, the code, run_bot_with_nightly_restart.bat, the event store). Nobody has told you what answer to expect; do not guess one and do not look for another agent's report or docs/CLAIMS.md / CURRENT_STATE.md to copy from. Say exactly what you measured, how (command / SQL / regex / path:line), n, and the unmatched remainder. If you cannot measure it, say so (status could_not_measure).

QUESTION: ${c.question}

WHERE TO LOOK: ${c.check_against}`

const refutePrompt = (c) => `Verify this claim from a fresh context. Assume it is FALSE until primary evidence (the code, the raw logs, the event store) forces otherwise. Do not look for the reasoning that produced it; docs, CURRENT_STATE.md, CLAIMS.md and other agents' reports are not evidence. Hunt the counter-case: the code path where it does not hold, the flag the .bat does not arm, the sample where the number reverses, the confound. Never run the bot or anything that launches a browser. Repo: C:\\Users\\elric\\Desktop\\testScraper. The Grep tool skips logs/runs/ when rooted at logs/; print-only markers are often glued onto the next logger line.

CLAIM ${c.id}: ${c.claim}

Where it can be checked: ${c.check_against}

Return the verdict, the narrowest version the evidence supports, the evidence with path:line / log:line and n, and what would settle it if unverifiable.`

const clip = (s, n) => { const t = String(s == null ? '' : s); return t.length > n ? t.slice(0, n) + ' …[clipped]' : t }

const judgePrompt = (c, rep, ref) => `You are a neutral judge. You did not produce this claim and have no stake in it. Two independent checks were made: a REPLICATION by an agent that saw only the question (never the claim or its value), and an ADVERSARIAL CHECK by an agent that saw only the bare claim and tried to refute it.

CLAIM ${c.id}: ${c.claim}
REPLICATION QUESTION: ${c.question}
THE CLAIM'S OWN ANSWER TO THAT QUESTION: ${c.expected}

INDEPENDENT REPLICATION: ${rep ? clip(JSON.stringify(rep), 6000) : '(the replicator returned nothing)'}

ADVERSARIAL CHECK: ${ref ? clip(JSON.stringify(ref), 6000) : '(the refuter returned nothing)'}

Rules:
- CONFIRMED only if the replication independently measured a value that agrees with the claim's answer (within any tolerance the claim itself states) AND the adversarial check found no counter-evidence that survives.
- PARTIALLY CONFIRMED when a narrower version is supported; write that narrower version.
- REFUTED when the replication materially disagrees, or the adversarial check's counter-evidence holds.
- UNVERIFIABLE when neither check could measure it.
- If the two checks conflict, you may inspect primary evidence yourself, read-only (repo C:\\Users\\elric\\Desktop\\testScraper; never launch anything), to break the tie; say what you checked.
Quote the claimed value and the replicated value in basis.`

const pickAgent = (name) => (AGENTS.includes(name) ? name : 'failure-forensics')

// ── verification of one claim: blind replicate || refute -> judge ───────────
const verifyClaim = (c, tag) => {
  const kind = ['count', 'code', 'causal'].includes(c.kind) ? c.kind : 'causal'
  const repOpts = kind === 'count'
    ? { label: `replicate:${tag}:${c.id}`, phase: 'Replicate', agentType: 'log-miner', model: 'sonnet', schema: REPLICATION_SCHEMA, effort: 'high' }
    : { label: `replicate:${tag}:${c.id}`, phase: 'Replicate', agentType: 'claims-verifier', schema: REPLICATION_SCHEMA, effort: 'high' }
  return parallel([
    () => agent(replicatePrompt(c), repOpts),
    () => agent(refutePrompt(c), { label: `refute:${tag}:${c.id}`, phase: 'Refute', agentType: 'claims-verifier', schema: VERDICT_SCHEMA, effort: 'high' }),
  ]).then(([rep, ref]) =>
    agent(judgePrompt(c, rep, ref), { label: `judge:${tag}:${c.id}`, phase: 'Judge', agentType: 'claims-verifier', schema: JUDGE_SCHEMA, effort: 'high' })
      .then(j => ({ claim: c, replication: rep, refutation: ref, judgment: j })))
}

// ── Phase 1: facts ──────────────────────────────────────────────────────────
phase('Facts')
const facts = await agent(DOCTRINE + `
YOUR TASK: establish the facts of run ${RUN || 'the newest logs/runs/run_*.log larger than 1 MB (by modification time)'}.
1. Build the store for that run; report the per-marker unparsed remainder.
2. Run q.py regime, regime_tcin, monitor_hours, walls, windows, checkout and per_tcin for it (monitor_hours also for the previous run, for a like-for-like hour comparison); put the tables in tables_md (trim long ones to 40 rows and say so). If q.py arms returns rows, include it.
3. Compare against CURRENT_STATE.md REGIME WATCH: list in regime_flags every metric that moved more than ~3x, went to zero over a meaningful n, showed a new error string, or recovered - with both numbers. For every rate-based flag also compute it PER TCIN and with the TCIN that has the most shots left out; a flag that does not survive both is COMPOSITION (which SKUs restocked), not a regime change - say which. Compare monitor loss like-for-like: the same clock hours on earlier restock and no-stock nights, not a whole-run average.
Return the schema; put anything the store could not answer in gaps.`,
  { label: 'facts', phase: 'Facts', agentType: 'log-miner', model: 'sonnet', schema: FACTS_SCHEMA })
if (!facts) {
  log('facts agent returned nothing - stopping (read the journal)')
  return { facts: null }
}
log(`run ${facts.run_id}: restock=${facts.restock} shots=${facts.shots} carts=${facts.carts} orders=${facts.orders} regime_flags=${(facts.regime_flags || []).length}`)

const FACTS_BLOCK = `\nFACTS FROM THE EVENT STORE (run ${facts.run_id}, ${facts.log_path || ''}) - a starting point; re-derive anything load-bearing:\n` +
  `restock=${facts.restock} windows=${facts.windows} races=${facts.races} shots=${facts.shots} wall1_first=${facts.wall1_first} wall1_later=${facts.wall1_later} wall2_denied=${facts.wall2_denied} carts=${facts.carts} tickets=${facts.tickets} orders=${facts.orders}\n` +
  `loop end reasons: ${(facts.loop_end_reasons || []).join('; ') || '-'}\nregime flags: ${(facts.regime_flags || []).join('; ') || 'none'}\n${clip(facts.tables_md, 12000)}\n`

const CLAIM_RULES = `
CLAIMS: for each load-bearing claim give kind, a replication QUESTION that an independent agent can answer from primary evidence WITHOUT being told your number or conclusion, and your own answer to it (expected). check_against holds pointers only - no numbers, no conclusions. The claims go to a blind replicator, a refuter and a judge; a claim whose question leaks its answer is wasted.`

// ── round 1 routing ─────────────────────────────────────────────────────────
const round1 = []
if (!facts.restock) {
  round1.push({ key: 'missed-restock', agentType: 'stock-pipeline-analyst', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: no raced restock in this run. Did we MISS one, or did none happen? Sweep loss by hour, RedSky 206 bursts, blind intervals, TCIN visibility, ground-truth reads, any in-stock read that failed to dispatch; compare with the base rate (zero-stock nights are the norm).` + CLAIM_RULES })
} else {
  round1.push({ key: 'carting', agentType: 'antibot-analyst', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the carting walls. For every shot class (account x first/later x network): how many died at the limiter (ERR_A2C), passed it and died at the SSX hop (keyless 401), were admitted and throttled (FAST_SELLING), carted. Compare with CURRENT_STATE baselines; read any registered experiment arm (q.py arms) exactly as pre-registered. Name the single highest-loss stage and separate Target's limits from OUR behaviour (cadence, dispatch, gates, what the bot did after an admitted shot).` + CLAIM_RULES })
  if ((facts.carts || 0) > 0) {
    round1.push({ key: 'checkout', agentType: 'purchase-flow-engineer', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: every won cart, hop by hop with elapsed times: in-chain pre_checkout / place-order, the won-cart loop (tickets, gaps, statuses, loop end reasons), and what happened to the line afterwards. For each cart answer plainly: did TARGET end it (evicted, out of stock, 424) or did OUR limits end it (a cap, a deadline, a gate, a delete)? Was the line proven present and the TCIN live when it ended? Cite path:line for every gate and the armed value in run_bot_with_nightly_restart.bat.` + CLAIM_RULES })
  }
}
if ((facts.regime_flags || []).length > 0) {
  round1.push({ key: 'regime', agentType: 'failure-forensics', prompt: DOCTRINE + FACTS_BLOCK + `
YOUR TASK: the regime flags above. For each: real (re-derive from the raw log)? when did it start? Target-side or ours? how long did we take to notice (the detection gap)? Draft the docs/TARGET_CHANGES.md entry text in your report (do not write the file).` + CLAIM_RULES })
}

// ── rounds: investigate -> (replicate || refute) -> judge -> critic ──────────
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

const canaryIds = CANARIES.map((_, i) => `X${i + 1}`)
const canaryClaims = CANARIES.map((c, i) => ({
  id: canaryIds[i], claim: String(c.claim || ''), check_against: String(c.check_against || ''),
  kind: ['count', 'code', 'causal'].includes(c.kind) ? c.kind : 'count',
  question: String(c.question || ''), expected: String(c.expected || ''),
}))

while (round <= MAX_ROUNDS && jobs.length > 0) {
  const R = round
  log(`round ${R}: ${jobs.map(j => j.key).join(', ')}${R === 1 && canaryClaims.length ? ` (+${canaryClaims.length} calibration claims)` : ''}`)
  jobs.forEach(j => investigated.push(`r${R}:${j.key}${j.question ? ' - ' + j.question : ''}`))
  const analysisP = pipeline(
    jobs,
    (j) => agent(j.prompt, { label: `r${R}:${j.key}`, phase: 'Investigate', agentType: j.agentType, schema: ANALYSIS_SCHEMA, effort: 'xhigh' }),
    (res, j) => {
      if (!res) return []
      reports.push({ round: R, key: j.key, report: res.report_markdown, fix_specs: res.fix_specs || [], claims: res.claims || [] })
      const cl = (res.claims || []).map((c, i) => Object.assign({}, c, { id: `${j.key}.${c.id || i + 1}` }))
      if (cl.length > MAX_CLAIMS) log(`r${R}:${j.key}: ${cl.length} claims; verifying ${MAX_CLAIMS}; NOT verified: ${cl.slice(MAX_CLAIMS).map(c => c.id).join(', ')}`)
      return parallel(cl.slice(0, MAX_CLAIMS).map(c => () => verifyClaim(c, `r${R}`)))
        .then(vs => vs.filter(Boolean).map(v => Object.assign({ round: R, source: j.key }, v)))
    },
  )
  const canaryP = (R === 1 && canaryClaims.length)
    ? parallel(canaryClaims.map(c => () => verifyClaim(c, 'r1'))).then(vs => vs.filter(Boolean).map(v => Object.assign({ round: 1, source: 'calibration' }, v)))
    : Promise.resolve([])
  const [analysed, calib] = await Promise.all([analysisP, canaryP])
  analysed.filter(Boolean).forEach(arr => (arr || []).forEach(v => results.push(v)))
  calib.forEach(v => results.push(v))

  // Critic: sees every report and every judged verdict EXCEPT the calibration claims.
  phase('Critic')
  const digest = reports.map(r => `## r${r.round}:${r.key}\n${clip(r.report, 7000)}\nFIX SPECS: ${(r.fix_specs || []).map(f => `${f.id} ${f.title} (rests on ${(f.rests_on_claims || []).join(', ') || '-'})`).join('; ') || '-'}`).join('\n\n')
  const verdicts = results.filter(v => v.source !== 'calibration').map(v =>
    `- ${v.claim.id} [${v.source}]: ${v.claim.claim} => ${v.judgment ? `${v.judgment.verdict} (replication ${v.judgment.replication_agrees}; ${clip(v.judgment.narrowed_claim, 400)})` : 'NO JUDGMENT'}`).join('\n')
  critic = await agent(DOCTRINE + FACTS_BLOCK + `
YOUR TASK: completeness critic after round ${R}. Below: every report so far and each claim's verdict (a blind replication + an adversarial check + a neutral judge). Put in gaps ONLY a question whose answer could flip a verdict below, or change which fix spec is eligible or ranked first - each as one answerable question with the agent best placed to answer it. Put everything else worth knowing (history, mechanism, curiosity) in research_questions; those do NOT start another round. The loop exists to settle decisions, not to exhaust the topic. Do NOT repeat anything already investigated:
${investigated.join('\n')}
Also list disagreements between reports / verdicts / the event store, fix specs resting on claims that are not CONFIRMED (say whether a PARTIALLY CONFIRMED narrowing still supports them), and file:line of any doc, memory or code comment this run contradicts. Return an empty gaps list when the investigation is complete.

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
A completeness critic reviewing this post-mortem asked a question nobody has answered yet:
QUESTION: ${g.question}
WHY IT MATTERS: ${g.why}
Answer it from primary evidence. Report what you found, including a negative result.` + CLAIM_RULES,
  }))
  round++
}
if (round > MAX_ROUNDS && openGaps.length) log(`stopped at max_rounds=${MAX_ROUNDS}; OPEN GAPS NOT INVESTIGATED: ${openGaps.map(g => g.question).join(' | ')}`)

// ── deterministic summaries ─────────────────────────────────────────────────
const verdictOf = (id) => { const r = results.find(v => v.claim.id === id); return r && r.judgment ? r.judgment.verdict : 'NOT VERIFIED' }
const specs = reports.flatMap(r => (r.fix_specs || []).map(f => {
  const ids = (f.rests_on_claims || []).map(x => (String(x).startsWith(r.key + '.') ? String(x) : `${r.key}.${x}`))
  const vs = ids.map(verdictOf)
  const ok = ids.length > 0 && vs.every(v => v === 'CONFIRMED' || v === 'PARTIALLY CONFIRMED')
  return { source: `r${r.round}:${r.key}`, id: f.id, title: f.title, kind: f.kind, flag: f.flag, change: f.change, where: f.where,
    readout_rule: f.readout_rule, kill_rule: f.kill_rule, risk: f.risk, expected_value: f.expected_value,
    rests_on: ids.map((x, i) => `${x}=${vs[i]}`), eligible: ok }
}))
const calibration = results.filter(v => v.source === 'calibration').map(v => ({
  id: v.claim.id, verdict: v.judgment ? v.judgment.verdict : 'NO JUDGMENT',
  pass: !!(v.judgment && (v.judgment.verdict === 'REFUTED' || v.judgment.verdict === 'UNVERIFIABLE')),
  review: !!(v.judgment && v.judgment.verdict === 'PARTIALLY CONFIRMED'),
}))
if (calibration.length) log(`calibration: ${calibration.filter(c => c.pass).length}/${calibration.length} known-false claims refuted; FALSE CONFIRMS: ${calibration.filter(c => c.verdict === 'CONFIRMED').map(c => c.id).join(', ') || 'none'}`)

// ── optional grading against an answer key (never shown to anyone above) ──
let grade = null
if (KEY.length) {
  phase('Grade')
  const judged = results.filter(v => v.source !== 'calibration').map(v =>
    `- ${v.claim.id}: ${v.claim.claim} => ${v.judgment ? v.judgment.verdict + ' / narrowed: ' + clip(v.judgment.narrowed_claim, 500) : 'NO JUDGMENT'}`).join('\n')
  grade = await agent(`You grade a post-mortem workflow against an answer key of findings established earlier by independent verification. For each key item decide: found (a CONFIRMED or PARTIALLY CONFIRMED claim below states it, possibly in other words), partly (only part of it), missed (nothing states it), contradicted (a CONFIRMED claim states the opposite). Be strict about substance, lenient about wording. Do not use tools.

ANSWER KEY:
${KEY.map((k, i) => `K${i + 1}. ${k}`).join('\n')}

THE WORKFLOW'S JUDGED CLAIMS:
${judged || '(none)'}`, { label: 'grade', phase: 'Grade', schema: GRADE_SCHEMA, effort: 'high' })
}

return {
  facts,
  rounds_run: Math.min(round, MAX_ROUNDS),
  complete: openGaps.length === 0,
  open_gaps: openGaps.map(g => g.question),
  research_questions: research,
  reports: reports.map(r => ({ round: r.round, key: r.key, report: r.report })),
  results: results.map(v => ({
    round: v.round, source: v.source, id: v.claim.id, kind: v.claim.kind, claim: v.claim.claim,
    verdict: v.judgment ? v.judgment.verdict : 'NO JUDGMENT',
    replication_agrees: v.judgment ? v.judgment.replication_agrees : 'no_data',
    narrowed: v.judgment ? v.judgment.narrowed_claim : '',
    basis: v.judgment ? v.judgment.basis : '',
    replication: v.replication ? clip(v.replication.answer, 1500) : '',
    refutation: v.refutation ? `${v.refutation.verdict}: ${clip(v.refutation.narrowed_claim, 800)}` : '',
  })),
  fix_specs: specs,
  critic,
  calibration,
  grade,
}
