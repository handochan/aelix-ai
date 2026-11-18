"""The ``mcp`` selection kind: ``mcp.json``'s ``enabled``, served like the settings kinds.

#624, ADR-0305 D11–D16. MCP cannot be a fifth :class:`~.resource_selection.ResourceKind`
under :class:`~.resource_selection.ResourceSelection` (D11): a server's state is a
boolean inside ``mcpServers.<name>`` of another file, the project file REPLACES the
global one instead of layering over it, ``$AELIX_MCP_CONFIG`` is a third source, and a
server connects only at launch. So :class:`McpSelection` has the service's four
methods (``snapshot``, ``needs_new_project_file``, ``save``, ``apply``) and returns
its :class:`~.resource_selection.Snapshot`, :class:`~.resource_selection.SaveResult`
and :class:`~.resource_selection.ApplyResult`; the panel and RPC take it unchanged.

* **The file in force** (D12): the one a launch here would read now, resolved fresh
  at every snapshot and again inside the write's lock (D13). At most one scope is
  writable, that file's; the writer never creates a file.
* **Trust** (D13): a project file is read and written only while the folder is
  trusted at launch AND the settings manager's live flag still says so. An
  untrusted project file is never opened: :func:`~aelix_coding_agent.cli.config.resolve_mcp_config`
  only stats it, and one locked placeholder row stands for it.
* **Rows** (D3, D16): the file's entries; an entry the loader skips is a placeholder
  locked ``unavailable`` with the loader's warning as its problem; a server an
  extension declares is a status row. No row carries an endpoint, command,
  argument, environment, header or url (D14): the name, the transport word, the
  state and the file's label only. Keys cannot collide (:func:`file_key`,
  :func:`extension_key`): a file entry is keyed by its name unless the name starts
  with a reserved prefix, and an extension's server never shares a file entry's key.
* **Off the event loop** (CX-10): :meth:`McpSelection.snapshot_off_loop` and
  :meth:`McpSelection.save` resolve, read and parse ``mcp.json`` in a worker
  thread; the rows, which read what this launch started, are built on the loop.
* **Effect** ``restart`` (D15): ``saved`` is what the file says now, ``applied``
  what this launch started; nothing is applied in place.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .mcp_config_edit import resolves_elsewhere, write_enabled
from .mcp_servers import (
    MCP_PARENT_NAMES_ENV,
    STATE_MEANINGS,
    McpServerStatus,
    config_label,
    runtime_statuses,
)
from .resource_filter import Scope
from .resource_selection import (
    UNTRUSTED_LOCK_REASON,
    UNTRUSTED_SCOPE_REASON,
    ApplyResult,
    Effect,
    ItemState,
    Lock,
    ResourceItem,
    SaveResult,
    ScopeStatus,
    Snapshot,
)

if TYPE_CHECKING:
    from aelix_coding_agent.cli.config import McpConfig

UNTRUSTED_KEY = "untrusted:project"
"""The row that stands for an untrusted project's ``mcp.json`` (D12)."""
EXTENSION_PREFIX = "extension:"
"""A server an extension declares is keyed ``extension:<name>`` (:func:`extension_key`)."""
FILE_PREFIX = "file:"
"""A file entry whose name starts with a reserved prefix is keyed ``file:<name>``."""
RESERVED_PREFIXES = (EXTENSION_PREFIX, "untrusted:", FILE_PREFIX)
"""Key prefixes a bare file entry name may not start with (Codex CX-11)."""
REOPEN_MCP = "reopen /mcp"
"""The TUI's way to take a fresh snapshot; RPC says "list again" instead (ADR-0305 D3)."""
NOT_APPLIED = "MCP servers connect at launch: the next aelix start applies mcp.json"
""":meth:`McpSelection.apply`'s warning: nothing is applied in place (D15)."""

_GROUP_WORD = {"env": "$AELIX_MCP_CONFIG", "project": "PROJECT", "global": "GLOBAL"}
_EXTENSIONS_GROUP = "EXTENSIONS  status only · /extension turns them on and off"


def _config_module() -> Any:
    # Late: ``cli`` imports this package's siblings, and ``aelix status`` must not
    # pull the TUI in through ``cli/__init__``'s REPL at import time.
    from aelix_coding_agent.cli import config  # noqa: PLC0415

    return config


def changed_text(now: str) -> str:
    """D13: the file a launch here reads is not the one this save was for."""

    return f"the file aelix reads here changed (now {now}); nothing written; {REOPEN_MCP}"


