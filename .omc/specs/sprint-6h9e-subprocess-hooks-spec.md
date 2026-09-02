# Sprint 6h₉e — Subprocess Hooks (Tier 4b) — W1 Binding Spec

**Workflow:** ADR-0032 W0→W6. **Closure ADR:** ADR-0102.
**Phase:** 5b-foundation (framework-neutral). **Tier:** 4b (Aelix-additive).
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` — held, **no Pi feature imported**.

---

## 0. Binding principle & nature

Top-level binding principle: **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**
Subprocess hooks are **Aelix-additive** — Pi has *no* subprocess hook lane in core at the pinned SHA.
The reference standard is therefore **Claude Code's documented hook system** (code.claude.com/docs/en/hooks),
NOT Pi. Pi parity is unaffected: this sprint imports **zero** Pi behavior and must not alter the
in-process `HookBus` reducer semantics (which ARE Pi-parity, ADR-0017).

The subprocess lane is a **second, separate lane** layered *on top of* the existing in-process hook bus
via a normal `api.on(...)` registration. It does NOT modify `HookBus`, `_REDUCERS`, `HOOK_RESULT_TYPES`,
or any `core.py` emit site.

---

## 1. Goal (one sentence)

Wire the `HookContrib` manifest declaration (defined schema-only in Sprint 6h₉a) to an actual
subprocess dispatch lane: each declared `[[contributes.hooks]]` entry registers an in-process handler
that, when its event fires, spawns the declared shell command, passes a Claude-Code-style JSON envelope
on stdin, and maps the command's stdout-JSON / stderr / exit-code back to the matching Aelix hook result
type — under a declarative `shell_exec` trust gate.

---

## 2. Out of scope (explicit deferrals — document in ADR-0102)

- **Interactive / runtime workspace-trust prompt** — there is no UI in Phase 5b-foundation. v1 uses the
  *declarative* `capabilities.shell_exec` gate only. An interactive "trust this workspace's hooks?" prompt
  is deferred to **Phase 5c-tui**.
- **Actionable results beyond `tool_call` blocking** — v1 actions ONLY the `tool_call` block decision
  (PreToolUse analog). `PostToolUse` output patching (`updatedToolOutput`), `UserPromptSubmit`
  `additionalContext` injection, and `permissionDecision: "allow"/"ask"` semantics are **observational**
  in v1 (subprocess runs, result logged, returns `None`). v2 widening path documented in ADR-0102.
- **`http` / `mcp_tool` / `prompt` / `agent` hook types** — CC supports these; Aelix v1 supports only
  `type: "command"` (shell), which is the only shape `HookContrib` declares.
- **CC config-file `hooks` key / matchers / `if` filters** — Aelix declares hooks via the plugin manifest
  (`[[contributes.hooks]]`), not a settings.json `hooks` block. No matcher/regex layer in v1 (the
  `HookContrib.event` name IS the selector).
- **Per-`tool_name` matching within an event** — `HookContrib` has no matcher field; a `tool_call` hook
  fires for ALL tool calls. Tool-name matching is a v2 `HookContrib` field (deferred).

---

## 3. Reference protocol (Claude Code — verified W0)

**stdin envelope — snake_case.** Common: `hook_event_name`, `session_id`, `cwd`. Event-specific (see §5.2).
**stdout control JSON — camelCase.** `continue`, `stopReason`, `suppressOutput`, `systemMessage`,
top-level `decision`/`reason`, and `hookSpecificOutput.{hookEventName, permissionDecision,
permissionDecisionReason, additionalContext, updatedToolOutput}`.
**Exit codes:** `0` → parse stdout JSON for control; `2` → **blocking** (stdout ignored, stderr fed back as
the block reason); other non-zero → **non-blocking error** (logged, execution continues = fail-open).
**Fail-open rule (matches CC):** spawn failure, timeout, invalid JSON, and non-{0,2} exit codes are all
**non-blocking** (return `None` = allow). The ONLY fail-closed paths are explicit `exit 2` or
`permissionDecision: "deny"` / `decision: "block"` on a `tool_call` event.

> Casing is load-bearing: stdin is snake_case (we *write* it), stdout control is camelCase (we *read* it).

---

## 4. Files

**New:**
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/subprocess_hooks.py`
- `tests/subprocess_hooks/test_subprocess_hooks.py` (note: own dir to avoid any import shadowing;
  add `tests/subprocess_hooks/__init__.py` only if the existing tests/ tree uses package dirs —
  match the prevailing convention; otherwise a flat module is fine. **Verify** how `tests/mcp_client/`
  is structured and mirror it.)

