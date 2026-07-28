# P3 kickoff — handoff for a fresh session

**Written:** 2026-07-27, at the close of P2. **Read this first, then the two source documents below.**
Nothing here is a plan. The P3 plan is built in the new session, the same way P1 and P2 were:
recon → draft → adversarial review → owner decision → implement → verify → review → merge.

---

## 1. Where the tree is

- `main` = `ca8fc24`, identical to `origin/main`. Working tree carries only unrelated untracked
  leftovers (brand assets under `docs/assets/`, root `AGENTS.md` / `CLAUDE.md`, an old session HTML,
  `.omc` scratch). **Do not sweep these into a P3 commit.**
- Shipped: **P1** (`fe64f34`, ADR-0196) agent-profile identity — `--agent`, `/agents list|show|use`,
  the profile format and discovery. **P2** (`f6d8657` → merge `283e2fc`, ADR-0197 + ADR-0198) the
  subagent runtime seam, the bundled `aelix_agents` extension, single-mode delegation, the posture
  clamp and spawn-time consent.
- Full suite at this commit: **6684 passed / 1 skipped**; ruff + pyright clean; kernel diff empty.

## 2. The four documents that matter (all committed)

| Document | What it is |
|---|---|
| `.omc/specs/multiagent-profiles-teams-architecture-spec.md` | The ratified 3-band architecture. §8 lists the phases; §9 the ADR plan; §10 the non-goals. |
| `docs/decisions/0197-subagent-runtime-seam-and-aelix-agents.md` | P2's decisions **and its "Deferred deliberately" section — that list is the real P3 backlog.** |
| `docs/decisions/0198-print-mode-json-envelope-contract.md` | The child event-envelope contract. Anything that parses child output is bound by it. |
| `.omc/specs/p2-subagent-runtime-plan.md` | P2's implementation plan (141 KB). Its §12 work breakdown is the template for splitting P3 across agents with disjoint file ownership. |

## 3. P3 candidates — and the scope fork the owner must settle first

The ratified spec's Phase 3 and the owner's stated interest are **not the same list**. Settle this
before planning:

**A. Spec §8 Phase 3, as ratified**
1. `agent` tool parallel + chain modes; `{previous}` substitution.
2. Governance: concurrency semaphore (`MAX_CONCURRENCY=4`), `MAX_PARALLEL_TASKS=8`, `max_depth` via
   `AELIX_SUBAGENT_DEPTH`, over-limit errors rather than truncation.
3. The long-lived `RpcChannel` (interactive members) wrapping `RpcClient`.
4. The **cross-channel parity test** — `PrintChannel` ≡ `RpcChannel` for the same task. ADR-0198
   requires it before anything else depends on the rpc envelope.

**B. What the owner asked about during P2 (ADR-0197 deferrals)**
5. **The child → parent approval back-channel** — per-tool consent, not per-spawn. The owner raised
   this twice; it is the headline item. Measured blockers, all in ADR-0197 §(i) and the P2
   investigation: the child's stdin blocks to EOF so it cannot carry a reply; the RPC extension-UI
   request/response types exist but the server drops responses and nothing ever sends one; the parent
   TUI has a **single modal slot with no arbiter**, so a second prompt orphans the first. Estimated
   ~+500 LOC plus two product-core TUI subsystems, plus a rewrite of §(i)'s threat model. **It is a
   sprint of its own, not an add-on.**
6. A correctly-shaped consent memo — keyed `(profile, source_path, granted mode)` and re-prompting
   whenever cwd or the resolved posture differs from what was on screen.
7. A bash environment scrubber (a child holding `bash` can reset `AELIX_SUBAGENT_DEPTH`).
8. cgroup / `pidfd_open` subtree containment + a stray-pid sweeper (closes the two pdeathsig
   residuals: a SIGTERM-ignoring child, and a session-leader grandchild).
9. The wider child-trust rule that recovers `inherit_extensions: true` in a trusted directory.
10. A keyed multi-runtime `bind_subagents` registry (only exercisable once a second runtime exists —
    i.e. with P4 `aelix-team`).