def file_key(name: str) -> str:
    """A file entry's row key: its name, or ``file:<name>`` when the name starts with a
    reserved prefix. Injective, and never equal to an :func:`extension_key` or
    :data:`UNTRUSTED_KEY`, whatever the names (Codex CX-11)."""

    return FILE_PREFIX + name if name.startswith(RESERVED_PREFIXES) else name


def extension_key(name: str) -> str:
    """A server an extension declares: ``extension:<name>``."""

    return EXTENSION_PREFIX + name


@dataclass
class McpKind:
    """The ``mcp`` kind: what THIS launch started, for the rows' ``applied``/``live``.

    *manager* is the launch's ``McpClientManager`` (or ``None``), *launch* its
    ``McpLaunch`` (who declared each extension server and which started) and
    *inherited* the ``McpNameRecord`` a delegating aelix passed in. All are read
    duck-typed, so an odd shape lists less instead of raising.
    """

    manager: Any = None
    launch: Any = None
    inherited: Any = None
    kind: str = "mcp"
    settings_key: str = ""
    effect: Effect = "restart"
    files_word: str = "mcp.json"

    def statuses(self) -> dict[str, McpServerStatus]:
        """The started servers' states, by name (``connected``/``failed``/``disconnected``)."""

        try:
            rows = runtime_statuses(self.manager)
        except Exception:  # noqa: BLE001 — a render path never raises
            return {}
        return {s.name: s for s in rows if s.state != "disabled"}

    def _extension_names(self) -> set[str]:
        owners = getattr(self.launch, "owners", None) or {}
        return {str(name) for name, owner in owners.items() if getattr(owner, "started", False)}

    def _key(self, name: str, extension: set[str]) -> str:
        return extension_key(name) if name in extension else file_key(name)

    def applied(self) -> set[str]:
        """Row keys this launch started: an extension's server by :func:`extension_key`,
        every other (a file entry, or a server only the manager knows) by :func:`file_key`."""

        extension = self._extension_names()
        return {self._key(name, extension) for name in self.statuses()}

    def live(self) -> set[str]:
        """Row keys connected now."""

        extension = self._extension_names()
        return {
            self._key(name, extension)
            for name, status in self.statuses().items()
            if status.state == "connected"
        }

    def live_notes(self) -> dict[str, tuple[str, str]]:
        """Row key -> a started server that is not connected: its state word and meaning (D15)."""

        extension = self._extension_names()
        return {
            self._key(name, extension): (status.state, STATE_MEANINGS[status.state])
            for name, status in self.statuses().items()
            if status.state != "connected"
        }

    def inherited_off(self) -> frozenset[str]:
        return frozenset(getattr(self.inherited, "off", None) or ())

    def notices(self) -> list[str]:
        return []

    def busy(self) -> bool:
        return False

    async def wait_until_idle(self) -> None:
        return None

    async def apply(self, enabled: set[str]) -> ApplyResult:
        del enabled
        return ApplyResult(False, warning=NOT_APPLIED)


@dataclass(frozen=True)
class _InForce:
    """The file a launch here reads now, and whether it may be read."""

    source: str
    path: Path
    readable: bool
    label: str
    real: str
    """``os.path.realpath(path)`` when it was resolved: the file a save writes (CX-3)."""


@dataclass(frozen=True)
class _Loaded:
    """What :meth:`McpSelection._load` found on disk: file I/O only, no runtime state."""

    here: _InForce
    config: McpConfig | None
    """The parsed file in force; ``None`` when it may not be read (an untrusted project)."""
    here_exists: bool
    exists: Mapping[Scope, bool]


