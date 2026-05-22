# 0091. Sprint 6h₇b Phase 5a-iii-β — SettingsManager Standalone Port

Status: Accepted (Sprint 6h₇b / Phase 5a-iii-β / W6 shipped)
Date: 2026-05-22
Pi pin: `earendil-works/pi@734e08edf82ff315bc3d96472a6ebfa69a1d8016` (no advance)

Top-level principle (binding): **"pi agent를 완전 동일하게 완벽하게 구현이
1차적 목표입니다."**

## Context

Sprint 6h₇a (ADR-0090) closed two deferred Phase 5a-iii carry-forwards
(`--list-models` + `--append-system-prompt`) without paying for the full
Pi 5a-iii surface. The remaining binding item was the full
`SettingsManager` standalone port — deferred to Sprint 6h₇b per
ADR-0090 §"deferred items".

Pi `coding-agent/src/core/settings-manager.ts` (SHA `734e08e`,
1067 LOC TypeScript) defines:

- `Settings` interface — 33 optional top-level fields.
- 11 nested types (`CompactionSettings`, `BranchSummarySettings`,
  `ProviderRetrySettings`, `RetrySettings`, `TerminalSettings`,
  `ImageSettings`, `ThinkingBudgetsSettings`, `MarkdownSettings`,
  `WarningSettings`, `PackageSourceObject`, `PackageSource` union).
- 5 Literal union types (`ThinkingLevel`, `SteeringMode`,
  `FollowUpMode`, `DoubleEscapeAction`, `TreeFilterMode`).
- `deepMergeSettings(global, project)` helper (Pi `:116-144`).
- `SettingsStorage` interface + `FileSettingsStorage` + `InMemorySettingsStorage`
  (Pi `:146-239`).
- `SettingsManager` class (Pi `:241-1067`) — 3 lifecycle factories,
  ~80 getters/setters, `migrateSettings` (4 transforms), `reload()`,
  `persistScopedSettings`, `enqueueWrite`, modification tracking.

Additionally, `agent-session.ts:2382-2398` (Pi `reload()`) calls
`settingsManager.reload()` as the first step of the harness reload
sequence. Sprint 6h₇b adds the `AgentHarness.reload()` stub that
covers this first step only (the remaining 4 P-380 primitives are
deferred per ADR-0087 ledger).

## Decision

### §B — Port Pi `settings-manager.ts` → Python `aelix_ai/settings/`

Three Python modules mirror the Pi module structure:

**`packages/aelix-ai/src/aelix_ai/settings/types.py`** — Pi parity
`settings-manager.ts:8-113`.

- `DEFAULT_THINKING_LEVEL: Final[str] = "medium"` — Pi `defaults.ts`.
- 5 Literal type aliases: `ThinkingLevel`, `SteeringMode`,
  `FollowUpMode`, `DoubleEscapeAction`, `TreeFilterMode`.
- 10 nested `@dataclass` types (all mutable, all fields `None` by
  default where applicable).
- `PackageSource = str | PackageSourceObject` union.
- `Settings` `@dataclass` — 33 optional fields (all `None` defaults).
- `SettingsError` frozen `@dataclass` with `scope: SettingsScope` +
  `error: BaseException`.
- Bidirectional translation dicts (`SETTINGS_PY_TO_JSON`,
  `SETTINGS_JSON_TO_PY`, `NESTED_PY_TO_JSON`, `NESTED_JSON_TO_PY`,
  `SETTINGS_NESTED_CLASSES`) — applied at JSON read/write boundaries
  only (Pi camelCase ↔ Aelix snake_case).

**`packages/aelix-ai/src/aelix_ai/settings/storage.py`** — Pi parity
`settings-manager.ts:146-239`.

- `default_settings_path()` — XDG resolution with `AELIX_SETTINGS_PATH`
  override (sibling of `default_auth_path()`).
