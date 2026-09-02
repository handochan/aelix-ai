# Sprint 6h₇b — Phase 5a-iii-β — SettingsManager BINDING SPEC

**Top-level binding principle:** "pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다."
**Pi pin:** `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance).
**Splits from:** original Sprint 6h₇ scope per Option A decision (non-UI carry-forward, partial).
**Carry-forward to:** Sprint 6h₇c+ (P-380 remaining 4 primitives — `resetApiProviders` / `_resourceLoader.reload` / `_buildRuntime` / extension reload lifecycle).

Ports Pi `core/settings-manager.ts` (1067 LOC TS) → `settings_manager.py` (~1400-1600 LOC Python) standalone. Adds dual-scope file/in-memory storage backends with `fcntl.flock` locking (AuthStorage parity), full Settings dataclass tree (33 fields + 11 nested types), ~80 getters/setters, migration transforms, modification tracking + async write queue, and `AgentHarness.reload()` stub that delegates to `SettingsManager.reload()` only (other 4 P-380 primitives deferred). NO Pi pin advance. NO new RPC. NO TUI / Phase 5b wiring of 9 Pi consumption sites.

---

## §0 — W0 findings (P-423 ~ P-432)

### P-423 — Pi `settings-manager.ts` module structure (1067 LOC)
Agent A confirmed. Layout:
- Lines 1-6: imports.
- Lines 8-113: interfaces (`Settings` + 11 nested types).
- Lines 116-144: `deepMergeSettings(global, project)` helper.
- Lines 146-239: storage abstractions (`SettingsStorage` interface, `FileSettingsStorage`, `InMemorySettingsStorage`).
- Lines 241-1067: `SettingsManager` class.

Method count: 3 lifecycle (constructor + 2 static factories) + 3 helpers (`deepMerge` / `migrate` / `persistScoped`) + ~80 public getters/setters + 4 public misc (`reload`, `drainErrors`, `getSettings`, `getProjectSettings`) + 9 private bookkeeping (`markModified`, `markProjectModified`, `save`, `saveProjectSettings`, `enqueueWrite`, etc.) = **~99 methods total**.

### P-424 — `Settings` interface shape
Agent B confirmed. 33 optional fields on top-level `Settings` + 10 nested type interfaces + 1 union (`PackageSource = string | PackageSourceObject`, total 11 nested types).
- Only `defaults.ts` constant: `DEFAULT_THINKING_LEVEL = "medium"` (3 LOC file).
- 5 union string-literal types: `defaultThinkingLevel` (`"low" | "medium" | "high"`), `steeringMode`, `followUpMode`, `doubleEscapeAction`, `treeFilterMode`.
- Env var fallbacks (preserved verbatim, no `AELIX_*` rename — Phase 5b concern): `PI_CLEAR_ON_SHRINK`, `PI_HARDWARE_CURSOR`.

### P-425 — `reload()` body (Pi `settings-manager.ts:403-429`)
1. `await this.writeQueue` (drain pending writes).
2. Load global storage → `globalSettings` (or capture error into `globalLoadError`).
3. Clear ALL 4 modification tracking sets (`modifiedFields`, `modifiedNestedFields`, `modifiedProjectFields`, `modifiedProjectNestedFields`).
4. Load project storage → `projectSettings` (or capture error into `projectLoadError`).
5. Re-merge via `deepMergeSettings(global, project)` → assign to `this.settings`.

### P-426 — Method inventory (~80 getters/setters, lines 395-1066)
Agent A full table. Each getter applies its default inside the method (NOT on the dataclass field) — matches Pi pattern. Group by Settings field. Examples:
- `getDefaultThinkingLevel() -> "low" | "medium" | "high"` (default: `DEFAULT_THINKING_LEVEL = "medium"`).
- `setDefaultThinkingLevel(value) -> Promise<void>` → mutate + `markModified("defaultThinkingLevel")` + `save()`.
- Nested setters: `setBranchSummarySkipPrompt(value)` → `markModified("branchSummary", "skipPrompt")` + per-nested-key persistence.

[W2 to verify]: exact final getter/setter count (target ~80 ±5) by enumerating Pi class body. Each Settings field gets a getter + setter pair (33 × 2 = 66) plus nested-field convenience accessors (~10-14 more). Implementer ports Pi method names verbatim (snake_case conversion: `getDefaultThinkingLevel` → `get_default_thinking_level`).

### P-427 — `migrateSettings()` 4 transforms (Pi `settings-manager.ts:334-393`)
Static method. In-place mutation on dict, returns migrated dict.
1. **Lines 336-339** — `queueMode` → `steeringMode`: if `target.steeringMode` absent and `source.queueMode` present, copy and delete legacy.
2. **Lines 342-345** — `websockets: boolean` → `transport: "websocket" | "sse"`: map `true → "websocket"`, `false → "sse"`, delete legacy.
3. **Lines 348-366** — `skills` object → array: extract `enableSkillCommands` to top-level, migrate `customDirectories[]` to top-level `skills[]` array, delete legacy object form.
4. **Lines 369-390** — `retry.maxDelayMs` → `retry.provider.maxRetryDelayMs`: nested-field move into `retry.provider.{maxRetryDelayMs}`.

### P-428 — `persistScopedSettings()` body (Pi `settings-manager.ts:493-522`)
Private method. Scopes: `"global" | "project"`. Sequence:
1. `await this.storage.withLock(scope, async () => { ... })`.
2. Inside lock: read existing JSON (or `{}` if missing) → `migrate(existing)`.
3. For each modified top-level field: shallow override merged[field] = current[field].
4. For each modified nested field (e.g., `branchSummary.skipPrompt`): merge per-key (preserve unmodified nested keys from disk).
5. Return `JSON.stringify(merged, null, 2)` to storage write path.

### P-429 — Modification tracking lifecycle
Agent A §E. Each setter:
1. Mutate in-memory `this.settings` (or `this.projectSettings`).
2. Call `markModified(field, nestedKey?)` or `markProjectModified(...)`.
3. Call `save()` (global) or `saveProjectSettings()` (project) → enqueues write.

Write completion (`enqueueWrite` chain at line 478) **clears the tracking sets AFTER successful write**. `reload()` clears them BEFORE remerging (lines 414-417). Both clear paths must be preserved.

### P-430 — Pi test suite inventory
Agent C inventory:
- `coding-agent/test/core/settings-manager.test.ts` — 18 main test cases.
- `coding-agent/test/core/settings-manager-bug.test.ts` — 4 regression bug tests (external-edit preservation: file on disk modified between read+write, persistScoped must merge not clobber).
- **MISSING** in Pi suite: migration tests (4 transforms uncovered) + lock contention tests (concurrent writers).

Aelix port ADDS migration + lock contention tests as Aelix-additive (document in ADR-0091).

### P-431 — Aelix `fcntl.flock` precedent
`AuthStorage` at `packages/aelix-ai/src/aelix_ai/oauth/auth_storage.py:81-681` uses dual-layer locking:
- `asyncio.Lock` for in-process write coordination (single event loop).
- `fcntl.flock` (LOCK_EX) for cross-process locking on a sidecar `.lock` file.
- Atomic write-rename pattern: write tmp → `fsync` → `os.replace(tmp, target)`.
- Re-read-before-write merge on disk (mirrors Pi `persistScopedSettings`).
- File permissions: 0o600 (credentials).

SettingsManager mirrors this pattern with permissions 0o644 (non-secret config). Document divergence from Pi `proper-lockfile` directory-locks in ADR-0091.

### P-432 — Aelix integration boundary (Agent E)
- Option A (CHOSEN): `AgentHarnessOptions.settings_manager: SettingsManager | None = None` field — Pi parity via constructor injection (no global singleton).
- NEW `AgentHarness.reload()` method (NOT extension of existing `reload_resources()`).
- 6h₇b ships single-operation stub: `await self._settings_manager.reload()`. Remaining 4 P-380 primitives (`resetApiProviders`, `_resourceLoader.reload`, `_buildRuntime`, extension shutdown/start re-emit) deferred to 6h₇c+.
- 9 Pi consumption sites NOT wired in this sprint (Phase 5b TUI command layer responsibility per Agent E §H).
- Package placement: `packages/aelix-ai/src/aelix_ai/settings/` (alongside `oauth/auth_storage.py` — both credential/config-adjacent, both reuse `fcntl.flock` pattern). [W2 to verify]: confirm no import cycle between `aelix_ai` and `aelix_agent_core` before C5 (`grep -rn "from aelix_agent_core" packages/aelix-ai/src/`).

---

## §A — Scope LOC table

| Item | Files | Prod | Test |
|---|---|---|---|
| §B Settings dataclass tree (33 + 11 nested) | `settings/types.py` | ~200-300 | ~80-120 |
| §B SettingsManager class core + ~80 methods + reload + migrate + persist + deepMerge | `settings/settings_manager.py` | ~900-1200 | ~400-600 |
| §C Storage backends (FileSettingsStorage + InMemorySettingsStorage + Protocol) | `settings/storage.py` | ~150-250 | (covered above) |
| §E AgentHarness integration (field + property + reload stub) | `harness/core.py` + `tests/harness/test_harness_reload.py` | ~30-50 | ~50-80 |
| §F Migration + locking tests (Aelix-additive) | `tests/settings_manager/test_*_migration.py` + `test_*_locking.py` | (test only) | ~180-350 |
| §G ADR-0091 + amends | 3 docs | (docs) | (docs) |
| **Total** | | **~1280-1800 prod** | **~710-1150 test** |

---

## §B — Port Pi `settings-manager.ts` → `settings_manager.py`

**Pi source:** `packages/coding-agent/src/core/settings-manager.ts` (1067 LOC TS).
**Target:** `packages/aelix-ai/src/aelix_ai/settings/settings_manager.py` (~900-1200 LOC Python) + `settings/types.py` (~200-300 LOC for dataclasses).

### §B.1 — `Settings` dataclass tree (`types.py`)
- Top-level `@dataclass Settings` with 33 optional fields (all `| None` default `None`).
- 11 nested `@dataclass` types: `CompactionSettings`, `BranchSummarySettings`, `ProviderRetrySettings`, `RetrySettings`, `TerminalSettings`, `ImageSettings`, `ThinkingBudgetsSettings`, `MarkdownSettings`, `WarningSettings`, `PackageSourceObject`, plus `PackageSource = str | PackageSourceObject` union alias.
- 5 union literal types via `Literal[...]`: `defaultThinkingLevel`, `steeringMode`, `followUpMode`, `doubleEscapeAction`, `treeFilterMode`.
- `DEFAULT_THINKING_LEVEL: Final[str] = "medium"`.

### §B.2 — `SettingsManager` class shape
```python
class SettingsManager:
    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        global_load_error: BaseException | None = None,
        project_load_error: BaseException | None = None,
        initial_errors: list[BaseException] | None = None,
    ) -> None: ...

    @staticmethod
    async def create(cwd: str, agent_dir: str | None = None) -> "SettingsManager": ...

    @staticmethod
    async def from_storage(storage: SettingsStorage) -> "SettingsManager": ...

    @staticmethod
    def in_memory(settings: Settings | None = None) -> "SettingsManager": ...