@dataclass
class McpSelection:
    """Read and save ``enabled`` in the ``mcp.json`` in force (ADR-0305 D11–D13)."""

    cwd: str
    agent_dir: str
    project_trusted: bool
    settings_manager: Any = None
    _scope_paths: dict[Scope, Path] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._scope_paths = {
            "global": Path(self.agent_dir) / "mcp.json",
            "project": Path(self.cwd) / ".aelix" / "mcp.json",
        }

    # --- reading -----------------------------------------------------------

    def _project_readable(self) -> bool:
        """The launch's trust AND the manager's live flag (``ResourceSelection``'s predicate)."""

        manager = self.settings_manager
        return bool(self.project_trusted and manager is not None and manager.is_project_trusted())

    def _in_force(self) -> _InForce:
        source, path = _config_module().resolve_mcp_config(self.cwd)
        readable = source != "project" or self._project_readable()
        label = config_label(path, source, cwd=self.cwd)
        return _InForce(source, path, readable, label, os.path.realpath(path))

    def _load(self) -> _Loaded:
        """Resolve, read and parse the file in force. Blocking: file I/O and nothing else."""

        here = self._in_force()
        config: McpConfig | None = None
        if here.readable:
            config = _config_module()._read_mcp_config(here.path, here.source, self.cwd)  # noqa: SLF001
        exists: dict[Scope, bool] = {
            scope: path.is_file() for scope, path in self._scope_paths.items()
        }
        return _Loaded(here, config, here.path.is_file(), exists)

    async def _load_off_loop(self) -> _Loaded:
        return await asyncio.to_thread(self._load)

    def snapshot(self, kind: McpKind) -> Snapshot:
        """A fresh resolution and read of the file in force (never an untrusted project file)."""

        return self._rows(kind, self._load())

    async def snapshot_off_loop(self, kind: McpKind) -> Snapshot:
        """:meth:`snapshot`, with the file's resolve, read and parse in a worker thread.

        RPC's ``selections/list`` and ``selections/set`` take this one (Codex CX-10):
        a slow filesystem or a large file does not hold up the event loop. The rows,
        which read what this launch started (the MCP manager), are built on the loop.
        """

        return self._rows(kind, await self._load_off_loop())

    def _rows(self, kind: McpKind, loaded: _Loaded) -> Snapshot:
        here, config = loaded.here, loaded.config
        failure = _file_failure(config) if config is not None else None
        states: list[ItemState] = []
        names: set[str] = set()
        if config is not None:
            states.extend(self._file_rows(kind, config, here))
            names = set(config.declared)
        else:
            states.append(_untrusted_row())
        states.extend(_extension_rows(kind, names))
        return Snapshot(
            kind=kind.kind,
            states=tuple(states),
            scopes=self._scopes(loaded, failure),
            problems=tuple(config.warnings) if config is not None else (),
            unreadable=(failure,) if failure is not None else (),
        )

    def _file_rows(self, kind: McpKind, config: McpConfig, here: _InForce) -> list[ItemState]:
        on = {c.name: c for c in config.contribs}
        off = {c.name: c for c in config.disabled}
        applied, live, notes = kind.applied(), kind.live(), kind.live_notes()
        inherited_off = kind.inherited_off()
        decided = here.source if here.source in ("global", "project") else None
        group = f"{_GROUP_WORD[here.source]}  {here.label}"
        rows: list[ItemState] = []
        for name in config.declared:
            contrib = on.get(name) or off.get(name)
            if contrib is None:
                rows.append(_skipped_row(name, config, here, group, decided))
                continue
            key = file_key(name)
            item = ResourceItem(
                kind="mcp",
                key=key,
                label=name,
                identities=(name,),
                description=str(contrib.transport),
                group="file",
                group_label=group,
                scope=decided,
                location=here.label,
            )
            lock = None
            if here.source == "env":
                lock = Lock(
                    key in applied,
                    _env_reason(here),
                    tag="$AELIX_MCP_CONFIG",
                    kind="cli",
                )
            elif name in inherited_off:
                lock = Lock(
                    False,
                    f"${MCP_PARENT_NAMES_ENV} (the names a delegating aelix passes on) "
                    "keeps it off this run",
                    tag="inherited",
                    kind="cli",
                )
            rows.append(
                ItemState(
                    item,
                    name in on,
                    key in applied,
                    decided,  # type: ignore[arg-type]
                    lock,
                    live=key in live,
                    live_note=notes.get(key),
                )
            )
        return rows

    def _scopes(self, loaded: _Loaded, failure: str | None) -> dict[Scope, ScopeStatus]:
        here = loaded.here
        global_label = config_label(self._scope_paths["global"], "global")
        reasons: dict[str, str | None]
        if here.source == "env":
            reasons = {"global": _env_reason(here), "project": _env_reason(here)}
        elif here.source == "project":
            reasons = {
                "global": f"aelix reads .aelix/mcp.json in this folder, not {global_label}",
                "project": None if here.readable else UNTRUSTED_SCOPE_REASON,
            }
        else:
            reasons = {
                "global": None if loaded.here_exists else f"{global_label} does not exist",
                "project": (
                    f"aelix reads one mcp.json: a .aelix/mcp.json here would replace "
                    f"{global_label} in this folder, so its other servers would stop here "
                    "too; turning a global server off in one project waits for #644"
                ),
            }
        if failure is not None and here.source in reasons and reasons[here.source] is None:
            reasons[here.source] = f"{here.label} did not load: {_detail(failure, here.path)}"
        return {
            scope: ScopeStatus(
                scope,
                str(path),
                reasons[scope] is None,
                reasons[scope],
                loaded.exists[scope],
            )
            for scope, path in self._scope_paths.items()
        }

    # --- saving ------------------------------------------------------------

    def needs_new_project_file(self, scope: str, edits: Mapping[str, bool]) -> bool:
        """Never: the writer only edits the file in force and never creates one (D12)."""

        del scope, edits
        return False

    async def save(
        self,
        kind: McpKind,
        scope: str,
        edits: Mapping[str, bool],
        *,
        may_create_project_file: bool = False,
    ) -> SaveResult:
        """Write *edits* (row key -> wanted value) to the file in force, if *scope* is its scope.

        An edit equal to what the file says now is dropped (D4); with none left,
        nothing is written. The write runs in a worker thread under the file's
        lock (``write_enabled``), which calls back here once it holds the lock to
        re-resolve the file in force and re-check trust (D13). A cancel of the
        awaiting task tells the writer to stop: it writes nothing unless it was
        already past its last check.
        """

        del may_create_project_file  # the writer creates nothing (D12)
        loaded = await self._load_off_loop()
        here, before = loaded.here, self._rows(kind, loaded)
        status = before.scopes.get(scope)  # type: ignore[call-overload]
        if status is None or not status.writable:
            if here.source in ("global", "project") and here.source != scope:
                return SaveResult(False, changed_text(here.label))
            if status is None:
                return SaveResult(False, f"Cannot save to {scope}: not available")
            return SaveResult(False, f"Cannot save to {status.path or scope}: {status.reason}")
        rows = {s.item.key: s for s in before.states}
        wanted = {
            key: want
            for key, want in edits.items()
            if key in rows
            and rows[key].item.identities
            and rows[key].item.role == "item"
            and rows[key].saved != want
        }
        if not wanted:
            return SaveResult(True)
        changes = [(rows[key].item.identities[0], want) for key, want in wanted.items()]
        outcome = await self._write(here, changes)
        if outcome.status == "refused":
            return SaveResult(False, outcome.detail)
        if outcome.status == "abandoned":
            return SaveResult(False, "the save was cancelled; nothing written")
        written = (str(here.path),) if outcome.status == "written" else ()
        after = await self.snapshot_off_loop(kind)
        changed = tuple(
            (s.item.label, s.saved)
            for s in after.states
            if s.item.key in rows and rows[s.item.key].saved != s.saved
        )
        mismatches = tuple(
            f"{s.item.label}: saved, but {here.label} says {'on' if s.saved else 'off'} now"
            for s in after.states
            if s.item.key in wanted and s.saved != wanted[s.item.key]
        )
        return SaveResult(True, None, written, changed, mismatches)

    async def _write(self, here: _InForce, changes: list[tuple[str, bool]]) -> Any:
        abandoned = threading.Event()
        try:
            return await asyncio.to_thread(
                write_enabled,
                here.path,
                changes,
                authorize=self._authorizer(here),
                abandoned=abandoned,
                expected_real=here.real,
            )
        except asyncio.CancelledError:
            # The thread cannot be stopped; the writer checks this after its
            # lock and again just before the replace.
            abandoned.set()
            raise

    def _authorizer(self, here: _InForce) -> Callable[[str], str | None]:
        def _authorize(real: str) -> str | None:
            # Inside the lock, before anything is read (D13): still the file a
            # launch here reads, still the real file the save's snapshot read
            # (CX-3), and still a folder whose project file may be written.
            now = self._in_force()
            if now.source != here.source or now.path != here.path:
                return changed_text(now.label)
            if now.real != here.real or real != here.real:
                # The same path, another real file: a link retargeted (N6).
                return resolves_elsewhere(here.path.name or "mcp.json")
            if not now.readable:
                return f"{UNTRUSTED_SCOPE_REASON}; nothing written"
            return None

        return _authorize

    # --- applying ----------------------------------------------------------

    async def apply(
        self,
        kind: McpKind,
        snapshot: Snapshot | None = None,
        *,
        on_deferred: Callable[[ApplyResult], None] | None = None,
    ) -> ApplyResult:
        """Nothing is applied in place: MCP servers connect at launch (D15)."""

        del kind, snapshot, on_deferred
        return ApplyResult(False, warning=NOT_APPLIED)


