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
* :class:`ExtensionGrammar` — the panel's save: ADR-0279 D1's edit, except that
  a row whose name another row shares is written by a form only it answers to
  (#25 verify V2), and no other row's saved value moves.
* :class:`ExtensionOffRecord` — what a delegated child inherits: the carriers
  its parent's settings turned off, to suppress only (#25 verify V5, the
  owner's "a child never gets more than its parent").

Identities (ADR-0284 D1): a manifest pack is named by its ``plugin.id``, then
its entry-point name and PEP 503 distribution name (kept when the directory
tier found the same pack first — review R01); a manifest-less entry point by
its name and distribution; a legacy file by its stem, a package directory by
its name, a file a ``pyproject.toml`` package declares by the package's name
and then its own stem (review R02); a built-in by ``builtin:<name>``. The
first is what the panel writes, unless another row shares it.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from collections import Counter
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .resource_filter import (
    FilterTarget,
    Scope,
    ScopeList,
    Target,
    edit,
    effective,
    matches_any,
    reaches,
    source_entries,
    split_entry,
)
from .resource_selection import (
    ApplyGate,
    ApplyResult,
    Effect,
    Lock,
    PerItemGrammar,
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
    package_path: str | None = None
    """The ``pyproject.toml`` package directory that declared this file (review R02):
    a path entry naming the directory names it too."""


def describe_record(record: Any) -> EntryInfo:
    """A discovery record's facts: its carrier's, plus the package that declared
    it and the names of a duplicate discovery dropped (reviews R01, R02)."""

    return describe(
        record.entry,
        record.tier,
        package=getattr(record, "package", None),
        aliases=getattr(record, "aliases", ()) or (),
    )


def describe(
    entry: Any, tier: str, *, package: Path | None = None, aliases: Sequence[str] = ()
) -> EntryInfo:
    """*entry* (a discovery carrier) as a row's facts, from metadata alone."""

    info = _describe(entry, tier, package)
    if aliases:
        info = replace(info, identities=_unique([*info.identities, *aliases]))
    return info


def _describe(entry: Any, tier: str, package: Path | None) -> EntryInfo:
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
        if package is not None:
            # A file a ``pyproject.toml`` package declares (review R02, V7): the
            # package is what a user names, and each declared file by its stem —
            # or by its label, as the panel shows it (round-1 verify R7).
            label = f"{package.name}/{path.name}"
            return EntryInfo(
                label=label,
                identities=_unique([package.name, path.stem, label]),
                path=str(path),
                scope=scope,
                description="(no manifest) · declared by its pyproject.toml",
                package_path=str(package),
            )
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
    return Target(
        identities=info.identities,
        path=info.path,
        scope=info.scope,
        package_path=info.package_path,
    )


def is_filtered_tier(record: DiscoveredEntry) -> bool:
    """Whether the settings may turn *record* off: never ``-e``, never a built-in."""

    return record.tier not in ("builtin", "cli") and not record.explicit


def extension_filter(
    lists: SettingsLists,
    *,
    phase: Phase,
    inherited: ExtensionOffRecord | None = None,
) -> Callable[[Sequence[Any]], list[bool]]:
    """The loader's ``entry_filter`` for *phase* (ADR-0284 D2; ADR-0279 D16, C5).

    ``G``: the global file only — the project's is not read before trust, so it
    cannot keep an extension from voting. ``P``: both, by ADR-0279 D2. Never
    raises: an entry whose evaluation fails loads, as it did before the filter.

    *inherited* (a delegated child, #25 verify V5): what its parent's settings
    turned off stays off here, in both phases, whatever this process's files
    say — it can only turn an extension off, never on.
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
                if inherited is not None and inherited.covers(record):
                    value = False
                else:
                    value, _scope = effective(
                        target_of(describe_record(record)), global_list, project_list
                    )
            except Exception:  # noqa: BLE001 — the filter never takes a build down
                value = True
            out.append(value)
        return out

    return _filter


def list_warnings(lists: SettingsLists, records: Sequence[Any], *, check_names: bool) -> list[str]:
    """ADR-0279 D8 for ``extensions``, said once per launch.

    A plain entry — a path that would ADD an extension source, pi's grammar — is
    not supported yet (owner decision 8, ADR-0284 D9) and is ignored. An entry
    naming a built-in is ignored too, and said (verify V8: pi's
    ``-builtin:<name>`` turns its built-ins off; aelix's are locked, ADR-0284
    D4). With *check_names* (discovery ran: not ``--no-extensions``), an ``!`` /
    ``+`` / ``-`` entry that names no extension found at launch.
    """

    out = list(lists.problems)
    targets = [target_of(describe_record(r)) for r in records]
    for scope_list in (lists.global_list, lists.project_list):
        if scope_list is None:
            continue
        label = lists.label(scope_list.scope)
        for entry in source_entries(scope_list.entries):
            out.append(
                f'{label}: "extensions" entry {entry!r} would add an extension source by '
                "path, which is not supported yet; it is ignored"
            )
        builtin: list[str] = []
        for entry in scope_list.entries:
            prefix, body = split_entry(entry)
            if prefix and body.startswith("builtin:"):
                builtin.append(entry)
                out.append(
                    f'{label}: "extensions" entry {entry!r} is ignored: Guardrail, Permission '
                    "and Status are always on, and agents follows [features] agents (/settings)"
                )
        if not check_names:
            continue
        for entry in scope_list.entries:
            if not split_entry(entry)[0] or entry in builtin:
                continue
            if not matches_any(entry, targets, scope_list.base_dir, list_scope=scope_list.scope):
                out.append(
                    f'{label}: "extensions" entry {entry!r} names no extension found at launch'
                )
    return out


# --- what a delegated child inherits (#25 verify V5) ---------------------------

EXTENSIONS_PARENT_OFF_ENV = "AELIX_EXTENSIONS_PARENT_OFF"
"""The environment variable a delegated child reads its parent's off-set from.

``AELIX_``: a cwd ``.env`` cannot set it (``runtime_bootstrap._DOTENV_NEVER``)
unless the user's own ``AELIX_DOTENV_ALLOW`` lists it, so a cloned repo cannot
inject it — and it could only turn the user's extensions off anyway. The
launch reads it and removes it from ``os.environ`` (``cli/entry.py``), so the
child's bash and ``api.exec`` do not hand it on; the child's own children get
:data:`_launch_off` instead (as ADR-0282 D9's MCP record)."""

_OFF_RECORD_MAX_BYTES = 16 * 1024
"""The most :meth:`ExtensionOffRecord.to_env` writes: an environment string has
a ceiling (ADR-0282 D9 measured it). Past it the record says ``every``: the child
loads no discovered extension, which is less than its parent, never more."""


def carrier_key(entry: Any) -> str | None:
    """What names one carrier in another process: its resolved path, or its entry point.

    ``None`` for what the settings never filter (a module ``-e`` named, a built-in).
    """

    from aelix_coding_agent.extensions.loader import (  # noqa: PLC0415
        _EntryPointEntry,
        _ManifestEntry,
    )

    if isinstance(entry, _EntryPointEntry):
        return f"ep:{entry.name}"
    if isinstance(entry, _ManifestEntry):
        path: Path = entry.pkg_dir
    elif isinstance(entry, Path):
        path = entry
    else:
        return None
    try:
        return "path:" + os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return "path:" + os.path.normcase(os.path.abspath(path))


@dataclass(frozen=True)
class ExtensionOffRecord:
    """The extensions a parent's launch kept off, as a delegated child inherits them.

    ``keys`` are :func:`carrier_key` values; ``every`` says every discovered
    extension was off (the parent ran ``--no-extensions``, or the record did not
    fit). SUPPRESS ONLY: a child turns off what it covers and reads nothing
    else from it — it names no file to load and cannot turn anything on.
    ``-e`` and the built-ins are never covered (:func:`is_filtered_tier`).
    """

    keys: frozenset[str] = frozenset()
    every: bool = False

    def __bool__(self) -> bool:
        return bool(self.keys or self.every)

    def __or__(self, other: ExtensionOffRecord) -> ExtensionOffRecord:
        return ExtensionOffRecord(self.keys | other.keys, self.every or other.every)

    def covers(self, record: Any) -> bool:
        if not is_filtered_tier(record):
            return False
        if self.every:
            return True
        key = carrier_key(record.entry)
        return key is not None and key in self.keys

    def to_env(self) -> str:
        """JSON, ASCII-only, at most :data:`_OFF_RECORD_MAX_BYTES`."""

        raw = json.dumps(
            {"off": sorted(self.keys), "all": self.every}, ensure_ascii=True, separators=(",", ":")
        )
        if len(raw) > _OFF_RECORD_MAX_BYTES:
            return json.dumps({"off": [], "all": True}, separators=(",", ":"))
        return raw

    @classmethod
    def from_env(cls, raw: str | None) -> ExtensionOffRecord:
        """The record in *raw*; empty for anything malformed (it can then suppress less)."""

        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        keys = data.get("off")
        return cls(
            frozenset(k for k in keys if isinstance(k, str))
            if isinstance(keys, list)
            else frozenset(),
            data.get("all") is True,
        )

    @classmethod
    def of(cls, records: Sequence[Any], *, no_extensions: bool = False) -> ExtensionOffRecord:
        """What a build's discovery *records* left off (``--no-extensions``: everything)."""

        if no_extensions:
            return cls(every=True)
        keys = {
            key
            for record in records
            if is_filtered_tier(record) and not record.selected
            for key in (carrier_key(record.entry),)
            if key is not None
        }
        return cls(frozenset(keys))


_launch_off: ExtensionOffRecord | None = None
"""What THIS process's latest committed build kept off, with what it inherited
(``cli/entry.py`` sets it); ``None`` in a process that never built one."""


def inherited_extension_off(environ: Mapping[str, str] | None = None) -> ExtensionOffRecord:
    """The record a delegating aelix left in this process's environment."""

    source = os.environ if environ is None else environ
    return ExtensionOffRecord.from_env(source.get(EXTENSIONS_PARENT_OFF_ENV))


def set_launch_extension_off(record: ExtensionOffRecord | None) -> None:
    """Record what this process keeps off, for the children it delegates to."""

    global _launch_off
    _launch_off = record


def pass_extension_off_to_child(env: MutableMapping[str, str]) -> None:
    """Put this process's off-set into a delegated child's environment.

    Called by ``aelix_agents.print_channel.build_child_env``, the one builder both
    channels (and a resume) use. A process that recorded nothing leaves ``env``
    as inherited — a record from further up still passes through.
    """

    if _launch_off is None:
        return
    if _launch_off:
        env[EXTENSIONS_PARENT_OFF_ENV] = _launch_off.to_env()
    else:
        env.pop(EXTENSIONS_PARENT_OFF_ENV, None)


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
    carrier: str | None = None
    """:func:`carrier_key` — what ties a row to the MCP servers its pack declared."""


@dataclass(frozen=True)
class RefusedRow:
    """A carrier discovery could not produce (a malformed manifest, a refused ``-e``)."""

    key: str
    label: str
    reason: str
    path: str | None = None


@dataclass(frozen=True)
class ExtensionInventory:
    """What one harness build discovered and loaded — published when it is committed (F08)."""

    rows: tuple[ExtensionRow, ...] = ()
    refused: tuple[RefusedRow, ...] = ()
    runtime: Any = None
    no_extensions: bool = False
    mcp_hidden: frozenset[str] = frozenset()
    """The started MCP servers whose tools this build left out (owner decision 10) —
    the decision the factory APPLIED, which is what every surface reports (verify V1)."""

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
        info = describe_record(record)
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
                error=(
                    _error_text(error.error, getattr(outcome, "error_type", None))
                    if error is not None
                    else None
                ),
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
                carrier=carrier_key(record.entry),
            )
        )
    refused: list[RefusedRow] = []
    for index, err in enumerate(loaded.errors):
        if id(err) in tied:
            continue
        path = str(getattr(err, "path", "") or "?")
        label = os.path.basename(path.rstrip("/" + os.sep)) or path
        refused.append(
            RefusedRow(f"refused:{index}:{path}", label, str(getattr(err, "error", "")), path)
        )
    return ExtensionInventory(tuple(rows), tuple(refused), loaded.runtime, no_extensions)


def _error_text(message: str, error_type: str | None) -> str:
    """``RuntimeError: boom`` — the type first, as the guide shows it (verify V7)."""

    if not error_type:
        return message
    return f"{error_type}: {message}" if message else error_type


def listing_inventory(records: Sequence[Any]) -> ExtensionInventory:
    """Rows for carriers no build loaded (``--no-extensions``): discovery only, nothing imported."""

    rows: list[ExtensionRow] = []
    seen: set[str] = set()
    for record in records:
        if record.tier in ("builtin", "cli"):
            continue
        info = describe_record(record)
        key = _row_key(record.tier, info)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            ExtensionRow(
                key,
                info,
                record.tier,
                record.explicit,
                False,
                False,
                carrier=carrier_key(record.entry),
            )
        )
    return ExtensionInventory(tuple(rows), (), None, True)


