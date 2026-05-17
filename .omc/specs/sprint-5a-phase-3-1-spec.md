# Sprint 5a · Phase 3.1 — Extension Auto-Discovery + Full ExtensionAPI Surface + 3 Event Registration (BINDING SPEC)

Status: **Binding** (Architect READ-ONLY)
Author: Architect (Opus)
Date: 2026-05-17
Pi pin (ADR-0034): `badlogic/pi-mono@734e08edf82ff315bc3d96472a6ebfa69a1d8016`
Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."**

---

## §0 — Sub-sprint scoping + P-21~P-28 surprises

### 0.1 Sub-sprint split (CONFIRMED)

| Sub-sprint | Scope | ADRs | LOC est |
|---|---|---|---|
| **5a (this)** | Directory-scan discovery (Pi primary) + entry_points (Aelix additive) + full ExtensionAPI 48 methods + ExtensionContext 14 fields + 3 event REGISTRATION (input/user_bash/resources_discover) | ADR-0028 Accepted, ADR-0017 amended, ADR-0041 NEW closure | ~550 prod + ~1,040 test |
| **5b** | 7 built-in coding tools (bash/read/edit/write/grep/find/ls) + 3 event EMIT sites + CLI loop | ADR-0042 NEW, ADR-0043 NEW | ~750 prod + ~600 test |

**Structural correction:** original spec proposed 5a emit wiring; Pi emit sites all live in `agent-session.ts` (CLI loop) which 5b owns. 5a registers types + adds to DEFERRED_ALLOWLIST with ADR-0042 owner (mirrors Sprint 3a session_* pattern).

### 0.2 P-21~P-28 findings (Draft ADR drift, as predicted)

| ID | Pi truth | Aelix today | Drift |
|---|---|---|---|
| **P-21** | Pi discovery: 3-tier **directory scan** (`cwd/.pi/extensions/`, `~/.pi/extensions/`, explicit). NO `entry_points` (JS has none). Source: `extensions/loader.ts:520-620` | Explicit-only loader | **ADR-0028 Draft is INVERTED** — treats entry_points as primary. Correction: directory-scan PRIMARY (Pi parity), entry_points ADDITIVE (Aelix-additive). |
| **P-22** | Pi ExtensionAPI = **48 methods** (29 `on()` + 19 non-event). `types.ts:1064-1218` | 8 methods | **15+ missing**: `register_command/shortcut/message_renderer/provider/unregister_provider`, `send_message/user_message`, `append_entry`, `set/get_session_name`, `set_label`, `exec`, `get_all_tools/commands`, `set_model`, `set/get_thinking_level`, `events` property |
| **P-23** | Pi ExtensionContext = **14 fields**. `types.ts:280-310` | 5 fields | **8 non-UI missing**: `has_ui`, `session_manager`, `model_registry`, `signal`, `has_pending_messages`, `shutdown`, `get_context_usage`, `compact` |
| **P-24** | `InputEvent` EXISTS at SHA. `types.ts:619-625`. Emit `agent-session.ts:987-988` | Not registered | Sprint 3a P-1 mis-classified as "wishlist only" — Pi DOES ship in `coding-agent` (just not `agent-core`). Phase 3 owns. |
| **P-25** | `UserBashEvent` EXISTS. `types.ts:602-609`. Emit `agent-session.ts:1403` | Not registered | Same — Phase 3 owns. |
| **P-26** | `ResourcesDiscoverEvent` EXISTS. `types.ts:512-517`. Emit `agent-session.ts:2055-2059` gated by `hasHandlers` | Not registered | Same. |
| **P-27** | Pi Extension = 7 collections + 2 metadata. `types.ts:1538-1547` | 4 collections + cleanups | Missing: `message_renderers`, `commands`, `shortcuts`, `source_info`, `resolved_path`. `cleanups` is Aelix-additive (Pi uses EventBus). |
| **P-28** | Pi ExtensionRuntime = **15 action methods**. `loader.ts:140-170` | 3 actions | 12 missing actions |

---

## §A — Extension auto-discovery

### A.1 Pi-truth directory-scan (PRIMARY)

Port Pi `discoverAndLoadExtensions()` verbatim:

```python
async def discover_and_load_extensions(
    configured_paths: list[str | Path | ExtensionFactory],
    *,
    cwd: Path,
    agent_dir: Path | None = None,
) -> LoadExtensionsResult:
    """Pi-parity 3-tier discovery."""
    all_paths: list[Any] = []
    seen: set[Path] = set()

    # 1. Project-local: cwd/.aelix/extensions/
    _add_discovered(all_paths, seen, _discover_in_dir(cwd / ".aelix" / "extensions"))

    # 2. Global: ~/.aelix/extensions/
    home_dir = agent_dir or (Path.home() / ".aelix")
    _add_discovered(all_paths, seen, _discover_in_dir(home_dir / "extensions"))

    # 3. Explicit configured paths
    for p in configured_paths:
        ...

    # 4. Aelix-additive: entry_points (loaded LAST)
    _add_entry_points(all_paths, group="aelix.extensions")

    return await load_extensions(all_paths, cwd=cwd)
```

### A.2 `_discover_in_dir()` per Pi `loader.ts:481-518`

- For each entry in dir:
  - `*.py` file → add directly
  - Subdirectory: check `pyproject.toml [tool.aelix.extensions]` array OR `__init__.py` OR skip
- No recursion beyond one level (Pi parity).

### A.3 entry_points (Aelix-additive)

```python
from importlib.metadata import entry_points
for ep in entry_points(group="aelix.extensions"):
    try:
        factory = ep.load()
        all_paths.append(factory)
    except Exception as exc:
        result.errors.append(ExtensionLoadError(path=f"entry_point:{ep.name}", error=str(exc)))
```

### A.4 Priority (Pi parity + Aelix-additive)

1. Project-local directory (wins on tool name collision)
2. Global directory
3. Explicit configured paths
4. entry_points (Aelix-additive, last)

Dedup by resolved absolute path.

### A.5 Error containment

Per-extension try/except, accumulate into `errors` list, never abort wave.

---

## §B — Full ExtensionAPI surface (15 new methods)

Land 15 methods (Pi `types.ts:1064-1218`). Each delegates to `_ExtensionRuntime.actions` via throwing-stub pattern.

| # | Pi method | Aelix signature | Real binding (5a) |
|---|---|---|---|
| 1 | `registerCommand` | `register_command(name, *, description=None, handler)` | mutator on Extension.commands |
| 2 | `registerShortcut` | `register_shortcut(key, *, description=None, handler)` | mutator on Extension.shortcuts |
| 3 | `registerMessageRenderer` | `register_message_renderer(custom_type, renderer)` | mutator on Extension.message_renderers |
| 4 | `registerProvider` | `register_provider(name, config)` | queue into `runtime.pending_provider_registrations` (Phase 4 flush) |
| 5 | `unregisterProvider` | `unregister_provider(name)` | dequeue/queue |
| 6 | `sendMessage` | `send_message(message, *, trigger_turn=False, deliver_as=None)` | throwing stub (5b CLI/RPC) |
| 7 | `sendUserMessage` | `send_user_message(content, *, deliver_as=None)` | throwing stub (5b) |
| 8 | `appendEntry` | `append_entry(custom_type, data=None)` | throwing stub (5b Session integration) |
| 9 | `setSessionName` | `set_session_name(name)` | delegates to `Session.set_session_name` |
| 10 | `getSessionName` | `get_session_name() -> str \| None` | delegates to `Session.get_session_name` |
| 11 | `setLabel` | `set_label(entry_id, label)` | delegates to `Session.append_label` |
| 12 | `exec` | `exec(command, args, *, cwd=None, env=None, timeout_ms=None) -> ExecResult` | direct subprocess.run port from Pi `exec.ts execCommand` |
| 13 | `getAllTools` | `get_all_tools() -> list[ToolInfo]` | delegates to AgentHarness._tools snapshot |
| 14 | `getCommands` | `get_commands() -> list[SlashCommandInfo]` | throwing stub returning `[]` (5b) |
| 15 | `setModel` | `set_model(model) -> bool` | delegates to AgentHarness.set_model |
| 16 | `getThinkingLevel` | `get_thinking_level()` | reads AgentState.thinking_level |
| 17 | `setThinkingLevel` | `set_thinking_level(level)` | delegates to AgentHarness.set_thinking_level |
| 18 | `events` property | `events: EventBus` | shared per-runtime EventBus instance (port Pi `event-bus.ts`) |

**Aelix-additive preservations** (document in ADR-0041):
- `add_cleanup()` stays — no Pi equivalent (Pi uses events: EventBus)
- `error_mode` overload param (ADR-0019 v3)
- 28 `@overload`s of `on()` (pyright narrowing)

---

## §C — Full ExtensionContext (8 missing non-UI fields)

