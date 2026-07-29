# RPC sprint kickoff — handoff for a fresh session

**Written:** 2026-07-29, at the close of P3. **Read this first, then the two recon notes beside it.**
Nothing here is a plan. The plan is built in the new session the way P1, P2 and P3 were:
recon → draft → adversarial review → owner decision → implement → verify → adversarial code review → merge.

---

## 1. Where the tree is

- `main` = `ab624fd`, identical to `origin/main`. Working tree carries only unrelated untracked
  leftovers (brand assets, `.omc` scratch, an old session HTML). **Do not sweep these into a commit.**
- Shipped: **P1** (`fe64f34`, ADR-0196) agent-profile identity. **P2** (`f6d8657` → merge `283e2fc`,
  ADR-0197 + ADR-0198) the subagent runtime seam, the bundled `aelix_agents` extension, single-mode
  delegation, the posture clamp and spawn-time consent. **P3** (`bb24295` → merge `ab624fd`,
  ADR-0199) parallel/chain delegation and governance.
- Full suite at `ab624fd`: **7102 passed / 1 skipped**; ruff clean; pyright clean apart from 10
  pre-existing errors under `scripts/`; kernel and product-core diffs byte-empty across all of P3.
- Worktree `/workspaces/aelix-p3` still exists on branch `feat/p3-parallel-chain-governance`
  (merged). Remove it or reuse it; do not build this sprint in it.

## 2. The documents that matter

| Document | What it is |
|---|---|
| `.omc/specs/rpc-sprint-recon-transport.md` | **Start here.** The RPC transport recon: what `RpcClient` / `rpc_mode` actually do, 8 contradictions between the spec and the code, 16 gaps, 8 open questions. |
| `.omc/specs/rpc-sprint-recon-envelope.md` | The envelope recon: ADR-0198's contract, what is pinned by a test vs by prose only, and the field-by-field classification the parity test needs. |
| `docs/decisions/0198-print-mode-json-envelope-contract.md` | The `json` half of the envelope contract. Its *Partial fulfilment* section names **this sprint's two deliverables** explicitly. |
| `docs/decisions/0199-parallel-chain-and-delegation-governance.md` | P3's decisions and residuals R-A…R-G. Its scope section records why this sprint was split out. |
| `docs/decisions/0197-subagent-runtime-seam-and-aelix-agents.md` | P2. The 3-band rule and the *Deferred deliberately* list. |
| `.omc/specs/p3-parallel-chain-governance-plan.md` | P3's plan. Its §5 work-breakdown (disjoint file ownership) is the template for splitting this sprint across agents. |

## 3. The scope, and the ONE fork the owner must settle first

**Scope: spec §8 Phase 3 items 3 and 4**, which P3 deliberately deferred:

3. A long-lived `RpcChannel` wrapping `RpcClient`, with `prompt_and_wait` / `stop` / `set_model`.
4. The **cross-channel parity test** — `PrintChannel` ≡ `RpcChannel` for the same task. ADR-0198
   requires it before anything else may depend on the rpc envelope.

**THE SPEC'S PREMISE FOR BOTH IS FALSE, AND THAT IS MEASURED.** Do not plan from spec §8 alone:

- Spec §8 claims `tests/rpc/*` "already drives real `--mode rpc` children and is the ready-made bed."
  It does not. All three files subclass `RpcClient` purely to replace `_build_argv` with an inline
  `python -c` echo stub. **The real-child bed does not exist and must be built.**
- `rpc_client.py:466` builds `-m aelix`, which `src/aelix/__main__.py:143-145` shows is an
  `AgentHarness` with a **mock `stream_fn`** — a demo, not the product. Already called a live bug in
  two places in-tree.
- `RpcClient.start()` has no `limit=`, no `start_new_session=`, no `pdeathsig`, no `kill_tree`, and
  **no seam to add them**. Every containment property P2 built is absent on this path.