- `default_project_settings_path(cwd)` — `Path(cwd) / ".aelix" / "settings.json"`.
- `SettingsStorage` `Protocol` — `with_lock(scope, fn)` synchronous
  abstract method (matches Pi `withLock` synchronous signature).
- `FileSettingsStorage` — dual-layer locking (`asyncio.Lock` +
  `fcntl.flock`), atomic tmp-write-then-replace, `0o644` permissions.
- `InMemorySettingsStorage` — test backend with no I/O.
- `_AsyncLockRegistry` — per-scope `asyncio.Lock` registry (in-process
  write serialization; Aelix-additive over Pi's single-loop `Promise`
  chaining).

**`packages/aelix-ai/src/aelix_ai/settings/settings_manager.py`** — Pi
parity `settings-manager.ts:116-1067`.

- `deep_merge_settings(base, overrides) -> Settings` — Pi `:116-144`
  nested field-by-field merge; overrides win on conflict.
- JSON boundary helpers: `_json_dict_to_settings`, `_settings_to_json_dict`,
  `_json_dict_to_nested`, `_nested_to_json_dict` — applied at
  read/write boundaries only.
- `SettingsManager` class — private `__init__`, 3 static factories:
  - `create(cwd, agent_dir=None)` — disk-backed `FileSettingsStorage`.
  - `from_storage(storage)` — accepts any `SettingsStorage` Protocol.
  - `in_memory(settings=None)` — accepts `Settings | dict | None`.
- `migrate_settings(dict)` — static, 4 Pi `:334-393` transforms:
  1. `queueMode` → `steeringMode` (Pi `:336-339`).
  2. `websockets: bool` → `transport: "websocket" | "sse"` (Pi `:342-345`).
  3. `skills` object → array + `enableSkillCommands` lift (Pi `:348-366`).
  4. `retry.maxDelayMs` → `retry.provider.maxRetryDelayMs` (Pi `:369-390`).
- `reload()` async — Pi `:403-429` 5-step lifecycle:
  1. Drain pending write queue (`flush()`).
  2. Load global storage → `_global_settings` (or capture error).
  3. Clear all 4 modification tracking sets.
  4. Load project storage → `_project_settings` (or capture error).
  5. Re-merge via `deep_merge_settings` → assign to `_settings`.
- `_persist_scoped_settings(scope, ...)` — Pi `:493-522` re-read-before-write
  merge; preserves unmodified keys changed externally (bug-fix parity).
- `_enqueue_write(scope)` — schedules `asyncio.ensure_future(_write_task(...))`.
- ~80 getters/setters — snake_case Pi parity (Pi `:566-1066`), defaults
  applied inside each getter, NOT on the dataclass field.
- `drain_errors()` — returns and clears `list[SettingsError]`.

### §C — Storage: `fcntl.flock` instead of `proper-lockfile`

Pi uses `proper-lockfile` directory-lock primitives.
Aelix uses `fcntl.flock(LOCK_EX)` mirroring the `AuthStorage` Sprint 6c
precedent (`packages/aelix-ai/src/aelix_ai/oauth/auth_storage.py`).

Dual-layer locking strategy:

- `asyncio.Lock` — per-scope in-process write serialization (one per
  global/project scope, held in `_AsyncLockRegistry`).
- `fcntl.flock(LOCK_EX)` — cross-process exclusive lock on the settings
  file (POSIX only; Windows fallback deferred to Sprint 6h₇c+).

Atomic write path: open tmp file with `os.O_CREAT | os.O_WRONLY |
O_TRUNC` → `os.write` → `os.fsync` → `os.close` → `os.replace(tmp,
target)` + `os.chmod(target, 0o644)`.

File permissions: `0o644` (non-secret config). Auth credentials use
`0o600`; the distinction is intentional.

### §D — `.aelix/` namespace (Aelix-additive divergence)

Pi global settings path: `~/.pi/agent/settings.json`.
Aelix global settings path: `$XDG_CONFIG_HOME/aelix/agent/settings.json`
(fallback: `~/.config/aelix/agent/settings.json`).