**Modified:**
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/loader.py` — `_resolve_factory`
  (hook-only plugin support) + `_invoke_factory` (trust gate + wiring).
- `packages/aelix-agent-core/src/aelix_agent_core/contracts/manifest.py` — `HookContrib` docstring/comment
  only (drop "validated downstream Sprint 6h₉e" → point to ADR-0102). **No schema field change.**
- `docs/decisions/0102-*.md` — new closure ADR.
- `docs/decisions/README.md` — index row for ADR-0102.

**MUST NOT touch:** `harness/hooks.py`, `harness/core.py`, any reducer, `HOOK_RESULT_TYPES`,
`McpServerContrib`, the `mcp/` package, `scripts/pyright_spike.py` (8 baseline errors preserved).

---

## 5. `subprocess_hooks.py` — design

### 5.1 Types & errors

```python
class SubprocessHookError(Exception): ...   # internal; never escapes a handler

@dataclass(frozen=True)
class HookSubprocessOutcome:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
```

**Event allowlist** — the subset of the 35 ADR-0017 events a subprocess hook may bind to. This both
validates `HookContrib.event` AND prevents a performance footgun (binding a subprocess to a
high-frequency streaming event like `message_update` would spawn a process per update). v1 set =
clean Claude-Code analogs:

```python
SUBPROCESS_HOOK_EVENTS: frozenset[str] = frozenset({
    "before_agent_start",  # ~ UserPromptSubmit (run start)
    "input",               # ~ UserPromptSubmit (raw input)
    "tool_call",           # ~ PreToolUse   (ONLY actionable/blockable in v1)
    "tool_result",         # ~ PostToolUse  (observational v1)
    "user_bash",           # Aelix `!` bash (observational)
    "session_start",       # ~ SessionStart (observational)
    "session_shutdown",    # ~ SessionEnd   (observational)
    "agent_end",           # ~ Stop         (observational)
})
```

> The allowlist is intentionally a *subset* of `HOOK_RESULT_TYPES`. Membership in `HOOK_RESULT_TYPES`
> alone is necessary but not sufficient — the event must also be in `SUBPROCESS_HOOK_EVENTS`.
> Cross-check at module import that `SUBPROCESS_HOOK_EVENTS <= set(HOOK_RESULT_TYPES)` (assert / test).

### 5.2 Spawn core

```python
async def run_hook_subprocess(
    command: str, payload: str, *, timeout_ms: int,
    cwd: str | None = None, env: dict[str, str] | None = None,
) -> HookSubprocessOutcome
```

- `asyncio.create_subprocess_shell(command, stdin=PIPE, stdout=PIPE, stderr=PIPE, cwd=cwd, env=...)`.
  Shell form matches CC's `sh -c "<command>"` shell-form hooks (single command string, no args field).
- env: `dict(os.environ)` + provided `env`. Set an Aelix-additive `AELIX_PROJECT_DIR = cwd` when `cwd`
  is set (CC analog of `$CLAUDE_PROJECT_DIR`); document it as Aelix-additive, not a Pi/CC import.
- `await asyncio.wait_for(proc.communicate(input=payload.encode()), timeout=timeout_ms / 1000)`.
- On `TimeoutError`: teardown using the `rpc_client.py:158-194` pattern — `proc.terminate()` (suppress
  `ProcessLookupError`) → `wait_for(proc.wait(), 1.0)` → on second `TimeoutError` `proc.kill()` →
  bounded `wait_for(proc.wait(), 5.0)` (suppress). Return `HookSubprocessOutcome(exit_code=124, stdout="",
  stderr="<hook timed out after {timeout_ms}ms>", timed_out=True)`.
- Decode stdout/stderr as UTF-8 with `errors="replace"`.
- Cap captured stdout at 10_000 chars before JSON parse (CC parity: hook output capped at 10k);
  truncation is logged at debug.
- This function does NOT raise on non-zero exit — it returns the outcome. It MAY raise
  `SubprocessHookError` only on a genuine spawn failure that `create_subprocess_shell` surfaces
  (e.g. `OSError`); the caller (`make_subprocess_handler`) catches it and fails open.

### 5.3 Serialization

```python
def serialize_hook_event(event: HookEvent, ctx: ExtensionContext) -> dict[str, Any]
```

Common keys always present: `hook_event_name = event.type`, `cwd = ctx.cwd`,
`session_id` = best-effort (`ctx.session_manager` id if available, else `""`).
Per-event extras (snake_case, mirroring CC `tool_input` etc.):

| event.type | extra keys |
|---|---|
| `tool_call` (+ typed subclasses) | `tool_name`, `tool_use_id` (= `event.tool_call_id`), `tool_input` (= `event.args`) |
| `tool_result` | `tool_name`, `tool_use_id`, `tool_input` (= `event.args`), `is_error` |
| `input` | `prompt` (= `event.text`), `source` |
| `user_bash` | `command`, `cwd` (event's), `exclude_from_context` |
| `session_start` | `reason`, `previous_session_file` |
| `session_shutdown` | `reason`, `target_session_file` |
| `before_agent_start` | `prompt`, `system_prompt` |
| `agent_end` | (common only) |

- `tool_input` / `args` must be JSON-serializable. Use a safe encoder: `json.dumps(payload, default=str)`
  so a non-serializable arg value degrades to its `str()` rather than raising. The serializer returns a
  `dict`; the caller does the `json.dumps`.
- `event.type` matching: use the typed-subclass fact that `BashToolCallHookEvent` etc. all carry
  `type == "tool_call"` — match on `event.type`, not `isinstance`, so the 7 typed `*ToolCallHookEvent`
  variants and `CustomToolCallHookEvent` are all handled by the `tool_call` branch.

### 5.4 Output parsing

```python
def parse_hook_output(event_type: str, outcome: HookSubprocessOutcome) -> Any
```

Returns the Aelix hook result object for the event, or `None` (no opinion / observational / fail-open).

1. `outcome.timed_out` → log warning, return `None` (fail-open).
2. `outcome.exit_code == 2` (blocking):
   - if `event_type == "tool_call"` → `ToolCallResult(block=True, reason=outcome.stderr.strip() or "blocked by subprocess hook")`.
   - else → log info ("subprocess hook requested block on non-blockable event %s", event_type), return `None`.
3. `outcome.exit_code == 0`:
   - if `outcome.stdout.strip()` is empty → `None`.
   - parse `json.loads(stdout)`; on `JSONDecodeError` → log debug, return `None` (fail-open).
   - if `event_type == "tool_call"`:
     - `hookSpecificOutput = data.get("hookSpecificOutput") or {}`
     - deny if `hookSpecificOutput.get("permissionDecision") == "deny"` OR `data.get("decision") == "block"`.
     - on deny → `ToolCallResult(block=True, reason=hookSpecificOutput.get("permissionDecisionReason") or data.get("reason") or "denied by subprocess hook")`.
     - else → `None` (allow/ask/no-opinion all observational in v1).
   - else (other allowlisted events) → `None` (observational v1).
4. any other exit code → log info (non-blocking error, first stderr line), return `None`.

> Defensive: `data` may not be a `dict` (e.g. hook prints `"true"`). Guard `isinstance(data, dict)` before
> `.get`; non-dict JSON → `None`.

### 5.5 Handler factory

```python
def make_subprocess_handler(contrib: HookContrib) -> HookHandler:
    async def _handler(event: HookEvent, ctx: ExtensionContext) -> Any:
        try:
            payload = json.dumps(serialize_hook_event(event, ctx), default=str)
            outcome = await run_hook_subprocess(
                contrib.command, payload,
                timeout_ms=contrib.timeout_ms, cwd=ctx.cwd,
            )
            return parse_hook_output(event.type, outcome)
        except Exception as exc:           # noqa: BLE001 — fail-open lane
            _log.warning("subprocess hook %r failed (fail-open): %r", contrib.command, exc)
            return None
    return _handler