@dataclass
class McpControl:
    """What ``/mcp`` and ``selections/*`` kind ``mcp`` are given (ADR-0305 D10)."""

    selection: McpSelection
    kind: McpKind


def build_mcp_control(
    *,
    cwd: str,
    agent_dir: str,
    project_trusted: bool,
    settings_manager: Any,
    mcp_manager: Any,
    launch_record: Any,
    inherited: Any,
) -> McpControl:
    """The ``mcp`` control for one launch: its directory, trust and what it started."""

    return McpControl(
        McpSelection(
            cwd=cwd,
            agent_dir=agent_dir,
            project_trusted=project_trusted,
            settings_manager=settings_manager,
        ),
        McpKind(manager=mcp_manager, launch=launch_record, inherited=inherited),
    )


# --- rows ---------------------------------------------------------------------------


def _env_reason(here: _InForce) -> str:
    return f"$AELIX_MCP_CONFIG chooses the file this run; edit {here.label} by hand"


def _file_failure(config: McpConfig) -> str | None:
    """The loader's warning when the file as a whole did not load (no entry was read)."""

    if config.warnings and not config.declared:
        return config.warnings[0]
    return None


def _detail(warning: str, path: Path) -> str:
    """A loader warning without its leading ``<path>: ``."""

    return warning.removeprefix(f"{path}: ")