| # | Pi field | Aelix | Real binding (5a) |
|---|---|---|---|
| 1 | `hasUI: boolean` | `has_ui: bool` property | constant `False` |
| 2 | `sessionManager` | `session_manager: ReadonlySessionManager` property | wires to AgentHarness._session; raises `ExtensionError("invalid_state")` if None |
| 3 | `modelRegistry` | `model_registry: ModelRegistry` property | NEW minimal stub class (5a) — only register/unregister; full impl Phase 4 |
| 4 | `signal` | `signal: AbortSignalLike \| None` property | wires to harness `_current_abort_signal` |
| 5 | `hasPendingMessages` | `has_pending_messages() -> bool` | reads queue lengths |
| 6 | `shutdown` | `shutdown() -> None` | throwing stub (5b CLI) |
| 7 | `getContextUsage` | `get_context_usage() -> ContextUsage \| None` | reads from last MessageEndHookEvent cost |
| 8 | `compact(options?)` | `compact(*, custom_instructions=None, on_complete=None, on_error=None)` | wraps `AgentHarness.compact()` fire-and-forget via `asyncio.create_task` |

ExtensionCommandContext OUT of 5a (6 methods deferred to 5b/Phase 5).

---

## §D — 3 new events (P-24/P-25/P-26)

### D.1 HookEventName extension (28 → 31)

Add `"input"`, `"user_bash"`, `"resources_discover"` to Literal.

### D.2 Event dataclasses

**`InputHookEvent`** (Pi `types.ts:619-625`):
```python
@dataclass(frozen=True)
class InputHookEvent:
    text: str = ""
    images: list[ImageContent] | None = None
    source: Literal["interactive", "rpc", "extension"] = "interactive"
    type: Literal["input"] = "input"
```

**`InputResult`** (3 variants):
```python
@dataclass(frozen=True)
class InputContinue: action: Literal["continue"] = "continue"

@dataclass(frozen=True)
class InputTransform:
    text: str
    images: list[ImageContent] | None = None
    action: Literal["transform"] = "transform"

@dataclass(frozen=True)
class InputHandled: action: Literal["handled"] = "handled"

InputResult = InputContinue | InputTransform | InputHandled | None
```

Reducer: first `"handled"` short-circuits; `"transform"` chains; `"continue"` passthrough.

**`UserBashHookEvent`** (Pi `types.ts:602-609`):
```python
@dataclass(frozen=True)
class UserBashHookEvent:
    command: str = ""
    exclude_from_context: bool = False
    cwd: str = ""
    type: Literal["user_bash"] = "user_bash"
```

**`UserBashResult`**:
```python
@dataclass(frozen=True)
class UserBashResult:
    operations: BashOperations | None = None  # minimal stub Protocol
    result: BashResult | None = None
```

**`ResourcesDiscoverHookEvent`** (Pi `types.ts:512-517`):
```python
@dataclass(frozen=True)
class ResourcesDiscoverHookEvent:
    cwd: str = ""
    reason: Literal["startup", "reload"] = "startup"
    type: Literal["resources_discover"] = "resources_discover"
```

**`ResourcesDiscoverResult`**:
```python
@dataclass(frozen=True)
class ResourcesDiscoverResult:
    skill_paths: list[str] | None = None
    prompt_paths: list[str] | None = None
    theme_paths: list[str] | None = None
```

Reducer: collects + dedups across handlers (Pi `agent-session.ts:2059-2068`).

### D.3 `@overload`s

3 new `@overload`s on `ExtensionAPI.on()`. `__all__` exports add 3 events + 3 result types + InputContinue/Transform/Handled.

### D.4 Emit-site policy — DEFERRED to 5b

Add 3 names to `DEFERRED_ALLOWLIST`:
```python
"input": "ADR-0042 (Sprint 5b CLI loop)",
"user_bash": "ADR-0042 (Sprint 5b CLI loop)",
"resources_discover": "ADR-0042 (Sprint 5b CLI loop)",
```

Mirror Sprint 3a session_* pattern (register-without-emit).

---

## §E — Tests (~+1040 LOC, ~14 new files)

