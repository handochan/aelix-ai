# Sprint 6h₇c — Phase 5a-iii-γ — ADR-0087 P-380 Reload Primitives Closure BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."

**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (NO advance this sprint).

**Closes carry-forwards:**
- ADR-0087 P-380 #2 (`resetApiProviders`)
- ADR-0087 P-380 #4 (`flagValues` primitives — partial: getter/setter ship; round-trip wiring stays Phase 5b)
- ADR-0087 P-380 #5 (`_buildRuntime` — partial: tool registry merge extraction only)
- ADR-0091 §"Deferred items" 1 (`resetApiProviders`) and 5 (`Extension shutdown/start re-emit in reload`)

**Carry-forward to Phase 5b:**
- ADR-0087 P-380 #3 (`_resourceLoader.reload` — depends on standalone ResourceLoader port)
- ADR-0087 P-380 #5 full (`_buildRuntime` full extraction — ~200 LOC `__init__` refactor)
- `hasBindings` UI 4-field check (depends on extension UI context binding)
- `flagValues` round-trip wire (depends on `_buildRuntime` full extraction)

---

## §0 — W0 findings (P-445 ~ P-454)

**P-445** — Pi `agent-session.ts:reload()` is a 7-step chain (lines 2382-2413): flagValues snapshot → session_shutdown emit → settings.reload → resetApiProviders → resourceLoader.reload → `_buildRuntime({flagValues})` → if hasBindings: session_start emit + `extendResourcesFromExtensions("reload")`.

**P-446** — Pi `resetApiProviders()` (`register-builtins.ts:400-403`) = `clearApiProviders()` + `registerBuiltInApiProviders()`. Aelix equivalent already exists in primitives: `clear_providers()` at `api_registry.py:114-117` + `ModelRegistry.refresh()` at `model_registry.py:290-299`. Sprint 6h₇c adds module-level `reset_api_providers()` wrapper + `ModelRegistry.reset()` alias.

**P-447** — Pi `ExtensionRunner.getFlagValues()` (`runner.ts:409-411`) returns `Map` shallow copy. Aelix `flagValues` runtime state container ABSENT. Sprint 6h₇c adds field + get/set primitives on `_ExtensionRuntime` (~20 LOC) + `ExtensionRunner` delegation (~10 LOC). Round-trip WIRING (snapshot in reload, restore in `_buildRuntime`) DEFERRED to Phase 5b.

**P-448** — Pi `emitSessionShutdownEvent` (`runner.ts:177-189`) gates on `hasHandlers("session_shutdown")` before emit. Aelix already has `_emit_session_shutdown_event` helper at `agent_session_runtime.py:107-138` but NOT wrapped on harness. Sprint 6h₇c adds `AgentHarness._emit_session_shutdown` private method (~15 LOC) + `_emit_session_start` symmetric pair.

**P-449** — Pi `hasBindings` predicate (`agent-session.ts:2406-2409`) checks 4 UI binding fields: `_extensionUIContext` / `_extensionCommandContextActions` / `_extensionShutdownHandler` / `_extensionErrorListener`. Aelix has NO UI context fields (Phase 5b ADR-0033 deferred). 6h₇c proxy: `bool(self._extension_runner.extensions)` — "extensions present" is the semantic intent.

**P-450** — Pi `_buildRuntime` (`agent-session.ts:1249-1289`) is ~40 LOC. Aelix has this logic INLINE in `AgentHarness.__init__:509-516` (tool merge) + `:653-670` (ExtensionRunner construction). Full extraction is ~70-100 LOC refactor of `__init__` pipeline — RISKY without reload trigger justifying it. Sprint 6h₇c partial: extract tool merge as `_rebuild_tool_registry()` only; ExtensionRunner re-create stays in `__init__`.

**P-451** — Aelix `SessionStartHookEvent` + `SessionShutdownHookEvent` ALREADY ported at `hooks.py:716-774` (Sprint 6h₅a, ADR-0081) with `reason="reload"` literal already permitted. No new types needed.

**P-452** — Aelix `reload_resources()` = Pi `extendResourcesFromExtensions("reload")` semantic equivalent (Sprint 6h₇b). Already invoked in current `AgentHarness.reload()` stub. 6h₇c re-orders it to inside `has_bindings` gate (Pi parity).

**P-453** — Aelix integration site: `AgentHarness.reload()` currently 2-op at `harness/core.py:2151-2175`. Sprint 6h₇c expands to 7-op chain (5 NEW + 2 EXISTING + 2 deferred TODOs).