Pi project settings path: `./.pi/settings.json`.
Aelix project settings path: `./.aelix/settings.json`.

This follows the ADR-0034 Pi pin namespace decision applied consistently
across `AuthStorage` (Sprint 6c), `AgentDir` (Sprint 6h₆), and now
`SettingsStorage`.

`PI_CLEAR_ON_SHRINK` and `PI_HARDWARE_CURSOR` env var names are retained
verbatim (no `AELIX_*` rename) — TUI-side concern deferred to Phase 5b.

### §E — `AgentHarness.reload()` stub + `settings_manager` integration

**`packages/aelix-agent-core/src/aelix_agent_core/harness/core.py`**:

- `from aelix_ai.settings import SettingsManager` added to the
  `if TYPE_CHECKING:` block (avoids runtime import cycle;
  `aelix-ai` has zero imports from `aelix_coding_agent` — confirmed
  by grep at sprint close).
- `AgentHarnessOptions.settings_manager: "SettingsManager | None" = None`
  field added after `_branch_summarizer_override`.
- `AgentHarness.__init__`: `self._settings_manager = options.settings_manager`.
- `AgentHarness.settings_manager` read-through property — returns
  `self._settings_manager`.
- `AgentHarness.reload()` async method — Pi parity
  `agent-session.ts:2382-2398`:
  1. Guard: `settings_manager` must not be `None` (raises
     `AgentHarnessError("invalid_state", "reload() requires
     options.settings_manager to be attached")`).
  2. `await self._settings_manager.reload()`.
  3. `await self._emit_resources_discover("reload")`.

The remaining 4 P-380 primitives (`resetApiProviders` /
`_resourceLoader.reload` / `flagValues` round-trip / `_buildRuntime`)
remain deferred per ADR-0087.

### §F — Aelix-additive tests (Pi suite gaps)

Pi's `settings-manager.test.ts` (18 main) + `settings-manager-bug.test.ts`
(4 regression) have no migration tests and no lock contention tests.
Aelix adds:

- `tests/settings_manager/test_settings_manager_migration.py` — 16
  tests covering all 4 `migrate_settings` transforms including edge
  cases (already-migrated, partial, combined).
- `tests/settings_manager/test_settings_manager_locking.py` — 3 tests
  covering `asyncio.Lock` in-process serialization + cross-process
  `fcntl.flock` contention (using `subprocess.Popen` to hold the lock
  from a sibling process). Module-level `pytestmark` with
  `pytest.mark.skipif(sys.platform == "win32", ...)` guards POSIX-only
  tests.
- `tests/harness/test_harness_reload.py` — 4 tests:
  `reload()` raises `invalid_state` when no `settings_manager`,
  `settings_manager` property returns attached instance,
  `settings_manager` property returns `None` when omitted,
  `reload()` delegates to `settings_manager.reload()` and refreshes.

## Aelix-additive divergences from Pi

1. **`fcntl.flock` instead of `proper-lockfile` directory-locks** —
   AuthStorage Sprint 6c precedent; POSIX-only (Windows deferred).
2. **`.aelix/` namespace instead of `.pi/`** — Pi pin policy /
   ADR-0034 namespace decision.
3. **Settings file in `aelix_ai/settings/` instead of
   `coding-agent/core/`** — Python package boundary; placed alongside
   `oauth/` to group config-persistence concerns.
4. **Migration tests added** — Aelix-additive; Pi test suite has no
   coverage for the 4 `migrateSettings` transforms.
5. **Lock contention tests added** — Aelix-additive; Pi test suite has
   no lock contention tests. Aelix adds 3 tests using `subprocess.Popen`
   for realistic cross-process `fcntl.flock` contention.
6. **File permissions `0o644`** — non-secret config. Pi does not specify
   permissions; Aelix follows `AuthStorage` precedent (0o600 for
   secrets, 0o644 for config).