def display_labels(rows: Sequence[ExtensionRow]) -> dict[str, str]:
    """Each row's label, with its tier when another row has the same one (verify V2):
    ``weather.py (project)`` and ``weather.py (global)`` in a summary and a reload line."""

    counts = Counter(r.info.label for r in rows if r.tier != "builtin")
    return {
        r.key: f"{r.info.label} ({r.tier})" if counts[r.info.label] > 1 else r.info.label
        for r in rows
    }


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
    carrier: str | None = None
    """:func:`carrier_key` of the declaring pack — its inventory row."""


@dataclass(frozen=True)
class McpLaunch:
    """Fixed at launch: who declared each server and which were started (F11).

    The connections and their tool wrappers are made once; ``/reload`` only
    hides the tools of a server whose plugin is off now (owner decision 10).
    A server ``mcp.json`` names is the file's (#512), whatever a plugin says.
    ``tools`` holds each started server's wrappers as the launch made them, so
    hiding a server removes exactly its own (review R07: a server may be named
    ``jira__admin``, and ``jira__`` is no prefix to hide ``jira`` by).
    """

    owners: Mapping[str, McpOwner] = field(default_factory=dict)
    tools: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)

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

    def visible(self, tools: Sequence[Any], hidden: frozenset[str]) -> list[Any]:
        """*tools* minus the wrappers of *hidden* servers."""

        return without_hidden(tools, hidden, self.tools)


