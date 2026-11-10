// Claude Code Workflow skeleton: implement -> (independent verify + cross-review) -> fix round 1.
//
// CLAUDE CODE ONLY. This is a script for Claude Code's Workflow tool, not a Node
// program. Copy it to your session, fill in ARGS (or pass them as the Workflow `args`),
// and run it. The lead reads the round-1 results before deciding on fix round 2: that
// is a second run of this script with `round: 2` and the round-2 findings, not a loop
// here. See references/claude-code.md for why each guard exists.
//
// Every lane brief points to .agents/skills/aelix-issue-run/lane-rules.md and fills its
// "Batch facts" block. Do not paste rules into the prompts below.

export const meta = {
  name: 'aelix-lane-round',
  description: 'One aelix lane round: implement (or fix), then an independent verify and a cross-review in parallel',
  phases: [
    { title: 'Implement', detail: 'one lane per worktree, wip checkpoints, no full suite' },
    { title: 'Verify', detail: 'fresh-context verify and one serialized cross-review' },
  ],
}

// --- Inputs ------------------------------------------------------------------------
// args = {
//   round: 1,                              // 1: implement; 2: fix round (pass findings)
//   rulesPath: '<worktree>/.agents/skills/aelix-issue-run/lane-rules.md',
//   lanes: [{
//     key: '710', issues: [710, 709], worktree: '/abs/worktree', branch: 'fix/710-...',
//     base: '<sha>', scratch: '/tmp/710-impl', evidence: '/abs/durable/evidence/710',
//     baseWorktree: '/tmp/710-verify/base-wt', briefPath: '/abs/path/710-impl-brief.md',
//   }],
// }
const ARGS = args || {}
const LANES = ARGS.lanes || []
const ROUND = ARGS.round || 1
if (!LANES.length) throw new Error('no lanes: pass args.lanes')

// The model every agent() names. Omitting `model` inherits the session's model.
const MODEL = 'opus'
const CROSS_MODEL = 'sonnet' // the cross-review agent only drives the other CLI

const TAIL = `

Your final message is your return value, given through the StructuredOutput tool. Use plain strings with newlines escaped and keep every string under its limit. Put long evidence in a file under your scratch dir and cite it by path. Stay in your turn until everything you started has finished. Never end the turn to wait.`

const short = (n) => ({ type: 'string', maxLength: n })
const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    head: short(64),
    checkpoints: { type: 'array', items: { type: 'object', properties: { sha: short(64), subject: short(200) }, required: ['sha', 'subject'] } },
    evidence_path: short(300),
    gates: short(2000),
    live: short(1500),
    open_items: { type: 'array', items: short(400) },
  },
  required: ['head', 'checkpoints', 'evidence_path', 'gates', 'live', 'open_items'],
}
const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['READY', 'NOT READY'] },
    head_verified: short(64),
    report: short(6000), // the report itself; command output stays in files under evidence_dir
    evidence_dir: short(300),
    findings: { type: 'array', items: { type: 'object', properties: {
      id: short(20), severity: { type: 'string', enum: ['must-fix', 'should-fix', 'nit'] },
      summary: short(500), reproducer: short(300) }, required: ['id', 'severity', 'summary'] } },
    not_measured: { type: 'array', items: short(300) },
  },
  required: ['verdict', 'head_verified', 'report', 'findings', 'not_measured'],
}
const CROSS_SCHEMA = {
  type: 'object',
  properties: {
    completed: { type: 'boolean' },
    failure: short(400),
    raw_path: short(300),
    findings: { type: 'array', items: { type: 'object', properties: { id: short(20), summary: short(600) }, required: ['id', 'summary'] } },
  },
  required: ['completed', 'raw_path', 'findings'],
}

// --- Guards ------------------------------------------------------------------------
// agent() returns null on a 529, a session limit, a skipped agent or a StructuredOutput
// parse failure. Retry once. If it is still null, throw: pipeline() drops that lane to
// null and the other lanes carry on. Do not retry more: a session limit makes every
// retry fail within seconds. After the reset, resume with resumeFromRunId (completed
// agents replay from the cache).
async function robust(prompt, opts) {
  for (let attempt = 1; attempt <= 2; attempt++) {
    const r = await agent(prompt, { ...opts, label: attempt === 1 ? opts.label : `${opts.label}-retry`, model: opts.model || MODEL })
    if (r) return r
    log(`${opts.label}: null result (attempt ${attempt}). Check the worktree for partial edits before resuming.`)
  }
  throw new Error(`${opts.label} returned nothing twice: audit its worktree, patch only its prompt, then resume with resumeFromRunId`)
}