```

Constructor is **private by convention** (no `_` prefix per Pi parity — but callers go through factories). Three factories mirror Pi `create` / `fromStorage` / static instantiation.

### §B.3 — `reload()` (Pi parity for `settings-manager.ts:403-429`)
Implements P-425 5-step sequence verbatim. Async. Acquires the same write queue lock used by setters before clearing tracking state to avoid races with in-flight writes.

### §B.4 — `migrate_settings()` static (Pi parity for `:334-393`)
Implements all 4 P-427 transforms. In-place dict mutation, returns the dict.

### §B.5 — `persist_scoped_settings()` private (Pi parity for `:493-522`)
Implements P-428 sequence inside `storage.with_lock(scope, ...)`. Uses `json.dumps(merged, indent=2, ensure_ascii=False)` to match Pi `JSON.stringify(merged, null, 2)`.

### §B.6 — `deep_merge_settings(global, project)` helper (Pi parity for `:116-144`)
Per-field deep merge: project-scope value wins on conflict; nested dataclasses merged field-by-field.

### §B.7 — Modification tracking (P-429)
- `_modified_fields: set[str]`
- `_modified_nested_fields: dict[str, set[str]]`
- `_modified_project_fields: set[str]`
- `_modified_project_nested_fields: dict[str, set[str]]`

Helpers `_mark_modified(field, nested_key=None)` + `_mark_project_modified(...)`.

### §B.8 — Async write queue (Pi `writeQueue: Promise<void>`)
Aelix implementation: `asyncio.Lock()` serializes writes (single event loop). Each `save()` / `save_project_settings()` call schedules the next write inside the lock. Pattern:
```python
async def _enqueue_write(self, scope: Literal["global", "project"]) -> None:
    async with self._write_queue_lock:
        await self._persist_scoped_settings(scope)
        # clear modification tracking AFTER successful write (P-429)
        if scope == "global":
            self._modified_fields.clear()
            self._modified_nested_fields.clear()
        else:
            self._modified_project_fields.clear()
            self._modified_project_nested_fields.clear()