```

- The handler NEVER raises — fail-open is the contract. (Belt-and-suspenders with the
  `error_mode="continue"` registration in §6.)

### 5.6 Validation helper

```python
def validate_subprocess_hook_event(event: str) -> None:
    """Raise ExtensionManifestError if `event` is not a valid subprocess-bindable event."""
```
- Unknown to `HOOK_RESULT_TYPES` → error message "unknown hook event {event!r}".
- Known but not in `SUBPROCESS_HOOK_EVENTS` → error message listing the allowed set.
- Import `HOOK_RESULT_TYPES` from `aelix_agent_core.harness.hooks`. `ExtensionManifestError` is the
  existing loader exception (verify its import location — Sprint 6h₉b defined it; reuse, do not redefine).

---

## 6. Loader wiring (`loader.py`)

### 6.1 `_resolve_factory` — hook-only plugin support

Current code (line ~554-565) rejects any `_ManifestEntry` whose `entry.python is None`. This is
inconsistent with the manifest validator, which permits a hooks-only plugin (no entry.python). Fix:

```python
if isinstance(entry, _ManifestEntry):
    py_entry = entry.manifest.entry.python
    if py_entry is None:
        if entry.manifest.contributes.hooks:
            # Hooks-only plugin (Tier 4b, ADR-0102): no Python factory to load;
            # return a no-op factory so _invoke_factory still builds the Extension
            # (with manifest attached) and wires the subprocess hooks.
            return _noop_factory, entry.manifest.plugin.id, entry.manifest
        raise ValueError(... existing message ...)
    factory = _factory_from_module(py_entry)
    return factory, entry.manifest.plugin.id, entry.manifest