def _entry_warning(config: McpConfig, name: str) -> str | None:
    """The loader's warning for the entry *name* skipped, without the file's path."""

    # A name's repr ends at its closing quote, so no other entry's warning
    # starts with this lead; a skipped entry has exactly one warning.
    lead = f"{config.path}: server {name!r}"
    for warning in config.warnings:
        if warning.startswith(lead):
            return _detail(warning, config.path) if config.path is not None else warning
    return None


def _skipped_row(
    name: str, config: McpConfig, here: _InForce, group: str, decided: str | None
) -> ItemState:
    item = ResourceItem(
        kind="mcp",
        key=file_key(name),
        label=name,
        description="not started: its entry is invalid",
        group="file",
        group_label=group,
        scope=decided,
        location=here.label,
        role="placeholder",
    )
    lock = Lock(
        False, f"{here.label} skips this entry; fix it by hand", tag="skipped", kind="unavailable"
    )
    return ItemState(
        item,
        False,
        False,
        decided,  # type: ignore[arg-type]
        lock,
        problem=_entry_warning(config, name) or "the loader skips it",
    )


def _untrusted_row() -> ItemState:
    item = ResourceItem(
        kind="mcp",
        key=UNTRUSTED_KEY,
        label=".aelix/mcp.json not read",
        description="This folder is not trusted, so its mcp.json is not read.",
        group="project",
        group_label="PROJECT  .aelix/mcp.json",
        scope="project",
        role="placeholder",
    )
    lock = Lock(False, UNTRUSTED_LOCK_REASON, tag="untrusted", kind="untrusted")
    return ItemState(item, False, False, None, lock)


def _extension_rows(kind: McpKind, file_names: set[str]) -> list[ItemState]:
    """A status row per server an extension declared (D16).

    A name the file in force declares is the file's, as at launch (``mcp_launch``,
    #512: an extension's server of that name is not started), so it has no status
    row here. Keys never decide it: :func:`extension_key` cannot equal a file
    entry's :func:`file_key` (Codex CX-11).
    """

    owners = getattr(kind.launch, "owners", None) or {}
    statuses = kind.statuses()
    rows: list[ItemState] = []
    for raw_name, owner in owners.items():
        name = str(raw_name)
        if name in file_names:
            continue
        key = extension_key(name)
        status = statuses.get(name) if getattr(owner, "started", False) else None
        state = status.state if status is not None else "not started"
        plugin = str(getattr(owner, "plugin", "?"))
        why = f"declared by {plugin} · /extension turns the plugin on and off"
        transport = f"{status.transport} · " if status is not None else ""
        item = ResourceItem(
            kind="mcp",
            key=key,
            label=name,
            description=f"{transport}declared by {plugin}",
            group="extensions",
            group_label=_EXTENSIONS_GROUP,
            location=f"declared by {plugin}",
            role="info",
        )
        lock = Lock(
            state == "connected",
            f"{state.capitalize()} · {why}",
            tag=state,
            kind="status",
            refusal=f"{name} is {why}",
        )
        # Saved and applied alike say whether this launch started it: the row is
        # status only, and a client reading ``saved`` must not take a started
        # server for one that is off (live check F5).
        started = status is not None
        rows.append(ItemState(item, started, started, None, lock))
    return rows


__all__ = [
    "EXTENSION_PREFIX",
    "FILE_PREFIX",
    "NOT_APPLIED",
    "REOPEN_MCP",
    "RESERVED_PREFIXES",
    "UNTRUSTED_KEY",
    "McpControl",
    "McpKind",
    "McpSelection",
    "build_mcp_control",
    "changed_text",
    "extension_key",
    "file_key",
]