**Recommendation:** do **4 + 1 + 2** as one landable phase (they share the runtime and the test bed),
then **5** as its own sprint. Items 7/8/9 are small and can ride along; 10 belongs with P4.

## 4. Landmines a fresh session must know

- **Worktree first.** `git worktree add -b feat/p3-… /workspaces/aelix-p3 main`. P1 was built in the
  main tree and an unrelated uncommitted change got swept into the commit, which had to be amended.
- **PYTHONPATH is mandatory in a worktree.** The venv lives in `/workspaces/aelix-ai/.venv` and its
  editable installs point at the **main tree**, so a bare `pytest` in a worktree silently tests the
  wrong source:
  `PYTHONPATH=<wt>/packages/aelix-coding-agent/src:<wt>/packages/aelix-agent-core/src:<wt>/packages/aelix-ai/src /workspaces/aelix-ai/.venv/bin/python -m pytest -q`
- **pyright needs `--pythonpath /workspaces/aelix-ai/.venv/bin/python`**, or it reports ~46 phantom
  unresolved-import errors (the repo's `[tool.pyright]` has no `venvPath`).
- **`/tmp` can be wiped between sessions.** Plans belong in `.omc/specs/` (tracked), not only in the
  scratchpad. Workflow journals under
  `~/.claude/projects/<proj>/subagents/workflows/<runId>/journal.jsonl` survive and hold every agent's
  full return value under the key `result` — that is how P2's lost plan was recovered.
- **Do not pass large data through a workflow prompt.** P2 lost its delta list to an `args`
  interpolation that produced the literal `undefined`, and lost 16 review findings to a
  `slice(0, 22000)` truncation. Write the data to a file and have the agent read it.
- **The kernel is never edited.** `git diff --stat -- packages/aelix-agent-core` must be empty, and
  `tests/agents/test_p2_band_boundaries.py` is the machine gate for the whole 3-band rule.
- **product-core must gain no spawn behaviour.** `tests/cli/test_p2_import_direction.py` pins the
  one-way import direction (`aelix_agents` may import product-core, never the reverse).

## 5. Invariants P3 must not regress

These are the security properties P2 exists to provide; any P3 change touching the child path must
re-prove them (each already has tests):

1. A child never runs with a posture looser than its parent's, and a **project-scoped profile can
   never widen** — `child_permission_mode` takes a rank-**min**, verified across all **80** cells
   (5 parent postures x 4 `approval_mode` values x 2 scopes x 2 `has_ui` values, pinned literally by
   `tests/agents_ext/test_posture_clamp.py:157` and `:173`; the earlier "30" was wrong).
2. A headless child **blocks** where it would otherwise need approval (`headless_default="block"`) —
   it never silently auto-approves.
3. `.aelix/**` is never auto-writable, and the auto-approve gate **resolves symlinks** — together
   these close write-to-execute self-escalation.
4. Consent is per-spawn, never persisted, ceiling `auto-accept-edits`, user-scope only; the dialog
   fires only when write authority is at stake (the clamp grants it, or the profile declares
   `approval_mode: auto`/`ask`).
5. The model-driven door resolves identities with `allow_project=False`; `/agents run` prompts for a
   project identity **before** spawning.
6. Delegation is bounded: 12 per user prompt, 4 live children, and leaves are spawned without the
   extension so they cannot nest.

## 6. How to start the new session

Open a fresh session and say, roughly:

> P3를 시작합니다. `.omc/specs/p3-kickoff-handoff.md`를 먼저 읽고, 거기 §3의 스코프 분기(스펙 Phase 3
> vs 승인 백채널)에 대한 내 결정을 받은 뒤, P1·P2와 같은 방식으로 진행해줘 — 워크트리 생성 → 리콘 →
> 계획 초안 → 적대적 검토 → 구현 → 검증 → 리뷰 → 병합.

The session will need the ultracode/workflow opt-in if you want the same multi-agent treatment P1 and
P2 got; without it, say so explicitly ("use a workflow").