```

`_noop_factory` = a module-level `def _noop_factory(api: ExtensionAPI) -> None: return None`.
(Do NOT use a lambda — the loader resolves `__qualname__` for display names elsewhere; a named def is
cleaner and matches the existing factory-callable contract.)

### 6.2 `_invoke_factory` — trust gate + wiring

After `result` is awaited (line ~676), before `return extension`:

```python
if manifest is not None and manifest.contributes.hooks:
    if not manifest.capabilities.shell_exec:
        raise ExtensionManifestError(
            f"plugin {manifest.plugin.id!r} declares [[contributes.hooks]] but "
            f"capabilities.shell_exec is false; subprocess hooks require shell_exec=true"
        )
    for contrib in manifest.contributes.hooks:
        validate_subprocess_hook_event(contrib.event)
        api.on(
            contrib.event,                       # type: ignore[arg-type]  (str → HookEventName)
            make_subprocess_handler(contrib),
            source=name,
            error_mode="continue",
        )
```

- **Trust gate** = `capabilities.shell_exec` must be `True`. This is the v1 declarative gate.
- `error_mode="continue"` — a subprocess hook crash must never abort the harness (fail-open, Aelix-additive).
- `source=name` — attributes the handler to the plugin in `HookRegistration.source` (telemetry/teardown).
- **Verify** `api.on` runtime accepts `source` + `error_mode` kwargs (ADR-0019 v3 added them) AND accepts a
  `str` channel (the 35 typed overloads narrow `HookEventName`; passing a validated `str` needs the
  `# type: ignore[arg-type]` OR a `cast(HookEventName, contrib.event)` — prefer `cast` over ignore for
  clarity; pick whichever keeps pyright at the 8-error baseline).
