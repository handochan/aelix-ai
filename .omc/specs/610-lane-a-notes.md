# #610 lane A (core) — notes for lane B and the lead (2026-10-30)

Base `f3e76fbb`, branch `feat/610-agent-resume`, worktree `claude-610`. Everything here is in the lane A WIP commit.
Prefixes: `A/` = `packages/aelix-coding-agent/src/aelix_agents/`, `C/` = `packages/aelix-coding-agent/src/aelix_coding_agent/`.

## 1. API for lane B (exact imports and signatures)

### Target, listing, refusals — `aelix_agents.child_session`
- `ResumeTarget` (frozen): `session_id` (child session uuid = the id the model passes), `first_key` (`sub-<12hex>`),
  `path` (absolute, DERIVED from the parent file now), `rel`, `limits: ChildLimits` (FIRST run's, the ceiling),
  `profile: str` (record's name — untrusted, sanitise before showing), `task_preview: str` (first run's, ≤200 chars,
  untrusted), `last_status` (`finished|stopped|failed|unknown`, this branch), `parent_session_id`; property `child_file`.
- `ChildLimits` (frozen): `granted_mode: PermissionMode`, `tools: tuple[str,...]`, `context_files: bool`,
  `thinking: str|None`, `model: ModelPin`, `cwd: str`, `profile_source: str|None`, `profile_scope: str`;
  `to_record() -> dict`, `ChildLimits.from_record(obj) -> ChildLimits` raises `LimitsError(reason)` (`.reason`).
- `RESUME_*` constants — every refusal is a complete model-facing sentence, no path, no record value:
  `RESUME_NEEDS_ID`, `RESUME_UNKNOWN_ID`, `RESUME_NO_SESSION`, `RESUME_UNREADABLE`, `RESUME_NOT_YOURS`,
  `RESUME_FIRST_RUN_MISSING`, `RESUME_BEFORE_LIMITS`, `RESUME_FILE_GONE`, `RESUME_FORKED`, `RESUME_NEVER_STARTED`,
  `RESUME_DAMAGED` (use `resume_damaged(detail)`), `RESUME_RUNNING`, `RESUME_IN_USE`, `RESUME_LOCK_ERROR`
  (`.format(error=...)`), `RESUME_CWD_GONE`, `RESUME_ROUTE_MOVED`, `RESUME_PROFILE_CHANGED`,
  `RESUME_CONVERSATION_CHANGED`. Listing: `RESUMABLE_HEAD`, `RESUMABLE_NONE`, `LISTED_AGENTS`=10, `LISTING_BYTES`=2048.
- `ResumeRefused(Exception)`: `str(exc)` is a `RESUME_*` sentence.
- Module functions (the runtime wraps them; lane B should call the runtime methods): `find_resume_target(session, child_id)`,
  `resumable_agents(session)`, `session_id_of(session)`.

### The runtime door — `aelix_agents.runtime._SubagentRuntimeImpl` (off the Protocol; CONTRACT_VERSION unchanged)
- `await runtime.find_resume_target(child_id: str | None) -> ResumeTarget | str`
  Reads the parent through `host.session` (the getter `_run` records into). `None`/`""` → `RESUME_NEEDS_ID + "\n\n" + listing`;
  unknown → `RESUME_UNKNOWN_ID + "\n\n" + listing`; every other refusal is one `RESUME_*` sentence. The hook can pass
  `call.id` straight in and block with the returned string — the listing is already attached.
- `await runtime.resumable_agents() -> str` — the listing alone (≤10 entries, ≤2048 B, newest first, overflow counted;
  line shape `- <id> (<profile>, <status>): "<preview ≤80>"`; profile/preview through `child_line`, JSON-quoted).
- `runtime.resume_refusal(target, *, resolved=None) -> str | None` — SYNC. In order: identity (when `resolved` given:
  same name, same `source_path` as `target.limits.profile_source`, not project scope → `RESUME_PROFILE_CHANGED`);
  a live registry row runs this child (`RESUME_RUNNING`); file still a regular file (`RESUME_FILE_GONE` / damaged);
  recorded cwd re-contained in `host.cwd()` (`RESUME_CWD_GONE`); pinned route (`RESUME_ROUTE_MOVED`); the child file's
  writer lock probed with `SessionWriterLock(path, timeout=0)` and released (`RESUME_IN_USE`, or `RESUME_LOCK_ERROR`
  with the OSError's strerror; a filesystem with no locking is not a refusal).
- `await runtime.resume_granted(grant, resolved, target, instruction, *, on_event=None, record=None) -> SubagentResult`
  DEVIATION from the design's signature: it also takes `resolved` (the profile re-resolved by `target.profile` with
  `allow_project=False`, as `_execute` already re-resolves for a fresh spawn) — `_run` needs the profile's body,
  skills, extensions and output cap. Fails closed on a non-consented grant (`declined`). Then, before `_run`, so a
  refusal costs no budget and no records: `session_id_of(host.session()) == target.parent_session_id` else
  `RESUME_CONVERSATION_CHANGED`; `resume_refusal(target, resolved=resolved)` again. Refusals come back as
  `SubagentResult(ok=False, status="error", summary=<RESUME_* sentence>)` (the existing `_error_result` shape).
  Then `_run` with a NEW key, the attached receipt, the intersected limits, `permission_floor=target.limits.granted_mode`,
  task `resume_task(instruction)`. Charged against the per-prompt budget. `record` should be
  `SpawnRecordMeta(tool_call_id=<key>, mode="single", index=0, envelopes=<CallEnvelopes>)` exactly as for a single spawn.
- `RESUME_PREAMBLE`, `RESUME_PREAMBLE_RESERVE` (=1024), `resume_task(instruction)` — module level in `aelix_agents.runtime`.
  The parser's resume budget is `chain.MAX_TASK_BYTES - RESUME_PREAMBLE_RESERVE`. `tool.py` may import them from
  `runtime`: runtime's own imports (child_session, consent, envelope, posture, print_channel, prompt_file, stream) never
  reach tool/aggregate/batch/extension, so the edge is acyclic.

### The ceiling — `aelix_agents.posture.resume_clamp(ceiling, live) -> PermissionMode`
rank-min over `_RANK`. Use it (critique B1) as `want = resume_clamp(target.limits.granted_mode, child_permission_mode(...))`
in `_grant_for`, inside `request_spawn_consent` before the no-UI return, and (already done) as `resume_granted`'s
floor. The disclosure should be built from the clamped mode.

### Limits / argv — `aelix_agents.print_channel`
`fresh_limits(resolved, *, granted_mode, cwd, parent_tools, parent_context_files, parent_thinking, parent_model,
model_registry) -> ComputedLimits(limits, dropped)`; `resume_limits(stored, fresh, *, parent_cwd) -> ComputedLimits`
(raises `ResumeRefused(RESUME_CWD_GONE)`); `limited_profile(profile, limits)`. `SpawnPlan` gained `limits`,
`dropped_tools`, `resumes`; `RunningChild` gained `session_id`. Lane B normally needs none of these directly.

### Model pin — `aelix_coding_agent.agents.resolver`
`ModelPin(model, provider, route)` (all optional; `ModelPin()` = no flags), `child_model_pin(...)`,
`model_pin_flags(pin)` (THE one emission site), `model_pin_route_moved(pin, registry) -> bool`.
For the dialog's Model row use `target.limits.model.model` (and `.provider`).

### How the hook gets the parent session / id
Do not read it yourself: call `runtime.find_resume_target(call.id)` — it reads `host.session()` and its metadata id, and
the target carries `parent_session_id`, which `resume_granted` re-checks.

### Suggested hook order (lane B decides)
parse → `find_resume_target(call.id)` (str → block) → `call.profile == target.profile` else block (sanitise the record's
name with `child_line` if you echo it) → `resolve_profile(target.profile, allow_project=False)` (raise → block) →
`resume_refusal(target, resolved=...)` → budget ≥ 1 → `_grant_for` with the ceiling → pending. `_execute`:
re-resolve + source check as today → `resume_granted(...)` → `render_subagent_result`.

## 2. What changed (by file)
- `C/agents/resolver.py`: `ModelPin`, `_pin_of`, `model_pin_flags`, `child_model_pin`, `model_pin_route_moved`;
  `profile_to_flags`/`profile_to_argv` take `model_pin=` and always emit model flags through `model_pin_flags`.
- `A/posture.py`: `resume_clamp` (not in my listed scope — it is where `_RANK` lives and consent.py can import it
  without a cycle).
- `A/child_session.py`: `LimitsError`, `ChildLimits` (+`to_record`/`from_record`), `RESUME_*`, `resume_damaged`,
  `ResumeRefused`; receipt `limits`/`resumes` keys on every start+settle, `attach()`; the reader
  (`find_resume_target`, `resumable_agents`, `session_id_of`, listing).
- `A/print_channel.py`: `ComputedLimits`, `fresh_limits`, `resume_limits`, `limited_profile`; plan/row fields;
  `PrintChannel.run` builds argv from `plan.limits` only (computes with `fresh_limits` when absent); belt refusing a
  resume plan without limits; `build_child_argv(model_pin=...)`.
- `A/runtime.py`: limits decided before admission; `_parent_model` (guarded); resume door (`find_resume_target`,
  `resumable_agents`, `resume_refusal`, `resume_granted`); `_Resume`; preamble constants; `_cancelled_envelope` uses
  `plan.dropped_tools`; `_same_identity`, `_lock_refusal`.
- `A/rpc_channel.py`: refuses a plan with `resumes` set (error envelope, no process).
- Citation `--fix` relocated line numbers in comments of other files (batch, aggregate, chain, consent, extension, panel,
  progress, prompt_file, reaper, profile.py, some tests); two generic `try:` continuations were re-derived by hand
  (`batch.py:301`, `test_batch_executor.py:258` → `print_channel.py:1318`/`:1319`).
- Tests updated: `_IDENTITY_KEYS` (+`limits`,`resumes`); three argv-builder spies read `model_pin` instead of
  `parent_model` (`test_host_late_binding.py`, two in `test_print_channel_spawn.py`).
- New: `tests/agents_ext/test_agent_resume_610.py` (114 cases), `tests/agents_ext/test_agent_resume_real_child_610.py` (POSIX).

## 3. Measured
- Mutations (each restored): dropping `resume_granted`'s floor → 2 failures; skipping the rel check → 2; open() instead
  of attach() → 4; transform text "No result provided" → "ok" → the real-child test fails on the wire assertion.
- Real child on `openai-completions` + local SSE mock: the resume request is `user, assistant(tool_calls call_sleep_1),
  tool(call_sleep_1, "No result provided"), user("Task: <preamble>…Next instruction:\n…")`; no toolResult written to
  the child file.
- `SessionWriterLock(timeout=0)` probe (filelock 4.0.1, macOS): a second instance in the same process sees contention
  (`False`, error None, 0.0 s); a symlinked sidecar gives `OSError(ELOOP)` on `.error`.

## 4. Not done / for later
- Not measured on Python 3.11/3.13/3.14 or Windows (ran 3.12.13 on macOS only); the new tests avoid loop-turn counting.
- The design's lane-B items (parser, hook, `_grant_for`/consent ceiling, dialog, Session line on success, docs).
- `resume_refusal` probes the lock by creating `<child>.jsonl.lock` beside the child file (the child creates the same
  sidecar when it runs; on POSIX it stays, by `session_lock`'s design).