// One cross-review at a time: the other model's quota is shared.
let crossChain = Promise.resolve()
function serialCross(prompt, opts) {
  const p = crossChain.then(() => agent(prompt, opts))
  crossChain = p.catch(() => null)
  return p
}

// --- Prompts -----------------------------------------------------------------------
function implPrompt(l) {
  const role = ROUND === 1 ? 'implementation lane' : `fix round ${ROUND - 1} lane`
  return `You are the ${role} "${l.key}" for aelix issue(s) ${l.issues.map((n) => '#' + n).join(' ')}.
Read ${ARGS.rulesPath} first (binding), then your brief at ${l.briefPath}, and carry it out completely in ${l.worktree}.
Scratch: ${l.scratch}/. Evidence: ${l.evidence}/.` + TAIL
}

function verifyPrompt(l, impl) {
  // Deliberately NOT given: the implementer's evidence, summary or claims.
  return `Independent verify of aelix issue(s) ${l.issues.map((n) => '#' + n).join(' ')} at head ${impl.head} on branch ${l.branch}.
Lane worktree (read only, do not edit or commit): ${l.worktree}. Base: ${l.base}, with a base worktree at ${l.baseWorktree} (create it with its own venv if missing).
Round: ${ROUND === 1 ? 'round 1, full' : `delta only: git range-diff against the previous round's head`}. Scratch: ${l.scratch}-verify/, for probes and command output (evidence_dir). Your report is your final message (the report field), not a file.
Follow your agent instructions (aelix-verifier). Read ${ARGS.rulesPath} for the repository rules.` + TAIL
}

function crossPrompt(l, impl) {
  const dir = `${l.scratch}-cross`
  const ask = `Contract-correctness review of commits ${l.base}..${impl.head} in this repository, which implement issues ${l.issues.map((n) => '#' + n).join(' ')}. For each issue: (1) list concrete inputs (arguments, key sequences, error shapes, modes, adapters, interleavings) for which the new behaviour differs from what the issue and the commit message promise, and show the code path; (2) list other entry points that reach the same behaviour but were not changed; (3) list tests that would still pass if the core condition were reverted. Name the symbol or test for every claim. Report only what you verified by reading or running code, and say which. A clean category is a valid answer.`
  return `You run one cross-review with the Codex CLI and return its findings. You do not review the code yourself.
1. mkdir -p ${dir} and write this prompt to ${dir}/prompt.txt:
---
${ask}
---
2. Start it with ONE Bash call in the background: cd ${l.worktree} && codex exec -C ${l.worktree} -s read-only "$(cat ${dir}/prompt.txt)" < /dev/null > ${dir}/review.txt 2>&1; echo "rc=$?" >> ${dir}/review.txt
3. Poll in the foreground with bounded loops of 4 minutes or less (for i in $(seq 23); do grep -q '^rc=' ${dir}/review.txt && break; sleep 10; done; tail -c 600 ${dir}/review.txt), and re-issue until the rc= line appears.
4. If it ended on a usage limit or a content filter, set completed=false, give the reason, and salvage the concrete leads from its interim messages.
5. Return one finding per distinct claim, copied faithfully. Do not judge them.` + TAIL
}

// --- Run ---------------------------------------------------------------------------
const results = await pipeline(
  LANES,
  (l) => robust(implPrompt(l), { label: `impl-${l.key}`, phase: 'Implement', schema: IMPL_SCHEMA }),
  async (impl, l) => {
    log(`${l.key}: head ${impl.head}, ${impl.checkpoints.length} checkpoints; starting verify and cross-review`)
    const [verify, cross] = await Promise.all([
      robust(verifyPrompt(l, impl), { label: `verify-${l.key}`, phase: 'Verify', schema: VERIFY_SCHEMA, agentType: 'aelix-verifier' }),
      ROUND === 1
        ? serialCross(crossPrompt(l, impl), { label: `cross-${l.key}`, phase: 'Verify', schema: CROSS_SCHEMA, model: CROSS_MODEL, effort: 'low' })
        : Promise.resolve(null), // the cross-review runs in round 1 only
    ])
    return { lane: l.key, impl, verify, cross }
  },
)
return results