7. **`PI_CLEAR_ON_SHRINK` / `PI_HARDWARE_CURSOR` env var names retained**
   — TUI-side concern; no `AELIX_*` rename until Phase 5b.
8. **`AgentHarness.reload()` is a 2-op stub** — ships exactly two Pi
   `agent-session.ts:2382-2398` operations: (a) `await
   self._settings_manager.reload()` (Pi `:2386`) and (b) `await
   self._emit_resources_discover("reload")` (mirrors Pi `:2402`
   `extendResourcesFromExtensions("reload")`). Spec §E.4 originally
   declared a "single-op stub"; the W2 executor expanded to 2 ops for
   Pi fidelity since both are real Pi reload steps. W5 MAJOR-2
   fold-in: this divergence is the intentional scope expansion. The
   remaining 4 P-380 primitives (resetApiProviders, _resourceLoader,
   _buildRuntime, extension lifecycle re-emit) stay deferred per
   ADR-0087.
9. **`SettingsStorage` Protocol narrowed to `with_lock` only** — spec
   §C.1 sketched a 3-method Protocol (`load_global`, `load_project`,
   `with_lock`); the implementation collapses to the single
   `with_lock(scope, fn)` method matching Pi `settings-manager.ts:148-150`
   exactly. The spec sketch was an intermediate design draft; the
   shipped Protocol is closer to Pi parity. W4 MINOR-4 fold-in.
10. **`AELIX_SETTINGS_PATH` env var honored end-to-end** —
    `SettingsManager.create()` now plumbs the full override path
    (including filename) into `FileSettingsStorage` via a new optional
    `global_path` constructor arg. Previously the env override's
    filename was silently dropped because `default_settings_path().parent`
    discarded it and `FileSettingsStorage` always derived
    `agent_dir / "settings.json"`. W5 MAJOR-3 fold-in (real bug fix,
    not just documentation).

## Deferred items (carry-forward to Sprint 6h₇c+)

- `resetApiProviders()` Aelix equivalent (ADR-0087 P-380 primitive #2).
- `_resourceLoader.reload()` integration (P-380 primitive #3; depends
  on ResourceLoader port).
- `_buildRuntime()` call in reload (P-380 primitive #5).
- Extension shutdown/start re-emit in reload (Phase 5b).
- 9 Pi consumption site wiring (Phase 5b TUI command layer — NOT in
  6h₇b scope per spec §K).
- Windows `fcntl.flock` fallback (Sprint 6h₇c+).

## Pi citations (SHA `734e08edf82ff315bc3d96472a6ebfa69a1d8016`)

- `core/settings-manager.ts:1-1067` — module structure.
- `core/settings-manager.ts:116-144` — `deepMergeSettings`.
- `core/settings-manager.ts:334-393` — `migrateSettings` (4 transforms).
- `core/settings-manager.ts:403-429` — `reload()` body.
- `core/settings-manager.ts:493-522` — `persistScopedSettings` body.
- `agent-session.ts:2382-2398` — `reload()` Pi parity target for
  `AgentHarness.reload()` stub.

## Reference companions

- ADR-0090 — Sprint 6h₇a (5a-iii-α closure; carry-forward source).
- ADR-0087 — P-380 reload primitives ledger.
- ADR-0086 — carry-forward catalog (A 단계 closure).
- ADR-0034 — Pi pin (namespace policy).

## Verification

- `ruff check` — clean.
- `pyright` — 8 baseline errors (intentional fixtures in
  `scripts/pyright_spike.py`); no new errors introduced.
- `pytest` — 2116 + N new tests pass (N = 7 types + 31 manager +
  4 bug + ~89 getters/setters + 16 migration + 3 locking + 4 harness).
- RPC roster STAYS CLOSED at **29 supported / 0 deferred / 29 total**.
- Pi pin held at `734e08e` (no advance — Sprint 6h₇b imports no new
  Pi feature beyond the pinned SHA).

## Phase

Sprint 6h₇b / Phase 5a-iii-β (shipped).