```

### §B.9 — `drain_errors()` (Pi parity)
Returns `list[BaseException]`, clears internal queue.

### §B.10 — ~80 getter/setter methods (P-426)
Port verbatim from Pi. Snake_case names. Each getter applies default in-method (NOT on dataclass field). Each setter mutates + marks modified + enqueues write. [W2 to verify]: exact count by enumerating Pi class body lines 395-1066.

---

## §C — Storage backends + `fcntl.flock` locking

**Target:** `packages/aelix-ai/src/aelix_ai/settings/storage.py` (~150-250 LOC).

### §C.1 — `SettingsStorage` Protocol
```python
class SettingsStorage(Protocol):
    async def load_global(self) -> Settings: ...
    async def load_project(self) -> Settings: ...
    async def with_lock(
        self,
        scope: Literal["global", "project"],
        fn: Callable[[], Awaitable[str]],
    ) -> None: ...
```

`with_lock` acquires the appropriate per-scope lock, invokes `fn()` to compute the new JSON string (which performs the read-migrate-merge sequence per P-428), and persists the result atomically.

### §C.2 — `FileSettingsStorage`
- Constructor: `FileSettingsStorage(global_path: Path, project_path: Path)`.
- `load_global` / `load_project`: open file → parse JSON → `migrate_settings` → construct `Settings` dataclass. Missing file → empty `Settings()`.
- `with_lock`: mirror `AuthStorage` dual-layer pattern:
  - `asyncio.Lock` (per scope, in-process).
  - `fcntl.flock(LOCK_EX)` on a sidecar `{path}.lock` file (cross-process).
- Atomic write: tmp file → `fsync` → `os.replace(tmp, target)`.
- Permissions: 0o644 (non-secret config; AuthStorage uses 0o600 for credentials — distinction is intentional and documented).

### §C.3 — `InMemorySettingsStorage`
Test storage. No I/O. Holds two `dict[str, Any]` for global + project. `with_lock` just runs `fn()` under `asyncio.Lock`. No file permission semantics.

### §C.4 — Aelix-additive divergence (document in ADR-0091)
- Pi uses `proper-lockfile` (directory-lock).
- Aelix uses `fcntl.flock` for parity with `AuthStorage` precedent. Zero new dependencies. POSIX-only (matches existing AuthStorage constraint).

---

## §D — File path conventions (`.aelix/` namespace)

- **Global path:** `get_agent_dir() / "settings.json"` → `~/.aelix/agent/settings.json`. Mirrors AuthStorage placement. [W2 to verify]: confirm `get_agent_dir()` helper exists in `aelix_ai.config` or equivalent; if absent, document the lookup site and use the same convention as `auth_storage.py` uses.
- **Project path:** `Path(cwd) / ".aelix" / "settings.json"`. Pi parity for `./.pi/settings.json`.

**Aelix-additive divergence** (document in ADR-0091): Pi global path is `~/.pi/agent/settings.json`; Aelix is `~/.aelix/agent/settings.json` (consistent with ADR-0034 Pi pin namespace decision — `PI_*` env vars retained per Phase 5b deferral).

---

## §E — `AgentHarness` integration

### §E.1 — `AgentHarnessOptions` field
Add after `_branch_summarizer_override` test seam (`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py:~251` — [W2 to verify] exact line; mirror existing `Optional` field placement):
```python
settings_manager: "SettingsManager | None" = None
```
Use `TYPE_CHECKING`-guarded import to avoid cross-package runtime cycle: `from aelix_ai.settings import SettingsManager` inside `if TYPE_CHECKING:` block.

### §E.2 — `AgentHarness.__init__` store
```python
self._settings_manager = options.settings_manager
```

### §E.3 — Public property (Pi parity for `runtimeHost.settingsManager`)
Mirror `session` property pattern at `core.py:752` (Sprint 6h₅d MINOR-3):
```python
@property
def settings_manager(self) -> "SettingsManager | None":
    """Pi parity for runtimeHost.settingsManager.

    Sprint 6h₇b §E: exposes the optionally-attached SettingsManager so callers
    can read settings without reaching into harness internals. Re-reads
    self._settings_manager per call to mirror session property semantics.
    """
    return self._settings_manager