**P-454** — Pyright baseline: 8 errors all from `scripts/pyright_spike.py` (intentional fixtures). Adding ~250-400 LOC MUST NOT introduce new pyright errors.

---

## §A — Scope LOC table

| Item | Files | Prod LOC | Test LOC |
|---|---|---|---|
| §B `reset_api_providers` + `ModelRegistry.reset` | 2 prod | ~25 | ~30-50 |
| §C `flag_values` primitives | 2 prod | ~30 | ~30-50 |
| §D `_rebuild_tool_registry` extraction | 1 prod | ~70-80 | ~30-50 |
| §E `_emit_session_shutdown` + `_emit_session_start` wrappers | 1 prod | ~30 | ~30-50 |
| §F `AgentHarness.reload()` expansion | 1 prod | ~50 | ~80-150 |
| §G ADR-0093 + ADR-0034/README amends | 3 docs | (docs) | (docs) |
| **Total** | — | **~205-265 prod** | **~200-350 test** |

---

## §B — `reset_api_providers()` module function + `ModelRegistry.reset()` alias

### B.1 Pi source

- `packages/ai/src/providers/register-builtins.ts:400-403` — `resetApiProviders = clearApiProviders + registerBuiltInApiProviders`.

### B.2 Aelix target

- **MODIFY** `packages/aelix-ai/src/aelix_ai/api_registry.py` — add module-level `reset_api_providers()` (~10-15 LOC) that:
  - Calls `clear_providers()` (already exists `api_registry.py:114-117`).
  - Optionally re-registers built-in providers (Aelix has no module-level lazy cache; current pattern is per-call client creation, so `reset` is mostly a registry flush — document inline that this is the intentional Aelix divergence).
- **MODIFY** `packages/aelix-coding-agent/src/aelix_coding_agent/model_registry.py` — add `ModelRegistry.reset()` method (~5 LOC) that delegates to the existing `refresh()` (Pi parity naming alias; `refresh()` retained for backward compat).

### B.3 Tests (~30-50 LOC)

- **NEW or EXTEND** `tests/test_api_registry_reset.py` — verify `reset_api_providers()` clears registry + re-registers (if applicable).
- **NEW or EXTEND** `tests/model_registry/test_model_registry_reset.py` — verify `ModelRegistry.reset()` invokes `_load_models()` and clears any cached state.

### B.4 Aelix-additive divergence (document in ADR-0093)

- Pi has a module-level lazy provider cache that `resetApiProviders` flushes. Aelix has no such cache (each handle is constructed per call), so `reset_api_providers()` is effectively a registry flush. The function exists for Pi-parity API surface and future-proofing.

---

## §C — `flagValues` primitives on `_ExtensionRuntime` + `ExtensionRunner` delegation

### C.1 Pi source

- `packages/coding-agent/src/core/extensions/runner.ts:409-411` — `getFlagValues` returns `Map` shallow copy; `setFlagValue(name, value)` mutates.

### C.2 Aelix target

- **MODIFY** `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` `_ExtensionRuntime` class (line ~359):
  - Add `flag_values: dict[str, bool | str]` field defaulting to empty `dict`.
  - Add `get_flag_values() -> dict[str, bool | str]` method that returns a shallow copy (Pi `Map` shallow-copy semantic — `dict(self.flag_values)`).
  - Add `set_flag_value(name: str, value: bool | str) -> None` method that mutates the internal dict.
- **MODIFY** `packages/aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py` `ExtensionRunner` class (line ~72):
  - Add `get_flag_values() -> dict[str, bool | str]` delegating to `self._runtime.get_flag_values()`.
  - Add `set_flag_value(name: str, value: bool | str) -> None` delegating to `self._runtime.set_flag_value(name, value)`.

### C.3 Sprint 6h₇c scope note

- The flag values are NOT yet wired into the reload round-trip (Pi snapshot → `_buildRuntime({flagValues})` restore). 6h₇c lands the primitives only; the restoration site lives in `_buildRuntime`, which is itself a Phase 5b extraction target (see §D). Reload snapshots the dict but discards it (smoke test required in §F).

### C.4 Tests (~30-50 LOC)

