"""SettingsManager — Sprint 6h₇b · Phase 5a-iii-β · §B/§C/§D.

Pi parity: ``packages/coding-agent/src/core/settings-manager.ts:241-1067``
(SHA ``734e08edf82ff315bc3d96472a6ebfa69a1d8016``).

Standalone port of Pi's ``SettingsManager`` class with:

- 3 static factories (:meth:`create`, :meth:`from_storage`,
  :meth:`in_memory`).
- :meth:`reload` 5-step lifecycle (Pi `:403-429`).
- :meth:`migrate_settings` 4 transforms (Pi `:334-393`).
- :meth:`_persist_scoped_settings` re-read-merge write (Pi `:493-522`).
- :meth:`deep_merge_settings` recursive merge helper (Pi `:116-144`).
- ~80 getters / setters mirroring Pi `:395-1066` verbatim. Pi
  ``camelCase`` method names are converted to ``snake_case`` per Aelix
  convention; Pi names cited in docstrings.
- Modification tracking (P-429): ``_modified_fields`` /
  ``_modified_nested_fields`` + project equivalents, cleared AFTER a
  successful write and BEFORE :meth:`reload` remerge.
- Async write queue via :class:`asyncio.Lock` (Aelix replacement for
  Pi's ``writeQueue: Promise<void>`` chaining; serializes writes from
  the same event loop). Cross-process serialization stays in
  :class:`aelix_ai.settings.storage.FileSettingsStorage` via
  ``fcntl.flock``.

Env-var fallbacks (Aelix-retained per Pi parity — documented in
ADR-0091; ``AELIX_*`` rename deferred to Phase 5b TUI surface):

- ``PI_CLEAR_ON_SHRINK == "1"`` — fallback for ``terminal.clearOnShrink``.
- ``PI_HARDWARE_CURSOR == "1"`` — fallback for ``showHardwareCursor``.

Clamp ranges (Pi parity):

- ``editor_padding_x`` clamped to ``[0, 3]`` (Pi `:1039`).
- ``autocomplete_max_visible`` clamped to ``[3, 20]`` (Pi `:1049`).
- ``image_width_cells`` clamped to ``[1, ∞)`` (Pi `:924, :931`).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

from aelix_ai.settings.storage import (
    FileSettingsStorage,
    InMemorySettingsStorage,
    SettingsStorage,
    _AsyncLockRegistry,
    default_settings_path,
)
from aelix_ai.settings.types import (
    NESTED_JSON_TO_PY,
    NESTED_PY_TO_JSON,
    SETTINGS_JSON_TO_PY,
    SETTINGS_NESTED_CLASSES,
    SETTINGS_PY_TO_JSON,
    CompactionSettings,
    DoubleEscapeAction,
    FollowUpMode,
    ImageSettings,
    PackageSource,
    PackageSourceObject,
    ProviderRetrySettings,
    RetrySettings,
    Settings,
    SettingsError,
    SettingsScope,
    SteeringMode,
    TerminalSettings,
    ThinkingBudgetsSettings,
    ThinkingLevel,
    TransportSetting,
    TreeFilterMode,
    WarningSettings,
)

_LOG = logging.getLogger(__name__)


# === JSON boundary helpers ===
# Pi JSON uses camelCase verbatim; Aelix internal Python uses
# snake_case. Translation happens at the JSON read/write boundary only.


def _nested_class_for_field(py_field: str) -> type | None:
    return SETTINGS_NESTED_CLASSES.get(py_field)


def _json_dict_to_settings(raw: dict[str, Any]) -> Settings:
    """Convert a Pi-shape JSON dict (camelCase) to a :class:`Settings`."""

    kwargs: dict[str, Any] = {}
    for json_key, value in raw.items():
        py_field = SETTINGS_JSON_TO_PY.get(json_key)
        if py_field is None:
            # Unknown field — drop silently (Pi parity: TS strips unknown
            # via the typed ``settings as Settings`` cast).
            continue
        nested_cls = _nested_class_for_field(py_field)
        if nested_cls is not None and isinstance(value, dict):
            kwargs[py_field] = _json_dict_to_nested(nested_cls, value)
        elif py_field == "packages" and isinstance(value, list):
            kwargs[py_field] = _json_list_to_packages(value)
        else:
            kwargs[py_field] = value
    return Settings(**kwargs)


def _json_dict_to_nested(cls: type, raw: dict[str, Any]) -> Any:
    """Hydrate a nested dataclass instance from a Pi-shape JSON dict."""

    json_to_py = NESTED_JSON_TO_PY.get(cls.__name__, {})
    kwargs: dict[str, Any] = {}
    for json_key, value in raw.items():
        py_field = json_to_py.get(json_key)
        if py_field is None:
            continue
        # RetrySettings.provider is itself a nested ProviderRetrySettings.
        if (
            cls is RetrySettings
            and py_field == "provider"
            and isinstance(value, dict)
        ):
            kwargs[py_field] = _json_dict_to_nested(
                ProviderRetrySettings, value
            )
        else:
            kwargs[py_field] = value
    return cls(**kwargs)


def _json_list_to_packages(raw: list[Any]) -> list[PackageSource]:
    """Convert a Pi JSON ``packages`` list to the Aelix union shape."""

    out: list[PackageSource] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            json_to_py = NESTED_JSON_TO_PY.get("PackageSourceObject", {})
            kwargs: dict[str, Any] = {}
            for json_key, value in item.items():
                py_field = json_to_py.get(json_key)
                if py_field is None:
                    continue
                kwargs[py_field] = value
            out.append(PackageSourceObject(**kwargs))
        else:
            # Malformed entry — drop silently for Pi parity.
            continue
    return out


def _settings_to_json_dict(settings: Settings) -> dict[str, Any]:
    """Convert a :class:`Settings` to a Pi-shape JSON dict (camelCase).

    Omits fields that are :data:`None` so the on-disk file stays
    Pi-shaped (Pi uses ``?:`` optionals which serialize to ``undefined``
    → missing in JSON).
    """

    out: dict[str, Any] = {}
    for py_field, json_key in SETTINGS_PY_TO_JSON.items():
        value = getattr(settings, py_field, None)
        if value is None:
            continue
        if py_field == "packages":
            out[json_key] = [_package_to_json(p) for p in value]
        elif _nested_class_for_field(py_field) is not None:
            out[json_key] = _nested_to_json_dict(value)
        else:
            out[json_key] = _scalar_or_list_to_json(value)
    return out


def _nested_to_json_dict(obj: Any) -> dict[str, Any]:
    """Convert a nested dataclass instance to its Pi-shape JSON dict."""

    if obj is None or not is_dataclass(obj):
        return {}
    py_to_json = NESTED_PY_TO_JSON.get(type(obj).__name__, {})
    out: dict[str, Any] = {}
    for f in dataclass_fields(obj):
        value = getattr(obj, f.name)
        if value is None:
            continue
        json_key = py_to_json.get(f.name, f.name)
        if isinstance(value, ProviderRetrySettings):
            out[json_key] = _nested_to_json_dict(value)
        else:
            out[json_key] = value
    return out


def _package_to_json(p: PackageSource) -> Any:
    if isinstance(p, str):
        return p
    return _nested_to_json_dict(p)


def _scalar_or_list_to_json(value: Any) -> Any:
    # Defensive copy for lists so callers can mutate without affecting
    # the in-memory settings.
    if isinstance(value, list):
        return list(value)
    return value


def deep_merge_settings(base: Settings, overrides: Settings) -> Settings:
    """Pi parity: ``settings-manager.ts:116-144`` ``deepMergeSettings``.

    Per-field deep merge: ``overrides`` wins on conflict; nested
    dataclasses merge field-by-field (overrides take precedence per
    nested key); ``None`` values in ``overrides`` are SKIPPED (matching
    Pi's ``if (overrideValue === undefined) continue`` guard). Lists +
    primitives — override value wins outright.
    """

    result = copy.deepcopy(base)
    for f in dataclass_fields(overrides):
        override_value = getattr(overrides, f.name)
        if override_value is None:
            continue
        base_value = getattr(result, f.name)
        # Nested dataclass merge (recursive per-field).
        if (
            is_dataclass(override_value)
            and not isinstance(override_value, type)
            and is_dataclass(base_value)
            and not isinstance(base_value, type)
        ):
            merged = copy.deepcopy(base_value)
            for inner in dataclass_fields(override_value):
                inner_val = getattr(override_value, inner.name)
                if inner_val is not None:
                    setattr(merged, inner.name, copy.deepcopy(inner_val))
            setattr(result, f.name, merged)
        else:
            setattr(result, f.name, copy.deepcopy(override_value))
    return result


class SettingsManager:
    """Pi parity: ``settings-manager.ts:241-1067`` ``SettingsManager``.

    Constructor is private by convention — callers should use one of
    the three static factories: :meth:`create`, :meth:`from_storage`,
    :meth:`in_memory`. Construction itself is synchronous (Pi parity);
    the three factories are also synchronous despite being typed as
    ``staticmethod`` here so they can be used in async + sync contexts
    without ``await``. :meth:`reload`, :meth:`flush`, and the setter
    side-effects are async (Aelix replaces Pi's ``Promise<void>``
    write-queue chain with :class:`asyncio.Lock`).
    """

    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: Settings,
        initial_project: Settings,
        global_load_error: BaseException | None = None,
        project_load_error: BaseException | None = None,
        initial_errors: list[SettingsError] | None = None,
    ) -> None:
        self._storage: SettingsStorage = storage
        self._global_settings: Settings = initial_global
        self._project_settings: Settings = initial_project
        self._global_load_error: BaseException | None = global_load_error
        self._project_load_error: BaseException | None = project_load_error
        self._errors: list[SettingsError] = list(initial_errors or [])
        self._settings: Settings = deep_merge_settings(
            initial_global, initial_project
        )
        # Modification tracking (Pi parity P-429).
        self._modified_fields: set[str] = set()
        self._modified_nested_fields: dict[str, set[str]] = {}
        self._modified_project_fields: set[str] = set()
        self._modified_project_nested_fields: dict[str, set[str]] = {}
        # Async write queue — single per-scope :class:`asyncio.Lock`.
        # Pi chains ``writeQueue: Promise<void>``; Aelix uses a lock to
        # serialize writes from the same event loop. Cross-process
        # serialization handled by :class:`FileSettingsStorage` flock.
        self._async_locks = _AsyncLockRegistry()
        # In-flight write tracking so :meth:`flush` can ``await`` them.
        self._pending_writes: set[asyncio.Task[None]] = set()

    # === Static factories (Pi parity `:273-306`) ===

    @staticmethod
    def create(
        cwd: str | Path, agent_dir: str | Path | None = None
    ) -> SettingsManager:
        """Pi parity: ``settings-manager.ts:273-276`` ``SettingsManager.create``.

        Resolves ``agent_dir`` via :func:`default_settings_path` when not
        supplied. Mirrors :func:`aelix_ai.oauth.auth_storage.default_auth_path`
        with the ``aelix/agent/`` directory.

        Sprint 6h₇b W5 MAJOR-3 fold-in: when ``AELIX_SETTINGS_PATH`` env
        override is set, the FULL override path (filename included) is
        honored end-to-end via :class:`FileSettingsStorage.global_path`.
        Without the explicit pass-through the storage layer silently
        dropped the override filename and reverted to
        ``<override_dir>/settings.json``.
        """

        override = os.environ.get("AELIX_SETTINGS_PATH")
        if override:
            override_path = Path(override)
            if agent_dir is None:
                agent_dir = override_path.parent
            storage = FileSettingsStorage(
                cwd, agent_dir, global_path=override_path
            )
        else:
            if agent_dir is None:
                # Use the same parent directory the default settings path
                # would resolve to.
                agent_dir = default_settings_path().parent
            storage = FileSettingsStorage(cwd, agent_dir)
        return SettingsManager.from_storage(storage)

    @staticmethod
    def from_storage(storage: SettingsStorage) -> SettingsManager:
        """Pi parity: ``settings-manager.ts:279-298`` ``SettingsManager.fromStorage``."""

        global_settings, global_err = SettingsManager._try_load(
            storage, "global"
        )
        project_settings, project_err = SettingsManager._try_load(
            storage, "project"
        )
        initial_errors: list[SettingsError] = []
        if global_err is not None:
            initial_errors.append(
                SettingsError(scope="global", error=global_err)
            )
        if project_err is not None:
            initial_errors.append(
                SettingsError(scope="project", error=project_err)
            )
        return SettingsManager(
            storage,
            global_settings,
            project_settings,
            global_err,
            project_err,
            initial_errors,
        )

    @staticmethod
    def in_memory(
        settings: Settings | dict[str, Any] | None = None,
    ) -> SettingsManager:
        """Pi parity: ``settings-manager.ts:301-306`` ``SettingsManager.inMemory``.

        Accepts either a :class:`Settings` dataclass (Aelix-native) or a
        Pi-shape JSON dict (for test fidelity). The dict form runs
        through :meth:`migrate_settings` so callers can simulate legacy
        settings being upgraded on first read.

        Storage backend is :class:`InMemorySettingsStorage` — no disk I/O.
        The Protocol's ``with_lock`` callback is still invoked
        synchronously to populate state via the canonical read/write
        path; only the file-system layer is bypassed.
        """

        storage = InMemorySettingsStorage()
        if settings is None:
            initial_dict: dict[str, Any] = {}
        elif isinstance(settings, Settings):
            initial_dict = _settings_to_json_dict(settings)
        else:
            initial_dict = SettingsManager.migrate_settings(dict(settings))
        if initial_dict:
            storage.with_lock(
                "global", lambda _: json.dumps(initial_dict, indent=2)
            )
        return SettingsManager.from_storage(storage)

    @staticmethod
    def _load_from_storage(
        storage: SettingsStorage, scope: SettingsScope
    ) -> Settings:
        """Pi parity: ``settings-manager.ts:308-320`` ``loadFromStorage``."""

        content_holder: list[str | None] = [None]

        def _read(current: str | None) -> str | None:
            content_holder[0] = current
            return None

        storage.with_lock(scope, _read)
        content = content_holder[0]
        if not content:
            return Settings()
        raw = json.loads(content)
        migrated = SettingsManager.migrate_settings(raw)
        return _json_dict_to_settings(migrated)

    @staticmethod
    def _try_load(
        storage: SettingsStorage, scope: SettingsScope
    ) -> tuple[Settings, BaseException | None]:
        """Pi parity: ``settings-manager.ts:322-331`` ``tryLoadFromStorage``."""

        try:
            return (
                SettingsManager._load_from_storage(storage, scope),
                None,
            )
        except BaseException as exc:  # noqa: BLE001 — Pi parity
            return (Settings(), exc)

    # === Migration (Pi parity `:334-393`) ===

    @staticmethod
    def migrate_settings(settings: dict[str, Any]) -> dict[str, Any]:
        """Pi parity: ``settings-manager.ts:334-393`` ``migrateSettings``.

        In-place mutation of the JSON dict (Pi-shape camelCase keys),
        returns the migrated dict. 4 transforms:

        1. ``queueMode`` -> ``steeringMode`` (if target absent).
        2. ``websockets: bool`` -> ``transport: "websocket"|"sse"`` (if
           target absent).
        3. ``skills`` object form -> ``skills`` array + extract
           ``enableSkillCommands`` to top-level.
        4. ``retry.maxDelayMs`` -> ``retry.provider.maxRetryDelayMs``.
        """

        # 1. queueMode -> steeringMode
        if "queueMode" in settings and "steeringMode" not in settings:
            settings["steeringMode"] = settings["queueMode"]
            del settings["queueMode"]

        # 2. websockets boolean -> transport enum
        if (
            "transport" not in settings
            and isinstance(settings.get("websockets"), bool)
        ):
            settings["transport"] = (
                "websocket" if settings["websockets"] else "sse"
            )
            del settings["websockets"]

        # 3. skills object -> array (extract enableSkillCommands).
        # ORDERING: ``enableSkillCommands`` MUST be extracted to the
        # top level BEFORE the unconditional ``del settings["skills"]``
        # in the empty/None ``customDirectories`` branch. Pi parity
        # ``settings-manager.ts:348-366``. W4 MAJOR-2 fold-in.
        if (
            "skills" in settings
            and isinstance(settings["skills"], dict)
            and not isinstance(settings["skills"], list)
        ):
            skills_obj = settings["skills"]
            enable = skills_obj.get("enableSkillCommands")
            if (
                enable is not None
                and settings.get("enableSkillCommands") is None
            ):
                settings["enableSkillCommands"] = enable
            custom = skills_obj.get("customDirectories")
            if isinstance(custom, list) and len(custom) > 0:
                settings["skills"] = custom
            else:
                del settings["skills"]

        # 4. retry.maxDelayMs -> retry.provider.maxRetryDelayMs
        if (
            "retry" in settings
            and isinstance(settings["retry"], dict)
            and not isinstance(settings["retry"], list)
        ):
            retry_obj: dict[str, Any] = settings["retry"]
            provider_obj = retry_obj.get("provider")
            if not isinstance(provider_obj, dict):
                provider_obj = None
            max_delay = retry_obj.get("maxDelayMs")
            if isinstance(max_delay, (int, float)):
                existing_max_retry_delay = (
                    provider_obj.get("maxRetryDelayMs")
                    if provider_obj is not None
                    else None
                )
                if existing_max_retry_delay is None:
                    new_provider = (
                        dict(provider_obj) if provider_obj is not None else {}
                    )
                    new_provider["maxRetryDelayMs"] = max_delay
                    retry_obj["provider"] = new_provider
                retry_obj.pop("maxDelayMs", None)

        return settings

    # === Modification tracking (P-429) ===

    def _mark_modified(
        self, field_name: str, nested_key: str | None = None
    ) -> None:
        """Pi parity: ``settings-manager.ts:436-445`` ``markModified``."""

        self._modified_fields.add(field_name)
        if nested_key is not None:
            self._modified_nested_fields.setdefault(field_name, set()).add(
                nested_key
            )

    def _mark_project_modified(
        self, field_name: str, nested_key: str | None = None
    ) -> None:
        """Pi parity: ``settings-manager.ts:447-456`` ``markProjectModified``."""

        self._modified_project_fields.add(field_name)
        if nested_key is not None:
            self._modified_project_nested_fields.setdefault(
                field_name, set()
            ).add(nested_key)

    def _record_error(
        self, scope: SettingsScope, error: BaseException
    ) -> None:
        """Pi parity: ``settings-manager.ts:458-461`` ``recordError``."""

        self._errors.append(SettingsError(scope=scope, error=error))

    def _clear_modified_scope(self, scope: SettingsScope) -> None:
        """Pi parity: ``settings-manager.ts:463-472`` ``clearModifiedScope``."""

        if scope == "global":
            self._modified_fields.clear()
            self._modified_nested_fields.clear()
        else:
            self._modified_project_fields.clear()
            self._modified_project_nested_fields.clear()

    # === Public read surface ===

    def get_settings(self) -> Settings:
        """Pi parity: merged-view settings (Pi `settings`).

        Returns a deep copy so caller mutations don't leak into the
        manager.
        """

        return copy.deepcopy(self._settings)

    def get_global_settings(self) -> Settings:
        """Pi parity: ``settings-manager.ts:395-397`` ``getGlobalSettings``."""

        return copy.deepcopy(self._global_settings)

    def get_project_settings(self) -> Settings:
        """Pi parity: ``settings-manager.ts:399-401`` ``getProjectSettings``."""

        return copy.deepcopy(self._project_settings)

    # === reload + applyOverrides + drainErrors ===

    async def reload(self) -> None:
        """Pi parity: ``settings-manager.ts:403-429`` ``reload``.

        5-step lifecycle (P-425):

        1. Drain pending writes.
        2. Load global storage -> ``_global_settings`` (or capture error).
        3. Clear all 4 modification tracking sets (Pi `:414-417`).
        4. Load project storage -> ``_project_settings`` (or capture error).
        5. Re-merge via :func:`deep_merge_settings`.
        """

        await self.flush()
        global_settings, global_err = SettingsManager._try_load(
            self._storage, "global"
        )
        if global_err is None:
            self._global_settings = global_settings
            self._global_load_error = None
        else:
            self._global_load_error = global_err
            self._record_error("global", global_err)

        self._modified_fields.clear()
        self._modified_nested_fields.clear()
        self._modified_project_fields.clear()
        self._modified_project_nested_fields.clear()

        project_settings, project_err = SettingsManager._try_load(
            self._storage, "project"
        )
        if project_err is None:
            self._project_settings = project_settings
            self._project_load_error = None
        else:
            self._project_load_error = project_err
            self._record_error("project", project_err)

        self._settings = deep_merge_settings(
            self._global_settings, self._project_settings
        )

    def apply_overrides(self, overrides: Settings) -> None:
        """Pi parity: ``settings-manager.ts:432-434`` ``applyOverrides``."""

        self._settings = deep_merge_settings(self._settings, overrides)

    def drain_errors(self) -> list[SettingsError]:
        """Pi parity: ``settings-manager.ts:560-564`` ``drainErrors``."""

        drained = list(self._errors)
        self._errors = []
        return drained

    async def flush(self) -> None:
        """Pi parity: ``settings-manager.ts:556-558`` ``flush``.

        Awaits any in-flight write tasks scheduled by setters. Pi chains
        the ``writeQueue: Promise<void>`` synchronously inside the
        single-loop Node runtime; Aelix tracks pending tasks explicitly
        so :meth:`flush` can ``await`` them.
        """

        # Snapshot pending and await each. New tasks scheduled during
        # the wait will be appended; the loop drains until empty.
        while self._pending_writes:
            pending = list(self._pending_writes)
            await asyncio.gather(*pending, return_exceptions=True)
            # Remove completed tasks (defensive — the task itself does
            # this via the done callback).
            for t in pending:
                self._pending_writes.discard(t)

    # === Write enqueue helpers ===

    def _enqueue_write(self, scope: SettingsScope) -> None:
        """Pi parity: ``settings-manager.ts:474-483`` ``enqueueWrite``.

        Snapshot the modification tracking sets + the in-memory
        settings, then schedule a write task. The task acquires the
        per-scope async lock to serialize same-loop writes, runs
        :meth:`_persist_scoped_settings` (which itself acquires the
        cross-process flock inside ``storage.with_lock``), and clears
        the per-scope tracking on success.
        """

        if scope == "global":
            if self._global_load_error is not None:
                # Pi parity (`:527-529`): skip enqueue when global load
                # errored — the in-memory state is not safe to overwrite
                # the on-disk corrupted file.
                return
            snapshot_settings = copy.deepcopy(self._global_settings)
            modified_fields = set(self._modified_fields)
            modified_nested = {
                k: set(v) for k, v in self._modified_nested_fields.items()
            }
        else:
            if self._project_load_error is not None:
                return
            snapshot_settings = copy.deepcopy(self._project_settings)
            modified_fields = set(self._modified_project_fields)
            modified_nested = {
                k: set(v)
                for k, v in self._modified_project_nested_fields.items()
            }

        task = asyncio.ensure_future(
            self._write_task(
                scope, snapshot_settings, modified_fields, modified_nested
            )
        )
        self._pending_writes.add(task)
        task.add_done_callback(self._pending_writes.discard)

    async def _write_task(
        self,
        scope: SettingsScope,
        snapshot_settings: Settings,
        modified_fields: set[str],
        modified_nested_fields: dict[str, set[str]],
    ) -> None:
        async with self._async_locks.for_scope(scope):
            try:
                self._persist_scoped_settings(
                    scope,
                    snapshot_settings,
                    modified_fields,
                    modified_nested_fields,
                )
                self._clear_modified_scope(scope)
            except BaseException as exc:  # noqa: BLE001 — Pi parity
                self._record_error(scope, exc)

    def _persist_scoped_settings(
        self,
        scope: SettingsScope,
        snapshot_settings: Settings,
        modified_fields: set[str],
        modified_nested_fields: dict[str, set[str]],
    ) -> None:
        """Pi parity: ``settings-manager.ts:493-522`` ``persistScopedSettings``.

        Read existing on-disk JSON -> migrate -> shallow override modified
        top-level fields -> per-key merge for modified nested fields ->
        write atomic.
        """

        def _builder(current: str | None) -> str | None:
            if current:
                try:
                    parsed = json.loads(current)
                    current_dict = SettingsManager.migrate_settings(parsed)
                except (json.JSONDecodeError, ValueError):
                    current_dict = {}
            else:
                current_dict = {}

            merged: dict[str, Any] = dict(current_dict)
            for py_field in modified_fields:
                json_key = SETTINGS_PY_TO_JSON.get(py_field, py_field)
                value = getattr(snapshot_settings, py_field, None)
                if (
                    py_field in modified_nested_fields
                    and value is not None
                    and is_dataclass(value)
                ):
                    nested_modified = modified_nested_fields[py_field]
                    py_to_json = NESTED_PY_TO_JSON.get(
                        type(value).__name__, {}
                    )
                    # Read base nested dict from current file (preserves
                    # unmodified sibling keys per Pi `:507-510`).
                    base_nested = current_dict.get(json_key) or {}
                    if not isinstance(base_nested, dict):
                        base_nested = {}
                    merged_nested: dict[str, Any] = dict(base_nested)
                    for nested_py_key in nested_modified:
                        nested_json_key = py_to_json.get(
                            nested_py_key, nested_py_key
                        )
                        in_memory_val = getattr(value, nested_py_key, None)
                        if in_memory_val is None:
                            # Defensive only — Aelix nested setters are
                            # typed to non-optional scalars (bool/int/
                            # str/float) so this branch is unreachable
                            # via the public surface today. Pi serializes
                            # ``undefined`` as a missing JSON key; we
                            # mirror that semantic via ``pop`` so a
                            # future ``set_x(None)`` would behave
                            # identically. See W5 MAJOR-1 fold-in.
                            merged_nested.pop(nested_json_key, None)
                        elif isinstance(in_memory_val, ProviderRetrySettings):
                            merged_nested[nested_json_key] = (
                                _nested_to_json_dict(in_memory_val)
                            )
                        else:
                            merged_nested[nested_json_key] = in_memory_val
                    merged[json_key] = merged_nested
                else:
                    # Top-level non-nested field — shallow override.
                    if value is None:
                        merged.pop(json_key, None)
                    elif py_field == "packages":
                        merged[json_key] = [
                            _package_to_json(p) for p in value
                        ]
                    elif is_dataclass(value):
                        merged[json_key] = _nested_to_json_dict(value)
                    elif isinstance(value, list):
                        merged[json_key] = list(value)
                    else:
                        merged[json_key] = value

            return json.dumps(merged, indent=2, ensure_ascii=False)

        self._storage.with_lock(scope, _builder)

    def _save(self) -> None:
        """Pi parity: ``settings-manager.ts:524-538`` ``save``.

        Refresh merged view + enqueue a global write.
        """

        self._settings = deep_merge_settings(
            self._global_settings, self._project_settings
        )
        self._enqueue_write("global")

    def _save_project_settings(self, settings: Settings) -> None:
        """Pi parity: ``settings-manager.ts:540-554`` ``saveProjectSettings``."""

        self._project_settings = copy.deepcopy(settings)
        self._settings = deep_merge_settings(
            self._global_settings, self._project_settings
        )
        self._enqueue_write("project")

    # =====================================================================
    # === ~80 GETTERS / SETTERS (Pi parity `:566-1066`) ====================
    # =====================================================================

    # --- lastChangelogVersion (Pi `:566-574`) ---
    def get_last_changelog_version(self) -> str | None:
        """Pi parity: ``settings-manager.ts::getLastChangelogVersion`` (line 566-568)."""

        return self._settings.last_changelog_version

    def set_last_changelog_version(self, version: str) -> None:
        """Pi parity: ``settings-manager.ts::setLastChangelogVersion`` (line 570-574)."""

        self._global_settings.last_changelog_version = version
        self._mark_modified("last_changelog_version")
        self._save()

    # --- sessionDir (Pi `:576-588`) ---
    def get_session_dir(self) -> str | None:
        """Pi parity: ``settings-manager.ts::getSessionDir`` (line 576-588).

        Expands ``~`` and ``~/`` prefix; returns the raw value otherwise.
        """

        session_dir = self._settings.session_dir
        if not session_dir:
            return session_dir
        if session_dir == "~":
            return str(Path.home())
        if session_dir.startswith("~/"):
            return str(Path.home() / session_dir[2:])
        return session_dir

    # --- defaultProvider / defaultModel (Pi `:590-616`) ---
    def get_default_provider(self) -> str | None:
        """Pi parity: ``settings-manager.ts::getDefaultProvider`` (line 590-592)."""

        return self._settings.default_provider

    def get_default_model(self) -> str | None:
        """Pi parity: ``settings-manager.ts::getDefaultModel`` (line 594-596)."""

        return self._settings.default_model

    def set_default_provider(self, provider: str) -> None:
        """Pi parity: ``settings-manager.ts::setDefaultProvider`` (line 598-602)."""

        self._global_settings.default_provider = provider
        self._mark_modified("default_provider")
        self._save()

    def set_default_model(self, model_id: str) -> None:
        """Pi parity: ``settings-manager.ts::setDefaultModel`` (line 604-608)."""

        self._global_settings.default_model = model_id
        self._mark_modified("default_model")
        self._save()

    def set_default_model_and_provider(
        self, provider: str, model_id: str
    ) -> None:
        """Pi parity: ``settings-manager.ts::setDefaultModelAndProvider`` (line 610-616)."""

        self._global_settings.default_provider = provider
        self._global_settings.default_model = model_id
        self._mark_modified("default_provider")
        self._mark_modified("default_model")
        self._save()

    # --- steeringMode (Pi `:618-626`) ---
    def get_steering_mode(self) -> SteeringMode:
        """Pi parity: ``settings-manager.ts::getSteeringMode`` (line 618-620)."""

        return self._settings.steering_mode or "one-at-a-time"

    def set_steering_mode(self, mode: SteeringMode) -> None:
        """Pi parity: ``settings-manager.ts::setSteeringMode`` (line 622-626)."""

        self._global_settings.steering_mode = mode
        self._mark_modified("steering_mode")
        self._save()

    # --- followUpMode (Pi `:628-636`) ---
    def get_follow_up_mode(self) -> FollowUpMode:
        """Pi parity: ``settings-manager.ts::getFollowUpMode`` (line 628-630)."""

        return self._settings.follow_up_mode or "one-at-a-time"

    def set_follow_up_mode(self, mode: FollowUpMode) -> None:
        """Pi parity: ``settings-manager.ts::setFollowUpMode`` (line 632-636)."""

        self._global_settings.follow_up_mode = mode
        self._mark_modified("follow_up_mode")
        self._save()

    # --- theme (Pi `:638-646`) ---
    def get_theme(self) -> str | None:
        """Pi parity: ``settings-manager.ts::getTheme`` (line 638-640)."""

        return self._settings.theme

    def set_theme(self, theme: str) -> None:
        """Pi parity: ``settings-manager.ts::setTheme`` (line 642-646)."""

        self._global_settings.theme = theme
        self._mark_modified("theme")
        self._save()

    # --- defaultThinkingLevel (Pi `:648-656`) ---
    def get_default_thinking_level(self) -> ThinkingLevel | None:
        """Pi parity: ``settings-manager.ts::getDefaultThinkingLevel`` (line 648-650).

        Returns :data:`None` if unset. Pi getter has no default — callers
        layering ``DEFAULT_THINKING_LEVEL = "medium"`` apply it
        themselves (per Pi `defaults.ts`).
        """

        return self._settings.default_thinking_level

    def set_default_thinking_level(self, level: ThinkingLevel) -> None:
        """Pi parity: ``settings-manager.ts::setDefaultThinkingLevel`` (line 652-656)."""

        self._global_settings.default_thinking_level = level
        self._mark_modified("default_thinking_level")
        self._save()

    # --- transport (Pi `:658-666`) ---
    def get_transport(self) -> TransportSetting:
        """Pi parity: ``settings-manager.ts::getTransport`` (line 658-660). Default: "auto"."""

        return self._settings.transport or "auto"

    def set_transport(self, transport: TransportSetting) -> None:
        """Pi parity: ``settings-manager.ts::setTransport`` (line 662-666)."""

        self._global_settings.transport = transport
        self._mark_modified("transport")
        self._save()

    # --- compaction (Pi `:668-695`) ---
    def get_compaction_enabled(self) -> bool:
        """Pi parity: ``settings-manager.ts::getCompactionEnabled`` (line 668-670). Default: True."""

        comp = self._settings.compaction
        if comp is None or comp.enabled is None:
            return True
        return comp.enabled

    def set_compaction_enabled(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setCompactionEnabled`` (line 672-679)."""

        if self._global_settings.compaction is None:
            self._global_settings.compaction = CompactionSettings()
        self._global_settings.compaction.enabled = enabled
        self._mark_modified("compaction", "enabled")
        self._save()

    def get_compaction_reserve_tokens(self) -> int:
        """Pi parity: ``settings-manager.ts::getCompactionReserveTokens`` (line 681-683). Default: 16384."""

        comp = self._settings.compaction
        if comp is None or comp.reserve_tokens is None:
            return 16384
        return comp.reserve_tokens

    def get_compaction_keep_recent_tokens(self) -> int:
        """Pi parity: ``settings-manager.ts::getCompactionKeepRecentTokens`` (line 685-687). Default: 20000."""

        comp = self._settings.compaction
        if comp is None or comp.keep_recent_tokens is None:
            return 20000
        return comp.keep_recent_tokens

    def get_compaction_settings(self) -> dict[str, Any]:
        """Pi parity: ``settings-manager.ts::getCompactionSettings`` (line 689-695)."""

        return {
            "enabled": self.get_compaction_enabled(),
            "reserveTokens": self.get_compaction_reserve_tokens(),
            "keepRecentTokens": self.get_compaction_keep_recent_tokens(),
        }

    # --- branchSummary (Pi `:697-706`) ---
    def get_branch_summary_settings(self) -> dict[str, Any]:
        """Pi parity: ``settings-manager.ts::getBranchSummarySettings`` (line 697-702)."""

        bs = self._settings.branch_summary
        reserve = (
            bs.reserve_tokens if bs is not None and bs.reserve_tokens is not None else 16384
        )
        skip = (
            bs.skip_prompt if bs is not None and bs.skip_prompt is not None else False
        )
        return {"reserveTokens": reserve, "skipPrompt": skip}

    def get_branch_summary_skip_prompt(self) -> bool:
        """Pi parity: ``settings-manager.ts::getBranchSummarySkipPrompt`` (line 704-706). Default: False."""

        bs = self._settings.branch_summary
        if bs is None or bs.skip_prompt is None:
            return False
        return bs.skip_prompt

    # --- retry (Pi `:708-735`) ---
    def get_retry_enabled(self) -> bool:
        """Pi parity: ``settings-manager.ts::getRetryEnabled`` (line 708-710). Default: True."""

        r = self._settings.retry
        if r is None or r.enabled is None:
            return True
        return r.enabled

    def set_retry_enabled(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setRetryEnabled`` (line 712-719)."""

        if self._global_settings.retry is None:
            self._global_settings.retry = RetrySettings()
        self._global_settings.retry.enabled = enabled
        self._mark_modified("retry", "enabled")
        self._save()

    def get_retry_settings(self) -> dict[str, Any]:
        """Pi parity: ``settings-manager.ts::getRetrySettings`` (line 721-727)."""

        r = self._settings.retry
        return {
            "enabled": self.get_retry_enabled(),
            "maxRetries": (
                r.max_retries if r is not None and r.max_retries is not None else 3
            ),
            "baseDelayMs": (
                r.base_delay_ms if r is not None and r.base_delay_ms is not None else 2000
            ),
        }

    def get_provider_retry_settings(self) -> dict[str, Any]:
        """Pi parity: ``settings-manager.ts::getProviderRetrySettings`` (line 729-735). max_retry_delay_ms default: 60000."""

        r = self._settings.retry
        prov = r.provider if r is not None else None
        return {
            "timeoutMs": prov.timeout_ms if prov is not None else None,
            "maxRetries": prov.max_retries if prov is not None else None,
            "maxRetryDelayMs": (
                prov.max_retry_delay_ms
                if prov is not None and prov.max_retry_delay_ms is not None
                else 60000
            ),
        }

    # --- hideThinkingBlock (Pi `:737-745`) ---
    def get_hide_thinking_block(self) -> bool:
        """Pi parity: ``settings-manager.ts::getHideThinkingBlock`` (line 737-739). Default: False."""

        v = self._settings.hide_thinking_block
        return False if v is None else v

    def set_hide_thinking_block(self, hide: bool) -> None:
        """Pi parity: ``settings-manager.ts::setHideThinkingBlock`` (line 741-745)."""

        self._global_settings.hide_thinking_block = hide
        self._mark_modified("hide_thinking_block")
        self._save()

    # --- shellPath (Pi `:747-755`) ---
    def get_shell_path(self) -> str | None:
        """Pi parity: ``settings-manager.ts::getShellPath`` (line 747-749)."""

        return self._settings.shell_path

    def set_shell_path(self, path: str | None) -> None:
        """Pi parity: ``settings-manager.ts::setShellPath`` (line 751-755)."""

        self._global_settings.shell_path = path
        self._mark_modified("shell_path")
        self._save()

    # --- quietStartup (Pi `:757-765`) ---
    def get_quiet_startup(self) -> bool:
        """Pi parity: ``settings-manager.ts::getQuietStartup`` (line 757-759). Default: False."""

        v = self._settings.quiet_startup
        return False if v is None else v

    def set_quiet_startup(self, quiet: bool) -> None:
        """Pi parity: ``settings-manager.ts::setQuietStartup`` (line 761-765)."""

        self._global_settings.quiet_startup = quiet
        self._mark_modified("quiet_startup")
        self._save()

    # --- shellCommandPrefix (Pi `:767-775`) ---
    def get_shell_command_prefix(self) -> str | None:
        """Pi parity: ``settings-manager.ts::getShellCommandPrefix`` (line 767-769)."""

        return self._settings.shell_command_prefix

    def set_shell_command_prefix(self, prefix: str | None) -> None:
        """Pi parity: ``settings-manager.ts::setShellCommandPrefix`` (line 771-775)."""

        self._global_settings.shell_command_prefix = prefix
        self._mark_modified("shell_command_prefix")
        self._save()

    # --- npmCommand (Pi `:777-785`) ---
    def get_npm_command(self) -> list[str] | None:
        """Pi parity: ``settings-manager.ts::getNpmCommand`` (line 777-779). Returns defensive copy."""

        v = self._settings.npm_command
        return list(v) if v is not None else None

    def set_npm_command(self, command: list[str] | None) -> None:
        """Pi parity: ``settings-manager.ts::setNpmCommand`` (line 781-785)."""

        self._global_settings.npm_command = (
            list(command) if command is not None else None
        )
        self._mark_modified("npm_command")
        self._save()

    # --- collapseChangelog (Pi `:787-795`) ---
    def get_collapse_changelog(self) -> bool:
        """Pi parity: ``settings-manager.ts::getCollapseChangelog`` (line 787-789). Default: False."""

        v = self._settings.collapse_changelog
        return False if v is None else v

    def set_collapse_changelog(self, collapse: bool) -> None:
        """Pi parity: ``settings-manager.ts::setCollapseChangelog`` (line 791-795)."""

        self._global_settings.collapse_changelog = collapse
        self._mark_modified("collapse_changelog")
        self._save()

    # --- enableInstallTelemetry (Pi `:797-805`) ---
    def get_enable_install_telemetry(self) -> bool:
        """Pi parity: ``settings-manager.ts::getEnableInstallTelemetry`` (line 797-799). Default: True."""

        v = self._settings.enable_install_telemetry
        return True if v is None else v

    def set_enable_install_telemetry(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setEnableInstallTelemetry`` (line 801-805)."""

        self._global_settings.enable_install_telemetry = enabled
        self._mark_modified("enable_install_telemetry")
        self._save()

    # --- packages (Pi `:807-822`) ---
    def get_packages(self) -> list[PackageSource]:
        """Pi parity: ``settings-manager.ts::getPackages`` (line 807-809). Returns defensive copy."""

        return list(self._settings.packages or [])

    def set_packages(self, packages: list[PackageSource]) -> None:
        """Pi parity: ``settings-manager.ts::setPackages`` (line 811-815)."""

        self._global_settings.packages = list(packages)
        self._mark_modified("packages")
        self._save()

    def set_project_packages(self, packages: list[PackageSource]) -> None:
        """Pi parity: ``settings-manager.ts::setProjectPackages`` (line 817-822)."""

        project_settings = copy.deepcopy(self._project_settings)
        project_settings.packages = list(packages)
        self._mark_project_modified("packages")
        self._save_project_settings(project_settings)

    # --- extensions (Pi `:824-839`) ---
    def get_extension_paths(self) -> list[str]:
        """Pi parity: ``settings-manager.ts::getExtensionPaths`` (line 824-826). Returns defensive copy."""

        return list(self._settings.extensions or [])

    def set_extension_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setExtensionPaths`` (line 828-832)."""

        self._global_settings.extensions = list(paths)
        self._mark_modified("extensions")
        self._save()

    def set_project_extension_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setProjectExtensionPaths`` (line 834-839)."""

        project_settings = copy.deepcopy(self._project_settings)
        project_settings.extensions = list(paths)
        self._mark_project_modified("extensions")
        self._save_project_settings(project_settings)

    # --- skills (Pi `:841-856`) ---
    def get_skill_paths(self) -> list[str]:
        """Pi parity: ``settings-manager.ts::getSkillPaths`` (line 841-843). Returns defensive copy."""

        return list(self._settings.skills or [])

    def set_skill_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setSkillPaths`` (line 845-849)."""

        self._global_settings.skills = list(paths)
        self._mark_modified("skills")
        self._save()

    def set_project_skill_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setProjectSkillPaths`` (line 851-856)."""

        project_settings = copy.deepcopy(self._project_settings)
        project_settings.skills = list(paths)
        self._mark_project_modified("skills")
        self._save_project_settings(project_settings)

    # --- prompts (Pi `:858-873`) ---
    def get_prompt_template_paths(self) -> list[str]:
        """Pi parity: ``settings-manager.ts::getPromptTemplatePaths`` (line 858-860). Returns defensive copy."""

        return list(self._settings.prompts or [])

    def set_prompt_template_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setPromptTemplatePaths`` (line 862-866)."""

        self._global_settings.prompts = list(paths)
        self._mark_modified("prompts")
        self._save()

    def set_project_prompt_template_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setProjectPromptTemplatePaths`` (line 868-873)."""

        project_settings = copy.deepcopy(self._project_settings)
        project_settings.prompts = list(paths)
        self._mark_project_modified("prompts")
        self._save_project_settings(project_settings)

    # --- themes (Pi `:875-890`) ---
    def get_theme_paths(self) -> list[str]:
        """Pi parity: ``settings-manager.ts::getThemePaths`` (line 875-877). Returns defensive copy."""

        return list(self._settings.themes or [])

    def set_theme_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setThemePaths`` (line 879-883)."""

        self._global_settings.themes = list(paths)
        self._mark_modified("themes")
        self._save()

    def set_project_theme_paths(self, paths: list[str]) -> None:
        """Pi parity: ``settings-manager.ts::setProjectThemePaths`` (line 885-890)."""

        project_settings = copy.deepcopy(self._project_settings)
        project_settings.themes = list(paths)
        self._mark_project_modified("themes")
        self._save_project_settings(project_settings)

    # --- enableSkillCommands (Pi `:892-900`) ---
    def get_enable_skill_commands(self) -> bool:
        """Pi parity: ``settings-manager.ts::getEnableSkillCommands`` (line 892-894). Default: True."""

        v = self._settings.enable_skill_commands
        return True if v is None else v

    def set_enable_skill_commands(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setEnableSkillCommands`` (line 896-900)."""

        self._global_settings.enable_skill_commands = enabled
        self._mark_modified("enable_skill_commands")
        self._save()

    # --- thinkingBudgets (Pi `:902-904`) ---
    def get_thinking_budgets(self) -> ThinkingBudgetsSettings | None:
        """Pi parity: ``settings-manager.ts::getThinkingBudgets`` (line 902-904)."""

        return self._settings.thinking_budgets

    # --- terminal.showImages (Pi `:906-917`) ---
    def get_show_images(self) -> bool:
        """Pi parity: ``settings-manager.ts::getShowImages`` (line 906-908). Default: True."""

        t = self._settings.terminal
        if t is None or t.show_images is None:
            return True
        return t.show_images

    def set_show_images(self, show: bool) -> None:
        """Pi parity: ``settings-manager.ts::setShowImages`` (line 910-917)."""

        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.show_images = show
        self._mark_modified("terminal", "show_images")
        self._save()

    # --- terminal.imageWidthCells (Pi `:919-934`) ---
    def get_image_width_cells(self) -> int:
        """Pi parity: ``settings-manager.ts::getImageWidthCells`` (line 919-925).

        Default: 60. Clamps to ``[1, ∞)`` via ``max(1, floor(width))``.
        Returns 60 if value is non-numeric or non-finite.
        """

        t = self._settings.terminal
        if t is None:
            return 60
        width = t.image_width_cells
        if width is None or not isinstance(width, (int, float)):
            return 60
        if isinstance(width, float) and not math.isfinite(width):
            return 60
        return max(1, math.floor(width))

    def set_image_width_cells(self, width: int) -> None:
        """Pi parity: ``settings-manager.ts::setImageWidthCells`` (line 927-934). Clamps to ``[1, ∞)``."""

        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.image_width_cells = max(
            1, math.floor(width)
        )
        self._mark_modified("terminal", "image_width_cells")
        self._save()

    # --- terminal.clearOnShrink (Pi `:936-951`) ---
    def get_clear_on_shrink(self) -> bool:
        """Pi parity: ``settings-manager.ts::getClearOnShrink`` (line 936-942).

        Settings precedence: explicit value -> ``PI_CLEAR_ON_SHRINK == "1"``
        env var -> ``False``. Env var preserved verbatim from Pi (Aelix-
        retained per ADR-0091 — no ``AELIX_*`` rename in 6h₇b scope).
        """

        t = self._settings.terminal
        if t is not None and t.clear_on_shrink is not None:
            return t.clear_on_shrink
        return os.environ.get("PI_CLEAR_ON_SHRINK") == "1"

    def set_clear_on_shrink(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setClearOnShrink`` (line 944-951)."""

        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.clear_on_shrink = enabled
        self._mark_modified("terminal", "clear_on_shrink")
        self._save()

    # --- terminal.showTerminalProgress (Pi `:953-964`) ---
    def get_show_terminal_progress(self) -> bool:
        """Pi parity: ``settings-manager.ts::getShowTerminalProgress`` (line 953-955). Default: False."""

        t = self._settings.terminal
        if t is None or t.show_terminal_progress is None:
            return False
        return t.show_terminal_progress

    def set_show_terminal_progress(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setShowTerminalProgress`` (line 957-964)."""

        if self._global_settings.terminal is None:
            self._global_settings.terminal = TerminalSettings()
        self._global_settings.terminal.show_terminal_progress = enabled
        self._mark_modified("terminal", "show_terminal_progress")
        self._save()

    # --- images.autoResize (Pi `:966-977`) ---
    def get_image_auto_resize(self) -> bool:
        """Pi parity: ``settings-manager.ts::getImageAutoResize`` (line 966-968). Default: True."""

        i = self._settings.images
        if i is None or i.auto_resize is None:
            return True
        return i.auto_resize

    def set_image_auto_resize(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setImageAutoResize`` (line 970-977)."""

        if self._global_settings.images is None:
            self._global_settings.images = ImageSettings()
        self._global_settings.images.auto_resize = enabled
        self._mark_modified("images", "auto_resize")
        self._save()

    # --- images.blockImages (Pi `:979-990`) ---
    def get_block_images(self) -> bool:
        """Pi parity: ``settings-manager.ts::getBlockImages`` (line 979-981). Default: False."""

        i = self._settings.images
        if i is None or i.block_images is None:
            return False
        return i.block_images

    def set_block_images(self, blocked: bool) -> None:
        """Pi parity: ``settings-manager.ts::setBlockImages`` (line 983-990)."""

        if self._global_settings.images is None:
            self._global_settings.images = ImageSettings()
        self._global_settings.images.block_images = blocked
        self._mark_modified("images", "block_images")
        self._save()

    # --- enabledModels (Pi `:992-1000`) ---
    def get_enabled_models(self) -> list[str] | None:
        """Pi parity: ``settings-manager.ts::getEnabledModels`` (line 992-994)."""

        v = self._settings.enabled_models
        return list(v) if v is not None else None

    def set_enabled_models(self, patterns: list[str] | None) -> None:
        """Pi parity: ``settings-manager.ts::setEnabledModels`` (line 996-1000)."""

        self._global_settings.enabled_models = (
            list(patterns) if patterns is not None else None
        )
        self._mark_modified("enabled_models")
        self._save()

    # --- doubleEscapeAction (Pi `:1002-1010`) ---
    def get_double_escape_action(self) -> DoubleEscapeAction:
        """Pi parity: ``settings-manager.ts::getDoubleEscapeAction`` (line 1002-1004). Default: "tree"."""

        v = self._settings.double_escape_action
        return v if v is not None else "tree"

    def set_double_escape_action(self, action: DoubleEscapeAction) -> None:
        """Pi parity: ``settings-manager.ts::setDoubleEscapeAction`` (line 1006-1010)."""

        self._global_settings.double_escape_action = action
        self._mark_modified("double_escape_action")
        self._save()

    # --- treeFilterMode (Pi `:1012-1022`) ---
    def get_tree_filter_mode(self) -> TreeFilterMode:
        """Pi parity: ``settings-manager.ts::getTreeFilterMode`` (line 1012-1016).

        Default: "default". Invalid values fall back to "default" (Pi
        validates against the allowed set).
        """

        mode = self._settings.tree_filter_mode
        valid = ("default", "no-tools", "user-only", "labeled-only", "all")
        if mode in valid:
            return mode  # type: ignore[return-value]
        return "default"

    def set_tree_filter_mode(self, mode: TreeFilterMode) -> None:
        """Pi parity: ``settings-manager.ts::setTreeFilterMode`` (line 1018-1022)."""

        self._global_settings.tree_filter_mode = mode
        self._mark_modified("tree_filter_mode")
        self._save()

    # --- showHardwareCursor (Pi `:1024-1032`) ---
    def get_show_hardware_cursor(self) -> bool:
        """Pi parity: ``settings-manager.ts::getShowHardwareCursor`` (line 1024-1026).

        Settings precedence: explicit value -> ``PI_HARDWARE_CURSOR == "1"``
        env var -> ``False``. Env var preserved verbatim from Pi (Aelix-
        retained per ADR-0091 — no ``AELIX_*`` rename in 6h₇b scope).
        """

        v = self._settings.show_hardware_cursor
        if v is not None:
            return v
        return os.environ.get("PI_HARDWARE_CURSOR") == "1"

    def set_show_hardware_cursor(self, enabled: bool) -> None:
        """Pi parity: ``settings-manager.ts::setShowHardwareCursor`` (line 1028-1032)."""

        self._global_settings.show_hardware_cursor = enabled
        self._mark_modified("show_hardware_cursor")
        self._save()

    # --- editorPaddingX (Pi `:1034-1042`) ---
    def get_editor_padding_x(self) -> int:
        """Pi parity: ``settings-manager.ts::getEditorPaddingX`` (line 1034-1036). Default: 0."""

        v = self._settings.editor_padding_x
        return 0 if v is None else v

    def set_editor_padding_x(self, padding: int) -> None:
        """Pi parity: ``settings-manager.ts::setEditorPaddingX`` (line 1038-1042). Clamps to ``[0, 3]``."""

        clamped = max(0, min(3, math.floor(padding)))
        self._global_settings.editor_padding_x = clamped
        self._mark_modified("editor_padding_x")
        self._save()

    # --- autocompleteMaxVisible (Pi `:1044-1052`) ---
    def get_autocomplete_max_visible(self) -> int:
        """Pi parity: ``settings-manager.ts::getAutocompleteMaxVisible`` (line 1044-1046). Default: 5."""

        v = self._settings.autocomplete_max_visible
        return 5 if v is None else v

    def set_autocomplete_max_visible(self, max_visible: int) -> None:
        """Pi parity: ``settings-manager.ts::setAutocompleteMaxVisible`` (line 1048-1052). Clamps to ``[3, 20]``."""

        clamped = max(3, min(20, math.floor(max_visible)))
        self._global_settings.autocomplete_max_visible = clamped
        self._mark_modified("autocomplete_max_visible")
        self._save()

    # --- markdown.codeBlockIndent (Pi `:1054-1056`) ---
    def get_code_block_indent(self) -> str:
        """Pi parity: ``settings-manager.ts::getCodeBlockIndent`` (line 1054-1056). Default: "  " (two spaces)."""

        m = self._settings.markdown
        if m is None or m.code_block_indent is None:
            return "  "
        return m.code_block_indent

    # --- warnings (Pi `:1058-1066`) ---
    def get_warnings(self) -> WarningSettings:
        """Pi parity: ``settings-manager.ts::getWarnings`` (line 1058-1060).

        Returns a defensive copy of the in-memory :class:`WarningSettings`
        (or an empty one when unset).
        """

        w = self._settings.warnings
        if w is None:
            return WarningSettings()
        return WarningSettings(
            anthropic_extra_usage=w.anthropic_extra_usage,
        )

    def set_warnings(self, warnings: WarningSettings) -> None:
        """Pi parity: ``settings-manager.ts::setWarnings`` (line 1062-1066)."""

        self._global_settings.warnings = WarningSettings(
            anthropic_extra_usage=warnings.anthropic_extra_usage,
        )
        self._mark_modified("warnings")
        self._save()


__all__ = [
    "SettingsManager",
    "deep_merge_settings",
]