```

### §E.4 — NEW `AgentHarness.reload()` method
Pi parity skeleton for `agent-session.ts:2382-2398`. 6h₇b ships **only** the SettingsManager reload step (remaining 4 P-380 primitives deferred):
```python
async def reload(self) -> None:
    """Pi parity for agent-session.ts reload() — Sprint 6h₇b stub.

    Sprint 6h₇b ships SettingsManager.reload() only. Remaining 4 primitives
    (resetApiProviders, _resourceLoader.reload, _buildRuntime, extension
    shutdown/start re-emit) deferred to Sprint 6h₇c+ per ADR-0087 P-380.
    """
    if self._settings_manager is None:
        raise AgentHarnessError(
            "invalid_state",
            "reload() requires options.settings_manager to be attached",
        )
    await self._settings_manager.reload()
    # TODO Sprint 6h₇c+ (ADR-0087 P-380):
    # - resetApiProviders Aelix equivalent
    # - self._resource_loader.reload()  (depends on ResourceLoader port)
    # - self._build_runtime(...)
    # - emit session_shutdown(reason="reload") + session_start(reason="reload")
```

[W2 to verify]: confirm `AgentHarnessError` constructor signature (`code: str, message: str`) matches existing usages; if different, follow the existing pattern.

### §E.5 — Out-of-scope (BINDING) — DO NOT wire
- 9 Pi consumption sites in agent-session / TUI command layer — Phase 5b.
- Extension shutdown/start re-emit on reload — Phase 5b.
- Replace existing `harness.reload_resources()` — keep both methods side-by-side; do not collapse.

---

## §F — Tests

### §F.1 — Directory structure
- NEW `tests/settings_manager/__init__.py` (empty marker).
- NEW `tests/settings_manager/conftest.py` (~30-60 LOC) — fixtures: `tmp_path`-rooted global/project paths, `monkeypatch` env vars (`PI_CLEAR_ON_SHRINK`, `PI_HARDWARE_CURSOR`), sample `Settings()` builders.

### §F.2 — `tests/settings_manager/test_settings_types.py` (~80-120 LOC, Commit 1)
- Dataclass instantiation: `Settings()` with all defaults `None`.
- Nested dataclass defaults: `BranchSummarySettings()` etc.
- `PackageSource` union accepts both `str` and `PackageSourceObject`.
- 5 Literal union types reject invalid values via type-time check (pyright spike fixture pattern).

### §F.3 — `tests/settings_manager/test_settings_manager.py` (~400-600 LOC, Commit 2)
Ports Pi's 18 main test cases verbatim. Test names mirror Pi:
- create + load global empty
- create + load project empty
- migrate on load
- reload picks up disk changes
- reload clears modification tracking
- save persists global
- save persists project
- deepMerge: project wins on conflict
- deepMerge: nested per-field
- drainErrors returns accumulated errors
- getSettings returns merged view
- getProjectSettings returns project-only view
- in_memory factory no I/O
- ... (~5 more — [W2 to verify] exact list from Pi test file)

### §F.4 — `tests/settings_manager/test_settings_manager_bug.py` (~150-250 LOC, Commit 2)
Ports Pi's 4 regression bug tests:
- External edit between read+write preserved (persistScoped merges, doesn't clobber).
- Concurrent setter calls coalesce correctly.
- Partial nested modification doesn't clobber sibling keys.
- Reload-during-pending-write completes pending write first (`await this.writeQueue`).

### §F.5 — `tests/settings_manager/test_settings_manager_getters_setters.py` (~200-400 LOC, Commit 3)
Coverage for all ~80 getter/setter methods. One test per pair (or parametrized). Asserts:
- Getter returns default when field unset.
- Setter mutates + persists + clears tracking after write.
- Nested setters update per-key without clobbering siblings.

### §F.6 — `tests/settings_manager/test_settings_manager_migration.py` (~100-200 LOC, Commit 4, Aelix-additive)
Cover all 4 P-427 transforms:
- `queueMode` → `steeringMode` (only when target absent).
- `websockets: true/false` → `transport: "websocket"/"sse"`.
- `skills` object format → array format + `enableSkillCommands` extraction.
- `retry.maxDelayMs` → `retry.provider.maxRetryDelayMs`.

### §F.7 — `tests/settings_manager/test_settings_manager_locking.py` (~80-150 LOC, Commit 4, Aelix-additive)
- Concurrent `save()` calls from same event loop coalesce.
- Cross-process `fcntl.flock` blocks second writer (use `subprocess` to spawn second writer; [W2 to verify]: skippable on non-POSIX CI if Aelix CI matrix includes Windows — current matrix is Linux-only per existing `auth_storage.py` test pattern).
- Reload during pending write completes pending first.

### §F.8 — `tests/harness/test_harness_reload.py` (~50-80 LOC, Commit 5)
- `AgentHarness.reload()` raises `AgentHarnessError("invalid_state", ...)` when `settings_manager` is None.
- `AgentHarness.reload()` calls `settings_manager.reload()` (verify via spy/mock SettingsManager).
- Integration: construct harness with `InMemorySettingsStorage`-backed SettingsManager, write changed value to storage, call `harness.reload()`, observe new value via `settings_manager.get_settings()`.

### §F.9 — Expected pytest delta
Current 2116 → ~2166-2196 pass + 1 skipped (estimate N ≈ 50-80 new tests). RPC roster 29/0 unchanged.

---

## §G — ADR-0091 + ADR-0034/README amends

### §G.1 — NEW ADR-0091 `docs/decisions/0091-sprint-6h7b-phase-5a-iii-beta.md`
- Title: **Sprint 6h₇b Phase 5a-iii-β — SettingsManager Standalone Port**
- Status: Accepted, Date: 2026-05-22
- Decisions per §B (port), §C (storage + fcntl.flock), §D (.aelix namespace), §E (harness integration), §F (Aelix-additive tests).
- Pi citations (verbatim line ranges from P-423 ~ P-432):
  - `core/settings-manager.ts:1-1067` (module structure)
  - `core/settings-manager.ts:116-144` (deepMergeSettings)
  - `core/settings-manager.ts:334-393` (migrateSettings 4 transforms)
  - `core/settings-manager.ts:403-429` (reload body)
  - `core/settings-manager.ts:493-522` (persistScopedSettings body)
  - `agent-session.ts:2382-2398` (reload() Pi parity target for AgentHarness.reload stub)
- **Aelix-additive divergences** (7):
  1. `fcntl.flock` instead of `proper-lockfile` directory-locks (AuthStorage precedent).
  2. `.aelix/` namespace instead of `.pi/` (Pi pin policy / ADR-0034 line).
  3. Settings file in `aelix_ai/settings/` instead of `coding-agent/core/` (Python package boundary; alongside `oauth/`).
  4. Migration tests added (Aelix-additive — Pi suite missing).
  5. Lock contention tests added (Aelix-additive — Pi suite missing).
  6. File permissions 0o644 (non-secret config) instead of unspecified Pi default.
  7. `PI_*` env vars retained (no `AELIX_*` rename — TUI-side concern Phase 5b).
- **Deferred items** (carry-forward to Sprint 6h₇c+):
  - `resetApiProviders()` Aelix equivalent (ADR-0087 P-380 primitive #2).
  - `_resourceLoader.reload()` integration (P-380 primitive #3; depends on ResourceLoader port).
  - `_buildRuntime()` call in reload (P-380 primitive #5).
  - Extension shutdown/start re-emit in reload (Phase 5b).
  - 9 Pi consumption site wiring (Phase 5b TUI command layer).
- Reference companions: ADR-0090 (Sprint 6h₇a closure, 5a-iii-α), ADR-0087 (P-380 ledger), ADR-0086 (carry-forward catalog), ADR-0034 (Pi pin).

### §G.2 — AMEND ADR-0034 `docs/decisions/0034-pi-reference-version-pin.md`
Append Sprint 6h₇b row to version-pin table (no Pi pin advance — same SHA, new sprint reference).

### §G.3 — AMEND `docs/decisions/README.md`
Append ADR-0091 row.

---

## §H — Atomic commit plan (EXACTLY 6)

**Commit 1** — `feat(settings): Settings dataclass tree + 11 nested types (Sprint 6h₇b §B)`
- `packages/aelix-ai/src/aelix_ai/settings/__init__.py` (NEW)
- `packages/aelix-ai/src/aelix_ai/settings/types.py` (NEW — Settings + 11 nested dataclasses + 5 Literal unions + DEFAULT_THINKING_LEVEL)
- `tests/settings_manager/__init__.py` (NEW marker)
- `tests/settings_manager/conftest.py` (NEW — fixtures)
- `tests/settings_manager/test_settings_types.py` (NEW)

**Commit 2** — `feat(settings): SettingsManager class core + reload + migrate + persist (Sprint 6h₇b §B/§C)`
- `packages/aelix-ai/src/aelix_ai/settings/settings_manager.py` (NEW — class + factories + reload + migrate + persistScoped + deepMerge + write queue + modification tracking)
- `packages/aelix-ai/src/aelix_ai/settings/storage.py` (NEW — `SettingsStorage` Protocol + `FileSettingsStorage` + `InMemorySettingsStorage`)
- `tests/settings_manager/test_settings_manager.py` (NEW — 18 Pi parity main tests)
- `tests/settings_manager/test_settings_manager_bug.py` (NEW — 4 regression tests)

**Commit 3** — `feat(settings): ~80 getters/setters (Sprint 6h₇b §B P-426)`
- `packages/aelix-ai/src/aelix_ai/settings/settings_manager.py` (AMEND — add getters/setters)
- `tests/settings_manager/test_settings_manager_getters_setters.py` (NEW)

**Commit 4** — `feat(settings): migration + lock-contention tests (Aelix-additive — Sprint 6h₇b §F)`
- `tests/settings_manager/test_settings_manager_migration.py` (NEW — 4 transforms)
- `tests/settings_manager/test_settings_manager_locking.py` (NEW — concurrent + cross-process)

**Commit 5** — `feat(harness): SettingsManager integration + AgentHarness.reload() stub (Sprint 6h₇b §E)`
- `packages/aelix-agent-core/src/aelix_agent_core/harness/core.py` (MODIFIED — `settings_manager` field on `AgentHarnessOptions` + property + `reload()` method)
- `tests/harness/test_harness_reload.py` (NEW)

**Commit 6** — `docs: ADR-0091 (Sprint 6h₇b closure) + ADR-0034/README amends`
- `docs/decisions/0091-sprint-6h7b-phase-5a-iii-beta.md` (NEW)
- `docs/decisions/0034-pi-reference-version-pin.md` (AMEND — append row)
- `docs/decisions/README.md` (AMEND — append ADR-0091 row)

Each commit uses HEREDOC + trailer (no shortcut):
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

---

## §I — Verification gates

After each commit:
- `uv run ruff check 2>&1 | tail -2` — clean.
- `uv run pyright 2>&1 | tail -3` — 8 baseline preserved (no new errors from new code; `scripts/pyright_spike.py` untouched).
- `uv run pytest 2>&1 | tail -3` — current 2116 + N new pass, 1 skipped (estimate N ≈ 50-80).

After C2 smoke:
```bash
uv run python -c "from aelix_ai.settings import SettingsManager; m = SettingsManager.in_memory(); print(m.get_default_thinking_level())"
```
Expected: `None` (default for unset field; in-memory factory uses empty `Settings()`).

After C5 smoke (integration script):
1. Construct `InMemorySettingsStorage`-backed `SettingsManager`.
2. Construct `AgentHarness(options=AgentHarnessOptions(settings_manager=m, ...))`.
3. Mutate storage backing dict directly (simulate disk edit).
4. `await harness.reload()`.
5. Assert `harness.settings_manager.get_settings()` reflects new value.

Final: ~2166-2196 pass + 1 skipped. RPC roster 29/0/29 unchanged.

---

## §J — Workflow

W2 executor (opus for C2/C3 due to size; sonnet acceptable for C1/C4/C5/C6) → W3 verification → W4 code-reviewer (opus) → W5 critic (opus, Pi parity audit via curl against pinned SHA) → W6 commits (NO push — user pushes).

---

## §K — Out-of-scope (BINDING — 10 non-goals)

1. **NO** `resetApiProviders` Aelix equivalent (ADR-0087 P-380 primitive #2).
2. **NO** `_resourceLoader.reload` / ResourceLoader port (P-380 primitive #3; Phase 5b).
3. **NO** `_buildRuntime()` invocation in reload (P-380 primitive #5).
4. **NO** extension shutdown / session_start re-emit in reload (Phase 5b).
5. **NO** wire of 9 Pi consumption sites (Phase 5b TUI command layer per Agent E §H).
6. **NO** Phase 5b TUI work (theme reads, branchSummary skipPrompt UI, etc.).
7. **NO** tool config reads (`getImageAutoResize`, `getShellCommandPrefix`, `getShellPath`) — Phase 5b tools/bash.
8. **NO** `filelock` package addition (use `fcntl.flock` for AuthStorage parity; zero new deps).
9. **NO** Pi pin advance.
10. **NO** new RPC commands (RPC roster stays 29/0/29).

---

## §L — Sprint 6h₇c+ preview (carry-forward — ADR-0087 P-380 remaining 4 primitives)

- `resetApiProviders` Aelix equivalent.
- `_resourceLoader.reload` (depends on ResourceLoader port — separate sprint).
- `_buildRuntime` call in reload (Phase 5b).
- Extension shutdown/start re-emit (Phase 5b).
- 9 Pi consumption site wiring (Phase 5b TUI command layer).

**Binding principle echo:** pi agent를 완전 동일하게 완벽하게 구현이 1차적 목표입니다.

**End of spec.**