def mcp_launch(
    records: Sequence[Any],
    started: Sequence[Any],
    mcp_json: set[str],
    tools: Mapping[str, Sequence[Any]] | None = None,
) -> McpLaunch:
    """Who declared each manifest server, from the launch scan's records.

    *started* are the manifest contributions the launch handed the MCP manager,
    in its order: the manager keeps the LAST of a name, so that one's pack owns
    the connection (review R03 — the owner was the first pack found, which a
    project/global clash made the losing one). A server no started contribution
    names is listed under the pack that declared it, the scan's project tier first.
    """

    from aelix_coding_agent.extensions.loader import _ManifestEntry  # noqa: PLC0415

    manifests = [r for r in records if isinstance(r.entry, _ManifestEntry)]
    declared_by = {
        id(contrib): record
        for record in manifests
        for contrib in record.entry.manifest.contributes.mcp_servers
    }
    winners: dict[str, Any] = {}
    for contrib in started:
        record = declared_by.get(id(contrib))
        if record is not None:
            winners[str(contrib.name)] = record
    owners: dict[str, McpOwner] = {}
    for server, record in winners.items():
        info = describe_record(record)
        owners[server] = McpOwner(
            plugin=info.label,
            target=target_of(info),
            filtered=is_filtered_tier(record),
            started=True,
            carrier=carrier_key(record.entry),
        )
    for record in reversed(manifests):
        info = describe_record(record)
        for server in info.mcp_servers:
            if server in mcp_json or server in owners:
                continue
            owners[server] = McpOwner(
                plugin=info.label,
                target=target_of(info),
                filtered=is_filtered_tier(record),
                started=False,
                carrier=carrier_key(record.entry),
            )
    wrappers = {name: tuple(ts) for name, ts in (tools or {}).items() if name in winners}
    return McpLaunch(owners, wrappers)


