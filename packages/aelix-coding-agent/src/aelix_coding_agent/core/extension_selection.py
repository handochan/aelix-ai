"""Extensions under the resource selection contract (#25, ADR-0284).

What lives here:

* :func:`extension_filter` — the ``extensions`` settings filter between
  discovery and the load (ADR-0279 D16, C5). Phase ``G`` (before project trust:
  the vote load) reads the global file only; phase ``P`` (after it: every
  harness build, ``/reload`` included, the manifest scan and ``aelix status``)
  reads both, by ADR-0279 D2. ``-e`` and the prepended built-ins are never
  filtered. An entry the filter turns off is never imported.
* :func:`build_inventory` — every carrier a build found, on or off, with what
  became of it, read from the discovery carriers and manifest TOML only
  (ADR-0284 §3; nothing is imported to list it).
* :class:`ExtensionKind` — the ``/extension`` Installed tab's
  :class:`~.resource_selection.ResourceKind`: an extension's effect is
  ``reload``, so Enter saves and ``/reload`` (or a restart) applies.
* :class:`McpLaunch` — which plugin declared each MCP server, fixed at launch
  (Codex F11): a server whose plugin is off at ``/reload`` keeps its
  connection and loses its tools; a plugin turned on connects its servers at
  the next launch.
* :func:`reload_lines` — the one transcript entry ``/reload`` leaves: what
  changed, what failed, and what waits for a restart (MCP servers, providers,
  new shortcut keys — Codex F10).

Identities (ADR-0284 D1): a manifest pack is named by its ``plugin.id``, then
its entry-point name and PEP 503 distribution name; a manifest-less entry
point by its name and distribution; a legacy file by its stem, a package
directory by its name; a built-in by ``builtin:<name>``. The first is what the
panel writes.
"""

from __future__ import annotations

import importlib.metadata
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .resource_filter import (
    Target,
    effective,
    matches_any,
    source_entries,
    split_entry,
)
from .resource_selection import (
    ApplyGate,
    ApplyResult,
    Effect,
    Lock,
    ResourceItem,
    ResourceSelection,
    SettingsLists,
    harness_busy,
    wait_harness_idle,
)

if TYPE_CHECKING:
    from aelix_coding_agent.extensions.loader import DiscoveredEntry, LoadExtensionsResult

Phase = Literal["G", "P"]

SETTINGS_KEY = "extensions"

#: The prepended built-ins, by class name: their identity and what the row says.
BUILTINS: Mapping[str, tuple[str, str, str]] = {
    "GuardrailExtension": ("guardrail", "Blocks dangerous commands and paths", "Guardrail gate"),
    "PermissionExtension": ("permission", "Asks before a tool runs (/permissions)", "/permissions"),
    "StatusExtension": ("status", "The aelix_status tool", "aelix_status"),
    "AgentsExtension": ("agents", "Delegation to other agents (/agents)", "[features] agents"),
}
_ALWAYS_ON = ("GuardrailExtension", "PermissionExtension", "StatusExtension")

_TIER_ORDER = {"builtin": 0, "project": 1, "global": 2, "installed": 3, "cli": 4}
_SCOPE_OF_TIER = {"project": "project", "global": "global", "installed": "global", "cli": "cli"}


def _canonical_dist(name: str) -> str:
    from aelix_coding_agent.extensions.ep_manifest import _canonical_dist as canon  # noqa: PLC0415

    return canon(name)