- **The turn terminator is broken for an automated parent.** `agent_end` is the only completion
  signal, and it is NOT emitted on `abort` (the harness swallows the `CancelledError` and returns
  `[]`), on a failed prompt (`rpc_mode.py:310-320`, whose own comment promises a bridge "Sprint 6f
  wires" — it never did), or on a busy rejection (`_handle_prompt` returns `success: true` *before*
  `harness.prompt` raises). Each costs a 60 s dead wait.

The good news, also measured: **both channels serialize through the same function**, so field
spelling and discriminators are byte-identical and `stream.reduce_line` works unmodified on rpc
output. The parity test is genuinely feasible. The work is in the terminator, `exit_code`,
`dropped_lines` and response-envelope-interleave columns — not in the event layer.

### THE FORK: how much product-core may this sprint touch?

P3 shipped a **zero** product-core delta and that was the right call there. **This sprint cannot.**
The honest fixes are in `rpc_mode.py`, which is product-core. Settle this before planning:

| Candidate | What it is | Argument |
|---|---|---|
| `rpc_mode.py:310-320` | Emit a synthetic terminal event when a prompt task fails | Closes an ADR-0058 carry-forward the code itself advertises and never delivered. Not spawn behaviour, not a cap, not consent policy — so it does not violate the band rule as written. |
| `rpc_mode.py:333-340` | An `agent_end`-equivalent (or abort ack) after `harness.abort()` | The harness half is **KERNEL and forbidden**. A server-side synthetic event is the only in-band fix. |
| `rpc_mode.py:309` | `_handle_prompt` drops `cmd.images` while `_handle_steer` decodes them | One-line pre-existing bug. |
| `rpc_client.py:120-127` | A containment seam (`start_new_session`, `preexec_fn`, `limit`) on `RpcClientOptions` | The alternative is overriding `start()` wholesale in an extension subclass, duplicating ~45 lines of lifecycle. `rpc/rpc_client.py` **is** on the band gate's spawn allowlist, so the machine gate stays green either way — this is a question of intent, not of gates. |

Whatever is chosen, the depth/env threading and every spawn decision stay in `aelix_agents`.
Implementing spec lines 275/289 literally would put `AELIX_SUBAGENT_DEPTH` into
`rpc_client._build_env`, i.e. spawn policy in product-core — **do not**.

## 4. Landmines

- **Worktree first.** `git worktree add -b feat/rpc-… /workspaces/aelix-rpc main`.
- **PYTHONPATH is mandatory in a worktree.** The venv's editable installs point at the MAIN tree, so
  a bare `pytest` in a worktree silently tests the wrong source:
  `PYTHONPATH=<wt>/packages/aelix-coding-agent/src:<wt>/packages/aelix-agent-core/src:<wt>/packages/aelix-ai/src /workspaces/aelix-ai/.venv/bin/python -m pytest -q`
- **pyright needs `--pythonpath /workspaces/aelix-ai/.venv/bin/python`**, or it reports phantom
  unresolved imports. An IDE pyright pointed at the main tree will report P3's own modules as
  unknown — those are phantoms, not defects.
- **`/tmp` IS wiped between sessions — this is now proven, not theoretical.** P3's recon notes were
  lost exactly this way. The two notes beside this file were **recovered from the workflow journal**
  at `~/.claude/projects/<proj>/subagents/workflows/<runId>/journal.jsonl`, which holds every
  agent's full return value under `result`. **Write anything durable to `.omc/specs/` immediately,
  not at the end.**
- **Do not pass large data through a workflow prompt.** Write it to a file and have the agent read it.
- **The kernel is never edited.** `tests/agents/test_p2_band_boundaries.py` is the machine gate, and
  it is a TEXT scan — a kernel comment mentioning subagents trips it.
- **A naive `RpcChannel` spawns children that think they are the root.** `RpcClient._build_env` is
  `dict(os.environ)` plus overrides and nothing else: no `AELIX_SUBAGENT_DEPTH`, no `--no-agents`, no
  `AELIX_MCP_CONFIG` pop. A grandchild would read depth 0 and load `aelix_agents` itself — the fork
  bomb the depth guard exists to prevent.
