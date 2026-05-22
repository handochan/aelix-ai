# 0093. Sprint 6h₇c Phase 5a-iii-γ — ADR-0087 P-380 Reload Primitives Closure

Status: Accepted (Sprint 6h₇c / Phase 5a-iii-γ / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₇b (ADR-0091 §E) shipped the `AgentHarness.reload()` 2-op
stub covering only Pi `agent-session.ts:2386` (`settingsManager.reload`)
+ Pi `:2402` (`extendResourcesFromExtensions("reload")`). Four of the
five ADR-0087 P-380 reload primitives stayed deferred at sprint close.

Sprint 6h₇c expands the stub into the full **7-op Pi parity chain**
(`agent-session.ts:2382-2413`) and lands the supporting primitives:

- `reset_api_providers()` module function (Pi `register-builtins.ts:400-403`).
- `ModelRegistry.reset()` naming alias for `refresh()` (Pi semantic identity).
- `flag_values` field + `get_flag_values` / `set_flag_value` primitives
  on `_ExtensionRuntime` + `ExtensionRunner` delegation (Pi
  `runner.ts:409-411`).
- `_rebuild_tool_registry()` extraction (partial port of Pi
  `_buildRuntime` — tool merge step only).
- `_emit_session_shutdown` / `_emit_session_start` private wrappers (Pi
  `runner.ts:177-189` + `agent-session.ts:2407`).

Two of the original five P-380 primitives stay deferred for binding
reasons (see §"Deferred items"):

- `_resourceLoader.reload()` — depends on ResourceLoader port.
- `_buildRuntime()` full extraction — ~200 LOC `__init__` refactor.

## Decision

### §B — `reset_api_providers()` module function + `ModelRegistry.reset()` alias

**`packages/aelix-ai/src/aelix_ai/api_registry.py`** — adds module-level
`reset_api_providers()` which calls the existing `clear_providers()`.
Aelix has NO module-level lazy provider cache (each provider package
registers itself via its own init / explicit `register_all()` call), so
the Aelix port is effectively a registry flush. The function exists for
Pi-parity API surface and future-proofing.

**`packages/aelix-coding-agent/src/aelix_coding_agent/model_registry.py`**
— adds `ModelRegistry.reset()` method that delegates to the existing
`refresh()`. Pi `agent-session.ts:2389` calls `modelRegistry.reset()`;
the alias keeps Pi-source citations clean. Aelix retains `refresh()`
for backward compatibility — both invoke `_load_models()`.

### §C — `flag_values` primitives on `_ExtensionRuntime` + `ExtensionRunner` delegation

**`packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py`**
`_ExtensionRuntime` class — adds:

- `flag_values: dict[str, bool | str]` field (default empty dict).
- `get_flag_values() -> dict[str, bool | str]` returning
  `dict(self.flag_values)` — shallow copy (Pi `Map` shallow-copy parity
  via `new Map(this.flagValues)`).
- `set_flag_value(name, value)` mutating the internal dict.

**`packages/aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py`**
`ExtensionRunner` class — adds:

- Optional `_runtime: _ExtensionRuntime | None` bridge field (default
  `None`).
- `get_flag_values()` delegating to runtime; returns `{}` when bridge is
  unwired.
- `set_flag_value(name, value)` delegating to runtime; silently drops
  the mutation when bridge is unwired.

`AgentHarness.__init__` wires the `_runtime` bridge alongside the
existing `_emit` / `_has_handlers` / `_invalidate_runtime` bridges.

**Sprint 6h₇c scope note** — flag values are NOT yet wired into the
reload round-trip (Pi snapshot → `_buildRuntime({flagValues})` restore).
6h₇c lands the primitives only; the restoration site lives in
`_buildRuntime`, which is itself a Phase 5b extraction target (§D).
`reload()` snapshots the dict but discards it.

### §D — `_rebuild_tool_registry()` extraction

**`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`** — extracts
the tool-merge logic from `AgentHarness.__init__` (former lines 509-516)
into a private method:

```python
def _rebuild_tool_registry(self) -> list[AgentTool]:
    """Pi parity (partial): tool merge step of agent-session.ts:_buildRuntime."""
    merged: dict[str, AgentTool] = {}
    for extension in self._extensions:
        for name, tool in extension.tools.items():
            merged.setdefault(name, tool)
    for tool in self._options.tools:
        merged[tool.name] = tool
    return list(merged.values())
```

The `__init__` call site reduces to `merged_tools = self._rebuild_tool_registry()`.
Application-supplied tools win over extension-registered ones with the
same name (D.1.13 M-9 — semantic unchanged); `setdefault` collects the
first extension occurrence per name; the options.tools loop overrides
on collision via direct assignment.

Only the merge logic is extracted this sprint. Extension runner re-create
+ active tool filter refresh + state assembly stay inline in `__init__`
(Phase 5b carry-forward).

### §E — `_emit_session_shutdown` + `_emit_session_start` wrappers

**`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`** — adds two
private async methods on `AgentHarness`:

```python
async def _emit_session_shutdown(self, reason: str) -> bool: ...
async def _emit_session_start(self, reason: str) -> bool: ...
```

Both gate on `self._extension_runner.has_handlers(...)` before emitting;
return `True` on emit, `False` when no handler is registered. Pi's
module-level `emitSessionShutdownEvent` (`runner.ts:177-189`) is wrapped
inside the harness so `reload()` can call directly without re-binding
the runner reference.

Imports `SessionShutdownHookEvent` + `SessionStartHookEvent` from
`aelix_agent_core.harness.hooks` (already shipped Sprint 6h₅a /
ADR-0081). The `reason="reload"` literal is already permitted by both
event types' Literal unions.

### §F — `AgentHarness.reload()` 2-op → 7-op expansion

**`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`** — replaces
the Sprint 6h₇b 2-op stub at `AgentHarness.reload()` with the full
7-op Pi parity chain (`agent-session.ts:2382-2413`):

1. **Guard (EXISTING)**: `settings_manager` must not be `None`
   (raises `AgentHarnessError("invalid_state")`).
2. **NEW**: snapshot `previous_flag_values =
   self._extension_runner.get_flag_values()` — UNUSED this sprint;
   round-trip restoration is Phase 5b (§"Deferred items").
3. **NEW**: `await self._emit_session_shutdown("reload")` (Pi `:2385`).
4. **EXISTING**: `await self._settings_manager.reload()` (Pi `:2386`).
5. **NEW**: `reset_api_providers()` (Pi `:2387`) + conditional
   `self._model_registry.reset()` (Aelix-additive — see divergence #8).
   Pi `:2387` is a single line `resetApiProviders();`; Pi
   does NOT call `modelRegistry.reset()` in its `reload()` body. The
   Aelix `_model_registry.reset()` call exists because Aelix's
   `ModelRegistry` caches the per-provider model catalog + OAuth-
   modified models (Sprint 6f₁); the explicit reset forces fresh
   credentials to be re-read after `settings_manager.reload()`
   completes. Pi achieves the same effect lazily via its provider
   lazy-load pattern (each stream call constructs a fresh client),
   which Aelix doesn't share. Model-registry call is gated on
   `hasattr(self, "_model_registry") and self._model_registry is not
   None` because Aelix harnesses may not attach a registry.
   W5 MAJOR-2 fold-in (Sprint 6h₇c): line citation corrected from
   `:2388-2389` (incorrect) to `:2387` (verified at SHA 734e08e).
6. **DEFERRED P-380 #3**: inline TODO marker at Pi `:2391`
   `_resourceLoader.reload()` site (ResourceLoader port required;
   Phase 5b).
7. **DEFERRED P-380 #5 (full)**: inline TODO marker at Pi `:2393`
   `_buildRuntime({flagValues: previous_flag_values})` site
   (full `__init__` pipeline extraction required; Phase 5b).
8. **NEW**: `has_bindings = bool(self._extension_runner.extensions)` —
   Pi 4-field UI check proxy; Phase 5b ADR-0033 replaces with the real
   `_extensionUIContext` / `_extensionCommandContextActions` /
   `_extensionShutdownHandler` / `_extensionErrorListener` check
   (P-449).
9. **NEW**: if `has_bindings`:
   - `await self._emit_session_start("reload")` (Pi `:2407`).
   - `await self._emit_resources_discover("reload")` (Pi `:2411`).
     Sprint 6h₇b wired this outside the gate; 6h₇c re-orders to
     inside (Pi parity).

The `reset_api_providers` symbol is imported at module top-level from
`aelix_ai.api_registry` (no circular dependency — `aelix-ai` has zero
imports from `aelix_coding_agent`).

### §G — ADR-0087 P-380 final status update (BINDING ledger)

| # | Primitive | Status | Closure ADR |
|---|---|---|---|
| 1 | `settingsManager.reload()` | CLOSED | ADR-0091 (Sprint 6h₇b) |
| 2 | `resetApiProviders()` | CLOSED | ADR-0093 (Sprint 6h₇c §B) |
| 3 | `_resourceLoader.reload()` | DEFERRED to Phase 5b | (ResourceLoader port standalone sprint) |
| 4 | `flagValues` primitives | PARTIAL — getter/setter shipped | ADR-0093 §C; round-trip wire Phase 5b |
| 5 | `_buildRuntime()` | PARTIAL — tool registry merge extracted | ADR-0093 §D; full extraction Phase 5b |

**A 단계 closure status:** ADR-0087 P-380 effectively closed (5/5
addressed; remaining items strictly Phase 5b-dependent).

## Aelix-additive divergences from Pi

1. **`reset_api_providers()` is a registry-flush wrapper** — Pi
   `register-builtins.ts:400-403` composes `clearApiProviders +
   registerBuiltInApiProviders`; Aelix has no module-level lazy
   provider cache, so the Aelix port is effectively a registry flush
   only. After `reset_api_providers()` Aelix does NOT auto-re-register
   built-ins — callers must re-register explicitly via provider
   package init.
2. **`ModelRegistry.reset()` is a Pi-parity alias for `refresh()`** —
   semantic identity preserved; `refresh()` retained for
   backward compatibility.
3. **`flag_values` is a `dict[str, bool | str]`** — Pi uses
   `Map<string, boolean | string>`. Shallow-copy semantic preserved
   via `dict(self.flag_values)`.
4. **`_rebuild_tool_registry()` extracted; full `_buildRuntime`
   extraction deferred** — Pi inlines all of `_buildRuntime`; Aelix
   ports only the merge step this sprint. Full extraction is a
   Phase 5b ~200 LOC `__init__` pipeline refactor.
5. **`has_bindings` proxy** — `bool(self._extension_runner.extensions)`
   replaces Pi's 4-field UI check (`_extensionUIContext` /
   `_extensionCommandContextActions` / `_extensionShutdownHandler` /
   `_extensionErrorListener`). Phase 5b ADR-0033 will swap in the real
   check.
6. **`_emit_session_shutdown` + `_emit_session_start` are
   `AgentHarness` private methods** — Pi has
   `emitSessionShutdownEvent` as a module-level helper in
   `runner.ts:177-189`; Aelix wraps inside the harness for direct
   access in `reload()` without re-binding the runner reference.
7. **`flag_values` snapshot captured in `reload()` but UNUSED** —
   round-trip restoration requires Phase 5b `_buildRuntime` extraction.
8. **`_model_registry.reset()` call in `reload()` is Aelix-additive**
   (W5 MAJOR-2 fold-in) — Pi `agent-session.ts:2387-2389` calls
   `resetApiProviders()` then `await this._resourceLoader.reload()`
   then `this._buildRuntime({...})` but does NOT call
   `modelRegistry.reset()` at any point in its `reload()` body. Aelix
   adds the call because `ModelRegistry` (Sprint 6f₁) caches both the
   per-provider model catalog and OAuth-credentials-modified models —
   without the explicit reset, fresh credentials from
   `settings_manager.reload()` would not be reflected in the cached
   catalog until the next process restart. Pi achieves the same
   effect lazily through its provider lazy-load pattern (`@earendil-
   works/pi-ai` reconstructs clients per stream call with fresh
   credentials), which Aelix does not share. The Aelix call is
   `hasattr` + `is not None` gated so harnesses without an attached
   registry skip cleanly.

## Deferred items (Phase 5b carry-forward)

- `_resourceLoader.reload()` — ResourceLoader port standalone sprint
  (~250-450 LOC).
- `_buildRuntime()` full extraction — ~200 LOC `__init__` pipeline
  refactor.
- `hasBindings` UI 4-field check — depends on Phase 5b extension UI
  context binding (P-449 / ADR-0033).
- `flag_values` round-trip wiring — depends on UI context binding
  + `_buildRuntime` full extraction (P-447 closure).

## Pi citations (SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`)

- `agent-session.ts:2382-2413` — full Pi `reload()` body (7-op chain).
- `register-builtins.ts:400-403` — `resetApiProviders` = `clearApiProviders + registerBuiltInApiProviders`.
- `runner.ts:177-189` — `emitSessionShutdownEvent` module-level helper.
- `runner.ts:409-411` — `getFlagValues` / `setFlagValue` over `Map`.
- `agent-session.ts:2329-2380` — `_buildRuntime` full body
  (partial-extraction reference; only the tool-merge subset is
  extracted this sprint via `_rebuild_tool_registry()`). W5 MAJOR-1
  fold-in (Sprint 6h₇c): line citation corrected from `:1249-1289`
  (incorrect — that range is inside `sendCustomMessage`) to
  `:2329-2380` (verified at SHA 734e08e via `grep -n "private
  _buildRuntime" agent-session.ts`).
- `agent-session.ts:2406-2409` — `hasBindings` 4-field UI predicate (Phase 5b reference).

## Reference companions

ADR-0091, ADR-0092, ADR-0089, ADR-0087, ADR-0086, ADR-0034.

## Verification

- `ruff check` — clean.
- `pyright` — 8 baseline errors preserved (intentional fixtures in
  `scripts/pyright_spike.py`); no new errors introduced.
- `pytest` — 2339 baseline + 41 new tests (5 reset api + 5 model
  registry reset + 9 flag_values + 8 rebuild + 7 session lifecycle
  + 7 reload expansion) = 2380 passed + 1 skipped.
- Smoke 1: `from aelix_ai.api_registry import reset_api_providers;
  reset_api_providers()` → no exception.
- Smoke 2: construct `AgentHarness` with `SettingsManager.in_memory()`
  + extension with `session_shutdown` handler → `await harness.reload()`
  fires handler with `reason="reload"`.
- RPC roster STAYS CLOSED at **29 supported / 0 deferred / 29 total**.
- Pi pin held at `734e08e` (no advance — Sprint 6h₇c imports no new
  Pi feature beyond the pinned SHA).

## Phase

Sprint 6h₇c / Phase 5a-iii-γ (shipped).