- `validate_subprocess_hook_event` raising propagates out of `_invoke_factory`. `load_extensions`
  already wraps each entry in try/except → the error is collected as an `ExtensionLoadError`, not a crash.
  **Confirm** this containment holds for both `load_extensions` and `discover_and_load_extensions`.

### 6.3 Import hygiene

`subprocess_hooks.py` lives in `aelix_coding_agent.extensions`. It imports `HookContrib` from
`aelix_agent_core.contracts`, `HOOK_RESULT_TYPES` + `ToolCallResult` + `HookEvent`/`HookHandler` from
`aelix_agent_core.harness.hooks`, and `ExtensionContext` from `aelix_coding_agent.extensions.api`.
Watch for an import cycle (`api.py` imports from `harness.hooks`; `subprocess_hooks.py` importing both is
fine as a leaf consumed only by `loader.py`). If `loader.py` importing `subprocess_hooks` creates a cycle,
do a function-local import inside `_invoke_factory` (document why).

---

## 7. Tests (`tests/subprocess_hooks/`)

`asyncio_mode = "auto"` — plain `async def test_*`, no decorator. Mirror `tests/mcp_client/` structure.
Use `tmp_path` for hook script fixtures; write tiny `#!/bin/sh` scripts (or use `python -c`).

**`run_hook_subprocess`:**
1. echo: `command="cat"` (stdin→stdout) → outcome.stdout == payload, exit 0.
2. exit-0 with JSON on stdout.
3. exit 2 with stderr → `exit_code == 2`, stderr captured, `timed_out == False`.
4. timeout: `command="sleep 5"`, `timeout_ms=200` → `timed_out == True`, `exit_code == 124`, fast (< 2s).
5. nonexistent command via shell (`command="this_cmd_does_not_exist_xyz"`) → non-zero exit, no raise.
6. 10k stdout cap (optional but preferred).

**`serialize_hook_event`:**
7. `tool_call` envelope → `hook_event_name=="tool_call"`, `tool_name`, `tool_use_id`, `tool_input==args`, `cwd`.
8. typed subclass `BashToolCallHookEvent` → routed through `tool_call` branch (has `tool_name`).
9. `input` → `prompt`, `source`. `non-JSON-serializable arg` → degrades via `default=str` (no raise).

**`parse_hook_output`:**
10. timeout outcome → `None`.
11. exit 2 + `tool_call` → `ToolCallResult(block=True, reason=stderr)`.
12. exit 2 + non-tool_call event → `None`.
13. exit 0 + `{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"nope"}}` +
    tool_call → `ToolCallResult(block=True, reason=="nope")`.
14. exit 0 + `{"decision":"block","reason":"x"}` + tool_call → block, reason "x".
15. exit 0 + `{"hookSpecificOutput":{"permissionDecision":"allow"}}` + tool_call → `None` (observational).
16. exit 0 + empty stdout → `None`. exit 0 + invalid JSON → `None`. exit 0 + non-dict JSON (`"true"`) → `None`.
17. exit 1 (non-blocking error) → `None`.

**`validate_subprocess_hook_event`:**
18. valid event (`"tool_call"`) → no raise.
19. unknown event (`"nope"`) → `ExtensionManifestError`.
20. known-but-not-allowlisted (`"message_update"`) → `ExtensionManifestError`.
21. module invariant: `SUBPROCESS_HOOK_EVENTS <= set(HOOK_RESULT_TYPES)`.