- **`exit_code` is meaningless on rpc** (the child never exits on its own), and it is
  *nondeterministic*: 0 if SIGTERM lands inside the 1 s window, `-9` otherwise. `build_result`'s
  exit-code failure clause would turn a perfect delegation into `ok=False` as a function of shutdown
  timing. Decide the policy before writing the parity test.
- **`dropped_lines` is structurally always 0 on rpc** — the rpc read path has no per-line budget.
  Either give it one or document the asymmetry, and make the parity test assert the choice.
- **The two channels cannot be compared byte-for-byte**: print uses `json.dumps` default
  `ensure_ascii=True`, the rpc JSONL writer uses `ensure_ascii=False`. Value-level parity only.

## 5. Invariants this sprint must not regress

P2's six (see `p3-kickoff-handoff.md` §5, whose "30 cells" was corrected to **80**), plus what P3
added — all of these have tests, and all of them are load-bearing:

1. **Every field interpolated into a consent dialog goes through the sanitiser.** P3's review
   demonstrated end-to-end that a model-supplied `cwd` containing newlines and ESC could forge the
   whole dialog: the human reads `Permission: plan` over two benign tasks, approves, and the issued
   grant is `auto-accept-edits` over two never-rendered ones. A new dialog field that skips the
   sanitiser re-opens it.
2. **The consent modal bottom-truncates; it does not scroll — and `Cancel` is appended last.** The
   bounded quantity is the composed modal height, verified by a property test over
   mode × N × hostile field shapes. ADR-0197 residual **R3** (options pinned visible) is still OPEN
   and is product-core, so it may be in scope for this sprint if the fork above is settled generously.
3. **A batch member's posture floor needs two signals.** Gating on the derived child clamp alone is
   provably inert for a widened batch; the parent's own posture is the signal that does not saturate.
4. **The per-prompt delegation budget is charged per CHILD**, and the band gate now refuses a
   cap-like constant landing in product-core.
5. **Chain step k≥2 is not consented text** (ADR-0199 residual R-A). If this sprint touches the chain
   path, it must not weaken the fencing, the dialog row, or the chain widening ban.

## 6. Why this sprint comes before the approval back-channel

ADR-0197 §(i) lists four *measured* blockers for the child→parent approval back-channel. **Two of
them are in the rpc layer and dissolve here:**

- Blocker (a) — "the child's stdin reads to EOF, so it cannot carry a reply" — is **print-mode only**.
  `_read_piped_stdin` is gated to `app_mode in ("print", "json")` (`cli/entry.py:1346`), and
  `run_rpc_mode` attaches its own stdin reader via `asyncio.connect_read_pipe`. An rpc child has a
  **live bidirectional JSONL stdin**.
- Blocker (b) — the RPC extension-UI multiplex is types-only and the server drops responses — is
  squarely in `rpc_mode.py`, i.e. in this sprint's blast radius.

Blocker (c), the parent TUI's missing modal arbiter, is independent — and it touches the same code as
residual R3 above, so those two belong together in a later sprint.

## 7. How to start the new session

Open a fresh session and say, roughly:

> RPC 스프린트를 시작합니다. `.omc/specs/rpc-sprint-kickoff-handoff.md`를 먼저 읽고, 거기 §3의
> 스코프 분기(product-core를 어디까지 건드릴지)에 대한 내 결정을 받은 뒤, P1·P2·P3과 같은 방식으로
> 진행해줘 — 워크트리 생성 → 리콘 → 계획 초안 → 적대적 검토 → 구현 → 검증 → 코드 리뷰 → 병합.

The session needs the ultracode/workflow opt-in for the same multi-agent treatment; without it, say
so explicitly ("use a workflow").

**One process note worth repeating.** P3's adversarial code review returned 18 findings, every one
demonstrated by execution, plus ~16 source mutations that no test caught — including the CRITICAL
consent forgery and a governance function that was provably inert. What produced that was requiring
reviewers to **mutate the source and prove a test goes red**, not merely to read it. Do that again.