- **NEW or EXTEND** `tests/harness/test_extension_runner_flag_values.py` — verify:
  - Empty dict default on construction.
  - `set_flag_value` mutates; `get_flag_values` returns the mutated state.
  - `get_flag_values` returns a **copy** (mutating the returned dict does NOT affect the runtime's internal dict — shallow-copy semantic).
  - `ExtensionRunner` delegates correctly to `_ExtensionRuntime`.

### C.5 Aelix-additive divergence (document in ADR-0093)

- Pi uses `Map`; Aelix uses `dict` (Python idiom). Shallow-copy semantic preserved. No UI binding yet (Phase 5b).

---

## §D — `_rebuild_tool_registry()` extraction

### D.1 Pi source

- `packages/coding-agent/src/core/agent-session.ts:1249-1289` — `_buildRuntime` (~40 LOC); Aelix ports ONLY the tool registry merge portion this sprint.

### D.2 Aelix target

- **MODIFY** `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` `AgentHarness`:
  - Extract tool merge logic from `__init__` (lines 509-516) into a private method:
    ```python
    def _rebuild_tool_registry(self) -> list[AgentTool]:
        """Pi parity (partial): tool merge step of agent-session.ts:_buildRuntime.

        Sprint 6h₇c §D extracted from AgentHarness.__init__:509-516. The
        full _buildRuntime port (extension runner re-create + active tool
        names refresh + flagValues restore) stays in __init__ until
        Phase 5b extracts the entire constructor pipeline.
        """
        merged: dict[str, AgentTool] = {}
        for extension in self._extensions:
            for name, tool in extension.tools.items():
                merged.setdefault(name, tool)
        for tool in self._options.tools:
            merged[tool.name] = tool
        return list(merged.values())
    ```
  - REPLACE the inline merge at `__init__:509-516` with `merged_tools = self._rebuild_tool_registry()`.
  - **CHECK [W2 to verify]**: `self._options` storage. If not already preserved on the instance, add `self._options = options` at the appropriate constructor location; if already preserved, reuse the existing field.
  - Document inline that only the merge logic is extracted; extension runner creation + active tool filter refresh + state assembly stay inline (Phase 5b carry-forward).

### D.3 Tests (~30-50 LOC)

- **EXTEND** `tests/harness/test_agent_harness.py` OR **NEW** `tests/harness/test_rebuild_tool_registry.py`:
  - Verify the extracted method returns the same merged list as the prior inline logic (e.g., construct harness, call `_rebuild_tool_registry()` again, compare to initial `merged_tools` snapshot).
  - Verify ordering: extensions iterate first (via `setdefault`), options override on name collision (last write wins via direct `merged[tool.name] = tool`).

---

## §E — `AgentHarness._emit_session_shutdown` + `_emit_session_start` wrappers

### E.1 Pi source

- `packages/coding-agent/src/core/extensions/runner.ts:177-189` — `emitSessionShutdownEvent` module-level helper, gates on `hasHandlers("session_shutdown")`.
- `packages/coding-agent/src/core/agent-session.ts:2384` — Pi `reload()` shutdown emit site.
- `packages/coding-agent/src/core/agent-session.ts:2407` — Pi `reload()` start emit site (inside `hasBindings` gate).

### E.2 Aelix target

- **MODIFY** `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` `AgentHarness`:
  - Add private async `_emit_session_shutdown(reason: str) -> bool`:
    ```python
    async def _emit_session_shutdown(self, reason: str) -> bool:
        """Pi parity: emitSessionShutdownEvent (runner.ts:177-189).

        Returns True if a handler fired, False if no handler registered.
        """
        if not self._extension_runner.has_handlers("session_shutdown"):
            return False
        await self._extension_runner.emit(
            SessionShutdownHookEvent(reason=reason)
        )
        return True
    ```
  - Add private async `_emit_session_start(reason: str) -> bool` (symmetric for Pi `:2407`).

### E.3 Tests (~30-50 LOC)

- **NEW** `tests/harness/test_session_lifecycle_emit.py`:
  - `_emit_session_shutdown` returns `False` + does not invoke `emit` when no handler registered.
  - `_emit_session_shutdown` returns `True` + invokes `emit` with `SessionShutdownHookEvent(reason=...)` when handler registered.
  - Symmetric coverage for `_emit_session_start`.

### E.4 Aelix-additive divergence (document in ADR-0093)

- Pi `emitSessionShutdownEvent` is a module-level helper in `runner.ts`. Aelix wraps as a private `AgentHarness` method for direct access in `reload()` without re-binding the runner reference.
- `has_handlers(...)` is the existing Aelix predicate on `_ExtensionRunner`. **[W2 to verify]**: confirm the exact method name in current Aelix code (`has_handlers` vs `hasHandlers` vs equivalent) and use whichever matches the codebase. If absent, add it as a thin wrapper.

---

## §F — `AgentHarness.reload()` 2-op → 7-op expansion

### F.1 Pi source

- `packages/coding-agent/src/core/agent-session.ts:2382-2413` — full Pi `reload()` body.

### F.2 Aelix target

- **MODIFY** `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` `AgentHarness.reload()` (currently 2-op at lines ~2151-2175).
- Replace the body with the full 7-op chain (with 2 deferred TODOs marked inline):

  1. **Guard (EXISTING)**: if `self._settings_manager is None` → raise `AgentHarnessError("invalid_state", "reload() requires options.settings_manager to be attached")`.
  2. **NEW**: snapshot `previous_flag_values = self._extension_runner.get_flag_values()` (Aelix returns a dict shallow copy; unused this sprint — round-trip restoration is Phase 5b).
  3. **NEW**: `await self._emit_session_shutdown("reload")` (Pi `:2384`).
  4. **EXISTING**: `await self._settings_manager.reload()` (Pi `:2386`).
  5. **NEW**: `reset_api_providers()` + `self._model_registry.reset()` (Pi `:2388-2389`). The `_model_registry` call must be gated on `hasattr(self, "_model_registry") and self._model_registry is not None` — Aelix may construct harnesses without an attached model registry.
  6. **DEFERRED P-380 #3**: inline comment `# TODO Phase 5b — ResourceLoader port required` at the Pi `:2391` `_resourceLoader.reload()` site.
  7. **DEFERRED P-380 #5 (full)**: inline comment `# TODO Phase 5b — extract full __init__ pipeline (extension runner re-create + flagValues restore)` at the Pi `:2393` `_buildRuntime({flagValues: previous_flag_values})` site.
  8. **NEW**: `has_bindings = bool(self._extension_runner.extensions)` (Pi 4-field UI check proxy; Phase 5b will replace with real UI 4-field check per P-449).
  9. **NEW**: if `has_bindings`:
     - `await self._emit_session_start("reload")` (Pi `:2407`).
     - `await self._emit_resources_discover("reload")` (Pi `:2411` `extendResourcesFromExtensions("reload")` — already wired Sprint 6h₇b; re-ordered to inside the gate this sprint).
- Update the docstring with the Pi citation (`agent-session.ts:2382-2413`) and a numbered list of the 7-op chain.

### F.3 Tests (~80-150 LOC)

- **EXTEND** `tests/harness/test_harness_reload.py` (Sprint 6h₇b already ships 4 tests; add ~7 NEW):
  - `test_reload_emits_session_shutdown_when_handlers_registered` — register session_shutdown handler, call reload, assert emit observed.
  - `test_reload_emits_session_start_when_extensions_loaded` — extensions non-empty, assert session_start emit observed (`has_bindings` true path).
  - `test_reload_skips_session_start_when_no_extensions_loaded` — extensions empty, assert NO session_start emit (`has_bindings` false path).
  - `test_reload_snapshots_flag_values_before_shutdown` — smoke; assert `get_flag_values` invoked before `_emit_session_shutdown`. Acceptable to verify via call-order spy on `_extension_runner`.
  - `test_reload_calls_reset_api_providers_after_settings_reload` — spy `reset_api_providers` + `settings_manager.reload`; assert order.
  - `test_reload_skips_model_registry_reset_when_unattached` — harness without `_model_registry`; assert no AttributeError and no call.
  - `test_reload_ordering_pi_parity` — full 7-op order verified against Pi `agent-session.ts:2382-2413` via call-order spy (single test asserts the canonical sequence).

---

## §G — ADR-0093 + ADR-0034/README amends + ADR-0087 P-380 ledger update

### G.1 NEW `docs/decisions/0093-sprint-6h7c-p380-closure.md`

- **Status:** Accepted, **Date:** 2026-05-22, **Pi pin:** `734e08e` (no advance).
- **Title:** Sprint 6h₇c Phase 5a-iii-γ — ADR-0087 P-380 Reload Primitives Closure
- **Decisions** per §B / §C / §D / §E / §F above.
- **Pi citations** (SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`):
  - `agent-session.ts:2382-2413` — full reload chain.
  - `register-builtins.ts:400-403` — `resetApiProviders`.
  - `runner.ts:177-189` — `emitSessionShutdownEvent`.
  - `runner.ts:409-411` — `getFlagValues` / `setFlagValue`.
  - `agent-session.ts:1249-1289` — `_buildRuntime` (partial extraction reference).
  - `agent-session.ts:2406-2409` — `hasBindings` 4-field UI predicate (Phase 5b reference).

#### G.1.1 ADR-0087 P-380 final status update (BINDING ledger)

| # | Primitive | Status | Closure ADR |
|---|---|---|---|
| 1 | `settingsManager.reload()` | ✅ CLOSED | ADR-0091 (Sprint 6h₇b) |
| 2 | `resetApiProviders()` | ✅ CLOSED | ADR-0093 (Sprint 6h₇c §B) |
| 3 | `_resourceLoader.reload()` | ⚠ DEFERRED to Phase 5b | (ResourceLoader port standalone sprint) |
| 4 | `flagValues` primitives | ⚠ PARTIAL — getter/setter shipped | ADR-0093 §C; round-trip wire Phase 5b |
| 5 | `_buildRuntime()` | ⚠ PARTIAL — tool registry merge extracted | ADR-0093 §D; full extraction Phase 5b |

**A 단계 closure:** ADR-0087 P-380 effectively closed (5/5 addressed; remaining items strictly Phase 5b-dependent).

#### G.1.2 Aelix-additive divergences (enumerate explicitly — 7)

1. `reset_api_providers()` module function (Pi `register-builtins.ts` parity; Aelix has no lazy provider cache to flush — function is registry-flush-only for parity API surface).
2. `ModelRegistry.reset()` Pi parity alias for `ModelRegistry.refresh()` (semantic identity; backward-compat with `refresh()` callers preserved).
3. `flagValues` field initialized as empty `dict` (Pi `Map`); shallow-copy semantic preserved.
4. `_rebuild_tool_registry()` extracted; full `_buildRuntime` extraction deferred.
5. `has_bindings` proxy: `bool(self._extension_runner.extensions)` (Pi's UI 4-field check deferred to Phase 5b ADR-0033).
6. `_emit_session_shutdown` + `_emit_session_start` are `AgentHarness` private methods (Pi has `emitSessionShutdownEvent` as module-level helper in `runner.ts`; Aelix wraps inside harness for direct access without re-binding the runner reference).
7. `flag_values` snapshot captured in `reload()` but UNUSED this sprint (round-trip restoration requires Phase 5b `_buildRuntime` extraction).

#### G.1.3 Deferred items (Phase 5b carry-forward)

- `_resourceLoader.reload()` — ResourceLoader port standalone sprint (~250-450 LOC).
- `_buildRuntime` full extraction — ~200 LOC `__init__` pipeline refactor.
- `hasBindings` UI 4-field check — depends on Phase 5b extension UI context binding.
- `flagValues` round-trip wiring — depends on extension UI context binding + `_buildRuntime` full extraction.

#### G.1.4 Reference companions

ADR-0091, ADR-0092, ADR-0089, ADR-0087, ADR-0086, ADR-0034.

### G.2 AMEND `docs/decisions/0034-pi-reference-version-pin.md`

Append Sprint 6h₇c row to the sprint-tracking table; **no Pi pin advance** (same `734e08e` SHA, new sprint reference).

### G.3 AMEND `docs/decisions/README.md`

Append ADR-0093 row to the ADR index.

---

## §H — Atomic commit plan (EXACTLY 6 commits)

**Commit 1** — `feat(api): reset_api_providers() module function + ModelRegistry.reset() alias (Sprint 6h₇c §B P-446)`
- `packages/aelix-ai/src/aelix_ai/api_registry.py` (MODIFY)
- `packages/aelix-coding-agent/src/aelix_coding_agent/model_registry.py` (MODIFY)
- `tests/test_api_registry_reset.py` (NEW or EXTEND existing)
- `tests/model_registry/test_model_registry_reset.py` (NEW or EXTEND existing)

**Commit 2** — `feat(extensions): flag_values primitives on _ExtensionRuntime + ExtensionRunner delegation (Sprint 6h₇c §C P-447)`
- `packages/aelix-coding-agent/src/aelix_coding_agent/extensions/api.py` (MODIFY — add `flag_values` field + `get_flag_values` + `set_flag_value`)
- `packages/aelix-agent-core/src/aelix_agent_core/harness/_extension_runner.py` (MODIFY — add `get_flag_values` / `set_flag_value` delegates)
- `tests/harness/test_extension_runner_flag_values.py` (NEW or EXTEND existing)

**Commit 3** — `refactor(harness): extract _rebuild_tool_registry() from __init__ (Sprint 6h₇c §D P-450)`
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (MODIFY — extract tool merge into private method; replace inline at `__init__:509-516`)
- `tests/harness/test_agent_harness.py` (EXTEND) OR `tests/harness/test_rebuild_tool_registry.py` (NEW)

**Commit 4** — `feat(harness): _emit_session_shutdown + _emit_session_start helpers (Sprint 6h₇c §E P-448)`
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (MODIFY — add private async helpers)
- `tests/harness/test_session_lifecycle_emit.py` (NEW)

**Commit 5** — `feat(harness): AgentHarness.reload() 2-op → 7-op expansion (Sprint 6h₇c §F P-453)`
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (MODIFY — replace 2-op body with 7-op chain + 2 deferred TODO markers)
- `tests/harness/test_harness_reload.py` (EXTEND — add ~7 new tests for emit ordering, has_bindings gate, reset chain, ordering Pi parity)

**Commit 6** — `docs: ADR-0093 (Sprint 6h₇c P-380 closure) + ADR-0034/README amends + ADR-0087 P-380 ledger update`
- `docs/decisions/0093-sprint-6h7c-p380-closure.md` (NEW)
- `docs/decisions/0034-pi-reference-version-pin.md` (AMEND — append Sprint 6h₇c row)
- `docs/decisions/README.md` (AMEND — append ADR-0093 row)

Each commit: HEREDOC message + trailer
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §I — Verification gates

### I.1 Per-commit gates (run after each commit)

- `uv run ruff check 2>&1 | tail -2` — clean.
- `uv run pyright 2>&1 | tail -3` — 8 baseline preserved (no new errors from new code).
- `uv run pytest 2>&1 | tail -3` — current 2339 + N new tests pass (estimate N ≈ 30-50 new tests across §B/§C/§D/§E/§F).

### I.2 Smoke verifications

**After C1 smoke:**
```bash
uv run python -c "from aelix_ai.api_registry import reset_api_providers; reset_api_providers(); print('OK')"
```
Expected: `OK` (no exception).

**After C5 smoke (reload chain integration):**
1. Construct `InMemorySettingsStorage`-backed `SettingsManager`.
2. Construct `AgentHarness(options=AgentHarnessOptions(settings_manager=m, extensions=[ext_with_session_handlers]))`.
3. Register session_shutdown + session_start handlers on the extension.
4. `await harness.reload()`.
5. Assert both session_shutdown and session_start emits observed in order; assert settings_manager.reload was invoked between them.

### I.3 Final gates (before C6)

- All 5 prior commits green on lint / types / tests.
- Manual smoke verifications recorded in W3 verification notes.
- `uv run pytest -q` summary captured for W3 reviewer.

---

## §J — Workflow

W2 executor (opus) → W3 verification → W4 code-reviewer (opus) → W5 critic (opus Pi parity audit via `curl` against pinned Pi SHA `734e08e`) → W6 commits (NO push — user pushes).

---

## §K — Out-of-scope (BINDING — 7 non-goals)

1. **NO ResourceLoader port** — Phase 5b standalone sprint.
2. **NO `_buildRuntime` full extraction** (~200 LOC `__init__` refactor) — Phase 5b.
3. **NO `hasBindings` UI 4-field check** — Phase 5b extension UI context.
4. **NO `flagValues` round-trip wiring in `_buildRuntime`** — Phase 5b (depends on #2).
5. **NO Phase 5b TUI work** (extensions UI, themes, etc.).
6. **NO Pi pin advance.**
7. **NO new RPC commands** (RPC roster stays 29/0/29).

---

## §L — Phase 5b preview (remaining P-380 work)

After 6h₇c, Phase 5a is fully closed (non-UI). Phase 5b agenda:

1. ResourceLoader port (~250-450 LOC) → P-380 #3 closure.
2. `_buildRuntime` full extraction (~200 LOC `__init__` refactor) → P-380 #5 full closure.
3. UI context binding (extension UI context, command actions, shutdown handler, error listener) → `hasBindings` 4-field Pi parity (P-449).
4. `flagValues` UI binding + round-trip wire in `_buildRuntime` (P-447 closure).
5. TUI library decision per ADR-0088 + Component Protocol.

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