def _unique(values: Sequence[str | None]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return tuple(out)


@dataclass(frozen=True)
class EntryInfo:
    """What a carrier says about itself without being imported (ADR-0284 §3)."""

    label: str
    identities: tuple[str, ...]
    path: str | None
    scope: str | None
    description: str = ""
    version: str | None = None
    manifest: bool = False
    builtin: str | None = None
    """``guardrail`` / ``permission`` / ``status`` / ``agents`` for a prepended built-in."""
    dist: str | None = None
    declared_tools: int | None = None
    declared_commands: int | None = None
    declared_hooks: int | None = None
    mcp_servers: tuple[str, ...] = ()


def describe(entry: Any, tier: str) -> EntryInfo:
    """*entry* (a discovery carrier) as a row's facts, from metadata alone."""

    from aelix_coding_agent.extensions.loader import (  # noqa: PLC0415
        _EntryPointEntry,
        _ManifestEntry,
    )

    scope = _SCOPE_OF_TIER.get(tier)
    if isinstance(entry, _ManifestEntry):
        manifest = entry.manifest
        ep = entry.ep_ref
        dist = ep.dist if ep is not None else None
        contributes = manifest.contributes
        return EntryInfo(
            label=manifest.plugin.id,
            identities=_unique(
                [
                    manifest.plugin.id,
                    ep.name if ep is not None else None,
                    _canonical_dist(dist) if dist else None,
                ]
            ),
            path=str(entry.pkg_dir),
            scope=scope,
            description=manifest.plugin.description,
            version=manifest.plugin.version,
            manifest=True,
            dist=dist,
            declared_tools=len(contributes.tools),
            declared_commands=len(contributes.commands),
            declared_hooks=len(contributes.hooks),
            mcp_servers=tuple(c.name for c in contributes.mcp_servers),
        )
    if isinstance(entry, _EntryPointEntry):
        return EntryInfo(
            label=entry.name,
            identities=_unique([entry.name, _canonical_dist(entry.dist) if entry.dist else None]),
            path=None,
            scope=scope,
            description=_dist_summary(entry.dist),
            dist=entry.dist,
        )
    if isinstance(entry, str):
        # A module reference ``-e`` named (``pkg.mod`` / ``pkg.mod:factory``);
        # discovery turns every path-shaped entry into a ``Path``.
        return EntryInfo(label=entry, identities=(entry,), path=None, scope=scope)
    if isinstance(entry, Path):
        path = entry
        if path.name == "__init__.py":
            directory = path.parent
            return EntryInfo(
                label=directory.name,
                identities=(directory.name,),
                path=str(directory),
                scope=scope,
                description="(no manifest)",
            )
        return EntryInfo(
            label=path.name,
            identities=_unique([path.stem]),
            path=str(path),
            scope=scope,
            description="(no manifest)",
        )
    name = type(entry).__name__
    if tier == "builtin" and name in BUILTINS:
        short, description, _why = BUILTINS[name]
        return EntryInfo(
            label=short,
            identities=(f"builtin:{short}",),
            path=None,
            scope=None,
            description=description,
            builtin=short,
        )
    label = getattr(entry, "__qualname__", None) or name
    return EntryInfo(label=str(label), identities=(), path=None, scope=scope)


def _dist_summary(dist: str | None) -> str:
    """The installed distribution's ``Summary`` — metadata, no import (ADR-0284 §3)."""

    if not dist:
        return ""
    try:
        summary = importlib.metadata.metadata(dist)["Summary"] or ""
    except Exception:  # noqa: BLE001 — a listing never raises
        return ""
    return str(summary).strip()


def target_of(info: EntryInfo) -> Target:
    return Target(identities=info.identities, path=info.path, scope=info.scope)


def is_filtered_tier(record: DiscoveredEntry) -> bool:
    """Whether the settings may turn *record* off: never ``-e``, never a built-in."""

    return record.tier not in ("builtin", "cli") and not record.explicit


def extension_filter(
    lists: SettingsLists, *, phase: Phase
) -> Callable[[Sequence[Any]], list[bool]]:
    """The loader's ``entry_filter`` for *phase* (ADR-0284 D2; ADR-0279 D16, C5).

    ``G``: the global file only — the project's is not read before trust, so it
    cannot keep an extension from voting. ``P``: both, by ADR-0279 D2. Never
    raises: an entry whose evaluation fails loads, as it did before the filter.
    """

    global_list = lists.global_list
    project_list = lists.project_list if phase == "P" else None

    def _filter(records: Sequence[Any]) -> list[bool]:
        out: list[bool] = []
        for record in records:
            if not is_filtered_tier(record):
                out.append(True)
                continue
            try:
                value, _scope = effective(
                    target_of(describe(record.entry, record.tier)), global_list, project_list
                )
            except Exception:  # noqa: BLE001 — the filter never takes a build down
                value = True
            out.append(value)
        return out

    return _filter


def list_warnings(lists: SettingsLists, records: Sequence[Any], *, check_names: bool) -> list[str]:
    """ADR-0279 D8 for ``extensions``, said once per launch.

    A plain entry — a path that would ADD an extension source, pi's grammar — is
    not supported yet (owner decision 8, ADR-0284 D9) and is ignored. With
    *check_names* (discovery ran: not ``--no-extensions``), an ``!`` / ``+`` /
    ``-`` entry that names no extension found at launch.
    """

    out = list(lists.problems)
    targets = [target_of(describe(r.entry, r.tier)) for r in records]
    for scope_list in (lists.global_list, lists.project_list):
        if scope_list is None:
            continue
        label = lists.label(scope_list.scope)
        for entry in source_entries(scope_list.entries):
            out.append(
                f'{label}: "extensions" entry {entry!r} would add an extension source by '
                "path, which is not supported yet; it is ignored"
            )
        if not check_names:
            continue
        for entry in scope_list.entries:
            if not split_entry(entry)[0]:
                continue
            if not matches_any(entry, targets, scope_list.base_dir, list_scope=scope_list.scope):
                out.append(
                    f'{label}: "extensions" entry {entry!r} names no extension found at launch'
                )
    return out


# --- the inventory ------------------------------------------------------------


@dataclass(frozen=True)
class ExtensionRow:
    """One discovered extension as a build left it (ADR-0284 §3)."""

    key: str
    info: EntryInfo
    tier: str
    explicit: bool
    selected: bool
    loaded: bool
    deferred: bool = False
    error: str | None = None
    note: str | None = None
    """A discovery error about a carrier that still loaded (an entry point without its manifest)."""
    extension_name: str | None = None
    tools: int | None = None
    commands: int | None = None
    hooks: int | None = None
    providers: tuple[str, ...] = ()
    shortcuts: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefusedRow:
    """A carrier discovery could not produce (a malformed manifest, a refused ``-e``)."""

    key: str
    label: str
    reason: str


@dataclass(frozen=True)
class ExtensionInventory:
    """What one harness build discovered and loaded — published when it is committed (F08)."""

    rows: tuple[ExtensionRow, ...] = ()
    refused: tuple[RefusedRow, ...] = ()
    runtime: Any = None
    no_extensions: bool = False

    def row(self, key: str) -> ExtensionRow | None:
        return next((r for r in self.rows if r.key == key), None)

    def deferred_state(self, row: ExtensionRow) -> str | None:
        """``deferred`` / ``failed: …`` / ``None`` (active) for a lazily activated pack (F09)."""

        if not row.deferred or row.extension_name is None:
            return None
        runtime = self.runtime
        failures = getattr(runtime, "activation_failures", None) or {}
        if row.extension_name in failures:
            return f"failed: {failures[row.extension_name]}"
        pending = getattr(runtime, "pending_activations", None) or {}
        return "deferred" if row.extension_name in pending else None


def _row_key(tier: str, info: EntryInfo) -> str:
    return f"{tier}:{info.path or (info.identities[0] if info.identities else info.label)}"


def build_inventory(
    loaded: LoadExtensionsResult, *, no_extensions: bool = False
) -> ExtensionInventory:
    """Every carrier *loaded* found, on or off, with its outcome (no import)."""

    owners = getattr(loaded.runtime, "provider_owners", None) or {}
    tied: set[int] = set()
    rows: list[ExtensionRow] = []
    seen: set[str] = set()
    for record in loaded.discovered:
        info = describe(record.entry, record.tier)
        key = _row_key(record.tier, info)
        while key in seen:
            key += "'"
        seen.add(key)
        outcome = record.outcome
        ext = outcome.extension if outcome is not None else None
        error = outcome.error if outcome is not None else None
        if error is not None:
            tied.add(id(error))
        note = None
        if record.discovery_error is not None:
            tied.add(id(record.discovery_error))
            if record.selected:
                note = record.discovery_error.error
        live = ext is not None and not (outcome is not None and outcome.deferred)
        rows.append(
            ExtensionRow(
                key=key,
                info=info,
                tier=record.tier,
                explicit=record.explicit,
                selected=record.selected,
                loaded=ext is not None,
                deferred=bool(outcome is not None and outcome.deferred),
                error=f"{error.error}" if error is not None else None,
                note=note,
                extension_name=getattr(ext, "name", None),
                tools=len(ext.tools) if live and ext is not None else None,
                commands=len(ext.commands) if live and ext is not None else None,
                hooks=(
                    sum(len(h) for h in ext.handlers.values()) if live and ext is not None else None
                ),
                providers=tuple(
                    sorted(
                        n for n, owner in owners.items() if ext is not None and owner == ext.name
                    )
                ),
                shortcuts=tuple(sorted(ext.shortcuts)) if ext is not None else (),
            )
        )
    refused: list[RefusedRow] = []
    for index, err in enumerate(loaded.errors):
        if id(err) in tied:
            continue
        path = str(getattr(err, "path", "") or "?")
        label = os.path.basename(path.rstrip("/" + os.sep)) or path
        refused.append(RefusedRow(f"refused:{index}:{path}", label, str(getattr(err, "error", ""))))
    return ExtensionInventory(tuple(rows), tuple(refused), loaded.runtime, no_extensions)


def listing_inventory(records: Sequence[Any]) -> ExtensionInventory:
    """Rows for carriers no build loaded (``--no-extensions``): discovery only, nothing imported."""

    rows: list[ExtensionRow] = []
    seen: set[str] = set()
    for record in records:
        if record.tier in ("builtin", "cli"):
            continue
        info = describe(record.entry, record.tier)
        key = _row_key(record.tier, info)
        if key in seen:
            continue
        seen.add(key)
        rows.append(ExtensionRow(key, info, record.tier, record.explicit, False, False))
    return ExtensionInventory(tuple(rows), (), None, True)


def count_untrusted_extensions(directory: str) -> int:
    """How many extensions an untrusted ``.aelix/extensions`` holds, from a listing alone.

    No file is opened: a ``.py`` file counts, and so does a subdirectory holding
    an ``aelix-plugin.toml``, a ``pyproject.toml`` or an ``__init__.py`` — the
    shapes the directory tier would read were the folder trusted, as far as a
    listing shows them (hidden names skipped). A ``pyproject.toml`` that
    declares no extension is counted too: telling would mean reading it.
    """

    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    count = 0
    for name in names:
        if name.startswith(".") or name == "__pycache__":
            continue
        path = os.path.join(directory, name)
        is_file = name.endswith(".py") and os.path.isfile(path)
        is_pack = os.path.isdir(path) and any(
            os.path.isfile(os.path.join(path, marker))
            for marker in ("aelix-plugin.toml", "pyproject.toml", "__init__.py")
        )
        count += is_file or is_pack
    return count


# --- MCP servers a plugin declared (Codex F11) -------------------------------


@dataclass(frozen=True)
class McpOwner:
    """The plugin that declared a server, and whether the launch started it."""

    plugin: str
    target: Target
    filtered: bool
    """Whether the settings may turn the plugin off (not ``-e``)."""
    started: bool


@dataclass(frozen=True)
class McpLaunch:
    """Fixed at launch: who declared each server and which were started (F11).

    The connections and their tool wrappers are made once; ``/reload`` only
    hides the tools of a server whose plugin is off now (owner decision 10).
    A server ``mcp.json`` names is the file's (#512), whatever a plugin says.
    """

    owners: Mapping[str, McpOwner] = field(default_factory=dict)

    def hidden(self, lists: SettingsLists) -> frozenset[str]:
        """Started servers whose plugin the settings turn off now."""

        out: set[str] = set()
        for server, owner in self.owners.items():
            if not owner.started or not owner.filtered:
                continue
            try:
                on, _scope = effective(owner.target, lists.global_list, lists.project_list)
            except Exception:  # noqa: BLE001 — never hides by accident
                on = True
            if not on:
                out.add(server)
        return frozenset(out)


def mcp_launch(records: Sequence[Any], started: set[str], mcp_json: set[str]) -> McpLaunch:
    """Who declared each manifest server, from the launch scan's records."""

    from aelix_coding_agent.extensions.loader import _ManifestEntry  # noqa: PLC0415

    owners: dict[str, McpOwner] = {}
    # Reversed: the scan's project tier wins a manifest-vs-manifest name clash.
    for record in reversed(list(records)):
        if not isinstance(record.entry, _ManifestEntry):
            continue
        info = describe(record.entry, record.tier)
        for server in info.mcp_servers:
            if server in mcp_json or server in owners:
                continue
            owners[server] = McpOwner(
                plugin=info.label,
                target=target_of(info),
                filtered=is_filtered_tier(record),
                started=server in started and record.selected,
            )
    return McpLaunch(owners)


def without_hidden(tools: Sequence[Any], hidden: frozenset[str]) -> list[Any]:
    """*tools* minus the wrappers of *hidden* servers (``<server>__<tool>``)."""

    if not hidden:
        return list(tools)
    return [
        t
        for t in tools
        if not any(str(getattr(t, "name", "")).startswith(f"{s}__") for s in hidden)
    ]


# --- the panel -----------------------------------------------------------------


def _counts(row: ExtensionRow) -> str:
    info = row.info
    live = row.tools is not None
    tools = row.tools if live else info.declared_tools
    commands = row.commands if live else info.declared_commands
    hooks = row.hooks if live else info.declared_hooks
    parts: list[str] = []
    for count, word in ((tools, "tool"), (commands, "command"), (hooks, "hook")):
        if count:
            parts.append(f"{count} {word}{'s' if count != 1 else ''}")
    servers = len(info.mcp_servers)
    if servers:
        parts.append(f"{servers} MCP server{'s' if servers != 1 else ''}")
    return " · ".join(parts)


def _short(path: str, home: str, cwd: str) -> str:
    for base in (cwd, os.path.realpath(cwd)):
        if path.startswith(base + os.sep):
            return os.path.relpath(path, base)
    if home and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


@dataclass
class ExtensionKind:
    """The ``/extension`` Installed tab's view of the live inventory (``ResourceKind``).

    Every row comes from :class:`ExtensionInventory` — what the LAST build
    found (published when it was committed, Codex F08) — so listing an off
    extension imports nothing. ``effect`` is ``reload``: Enter saves only.
    """

    inventory: Callable[[], ExtensionInventory | None]
    harness: Callable[[], Any] = lambda: None
    gate: ApplyGate | None = None
    mcp_statuses: Callable[[], Sequence[Any]] = lambda: ()
    mcp_launch: Callable[[], McpLaunch | None] = lambda: None
    cwd: str = ""
    project_trusted: bool = True
    agent_dir: str = ""
    listing: Callable[[], ExtensionInventory | None] = lambda: None
    """``--no-extensions``: the carriers discovery would find (nothing imported)."""
    last_reload_failure: Callable[[], str | None] = lambda: None
    kind: str = "extension"
    settings_key: str = SETTINGS_KEY
    effect: Effect = "reload"
    _home: str = field(default_factory=lambda: os.path.expanduser("~"))

    # --- reading -----------------------------------------------------------

    def _inventory(self) -> ExtensionInventory:
        try:
            inventory = self.inventory()
        except Exception:  # noqa: BLE001 — a render path never raises
            inventory = None
        inventory = inventory or ExtensionInventory()
        if inventory.no_extensions:
            try:
                listed = self.listing()
            except Exception:  # noqa: BLE001
                listed = None
            if listed is not None:
                inventory = replace(inventory, rows=inventory.rows + listed.rows)
        return inventory

    def _group(self, row: ExtensionRow) -> tuple[str, str]:
        tier = row.tier
        if tier == "builtin":
            return "builtin", "BUILT-IN"
        if tier == "project":
            return "project", "PROJECT  .aelix/extensions"
        if tier == "global":
            where = _short(os.path.join(self.agent_dir, "extensions"), self._home, self.cwd)
            return "global", f"GLOBAL  {where}" if self.agent_dir else "GLOBAL"
        if tier == "installed":
            return "installed", "INSTALLED  entry points"
        return "cli", "THIS RUN  -e"

    def _location(self, row: ExtensionRow) -> str:
        info = row.info
        if row.tier == "installed":
            return f"pip {info.dist}" if info.dist else "entry point"
        if info.path:
            return _short(info.path, self._home, self.cwd)
        return ""

    def items(self) -> list[ResourceItem]:
        inventory = self._inventory()
        rows: list[tuple[tuple[int, int], ResourceItem]] = []
        has_agents = False
        for index, row in enumerate(inventory.rows):
            info = row.info
            has_agents = has_agents or info.builtin == "agents"
            group, label = self._group(row)
            description = info.description
            counts = _counts(row)
            if counts:
                description = f"{description} · {counts}" if description else counts
            if row.tier == "cli":
                description = description or "loaded by -e for this run"
            if inventory.deferred_state(row) == "deferred":
                # Codex F09: a pure on_command pack has run nothing yet.
                description = f"{description} · loads at its first command"
            if row.note:
                # An entry point loaded without its manifest (#91): said, not hidden.
                description = (
                    f"{description} · loaded without its manifest (aelix extension verify)"
                )
            rows.append(
                (
                    (_TIER_ORDER.get(row.tier, 5), index),
                    ResourceItem(
                        kind="extension",
                        key=row.key,
                        label=info.label,
                        identities=info.identities,
                        description=description,
                        group=group,
                        group_label=label,
                        path=info.path,
                        scope=info.scope,
                        location=self._location(row),
                    ),
                )
            )
        if not has_agents:
            _short_name, description, _why = BUILTINS["AgentsExtension"]
            rows.append(
                (
                    (0, len(BUILTINS)),
                    ResourceItem(
                        kind="extension",
                        key="builtin:agents",
                        label="agents",
                        identities=("builtin:agents",),
                        description=description,
                        group="builtin",
                        group_label="BUILT-IN",
                    ),
                )
            )
        untrusted = self._untrusted_count()
        if untrusted:
            rows.append(
                (
                    (1, -1),
                    ResourceItem(
                        kind="extension",
                        key="project-untrusted",
                        label=f"{untrusted} extension{'s' if untrusted != 1 else ''} not loaded",
                        description="This folder is not trusted, so its extensions are not read.",
                        group="project",
                        group_label="PROJECT  .aelix/extensions",
                        scope="project",
                    ),
                )
            )
        for index, refused in enumerate(inventory.refused):
            rows.append(
                (
                    (6, index),
                    ResourceItem(
                        kind="extension",
                        key=refused.key,
                        label=refused.label,
                        description=f"refused: {refused.reason}",
                        group="refused",
                        group_label="REFUSED  found, not loaded",
                        role="placeholder",
                    ),
                )
            )
        for index, (item, _lock) in enumerate(self._mcp_rows(inventory)):
            rows.append(((7, index), item))
        rows.sort(key=lambda r: r[0])
        return [item for _k, item in rows]

    def _untrusted_count(self) -> int:
        if self.project_trusted or not self.cwd:
            return 0
        return count_untrusted_extensions(os.path.join(self.cwd, ".aelix", "extensions"))

    def _mcp_rows(self, inventory: ExtensionInventory) -> list[tuple[ResourceItem, Lock]]:
        """Read-only rows (ADR-0284 D7): #512's state word and the declaring plugin."""

        try:
            statuses = list(self.mcp_statuses() or ())
        except Exception:  # noqa: BLE001
            statuses = []
        try:
            launch = self.mcp_launch() or McpLaunch()
        except Exception:  # noqa: BLE001
            launch = McpLaunch()
        loaded = {r.info.label for r in inventory.rows if r.loaded}
        refusal = "MCP servers are turned on and off in mcp.json · /mcp"
        out: list[tuple[ResourceItem, Lock]] = []
        listed: set[str] = set()
        for status in statuses:
            name = str(getattr(status, "name", "?"))
            listed.add(name)
            state = str(getattr(status, "state", "") or "?")
            transport = str(getattr(status, "transport", "") or "?")
            owner = launch.owners.get(name)
            source = f"declared by {owner.plugin}" if owner is not None else "mcp.json"
            line = state.capitalize()
            tag = state
            if owner is not None and owner.started and owner.plugin not in loaded:
                line = (
                    f"{line} · {owner.plugin} is off: its tools are hidden; disconnects at restart"
                )
                tag = f"{state} · hidden" if state == "connected" else state
            elif state == "disabled":
                config = getattr(status, "config", None)
                # The pointer before the path (#510 verify V5): a narrow line
                # cuts the file name, not where to look.
                line = f'Disabled · see /mcp · "enabled": false in {config or "mcp.json"}'
            item = ResourceItem(
                kind="extension",
                key=f"mcp:{name}",
                label=name,
                description=f"{transport} · {source}",
                group="mcp",
                group_label="MCP SERVERS  status only · /mcp manages them",
                location=source,
                role="info",
            )
            out.append(
                (item, Lock(state == "connected", line, tag=tag, kind="status", refusal=refusal))
            )
        # Declared by a plugin that was off at launch: nothing was started for it.
        for row in inventory.rows:
            for name in row.info.mcp_servers:
                if name in listed:
                    continue
                owner = launch.owners.get(name)
                if owner is not None and owner.started:
                    continue
                listed.add(name)
                if inventory.no_extensions:
                    line = "Not started · --no-extensions"
                elif row.selected:
                    line = f"Not started · {row.info.label} was off at launch: connects at the next launch"
                else:
                    line = f"Not started · {row.info.label} is off"
                item = ResourceItem(
                    kind="extension",
                    key=f"mcp:{name}",
                    label=name,
                    description=f"declared by {row.info.label}",
                    group="mcp",
                    group_label="MCP SERVERS  status only · /mcp manages them",
                    location=f"declared by {row.info.label}",
                    role="info",
                )
                out.append(
                    (item, Lock(False, line, tag="not started", kind="status", refusal=refusal))
                )
        return out

    def locks(self) -> dict[str, Lock]:
        inventory = self._inventory()
        locks: dict[str, Lock] = {}
        for row in inventory.rows:
            info = row.info
            if info.builtin is not None:
                if info.builtin == "agents":
                    locks[row.key] = Lock(
                        row.loaded,
                        "follows [features] agents (/settings)",
                        tag="/settings",
                        kind="always_on",
                    )
                else:
                    _n, _d, why = BUILTINS[f"{info.builtin.capitalize()}Extension"]
                    locks[row.key] = Lock(
                        True, f"always on ({why})", tag="always on", kind="always_on"
                    )
            elif row.tier == "cli" or row.explicit:
                locks[row.key] = Lock(True, "loaded by -e for this run", tag="-e", kind="cli")
            elif inventory.no_extensions:
                locks[row.key] = Lock(
                    False,
                    "--no-extensions: none is loaded this run",
                    tag="--no-extensions",
                    kind="cli",
                )
        if not any(r.info.builtin == "agents" for r in inventory.rows):
            locks["builtin:agents"] = Lock(
                False, "follows [features] agents (/settings)", tag="/settings", kind="always_on"
            )
        locks["project-untrusted"] = Lock(
            False, "this folder is not trusted; /trust to decide", tag="untrusted", kind="untrusted"
        )
        for refused in inventory.refused:
            locks[refused.key] = Lock(
                False, f"refused: {refused.reason}", tag="refused", kind="unavailable"
            )
        for item, lock in self._mcp_rows(inventory):
            locks[item.key] = lock
        return locks

    def applied(self) -> set[str]:
        return {r.key for r in self._inventory().rows if r.loaded}

    def item_problems(self) -> dict[str, str]:
        """Saved on, not loaded: the load error, or a deferred activation that failed (F09)."""

        inventory = self._inventory()
        out: dict[str, str] = {}
        for row in inventory.rows:
            if row.selected and row.error is not None:
                out[row.key] = row.error
                continue
            state = inventory.deferred_state(row)
            if state is not None and state.startswith("failed: "):
                out[row.key] = (
                    f"its first command failed to activate it: {state[len('failed: ') :]}"
                )
        return out

    def notices(self) -> list[str]:
        notes: list[str] = []
        failure = None
        try:
            failure = self.last_reload_failure()
        except Exception:  # noqa: BLE001
            failure = None
        if failure:
            notes.append(failure)
        if self._inventory().no_extensions:
            notes.append("--no-extensions: only -e extensions load this run; choices still save.")
        return notes

    def detail(self, key: str) -> str | None:
        """The restart-bound part of a row, for the reload note and the panel (F10)."""

        row = self._inventory().row(key)
        if row is None:
            return None
        return restart_note(row)

    # --- applying ----------------------------------------------------------

    def busy(self) -> bool:
        return harness_busy(self.harness, self.gate)

    async def wait_until_idle(self) -> None:
        await wait_harness_idle(self.harness, self.gate)

    async def apply(self, enabled: set[str]) -> ApplyResult:
        """Nothing is applied in place: ``/reload`` (or a restart) loads the saved set."""

        return ApplyResult(False, warning="/reload applies extension changes")


def restart_note(row: ExtensionRow) -> str | None:
    """What of *row* waits for a restart: its MCP servers and providers (Codex F10)."""

    parts: list[str] = []
    if row.info.mcp_servers:
        parts.append("its MCP servers connect or disconnect only at the next launch")
    if row.providers:
        parts.append(
            f"its provider{'s' if len(row.providers) != 1 else ''} "
            f"({', '.join(row.providers)}) stay in /model until restart"
        )
    return "; ".join(parts) or None


# --- /reload --------------------------------------------------------------------


def reload_lines(
    before: ExtensionInventory | None,
    after: ExtensionInventory | None,
    *,
    launch: McpLaunch | None = None,
    bound_shortcuts: frozenset[str] | None = None,
) -> list[tuple[str, str]]:
    """``(style, text)`` lines for the one transcript entry ``/reload`` leaves (ADR-0284 D6).

    Empty when nothing changed and nothing failed. The first line names what
    changed; then one line per failure, per MCP server whose tools are hidden
    or wait for the next launch, per provider that stays until restart and per
    new shortcut key that binds at the next launch.
    """

    if after is None:
        return []
    old = {r.key: r for r in (before.rows if before is not None else ())}
    changes: list[str] = []
    turned_off: list[ExtensionRow] = []
    turned_on: list[ExtensionRow] = []
    for row in after.rows:
        if row.tier == "builtin":
            continue
        prior = old.get(row.key)
        was = prior.loaded if prior is not None else False
        if row.loaded and not was:
            changes.append(f"{row.info.label} on" + ("" if prior is not None else " (new)"))
            turned_on.append(row)
        elif was and not row.loaded and prior is not None:
            changes.append(f"{row.info.label} off")
            turned_off.append(prior)
    for key, prior in old.items():
        if prior.tier != "builtin" and prior.loaded and after.row(key) is None:
            changes.append(f"{prior.info.label} removed")
            turned_off.append(prior)
    failures = [r for r in after.rows if r.selected and r.error is not None]
    lines: list[tuple[str, str]] = []
    if changes:
        lines.append(("", f"Extensions after /reload: {' · '.join(changes)}"))
    elif failures:
        lines.append(("", "Extensions after /reload: no change"))
    for row in failures:
        lines.append(("red", f"✖ {row.info.label} failed to load: {row.error} (see /extension)"))
    owners = launch.owners if launch is not None else {}
    for row in turned_off:
        for server in row.info.mcp_servers:
            owner = owners.get(server)
            if owner is not None and owner.started:
                lines.append(
                    (
                        "yellow",
                        f"{server} (MCP) stays connected until restart; its tools are hidden",
                    )
                )
        for provider in row.providers:
            lines.append(("yellow", f"{provider} (provider) stays in /model until restart"))
    for row in turned_on:
        for server in row.info.mcp_servers:
            owner = owners.get(server)
            if owner is None or not owner.started:
                lines.append(("yellow", f"{server} (MCP) connects at the next launch"))
        if bound_shortcuts is not None:
            new_keys = [k for k in row.shortcuts if k not in bound_shortcuts]
            if new_keys:
                lines.append(
                    (
                        "yellow",
                        f"{row.info.label}'s shortcut keys ({', '.join(new_keys)}) bind at the next launch",
                    )
                )
    return lines if (changes or failures) else []


def reload_failure_text(phase: str | None, exc: BaseException, applied: bool) -> str:
    """The banner and transcript line for a ``/reload`` that raised (Codex F08)."""

    what = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    if applied:
        step = phase or "a later step"
        return f"⚠ /reload applied the new extensions, but {step} failed: {what}"
    step = phase or "the rebuild"
    return f"✖ /reload failed during {step}: {what} — nothing applied; /reload again or restart"


# --- /tool's placeholders (ADR-0283 D6 seam) -----------------------------------


def tool_rows(inventory: ExtensionInventory | None) -> list[tuple[ResourceItem, Lock]]:
    """One locked placeholder per extension the settings turned off, for ``/tool``."""

    if inventory is None:
        return []
    out: list[tuple[ResourceItem, Lock]] = []
    for row in inventory.rows:
        if row.selected or row.loaded:
            continue
        key = f"ext-off:{row.key}"
        item = ResourceItem(
            kind="tool",
            key=key,
            label=row.info.label,
            description="extension off: its tools are not loaded",
            group=key,
            group_label=f"EXTENSION  {row.info.label}",
            role="placeholder",
        )
        out.append(
            (
                item,
                Lock(False, "see /extension · off in settings", tag="ext off", kind="unavailable"),
            )
        )
    return out


@dataclass
class ExtensionControl:
    """What a host needs for ``/extension``: the service, the kind and the holder."""

    selection: ResourceSelection
    kind: ExtensionKind
    holder: dict[str, Any]
    """``{"inventory": ExtensionInventory | None, "reload_failure": str | None}``."""
    launch: McpLaunch | None = None

    def inventory(self) -> ExtensionInventory | None:
        return self.holder.get("inventory")


__all__ = [
    "BUILTINS",
    "EntryInfo",
    "ExtensionControl",
    "ExtensionInventory",
    "ExtensionKind",
    "ExtensionRow",
    "McpLaunch",
    "McpOwner",
    "Phase",
    "RefusedRow",
    "SETTINGS_KEY",
    "build_inventory",
    "count_untrusted_extensions",
    "describe",
    "extension_filter",
    "is_filtered_tier",
    "list_warnings",
    "listing_inventory",
    "mcp_launch",
    "reload_failure_text",
    "reload_lines",
    "restart_note",
    "target_of",
    "tool_rows",
    "without_hidden",
]
