> **IMPLEMENTER ORIENTATION — read this before anything else.**
> 1. **What P2 is.** One parent aelix process spawns ONE child aelix process (`-m aelix_coding_agent --mode json -p --no-session`), streams its JSON events back, and returns a `SubagentResult`. Single level, single mode, no daemon, no cloud.
> 2. **The 3-band rule (ADR-0008, amended by ADR-0197).** Band 1 `aelix-agent-core` (kernel) gets **zero bytes**. Band 2 `aelix_coding_agent` (product-core) gets **interface only** — types, a Protocol, a binding slot, flags, settings; it may CALL a bound Protocol but must never spawn, never build child argv, never parse a child stream, never author consent policy. Band 3 `aelix_agents` (bundled extension) owns every line of behaviour.
> 3. **Run everything as:** `PYTHONPATH=/workspaces/aelix-p2/packages/aelix-coding-agent/src:/workspaces/aelix-p2/packages/aelix-agent-core/src:/workspaces/aelix-p2/packages/aelix-ai/src /workspaces/aelix-ai/.venv/bin/python -m pytest tests/ -q`. Worktree is `/workspaces/aelix-p2`, branch `feat/p2-subagent-runtime`. The venv lives in the OTHER tree; never `pip install -e` here.
> 4. **Non-negotiable #1 — the kernel is byte-unchanged.** `tests/agents/test_p2_band_boundaries.py` is the machine gate.
> 5. **Non-negotiable #2 — a child never gains a mode looser than the parent's WITHOUT a per-spawn human grant**, and that grant tops out at `auto-accept-edits`, never for a project-scoped profile, never persisted.
> 6. **Non-negotiable #3 — spawn-time consent exists and fails closed.** No grant ⇒ no process. `ask` never refuses a spawn; headless silently downgrades to `plan`.
> 7. **Non-negotiable #4 — `--no-approve` is CONDITIONAL** (`aelix_agents/trust.py::child_trust_argv`, two clauses), and `[features] agents` defaults to **False**.
> 8. Everything below is settled. Where a section says *SHIPPED*, the code is already in the worktree — read it, do not re-derive it.

# P2 — Subagent runtime seam + bundled `aelix-agents` extension + single-mode delegation (FINAL / Option-C merged)

**Worktree:** `/workspaces/aelix-p2`, branch `feat/p2-subagent-runtime`
**Scope:** spec §8 Phase 2 only.
**Child-authority model:** **Option C** — read-only default **plus** spawn-time consent with bounded widening. Owner-ratified; do not re-litigate.

**Run everything as:**
```
PYTHONPATH=/workspaces/aelix-p2/packages/aelix-coding-agent/src:/workspaces/aelix-p2/packages/aelix-agent-core/src:/workspaces/aelix-p2/packages/aelix-ai/src \
  /workspaces/aelix-ai/.venv/bin/python
```

---

## −1. SHIPPED STATUS — what already landed in this worktree

Waves 1 and 2-D1 landed before this document was rebuilt. **The plan below describes the code that exists.** Where the implementation chose a different-but-equivalent shape, the plan has been corrected — not the code.

| Work stream | State | Files |
|---|---|---|
| **WS-A** product-core contract | **SHIPPED** | `subagent_contract.py` (new) · `extensions/api.py` (4 hunks) · `tests/agents/test_subagent_contract.py` (18 tests) · `tests/agents/test_p2_band_boundaries.py` (4 tests) |
| **WS-B** settings flag | **SHIPPED** | `settings/{types,settings_manager,__init__}.py` · `tui/settings_rows.py` · `tests/settings_manager/test_features_agents_flag.py` (11 tests) |
| **WS-C** permission surface | **SHIPPED** | `cli/args.py` · `builtin/permission.py` (**5** hunks as landed) · `tests/builtin/test_permission_child_floor.py` (10) · `tests/builtin/test_sensitive_aelix_dir.py` (6) · `tests/cli/test_permission_mode_flag.py` (13) |
| **WS-D1** pure + near-pure extension modules | **SHIPPED** | `aelix_agents/{stream,envelope,posture,prompt_file,consent,trust}.py` · `tests/agents_ext/{test_stream_reduce,test_envelope,test_posture_clamp,test_spawn_consent,test_child_trust_argv}.py` |
| **WS-F** ADRs + infra | **SHIPPED** | `docs/decisions/0197-*.md` (913 lines) · `0198-*.md` (249) · amendments to `0008`/`0157`/`0165`/`0196` + `README.md` · `CHANGELOG.md` (`.aelix` behaviour change) · `.github/workflows/ci.yml` (`fetch-depth: 0`) |
| **WS-D2** process layer | **REMAINING** | `aelix_agents/{print_channel,reaper,runtime}.py` + 3 test files |
| **WS-D3** consent wiring + tool layer | **REMAINING** | `aelix_agents/{consent_gate wiring in extension.py,tool,progress,extension,__init__}.py` + 3 test files |
| **WS-D packaging** | **REMAINING — DO NOT FORGET** | `packages/aelix-coding-agent/pyproject.toml:101` still reads `packages = ["src/aelix_coding_agent"]`. **`src/aelix_agents` is NOT in the wheel yet.** |
| **WS-E** product-core wiring | **REMAINING** | `cli/entry.py` (5 hunks) · `tui/commands.py` (2 hunks) + 3 test files |

Current gate: `pytest tests/agents tests/agents_ext tests/builtin tests/settings_manager -q` → **844 passed**.

### Corrections applied to this plan against the shipped code

1. **`child_permission_mode` gained a keyword-only `has_ui: bool = False`** and the module exports `posture_rank()`. The plan's signature is updated (§2e).
2. **The project-scope widening ban is a rank-MIN**, not the plan draft's assignment (§2e; delta 1). Shipped `posture.py:121-124`.
3. **`cli/args.py` warns via `parsed.diagnostics`, not `print(..., file=sys.stderr)`** — matching `--thinking` (`args.py:466-489`), and adds a derived `VALID_PERMISSION_MODES` tuple. §5.2 updated.
4. **`builtin/permission.py` landed 5 hunks, not 3** (the `_MUTATING` DO-NOT-ADD-`agent` comment and `_HEADLESS_BLOCK_REASON` are separate hunks). §3/§5.4 updated.
5. **The consent dialog ships 2–3 options, not delta 6's four rungs.** Rung (2) *"don't ask again for this profile this session"* is **NOT shipped** — `tests/agents_ext/test_spawn_consent.py::test_grant_is_not_persisted_between_spawns` pins *"P2 ASKS EVERY TIME"*, and ADR-0197's *Deferred* list records the memo as P3 keyed on `(profile, source_path, mode)`. Rung (3) *"delegate read-only (plan) instead"* is **merged into option 0** whenever the clamp is already `PLAN` (the DEFAULT-parent case, i.e. the common one); under a loose parent option 0 states the real inherited posture instead. §2(i) documents the shipped shape and names the divergence. **Do not add the memo in P2.**
6. **`SubagentOutcome` gained `"declined"` and `SubagentResult` gained `permission_mode: str | None`** (additive, no version bump). §4 updated.
7. **The `/settings` row is `live=False` by design** (persist-only; the flag is consumed once at harness build). It is fully dispatchable — both `_BOOL_GETTERS` and `_BOOL_SETTERS` carry it — so it is *not* an inert #84 row. §5.6 keeps `live=False`.
8. **§0's B1 anchors were wrong** and are corrected per delta 18.

---

## 0. Disposition table — every BLOCKING finding

Each row is either **FIXED** (with the section that fixes it) or **REJECTED** (with evidence re-verified). Line numbers are from `/workspaces/aelix-p2` and were re-read during synthesis; where the draft's anchor drifted, the corrected anchor is given.

| # | Lens | Finding | Disposition | Verification |
|---|---|---|---|---|
| **B1** | process | Reaper is cancellation-unsafe; a 2nd Ctrl+C/Esc during the 5 s grace kills the SIGKILL escalation and orphans a session-leader child | **FIXED** — §9.5 rewritten: detached shielded reaper task + eager SIGKILL on cancel + `except BaseException` | CONFIRMED, **with corrected anchors (OC-9)**. The running-turn handler is `tui/chrome.py::_interrupt` at **`:854`**, whose running branch is **`:863-866`** (`if self._running: … on_interrupt(); return`). There is **no** debounce on that branch: `_CTRL_C_EXIT_WINDOW` is the constant at **`:159`** and is read only at `:875`, inside the idle-empty-buffer branch at **`:872-882`**. Esc is bound to the same `on_interrupt` at **`:883-890`** under `Condition(lambda: self._running)`. N presses = N `cancel()` calls. *(The draft cited `chrome.py:155-158` / `:160` / `:168-176`; those anchors are stale. The finding itself is unchanged.)* |
| **B2** | process | `stderr=PIPE` never drained concurrently → two-pipe deadlock | **FIXED** — §7.1 respecified: both pipes pumped concurrently, stderr into a bounded 64 KiB ring | CONFIRMED, and worse than reported. A probe (child writes 1 MiB to stderr between two stdout lines) hung for the full 2-minute tool timeout — even `proc.kill(); await proc.wait()` after the `wait_for` timeout did not unwedge it. The draft's "drain stdout to EOF, then read stderr" schedule is unshippable. |
| **B3** | process | `readline()`'s 64 KiB ceiling raises unrecoverably on the 200 KB `message_end` a routine file-read produces; the oversize-line test is pinned in the pure suite where it cannot fail | **FIXED** — §7.2 rewritten (explicit `limit=`, chunked reads, pure `LineAssembler`), test moved to the subprocess suite | CONFIRMED by execution: `asyncio.create_subprocess_exec`'s StreamReader `_limit == 65536`; a 200 000-byte line gives `ValueError: Separator is not found, and chunk exceed the limit`, and the **next** `readline()` gives `ValueError: Separator is found, but chunk is longer than limit` — the stream is unrecoverable, so "increment `dropped_lines` and discard" is unimplementable on `readline()`. `limit=` IS a real kwarg. |
| **B4** | security | `approval_mode: auto` is unclamped — a project-authored profile lifts a DEFAULT parent to auto-accepted repo-wide writes | **FIXED** — §2(e) replaced with a total-order clamp on **every** row + a project-scope widening ban (now a rank-min, OC-6); **preserved verbatim under Option C** by §2(i) constraint 1 | CONFIRMED. `builtin/permission.py`'s `mode == AUTO_ACCEPT and not is_bash and _is_auto_allowable_write(...) → return None` branch sits **~30 lines above** the `if not ctx.has_ui:` branch, so the draft's headless floor never runs for those writes. `_APPROVAL_MODES` in `agents/profile.py` is scope-blind; the only project-scope prohibition is `extensions:` (`agents/profile.py:346`). |
| **B5** | security | Delegation resolves project-scoped identities with **no** per-identity confirmation — P1 fail-closes the other two doors | **FIXED** — §2(f): the Protocol carries `allow_project`; the model-driven path is fail-closed | CONFIRMED. `agents/service.py:231-247` raises `ProfileError` when `profile.scope == "project"` and `confirm_project` is absent, and its docstring (`service.py:96-105`) states the rule verbatim. `cli/entry.py:1489-1503` hard-`return 1`s non-interactively. |
| **B6** | security | §5.5's "the child treats its cwd as UNTRUSTED" is **false**, and was scheduled to be recorded in the ADR as fact | **FIXED** — claim deleted; replaced with the measured 7-step ladder and the **two-clause** `child_trust_argv` rule (§2g, OC-4) | CONFIRMED. `cli/project_trust.py` step 1 `if override is not None: return override` (`:521-523`); step 2 `if not has_trust_requiring_project_resources(cwd): return True` (`:525-527`); step 4 is the persisted `trust.json` nearest-ancestor decision (`:550-557`). The deny-with-no-UI the draft cited is **step 6** (`:565-567`). `:60-61` documents "trusting a parent transitively trusts children". |
| **B7** | contract | `bind_subagents` is a single overwritable slot; teardown unbinds a foreign runtime | **FIXED (partial)** — refuse-second-bind-unless-`replace` + identity-scoped `unbind_subagents`. **Keyed multi-runtime registry REJECTED → P3/P4** | Slot shape confirmed against the `bind_ui` precedent (`extensions/api.py:527-535`) — singular because there is only ever one UI, which is the wrong precedent here. A keyed registry is only exercisable once a second runtime exists (P4 `aelix-team`); shipping it now freezes an unvalidated key namespace into `CONTRACT_VERSION 1`. |
| **B8** | contract | No channel for untruncated child output, yet `details` is referenced by the truncation marker and a test — the field does not exist | **FIXED** — `details: str | None = None` on the contract | CONFIRMED against the draft's own field list. `ToolResult.details` exists but is built in `tool.py`, unreachable from the pure envelope and invisible to `/agents run`, a P4 dashboard, or a Web UI — all of which consume `SubagentResult`. |
| **B9** | contract | `CONTRACT_VERSION` exact-equality refusal on a version P3 is guaranteed to bump | **FIXED** — `MIN_SUPPORTED_CONTRACT_VERSION` range + additive-fields-do-not-bump rule; `ExtensionError` `code` Literal widened (a **4th** api.py hunk the draft omitted) | CONFIRMED. `extensions/api.py:127` was `code: Literal["stale", "unbound", "invalid_state"]`; `"contract_mismatch"` was not in it. |
| **B10** | contract | Every security assertion is argv-shaped, and `args.py` swallows unknown `--` flags with no diagnostic — a renamed/typo'd `--permission-mode` yields a green suite and an auto-approving child | **FIXED** — `test_child_argv_parses_clean` + `test_a_typo_would_be_swallowed_silently` + a real-child realized-posture test; WS-C promoted to a Wave-2 blocker | CONFIRMED. `cli/args.py` records any unrecognized `--key value` into `parsed.unknown_flags` and emits **nothing**; contrast the `Unknown short flag: {arg}` error for `-x`. |

### IMPORTANT findings — disposition

| # | Finding | Disposition | Verification |
|---|---|---|---|
| I1 | Parent hard-death leaves a fully-running orphan; the child's EPIPE guard does not cover the initial prompt | **FIXED (PDEATHSIG only).** Per-session stray-pid file + sweeper **REJECTED → P3** | CONFIRMED: `modes/print_mode.py:158-165` `_emit` only sets `stdout_dead["v"]`; the `break` is at `:198-205` inside the **residual**-messages loop, and the `raise BrokenPipeError` at `:208-211` — both strictly after `await runtime_host.harness.prompt(initial_message)` at `:189-193`. `agents/resolver.py:204-205` appends exactly one positional, so the whole task IS the initial prompt. |
| I2 | §9.5's `os.killpg` rationale is factually wrong — bash grandchildren are in their own groups | **FIXED** — rationale corrected + `/proc` descendant walk on the SIGKILL leg. cgroup/pidfd containment **REJECTED → P3** | CONFIRMED: `tools/bash.py:163` `subprocess.Popen(..., start_new_session=True)` and `tools/_subprocess.py:78` `create_subprocess_exec(..., start_new_session=True)`. |
| I3 | `role` is arithmetically inert at `MAX_SUBAGENT_DEPTH = 1`; its test passes vacuously | **FIXED** — stated inert in ADR-0197 + discriminating test with `MAX` monkeypatched to 2 | CONFIRMED, and sharper: `agents/profile.py:208` — `role: Literal["leaf","orchestrator"] = "leaf"`, so **every** profile is a leaf by default, and both branches yield 1 at MAX=1. |
| I4 | `bind_subagents` has no depth check — the fork-bomb guard is bound to one extension instance, not the seam | **FIXED** — one `if` in `api.py` (SHIPPED) | Vector confirmed narrower than reported but real: `extensions/loader.py:470` does kill tier 4 under `--no-extensions`, and `agents/profile.py:346` blocks `extensions:` at project scope — but **user-scope** tier-1 extensions still load in a child with `inherit_extensions: true`. |
| I5 | `ExtensionError("contract_mismatch")` violates the declared Literal | **FIXED** — folded into B9 as api.py hunk 2 | see B9. |
| I6 | `test_kernel_untouched` errors on CI's shallow checkout and goes vacuous after merge | **FIXED (SHIPPED)** — content gate primary; git-range gate kept as a skip-if-unavailable adjunct + `fetch-depth: 0` | CONFIRMED: `.github/workflows/ci.yml:41` used `actions/checkout@08c6903…` with no `fetch-depth`; now `fetch-depth: 0`. The content gate is meaningful today: grep of `packages/aelix-agent-core/src/` for `subagent|SubagentRuntime|bind_subagents|AELIX_SUBAGENT` → **0**. |
| I7 | No test feeds a real print-mode emitter's bytes into `reduce_line` | **FIXED** — `test_reduce_consumes_real_print_mode_output.py` (WS-D3, remaining) | Emitter path confirmed: `modes/print_mode.py:158-165` `json.dumps(_event_to_dict(event))`. |
| I8 | `AssistantMessage.usage` is a free-form dict, so ADR-0198's "kernel dataclasses are the wire format" does not cover it | **FIXED (SHIPPED)** — dual-read helper in `stream.py` + scoped ADR-0198 §D2 | CONFIRMED: `aelix_ai/messages.py:126` `usage: dict[str, Any] | None = None`; the kernel itself dual-reads at `session/compaction.py:1036-1043`. |
| I9 | WS-A's Wave-1 gate asserts on WS-E's Wave-3 file; a "pure" test asserts non-pure code | **FIXED** — §10 ownership re-cut | Structural; accepted as stated. |
| I10 | `tests/conftest.py`'s autouse download guard does not reach child processes | **FIXED** — explicit child env in every real-child test | CONFIRMED: `tests/conftest.py:26-40` is `monkeypatch.setattr(_tm, ...)` — in-process only. |

### OPTION-C decisions — owner-settled, do not re-litigate