def without_hidden(
    tools: Sequence[Any], hidden: frozenset[str], wrappers: Mapping[str, Sequence[Any]]
) -> list[Any]:
    """*tools* minus the *wrappers* of *hidden* servers, by identity (review R07)."""

    if not hidden:
        return list(tools)
    drop = {id(t) for server in hidden for t in wrappers.get(server, ())}
    return [t for t in tools if id(t) not in drop]


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
                # Review R09: a file ``-e`` loaded this run is its committed row,
                # not also a second, locked-off one from the listing.
                committed = {r.carrier for r in inventory.rows if r.carrier is not None}
                extra = tuple(r for r in listed.rows if r.carrier not in committed)
                inventory = replace(inventory, rows=inventory.rows + extra)
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

    @property
    def grammar(self) -> ExtensionGrammar:
        """ADR-0279 D1's grammar, writing a form only the edited row answers to (V2)."""

        return ExtensionGrammar(self)

    def items(self) -> list[ResourceItem]:
        inventory = self._inventory()
        rows: list[tuple[tuple[int, int], ResourceItem]] = []
        has_agents = False
        labels = display_labels(inventory.rows)
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
                        label=labels.get(row.key, info.label),
                        identities=info.identities,
                        description=description,
                        group=group,
                        group_label=label,
                        path=info.path,
                        scope=info.scope,
                        location=self._location(row),
                        package_path=info.package_path,
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
        """Read-only rows (ADR-0284 D7): #512's state word and the declaring plugin.

        "hidden" is the decision the last build APPLIED (``mcp_hidden``), never
        inferred from the plugin's load (verify V1): a pack that is on but failed
        to load leaves the server it declared connected AND its tools in use.
        """

        try:
            statuses = list(self.mcp_statuses() or ())
        except Exception:  # noqa: BLE001
            statuses = []
        try:
            launch = self.mcp_launch() or McpLaunch()
        except Exception:  # noqa: BLE001
            launch = McpLaunch()
        by_carrier = {r.carrier: r for r in inventory.rows if r.carrier is not None}
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
            # The why goes in the description, which wraps; the state line is
            # one row and cut at the width (verify V10).
            why = ""
            owner_row = (
                by_carrier.get(owner.carrier)
                if owner is not None and owner.carrier is not None
                else None
            )
            if owner is not None and owner.started and name in inventory.mcp_hidden:
                line = f"{line} · tools hidden"
                tag = f"{state} · hidden" if state == "connected" else state
                why = f"{owner.plugin} is off: its tools are hidden; it disconnects at restart"
            elif (
                owner is not None
                and owner.started
                and owner_row is not None
                and owner_row.selected
                and not owner_row.loaded
            ):
                line = f"{line} · tools in use"
                why = (
                    f"{owner.plugin} failed to load, but this server started at launch: "
                    "its tools stay in use"
                )
            elif state == "disabled":
                config = getattr(status, "config", None)
                # The pointer before the path (#510 verify V5): a narrow line
                # cuts the file name, not where to look.
                line = f'Disabled · see /mcp · "enabled": false in {config or "mcp.json"}'
            item = ResourceItem(
                kind="extension",
                key=f"mcp:{name}",
                label=name,
                description=f"{transport} · {source}" + (f" · {why}" if why else ""),
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
                why = ""
                if inventory.no_extensions:
                    line = "Not started · --no-extensions"
                elif row.selected:
                    line = "Not started · connects at the next launch"
                    why = f"{row.info.label} was off at launch, so nothing was started for it"
                else:
                    line = f"Not started · {row.info.label} is off"
                item = ResourceItem(
                    kind="extension",
                    key=f"mcp:{name}",
                    label=name,
                    description=f"declared by {row.info.label}" + (f" · {why}" if why else ""),
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
            # Verify V4: every row is locked this run, as every panel's lock is,
            # so the notice no longer says a choice saves.
            notes.append(
                "--no-extensions: only -e extensions load this run; "
                "run without it to turn the others on and off."
            )
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


class ExtensionGrammar(PerItemGrammar):
    """ADR-0279 D1's grammar for the Installed tab, with one difference (verify V2).

    D1's edit writes an item's FIRST identity. Two extensions can share it — a
    global and a project ``weather.py`` are both ``weather`` — and ``-weather``
    then turned both off when one row was toggled. Here the edit writes the
    first identity no other row has; failing that, the row's path form, which
    reaches only its own file's scope (ADR-0279 D2); failing that, the name it
    shares when writing it moves no other row (review R3: both rows off in one
    save), else it refuses and says which file can name it. When the edit
    drops an entry that also named another row (``-weather``, turning one of
    them back on) and that row's value would move, an entry naming that row
    alone is added to keep it; when this file cannot name it alone, the save is
    refused and names the entry to add to the other file by hand (review R2).
    A global edit is checked against this project's file too, which is where a
    row's value is decided first.
    """

    checks_both_files = True

    def __init__(self, kind: ExtensionKind) -> None:
        self._kind = kind

    def edit(
        self,
        entries: list[str] | None,
        item: ResourceItem,
        want: bool,
        *,
        scope: Scope,
        global_list: ScopeList | None,
        base_dir: str,
        project_list: ScopeList | None = None,
    ) -> list[str] | None:
        rows = [
            i
            for i in self._kind.items()
            if i.role == "item" and i.identities and i.group not in ("builtin", "cli")
        ]
        others = [i for i in rows if i.key != item.key]

        def value(other: ResourceItem, listed: Sequence[str]) -> bool:
            own = ScopeList(scope, tuple(listed), base_dir)
            if scope == "project":
                return effective(other, global_list, own)[0]
            return effective(other, own, project_list)[0]

        current = list(entries or ())
        before = {o.key: value(o, current) for o in others}
        inherited = True if scope == "global" else effective(item, global_list, None)[0]
        target: FilterTarget = item
        refusal: ValueError | None = None
        try:
            target = _naming_target(item, others, scope, base_dir)
        except ValueError as exc:
            # Review R3: nothing only this row answers to. The name it shares
            # is still the edit when writing it moves no other row — both
            # ``weather.py`` rows off in one save is ``-weather``.
            refusal = exc
        out = edit(current, target, want, inherited, base_dir, list_scope=scope)
        for other in others:
            was = before[other.key]
            if value(other, out) == was:
                continue
            if refusal is not None:
                raise refusal
            try:
                restore = _naming_target(
                    other, [i for i in rows if i.key != other.key], scope, base_dir
                )
            except ValueError:
                raise ValueError(
                    self._by_hand(item, want, other, was, scope, global_list, project_list)
                ) from None
            out.append(("+" if was else "-") + restore.identities[0])
        return out

    def _by_hand(
        self,
        item: ResourceItem,
        want: bool,
        other: ResourceItem,
        was: bool,
        scope: Scope,
        global_list: ScopeList | None,
        project_list: ScopeList | None,
    ) -> str:
        """The refusal when *other* would move and only the other file can name it (review R2).

        The panel cannot write that entry: a row there that already reads
        *was* has no edit to save. So it names the entry and the file to add
        it to by hand — what to do first, then why, as #510's refusals do.
        """

        turning = (
            f"turning {item.label} {'on' if want else 'off'} would also turn "
            f"{other.label} {'off' if was else 'on'}"
        )
        there: Scope = "global" if scope == "project" else "project"
        listed = global_list if there == "global" else project_list
        if other.path is None or listed is None or not reaches(other, there):
            return f"{turning}, which no settings file can name on its own"
        entry = ("+" if was else "-") + _path_form(other.path, listed.base_dir)
        where = (
            _short(listed.path, self._kind._home, self._kind.cwd)
            if listed.path
            else f"the {there} settings"
        )
        # Short enough to wrap whole into the four rows a refusal gets at 50 columns.
        return f'add "{entry}" to {where}, then save again — {turning}, which only that file can name'


def _naming_target(
    item: ResourceItem, others: Sequence[ResourceItem], scope: Scope, base_dir: str
) -> Target:
    """*item* as a target whose first identity only it answers to (verify V2); raises to refuse."""

    taken = {name for other in others for name in other.identities}
    first = next((name for name in item.identities if name not in taken), None)
    if first is None and item.path and reaches(item, scope):
        first = _path_form(item.path, base_dir)
    if first is None:
        own = "This project" if item.scope == "project" else "Global"
        raise ValueError(
            f"{item.label} shares its name with another extension, and a path in "
            f"{'this project' if scope == 'project' else 'the global'} file cannot name it: "
            f"save it to {own}"
        )
    return Target(
        identities=(first, *item.identities),
        path=item.path,
        scope=item.scope,
        package_path=item.package_path,
    )


def _path_form(path: str, base_dir: str) -> str:
    """The path entry D1 writes for *path*: relative to the list's directory when
    it lies under it, absolute otherwise, ``/`` on every platform."""

    for base in dict.fromkeys((base_dir, os.path.realpath(base_dir))):
        for spelling in dict.fromkeys((path, os.path.realpath(path))):
            try:
                rel = os.path.relpath(spelling, base)
            except ValueError:  # another drive on Windows
                continue
            if not rel.startswith(".."):
                return rel.replace("\\", "/")
    return path.replace("\\", "/")


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

    Empty when nothing changed, nothing failed and nothing waits. The first line
    names what changed; then one line per failure — a pack that failed to load
    and one discovery refused (a malformed manifest, review R06) — per MCP
    server whose tools this build hid, or whose failed pack leaves them in use
    (verify V1), or that waits for the next launch, per provider that stays
    until restart and per shortcut key that binds at the next launch — a new
    one on an extension that was already on too (review R08).
    """

    if after is None:
        return []
    prior_rows = before.rows if before is not None else ()
    old = {r.key: r for r in prior_rows}
    names = display_labels(after.rows)
    old_names = display_labels(prior_rows)
    refused_paths = {_real(r.path) for r in after.refused if r.path}
    changes: list[str] = []
    dropped: list[ExtensionRow] = []
    turned_on: list[ExtensionRow] = []
    for row in after.rows:
        if row.tier == "builtin":
            continue
        prior = old.get(row.key)
        was = prior.loaded if prior is not None else False
        name = names.get(row.key, row.info.label)
        if row.loaded and not was:
            changes.append(f"{name} on" + ("" if prior is not None else " (new)"))
            turned_on.append(row)
        elif was and not row.loaded and prior is not None:
            # Saved on and not loaded is a failure, never "off" (verify V1).
            changes.append(f"{name} {'failed' if row.selected else 'off'}")
            dropped.append(prior)
        elif prior is not None and prior.selected and not row.selected and not row.loaded:
            # It was failing; now the settings turn it off.
            changes.append(f"{name} off")
    for key, prior in old.items():
        if prior.tier != "builtin" and prior.loaded and after.row(key) is None:
            gone = prior.info.path is not None and _real(prior.info.path) in refused_paths
            changes.append(
                f"{old_names.get(key, prior.info.label)} {'failed' if gone else 'removed'}"
            )
            dropped.append(prior)
    failures = [r for r in after.rows if r.selected and r.error is not None]
    owners = launch.owners if launch is not None else {}
    notes: list[tuple[str, str]] = []
    for row in failures:
        name = names.get(row.key, row.info.label)
        notes.append(("red", f"✖ {name} failed to load: {row.error} (see /extension)"))
        for server in row.info.mcp_servers:
            owner = owners.get(server)
            if (
                owner is not None
                and owner.started
                and owner.carrier == row.carrier
                and server not in after.mcp_hidden
            ):
                notes.append(("yellow", f"{server} (MCP) stays connected; its tools stay in use"))
    for refused in after.refused:
        notes.append(
            ("red", f"✖ {refused.label} was not loaded: {refused.reason} (see /extension)")
        )
    hidden_before = before.mcp_hidden if before is not None else frozenset()
    for server in sorted(after.mcp_hidden - hidden_before):
        notes.append(
            ("yellow", f"{server} (MCP) stays connected until restart; its tools are hidden")
        )
    for row in dropped:
        for provider in row.providers:
            notes.append(("yellow", f"{provider} (provider) stays in /model until restart"))
    for row in turned_on:
        for server in row.info.mcp_servers:
            owner = owners.get(server)
            if owner is None or not owner.started:
                notes.append(("yellow", f"{server} (MCP) connects at the next launch"))
    if bound_shortcuts is not None:
        for row in after.rows:
            if not row.loaded or row.tier == "builtin":
                continue
            prior = old.get(row.key)
            known = set(prior.shortcuts) if prior is not None and prior.loaded else set()
            new_keys = [k for k in row.shortcuts if k not in bound_shortcuts and k not in known]
            if new_keys:
                notes.append(
                    (
                        "yellow",
                        f"{names.get(row.key, row.info.label)}'s shortcut keys "
                        f"({', '.join(new_keys)}) bind at the next launch",
                    )
                )
    if not changes and not notes:
        return []
    head = f"Extensions after /reload: {' · '.join(changes) if changes else 'no change'}"
    return [("", head), *notes]


def _real(path: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return path


def failure_lines(inventory: ExtensionInventory | None) -> list[tuple[str, str]]:
    """What a ``/new``, ``/fork`` or ``/resume`` build failed to load (verify V11).

    Under the TUI those builds no longer print ``Warning: extension load:`` over
    the input; this is committed below the new session's banner instead.
    """

    if inventory is None:
        return []
    names = display_labels(inventory.rows)
    out = [
        ("red", f"✖ {names.get(r.key, r.info.label)} failed to load: {r.error} (see /extension)")
        for r in inventory.rows
        if r.selected and r.error is not None
    ]
    out += [
        ("red", f"✖ {r.label} was not loaded: {r.reason} (see /extension)")
        for r in inventory.refused
    ]
    return out


ReloadOutcome = Literal["applied", "kept", "stopped"]


def reload_outcome(exc: BaseException, *, replaced: bool) -> ReloadOutcome:
    """What runs after a ``/reload`` raised ``exc`` (#663, ADR-0289).

    ``kept`` / ``stopped`` are what :class:`RebuildFailedError` says (the old
    harness was torn down; the session was built again on its previous
    settings, or stopped). Any other exception: ``applied`` when the new
    harness was installed (``replaced``) and a later step failed, ``kept`` when
    it raised before anything was torn down.
    """

    from aelix_agent_core.runtime import RebuildFailedError

    if isinstance(exc, RebuildFailedError):
        return exc.state
    return "applied" if replaced else "kept"


def _what(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def reload_failure_text(phase: str | None, exc: BaseException, outcome: ReloadOutcome) -> str:
    """The banner and transcript line for a ``/reload`` that raised (Codex F08, #663).

    It states what runs now (ADR-0289): the new build (``applied``), the
    previous settings (``kept``), or nothing until a ``/reload`` succeeds
    (``stopped``).
    """

    from aelix_agent_core.runtime import STOPPED_MESSAGE, RebuildFailedError

    if outcome == "applied":
        step = phase or "a later step"
        return f"⚠ /reload applied the new extensions, but {step} failed: {_what(exc)}"
    step = phase or "the rebuild"
    cause = exc.cause if isinstance(exc, RebuildFailedError) else exc
    if outcome == "stopped":
        again = exc.restore_error if isinstance(exc, RebuildFailedError) else None
        also = f" ({_what(again)})" if again is not None and _what(again) != _what(cause) else ""
        return (
            f"✖ /reload failed during {step}: {_what(cause)}; rebuilding with the previous "
            f"settings failed too{also} — {STOPPED_MESSAGE}"
        )
    return (
        f"✖ /reload failed during {step}: {_what(cause)} — kept the previous settings; "
        "fix the cause, then /reload again"
    )


# --- /tool's placeholders (ADR-0283 D6 seam) -----------------------------------


def tool_rows(inventory: ExtensionInventory | None) -> list[tuple[ResourceItem, Lock]]:
    """One locked placeholder per extension the settings turned off, for ``/tool``."""

    if inventory is None:
        return []
    out: list[tuple[ResourceItem, Lock]] = []
    labels = display_labels(inventory.rows)
    for row in inventory.rows:
        if row.selected or row.loaded:
            continue
        key = f"ext-off:{row.key}"
        label = labels.get(row.key, row.info.label)
        item = ResourceItem(
            kind="tool",
            key=key,
            label=label,
            description="extension off: its tools are not loaded",
            group=key,
            group_label=f"EXTENSION  {label}",
            role="placeholder",
        )
        out.append(
            (
                item,
                Lock(False, "see /extension · off in settings", tag="ext off", kind="unavailable"),
            )
        )
    return out


def tool_group_tiers(inventory: ExtensionInventory | None) -> dict[str, str]:
    """By ``Extension.name``, the tier of each loaded extension whose label another
    row shares: ``/tool`` adds it to that extension's group title (round-1 verify
    R6), as :func:`tool_rows` does to an off one's."""

    if inventory is None:
        return {}
    labels = display_labels(inventory.rows)
    return {
        row.extension_name: row.tier
        for row in inventory.rows
        if row.extension_name and labels.get(row.key, row.info.label) != row.info.label
    }


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
    "EXTENSIONS_PARENT_OFF_ENV",
    "SETTINGS_KEY",
    "EntryInfo",
    "ExtensionControl",
    "ExtensionGrammar",
    "ExtensionInventory",
    "ExtensionKind",
    "ExtensionOffRecord",
    "ExtensionRow",
    "McpLaunch",
    "McpOwner",
    "Phase",
    "RefusedRow",
    "ReloadOutcome",
    "build_inventory",
    "carrier_key",
    "count_untrusted_extensions",
    "describe",
    "describe_record",
    "display_labels",
    "extension_filter",
    "failure_lines",
    "inherited_extension_off",
    "is_filtered_tier",
    "list_warnings",
    "listing_inventory",
    "mcp_launch",
    "pass_extension_off_to_child",
    "reload_failure_text",
    "reload_lines",
    "reload_outcome",
    "restart_note",
    "set_launch_extension_off",
    "target_of",
    "tool_group_tiers",
    "tool_rows",
    "without_hidden",
]