**Loader wiring (use a real `aelix-plugin.toml` in `tmp_path`, mirror `test_extension_discovery.py`):**
22. manifest with `[[contributes.hooks]]` + `capabilities.shell_exec=true`, no `entry.python` → loads
    (hooks-only plugin), `extension.handlers` contains the subprocess handler, no errors.
23. same manifest with `shell_exec=false` (or omitted) → `ExtensionLoadError` (or raised
    `ExtensionManifestError` depending on where the test calls in) mentioning `shell_exec`.
24. manifest with a hook on an unknown/non-allowlisted event → load error mentioning the event.

**End-to-end integration (the payoff test):**
25. Load a hook-only plugin whose `tool_call` hook command denies (a script that prints
    `{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"blocked"}}` and
    exits 0). Build a `HookBus` from the loaded extension's handlers (mirror `test_hooks.py` wiring),
    `emit(ToolCallHookEvent(...))`, assert the reduced result is `ToolCallResult(block=True)` with the
    reason. This proves the subprocess lane composes with the in-process reducer.

---

## 8. Gates (W3 / pre-commit)

- `ruff check` → clean.
- `uv run pyright` → **exactly 8 baseline errors** (the intentional `scripts/pyright_spike.py` fixtures).
  No new errors. If the `api.on` `str`→`HookEventName` call adds one, resolve with `cast`.
- `uv run pytest 2>&1 | tail -2` → all pass (current 2497 + new tests), ≤ existing skips.
- `python scripts/generate_contracts_schemas.py --check` → `exit=0` (no schema drift — `HookContrib`
  unchanged, so this MUST stay 0; if it changes, something modified the schema and must be reverted).

Test/lint commands (project memory):
`uv run pytest 2>&1 | tail -2 && echo "===SCHEMA===" && python scripts/generate_contracts_schemas.py --check 2>&1; echo "exit=$?"`

---

## 9. Commit plan (W6) — `git add <specific-paths>` only, HEREDOC msgs

Trailer on EVERY commit: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
Do NOT push. Do NOT `--no-verify`. Do NOT stage `.omc/project-memory.json` or `.omc/.project-memory.json.tmp.*`.

- **§A** `feat(extensions): subprocess hook dispatch core (Sprint 6h₉e §A)` — `subprocess_hooks.py`
  (spawn core + serialize + parse + factory + validation + allowlist).
- **§B** `feat(extensions): wire HookContrib subprocess lane + hooks-only plugins (Sprint 6h₉e §B)` —
  `loader.py` (`_resolve_factory` hooks-only support, `_invoke_factory` trust gate + wiring) +
  `manifest.py` HookContrib comment update.
- **§C** `test(extensions): subprocess hook core + wiring + e2e (Sprint 6h₉e §C)` — full test module.
- **§D** `docs: ADR-0102 subprocess hooks closure (Sprint 6h₉e §D)` — ADR-0102 + README row.
- **fold-ins** as W4/W5 require (§E, §F …) — citation/semantics corrections.

---

## 10. W4 / W5 emphasis (user directive: "코드 리뷰 확실히 + 레퍼런스 비교 확실히")

- **W4 code-reviewer (opus):** subprocess teardown correctness (no orphaned processes on timeout);
  fail-open invariant (handler never raises); trust-gate cannot be bypassed; import-cycle check;
  pyright 8-baseline held.
- **W5 critic (opus):** **reference comparison against the Claude Code hook spec** —
  stdin snake_case vs stdout camelCase fidelity; exit-code semantics (0/2/other) exactly matching CC;
  fail-open vs fail-closed boundary matching CC; that NO Pi behavior was imported and the in-process
  `HookBus`/reducers/`HOOK_RESULT_TYPES` are byte-unchanged; allowlist justification. Verify the
  `AELIX_PROJECT_DIR` env var is documented as Aelix-additive (not falsely cited as CC/Pi).
- Any divergence from the CC reference must be documented as an **intentional Aelix divergence** in
  ADR-0102 (numbered), not left implicit.