| # | Decision | Where it lands | Evidence / why |
|---|---|---|---|
| **OC-1** | **Child authority = Option C**: read-only default **plus** spawn-time consent with bounded widening. The narrower "read-only, full stop" recommendation was **incomplete**: of the 30-cell (5 parents × 3 `approval_mode` × 2 scopes) matrix, **10 cells are write-capable and 8 of those ask nobody**. A user who pressed shift+tab once for their OWN convenience thereby hands every future model-chosen delegation an unattended `auto-accept-edits` child. | §2(i), §11 ADR-0197 §(i) | Executed matrix, §1.4 of the investigation. shift+tab was consent to the PARENT's tool calls, with a tool card on screen — not consent to an unattended child process the model just chose. |
| **OC-2** | **The child→parent approval back-channel is P3**, blocked by four measured facts, not by plumbing. | §11 ADR-0197 *Deferred* | (a) stdin can't carry it — `entry.py:1266` → `_read_piped_stdin` → `sys.stdin.read()` reads to **EOF**: an open pipe hangs the child forever, a closed one **prepends the reply to the task prompt**; (b) the RPC UI multiplex is **types only** (`rpc/rpc_types.py:639-642`), the server drops responses (`rpc_mode.py:2045-2049`), RPC children never call `bind_ui`; (c) the parent TUI has **no modal arbiter** — `chrome.py:518`'s `_modal` is a single slot, `mount_modal` (`:1511`) overwrites unconditionally, `is_modal_open` (`:1520`) is a visibility filter only; (d) it would invalidate §2(e)'s entire derivation. ≈ +500 LOC, two new product-core subsystems — its own sprint. |
| **OC-3** | **Bounded widening — constraint 0 plus five.** (0) *(owner amendment, 2026-07-27)* the profile **declared** it needs write authority (`approval_mode: auto`/`ask`); (1) project scope **never** widens; (2) live `ctx.has_ui` only; (3) ceiling is **exactly `auto-accept-edits`** (`auto`/`yolo` unreachable from any dialog, so bash stays gated); (4) never a no-op; (5) the `.aelix` hole (OC-5) is a **hard prerequisite**. | §2(i), `consent.py::_may_widen` | B4's vulnerability was *a repo file widening silently*. A human answering a modal is not that. Read-only-only was **rejected** because it pushes users to raise the WHOLE SESSION to `auto-accept-edits`, which is strictly wider than granting one child one run. |
| **OC-4** | **`--no-approve` is CONDITIONAL** — the two-clause `child_trust_argv` rule, not unconditional. | §2(g), `aelix_agents/trust.py` | Always-on buys exactly ONE new escalation (the monorepo/vendored `.aelix` ancestor-walk case) and costs a silent regression of the shipped `inherit_skills: true` default, because `has_trust_requiring_project_resources` deliberately omits `skills/`. Wherever the gate is live, the two-clause rule is **security-identical**. |
| **OC-5** | **`".aelix"` joins `_SENSITIVE_DIR_COMPONENTS`.** | §5.4 hunk 5, `permission.py:253-255` | Measured before the fix: `_is_auto_allowable_write('.aelix/agents/evil.md', cwd) → True`, `'.aelix/extensions/evil.py' → True`, `'.aelix/mcp.json' → True`, `'.aelix/settings.json' → True`; `guardrail.py:165-208` has no `.aelix` rule. An auto-accepting child could author the project identity / project extension a LATER run then EXECUTES under an ancestor `trust.json: true`. `--no-approve` cannot touch this — it stops LOADING such a file, never WRITING one. **User-visible behaviour change** ⇒ CHANGELOG line (shipped) + ADR paragraph. |
| **OC-6** | **The project-scope widening ban is a rank-MIN, not an assignment.** | §2(e), `posture.py:121-124` | Executed proof of the bug: the draft's `requested = fallback` gave `child_permission_mode("auto", YOLO, "project") → yolo` while the user-scope equivalent returned `auto-accept-edits` — the ban made the **checked-in repo profile strictly WIDER** than the user's own. Fixed form: `fallback = child_permission_mode("inherit", parent, "user"); if _RANK[fallback] < _RANK[requested]: requested = fallback`. |
| **OC-7** | **`ctx.has_ui` is `runtime.ui is not HEADLESS_UI_CONTEXT`, and it is TIME-VARYING.** | §2(e), §2(i), §9.6 | The draft said `app_mode == "interactive"` (`cli/entry.py:1095`) — a **different local**, consumed only by `resolve_project_trusted` and never passed to the extension runtime. The real definition is `extensions/api.py:977-978` (property at `:957`; `:1062`/`:1082-1083` in the post-P2 file), bound only from `tui/shell.py:1892`, re-pointed at `:1565`, reverted at `:1950`. Consequence: it is **False during `harness.bootstrap()` even in interactive mode**. **Read it LIVE at prompt time; never cache it.** |
| **OC-8** | **`approval_mode: ask` no longer refuses a spawn.** `ask` = always prompt in the parent, session rung suppressed. Headless = **silently downgrade to PLAN**, never refuse. | §2(e), §2(i) | Retires one documented P3 deferral — `agents/profile.py:218-220` `approval_mode` was "Validated, not read". `child_permission_mode` gains `*, has_ui: bool = False`: `ask` + no UI → `PLAN`; `ask` + UI → the `inherit` baseline the dialog offers to raise. |
| **OC-9** | **§0 B1's anchors were stale.** `_interrupt` is `chrome.py:854`, running branch `:863-866`, idle-empty branch `:872-882`, Esc `:883-890`, `_CTRL_C_EXIT_WINDOW` the constant at `:159`. | §0 B1, §9.5 | The finding (no debounce on the running branch) is **confirmed**; only the citation moved. |
| **OC-10** | **`[features] agents` default = `False` in P2**, global-scope-only getter. | §2(b), §5.6 | Spec §8: default-off through Phase 3, flipped at Phase 4. A merged read would let any cloned repo switch delegation ON via `.aelix/settings.json` — the `get_default_project_trust` self-elevation defeat. |

### Anchor corrections found during synthesis (the draft was wrong; use these)

* `packages/aelix-coding-agent/pyproject.toml` — the wheel table is at **line 100-101**, not 114. The file is 101 lines long. **Still unedited: `packages = ["src/aelix_coding_agent"]`.**
* `extensions/api.py` gets **4** hunks, not 3.
* `builtin/permission.py` gets **5** hunks as landed (draft said 2; delta 10 said 3 — the `_MUTATING` comment and `_HEADLESS_BLOCK_REASON` are separate hunks in the shipped diff).
* `tui/settings_rows.py`: `_BOOL_GETTERS` at **334**, `_BOOL_SETTERS` at **345**, `build_settings_rows` at **93** (pre-P2 numbering).
* `settings/types.py`: `WarningSettings` at **133**, `Settings` at **193**, `SETTINGS_PY_TO_JSON` at **289**, `NESTED_PY_TO_JSON` at **341**, `SETTINGS_NESTED_CLASSES` at **408**, `__all__` at **420**.
* `cli/args.py`: `project_trust_override` field at **155**, `--approve` at **441**, `--no-approve` at **442-444**, help block at **583**.
* `builtin/permission.py` (pre-P2): `_MUTATING` at **64**, `PermissionExtension` opens at **256**, `approval_runner` ends the field block at **279**, the AUTO_ACCEPT write branch is **348-353**, the headless branch is **379-383**, `_SENSITIVE_DIR_COMPONENTS` at **219**.
* `tui/commands.py`: `_AGENTS_USAGE` at **479**, `_agents_handler` at **640**, the unknown-subcommand line at **721**, registry row at **1491**. All as drafted.
* **`tui/chrome.py` (OC-9):** `_interrupt` **854**, running branch **863-866**, idle-empty branch **872-882**, Esc binding **883-890**, `_CTRL_C_EXIT_WINDOW` **159**.

### Scope rejections (P3/P4 — named so nobody re-litigates them mid-implementation)

Everything in the draft's rejection list stands, **plus**:

* the stray-pid file + cross-session sweeper (P3, with `RpcChannel`'s lifecycle work);
* cgroup / `pidfd_open` subtree containment (P3);
* the keyed multi-runtime `bind_subagents` registry (P3/P4, when `aelix-team` is the second implementer);
* flipping `permission.py:382` allow→deny for **all** headless runs (its own ADR, post-P2);
* the cross-channel parity test (P3, per spec §9);
* **the child→parent approval back-channel (P3)** — with its own threat model for the child-authored request string (OC-2);
* **persisted per-profile consent, and any `<agent_dir>/subagent-grants.json` store (P3).** `ProjectTrustStore`'s nearest-ancestor inheritance is the **wrong shape** for identity consent — that is exactly why `_confirm_project_agent` exists as a second gate. A per-session memo, when it lands, must key on `(profile, source_path, granted mode)` and **never** on a `tool:agent` rule key;
* **an `[agents] autoApprove` settings allowlist — NEVER.** If a settings knob ever ships it must be a **global-scope-only CEILING** applied as one more `min()`, copying `settings_manager.py:1008-1019`'s shape;
* **any path setting `allow_project=True` on the model-driven `agent` tool — NEVER**;
* the wider child-trust rule (recovering `inherit_extensions: true` + project MCP) and honouring `defaultProjectTrust: "always"` in a child (P3, §2g).

**One deliberate pull-forward, not creep:** spec §8 lists `max_depth` via `AELIX_SUBAGENT_DEPTH` under **Phase 3**. P2 ships it anyway because P2 without it is a fork bomb. It lands as a *guard*, not a feature: no `max_depth` profile field, no configurability, a hardcoded `MAX_SUBAGENT_DEPTH = 1`. ADR-0197 says so explicitly.

---

## 1. Non-negotiables (review gates)

1. **Kernel untouched.** Zero bytes under `packages/aelix-agent-core/`.
2. **Product-core gets INTERFACE ONLY.** `aelix_coding_agent/**` may declare types, a Protocol, a binding slot, a settings flag, CLI flags, and *call* a bound Protocol. It may never `create_subprocess_exec`, never build child argv, never parse a child event stream, **and never author consent policy** — it may surface a refusal, never originate one (`test_product_core_never_prompts_for_spawn_consent`).
3. **The spawn implementation lives in `aelix_agents/**`.**
4. **Single mode only.** `spawn(mode=...)` accepts `"single"`; anything else raises `ValueError("mode %r is P3")`.
5. **No cloud, no daemon, no background thread or task that outlives session teardown** (the reaper task is registered in the child registry and joined by `stop_all`).
6. **Security floor, mandatory:** Guardrail + Permission always prepended (`cli/entry.py:860-873` documents Guardrail-first as a security invariant — DO NOT REORDER); the child's tool set is the intersection with the parent's live grant; the child's permission mode is clamped against the parent's posture; a project-scoped identity is never reachable from a model-chosen `agent` call.
7. **Spawn-time consent is a gate, and it fails closed.** Every write-capable delegation takes one human decision in the PARENT before any process exists. A missing grant ⇒ **no spawn**. `ask` never refuses; headless silently downgrades to `plan`.

---

## 2. Resolved decisions

### (a) Where the bundled extension lives — UNCHANGED

New top-level import package `packages/aelix-coding-agent/src/aelix_agents/`, shipped inside the existing `aelix-coding-agent` wheel, loaded as a **conditional `prepend`** entry appended **after** `permission`.

Packaging edit is `packages/aelix-coding-agent/pyproject.toml` **line 100-101** (**NOT YET APPLIED**):
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/aelix_coding_agent", "src/aelix_agents"]
```
A separate dist trips `tests/test_license_sync.py:19`, `tests/test_release_version_consistency.py:26-31`, `scripts/generate_sbom.py:35`, `RELEASING.md`, a new PyPI pending publisher, and `install.sh:165`; and `entry_points(group="aelix.extensions")` is empty in this worktree because dist-info lives in the MAIN tree's venv.

**Import-direction rule (test-enforced):** `aelix_agents.*` may import `aelix_coding_agent.*`. `aelix_coding_agent.*` may name `aelix_agents` at **exactly one** site — a function-level import inside `cli/entry.py::_async_main`.

### (b) The settings gate — `[features] agents`, default `False` (OC-10)

Default `False` in P2 (spec §8: default-off through Phase 3, flipped at Phase 4). Read via a **global-scope-only** getter copying `get_default_project_trust`'s shape (`settings_manager.py:1008-1019` — its docstring states the self-elevation reason verbatim). Run-scoped `--agents` / `--no-agents`; precedence **`--no-agents` > `--agents` > `get_features_agents()`**.

SHIPPED: `settings_manager.py:1034` `get_features_agents` (returns `False` at `:1050` when unset), `:1053` `set_features_agents`.

### (c) Who serves `/agents run` — UNCHANGED

The **product-core built-in** `_agents_handler` gains a `run` branch. `tui/shell.py:2452-2459` runs `match_command` (built-ins) then `command.handler`, and only falls through to `dispatch.try_execute` at `:2465-2470`; `extensions/command_dispatch.py:76-85` `_split_command` splits on the first space, so an extension command name can never contain one. **Spec §6.3's `register_command("run", …)` under `/agents` is not implementable as written.**

### (d) The depth guard — UNCHANGED in shape, one layer added

1. **Build-time suppression (primary).** `cli/entry.py::_build_harness_options` does not prepend `AgentsExtension` when `subagent_depth() >= MAX_SUBAGENT_DEPTH` (**1**).
2. **Seam-level refusal (closes I4 — SHIPPED).** `bind_subagents` itself refuses at depth. One `if`; turns "we did not load our extension" into "product-core will not hold a runtime at depth", which also covers a third-party or user-scope-tier-1 runtime inside a child.
3. **Tool-time re-check.** The `agent` tool returns `ToolResult(is_error=True)` at depth. Returns, never raises.
4. **`role` wiring — shipped but INERT in P2.** `role: leaf` → child depth = `MAX_SUBAGENT_DEPTH`; `orchestrator` → `subagent_depth() + 1`. At MAX=1 both are 1, and `agents/profile.py:208` defaults `role` to `"leaf"`. ADR-0197 **states plainly that `role` is arithmetically inert in P2**. The test is made discriminating by monkeypatching `MAX_SUBAGENT_DEPTH = 2`.

**Accepted residual risk (named in the ADR):** a child holding `bash` in its intersected grant can run `AELIX_SUBAGENT_DEPTH=0 python -m aelix_coding_agent …`. A bash-env scrubber is out of P2 scope.

### (e) The child permission FLOOR — the clamp (closes B4; corrected by OC-6, OC-7, OC-8)

**SHIPPED: `packages/aelix-coding-agent/src/aelix_agents/posture.py`.**

Today a headless child auto-approves: `builtin/permission.py:379-383` (pre-P2) is `if not ctx.has_ui: return None`. Three additions:

1. **`--permission-mode <default|auto-accept-edits|plan|yolo|auto>`** in `args.py`, applied when seeding `PermissionPosture`. The spawner **always** passes it explicitly, and it carries the **consent-resolved** mode (§2i), not the raw clamp output.
2. **A child-only headless floor** — `headless_default: Literal["allow","block"] = "allow"` on the `PermissionExtension` dataclass, flipped to `"block"` by `entry.py` only when `subagent_depth() > 0`.
3. **The clamp itself**, below.

#### `ctx.has_ui` — the corrected mechanism (OC-7)

The draft said `ctx.has_ui` is `app_mode == "interactive"` (`cli/entry.py:1095`). **That is a different local**, consumed only by `resolve_project_trusted` and never handed to the extension runtime. The real definition is in `extensions/api.py` (property at `:957`; body at `:977-978`, `:1062`/`:1082-1083` post-P2):

```python
runtime: _ExtensionRuntime = object.__getattribute__(self, "_runtime")
return runtime.ui is not HEADLESS_UI_CONTEXT
```

The conclusion (a child is `False`) is unchanged, but the **property** is different: this is **not a mode, it is a time-varying value**. It is `False` during `harness.bootstrap()` **even in interactive mode**, `True` after `tui/shell.py:1892` binds `AelixTUIContext`, re-pointed on every harness rebuild (`:1565`, i.e. `/new` / `/fork` / `/resume`), and back to `False` on TUI exit (`:1950`).

> **Consequence, and it is load-bearing: any consent gate must read `ctx.has_ui` LIVE, immediately before prompting, and must never cache it on `self`.**

#### The mapping is a CLAMP over a total order, applied to every row — not a lookup table

```python
# aelix_agents/posture.py  (PURE — no asyncio, no os, no subprocess, no UI)

_RANK: dict[PermissionMode, int] = {
    PermissionMode.PLAN: 0,
    PermissionMode.DEFAULT: 1,
    PermissionMode.AUTO_ACCEPT: 2,
    PermissionMode.AUTO: 3,
    PermissionMode.YOLO: 4,
}

_INHERIT_BASELINE: dict[PermissionMode, PermissionMode] = {
    PermissionMode.DEFAULT: PermissionMode.PLAN,
    PermissionMode.PLAN: PermissionMode.PLAN,
    PermissionMode.AUTO_ACCEPT: PermissionMode.AUTO_ACCEPT,
    PermissionMode.AUTO: PermissionMode.AUTO_ACCEPT,
    PermissionMode.YOLO: PermissionMode.YOLO,
}

def posture_rank(mode: PermissionMode) -> int:
    """Authority rank, 0 (PLAN) … 4 (YOLO). Exported so ``consent.py`` can
    decide whether offering widening would be a no-op without reaching into
    this module's private table."""
    return _RANK[mode]

def child_permission_mode(
    approval_mode: str, parent: PermissionMode, scope: str, *, has_ui: bool = False
) -> PermissionMode:
    """The FLOOR posture a child runs under — ADR-0197 §(e).

    Never returns a mode looser than ``parent``. This is the floor ONLY: the
    spawn-time consent dialog (§2i) may raise the RESULT to at most
    ``AUTO_ACCEPT``, and only on an explicit human answer.

    ``has_ui`` defaults to False — the conservative direction. It exists solely
    for ``approval_mode == "ask"`` (OC-8): with a live parent UI, ``ask``
    produces the ``inherit`` baseline the dialog then offers to raise; with no
    UI there is nobody to ask, so it collapses to PLAN exactly like ``deny``.

    ``scope`` is the profile's EFFECTIVE scope (``agents/profile.py:52``).
    Only the literal ``"project"`` triggers the widening ban; ``"explicit"``
    (``--agent-file``) is a path the human typed, so it counts as user-authored.
    """
    if approval_mode == "deny":
        requested = PermissionMode.PLAN
    elif approval_mode == "auto":
        requested = PermissionMode.AUTO_ACCEPT
    elif approval_mode == "ask" and not has_ui:
        requested = PermissionMode.PLAN
    else:  # "inherit", or "ask" WITH a live UI (the dialog's baseline)
        requested = _INHERIT_BASELINE[parent]

    # SECURITY (B4): a PROJECT-scoped profile may never WIDEN.
    #
    # CORRECTION (OC-6): this is a rank-MIN, not an assignment. The previous
    # ``requested = fallback`` form gave an ``auto``/YOLO-parent project profile
    # ``yolo`` while the same user-scope profile got ``auto-accept-edits`` — the
    # ban made the checked-in file WIDER than the user's own.
    #
    # HONEST NOTE (measured over all 5 parents): in its corrected rank-min form
    # this clause changes no answer today, because ``auto`` requests AUTO_ACCEPT
    # and AUTO_ACCEPT is already <= every parent loose enough to widen. It is
    # kept as a TESTED DEFENSIVE INVARIANT — it is what stays true if a future
    # edit raises what ``auto`` may request. The project-scope guarantee that
    # bites TODAY is §(i)'s absolute "never offer widening for a project-scoped
    # profile" dialog rule.
    if scope == "project" and approval_mode == "auto":
        fallback = child_permission_mode("inherit", parent, "user", has_ui=has_ui)
        if _RANK[fallback] < _RANK[requested]:
            requested = fallback

    clamped = requested if _RANK[requested] <= _RANK[parent] else parent
    # DEFAULT in a child means "prompt", and the child has no prompt. PLAN gives
    # the same denial with a model-actionable reason and does not depend on
    # ``headless_default`` being set correctly. Strictly tighter, never looser.
    return PermissionMode.PLAN if clamped is PermissionMode.DEFAULT else clamped
```

**Why a clamp and not a lookup table (B4).** The obvious shape — map `approval_mode` to a posture and hand it to the child — lets a profile declaring `approval_mode: auto` lift a DEFAULT (prompt-for-everything) parent into a child that auto-accepts repo-wide writes with no human in the loop. The child cannot compensate: `builtin/permission.py:348-353` returns `None` (allow) for an AUTO_ACCEPT in-cwd write roughly thirty lines ABOVE the `if not ctx.has_ui:` headless branch at `:382-383`, so the child-only headless floor never runs for exactly the writes that matter. **The posture the child is LAUNCHED with is the real guarantee, and it is computed in the parent.**

Consequences, stated plainly for the ADR:

* **Out of the box a delegated child is read-only** (parent DEFAULT → child PLAN). That is what a spawn gets when nobody answers a dialog.
* `approval_mode: auto` from a **user-scope** profile with an AUTO_ACCEPT/AUTO/YOLO parent → `auto-accept-edits`; from a DEFAULT parent → **`plan`** (this is the B4 fix).
* `approval_mode: auto` from a **project-scope** profile → never wider than the `inherit` result (rank-min, OC-6). A checked-in repo file can never widen — and §2(i) additionally never offers it a dialog option.
* `approval_mode: ask` → **prompts in the parent** (OC-8). With no live UI it collapses to `plan`. **It never refuses the spawn.** This retires the `approval_mode` "validated, not read" deferral at `agents/profile.py:218-220`.

**The shipped guarantee, in full.** The short form — *"a child never gains a permission mode looser than the parent's"* — is true of `child_permission_mode` and is **NOT** the whole product rule. What ships is one clause longer:

> **A child never gains a permission mode looser than the parent's UNLESS a human, at the parent's own TUI, explicitly granted it for that one spawn — and then never above `auto-accept-edits`, and never for a project-scoped profile.**

Do not let the shorter sentence survive anywhere in the code, the tests, or the ADR.

### (f) Project-scoped identity consent (closes B5) — UNCHANGED

The Protocol carries the gate:
```python
def resolve_profile(
    self, name_or_path: str, *, allow_project: bool = False
) -> ResolvedProfile: ...
```
`_SubagentRuntimeImpl.resolve_profile` refuses a `scope == "project"` profile unless `allow_project is True`, raising with the same message shape `agents/service.py:241-247` uses.

* **`agent` tool (model-driven) → always `allow_project=False`. Fail-closed, no prompt, ever.** The model picks the profile string, so repo content (README, comment, fixture) must not be able to select a project-authored identity with a replaced system prompt.
* **`/agents run` (user-typed) → `allow_project` is `True`** only when the parent launched with `--approve` (`parsed.project_trust_override is True`) or the user confirmed that identity this session via the existing `AgentProfileService.confirm_project` callback. Reuse it; do not re-implement `_confirm_project_agent`.

Note the deliberate asymmetry with §2(i): the identity door is **stricter** than the authority door, because there the model chose the name.

### (g) Child project trust — the TWO-CLAUSE `--no-approve` rule (closes B6; OC-4)

**SHIPPED: `packages/aelix-coding-agent/src/aelix_agents/trust.py`.**

**Delete the draft's claim that "the child treats its cwd as UNTRUSTED".** It is false. `cli/project_trust.py::resolve_project_trusted` (`:470-580`) is a **seven-step ladder**:

| step | anchor | behaviour |
|---|---|---|
| 1 | `:521-523` | `--approve`/`--no-approve` override → **immediate return, nothing persisted** |
| 2 | `:525-527` | no trust-requiring resources → **`True`** |
| 3 | `:531-548` | `project_trust` extension vote |
| 4 | `:550-557` | persisted `trust.json`, **NEAREST-ANCESTOR** walk (`:373-395`) |
| 5 | `:559-563` | global `defaultProjectTrust` |
| 6 | `:565-567` | headless → `False` |
| 7 | `:569-580` | prompt |

The headless denial a subagent would hit is **step SIX**, reached only after steps 1–5 have all declined to answer. A child in a trusted tree **is** trusted, and `:60-61` documents that trusting a parent directory transitively trusts its children.

**Decision: the flag is CONDITIONAL, not unconditional.** Zero product-core hunks — both `has_trust_requiring_project_resources` and `ProjectTrustStore` are exported in `project_trust.py:97-106`'s `__all__`, and §2(a)'s import-direction rule permits `aelix_agents.* → aelix_coding_agent.*`.

```python
# aelix_agents/trust.py  (NEAR-PURE: filesystem only via Path.resolve() + one
# already-shipped product-core predicate)

def child_trust_argv(child_cwd: Path, parent_cwd: Path) -> list[str]:
    """The project-trust flags the child argv carries — ADR-0197 §(g)."""
    try:
        same_cwd = child_cwd.resolve() == parent_cwd.resolve()
        if same_cwd and not has_trust_requiring_project_resources(child_cwd):
            return []
    except Exception:  # noqa: BLE001 — any failure means "no evidence", so deny
        return ["--no-approve"]
    return ["--no-approve"]
```

**Clause 1 — same cwd AND the gate has nothing to gate → emit NOTHING.** `has_trust_requiring_project_resources` (`project_trust.py:112-177`) checks `.aelix/extensions/`, `.aelix/mcp.json` and `.aelix/agents/` — and deliberately **NOT** `.aelix/skills/`. So a skills-only repo passes step 2 with no prompt and loads its skills today. Forcing `False` there strips `.aelix/skills/` (`entry.py:719-720` `_resolve_skill_dirs` drops the dir) for **zero security gain**, silently regressing the `inherit_skills: true` DEFAULT (`agents/profile.py:175`, `:180-181` — *"skills are inert prompt text, extensions are code"*), with the child's Notice going to stderr where a successful run never surfaces it. Measured:

```
has_trust_requiring_project_resources(repo) = False
flags '<none>'      : child> Warning: skill load: …/.aelix/skills/repoSkill/SKILL.md   ← scanned
flags '--no-approve': (nothing)                                                        ← not scanned
```

**Clause 2 — any other case, most importantly a DIFFERENT cwd (always MODEL-CHOSEN) → `--no-approve`.** This is the whole security value of the flag, and it is the **one measured escalation**:

> Alice trusts a monorepo root once (persisted). The root has no `.aelix/` at all, so nothing has ever executed. A vendored dependency merged three months ago contains `monorepo/vendor/sdk/.aelix/extensions/telemetry.py`. The parent runs at the root and has **never loaded it** (`extensions/loader.py:433` scans `cwd/.aelix/extensions` only). The model now calls `agent(profile=…, cwd="vendor/sdk")` — §6.4 allows it, it is inside the parent's tree. The child starts in `vendor/sdk`, step 4's ancestor walk finds the root's `True`, and **`telemetry.py` executes**.

```
### PARENT: run at repo ROOT
  parent executed nested ext? -> NO
### CHILD spawned with cwd=repo/vendor/sdk, NO --no-approve
  child executed nested ext? -> NESTED-EXT-EXECUTED
### CHILD same cwd, WITH --no-approve
  child executed nested ext? -> NO
```

Monorepos, vendoring, git submodules and checked-in `node_modules` trees all naturally have this shape, and **nothing is presented to a human at delegation time**.

**The narrative that does NOT work**, and must not be used to justify the flag: *"Alice cloned a repo and pressed trust → the child re-executes `.aelix/extensions/evil.py`."* That is **not an escalation** — the parent already `exec_module`'d the same file at startup, same user, same machine, same API keys. Framing delegation as "introducing RCE" double-counts a risk already accepted at the trust prompt.

**FAIL-CLOSED ON ANYTHING UNEXPECTED.** A corrupt path, a `resolve()` that raises, a predicate that blows up — all return `["--no-approve"]`. Clause 1 is an EXEMPTION from a security default, so it may only be granted on positive evidence; an exception is the absence of evidence.

**Wherever the trust gate is actually live, this is security-identical to passing `--no-approve` unconditionally.** It composes with, and does not replace, §6.4's cwd containment rule: that rule bounds WHERE a child may run, `child_trust_argv` bounds WHAT may execute once it gets there.

**Accepted residual costs (name all four in the ADR):**
1. `inherit_extensions: true` + auto-discovered project extensions are still lost on the clause-2 path. Narrow: `inherit_extensions` defaults to `False` (`agents/profile.py:187-191`), and a profile's explicit `extensions:` list renders as `-e <abs path>` and loads via loader **tier 3** (outside the guard). `extensions:` is banned at project scope entirely (`agents/profile.py:346-351`, "THE RCE CUT"), so this only affects user-scope profiles.
2. Project-local MCP servers are lost — arguably a feature; §6.3 already pops `AELIX_MCP_CONFIG` for the same reason.
3. A user's global `defaultProjectTrust: "always"` is silently ignored on the clause-2 path (**P3**).
4. **Not a cost — project-scoped agent profiles.** The spawner passes the profile BODY via `--system-prompt-file`/`--append-system-prompt-file` (`agents/resolver.py:175-178`); it never passes `--agent <name>` and the child never re-runs `discover_profiles`.
5. **Not a cost — session-only trust.** It never reaches the child anyway: `interpret_trust_option` (`:267-269`) returns `remember=False`, nothing hits disk, and the child denies at step 6.

The **wider** variant (also recovering `inherit_extensions: true` and project MCP) is +6 LOC but carries a named **TOCTOU** — an attacker creating `.aelix/extensions` between the parent's predicate check and the child's own step-2 re-evaluation. **P3.**

### (h) The temp 0600 prompt file — UNCHANGED, with one addition

**SHIPPED: `aelix_agents/prompt_file.py`.**

`tempfile.mkdtemp(prefix="aelix-subagent-")` (0700), then `os.open(path, O_CREAT|O_WRONLY|O_EXCL, 0o600)`. Name `prompt-<re.sub(r"[^\w.-]+","_",profile.name)[:64]>.md`. Content is `profile.body` (already frontmatter-free). The 0700-dir-first ordering means the mode is correct at CREATION — there is no instant where the file exists world-readable; `O_EXCL` additionally refuses to follow a planted symlink.

Unlinked in **one** `try/finally` in `PrintChannel.run` (`shutil.rmtree(tmpdir, ignore_errors=True)`), and the path is also recorded on the `_RunningChild` registry record so `stop`, `stop_all`, and the `api.add_cleanup` teardown (`extensions/api.py:1554`) unlink it even if the owning task was cancelled. **Addition:** the `finally` runs inside the shielded scope of §9.5 so a re-cancelled task cannot skip it.

### (i) Spawn-time consent — NEW (Option C; OC-1, OC-3, OC-7, OC-8)

**SHIPPED: `packages/aelix-coding-agent/src/aelix_agents/consent.py`** (the dialog, the `_ask` deny-on-error wrapper, `SpawnGrant`, the option constants). **REMAINING (WS-D3):** the `tool_call` handler in `extension.py`, the grant dict, and the `execute()` lookup.

#### The problem is an empty gate, not UX

Measured on the shipped ladder:

```
_MUTATING = ['bash','create_file','edit','execute_command','sh','shell','write','write_file']
'agent' in _MUTATING = False
```

`_MUTATING` (`permission.py:64`) is `_BASH_TOOLS | _WRITE_TOOLS` (`guardrail.py:46-47`), so an `agent` tool call falls into the non-mutating branch at `permission.py:324-325` and is **silently allowed**. Delegation is today the one action a model can take that starts a whole second agent with real authority and asks nobody.

**Adding `"agent"` to `_MUTATING` is NOT the fix and must not be attempted.** `_rule_key` (`permission.py:94-116`) falls through to an args-blind `f"tool:{tool_name}"` at `:116` — measured, `_rule_key("agent", {"profile":"a"})` and `_rule_key("agent", {"profile":"b"})` are both `'tool:agent'`. One "Yes, for this session" would approve **every profile against every task** for the rest of the run. A comment at `permission.py:64` and `tests/builtin/test_sensitive_aelix_dir.py::test_agent_is_not_in_mutating` pin this so it is not rediscovered as a "fix". `builtin/permission.py` is deliberately left alone otherwise.

#### The location is the `tool_call` hook, NOT `execute()`

The prompt lives in the `AgentsExtension`'s **own `tool_call` handler**, mirroring `builtin/permission.py`'s registration (`:283-284`), its lock (`:266`, `:387-391`), its deny-on-error (`:432-439`), its session clear (`:499-504`) and its four-option dialog (`:67-71`). Three measured reasons:

1. **No serialization in `execute()`.** The kernel runs Phase 1 — sequential prep, where `before_tool_call` fires (`loop.py:510-531`, driven from the prep loop at `loop.py:809-844`) — **sequentially**, and Phase 2 under `asyncio.gather` (`loop.py:877`) **in parallel**, with `harness/core.py:247` defaulting `tool_execution = "parallel"`. Two modals from one batch collide on `tui/chrome.py:518`'s single `_modal` slot: `mount_modal` (`:1511`) overwrites unconditionally, the first Future is orphaned, and the turn hangs until Ctrl+C.
2. **No `ctx.has_ui`.** `ToolExecutionContext` (`packages/aelix-ai/src/aelix_ai/tools.py:54-86`) has exactly four fields — `tool_call_id`, `signal`, `on_partial`, `model` — and **no UI**. `has_ui` is on `ExtensionContext` (`api.py:957`), which hooks get and `execute()` does not.
3. **No first-class refusal.** A hook returning `ToolCallResult(block=True, reason=…)` is handled by `harness/hooks.py::_reducer_tool_call` (`:1419-1439`) — sequential, **first block wins** — and the kernel renders it as a model-readable `_Immediate` error result (`loop.py:518-531`). An `execute()` refusal is just another error string.

In the hook the **kernel serializes for us**: every permission prompt in a batch completes before any `execute()` starts. Belt and braces for two `agent` calls in one assistant message: the tool declares `execution_mode="sequential"` (§9.7) **and** `consent.py` holds a process-wide `asyncio.Lock` around the prompt.

#### Consent semantics (the shipped dialog)

* **Fires only when write authority is at stake** *(owner amendment, 2026-07-27 — supersedes the earlier "fires whenever the resolved mode `!= PLAN`")*. With a live UI the dialog opens **iff** `grants_write_authority(clamped) or _may_widen(resolved, clamped, has_ui=True)`; a delegation that is read-only **and** cannot be widened proceeds with **no prompt**, returning the grant the baseline option would have produced (`mode == clamped`, `widened=False`, `consented=True`). Three things make this the shipped rule rather than a UX tweak:
  * **`grants_write_authority` is DERIVED, not `clamped is PLAN`.** It is a threshold on `posture.py`'s own `_RANK` ladder (`>= AUTO_ACCEPT`), walked off `builtin/permission.py`'s branch order for a **child** (`ctx.has_ui = False`, `headless_default = "block"`): `plan` blocks at branch (b); `default` reaches branch (d) and blocks, because DEFAULT means *ask* and asking is what is blocked; `auto-accept-edits` allows an in-cwd write at branch (f), ~30 lines **above** (d) — finding B4; `auto` the same plus classifier-ALLOW bash at (g); `yolo` everything at (e). Pinned against the **real** `PermissionExtension` by `tests/agents_ext/test_posture_clamp.py::test_write_authority_matches_the_real_permission_ladder`.
  * **Order: the early-out consults `_may_widen`, not the clamp alone.** A **declaring** profile under a `default` parent also clamps to `plan` but CAN be lifted, so it still prompts — otherwise shift+tab (which raises the WHOLE session) becomes the only way to let a child write, which is exactly the trade "why read-only-only was rejected" already refuses.
  * **Measured blast radius:** 60 live-UI `(parent × scope × approval_mode)` cells prompted before either amendment, **49** after the first, **35** after the second.

* **SECOND AMENDMENT, same day — widening is offered ONLY to a profile that DECLARED it needs write authority.** The first form left `_may_widen` True for *any* user/`explicit`-scope profile with a live UI below the ceiling, so only 9 of 40 cells stopped prompting (all project-scoped) and **an ordinary read-only user-scope delegation still showed a modal**, purely because a human could theoretically widen it there — the same click-through trainer, wearing a different hat. `_may_widen` gains **constraint 0**: `declares_write_authority(resolved.profile.approval_mode)`, a `frozenset({"auto", "ask"})` allow-list in `posture.py`.
  * `auto` **declares** — it literally asks for auto-approved writes; under a tight parent B4's clamp refuses to grant that silently, which is exactly what a dialog is for.
  * `ask` **declares** — it asks for a human decision at spawn time, and the only decision this dialog offers is how much authority to grant. It must stay on this side or the value is **inert**: its clamp under a `default` parent is `plan`, so the early-out would suppress the dialog `ask` exists to open, re-opening the "validated, not read" deferral OC-8 closed.
  * `inherit` **does not** — it asks for no more than the parent already has; when the parent IS loose the clamp is write-capable and the dialog fires through `grants_write_authority` instead. This is the owner's headline case: an ordinary read-only delegation renders **no dialog at all**.
  * `deny` **never** — the explicit opposite; its clamp is `plan` in every cell.
  * Unrecognised values declare nothing (allow-list, fail-closed).
  * **Consequences the owner accepted:** a non-declaring read-only spawn proceeds with no dialog; a declaring one gets the dialog with the bounded widening exactly as before; **a human can no longer opportunistically upgrade a non-declaring profile at spawn time** — authority follows *declared* intent, and the way to grant writes is to edit the profile, a reviewable and signable artifact.
  * **Unchanged:** the `grants_write_authority(clamped)` half (that path never reached `_may_widen`), the `AUTO_ACCEPT` ceiling, the project-scope ban, headless silent downgrade, fail-closed declining.

  **Owner's reasoning (binding):** a modal that appears when there is no real choice trains click-through, and the habit endangers the prompts that DO matter — a dialog rendering only `["Run read-only (plan)", "Cancel"]` is practice at dismissing gates.

  **The project-scope identity gate is not holed, and that is the precondition.** The consent dialog was a second place a human saw *which* identity runs. Both doors that can reach a project profile still meet a human first: the model-driven `agent` tool hardcodes `allow_project=False` at **both** sites (`tool_call` hook + the `execute()` re-resolve) so a project identity is unreachable there; `/agents run` resolves with the `False` default, catches `ProjectScopeRefused`, runs `_confirm_project_agent_for_run`, and only on the affirmative re-resolves with `allow_project=True` — the **only** `allow_project=True` in the source tree. Pinned by `tests/tui/test_agents_run_command.py::test_a_project_identity_is_unreachable_without_the_affirmative`.

  **Residual (ADR-0197 R7):** a read-only delegation now happens with no per-spawn human confirmation, bounded instead by the clamp (a `plan` child is refused every mutating tool at branch (b), headless or not), by `MAX_DELEGATIONS_PER_PROMPT` / `MAX_LIVE_CHILDREN`, and by the statusline row + `subagent_*` events. **The "no session memo" rule is unchanged** — nothing is persisted, nothing memoised, and a spawn that prompts prompts every time.
* **The MODEL door pre-filters ahead of this, with LITERALLY the same function.** `AgentsExtension._grant_for` calls `consent_is_required(resolved, want, has_ui=...)` — the exported predicate `request_spawn_consent`'s own early-out calls — and skips the dialog when it is False. It has been respelled three times (`want is PermissionMode.PLAN` → `not grants_write_authority(want) and approval_mode != "ask"` → the shared predicate); the middle form **disagreed with the dialog on two cells**, so a read-only user-scope delegation prompted through `/agents run` but not through the model door, and a declaring `auto` profile under a tight parent was swallowed entirely. `MAX_DELEGATIONS_PER_PROMPT` is the bound on the branch that stays silent.
* **Options, in order** (`build_options`):
  1. the **baseline** — take the clamp unchanged. Labelled `f"Run read-only ({clamped.value})"` when the clamp IS `plan`, and `f"Run with the inherited posture ({clamped.value})"` otherwise. *This wording split is load-bearing:* under an AUTO_ACCEPT / AUTO / YOLO parent with `approval_mode: inherit` the clamp is that same posture, and a button reading "Run read-only" that actually grants `yolo` would be the single most dangerous string in the dialog. `clamped.value` is in both branches, so the granted authority is always on screen.
  2. **(conditional)** `f"Allow file edits for this run ({auto-accept-edits})"` — offered only when `_may_widen` says yes.
  3. **`"Cancel"`** — always last, always present.
* **The human answer may RAISE the clamped mode to at most `AUTO_ACCEPT`**, and only when: `scope != "project"`, `ctx.has_ui` is True **at the moment of the prompt**, and nothing is persisted. `auto` / `yolo` are **unreachable from the dialog**, for any parent posture — so bash in the child stays gated by the child's own ladder even after a human says yes.
* **Headless (`-p` / `--mode json` / rpc) never prompts and never refuses — it silently DOWNGRADES to the clamp** (PLAN in the default case). No new refusal path appears for existing non-interactive users.
* **Esc, `Cancel`, a `select` that raises, and a non-`str` answer are all a DECLINE** (`consented=False`). `BaseException` is deliberately not caught: a `CancelledError` means the turn is aborting, and the correct response is to propagate, not to synthesise a decision the human never made.
* **Widening is matched by identity against the rendered option** (`answer == options[1]`), never by substring — a `"auto-accept" in answer` test would be steerable by a profile named `auto-accept-edits`.
* **The dialog body rides the `title` string.** `ExtensionUIDialogOptions` (`extensions/ext_ui.py:55-67`) carries only `signal` and `timeout`; the extension-facing `select` (`ext_ui.py:186-193`) has no body/detail parameter. `resolved.source_path` is **always shown and is deliberately the second line** — the task text below it is model-authored and injectable, whereas the path is a human-owned file the user can go and read. The task is flattened (`" ".join(task.split())`) and truncated to **`TASK_PREVIEW_CHARS = 300`**; flattening matters independently, because 300 newlines would otherwise render as a 300-row modal.

**DIVERGENCE FROM DELTA 6, recorded deliberately.** Delta 6 specified four rungs including *"don't ask again for this profile this session"* keyed on `resolved.source_path`. **That rung is NOT shipped and must NOT be added in P2.** `tests/agents_ext/test_spawn_consent.py::test_grant_is_not_persisted_between_spawns` pins *"P2 ASKS EVERY TIME"*, and ADR-0197's *Deferred* list records the memo as **P3**, keyed on `(profile, source_path, mode)` and never on a rule key. Delta 6's rung (3) *"delegate read-only (plan) instead"* is **merged into option 0** whenever the clamp is already `PLAN` — the DEFAULT-parent case, i.e. the common one; under a looser parent option 0 states the real inherited posture and there is no separate downgrade option. Rationale for keying on `source_path` rather than `profile.name` is preserved for P3: a project profile wins a name collision (`cli/entry.py:1051-1052` / `agents/service.py:99-100`), so the name is not the identity.

#### The constraints on bounded widening (`_may_widen`) — 0 plus five

0. `declares_write_authority(resolved.profile.approval_mode)` — the profile said it needs write authority (`auto` / `ask`). *(Owner amendment, 2026-07-27, second form.)* Numbered 0 because 1–5 **bound** how far a widening may go while this one decides whether the option exists at all.
1. `resolved.scope != "project"`. A project-scoped profile can **NEVER** be widened, dialog or no dialog. This preserves B4 verbatim — the vulnerability was *a repo file widening silently*, and a human answering a modal is not that.
2. `has_ui` is True **at the moment of the prompt** (live read, OC-7).
3. The ceiling is **exactly `AUTO_ACCEPT`**.
4. It must not be a no-op: `AUTO_ACCEPT` must be strictly looser than `clamped`, or the option is a lie.
5. *(Structural, not checked in code.)* `".aelix"` is in `_SENSITIVE_DIR_COMPONENTS` (§5.4 hunk 5, OC-5). Shipping widening without it is what would let a writable child author the parent's NEXT identity or project extension.

**Why read-only-only was rejected.** Under a narrow-only rule, a user who wants a child to write must raise the **whole session** to `auto-accept-edits` with shift+tab — after which §1.4's matrix says every subsequent model-chosen delegation silently inherits it. That is strictly **wider** than granting one child one run. A narrow-only rule pushes users toward more authority, not less.

#### The grant, and the anti-bypass invariant

```python
@dataclass(frozen=True)
class SpawnGrant:
    profile: str
    source_path: str
    scope: str
    mode: PermissionMode   # the posture the child will ACTUALLY run under
    widened: bool          # True only when a human lifted it above the clamp
    consented: bool        # False = declined / Esc / no answer / dialog failure

async def request_spawn_consent(
    ctx, resolved: ResolvedProfile, task: str, parent: PermissionMode, *, cwd: str
) -> SpawnGrant:
    ...  # never raises, never spawns
```

Flow: (1) compute the clamp with a **live** `ctx.has_ui`; (2) **no live UI → return the clamp, `consented=True`, `widened=False`, without prompting**; (3) **nothing at stake (`not consent_is_required(resolved, clamped, has_ui=has_ui)`) → return the clamp, `consented=True`, `widened=False`, without prompting** *(owner amendment, 2026-07-27)*; (4) otherwise recompute `_may_widen`, build the options and prompt under the process-wide lock.

**Transport:** a private `dict[str, PermissionMode]` (or `dict[str, SpawnGrant]`) on the extension, keyed by `tool_call_id`. `ToolCallHookEvent.tool_call_id` and `ToolExecutionContext.tool_call_id` are the **same id**. `execute()` does `self._grants.pop(tool_call_id, None)` with a **`None` default that FAILS CLOSED** — a caller that skipped the hook cannot spawn. **Do NOT smuggle the grant through mutated `event.args`** even though `harness/core.py:3723-3725` permits it: that puts an unvalidated key into the transcript.

**The lock is keyed by running loop, not created at import.** `asyncio.Lock` binds itself to the first loop that CONTENDS on it (`_LoopBoundMixin` — the uncontended fast path never touches `_get_loop`) and raises *"bound to a different event loop"* forever after. Production has one loop; a pytest session builds a fresh loop per test, and without the keying a single contended test would poison every later one.

**Two doors, two entry points.** The Protocol's `spawn()` is the **user-typed** door (`/agents run`) and takes its own consent internally; a declined dialog yields `SubagentResult(status="declined", ok=False)` — never an exception, never a silent spawn. The model-driven `agent` tool uses an **implementation-private** entry point (`spawn_granted(grant, …)`) carrying a grant already taken in the hook. **The grant type is deliberately NOT on the Protocol** — pinned by `tests/agents/test_subagent_contract.py::test_protocol_has_no_consent_parameter` and `tests/agents/test_p2_band_boundaries.py::test_product_core_never_prompts_for_spawn_consent`.

#### Accepted residuals (state them as trade-offs in the ADR, not as gaps)

* **R1 — consent is per-delegation, not per-tool.** One "yes" and that child writes unattended for its whole run; the human never sees the individual writes. **This is exactly what the back-channel would fix, and is the real price of deferring it to P3.**
* **R2 — the task string in the dialog is model-authored** and therefore injectable. Mitigated by always displaying `resolved.source_path` and truncating the task to 300 chars, and by never offering widening at project scope.
* **R3 — modal rendering is not unit-testable.** Our dialog is TALLER than the shipped approval dialog (path + 300 chars of task) and could hit `_CappedContainer` / `_modal_cap` clipping differently (`tui/overlay.py:256-271`). Mitigation: copy `approval_dialog.py:280-289`'s HSplit-with-fixed-options-window pattern — its own comment documents exactly why the deny option must stay visible when the body overflows — plus one budgeted live-TUI smoke run (§12 Wave 4).
* **R4 — a turn cannot be interrupted while the modal is open.** prompt-toolkit's `_CombinedRegistry` gives control-level bindings priority, so the modal absorbs Ctrl+C / Esc. Identical to today's approval dialog; Esc = decline = the turn continues.
* **R5 — the `.aelix` mitigation is a user-visible behaviour change** (OC-5). CHANGELOG line shipped.
* **R6 — the bash env-var bypass remains open** (§2d). Unchanged by any of this.
* **R7 — a read-only delegation happens with no per-spawn human confirmation** *(owner amendment, 2026-07-27, both forms)*. After the second form this is the **ordinary** case, not an edge one: an `inherit` profile — no `approval_mode:` line at all — under a `default` parent is what most delegations look like, and it now runs with no modal. Bounded instead by the clamp (a `plan` child is refused every mutating tool at branch (b) of its own ladder, headless or not), by `MAX_DELEGATIONS_PER_PROMPT` (12, per user prompt, model door) and `MAX_LIVE_CHILDREN` (4, both doors), and by the statusline row + `subagent_*` events, which make the run visible while it happens. **Not** bounded: reading — R1's per-tool consent is still deferred. The identity that runs is still gated (project scope by `_confirm_project_agent_for_run` / `allow_project=False`; user scope by the profile living in the user's own `~/.aelix/agent/agents/`).

---

## 3. Module layout

### New — product-core (interface only) — **SHIPPED**
```
packages/aelix-coding-agent/src/aelix_coding_agent/subagent_contract.py
```
Top-level sibling of `login_registry.py` / `model_registry.py`. **Hard rule: no runtime import from `aelix_coding_agent`** — only stdlib + a `TYPE_CHECKING` import of `AgentProfile`.

### New — the bundled extension (**12** modules)
```
packages/aelix-coding-agent/src/aelix_agents/
  __init__.py          # AgentsExtension + __all__                    [WS-D3]
  extension.py         # setup(), bind_subagents, register_tool,
                       # the tool_call CONSENT handler, add_cleanup   [WS-D3]
  runtime.py           # _SubagentRuntimeImpl — Protocol impl + child registry [WS-D2]
  print_channel.py     # argv/env/spawn/pumps. THE ONLY FILE THAT SPAWNS.      [WS-D2]
  reaper.py            # the shielded, cancellation-safe kill path             [WS-D2]
  stream.py            # PURE — LineAssembler (bytes→lines) + reduce_line   ✅ SHIPPED
  envelope.py          # PURE — _StreamState → SubagentResult; cap; fallback ✅ SHIPPED
  posture.py           # PURE — child_permission_mode() clamp (§2e)         ✅ SHIPPED
  consent.py           # the spawn-time dialog, _ask deny-on-error, SpawnGrant (§2i) ✅ SHIPPED
  trust.py             # child_trust_argv() — the two-clause rule (§2g)      ✅ SHIPPED
  prompt_file.py       # the 0600 temp writer + its context manager          ✅ SHIPPED
  tool.py              # the `agent` AgentTool (single mode) + roster description [WS-D3]
  progress.py          # SubagentProgress on api.events + the guarded statusline row [WS-D3]
```
`stream.py`, `envelope.py`, `posture.py` are **pure** — no `asyncio`, no `os`, no `subprocess`. `trust.py` is **near-pure** (filesystem only via `Path.resolve()` + one product-core predicate). `consent.py` is **the only module in `aelix_agents` allowed to touch `ctx.ui` / `ctx.has_ui`**, and it contains no spawn code and no argv code.

**`tui/shell.py` is still NOT edited.** The consent handler reads `ctx.ui` / `ctx.has_ui` off its own `ExtensionContext`. Re-verified: `shell.py:1565` re-points `bind_ui` on every rebuild and `_rebind` repoints `ctx.harness`, so the statusline and `/agents run` both read live.

### Edited — product-core
| File | Hunks | State |
|---|---|---|
| `.../aelix_coding_agent/extensions/api.py` | **4** (was 3) | ✅ SHIPPED |
| `.../aelix_coding_agent/cli/args.py` | 3 | ✅ SHIPPED |
| `.../aelix_coding_agent/cli/entry.py` | 5 | REMAINING (WS-E) |
| `.../aelix_coding_agent/builtin/permission.py` | **5** as landed (draft 2 · delta-10 3) | ✅ SHIPPED |
| `.../aelix_coding_agent/tui/commands.py` | 2 | REMAINING (WS-E) |
| `packages/aelix-coding-agent/pyproject.toml` | 1 (**line 100-101**) | **REMAINING (WS-D)** |
| `packages/aelix-ai/src/aelix_ai/settings/types.py` | 5 | ✅ SHIPPED |
| `packages/aelix-ai/src/aelix_ai/settings/settings_manager.py` | 1 | ✅ SHIPPED |
| `packages/aelix-ai/src/aelix_ai/settings/__init__.py` | 2 | ✅ SHIPPED |
| `.../aelix_coding_agent/tui/settings_rows.py` | 3 | ✅ SHIPPED |
| `.github/workflows/ci.yml` | 1 (`fetch-depth: 0`) | ✅ SHIPPED |

---

## 4. `subagent_contract.py` — the shipped contract

**SHIPPED VERBATIM at `packages/aelix-coding-agent/src/aelix_coding_agent/subagent_contract.py` (248 lines). Read the file; the signatures below are the contract downstream code binds against.**

```python
CONTRACT_VERSION = 1               # bumped only on a SHAPE change; additive defaulted fields do NOT bump
MIN_SUPPORTED_CONTRACT_VERSION = 1 # bind_subagents accepts MIN <= v <= CONTRACT_VERSION   (B9)
MAX_SUBAGENT_DEPTH = 1             # a GUARD, not a feature; not configurable
DEPTH_ENV_VAR = "AELIX_SUBAGENT_DEPTH"

SUBAGENT_START = "subagent_start"
SUBAGENT_TOOL  = "subagent_tool"
SUBAGENT_END   = "subagent_end"    # payload of all three: SubagentProgress

SubagentState   = Literal["starting", "running", "done", "error", "stopped"]
SubagentOutcome = Literal["ok", "error", "timeout", "aborted", "declined"]   # "declined" = §2(i)
SubagentMode    = Literal["single", "parallel", "chain"]                     # only "single" is live in P2

class SubagentStatus(TypedDict):
    id: str; profile: str; state: SubagentState
    current_tool: str | None; elapsed_ms: int; tokens: int; cost: float

@dataclass(frozen=True)
class SubagentUsage:
    input: int = 0; output: int = 0; cache_read: int = 0; cache_write: int = 0
    cost: float = 0.0
    tokens: int = 0   # context LEVEL, not a sum — last message wins
    turns: int = 0

@dataclass(frozen=True)
class SubagentResult:
    id: str; profile: str; ok: bool; status: SubagentOutcome; summary: str
    truncated: bool = False
    usage: SubagentUsage = field(default_factory=SubagentUsage)
    error: str | None = None
    exit_code: int | None = None
    stop_reason: str | None = None
    elapsed_ms: int = 0
    dropped_tools: tuple[str, ...] = ()   # asked for but not in the parent's grant
    details: str | None = None            # UNCAPPED raw material behind `summary`      (B8)
    dropped_lines: int = 0                # stdout lines over the 4 MiB per-line budget (B3)
    permission_mode: str | None = None    # the posture the child ACTUALLY ran under (§2e/§2i)

@dataclass(frozen=True)
class SubagentProgress:
    id: str; profile: str; state: SubagentState
    current_tool: str | None = None; elapsed_ms: int = 0; tokens: int = 0; cost: float = 0.0

@dataclass(frozen=True)
class ResolvedProfile:
    name: str
    profile: AgentProfile   # TYPE_CHECKING-only forward ref
    source_path: str        # ALWAYS shown at the consent dialog (§2i, residual R2)
    scope: str

@runtime_checkable
class SubagentRuntime(Protocol):
    """Product-core CALLS this. Product-core must never implement it, and must
    never encode any consent or posture POLICY — ``spawn`` takes its own consent
    internally (§2i); the grant type is deliberately NOT on this Protocol."""

    contract_version: int

    def resolve_profile(self, name_or_path: str, *, allow_project: bool = False) -> ResolvedProfile: ...
    async def spawn(
        self, resolved: ResolvedProfile, task: str, *,
        cwd: str | None = None, mode: SubagentMode = "single",
        timeout_ms: int | None = None, background: bool = False,
        on_event: Callable[[SubagentProgress], None] | None = None,
    ) -> SubagentResult: ...
    def list(self) -> list[SubagentStatus]: ...
    def status(self, id: str) -> SubagentStatus: ...
    async def stop(self, id: str) -> None: ...
    async def stop_all(self) -> None: ...

def subagent_depth(env: dict[str, str] | Any | None = None) -> int: ...
    # malformed / negative → 0 ("I am the root" — the most restrictive downstream)
```

`resolve_profile`'s docstring carries the B5 rule verbatim: a `scope == "project"` profile is REFUSED unless `allow_project` is True; model-driven callers must always pass False. `spawn`'s docstring carries §2(i): the implementation takes consent itself before any process exists, and a declined dialog yields `status="declined"`.

---

## 5. Exact insertion anchors — product-core edits

### 5.1 `extensions/api.py` — **4 hunks** — ✅ SHIPPED

**Hunk 1 — the `if TYPE_CHECKING:` / `pass` block at 108-109:**
```python
if TYPE_CHECKING:
    from aelix_coding_agent.subagent_contract import SubagentRuntime
```
TYPE_CHECKING-only on purpose: `subagent_contract` is interface-only and must stay importable without `api.py`, and `api.py` must not gain a runtime dependency on a seam that is empty (`self._subagents is None`) in every default build. `api.py:47` already has `from __future__ import annotations`.

**Hunk 2 — line 127 (closes B9/I5):**
```python
        code: Literal["stale", "unbound", "invalid_state", "contract_mismatch"],
```
plus the docstring line at `:118-123`: `- "contract_mismatch" — a bound runtime declares a contract_version outside this build's supported range (ADR-0197).`

**Hunk 3 — line 493**, last statement of `_ExtensionRuntime.__init__`, immediately after `self._ui: ExtensionUIContext = HEADLESS_UI_CONTEXT`:
```python
        # ADR-0197 (P2) — subagent-runtime binding slot. Default ``None``:
        # product-core ships the CONTRACT, the bundled ``aelix-agents``
        # extension binds the implementation at setup().
        self._subagents: SubagentRuntime | None = None
```

**Hunk 4 — after `bind_ui` (ends 535), before `bind_core` (537):** the `subagents` property, `bind_subagents(runtime, *, replace=False)` with its **three refusals in this order — depth, then version range, then double-bind** — and `unbind_subagents(runtime)` (identity-scoped: `if self._subagents is runtime: self._subagents = None`). The contract import inside `bind_subagents` is **function-local** so the contract stays a leaf module. `__all__` at `api.py:1942` is unchanged — the types are re-exported from `subagent_contract`, not from `api`.

### 5.2 `cli/args.py` — 3 hunks — ✅ SHIPPED (shape corrected)

**Hunk 1 — module scope, beside `VALID_MODES`:**
```python
VALID_PERMISSION_MODES: tuple[str, ...] = tuple(m.value for m in PermissionMode)
```
**DERIVED, not re-spelled**, so the parser can never accept a posture the gate does not know (or reject one it does). `from aelix_coding_agent.builtin.permission_mode import PermissionMode` at module scope — verified safe: `permission_mode.py` imports only stdlib + `dataclasses`, no cycle. Add `"VALID_PERMISSION_MODES"` to `__all__`.

**Hunk 2 — `class Args`, beside `no_extensions` (~152; `project_trust_override` at 155):**
```python
    permission_mode: str | None = None
    agents_override: bool | None = None
```
`permission_mode`'s docstring carries the SECURITY note: a delegated child receives this from the spawner, which computes it with `aelix_agents.posture.child_permission_mode` — a CLAMP against the parent's live posture — **optionally raised by ONE explicit human answer at the spawn-time consent dialog, never above `auto-accept-edits` and never for a project-scoped profile**. The whole child-authority guarantee is argv-shaped, which is why `tests/cli/test_permission_mode_flag.py` pins that this exact spelling PARSES (B10).
`agents_override`'s docstring: `--agents` → True, `--no-agents` → False, absent → None → the global `[features] agents` (default False). The spawner passes `--no-agents` to every child.

**Hunk 3 — the parse loop, after the `--thinking` branch (~466):**
```python
        elif arg == "--permission-mode":
            if i + 1 < n:
                value = argv[i + 1]
                if value in VALID_PERMISSION_MODES:
                    parsed.permission_mode = value
                    parsed.provided.add("permission_mode")
                else:
                    parsed.diagnostics.append(
                        {"type": "warning", "message": f"Invalid --permission-mode: {value}"}
                    )
                i += 1
        elif arg == "--agents":
            parsed.agents_override = True
        elif arg == "--no-agents":
            parsed.agents_override = False
```
**CORRECTION vs the draft:** warn via `parsed.diagnostics`, **not** `print(..., file=sys.stderr)` — mirroring `--thinking` (`args.py:423-429` pre-P2). A rejected value must **NOT** be recorded in `parsed.provided`: it leaves the field at its default, so claiming the user supplied it would let a bad flag veto a valid overlay.

**Help block (`args.py:583`), beside `--no-approve, -na`:**
```
  --permission-mode <mode>        default | auto-accept-edits | plan | yolo | auto
  --agents / --no-agents          Enable/disable agent delegation for this run
```

### 5.3 `cli/entry.py` — 5 hunks — **REMAINING (WS-E)**

**Hunk 1 — `_build_harness_options` signature (763-777).** Add after `permission_ext` (770): `agents_ext: Any | None = None,`

**Hunk 2 — lines 868-873**, the prepend site. Keep the existing `permission = ...` line and the comment block at `860-867` intact; replace only the list literal:
```python
    # ADR-0197 (P2) — the bundled ``aelix-agents`` delegation extension.
    # APPENDED, never inserted: Guardrail stays FIRST (first-block-wins, see the
    # DO NOT REORDER note above) and the permission gate second; OUR tool_call
    # handler — which carries the spawn-time consent gate (§2i) — therefore runs
    # LAST. The DEPTH GATE lives here as well as in the seam and the tool: a
    # process already at MAX_SUBAGENT_DEPTH must not even LOAD the extension, so
    # a nested child physically has no ``agent`` tool regardless of
    # ``inherit_extensions`` / ``-e``.
    prepend_extensions: list[Any] = [GuardrailExtension(), permission]
    if agents_ext is not None and subagent_depth() < MAX_SUBAGENT_DEPTH:
        prepend_extensions.append(agents_ext)
    loaded = await discover_and_load_extensions(
        [str(p) for p in parsed.extensions],
        ...
        prepend=prepend_extensions,
```
Module-scope import in the `entry.py:56-71` block:
```python
from aelix_coding_agent.subagent_contract import MAX_SUBAGENT_DEPTH, subagent_depth
```

**Hunk 3 — `_async_main`, after `permission_ext = PermissionExtension(posture=permission_posture)` (~1343):**
```python
    # ADR-0197 §(e) — CHILD-ONLY headless floor. A delegated child has no TUI, so
    # ``permission.py``'s ``not ctx.has_ui`` branch auto-ALLOWS every mutating
    # tool. Flipping it to block-with-reason ONLY when this process is itself a
    # subagent leaves every existing ``-p`` / json / rpc user untouched.
    # NOTE (finding B4): this floor is NOT sufficient on its own — the
    # AUTO_ACCEPT write branch returns ABOVE it. The real guarantee is the
    # spawner-side posture CLAMP, optionally raised by §2(i)'s consent dialog.
    if subagent_depth() > 0:
        permission_ext.headless_default = "block"
    if parsed.permission_mode is not None:
        permission_posture.set(PermissionMode(parsed.permission_mode))

    # ADR-0197 §(a)/(b) — the bundled delegation extension. Constructed ONCE and
    # threaded by held reference into every harness rebuild (mirror of
    # ``permission_ext``) so the child registry and ``stop_all`` survive
    # /new / /fork / /resume. The import is lazy so a broken extension can never
    # brick startup for a user who has delegation off.
    agents_ext: Any = None
    if _agents_delegation_enabled(parsed, settings_manager):
        try:
            from aelix_agents import AgentsExtension
        except Exception as exc:  # noqa: BLE001 — never fatal
            print(f"Warning: aelix-agents unavailable: {exc}", file=sys.stderr)
        else:
            agents_ext = AgentsExtension()
```
plus a module-level helper beside `_resolve_active_tools` (`entry.py:454`):
```python
def _agents_delegation_enabled(
    parsed: Args, settings_manager: SettingsManager | None
) -> bool:
    """``--no-agents`` > ``--agents`` > global ``[features] agents`` (default False).

    The settings read is GLOBAL-scope-only inside the getter — a project's own
    ``.aelix/settings.json`` must not be able to switch delegation on.
    """
    if parsed.agents_override is not None:
        return parsed.agents_override
    if settings_manager is None:
        return False
    return settings_manager.get_features_agents()
```

**Hunk 4 — every `_build_harness_options(...)` call inside `_harness_factory`.** Thread `agents_ext=agents_ext` alongside `permission_ext=permission_ext`. Grep `permission_ext=` inside `entry.py` and mirror each occurrence.

**Hunk 5 — `entry.py:64`** already imports `PermissionPosture`; extend to `from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture`.

### 5.4 `builtin/permission.py` — **5 hunks as landed** — ✅ SHIPPED

**Hunk 1 — `typing` import:** `from typing import Any, Literal`.

**Hunk 2 — above `_MUTATING` (line 64).** The DO-NOT-ADD comment (§2i):
```python
# DO NOT add ``"agent"`` here (ADR-0197 §(i)). It looks like the delegation
# consent gate and is not one: ``_rule_key`` falls through to an ARGS-BLIND
# ``f"tool:{tool_name}"`` at ``:116``, so a single "Yes, for this session" would
# approve every profile against every task for the rest of the run. Delegation
# consent lives in ``aelix_agents/consent.py``, keyed on what actually varies
# (profile + source_path + posture) and never persisted.
_MUTATING = _BASH_TOOLS | _WRITE_TOOLS
```

**Hunk 3 — beside `_MUTATING`:**
```python
_HEADLESS_BLOCK_REASON = (
    "This delegated agent has no approval channel, so mutating tools are "
    "blocked. Report what you would have changed and let the parent decide."
)
```
Phrased FOR THE MODEL: the child has no approval channel (the back-channel is P3), so the only useful thing it can do is report the intended change back to the parent, whose human can act.

**Hunk 4 — after `approval_runner` (field block ends 279):**
```python
    headless_default: Literal["allow", "block"] = "allow"
```
Docstring: `"allow"` preserves the shipped non-interactive behaviour for every `-p` / `--mode json` / `--mode rpc` user; `cli/entry.py` flips it to `"block"` for a DELEGATED CHILD only (`subagent_depth() > 0`). **SCOPE (B4):** this floor sits at branch (d), BELOW the AUTO_ACCEPT write short-circuit at branch (f) (`:348-353` pre-P2), which `return None`s before control reaches here. **Do not mistake this belt for the braces.**

**Hunk 5 — `_SENSITIVE_DIR_COMPONENTS` (line 219 pre-P2 → 253-255 post-P2) — the OC-5 hard prerequisite:**
```python
_SENSITIVE_DIR_COMPONENTS = frozenset(
    {".aelix", ".ssh", ".gnupg", "cron.d", "cron.daily"}
)
```
WHY comment: `.aelix/extensions/*.py`, `.aelix/mcp.json` and `.aelix/agents/*.md` are EXACTLY the three resources the Project Trust gate exists to guard (`cli/project_trust.py:112-177`), and `.aelix/settings.json` is the user's own configuration. Before this entry an auto-accepting agent could WRITE the project identity / project extension that a LATER run then EXECUTES under an ancestor `trust.json: true` (`project_trust.py:550-557`, transitivity at `:60-61`) — a write-to-exec escalation `--no-approve` cannot touch, because it only stops a child LOADING such a file, never writing one. **BEHAVIOUR CHANGE:** an INTERACTIVE AUTO_ACCEPT user editing their own `.aelix/agents/*.md` now sees the 4-option prompt instead of a silent write. That is the intended trade. CHANGELOG entry shipped under `### Changed`.

**And the headless branch (`:379-383` pre-P2 → `:431-441` post-P2):**
```python
        if not ctx.has_ui:
            if self.headless_default == "block":
                return ToolCallResult(block=True, reason=_HEADLESS_BLOCK_REASON)
            return None
```

### 5.5 `tui/commands.py` — 2 hunks — **REMAINING (WS-E)**

**Hunk 1 — line 479:**
```python
_AGENTS_USAGE = "Usage: /agents [list | show <name> | use <name> | use --none | run <name> <task>]"
```

**Hunk 2 — inside `_agents_handler` (opens 640), between the `use` block and the unknown-subcommand line 721:**
```python
    if sub == "run":
        # ADR-0197 — INTERFACE ONLY. This calls a bound Protocol
        # (``runtime.subagents``); it never spawns and never prompts for spawn
        # consent — ``spawn()`` takes its own (§2i). Built-ins shadow the whole
        # first word (``shell.py:2452-2459``) and extension command names cannot
        # contain a space (``command_dispatch.py:76-85``), so an extension can
        # NEVER serve ``/agents run``. Hence the branch is here, not there.
        runtime = getattr(getattr(ctx.harness, "runtime", None), "subagents", None)
        if runtime is None:
            ctx.commit(
                Text(
                    "Delegation is unavailable. Enable it with [features] agents "
                    "(/settings) or relaunch with --agents.",
                    style="yellow",
                )
            )
            return
        name, _, task = rest.partition(" ")
        if not name or not task.strip():
            ctx.commit(Text(f"/agents run needs a name and a task. {_AGENTS_USAGE}", style="yellow"))
            return
        # SECURITY (finding B5): ``/agents run`` is USER-typed, so it may consent
        # to a project-scoped IDENTITY — but only via the same per-identity gate
        # ``--agent`` uses at startup (``entry.py:1489-1503``) or an explicit
        # ``--approve``. A model-driven ``agent`` tool call never gets this.
        allow_project = ctx.parsed.project_trust_override is True
        try:
            resolved = runtime.resolve_profile(name, allow_project=allow_project)
        except Exception as exc:  # noqa: BLE001
            if not allow_project and _is_project_scope_refusal(exc):
                if not await _confirm_project_agent_for_run(ctx, name):
                    ctx.commit(Text("Project agent profile declined.", style="yellow"))
                    return
                resolved = runtime.resolve_profile(name, allow_project=True)
            else:
                ctx.commit(Text(f"✖ agents run failed: {exc}", style="bold red"))
                return
        try:
            result = await runtime.spawn(resolved, task.strip())
        except Exception as exc:  # noqa: BLE001 — surface, never kill the REPL
            ctx.commit(Text(f"✖ agents run failed: {exc}", style="bold red"))
            return
        for renderable in _render_subagent_result(result):
            ctx.commit(renderable)
        return
```
Two new module-level helpers beside `_render_agent_profile` (ends ~637):
* `_render_subagent_result(result) -> list[RenderableType]` — a `Panel` with the summary plus a usage grid (turns / in / out / cache-read / cost / elapsed), border `green` when `ok` else `red`, a `truncated` note, **the granted `result.permission_mode` on its own line**, a distinct yellow "Declined." rendering for `status == "declined"`, and — when `result.details` is present and longer than `summary` — a footer `Full output: {len(details)} bytes (use /agents show for the dry-run command).` Import the contract dataclass lazily inside the function to keep `commands.py` import-cheap.
* `_confirm_project_agent_for_run(ctx, name) -> bool` — delegates to the existing `_confirm_project_agent` path in `cli/entry.py:1503` (extract it to a shared helper rather than duplicating the prompt copy).

### 5.6 Settings — `packages/aelix-ai/src/aelix_ai/settings/` — ✅ SHIPPED

An unknown key is **silently dropped** (`settings_manager.py:107`), so all five table edits are mandatory.

1. `types.py`, after `WarningSettings` (opens **133**), before `Settings` (opens **193**):
   ```python
   @dataclass
   class FeaturesSettings:
       """Aelix-original (ADR-0197, P2): per-feature kill switches. GLOBAL scope
       only — see ``SettingsManager.get_features_agents``."""
       agents: bool | None = None
   ```
2. `types.py`, `Settings` (**193**) — add `features: FeaturesSettings | None = None`. (Field count 41 → 42; update both count comments.)
3. `types.py`, `SETTINGS_PY_TO_JSON` (**289**) — add `"features": "features"`.
4. `types.py`, `NESTED_PY_TO_JSON` (**341**) — add `"FeaturesSettings": {"agents": "agents"}`.
5. `types.py`, `SETTINGS_NESTED_CLASSES` (**408**) — add `"features": FeaturesSettings`; `"FeaturesSettings"` into `__all__` (**420**).
6. `settings/__init__.py` — re-export `FeaturesSettings` (import block + `__all__`).
7. `settings_manager.py`, immediately after `set_default_project_trust` (ends **1030**): `get_features_agents()` / `set_features_agents(enabled)`, **both GLOBAL-scope-only**. The getter reads `self._global_settings.features`, never the merged `self._settings`, and returns `False` when either is `None` — same rule, same reason, as `get_default_project_trust` (`:1008`). No `migrate_settings` entry — that table holds rename transforms only.
   **Landmine:** `_save()` only ENQUEUES the write (`:689` `_enqueue_write` returns silently with no running loop). Any test or CLI path that sets and exits must `await manager.flush()`.
8. `tui/settings_rows.py` — three edits, each of which fails *quietly* if omitted:
   * `_BOOL_GETTERS` (**334**) `+ "features_agents": "get_features_agents"`;
   * `_BOOL_SETTERS` (**345**) `+ "features_agents": "set_features_agents"`;
   * a row in `build_settings_rows` (**93**) with `key="features_agents"`, `label="Agent delegation"`, `kind="bool"`, `help="Allow this agent to delegate work to a subagent. Persisted; applies next launch."`, **`live=False`**.

   **`live=False` is the correct value and is not an oversight.** In this codebase `SettingsRow.live` means *"the shell also mirrors the change onto the LIVE session this run"*. The flag is consumed exactly once, by `cli/entry.py::_build_harness_options`, when the harness is built — the `agent` tool and the delegation extension are either loaded for this process or they are not. `live=True` would promise a mid-session effect nothing delivers: **that** is the #84 inert-row failure. The row is nonetheless fully **dispatchable** — a bool row whose key is missing from `_BOOL_GETTERS` raises `KeyError` inside `_row_bool` on the first toggle, which `apply_setting` swallows into a red line (a silently dead row), and the setter half fails the same way one keystroke later. **No live-mirror branch in `shell.py::_open_settings`.**

### 5.7 `.github/workflows/ci.yml` — 1 hunk (closes I6) — ✅ SHIPPED

Under the `actions/checkout@08c6903…` step at **line 41**: `with: fetch-depth: 0`, with a comment explaining that the kernel-untouched gate resolves `git merge-base origin/main HEAD` and the action's default depth-1 clone makes that command error (so the test could only skip). The **primary** gate is the content grep, which needs no history at all.

---

## 6. The child: exact argv, env, cwd, stdio

### 6.1 Narrowing before rendering argv

```python
parent_grant = set(api.runtime.actions.get_active_tools())   # api.py:945; None→all already materialized
requested    = set(profile.tools) if profile.tools is not None else parent_grant
allowed      = (requested & parent_grant & set(ALL_TOOL_NAMES)) - {AGENT_TOOL_NAME}
dropped      = tuple(sorted(requested - allowed))
child_profile = dataclasses.replace(profile, tools=tuple(sorted(allowed)))

# §2(i): the CONSENT-RESOLVED mode, not the raw clamp output. `grant.mode` is
# child_permission_mode(...) unless a human raised it at the dialog, in which
# case it is at most AUTO_ACCEPT. A missing grant NEVER reaches this line —
# execute() fails closed before it.
child_mode = grant.mode

argv_flags = profile_to_argv(child_profile, prompt_path=tmp_path, oneshot=True, task=task)
argv = [
    sys.executable, "-m", "aelix_coding_agent",
    *argv_flags,
    "--permission-mode", child_mode.value,
    *child_trust_argv(cwd, parent_cwd),   # §2(g) — two-clause; may be EMPTY
    "--no-agents",                        # belt-and-braces with the depth env var
]
```
* The intersection is mandatory and structural — the child can never exceed the parent's grant.
* `∩ ALL_TOOL_NAMES` prevents naming an extension/MCP tool the child cannot build (the harness's F-9 validator would kill it at startup).
* `- {"agent"}` is a second, independent anti-nesting layer.
* `tools=()` → `agents/resolver.py:152-156` emits `--no-tools`. **Preserve that branch**: `--tools ''` parses to `[]`, which `_resolve_active_tools` reads as falsy → `None` → **every** tool active, the exact inversion. The resolver's own comment says so.
* `dropped` rides back on `SubagentResult.dropped_tools`; `child_mode.value` rides back on `SubagentResult.permission_mode`.
* **`child_trust_argv` is ONE function shared by the spawner and `/agents show`'s dry-run rendering** — the dry run must stay the truth.

### 6.2 argv rules

* **Never** `[sys.executable, "-m", "aelix", ...]`. `rpc_client.py:466` does exactly that and it is a **live bug** (`python -m aelix` is the umbrella meta-package demo). Do not copy `_build_argv`.
* **Never** the `aelix` console script — a MAIN-tree editable install.
* `profile_to_argv` (`agents/resolver.py:183-206`) already prepends `["--mode","json","-p","--no-session"]` and appends `f"Task: {task}"`. **The `"Task: "` prefix is load-bearing** — a bare task starting with `--` is swallowed into `unknown_flags` with no diagnostic, one starting with `-` produces `Unknown short flag`. Do not strip it.
* `--no-session` is a validated error with `--continue` (`entry.py:331`) and `--resume` (`:350`). Never emit either.

### 6.3 env

`env = dict(os.environ)` then:

| var | value | why |
|---|---|---|
| `AELIX_SUBAGENT_DEPTH` | `MAX_SUBAGENT_DEPTH` if `profile.role == "leaf"` else `subagent_depth() + 1` | the depth gate. **INERT at MAX=1** — both branches yield 1, and `role` defaults to `"leaf"` (`agents/profile.py:208`). Wired now so P3 only has to raise the cap. |
| `AELIX_STDIN_TIMEOUT` | `"1"` | an inherited `"0"` means **wait forever** (`entry.py:172-174`). Belt-and-braces with `stdin=DEVNULL`. |
| `AELIX_MCP_CONFIG` | **popped** | otherwise every child fans out its own MCP subprocesses. |
| `PYTHONPATH` | inherited, plus `Path(aelix_coding_agent.__file__).resolve().parents[1]` prepended when absent | closes the worktree/venv trap deterministically. |

Inherited deliberately (document, do not clear): `AELIX_CODING_AGENT_DIR`, `AELIX_SETTINGS_PATH`, `AELIX_AUTH_PATH`, `XDG_CONFIG_HOME`, `PI_OFFLINE`, and every provider API key.

**Test hermeticity (closes I10):** `tests/conftest.py:26-40`'s `_no_real_tool_downloads` is an in-process `monkeypatch.setattr` and **does not reach a child interpreter**. Any test spawning a real `-m aelix_coding_agent` must set the child env explicitly: `AELIX_CODING_AGENT_DIR`, `HOME`, `XDG_CONFIG_HOME` under `tmp_path`, plus `PI_OFFLINE=1`.

### 6.4 cwd, stdio, and parent-death (closes I1)

* **cwd:** the tool's `cwd` argument when supplied *and* `Path.resolve().is_relative_to(parent_cwd)`; otherwise the parent's cwd. An out-of-tree `cwd` is a tool error, not a silent fallback. **This rule and `child_trust_argv` compose**: this bounds WHERE a child may run, `child_trust_argv` bounds WHAT may execute once it gets there.
* **`stdin=asyncio.subprocess.DEVNULL` — MANDATORY.** An inherited stdin costs **+30 s per delegation** (`_read_piped_stdin`, `entry.py:169-207`) and any bytes that arrive are **prepended to the task message**.
* **`stdout=PIPE, stderr=PIPE, limit=8 * 1024 * 1024`** — the explicit `limit` is load-bearing (see §7).
* **`start_new_session=True`** — the default puts the child in the parent's process group, so Ctrl+C SIGINTs every subagent at once with no envelope, and neither parent (`shell.py:1323-1339`) nor child (`print_mode.py:114-131`) installs a SIGINT handler.
* **`preexec_fn` sets `PR_SET_PDEATHSIG(SIGTERM)` (closes I1).** On Linux, `ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)` inside the `preexec_fn`, guarded by `sys.platform == "linux"` and a bare `except Exception: pass`. It composes with `start_new_session=True` (setsid does not clear the pdeathsig; nor does the *exec* — only a UID change does). Rationale: `modes/print_mode.py:158-165`'s `_emit` only records `stdout_dead["v"]`; the acting `break` is at `:198-205` inside the **residual**-messages loop and the `raise BrokenPipeError` at `:208-211` — both strictly **after** `await runtime_host.harness.prompt(initial_message)` at `:189-193`. Since `agents/resolver.py:204-205` makes the whole task the initial prompt, a child whose parent is SIGKILLed otherwise runs every turn, every LLM call and every tool to completion, reparented to init, in its own session, with the 0700 tmpdir leaked.
  Also add a `parent-killed` case to `test_prompt_file_unlinked_on_every_path`.
  **REJECTED to P3:** a per-session stray-pid file plus a cross-run sweeper.

---

## 7. Reading the child (closes B2, B3)

`print_channel.py` is the only file that touches pipes. Two rules, both load-bearing.

### 7.1 Both pipes are pumped concurrently, for the child's whole lifetime

```python
proc = await asyncio.create_subprocess_exec(
    *argv, env=env, cwd=cwd,
    stdin=asyncio.subprocess.DEVNULL,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    limit=_STREAM_LIMIT,            # 8 MiB — see 7.2
    start_new_session=True,
    preexec_fn=_pdeathsig,
)
out_task = asyncio.create_task(_pump_stdout(proc.stdout, assembler, state, on_event))
err_task = asyncio.create_task(_pump_stderr(proc.stderr, err_ring))
await asyncio.gather(out_task, err_task)
```
* `err_ring` is a bounded `collections.deque` holding the **last 64 KiB** of stderr — the fallback rung only ever shows a tail, and a chatty child must not balloon parent memory.
* **Do not use `proc.communicate()`** — it drains both but buffers everything and defeats §9's streaming progress.

**Why this is blocking, not stylistic.** The draft's schedule ("drain stdout to EOF, then read stderr") was reproduced with a real child writing 1 MiB to stderr between two stdout lines: the probe hung for the full 2-minute tool timeout, and even `proc.kill(); await proc.wait()` after an 8 s `wait_for` did not unwedge it. In production the only escape would be §9.4's 600 s timeout — a ten-minute stall reported as a child failure. A real aelix child writes plenty to stderr: `modes/print_mode.py:229` prints every caught exception there, the SIGTERM path emits a multi-line traceback, and provider SDK/httpx logging plus any extension `print(..., file=sys.stderr)` land in the same pipe.

### 7.2 Lines are assembled from chunks, never from `readline()`

```python
_STREAM_LIMIT   = 8 * 1024 * 1024   # StreamReader buffer ceiling
_MAX_LINE_BYTES = 4 * 1024 * 1024   # per-line budget (drop, never raise)

async def _pump_stdout(reader, assembler, state, on_event) -> None:
    while True:
        chunk = await reader.read(65536)     # NEVER readline()
        if not chunk:
            break
        for line in assembler.feed(chunk):
            reduce_line(state, line)
            on_event(state)
    for line in assembler.flush():           # trailing partial line at EOF
        reduce_line(state, line)
```
`LineAssembler` lives in the **pure** `stream.py` (SHIPPED): `feed(bytes) -> Iterator[str]` splits on `\n`, holds the tail, and when the in-progress line exceeds `_MAX_LINE_BYTES` it **discards it and increments `dropped_lines`**, resyncing at the next newline. No exception, ever. It survives split multibyte code points and never raises on invalid UTF-8.

**Why this is blocking.** `asyncio.create_subprocess_exec`'s StreamReaders default to `limit=65536`, and `readline()` **raises** past it rather than truncating. Executed against a real child emitting a 200 000-byte line followed by a valid `{}` line:
```
limit= 65536
RAISED ValueError: Separator is not found, and chunk exceed the limit
second RAISED ValueError: Separator is found, but chunk is longer than limit
```
The oversize bytes stay in the buffer, so **every subsequent `readline()` raises the same error** — "increment `dropped_lines` and discard" is unimplementable on `readline()`, and the terminating `agent_end` is lost. 200 KB is routine, not exotic: a `message_end` carrying one `read` of `harness/core.py` (199 246 bytes) serializes to ~207 KB — and `message_end` is §8's **sole** source of the summary and usage.

**Test relocation (mandatory):** `test_oversize_line_dropped_not_buffered` does not belong in the pure reducer suite — feeding a 5 MiB `str` to `reduce_line()` passes trivially while the real I/O path raises. Its real form is `tests/agents_ext/test_print_channel_spawn.py::test_oversize_line_dropped_and_stream_recovers`.

---

## 8. Event-parsing rules (`stream.py` — pure) — ✅ SHIPPED

`--mode json` emits **raw kernel `AgentEvent` dataclasses via `dataclasses.asdict`** (`modes/print_mode.py:158-165` → `rpc/rpc_mode.py`) — **snake_case**, not pi's camelCase. A line-for-line port of pi's parser silently reads `None` for every field and every failure looks like a success with zero usage.

`def reduce_line(state: _StreamState, line: str) -> _StreamState`

1. `json.loads` each line; **silently skip unparseable lines** (an extension or MCP server in the child can `print()` straight into the stream — `print_mode.py:23-26` explicitly declines pi's `takeOverStdout`).
2. **Use `event.get("type")`, never `event["type"]`.** The first line is the session-metadata header `{"id":…, "created_at":…}` with **no `type`** (`print_mode.py:174-185`), emitted under best-effort `try/except` — rely on neither its presence nor its absence.
3. **Consume exactly five types:** `agent_start`, `turn_start`, `message_end`, `tool_execution_start`, `agent_end`.
4. **Ignore `message_update` entirely** — its `partial` repeats the whole assistant message every delta (measured 329× and 2773× stream-to-result ratios), and the outer `message.content` stays `[]` during streaming.
5. **Accumulate the summary from `message_end`; treat `agent_end` as a terminator only.** `agent_end` carries the entire message array on one line.
6. **Summary extraction:** the last `role == "assistant"` message that has a text part; concatenate its `content[]` blocks where `type == "text"`, skipping `type == "thinking"`. (pi returns only the *first* text part, `index.ts:170-180` — concatenating is a deliberate, better divergence.)
7. **Field map — do NOT use pi's names:** `stop_reason`, `error_message`, `cache_read`, `cache_write`, `total_tokens`; tool-call blocks use `tool_name` / `input`, not `name` / `arguments`. Discriminators `"text"` / `"toolCall"` and the `{type, message}` envelope DO match pi.
8. **Usage — dual-read (closes I8).** `AssistantMessage.usage` is `dict[str, Any]` (`aelix_ai/messages.py:126`), passed through `asdict` untransformed, so its keys are whatever an adapter wrote — **not** protected by kernel immutability. The kernel itself dual-reads (`session/compaction.py:1036-1043`). Route every read through one `_usage_field(usage, *names) -> int` helper. Sum `input`/`output`/`cache_read`/`cache_write`; `tokens` **overwrites** (a level, not a flow); `turns` counts assistant messages; `stop_reason` / `error_message` are last-write-wins; `usage` is `null` on errored messages.
9. **Cost.** `usage["cost"]["total"]` when present, else compute parent-side via `aelix_ai.models.calculate_cost` from the `provider` + `model` fields on the assistant message. (openrouter/openai-completions emit no `cost` key, so the fallback is the common path — record provenance.)
10. **`current_tool`** from `tool_execution_start.tool_name`; cleared by `turn_start` and `agent_end`.
11. **`tool_result_end` does not exist** in pi's live event table or in aelix. Do not add the branch.

---

## 9. Envelope, cap, timeout, reaper, progress

### 9.1 Failure detection — ✅ SHIPPED (`envelope.py`)
```python
failed = exit_code != 0 or stop_reason in ("error", "aborted") or outcome != "ok"
```
**Never trust the return code alone.** `print_mode.py`'s `stop_reason in ("error","aborted") → exit_code = 1` is guarded by `if mode == "text"`; a bogus-model JSON run **exits 0 with empty stderr** while the stream carries `stop_reason: "error"`.

### 9.2 The fallback chain (pi `index.ts:186-191`)
`error_message` → sanitized stderr tail → extracted summary → `"(no output)"`. The stderr rung is **mandatory**: a child with no API key exits 1 with **zero stdout bytes** (not even the header).

**Sanitize before the model sees it.** The SIGTERM path prints `Task exception was never retrieved` / `future: <Task finished … exception=SystemExit(143)>` — that is `print_mode.py`'s `_signal_cleanup_and_exit` calling `sys.exit(128+sig)` inside a coroutine, and it is **normal**. Drop lines matching `^(Task exception was never retrieved|future: <Task|Traceback \(most recent call last\)|\s+File "|\s+\w|SystemExit)` from the stderr rung when `outcome in ("aborted","timeout")`, and keep the **raw** text on `SubagentResult.details`.

### 9.3 The 50 KiB cap
Budget = `profile.output_cap` (default 51200; `DEFAULT_OUTPUT_CAP` duplicated as a module constant for spawns that die before resolution). Applied to the **extracted summary**, on **UTF-8 bytes**, trimming one code point at a time, then appending:
```
\n\n[Output truncated: {N} bytes omitted. Full output preserved in tool details.]
```
`truncated = True`, and **`details` carries the uncapped text** so the marker is true on both doors (B8). A zero or negative cap disables truncation. This is a deliberate fix of a pi gap: pi's `truncateParallelOutput` has one call site (`index.ts:649`, parallel only); single mode returns uncapped.

### 9.4 Timeout
`timeout_ms` precedence: tool argument → `profile.timeout_ms` → **600 000**. pi has no timeout of any kind, so this is aelix-original. On expiry the reaper runs and the envelope is `status="timeout", ok=False` **carrying the partial summary and partial usage** — never an exception.

**The consent dialog is OUTSIDE the timeout.** A human thinking at the modal must not burn the child's budget; the clock starts when the process does.

### 9.5 The reaper (closes B1, I2) — **REMAINING (WS-D2)**

```python
# aelix_agents/reaper.py
async def reap(proc, *, grace: float = 5.0, eager_kill: bool = False) -> int:
    """SIGTERM → grace → SIGKILL, safe to be cancelled REPEATEDLY.

    Cancellation safety (P2 review finding B1, anchors corrected per OC-9).
    ``tui/chrome.py::_interrupt`` (``:854``) calls ``on_interrupt`` on EVERY
    Ctrl+C while a turn is running — the running branch at ``:863-866`` — with
    NO debounce. ``_CTRL_C_EXIT_WINDOW`` (the constant at ``:159``) is read only
    at ``:875``, inside the idle-empty-buffer branch at ``:872-882``. Esc is
    bound to the same callback at ``:883-890`` under
    ``Condition(lambda: self._running)``. N presses = N ``cancel()`` calls on the
    same turn task. A plain coroutine reaper takes CancelledError at its
    ``wait_for`` and NEVER reaches the SIGKILL leg, leaving a session leader
    (``start_new_session=True``) that no terminal signal and no parent-side
    timeout can ever reach again.

    Two defenses, both required:
      * ``except BaseException`` — CancelledError escalates like a timeout.
      * the caller runs this as a DETACHED task and awaits it under
        ``asyncio.shield``, so cancelling the awaiter never cancels the kill.
    """
    if proc.returncode is not None:
        return proc.returncode
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    if not eager_kill:
        try:
            return await asyncio.wait_for(asyncio.shield(_wait(proc)), grace)
        except BaseException:          # TimeoutError AND CancelledError
            pass
    _kill_tree(proc)
    return await asyncio.shield(_wait(proc))   # MUST await, or a zombie leaks
```
and at the call site in `PrintChannel.run`:
```python
        reaper_task = asyncio.ensure_future(reap(proc, grace=self._grace))
        child.reaper_task = reaper_task           # registry-owned; stop_all joins it
        try:
            rc = await asyncio.shield(reaper_task)
        except asyncio.CancelledError:
            # 2nd+ Ctrl+C — escalate NOW, do not wait out the grace.
            child.eager_kill = True
            _kill_tree(proc)
            raise
```

**`_kill_tree` (closes I2).** The draft's rationale — *"`os.killpg`, not `proc.kill()`: the child's own `bash` tool children must die too"* — is **factually wrong** and must be corrected in both the code comment and ADR-0197. Verified: `tools/bash.py:163` uses `subprocess.Popen(..., start_new_session=True)` and `tools/_subprocess.py:78` uses `create_subprocess_exec(..., start_new_session=True)`, so grandchildren are leaders of their **own** groups and `killpg` cannot reach them. Walk `/proc/*/stat` PPid instead, deepest-first. On the COOPERATIVE SIGTERM leg this is redundant — the child's `_signal_cleanup_and_exit` → `dispose()` → `abort()` → `bash.py:210-215` `_kill_group` already reaps them; the escalation leg exists precisely for the child that does NOT cooperate.

pid-recycling hazard: the ThreadedChildWatcher may already have waitpid'd the child, so `os.getpgid(proc.pid)` can resolve a RECYCLED pid. `contextlib.suppress(ProcessLookupError)` does not protect against that — hence the descendant walk is snapshotted BEFORE `terminate()` and the child pid is signalled via `proc.kill()` (which the event loop guards), never via a re-resolved pgid.

**REJECTED to P3:** cgroup or `pidfd_open` subtree containment.

**Do not port pi's escalation literally.** Node's `subprocess.killed` means "a signal was sent", so `if (!proc.killed) proc.kill("SIGKILL")` (`index.ts:403-407`) is false 5 s later in every case and SIGKILL is never sent. The Python predicate must be *liveness*.

**Drain stdout to EOF before reaping on the NORMAL path.** Closing the read end early gives the child `BrokenPipeError` → exit 141, a parent-side bug masquerading as a child failure. On the kill paths, accept any exit code.

**Abort returns, never raises.** pi throws and discards every streamed partial (`index.ts:413`). aelix returns `SubagentResult(status="aborted", ok=False, summary=<partial>, usage=<partial>, details=<raw>)`.

### 9.6 Progress + statusline — **REMAINING (WS-D3)**

* Emit `SubagentProgress` on `api.events` at `SUBAGENT_START` / `SUBAGENT_TOOL` / `SUBAGENT_END`.
  **EventBus caveats, re-verified at `extensions/api.py:271-279`:** handlers run **synchronously**, the return value is discarded (an `async def` subscriber's body never runs), and every handler exception is swallowed by `contextlib.suppress(Exception)` with **no logging**. A broken subscriber produces zero rows and zero diagnostics.
* Statusline: `runtime.ui.set_status(f"subagent:{id}", text)`.
  * Read `runtime.ui` **live per call** (OC-7) — rebound on `/new` / `/fork` / `/resume` (`shell.py:1565`) and reverted to headless on TUI exit (`shell.py:1950`).
  * Guard with `runtime.ui is not HEADLESS_UI_CONTEXT`; in print/json/rpc `bind_ui` is never called and every `ctx.ui.*` raises `NotImplementedError`.
  * `set_status` renders into **one height-1 row** (`chrome.py:1036-1047`). One segment per child. Multi-row panels are `set_widget` — P4.
  * Clear with `set_status(key, None)` in the `finally`.
* Also drive `ctx.on_partial` so the parent's own tool card streams. The bus and the partial are not alternatives; both ride the same reduce step.

### 9.7 The `agent` tool — **REMAINING (WS-D3)**

```python
AGENT_TOOL_NAME = "agent"

agent_tool = AgentTool(
    name=AGENT_TOOL_NAME,
    description=<roster-injected>,
    parameters={
        "type": "object",
        "properties": {
            "profile": {"type": "string", "description": "Agent profile name (see the roster below)."},
            "task":    {"type": "string", "description": "The complete task. The child has NO conversation history."},
            "cwd":     {"type": "string", "description": "Optional working directory; must be inside the parent's cwd."},
            "timeout_ms": {"type": "integer", "minimum": 1000},
        },
        "required": ["profile", "task"],
    },
    execution_mode="sequential",   # SECURITY, not performance — see below
)
```

* **`execution_mode="sequential"` is a SECURITY setting** (kernel `types.py:47-56`; `loop.py:683-693`), mirroring `tools/bash.py:574`. The kernel's `_execute_tool_calls` downgrades the **WHOLE batch** to sequential when any tool in it declares this, which closes the concurrent-modal hazard at **zero product-core cost**. A test must pin that `dataclasses.replace` (used to re-inject the roster) **preserves** it — losing it silently reopens the hazard.
* **`background=True` is NOT accepted in P2.** The tool rejects it; `spawn`'s `background` parameter exists on the Protocol for P3 shape stability only.
* **Always `resolve_profile(name, allow_project=False)`** — fail-closed, no prompt (B5).
* **The fail-closed grant lookup:**
  ```python
  async def execute(self, args, ctx):
      grant = self._grants.pop(ctx.tool_call_id, None)
      if grant is None or not grant.consented:
          # The hook is the ONLY place consent is taken. A call that reached
          # here without one skipped the gate; refuse rather than spawn.
          return ToolResult(is_error=True, content="Delegation was not approved.")
      ...
      result = await self._runtime.spawn_granted(grant, resolved, task, ...)
  ```
* **The description injects the profile roster** (name + description + scope, each description truncated to 160 chars, total ≤ 4 KiB, `MAX_ROSTER = 24`). pi does not do this (`agents.ts` still exports `formatAgentList` but `index.ts` never imports it), so pi's parent model discovers names by guessing. Do not copy that affordance.
* **Staleness:** `Tool` is `@dataclass(frozen=True)` and the description is fixed at `register_tool` time. Re-register from a **`before_agent_start`** handler (`harness/core.py:1242`, before the per-turn `AgentContext` build at `:4117-4133`) via `api.register_tool(dataclasses.replace(agent_tool, description=roster))`. A `turn_start` handler is too late for the current turn.
* **Landmine:** `register_tool` → `refresh_tools` → `_refresh_extension_tools` (`core.py:847-906`) **materializes `active_tool_names` from the `None` sentinel into a concrete list**. Assert the interaction in a test rather than discovering it.

---

## 10. Test plan

All tests live at repo root `/workspaces/aelix-p2/tests/` — `packages/aelix-coding-agent/tests/` does not exist. ✅ = shipped and green.

### `tests/agents/test_subagent_contract.py` (WS-A) ✅ 18 tests
`test_contract_imports_nothing_from_product_core` (AST-parse; no runtime import naming `aelix_coding_agent` outside `if TYPE_CHECKING:`) · `test_runtime_checkable_protocol_accepts_minimal_impl` · **`test_protocol_has_no_consent_parameter`** (§2i — the grant type must never reach product-core) · `test_bind_subagents_default_none` · `test_bind_subagents_roundtrip` · `test_bind_subagents_refuses_version_below_floor` (`code == "contract_mismatch"`) · `test_bind_subagents_refuses_version_above_ceiling` · `test_bind_accepts_older_supported_version` (**the B9 compat-window pin**) · `test_second_bind_is_refused_without_replace` (**B7**) · `test_teardown_does_not_unbind_a_foreign_runtime` (**B7**) · `test_bind_subagents_refused_at_max_depth` (**I4**) · `test_subagent_depth_parsing` · `test_subagent_depth_unset_is_zero` · `test_dataclasses_are_frozen` · `test_additive_field_does_not_break_a_v1_runtime` · `test_result_has_details_and_dropped_lines` (**B8**) · **`test_result_has_permission_mode`** (§2e/§2i) · `test_version_range_is_coherent`

### `tests/agents/test_p2_band_boundaries.py` (WS-A — the architecture gate) ✅ 4 tests
`test_kernel_has_no_subagent_surface` (grep `packages/aelix-agent-core/src/**/*.py` for `subagent`, `SubagentRuntime`, `bind_subagents`, `aelix_agents`, `AELIX_SUBAGENT` → **0 hits**; works in any checkout and stays meaningful after merge) · `test_kernel_untouched_vs_merge_base` (`git merge-base origin/main HEAD`; `pytest.skip` with an explicit reason when the ref is unavailable — **replaces the draft's `main...HEAD`**) · `test_product_core_never_spawns` (AST-walk `aelix_coding_agent/**` for `create_subprocess_exec` / `subprocess.Popen` / `os.fork`; allowlist `rpc/rpc_client.py`, `util/tools_manager.py`, `extensions/subprocess_hooks.py`, `tools/**`; assert **no new** site) · **`test_product_core_never_prompts_for_spawn_consent`** (§2i — no `ui.select` / consent copy anywhere in product-core)

### `tests/cli/test_p2_import_direction.py` (**WS-E**, moved from WS-A per I9) — REMAINING
`test_product_core_imports_aelix_agents_at_one_site_only` (grep `aelix_agents` across `aelix_coding_agent/**` → exactly one hit, in `cli/entry.py`, inside a function body) · `test_aelix_agents_may_import_product_core`

### `tests/settings_manager/test_features_agents_flag.py` (WS-B) ✅ 11 tests
`test_default_is_false` · `test_default_is_false_when_features_block_exists_but_key_does_not` · `test_roundtrip_through_disk` (with `await flush()`; on-disk JSON `{"features":{"agents":true}}`) · `test_disable_roundtrips_too` · `test_set_preserves_sibling_global_keys` · **`test_project_settings_cannot_enable`** (the security test) · `test_project_settings_cannot_disable_a_global_enable` · `test_unknown_key_is_not_dropped` (guards the 5-table registration) · `test_features_survives_a_full_json_round_trip` · `test_settings_row_wired` (row exists, `kind == "bool"`, label + help non-empty, key in **both** `_BOOL_GETTERS` and `_BOOL_SETTERS`, `row.read(manager) == "off"`) · `test_settings_row_toggle_persists` (drives `apply_setting` the way `shell.py::_open_settings` does; asserts `result.live is None` — persist-only by design — and that the value persisted)

### `tests/builtin/test_permission_child_floor.py` (WS-C) ✅ 10 tests
`test_headless_default_is_allow` · `test_headless_default_allow_is_unchanged` (**regression guard for every existing `-p` user**) · `test_headless_default_block_blocks_write_and_bash` · `test_headless_block_still_allows_read_only` · `test_headless_block_applies_under_yolo` · `test_plan_mode_blocks_above_headless_branch` · **`test_auto_accept_write_bypasses_headless_floor`** (documents B4's mechanism: `headless_default="block"` + AUTO_ACCEPT + in-cwd write → **ALLOW**, because the AUTO_ACCEPT branch returns above the headless one; this test exists so nobody ever again mistakes the floor for the guarantee) · `test_auto_accept_out_of_cwd_write_still_hits_the_floor` · **`test_auto_accept_child_cannot_write_dot_aelix`** (OC-5) · `test_guardrail_still_first`

### `tests/builtin/test_sensitive_aelix_dir.py` (WS-C — NEW, OC-5) ✅ 6 tests
`test_aelix_paths_are_not_auto_allowable` (pins `_is_auto_allowable_write('.aelix/agents/x.md', cwd) is False`, plus `extensions/`, `mcp.json`, `settings.json`) · `test_non_aelix_in_cwd_still_auto_allowable` (**blast-radius guard**) · `test_existing_sensitive_components_unchanged` · `test_aelix_write_prompts_under_auto_accept` · `test_ordinary_write_under_auto_accept_is_still_silent` · **`test_agent_is_not_in_mutating`** (pins §2i's "do not add `agent` to `_MUTATING`")

### `tests/cli/test_permission_mode_flag.py` (WS-C) ✅ 13 tests
`test_every_permission_mode_parses` · `test_valid_values_are_derived_from_the_enum` · `test_absent_flag_leaves_permission_mode_none` · `test_invalid_value_warns_and_drops` · `test_invalid_value_is_not_captured_as_an_unknown_flag` · `test_trailing_flag_without_a_value_is_inert` · `test_last_permission_mode_wins` · `test_permission_mode_help_line_present` · `test_agents_override_tristate` · `test_no_agents_wins_when_it_comes_last` · `test_agents_flags_are_not_unknown_flags` · `test_agents_help_line_present` · **`test_child_argv_parses_clean`** + **`test_a_typo_would_be_swallowed_silently`** (the B10 anti-typo gate — `args.py` swallows unknown `--` flags with no diagnostic, so without these a renamed flag ships an auto-approving child with a green suite)

### `tests/agents_ext/test_posture_clamp.py` (WS-D1, pure — closes B4/OC-6/OC-8) ✅ 14 tests
`test_full_matrix_snapshot` (**pins ALL 30 cells**) · `test_approval_mode_deny_maps_to_plan` · `test_approval_mode_inherit_never_widens` (parametrized over all 5 parents; `rank(child) <= rank(parent)`) · `test_no_approval_mode_ever_widens` · `test_approval_mode_auto_never_widens` (**the B4 pin** — DEFAULT parent → `PLAN`, not `AUTO_ACCEPT`) · **`test_project_scope_ban_is_rank_min_not_assignment`** · **`test_project_scope_auto_under_yolo_parent_is_auto_accept`** (**the OC-6 discriminating case:** `child_permission_mode('auto', YOLO, 'project') is AUTO_ACCEPT`) · `test_project_scope_profile_cannot_widen_posture` · `test_clamped_default_is_tightened_to_plan` · **`test_ask_without_ui_is_plan`** + **`test_ask_with_ui_matches_inherit_baseline`** (OC-8 — these two REPLACE the old `test_approval_mode_ask_refuses_to_spawn`) · `test_unknown_approval_mode_falls_back_to_inherit` · `test_posture_rank_is_a_total_order_over_every_mode` · `test_explicit_scope_is_not_treated_as_project`

### `tests/agents_ext/test_spawn_consent.py` (WS-D1/D3 — NEW, §2i) ✅ 23 tests
Pure, with a fake ctx and a scripted `ui.select` spy.
`test_no_ui_returns_clamp_without_prompting` · **`test_headless_ask_downgrades_to_plan_and_never_refuses`** (OC-8) · **`test_has_ui_is_read_live_not_cached`** (OC-7) · `test_dialog_shows_source_path_and_scope` · `test_task_text_is_truncated_to_300_chars` · `test_task_newlines_are_collapsed` · `test_esc_declines` · `test_cancel_option_declines` · **`test_a_raising_dialog_declines_and_never_allows`** · **`test_a_nonsense_answer_declines`** · **`test_cancellation_propagates_rather_than_becoming_a_decision`** · **`test_project_scope_never_offers_widening`** (OC-3 constraint 1) · **`test_widening_ceiling_is_auto_accept_edits`** (constraint 3) · `test_widening_not_offered_when_it_would_be_a_noop` (constraint 4) · `test_widening_not_offered_above_the_ceiling` · `test_widening_grant_records_widened_true` · `test_read_only_choice_uses_the_clamp` · `test_build_options_shape` · **`test_a_writable_clamp_is_never_labelled_read_only`** · **`test_grant_is_not_persisted_between_spawns`** (P2 ASKS EVERY TIME — the no-memo pin) · `test_a_decline_does_not_poison_the_next_spawn` · **`test_concurrent_requests_are_serialized`** · `test_grant_carries_the_identity_it_approved` · `test_grant_is_frozen`

**Still to add in WS-D3** (the wiring half, `tests/agents_ext/test_consent_wiring.py`): each dialog answer → the right grant on the extension's dict · `has_ui=False` + a write-capable want → grant PLAN and the hook returns `None` (**never blocks**) · want `== PLAN` → `ui.select` is never called · `execute()` with an unknown `tool_call_id` → `is_error=True` **and no process created** · two concurrent hook invocations serialize · the grant dict is cleared on `session_shutdown` · `dataclasses.replace` on the tool preserves `execution_mode="sequential"`.

### `tests/agents_ext/test_child_trust_argv.py` (WS-D1 — NEW, §2g/OC-4) ✅ 16 tests
`test_same_cwd_no_trust_resources_emits_nothing` · **`test_same_cwd_with_skills_only_still_emits_nothing`** (the `inherit_skills` regression clause 1 preserves) · `test_same_cwd_with_an_empty_aelix_dir_emits_nothing` · `test_same_cwd_with_an_empty_extensions_dir_emits_nothing` · `test_same_cwd_with_project_extensions_emits_no_approve` · `test_same_cwd_with_project_agents_emits_no_approve` · `test_same_cwd_with_project_mcp_emits_no_approve` · `test_different_cwd_always_emits_no_approve` · **`test_the_monorepo_vendor_shape_is_denied`** (the §4.2 measured escalation) · `test_parent_subdirectory_of_child_is_still_different` · `test_symlinked_same_cwd_is_treated_as_same` · `test_relative_and_absolute_forms_of_the_same_dir_agree` · `test_nonexistent_child_cwd_emits_no_approve` · **`test_a_raising_predicate_fails_closed`** · **`test_a_raising_resolve_fails_closed`** · `test_return_value_is_a_fresh_list`

> **This file REPLACES `test_spawn_argv_always_carries_no_approve`** in `tests/agents_ext/test_tool_and_security.py`. It is joined in WS-D2 by one **real-child subprocess** test asserting the nested-cwd escalation is blocked, with the explicit child env per §6.3 (`AELIX_CODING_AGENT_DIR`, `HOME`, `XDG_CONFIG_HOME` under `tmp_path`, `PI_OFFLINE=1` — `tests/conftest.py:26-40` is an in-process monkeypatch and does not reach a child).

### `tests/agents_ext/test_stream_reduce.py` (WS-D1, pure) ✅ 34 tests
Header/parse: `test_typeless_header_line_does_not_raise` · `test_absent_header_is_fine` · `test_unparseable_line_skipped` · `test_message_update_ignored` · `test_non_assistant_message_end_is_not_a_turn`.
Summary: `test_summary_is_last_textbearing_assistant` · `test_summary_survives_a_trailing_toolcall_only_turn` · `test_summary_concatenates_multiple_text_blocks` · `test_thinking_blocks_excluded` · `test_malformed_content_blocks_are_skipped`.
Wire shape: **`test_snake_case_fields_read`** (a camelCase fixture populates **nothing** — the pi-port trap) · `test_stop_reason_and_error_message_last_write_wins` · `test_current_tool_from_tool_execution_start` · `test_turn_start_and_agent_end_clear_current_tool` · `test_agent_end_is_a_terminator_only` · `test_provenance_recorded_for_the_cost_fallback`.
Usage (I8): `test_usage_sums_with_missing_keys` · `test_usage_null_on_errored_message` · **`test_usage_dual_spelling_read`** · `test_usage_zero_in_one_spelling_falls_through_to_the_other` · `test_usage_booleans_are_not_token_counts` · `test_tokens_overwrites_not_sums` · `test_cost_read_from_nested_total_and_summed`.
`LineAssembler` (B3): `test_line_assembler_splits_across_chunk_boundaries` · `test_line_assembler_handles_a_200kb_line` · `test_line_assembler_drops_oversize_and_resyncs` · `test_line_assembler_drops_a_whole_oversize_line_inside_one_chunk` · `test_line_assembler_counts_one_drop_per_line_not_per_chunk` · `test_trailing_partial_line_flushed_at_eof` · `test_flush_discards_an_oversize_partial` · `test_line_assembler_survives_split_multibyte_codepoints` · `test_line_assembler_never_raises_on_invalid_utf8` · `test_empty_feed_is_a_noop` · `test_max_line_bytes_default_is_the_documented_budget`.

> `test_oversize_line_dropped_not_buffered` is **NOT in this file** — feeding a 5 MiB `str` to `reduce_line()` passes trivially while the real I/O path raises. Its real form lives in the subprocess suite.

### `tests/agents_ext/test_envelope.py` (WS-D1, pure) ✅ 29 tests
`test_exit_zero_with_error_stop_reason_is_failure` · `test_nonzero_exit_is_a_failure_even_with_a_clean_stream` · `test_happy_path_is_ok` · `test_aborted_stop_reason_reports_aborted_not_error` · `test_caller_outcome_is_never_loosened` · `test_timeout_carries_the_partial_summary_and_usage` · `test_fallback_chain_order` · `test_empty_stream_uses_stderr` · `test_successful_run_keeps_its_summary_despite_stderr_noise` · `test_no_output_sentinel` · `test_stderr_only_success_still_surfaces_something` · `test_sigterm_traceback_stripped_from_stderr_rung` (stripped from `summary`, **present in `details`** — B8) · `test_abort_falls_through_to_the_partial_summary` · `test_real_errors_are_not_stripped_on_a_crash` · `test_sanitize_stderr_is_a_no_op_off_the_kill_paths` · `test_under_cap_not_marked` · `test_cap_on_utf8_bytes_not_chars` · `test_cap_uses_profile_output_cap` · `test_zero_or_negative_cap_disables_truncation` · `test_details_is_uncapped_when_summary_truncated` (**B8**) · `test_details_carries_raw_stderr_on_failure_only` · `test_details_is_none_when_there_is_nothing_behind_the_summary` · **`test_permission_mode_recorded_on_every_envelope`** (§2e/§2i) · **`test_declined_envelope_shape`** + **`test_declined_envelope_accepts_a_custom_reason`** (§2i) · `test_usage_is_carried_across_verbatim` · `test_dropped_lines_and_dropped_tools_ride_back` · `test_explicit_error_overrides_the_stream` · `test_stop_reason_and_elapsed_ride_back` · `test_result_is_frozen`

> `test_dropped_tools_recorded` lives in `tests/agents_ext/test_tool_and_security.py` (I9) — the narrowing lives in `tool.py`/`print_channel.py` and needs `api.runtime.actions.get_active_tools()`, both illegal in a pure module.

### `tests/agents_ext/test_print_channel_spawn.py` (WS-D2, real subprocess, hermetic) — REMAINING
Reuse the `_STUB_SERVER` idiom from `tests/rpc/test_rpc_client_lifecycle.py:1-80`. **Set the child env explicitly** per §6.3 (I10).

| test | asserts |
|---|---|
| `test_stdin_is_devnull` | a stub blocking on `sys.stdin.read()` still completes (pins the +30 s landmine) |
| `test_child_is_in_its_own_process_group` | stub echoes `os.getpgid(0)`; `!= parent` |
| `test_happy_path_envelope` | ok, summary, usage sums, turns |
| `test_startup_death_zero_stdout` | stub exits 1 with stderr only → `status="error"`, summary from stderr |
| `test_spawn_failure_is_a_result_not_a_raise` | nonexistent interpreter → `SubagentResult(status="error")` |
| `test_timeout_kills_and_returns_partial` | two messages then `sleep 60`, `timeout_ms=1500` → `status="timeout"`, partial summary, elapsed ≈ 1.5 s |
| `test_sigterm_then_sigkill` | `signal.signal(SIGTERM, SIG_IGN)` stub, `grace=0.5` → SIGKILL leg |
| **`test_double_cancellation_still_kills`** | **B1.** SIG_IGN stub, `grace=5.0`; cancel the owning task **twice ~0.4 s apart**; `/proc/<pid>` gone within 1 s and the reaper task completed |
| **`test_large_stderr_does_not_deadlock`** | **B2.** 1 MiB to stderr between two stdout events → envelope returns in < 5 s with `status="ok"` |
| **`test_oversize_line_dropped_and_stream_recovers`** | **B3.** 300 KB line then a valid `agent_end` → `agent_end` parses, `dropped_lines == 1`, no exception |
| **`test_bash_grandchild_killed_on_sigkill_leg`** | **I2.** stub spawns a grandchild with `start_new_session=True` → both dead after the kill leg |
| **`test_child_dies_with_parent`** | **I1.** spawn under a helper parent, SIGKILL the helper, child gone within 2 s (PDEATHSIG). `skipif(sys.platform != "linux")` |
| **`test_nested_project_extension_not_executed_in_relocated_child`** | **§2g clause 2** — the monorepo/vendor shape, real child |
| **`test_same_cwd_child_still_loads_project_skills`** | **§2g clause 1** — the `inherit_skills` recovery, real child |
| `test_stop_by_id_kills_child` / `test_stop_all_kills_every_child` | `status="aborted"` with partial summary; registry emptied |
| `test_prompt_file_unlinked_on_every_path` | parametrized over `{ok, error, timeout, killed, spawn-failure, **parent-killed**, **declined**}` |
| `test_prompt_file_mode_is_0600` | file `0o600`, dir `0o700` |
| `test_env_depth_incremented` / `test_env_mcp_config_cleared` / `test_pythonpath_propagated` | as drafted |
| **`test_orchestrator_and_leaf_produce_different_depth`** | **REPLACES `test_env_leaf_role_pins_depth_to_max` (I3).** Monkeypatch `MAX_SUBAGENT_DEPTH = 2`; leaf → `"2"`, orchestrator → `"1"` |

> `test_child_project_trust_is_denied` (the draft's B6 test) is **REPLACED** by the two real-child trust tests above.

### `tests/agents_ext/test_tool_and_security.py` (WS-D3) — REMAINING
`test_tools_intersected_with_parent_grant` · `test_empty_intersection_emits_no_tools` (**never** `--tools ''`) · `test_agent_tool_never_in_child_grant` · `test_dropped_tools_recorded` (moved here) · `test_depth_guard_in_tool` · `test_parallel_and_chain_modes_rejected` · **`test_background_true_is_rejected_in_p2`** · `test_cwd_outside_parent_rejected` · `test_roster_injected_and_capped` · `test_unknown_profile_lists_roster` · **`test_agent_tool_refuses_project_scoped_profile_without_confirmation`** (B5 — `is_error=True`, no process created) · **`test_the_identity_gate_is_a_parameter_not_a_mode`** (B5 — renamed in review: the `--approve` door in §2(f) was deliberately NOT implemented; `/agents run` starts `allow_project=False` unconditionally, which is stricter) · **`test_approval_mode_ask_opens_the_consent_dialog`** (OC-8 — **REPLACES `test_approval_mode_ask_refuses_to_spawn`**) · **`test_spawn_argv_follows_the_two_clause_rule`** (OC-4 — **REPLACES `test_spawn_argv_always_carries_no_approve`**) · **`test_execution_mode_survives_dataclasses_replace`**

### `tests/agents_ext/test_child_argv_contract.py` (WS-D2 — closes B10) — REMAINING
`test_child_argv_parses_clean` (feed the **exact** argv the spawner builds through `parse_args`; `parsed.unknown_flags == {}` and no `{"type": "error"}` diagnostics) · `test_child_argv_realizes_the_intended_posture` (`parse_args(argv).permission_mode == expected.value`, parametrized over the clamp table **and over a widened grant**) · `test_profile_to_argv_matches_spawner_argv` (the spawner's argv equals `[sys.executable, "-m", "aelix_coding_agent", *profile_to_argv(...), "--permission-mode", mode, *child_trust_argv(...), "--no-agents"]` — so `/agents show`'s dry run stays the truth)

### `tests/agents_ext/test_child_realized_posture.py` (WS-D2, real child — closes B10) — REMAINING
`test_child_process_actually_refuses_a_write_under_inherit_default` — a real `-m aelix_coding_agent --mode json -p --no-session --permission-mode plan --tools write` child with a stub provider; assert the write is **blocked**, not merely that the flag appeared in a list. The only test that observes the child's realized authority.

### `tests/agents_ext/test_reduce_consumes_real_print_mode_output.py` (WS-D3 — closes I7) — REMAINING
`test_reduce_line_over_real_emitter_output` — drive `run_print_mode(runtime, mode="json", …)` **in-process** with the existing `_ok_stream` mock `stream_fn` under `capsys`, reusing `tests/cli/test_print_mode.py`'s `_new_harness` / `_new_runtime` helpers verbatim; feed `captured.out` line-by-line through `reduce_line`; assert `summary` non-empty, `turns >= 1`, `usage.input > 0`, `stop_reason` populated. Without this the reducer's field map and the emitter's shape are two agreeing fictions.

### `tests/agents_ext/test_events_and_statusline.py` (WS-D3) — REMAINING
`test_progress_events_emitted_in_order` · `test_payload_is_the_contract_dataclass` · `test_headless_never_calls_set_status` · `test_statusline_row_set_and_cleared` · `test_broken_subscriber_does_not_break_spawn` (documents the silent swallow at `api.py:277-278`)

### `tests/cli/test_agents_extension_gate.py` (WS-E) — REMAINING
`test_not_prepended_when_flag_off` · `test_prepended_when_flag_on` · `test_no_agents_flag_overrides_settings_on` · `test_agents_flag_overrides_settings_off` · `test_depth_gate_suppresses_in_child` · `test_prepend_order_guardrail_permission_agents` (the invariant at `entry.py:860-867`; our consent hook must run **last**) · `test_child_process_has_no_agent_tool` (real child, explicit env per I10)

### `tests/tui/test_agents_run_command.py` (WS-E) — REMAINING
`test_run_without_runtime_degrades` · `test_run_dispatches_to_bound_runtime` · `test_run_requires_name_and_task` · `test_run_renders_result_panel` · **`test_run_renders_declined_result`** (§2i) · `test_builtin_still_shadows_extension` (pins `shell.py:2452-2470`) · `test_existing_subcommands_unchanged` · **`test_run_project_scoped_profile_prompts`** (B5)

### `tests/cli/test_print_mode_json_contract.py` (WS-D — the wire contract) — REMAINING
The JSON-mode shape is currently pinned by **nothing** (`tests/cli/test_print_mode.py:224` asserts only that each line parses as JSON). This file pins: the first line has no `type`; the sequence contains `agent_start`/`turn_start`/`message_start`/`message_end`/`turn_end`/`agent_end` in order; `agent_end` is last; `message_end.message` carries `stop_reason` / `error_message` / `usage` / `model` / `provider` / `role` in **snake_case**; discriminators are `"text"` / `"toolCall"`; tool-call blocks carry `tool_name` / `input`. **Scope note:** it does **not** pin `usage`'s inner keys — `messages.py:126` types them `dict[str, Any]` and adapters own the spelling (I8).

---

## 11. ADR plan

| ADR | Action | Content | State |
|---|---|---|---|
| **ADR-0197 — "subagent-runtime seam & the bundled `aelix-agents` extension"** | **NEW** `docs/decisions/0197-subagent-runtime-seam-and-aelix-agents.md` | Sections (a)–(m). See the rewritten §(e), §(g) and the new §(i) below. | ✅ SHIPPED (913 lines) |
| **ADR-0197 §(e)** — the child permission floor | **REWRITTEN** | The live auto-approve proof; `--permission-mode`; `headless_default` and the explicit correction that it is **insufficient alone** because the AUTO_ACCEPT write branch returns above it; the total-order CLAMP; **the project-scope ban as a rank-MIN with the executed inversion proof (OC-6)**; **the corrected `ctx.has_ui` mechanism and its TEMPORAL nature (OC-7)**; **`ask` prompts rather than refusing, headless downgrades to PLAN (OC-8)**; the plain statement that a delegated child is **read-only by default**; and **spec §10's invariant in its longer, true form** (never the short version alone). | ✅ |
| **ADR-0197 §(g)** — child project trust | **REWRITTEN** | The measured 7-step ladder; the deleted false "child treats cwd as UNTRUSTED" claim; **the two-clause `child_trust_argv` rule (OC-4)** with the ONE measured escalation (model-chosen nested cwd + `ProjectTrustStore` ancestor walk, `project_trust.py:373-395` / `:60-61`) as its justification — explicitly **not** "the child re-runs project code", which the parent already ran; the recovered `inherit_skills: true` regression; the four accepted residual costs; the wider variant and its named TOCTOU → P3. | ✅ |
| **ADR-0197 §(i)** — spawn-time consent | **NEW** | The empty-gate proof (`"agent" not in _MUTATING`) and why adding it is not the fix (args-blind `_rule_key`); the hook-not-`execute()` derivation with all three anchored reasons; the grant/`tool_call_id` transport and the **fail-closed** anti-bypass invariant; **bounded widening and why read-only-only was rejected**; the B4-preserved argument. **It must state plainly:** (1) the dialog may raise at most to `AUTO_ACCEPT` and only for **user-scope** profiles; (2) spec §10's invariant restated as *"a child never gains a mode looser than the parent's WITHOUT a per-spawn human grant"*; (3) **the ACCEPTED RESIDUAL: consent is per-delegation, not per-tool — an approved child writes unattended for its whole run, and per-tool visibility is the explicit P3 item**; (4) the task string in the dialog is model-authored, mitigated by always displaying `resolved.source_path` and truncating to 300 chars. Plus the `.aelix` prerequisite (OC-5), OC-2's four measured back-channel blockers on the record, and residuals R1–R6 as trade-offs. | ✅ |
| **ADR-0198 — "print-mode JSON event-envelope stability contract"** | **NEW** `docs/decisions/0198-print-mode-json-envelope-contract.md` | D1 snake_case wire format · D2 `usage` explicitly OUT of the guarantee · D3 the typeless header · D4 `message_update` is O(n²) · D5 JSON mode never exits non-zero on an assistant error · D6 the transport must be chunked · D7 `test_print_mode_json_contract.py` mandatory · D8 `SubagentResult` grows only by defaulted fields. Records that spec §9's third ADR is **partially** fulfilled — the rpc half and the cross-channel parity test are P3. | ✅ SHIPPED (249 lines) |
| **ADR-0008** | **AMEND** | The mechanism/policy criterion from spec §9, scoped so `MAX_SUBAGENT_DEPTH` in `subagent_contract.py` is not a violation: *"Output caps, concurrency limits, topology, task lists, goals, and dashboards are extension policy. The only limits product-core may declare are seam invariants that exist to keep the seam safe (delegation depth) — never tunables."* **Plus:** *"Consent policy is likewise extension policy — product-core may surface a refusal, never author one."* Scope *"runtime core에는 multi-agent 개념을 두지 않는다"* explicitly to the kernel (untouched) and to orchestration (extension). | ✅ |
| **ADR-0157** | **NOTE** | New note recording the `.aelix` addition to `_SENSITIVE_DIR_COMPONENTS` and the `headless_default` field as posture-lineage amendments. | ✅ |
| **ADR-0196** | **NOTE** | "Fulfilled in P2 (ADR-0197)" on each of the three deferrals at `:415-433`. `confirm_project_agents` as a settings key remains deferred; its *behavioural* half ships as **two** consent gates — §(f) for identity, §(i) for authority. `approval_mode`'s "validated, not read" deferral is **retired** by §(i). | ✅ |
| **ADR-0165** | **NOTE** | The deferred subagent-status subsystem is fulfilled at the extension layer from the child's own event stream; kernel `AgentStart/EndEvent` payloads stay empty; no `tui/types.py` enrichment. | ✅ |
| **CHANGELOG** | **ENTRY** | Under `### Changed`: `auto-accept-edits` / `auto` no longer auto-approve writes under `.aelix/` — editing `.aelix/agents/*.md`, `.aelix/extensions/*.py`, `.aelix/mcp.json` or `.aelix/settings.json` now shows the usual approval prompt. Those files **execute on a later run**. Ordinary in-project writes unaffected. See ADR-0197 §(i). | ✅ |

### Rejected scope, named in §0 and repeated in ADR-0197's *Deferred* list
Persisted per-profile consent and any `<agent_dir>/subagent-grants.json` store (P3) · an `[agents] autoApprove` allowlist (**never**, or only as a global-scope-only ceiling `min()`) · any path setting `allow_project=True` on the model-driven `agent` tool (**never**) · the child→parent approval back-channel (P3, with its own threat model for child-authored request strings) · a per-session consent memo (P3) · the wider child-trust rule and honouring `defaultProjectTrust: "always"` (P3) · flipping `permission.py:382` allow→deny for all headless runs (its own ADR).

---

## 12. Work breakdown — DISJOINT FILE OWNERSHIP (re-cut per I9, B10, delta 19)

Every module and every test file appears **exactly once**. `consent.py` and `trust.py` are **WS-D**.

### Wave 1 — three independent streams — ✅ ALL SHIPPED

**WS-A — product-core contract** *(Wave-2 blocker; landed first)*
`.../aelix_coding_agent/subagent_contract.py` (new) · `.../extensions/api.py` (**4** hunks, §5.1) · `tests/agents/test_subagent_contract.py` · `tests/agents/test_p2_band_boundaries.py`

**WS-B — the settings flag**
`packages/aelix-ai/src/aelix_ai/settings/{types.py,settings_manager.py,__init__.py}` · `.../tui/settings_rows.py` · `tests/settings_manager/test_features_agents_flag.py`

**WS-C — the permission surface** *(**also a Wave-2 blocker** — the child argv is a cross-workstream contract, B10)*
`.../cli/args.py` · `.../builtin/permission.py` (**5** hunks, §5.4 — includes the OC-5 `.aelix` hunk) · `tests/builtin/test_permission_child_floor.py` · `tests/builtin/test_sensitive_aelix_dir.py` · `tests/cli/test_permission_mode_flag.py`

### Wave 2 — the extension (needs WS-A **and** WS-C landed)

**WS-D — `aelix-agents`**
`packages/aelix-coding-agent/src/aelix_agents/**` (**12** modules) · `packages/aelix-coding-agent/pyproject.toml` (**line 100-101** — still unedited) · `tests/agents_ext/**` (dir + `__init__.py`) · `tests/cli/test_print_mode_json_contract.py`

Internal split (still disjoint):

* **D1 — pure / near-pure. ✅ SHIPPED.** `stream.py` (LineAssembler + `reduce_line`), `envelope.py`, `posture.py`, **`trust.py`**, `prompt_file.py` + `test_stream_reduce.py`, `test_envelope.py`, `test_posture_clamp.py`, `test_child_trust_argv.py`. No asyncio; startable immediately. *(`consent.py` also landed here ahead of schedule; its WIRING is D3's.)*
* **D2 — the process layer. REMAINING.** `print_channel.py`, `reaper.py`, `runtime.py` + `test_print_channel_spawn.py`, `test_child_argv_contract.py`, `test_child_realized_posture.py`. Depends on D1's `_StreamState` / `LineAssembler` shapes, on `child_trust_argv`, and on WS-C's `--permission-mode`.
* **D3 — consent wiring + the tool layer. REMAINING. Owns every `ctx.ui` line outside `consent.py`.** `tool.py`, `progress.py`, `extension.py` (the `tool_call` consent handler + the grant dict + `session_shutdown` clear), `__init__.py` + `test_tool_and_security.py`, `test_consent_wiring.py`, `test_events_and_statusline.py`, `test_reduce_consumes_real_print_mode_output.py`. Depends on D2's `PrintChannel.run` signature and on `consent.py`'s `SpawnGrant` / `request_spawn_consent`.

### Wave 3 — wiring + docs

**WS-E — product-core wiring. REMAINING.**
`.../cli/entry.py` (5 hunks, §5.3) · `.../tui/commands.py` (2 hunks, §5.5) · `tests/cli/test_agents_extension_gate.py` · `tests/cli/test_p2_import_direction.py` (**moved here from WS-A**) · `tests/tui/test_agents_run_command.py`

**WS-F — ADRs + infra. ✅ SHIPPED.**
`docs/decisions/0197-*.md`, `0198-*.md` (new) · `0008-*.md` (amend), `0157-*.md`, `0196-*.md`, `0165-*.md` (note) · `docs/decisions/README.md` · `CHANGELOG.md` · `.github/workflows/ci.yml` (`fetch-depth: 0`)

### Wave 4 — verification (single lane, separate context; nothing self-approves)

1. `pytest tests/ -q` under the prescribed PYTHONPATH.
2. `tests/agents/test_p2_band_boundaries.py` green.
3. **Live smoke, real key, real child:**
   * `aelix --agents` → `/agents run scout "list the files in packages/"` → structured panel with usage and the granted `permission_mode`; statusline row appears and clears.
   * **The consent modal — a MANDATORY manual live-TUI run (residual R3).** Our modal is TALLER than the shipped approval dialog (path + up to 300 chars of task) and could hit `_CappedContainer` / `_modal_cap` clipping differently (`tui/overlay.py:256-271`). Copy `approval_dialog.py:280-289`'s HSplit-with-fixed-options-window pattern — its comment documents exactly why the deny option must stay visible when the body overflows. **Verify by eye that `Cancel` is on screen with a 300-char task**, then verify each answer: baseline → the clamp; widen → `auto-accept-edits`; Esc and `Cancel` → `declined`, no process created.
   * **Ctrl+C twice, ~0.4 s apart, mid-flight** → `aborted` envelope with partial summary, and `pgrep -f aelix_coding_agent` clean. (One press is not sufficient evidence — that is the B1 case.)
   * A profile with `approval_mode: inherit` from a DEFAULT parent → the consent dialog does **not** appear (the clamp is already PLAN) and the child reports "mutating tools are blocked", not a silent write.
   * A profile with `approval_mode: ask` from a DEFAULT parent → **prompts**; declining yields `declined`, not a crash. Headless (`-p`) with the same profile → runs read-only, **never refuses**.
   * A **project-scoped** profile named in an `agent(...)` tool call → refused with no prompt; the same profile via `/agents run` → prompts, and its consent dialog offers **no widening option**.
   * `agent(profile=…, cwd="vendor/sdk")` in a monorepo with a vendored `.aelix/extensions/*.py` → the nested extension does **not** execute (§2g clause 2). Same-cwd, skills-only repo → project skills **do** load (§2g clause 1).
   * A `.aelix/agents/x.md` write under an interactive AUTO_ACCEPT parent → **prompts** (OC-5); an ordinary in-cwd write → still silent.
   * A child that reads a large file (≥ 200 KB) → envelope parses, no `ValueError`.
   * `AELIX_SUBAGENT_DEPTH=1 aelix --agents` → no `agent` tool, `/agents run` degrades.
4. A `code-reviewer` pass focused on §2(e) / §2(f) / §2(g) / **§2(i)** (the security floor) and §9.5 (the reaper).
