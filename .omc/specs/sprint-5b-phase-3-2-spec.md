# Sprint 5b · Phase 3.2 — 7 Built-in Coding Tools + 3 Event Emit Sites + Tool-Typed ToolCallEvent + CLI Loop Minimal + ExtensionCommandContext Partial (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**
ADR-0041 4-week deadline: **2026-06-14**

---

## §0 — P-31~P-36 findings (Sprint 5b W1 architect)

| ID | Pi truth (SHA `734e08e`) | Aelix today | Drift |
|---|---|---|---|
| **P-31** | Pi ships **tool-typed `ToolCallEvent` discriminated union** (`coding-agent/.../extensions/types.ts:771-830`): `BashToolCallEvent` / `ReadToolCallEvent` / `EditToolCallEvent` / `WriteToolCallEvent` / `GrepToolCallEvent` / `FindToolCallEvent` / `LsToolCallEvent` + `CustomToolCallEvent` (toolName: string fallback). Each variant carries `input: XxxToolInput` (typed schema). Pi `ToolResultEvent` is also tool-typed (`types.ts:833-876`) with per-tool `details` field (`BashToolDetails` / `EditToolDetails` etc.). Discriminator helpers `isBashToolResult`, `isToolCallEventType(toolName, event)` at `types.ts:888-940`. | Single generic `ToolCallHookEvent` with `tool_name: str` + `args: dict[str, Any]` (`hooks.py:371-388`). | **MAJOR** — NEW ADR-0043 REQUIRED. Sprint 5b lands 7 typed variants + `CustomToolCallEvent` + reducer narrowing via `isToolCallEventType()` runtime + py overload narrowing on `ExtensionAPI.on("tool_call", ...)`. Strict superset path: keep `ToolCallHookEvent` as the base class, ship 8 dataclass subclasses (7 known + 1 custom) — old code paths still work; new code can `match event:`. |
| **P-32** | Pi tools live at `core/tools/` (NOT `tools/` — Sprint 5a spec wording was inverted). 7 files: `bash.ts` (440 LOC), `read.ts` (363), `edit.ts` (489), `write.ts` (281), `grep.ts` (384), `find.ts` (370), `ls.ts` (229). `core/tools/index.ts:78-79` exports `ToolName = "read" \| "bash" \| "edit" \| "write" \| "grep" \| "find" \| "ls"` + `allToolNames: Set<ToolName>`. | No `tools/` directory under `aelix_coding_agent`. | 7 tool names confirmed = Aelix S-12 expectation. Bash includes `BashToolDetails`, `BashOperations` Protocol with `exec(command, cwd, {onData, signal, timeout, env})` returning `Promise<{exitCode}>`, `createLocalBashOperations()` factory + `BashSpawnHook` extension point. |
| **P-33** | Pi tools take `(cwd, options)` not a singleton. `core/tools/index.ts:108-127` `createTool(name, cwd, options)` + `core/tools/index.ts:128-176` collection factories: `createCodingTools(cwd, options) → [read, bash, edit, write]` (4 mutation tools), `createReadOnlyTools(cwd, options) → [read, grep, find, ls]`, `createAllTools(cwd, options) → Record<ToolName, Tool>` (all 7). Plus `*ToolDefinition` variants for unwrapped (`ToolDef`, no harness wrapping). | None. | Aelix mirrors as `aelix_coding_agent.tools` module with `create_<name>_tool(cwd, options=None)` per Pi + `create_coding_tools(cwd, options)` / `create_read_only_tools(cwd, options)` / `create_all_tools(cwd, options)` collection factories. AgentHarnessOptions.cwd already lands the path. |
| **P-34** | Pi `input` emit lives at **`agent-session.ts:984-1001`** inside `AgentSession.prompt()` (NOT CLI). Gated by `this._extensionRunner.hasHandlers("input")`. Pi `user_bash` emit lives at **`interactive-mode.ts:5403-5410`** inside `InteractiveMode.handleBashCommand()` (PURE CLI). Pi `resources_discover` emit lives at **`agent-session.ts:2055-2076`** inside `AgentSession.extendResourcesFromExtensions()` triggered from `session_start` (startup) and `/reload` command (line 2401-2402). | Sprint 5a registered types + reducers; zero emits. | **CORRECTION to Sprint 5a §D claim**: only `user_bash` lives in CLI loop; `input` and `resources_discover` belong in the harness (Pi `agent-session.ts`). 5b owns all 3 still, but the wiring split is: `input` → `AgentHarness.prompt()` head (before `before_agent_start`); `user_bash` → minimal CLI `!/!!` parser shim; `resources_discover` → new `AgentHarness.discover_resources()` called at harness construction + new `AgentHarness.reload_resources()` for ExtensionCommandContext.reload. |
| **P-35** | Pi `ExtensionCommandContext` (6 methods, `types.ts:333-364`): `waitForIdle()`, `newSession(options?)`, `fork(target, options?)`, `navigateTree(target, options?)`, `switchSession(target, options?)`, `reload()`. Aelix already exposes 3 underlying primitives: `AgentHarness.wait_for_idle` (core.py:1267), `JsonlSessionRepo.fork` (jsonl_repo.py:238), `AgentHarness.navigate_tree` (core.py:804). `newSession` / `switchSession` need a `SessionManager`-like layer (Phase 5 owns); `reload` triggers `resources_discover`-with-reason="reload". | None of the 6. | Sprint 5b lands 4 of 6 (one more than original 3): `wait_for_idle` delegate, `fork` wrapper, `navigate_tree` delegate, `reload` delegate (calls new `AgentHarness.reload_resources()`). Defer `new_session` + `switch_session` to Phase 5 (Pi's `SessionManager.replaceSession` requires CLI life-cycle plumbing). |
| **P-36** | Pi `BashToolInput` schema (`bash.ts:23-26`): `{command: string, timeout?: number}`. `ReadToolInput` (`read.ts:21-25`): `{path: string, offset?: number, limit?: number}`. `EditToolInput` (`edit.ts:42-51`): `{path: string, edits: Array<{oldText: string, newText: string}>}`. `WriteToolInput` (`write.ts:15-18`): `{path: string, content: string}`. `GrepToolInput` (`grep.ts:24-35`): `{pattern, path?, glob?, ignoreCase?, literal?, context?, limit?}`. `FindToolInput` (`find.ts:21-26`): `{pattern, path?, limit?}` (glob; uses `fd` binary). `LsToolInput` (`ls.ts:16-19`): `{path?, limit?}`. | None. | Aelix ports each schema as JSON Schema dict per `aelix_ai.tools.Tool.parameters` shape. Bash uses `subprocess.Popen(..., preexec_fn=os.setsid)` per Pi `spawn(detached: true)`; default no-timeout (Pi `timeout?: number` is optional). Grep prefers `ripgrep` via `shutil.which("rg")` falling back to a Python `re` line-scanner. Find prefers `fd` falling back to `pathlib.Path.rglob`. |

**P-31 ramification:** Pi `tool_call` reducer (`runner.ts:806`) operates on the typed event. Mutations to `event.input` are observed by tools (Pi `agent-session.ts:381-395` passes the same dict reference). Aelix `ToolCallHookEvent` already preserves dict identity (`hooks.py:378` docstring), so the discriminated union ports cleanly by adding 8 frozen dataclass subclasses keyed off `tool_name` Literal narrowing. Existing handlers typed against `ToolCallHookEvent` keep working; new code can switch to per-variant overload narrowing for typed `event.input.command` etc.

---

## §A — 7 built-in coding tools

### A.1 Module layout

```
packages/aelix-coding-agent/src/aelix_coding_agent/tools/
    __init__.py            # public re-exports (Pi parity mirror of core/tools/index.ts)
    bash.py                # createBashToolDefinition / BashOperations / createLocalBashOperations
    read.py                # createReadToolDefinition + image MIME stub (image content type already in aelix_ai.messages)
    edit.py                # createEditToolDefinition (unified-diff via difflib; no display rendering)
    write.py               # createWriteToolDefinition (mkdir parents + utf-8 write)
    grep.py                # createGrepToolDefinition (ripgrep preferred, regex fallback)
    find.py                # createFindToolDefinition (fd preferred, pathlib fallback)
    ls.py                  # createLsToolDefinition
    _truncate.py           # port of Pi core/tools/truncate.ts: truncate_head/truncate_tail/truncate_line/format_size
    _path_utils.py         # resolve_read_path / resolve_to_cwd (Pi core/tools/path-utils.ts)
    _file_mutation_queue.py  # asyncio.Lock per file (Pi withFileMutationQueue)
```

`tools/__init__.py` re-exports:
```python
__all__ = [
    "ToolName", "ALL_TOOL_NAMES",
    "create_bash_tool", "create_read_tool", "create_edit_tool",
    "create_write_tool", "create_grep_tool", "create_find_tool", "create_ls_tool",
    "create_coding_tools", "create_read_only_tools", "create_all_tools",
    "BashOperations", "create_local_bash_operations",
    "ReadOperations", "EditOperations", "WriteOperations",
    "GrepOperations", "FindOperations", "LsOperations",
    "BashToolDetails", "ReadToolDetails", "EditToolDetails",
    "GrepToolDetails", "FindToolDetails", "LsToolDetails",
]
```

`ToolName = Literal["read", "bash", "edit", "write", "grep", "find", "ls"]`; `ALL_TOOL_NAMES: frozenset[ToolName]`.

### A.2 Per-tool table

| # | Pi cite | Aelix file | Input schema | Output (ToolResult) | execution_mode | Security model |
|---|---|---|---|---|---|---|
| 1 | `core/tools/bash.ts:264-440` `createBashToolDefinition` | `tools/bash.py` | `{command: str, timeout: int \| None}` | `content=[TextContent(text=stdout+stderr)]`, `details=BashToolDetails(truncation, full_output_path)`, `is_error = exit_code != 0` | **sequential** (file mutation risk; Pi `bash.ts` does not declare parallel-safe) | shellPath via `getShellConfig()` port; cwd check `Path(cwd).is_dir()`; `subprocess.Popen` with `preexec_fn=os.setsid` (Linux/macOS detach group) + `signal.SIGKILL` group-kill on abort; truncate to last 256 lines / 32 KB (Pi `DEFAULT_MAX_LINES/MAX_BYTES`); spill overflow to `/tmp/aelix-bash-XXXX` |
| 2 | `core/tools/read.ts:206-363` `createReadToolDefinition` | `tools/read.py` | `{path: str, offset: int \| None, limit: int \| None}` | `content=[TextContent(text=numbered_lines)]` OR `[TextContent(note), ImageContent(data, mime_type)]` for images; `details=ReadToolDetails(truncation)` | **parallel** | `resolve_read_path(path, cwd)`: tolerates absolute paths inside cwd OR `~/.aelix` config dir (Pi `getReadmePath()` mirror — Aelix uses `aelix_coding_agent.tools._path_utils.resolve_read_path`); enforce `Path.is_file()` + readable; image resize stub deferred to Phase 4 (Pi `image-resize.ts` uses sharp; Aelix returns image as-is with note) |
| 3 | `core/tools/edit.ts:288-487` `createEditToolDefinition` | `tools/edit.py` | `{path: str, edits: list[{old_text: str, new_text: str}]}` | `content=[TextContent(text=diff)]`, `details=EditToolDetails(diff, first_changed_line)`, `is_error=False`; on validation error: `is_error=True` + `content=[TextContent(error_msg)]` | **sequential** (write) | `_file_mutation_queue`: `asyncio.Lock` keyed by absolute path (Pi `file-mutation-queue.ts`); each `oldText` MUST be unique + non-overlapping (port Pi `edit-diff.ts validateEdits`); preserve BOM + line endings (`detectLineEnding` + `restoreLineEndings` port); `utf-8` round-trip |
| 4 | `core/tools/write.ts:181-281` `createWriteToolDefinition` | `tools/write.py` | `{path: str, content: str}` | `content=[TextContent(text="Wrote N bytes")]`, `details=None`, `is_error=False` | **sequential** (write) | `_file_mutation_queue` per-path lock; `Path(parent).mkdir(parents=True, exist_ok=True)`; `utf-8` write; refuse path traversal outside cwd unless caller passes absolute (matches Pi `resolveToCwd`) |
| 5 | `core/tools/grep.ts:122-384` `createGrepToolDefinition` | `tools/grep.py` | `{pattern: str, path: str \| None, glob: str \| None, ignore_case: bool \| None, literal: bool \| None, context: int \| None, limit: int \| None}` | `content=[TextContent(text=matches)]`, `details=GrepToolDetails(truncation, match_limit_reached, lines_truncated)` | **parallel** | Prefer `shutil.which("rg")` + `subprocess.Popen(["rg", ...])` with line-buffered stdout; fallback to Python regex line-scanner walking `Path(cwd).rglob(glob or "**/*")` honoring `.gitignore` via `pathspec`. Match limit default 100 (Pi `DEFAULT_LIMIT`); line truncate at `GREP_MAX_LINE_LENGTH = 250` (Pi parity). |
| 6 | `core/tools/find.ts:112-368` `createFindToolDefinition` | `tools/find.py` | `{pattern: str, path: str \| None, limit: int \| None}` | `content=[TextContent(text=paths_joined)]`, `details=FindToolDetails(truncation, result_limit_reached)` | **parallel** | Prefer `shutil.which("fd")`; fallback `Path.rglob`. Limit default 1000. Refuse `..` traversal outside `resolveToCwd` boundary. |
| 7 | `core/tools/ls.ts:99-227` `createLsToolDefinition` | `tools/ls.py` | `{path: str \| None, limit: int \| None}` | `content=[TextContent(text=entries)]`, `details=LsToolDetails(truncation, entry_limit_reached)` | **parallel** | Resolve to cwd; `Path.iterdir()`; default limit 500 (Pi parity); entries sorted alphabetically with `/` suffix for dirs (Pi parity). |

### A.3 `Operations` Protocols (Pi parity SSH-swap surface)

Each tool exposes a `XxxOperations` Protocol that the caller may inject via `options.operations`. Default uses the local filesystem + subprocess. This is Pi's documented extension point for remote execution (e.g. SSH); Aelix ports it verbatim so an extension can subclass `BashOperations` and Aelix's bash tool obeys it.

```python
class BashOperations(Protocol):
    async def exec(
        self,
        command: str,
        cwd: str,
        *,
        on_data: Callable[[bytes], None],
        signal: Any | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecExitResult:
        """Returns ExecExitResult(exit_code: int | None) — None if killed."""
```

Same shape for `ReadOperations` (`read_file`, `access`, `detect_image_mime_type`), `EditOperations` (`read_file`, `write_file`, `access`), `WriteOperations` (`write_file`, `mkdir`), `GrepOperations` (`is_directory`, `read_file`), `FindOperations` (`exists`, `glob`), `LsOperations` (`exists`, `stat`, `readdir`).

### A.4 Sprint 5a `BashOperations` / `BashResult` stub upgrade

Sprint 5a registered empty `Protocol` stubs in `hooks.py:744-756` for the `user_bash` reducer surface. Sprint 5b replaces them with the real `BashOperations` from `tools/bash.py` + a new `BashResult` dataclass (`output: str`, `exit_code: int | None`, `cancelled: bool`, `truncated: bool`, `full_output_path: str | None`) per Pi `agent-session.ts:2556-2563`. Re-export from `hooks.py` to preserve the import path.

### A.5 Built-in extension wrapper (Pi parity — explicit creation, NOT auto-registered)

Pi does NOT auto-register the 7 tools into every harness; the CLI explicitly calls `createCodingTools(cwd)` and passes them via `AgentHarnessOptions.tools` (`main.ts:600` region). Aelix mirrors this — `create_coding_tools(cwd)` returns `list[AgentTool]` for callers to drop into `AgentHarnessOptions.tools`.

**For ExtensionAPI usage**, ship an Aelix-additive `coding_tools_extension(cwd, options=None) -> Extension` factory under `aelix_coding_agent/builtin/coding_tools.py` that calls `aelix.register_tool(create_X_tool(cwd, options))` for each — so users can write `extensions=[coding_tools_extension(cwd)]` if they prefer the extension-loader path. This wrapper is documented in ADR-0042 as Aelix-additive.

---

## §B — 3 event emit sites

### B.1 `input` emit — `AgentHarness.prompt()` head

**Pi cite:** `agent-session.ts:984-1001`. Pi gates the emit on `hasHandlers("input")`, runs the reducer, short-circuits on `action="handled"`, applies `action="transform"` (mutates text + images).

**Aelix wiring** (`harness/core.py` `prompt()` around line 578):

```python
async def prompt(self, text: str, *, images: list[ImageContent] | None = None,
                 source: Literal["interactive", "rpc", "extension"] = "interactive") -> list[AgentMessage]:
    if self._phase != "idle":
        raise AgentHarnessError("busy", ...)
    self._phase = "turn"
    self._idle_event.clear()
    try:
        # === Sprint 5b §B.1 — input event emit (P-24/P-34) ===
        # Run BEFORE existing before_agent_start emit so a "handled" short-circuit
        # also skips before_agent_start (Pi parity — handled returns from prompt entry).
        if self._hooks.has_handlers("input"):
            input_result = await self._hooks.emit(
                InputHookEvent(text=text, images=images, source=source)
            )
            if isinstance(input_result, InputHandled):
                # P-34: Pi exits prompt() entirely; harness returns idle.
                self._phase = "idle"
                self._idle_event.set()
                return []
            if isinstance(input_result, InputTransform):
                text = input_result.text
                if input_result.images is not None:
                    images = input_result.images
        # ...existing turn flow continues unchanged
```

**New `images` + `source` params on `AgentHarness.prompt()`**: these are additive (default `None` / `"interactive"`) so existing callers stay compatible. Sprint 5b doesn't yet store images on UserMessage — that's Phase 4 multimodal work — but the InputHookEvent payload carries them through to extensions.

**`HookBus.has_handlers(name) -> bool` helper:** new method (1-line; checks `name in self._handlers`). Mirrors Pi `hasHandlers`. Needed by the gating pattern in all 3 emit sites.

### B.2 `user_bash` emit — CLI minimal `!/!!` parser

**Pi cite:** `interactive-mode.ts:2582-2599` (parser) + `interactive-mode.ts:5399-5466` (`handleBashCommand`).

**Aelix wiring:** Sprint 5b ships a minimal CLI loop at `packages/aelix-coding-agent/src/aelix_coding_agent/cli/repl.py` (NEW module, ~150 LOC). Not a full TUI — just `input()` + stdout. Pi's full interactive-mode (5528 LOC) is Phase 5 owned. The minimal CLI:

```python
async def run_repl(harness: AgentHarness, *, cwd: str) -> None:
    """Sprint 5b minimal REPL. Reads stdin, parses !/!!, dispatches.

    Pi-equivalent surface: enough to test user_bash emit + extension command
    interception. Full TUI lives in ADR-0033 (Phase 5).
    """
    while True:
        try:
            line = await asyncio.to_thread(input, "» ")
        except EOFError:
            break
        if not line.strip():
            continue
        # Pi parity (interactive-mode.ts:2582): "!" prefix → bash, "!!" prefix → bash excluded
        if line.startswith("!!"):
            await _handle_user_bash(harness, line[2:].strip(), exclude_from_context=True, cwd=cwd)
            continue
        if line.startswith("!"):
            await _handle_user_bash(harness, line[1:].strip(), exclude_from_context=False, cwd=cwd)
            continue
        if line.strip() in ("/quit", "/exit"):
            break
        if line.strip() == "/reload":
            await harness.reload_resources()
            continue
        # Standard prompt path — input emit fires inside harness.prompt()
        msgs = await harness.prompt(line)
        for msg in msgs:
            _render(msg)  # minimal stdout print

async def _handle_user_bash(
    harness: AgentHarness, command: str, *, exclude_from_context: bool, cwd: str
) -> None:
    if not command:
        return
    # Pi parity (interactive-mode.ts:5403): emit user_bash, let extensions intercept
    event_result = await harness.hooks.emit(
        UserBashHookEvent(command=command, exclude_from_context=exclude_from_context, cwd=cwd)
    )
    operations: BashOperations | None = None
    result: BashResult | None = None
    if isinstance(event_result, UserBashResult):
        operations = event_result.operations
        result = event_result.result
    if result is not None:
        # Extension fully handled — record and return (Pi 5410-5440)
        if not exclude_from_context and harness.session is not None:
            await harness.session.append_custom_entry(
                custom_type="bash_execution",
                data={"command": command, "output": result.output, "exit_code": result.exit_code},
            )
        print(result.output, end="" if result.output.endswith("\n") else "\n")
        return
    # Normal execution — use injected operations OR default local
    ops = operations or create_local_bash_operations()
    output_chunks: list[bytes] = []
    exit_result = await ops.exec(
        command, cwd,
        on_data=lambda chunk: output_chunks.append(chunk),
        signal=None,
    )
    output = b"".join(output_chunks).decode("utf-8", errors="replace")
    print(output, end="" if output.endswith("\n") else "\n")
    if not exclude_from_context and harness.session is not None:
        await harness.session.append_custom_entry(
            custom_type="bash_execution",
            data={
                "command": command,
                "output": output,
                "exit_code": exit_result.exit_code,
            },
        )
```

`harness.hooks` and `harness.session` are existing `AgentHarness` properties. `append_custom_entry` is the Sprint 4a Session method (`session.py:210`).

### B.3 `resources_discover` emit — `AgentHarness.discover_resources()` + `reload_resources()`

**Pi cite:** `agent-session.ts:2054-2076` `extendResourcesFromExtensions(reason)`, triggered from session_start (`agent-session.ts:2051`) and `/reload` (`agent-session.ts:2401-2402`).

**Aelix wiring** (`harness/core.py`):

```python
async def discover_resources(self) -> None:
    """Sprint 5b §B.3 — Pi parity ``extendResourcesFromExtensions("startup")``.

    Called once after extension loading at harness init time (Sprint 5b adds
    invocation from ``__init__`` AFTER ``runtime.bind_core``; before
    ``before_agent_start`` would fire on the first prompt).
    """
    await self._emit_resources_discover("startup")

async def reload_resources(self) -> None:
    """Sprint 5b §B.3 — Pi parity ``extendResourcesFromExtensions("reload")``.

    Invoked from ``ExtensionCommandContext.reload`` (5b §D) and from the
    minimal CLI ``/reload`` command. Must be idempotent.
    """
    await self._emit_resources_discover("reload")

async def _emit_resources_discover(
    self, reason: Literal["startup", "reload"]
) -> None:
    if not self._hooks.has_handlers("resources_discover"):
        return
    result = await self._hooks.emit(
        ResourcesDiscoverHookEvent(cwd=self._options.cwd, reason=reason)
    )
    if not isinstance(result, ResourcesDiscoverResult):
        return
    # Sprint 5b: collect + dedup (reducer already does this; harness merges
    # into self._state.resources["skill_paths"] / ... for downstream consumers).
    # Phase 4 resource-loader hookup is deferred — for Sprint 5b we just
    # publish the merged lists into AgentState.resources so an extension
    # author or downstream test can observe the contract.
    if self._state.resources is None:
        self._state.resources = {}
    if result.skill_paths:
        existing = list(self._state.resources.get("skill_paths") or [])
        for p in result.skill_paths:
            if p not in existing:
                existing.append(p)
        self._state.resources["skill_paths"] = existing
    if result.prompt_paths:
        # ...same merge pattern
    if result.theme_paths:
        # ...same merge pattern
```

**Call site for startup emit:** `AgentHarness.__init__` cannot `await`. Add a `bootstrap()` async helper (new in 5b) and require callers to invoke it once before `prompt()`. Document in the docstring + add `RuntimeWarning` if `prompt()` runs without prior `bootstrap()`. The CLI loop calls `await harness.bootstrap()` at startup; pure-test seams can skip it (no resources_discover handlers ⇒ noop).

Pi calls `extendResourcesFromExtensions("startup")` from `AgentSession.start()` not the constructor, so this matches Pi parity exactly.

### B.4 Reducer drift fixes (Sprint 5a residue)

`_reducer_input` (`hooks.py:1171`): verify `InputHandled` short-circuits (lookup that the reducer already returns first `handled`); `InputTransform` chains text + images forward; `None` / `InputContinue` are passthrough. Sprint 5a already lands this; 5b only adds a regression test that bare `None` from a handler equals `InputContinue`.

`_reducer_resources_discover` (`hooks.py:1226`): verify the dedup helper preserves first-seen order across handlers.

`_reducer_user_bash` (`hooks.py:1206`): verify last `result`-bearing handler wins (Pi `runner.ts:829-855` returns the last `result`).

---

## §C — Tool-typed ToolCallEvent variants (NEW ADR-0043)

### C.1 Approach — additive subclasses (strict superset, no breakage)

Aelix keeps `ToolCallHookEvent` as the **base** class. Sprint 5b adds 8 subclasses (frozen dataclasses):

```python
# hooks.py — Sprint 5b additions, mirror Pi types.ts:771-830

@dataclass(frozen=True)
class BashToolCallHookEvent(ToolCallHookEvent):
    tool_name: Literal["bash"] = "bash"
    # args field is inherited; Aelix keeps the dict-of-Any shape since Python's
    # type system can't narrow a dict's values from a Literal discriminator the
    # way TS narrows InputSchema. Helpers below provide the narrowed view.

@dataclass(frozen=True)
class ReadToolCallHookEvent(ToolCallHookEvent):
    tool_name: Literal["read"] = "read"

@dataclass(frozen=True)
class EditToolCallHookEvent(ToolCallHookEvent):
    tool_name: Literal["edit"] = "edit"

@dataclass(frozen=True)
class WriteToolCallHookEvent(ToolCallHookEvent):
    tool_name: Literal["write"] = "write"

@dataclass(frozen=True)
class GrepToolCallHookEvent(ToolCallHookEvent):
    tool_name: Literal["grep"] = "grep"

@dataclass(frozen=True)
class FindToolCallHookEvent(ToolCallHookEvent):
    tool_name: Literal["find"] = "find"

@dataclass(frozen=True)
class LsToolCallHookEvent(ToolCallHookEvent):
    tool_name: Literal["ls"] = "ls"

@dataclass(frozen=True)
class CustomToolCallHookEvent(ToolCallHookEvent):
    """Pi parity ``CustomToolCallEvent`` — for any tool whose name does not
    match the 7 built-ins. ``tool_name`` is the runtime string."""
    # tool_name stays str (inherited); discriminator is "not in BUILTIN_NAMES".

BUILTIN_TOOL_NAMES: frozenset[str] = frozenset({
    "bash", "read", "edit", "write", "grep", "find", "ls"
})

def is_tool_call_event_type(
    tool_name: str, event: ToolCallHookEvent
) -> bool:
    """Pi parity ``isToolCallEventType`` (types.ts:934-940). Runtime narrow."""
    return event.tool_name == tool_name
```

Mirror the same pattern for `ToolResultHookEvent` (8 subclasses + `BUILTIN_TOOL_RESULT_NAMES`).

### C.2 Construction site change

The `_before_tool_call_bridge` at `core.py:1494-1521` currently builds a generic `ToolCallHookEvent`. Sprint 5b switches to a factory:

```python
def _make_tool_call_event(
    tool_call_id: str, tool_name: str, args: dict[str, Any],
    assistant_message: Any, context: Any
) -> ToolCallHookEvent:
    cls = _TOOL_CALL_EVENT_CLS_BY_NAME.get(tool_name, CustomToolCallHookEvent)
    return cls(
        tool_call_id=tool_call_id, tool_name=tool_name, args=args,
        assistant_message=assistant_message, context=context,
    )

_TOOL_CALL_EVENT_CLS_BY_NAME = {
    "bash": BashToolCallHookEvent, "read": ReadToolCallHookEvent,
    "edit": EditToolCallHookEvent, "write": WriteToolCallHookEvent,
    "grep": GrepToolCallHookEvent, "find": FindToolCallHookEvent,
    "ls": LsToolCallHookEvent,
}
```

Same `_TOOL_RESULT_EVENT_CLS_BY_NAME` mapping for `_after_tool_call_bridge`.

### C.3 New `@overload`s on `ExtensionAPI.on("tool_call", ...)` / `on("tool_result", ...)`

Sprint 5b adds 14 narrowed overloads (7 tool-typed × 2 events) so pyright narrows handler payloads. Default `on("tool_call", ...)` overload stays (generic `ToolCallHandler`); narrowed variants live below.

Practical signature: a handler subscribed via `on("tool_call", h)` receives the base `ToolCallHookEvent`; runtime narrow via `isinstance(event, BashToolCallHookEvent)` or `is_tool_call_event_type("bash", event)`. Static narrowing via subscribing to `on(BashToolCallHookEvent, h)` is **NOT** introduced — that diverges from Pi's `on("tool_call", h: Handler<ToolCallEvent, ...>)` and would create two parallel surfaces. Pi narrows in the *handler body* via `isToolCallEventType` switching; Aelix mirrors this.

### C.4 Backward compatibility

- `ToolCallHookEvent` stays as a concrete base class (constructible). Existing tests that build `ToolCallHookEvent(tool_name="x", ...)` directly keep passing.
- `isinstance(evt, ToolCallHookEvent)` continues to match all 8 subclasses.
- `_reducer_tool_call` / `_reducer_tool_result` operate on the base class — no reducer changes.

### C.5 Pi-parity drift fixture

NEW `tests/pi_parity/fixtures/pi_tool_call_event_variants_734e08e.json`:
```json
{
  "tool_call_event_variants": [
    "BashToolCallEvent", "ReadToolCallEvent", "EditToolCallEvent",
    "WriteToolCallEvent", "GrepToolCallEvent", "FindToolCallEvent",
    "LsToolCallEvent", "CustomToolCallEvent"
  ],
  "tool_result_event_variants": [
    "BashToolResultEvent", "ReadToolResultEvent", "EditToolResultEvent",
    "WriteToolResultEvent", "GrepToolResultEvent", "FindToolResultEvent",
    "LsToolResultEvent", "CustomToolResultEvent"
  ],
  "narrow_helpers": [
    "isBashToolResult", "isReadToolResult", "isEditToolResult",
    "isWriteToolResult", "isGrepToolResult", "isFindToolResult",
    "isLsToolResult", "isToolCallEventType"
  ],
  "_source": "packages/coding-agent/src/core/extensions/types.ts:771-940",
  "_sha": "734e08edf82ff315bc3d96472a6ebfa69a1d8016"
}
```

Closure pin `test_phase_3_2_strict_superset.py` (new) loads this fixture and asserts each Pi variant has an Aelix sibling (snake_case rename ok: `BashToolCallEvent` → `BashToolCallHookEvent`).

---

## §D — ExtensionCommandContext (partial, 4 of 6)

### D.1 New class

```python
# packages/aelix-coding-agent/src/aelix_coding_agent/extensions/command_context.py — NEW

class ExtensionCommandContext(ExtensionContext):
    """Pi parity ``ExtensionCommandContext`` (types.ts:333-364).

    Pi extends ExtensionContext with 6 lifecycle methods exposed to slash
    command handlers via ``aelix.register_command(name, handler=...)``.

    Sprint 5b lands 4 of 6 (P-35):
    - ``wait_for_idle`` → wraps ``AgentHarness.wait_for_idle``
    - ``fork`` → wraps ``JsonlSessionRepo.fork``
    - ``navigate_tree`` → wraps ``AgentHarness.navigate_tree``
    - ``reload`` → wraps ``AgentHarness.reload_resources``

    Deferred to Phase 5 (CLI lifecycle):
    - ``new_session`` (needs ``SessionManager.replaceSession`` port)
    - ``switch_session`` (same)
    """

    def __init__(self, runtime, *, harness: AgentHarness, repo: JsonlSessionRepo | None = None, **kwargs):
        super().__init__(runtime, **kwargs)
        object.__setattr__(self, "_harness", harness)
        object.__setattr__(self, "_repo", repo)

    async def wait_for_idle(self) -> None:
        await object.__getattribute__(self, "_harness").wait_for_idle()

    async def fork(
        self,
        source: JsonlSessionMetadata,
        options: ForkOptions,
    ) -> Session:
        repo = object.__getattribute__(self, "_repo")
        if repo is None:
            raise ExtensionError("invalid_state", "fork() requires JsonlSessionRepo binding")
        return await repo.fork(source, options)

    async def navigate_tree(
        self, target_id: str | None, options: NavigateTreeOptions | None = None
    ) -> NavigateTreeResult:
        return await object.__getattribute__(self, "_harness").navigate_tree(target_id, options)

    async def reload(self) -> None:
        await object.__getattribute__(self, "_harness").reload_resources()

    async def new_session(self, options=None) -> None:
        raise ExtensionError(
            "invalid_state",
            "ExtensionCommandContext.new_session is deferred to Phase 5 CLI lifecycle (ADR-0033 successor).",
        )

    async def switch_session(self, target, options=None) -> None:
        raise ExtensionError(
            "invalid_state",
            "ExtensionCommandContext.switch_session is deferred to Phase 5 CLI lifecycle (ADR-0033 successor).",
        )
```

### D.2 Wiring

`ExtensionAPI.register_command` already exists (Sprint 5a). Sprint 5b only adds the construction site: when the CLI dispatcher (`cli/repl.py`) invokes a command handler, it builds an `ExtensionCommandContext` (not plain `ExtensionContext`) and passes it through. The minimal CLI in §B.2 has a single command `/reload` so far; richer slash-command dispatch is OK to defer to a follow-on but the class must land in 5b to keep the surface complete.

### D.3 `dir(ExtensionCommandContext)` closure check

New `test_extension_command_context_full_surface.py` asserts all 6 method names exist (4 bound, 2 raise). Pi-parity fixture `pi_extension_command_context_methods_734e08e.json` pinned with the 6 Pi names.

---

## §E — Sprint 5b runtime ergonomics fixes (W4 MAJORS from Sprint 5a)

### E.1 `_action_get_session_name` sync cache (Pi `cachedSessionName`)

Current (`core.py:1421-1438`) probes `asyncio.get_running_loop()` and returns `None` silently when called inside an active loop with no settled future. **Fix:**

```python
class AgentHarness:
    def __init__(self, options):
        # ...existing init...
        self._cached_session_name: str | None = None  # F-5b-1
        # On session attach OR set_session_name, refresh the cache.

    async def _refresh_session_name_cache(self) -> None:
        if self._session is not None:
            self._cached_session_name = await self._session.get_session_name()

    def _action_get_session_name(self) -> str | None:
        # Sync: read the cache. Pi parity (cachedSessionName).
        return self._cached_session_name

    def _action_set_session_name(self, name: str) -> None:
        if self._session is None:
            raise AgentHarnessError("invalid_state", ...)
        # Update cache immediately so subsequent get_session_name() reflects the new value.
        self._cached_session_name = name
        task = asyncio.get_event_loop().create_task(
            self._session.append_session_name(name)
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
```

Call `_refresh_session_name_cache()` once when session is attached (from `__init__` if session present, otherwise on the first `bootstrap()` call). This eliminates the `asyncio.run` fallback and the silent-None problem.

### E.2 Fire-and-forget GC pinning

Add `self._pending_tasks: set[asyncio.Task] = set()` to `AgentHarness.__init__`. Replace all bare `loop.create_task(...)` sites in `_action_set_*` (lines 1417, 1448, 1474) with:

```python
task = loop.create_task(coro)
self._pending_tasks.add(task)
task.add_done_callback(self._pending_tasks.discard)
```

This guards against asyncio's "Task was destroyed but it is pending!" warning + ensures GC retention. Same pattern used in `_compact_action` closure (`core.py:1316-1336`).

### E.3 `asyncio.run` fallback removal

`_action_set_session_name` / `_action_set_label` / `_action_set_thinking_level` (lines 1419-1421, 1451-1453, 1476-1478) fall back to `asyncio.run(...)` outside a loop. **Issue:** `asyncio.run` MUST NOT be called from any context where a loop is already running on another thread — it constructs a fresh loop. In CI test seams this masks bugs.

**Fix:** Adopt `asyncio.new_event_loop().run_until_complete(...)` with explicit `loop.close()` in a `finally`, OR raise `ExtensionError("invalid_state", "Sync extension action called outside event loop")` so callers learn the boundary. Recommend the second — Sprint 5b enforces "extension actions called from sync code REQUIRE an active loop" because the cache fix removes the awkward sync-read case entirely.

```python
def _ensure_loop(self) -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_running_loop()
    except RuntimeError as exc:
        raise AgentHarnessError(
            "invalid_state",
            "Extension action requires an active asyncio event loop; "
            "call from within `asyncio.run(main())` or `await harness.<method>(...)`.",
        ) from exc
```

### E.4 Tests

`tests/test_harness_ergonomics_v3.py` (new ~120 LOC):
- `test_session_name_cache_returns_after_set`
- `test_pending_tasks_set_drains_on_dispose`
- `test_no_running_loop_raises_invalid_state`
- `test_compact_action_task_pinned`

---

## §F — Wire 4 throwing stubs from Sprint 5a

### F.1 `append_entry` — Session direct binding

Sprint 5a `_action_append_entry` is a throwing stub. Sprint 5b binds:

```python
def _action_append_entry(self, custom_type: str, data: Any = None) -> None:
    if self._session is None:
        raise AgentHarnessError("invalid_state", "append_entry requires options.session")
    loop = self._ensure_loop()  # E.3 helper
    task = loop.create_task(self._session.append_custom_entry(custom_type, data))
    self._pending_tasks.add(task)
    task.add_done_callback(self._pending_tasks.discard)
```

### F.2 `send_message` / `send_user_message` — queue routing via existing harness API

Pi semantics (`types.ts:1178-1192`):
- `send_message(msg, *, trigger_turn=False, deliver_as=None)` → if `deliver_as=="next_turn"` enqueue to next_turn queue; if `"steer"` call `harness.steer`; if `"follow_up"` call `harness.follow_up`. `trigger_turn=True` AND idle → call `harness.prompt(msg)`.
- `send_user_message(content, *, deliver_as=None)` → text-extracted form of `send_message`.

```python
def _action_send_message(
    self, message: Any, *, trigger_turn: bool = False,
    deliver_as: str | None = None,
) -> None:
    loop = self._ensure_loop()
    if deliver_as == "steer":
        task = loop.create_task(self.steer(_extract_text(message)))
    elif deliver_as == "follow_up":
        task = loop.create_task(self.follow_up(_extract_text(message)))
    elif deliver_as == "next_turn" or trigger_turn is False:
        # Enqueue into existing next_turn queue (Sprint 3b)
        self._next_turn_queue.append(_to_agent_message(message))
    if trigger_turn and self._phase == "idle":
        task = loop.create_task(self.prompt(_extract_text(message)))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
```

`send_user_message` is a simple wrapper that always builds a `UserMessage`.

### F.3 `get_commands` — registry view

Sprint 5b binds via a per-runtime command registry. Each `register_command` push (Sprint 5a `Extension.commands`) is harvested:

```python
def _action_get_commands(self) -> list[SlashCommandInfo]:
    out: list[SlashCommandInfo] = []
    for ext in self._options.extensions:
        for name, cmd in ext.commands.items():
            out.append(SlashCommandInfo(
                name=name, description=cmd.description, source=cmd.source,
            ))
    return out
```

Pi parity ✓ — Pi also enumerates per-extension registered commands (types.ts:1216-1221).

### F.4 `shutdown` real binding (Sprint 5a default raised)

Sprint 5b CLI loop installs `_shutdown` action that closes stdin and signals abort:

```python
def _shutdown_cli(self) -> None:
    self._mark_abort()
    # CLI's main loop checks self._stop_requested.
    self._stop_requested = True
```

Wired into `_make_context()` only when CLI is active. Outside CLI, the Sprint 5a default `invalid_state` raise stays.

---

## §G — Tests (~+780 LOC, ~16 new files)

| Test file | LOC | Coverage |
|---|---:|---|
| `tests/tools/test_bash_tool.py` | 110 | input validation; cwd check; timeout; abort; exit code; stdout/stderr capture; truncation; BashOperations swap |
| `tests/tools/test_read_tool.py` | 90 | path/offset/limit; absolute path; line numbering; binary image stub; missing file; permission denied |
| `tests/tools/test_edit_tool.py` | 110 | single edit; multiple edits; oldText must be unique; overlap rejected; preserves BOM + line endings; concurrent edits serialized by file-mutation-queue |
| `tests/tools/test_write_tool.py` | 60 | new file; mkdir parents; overwrites; file-mutation-queue interaction |
| `tests/tools/test_grep_tool.py` | 80 | regex; literal; case-insensitive; glob filter; context lines; limit; rg-fallback to Python regex |
| `tests/tools/test_find_tool.py` | 60 | glob pattern; path scoping; limit; fd-fallback to pathlib |
| `tests/tools/test_ls_tool.py` | 50 | dir listing; sorted; default limit; missing dir error |
| `tests/tools/test_create_collections.py` | 40 | `create_coding_tools(cwd)` returns 4; `create_read_only_tools` returns 4; `create_all_tools` returns dict of 7 |
| `tests/test_input_emit.py` | 90 | handled short-circuits; transform mutates text + images; continue passthrough; source=interactive/rpc/extension; no handlers = noop |
| `tests/test_user_bash_emit.py` | 110 | `!cmd` emit + execution; `!!cmd` exclude_from_context; extension supplies operations; extension supplies result; session append for `!` only |
| `tests/test_resources_discover_emit.py` | 70 | startup emit on bootstrap; reload emit on reload_resources; dedup across handlers; merge into AgentState.resources |
| `tests/test_extension_command_context.py` | 80 | wait_for_idle delegate; fork delegate; navigate_tree delegate; reload delegate; new_session raises invalid_state; switch_session raises invalid_state |
| `tests/test_harness_ergonomics_v3.py` | 120 | E.4 list (cache, pending tasks, no-loop raises, compact task pinned) |
| `tests/test_tool_call_event_variants.py` | 90 | 7 typed variants + 1 custom built; reducer dispatch routes correctly; is_tool_call_event_type narrows; ToolResult variants symmetric |
| `tests/pi_parity/test_phase_3_2_strict_superset.py` | 150 | Closure pin: 7 tools registered; 3 emits land; ToolCallEvent 8 variants; ExtensionCommandContext 4 bound + 2 raise; DEFERRED_ALLOWLIST drops 3 events; Sprint 5b ergonomics fixes regression |
| `tests/pi_parity/fixtures/pi_tool_call_event_variants_734e08e.json` | 30 | C.5 fixture |
| `tests/pi_parity/fixtures/pi_coding_tools_734e08e.json` | 20 | 7 tool names + descriptions snapshot |
| Updated `tests/pi_parity/test_phase_3_1_strict_superset.py` | — | DEFERRED_ALLOWLIST guard rewritten — assert input/user_bash/resources_discover NO LONGER in allowlist + closure pin moved to phase_3_2 |
| Updated `tests/pi_parity/test_phase_2_1_strict_superset.py` | — | Drop 3 entries from `DEFERRED_ALLOWLIST` per forward-compat clause |

**Test count delta:** ~+780 LOC across 18 new/updated files; ~95 new test cases; closure pin adds ~30 assertions.

---

## §H — ADR amendments + NEW ADRs

### H.1 NEW ADR-0042 — "Built-in Coding Tools + 3 Event Emit Sites + Minimal CLI Loop"

Status: Accepted (Sprint 5b shipped).

Sections:
1. **1st-principle invariant** — Pi-parity strict superset.
2. **Catalogue** — 7 tools with Pi cites + Aelix module paths (table from §A.2).
3. **3 event emit sites** — `input` (`AgentHarness.prompt()` head), `user_bash` (CLI `!/!!` parser), `resources_discover` (`AgentHarness.discover_resources` + `reload_resources`).
4. **Minimal CLI loop** — Aelix-additive minimal REPL at `cli/repl.py`; full TUI is Phase 5 (ADR-0033 successor).
5. **Aelix-additive `coding_tools_extension(cwd)`** — convenience wrapper that registers all 7 tools via ExtensionAPI; documented as additive (Pi does not have an analog because Pi callers always pass tools via `AgentHarnessOptions.tools`).
6. **Decision** — 4 ExtensionCommandContext methods land (wait_for_idle / fork / navigate_tree / reload); 2 deferred (new_session / switch_session → Phase 5).
7. **Deferred allowlist** — empty for 3.2 scope. Phase 4 / Phase 5 entries inherited.
8. **Forward-compat clause** — mirrors ADR-0041 §"Forward-compat".

### H.2 NEW ADR-0043 — "Tool-Typed ToolCallEvent Variants"

Status: Accepted.

Sections:
1. **Context** — Pi `types.ts:771-940` ships 8 typed `ToolCallEvent` variants + 8 `ToolResultEvent` variants + `isToolCallEventType` narrow helper. P-31 confirms.
2. **Decision** — Add 8 dataclass subclasses on `ToolCallHookEvent` + 8 on `ToolResultHookEvent`. Base classes stay constructible (back-compat). Construction site `_make_tool_call_event` factory dispatches by tool_name; CustomToolCallHookEvent is the fallback for non-built-in tool names. `is_tool_call_event_type(name, event)` mirrors Pi narrow helper.
3. **Why subclasses, not Union[type]** — Python `match event:` + `isinstance` is the idiomatic narrowing; preserves `isinstance(evt, ToolCallHookEvent)` for existing handlers.
4. **Aelix-additive divergence** — `args` stays `dict[str, Any]` (no `BashToolInput` TypedDict). Pi's TS narrows the `input: BashToolInput` field via the schema generic. Python's type system can't propagate Static<BashSchema> → narrowed dict. Documented as Aelix-additive divergence; helpers `as_bash_args(event)` (NEW, returns the dict typed as `BashArgs` TypedDict) provide opt-in narrowing.
5. **Test fixtures** — pi_tool_call_event_variants_734e08e.json.

### H.3 NEW ADR-0044 — "Phase 3 Strict Superset Closure" (mirrors ADR-0039/0040/0041)

Status: Accepted (Sprint 5b shipped).

Sections:
1. **1st-principle invariant** — Aelix Phase 3 is a strict Pi-parity superset of Pi's `aelix-coding-agent` package. Every Pi extension method, hook event, built-in tool, and ExtensionCommandContext method in Phase 3 scope has a corresponding binding or explicit deferral.
2. **Phase 3 findings roster (P-21 through P-36)** — full table inherited from ADR-0041 P-21~P-28 + Sprint 5b P-31~P-36.
3. **Closure** — Phase 3 ADRs all Accepted: 0017 (catalogue v3 amendment), 0028 (auto-discovery), 0041 (ExtensionAPI surface), 0042 (built-in tools + emits), 0043 (tool-typed events), 0044 (this).
4. **Durable regression guard** — `tests/pi_parity/test_phase_3_2_strict_superset.py` is the binding mechanization.
5. **Deferred allowlist (post-5b)** — empty for Phase 3 scope. Phase 4 / Phase 5 entries (ExtensionContext.ui, ModelRegistry full impl, new_session/switch_session) live in their owning ADRs.
6. **Forward-compat clause** — same as ADR-0039.

### H.4 Amendments

- **ADR-0041** — closure pin update: Sprint 5b shipped; 3 events left DEFERRED_ALLOWLIST; 4 throwing stubs bound; 4 ExtensionCommandContext methods land. Add §"Sprint 5b verification" with P-31~P-36 references.
- **ADR-0017** — Phase 3.2 subsection: emit sites added (input/user_bash/resources_discover); 8 ToolCallEvent variants registered; SlashCommandInfo populated; allowlist drops 3 entries.
- **ADR-0028** — extension context binding update (4 stubs now wired). No structural change.
- **ADR-0033** — note that ExtensionUIContext + new_session/switch_session remain deferred to Phase 5.
- **README index** — add 0042/0043/0044 entries.
- **ADR-0034** — no change; SHA pin stays.
- **ADR-0019** v3 — extends `error_mode` overload to 8 new tool-typed `on()` overloads (if §C.3 ships narrowed variants — recommend NOT, see C.3).

---

## §I — Acceptance checklist

1. `aelix_coding_agent.tools` package lands with 7 tool modules + `_truncate` + `_path_utils` + `_file_mutation_queue`.
2. `create_coding_tools(cwd)` / `create_read_only_tools(cwd)` / `create_all_tools(cwd)` collection factories exposed.
3. `aelix_coding_agent.builtin.coding_tools_extension(cwd)` factory registers all 7 via ExtensionAPI.
4. `AgentHarness.prompt()` emits `input` before `before_agent_start`; `handled` short-circuits; `transform` mutates text/images.
5. `AgentHarness.discover_resources()` + `reload_resources()` emit `resources_discover` with reason="startup"/"reload".
6. `cli/repl.py` minimal REPL parses `!/!!` and emits `user_bash`; `/reload` triggers `reload_resources`.
7. 8 ToolCallEvent variants + 8 ToolResultEvent variants registered; construction sites dispatch correctly.
8. `is_tool_call_event_type(name, event)` narrow helper exported.
9. `ExtensionCommandContext` class lands with 4 bound + 2 raising methods.
10. Sprint 5b ergonomics fixes: session_name cache, _pending_tasks GC pinning, asyncio.run removed, `_ensure_loop` helper.
11. 4 throwing-stub bindings wired: send_message, send_user_message, append_entry, get_commands; shutdown CLI binding.
12. `tests/pi_parity/test_phase_3_2_strict_superset.py` passes.
13. `tests/pi_parity/test_phase_3_1_strict_superset.py` deadline guard passes (today ≤ 2026-06-14).
14. `tests/pi_parity/test_phase_2_1_strict_superset.py` `DEFERRED_ALLOWLIST` drops 3 entries.
15. ADR-0042 / ADR-0043 / ADR-0044 written and Accepted; ADR-0041 amended.
16. Pi-parity drift fixtures match.
17. Existing tests pass; pyright spike ≤ 8 errors; ruff clean.

---

## §J — Out of scope (Phase 4 / Phase 5)

- Real provider streaming (ADR-0038, Phase 4)
- OAuth flow (Phase 4)
- ADR-0020 RPC mode (Phase 4)
- Full TUI / interactive-mode.ts (5528 LOC) → Phase 5 (ADR-0033 successor)
- ExtensionUIContext / has_ui=True → Phase 5
- `new_session` / `switch_session` ExtensionCommandContext methods → Phase 5
- MessageRenderer actual rendering → Phase 5
- KeyId shortcut dispatch → Phase 5
- Task #37 pyright 142 cleanup → out of band
- Image resize backend (Pi uses `sharp`; Aelix uses PIL or defers) → Phase 4 multimodal
- Slash command full registry + autocomplete → can partial in 5b but rich UI lives in Phase 5

---

## §K — Implementation order

1. §A.1-A.4 — port 7 tools + Operations Protocols (largest LOC; can parallelize across executors)
2. §C.1-C.5 — typed ToolCallEvent variants + tests (additive subclasses)
3. §A.5 — builtin/coding_tools.py extension wrapper
4. §B.3 — `discover_resources` / `reload_resources` (smallest)
5. §B.1 — `input` emit at `AgentHarness.prompt()` head
6. §F.1-F.4 — wire 4 throwing stubs
7. §E.1-E.4 — Sprint 5b ergonomics fixes
8. §D.1-D.3 — ExtensionCommandContext
9. §B.2 — minimal CLI loop + user_bash emit
10. §G — tests + Pi-parity drift fixtures
11. §H.1-H.4 — ADR-0042 / 0043 / 0044 + amendments

End of binding spec.