| Test file | Coverage |
|---|---|
| `test_extension_discovery.py` | discover_and_load_extensions: project-local/global/both/dedup/configured/errors/pyproject.toml/init.py |
| `test_extension_discovery_entry_points.py` | entry_points loading, priority vs directory-scan, error containment |
| `test_extension_api_full_surface.py` | 48 methods present; throwing stubs raise pre-bind |
| `test_extension_context_full_fields.py` | 14 fields accessible; has_ui=False; stale check |
| `test_extension_runtime_actions_v2.py` | 15-method dataclass; bind_core rebinds |
| `test_hook_input_event.py` | reducer transform/handled/continue |
| `test_hook_user_bash_event.py` | operations/result; exclude_from_context |
| `test_hook_resources_discover_event.py` | reducer collect+dedup |
| `tests/pi_parity/test_phase_3_1_strict_superset.py` | NEW closure pin: 31 names, 3 deferred entries match 5b owner |
| `tests/pi_parity/test_extension_api_method_count.py` | drift fixture vs pi_extension_api_methods_734e08e.json |
| `tests/pi_parity/test_extension_context_field_count.py` | drift fixture vs pi_extension_context_fields_734e08e.json |
| `tests/pi_parity/fixtures/pi_extension_api_methods_734e08e.json` | 48 method names snapshot |
| `tests/pi_parity/fixtures/pi_extension_context_fields_734e08e.json` | 14 field names snapshot |
| Existing test updates | test_extension_api.py, test_overloads_extension_api.py, test_extension_loader.py extensions |

---

## §F — ADR amendments + NEW ADR-0041

### F.1 ADR-0028 Draft → Accepted (structural correction)
- Rewrite Decision section
- Status: `Accepted (Sprint 5a / Phase 3.1.1 shipped — directory scan PRIMARY (Pi parity), entry_points ADDITIVE)`
- Add §"Sprint 5a verification" with P-21 finding
- Acknowledge Draft framing was inverted

### F.2 ADR-0017 amendment
Add §"Phase 3.1 event additions (Sprint 5a)":
- 28 → 31 names
- 3 new events Pi-cited
- DEFERRED_ALLOWLIST extension with ADR-0042 owner
- Amend Sprint 3a P-1 misclassification note

### F.3 NEW ADR-0041 "Phase 3.1 ExtensionAPI Full Surface Closure"
Mirror ADR-0039/0040 pattern. Sections:
- 1st-principle invariant
- P-21~P-28 roster
- Closure: ExtensionAPI 48/48, ExtensionContext 14/14, 3 events registered
- Deferred-binding allowlist (5a stubs → 5b/Phase 4)
- Durable regression guard: test_phase_3_1_strict_superset.py
- Forward-compat clause
- **EXPLICIT 4-week time bound** on deferred events: if Sprint 5b doesn't ship within 4 weeks of Sprint 5a accepted commit, ADR-0041 auto-demotes to Draft

### F.4 ADR-0019 v3 minor amendment
error_mode overload extends to 3 new events.

---

## §G — Acceptance checklist

1. discover_and_load_extensions lands with Pi-parity directory-scan + Aelix-additive entry_points
2. ExtensionAPI exposes 48 methods total
3. ExtensionContext exposes 14 non-UI fields
4. HookEventName contains 31 names; HOOK_RESULT_TYPES has 3 new entries
5. 3 new event dataclasses + 3 result types exported
6. 3 new @overloads on ExtensionAPI.on() with pyright narrowing
7. DEFERRED_ALLOWLIST extended with 3 events → ADR-0042
8. test_phase_3_1_strict_superset.py passes
9. Pi-parity drift fixtures match
10. ADR-0028 → Accepted with P-21 verification
11. ADR-0017 Phase 3 subsection added
12. ADR-0041 written and Accepted
13. All existing tests pass; pyright spike 8 errors; ruff clean
14. Echo example extended to demonstrate ≥1 new method

---

## §H — Out of scope (Sprint 5b)

- 7 built-in coding tools (bash/read/edit/write/grep/find/ls) — NEW ADR-0042
- Pi tool-typed ToolCallEvent variants (BashToolCallEvent etc.) — NEW ADR-0043
- input/user_bash/resources_discover EMIT sites
- ExtensionCommandContext (6 methods)
- Full BashOperations/BashResult types
- SlashCommandInfo registry
- MessageRenderer actual rendering (Phase 5)
- Full ModelRegistry (Phase 4 ADR-0038)
- KeyId/keyboard shortcut dispatch (Phase 5)
- ExtensionUIContext (Phase 5)

---

## §I — Implementation order

1. §D 3 hook events + result types + @overloads
2. §A discovery (directory-scan primary + entry_points additive)
3. §B 15 ExtensionAPI methods
4. §C 8 ExtensionContext fields
5. §E tests + Pi-parity drift fixtures
6. §F ADR amendments + ADR-0041

End of binding spec.
