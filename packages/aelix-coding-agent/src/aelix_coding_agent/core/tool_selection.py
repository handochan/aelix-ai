"""Tools under the resource selection contract (#510, ADR-0283; ADR-0279 D15).

Four things live here:

* :class:`DefaultToolsGrammar` — the ``defaultTools`` list, read, evaluated and
  edited. pi's grammar (``coding-agent/src/core/settings-manager.ts``
  ``resolveDefaultTools`` / ``mergeDefaultTools``) with the aelix base of
  EVERY tool on (owner decision 3, 2026-10-31): plain names replace the
  BUILT-IN set and nothing else; ``+name`` / ``-name`` turn any tool on or
  off, extension and MCP tools included, in list order; a project list with a
  plain name replaces the global one, one of only ``±`` entries is appended to
  it. An absent key is every tool on and ``[]`` is pi's "no built-ins" — kept
  apart through :attr:`ScopeList.present` (Codex F04). No globs.
* :func:`build_tool_policy` — the run's :class:`ToolPolicy` from the parsed
  flags and the saved selection, the one place the CLI's tool flags become
  the kernel's rules.
* :func:`launch_tool_warnings` — the D8 warnings said once per launch.
* :class:`ToolKind` — the ``/tool`` panel's :class:`ResourceKind`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast, get_args

from aelix_agent_core.harness.tool_policy import (
    ToolLock,
    ToolPolicy,
    tool_name_matcher,
    tool_pattern_matches,
)

from .resource_filter import Scope, ScopeList
from .resource_selection import (
    ApplyGate,
    ApplyResult,
    Effect,
    Lock,
    LockKind,
    ResourceItem,
    ResourceSelection,
    SettingsLists,
    harness_busy,
    wait_harness_idle,
)

if TYPE_CHECKING:
    from aelix_ai.settings import SettingsManager

#: The JSON key (``Settings.default_tools``).
_SETTINGS_KEY = "defaultTools"

_MODIFIERS = ("+", "-")

_UNNAMED = "\x00other"
"""A name no list spells, standing for every such tool when the editor checks a change."""


def _is_modifier(entry: str) -> bool:
    return entry[:1] in _MODIFIERS


def _builtin_order() -> tuple[str, ...]:
    from aelix_coding_agent.tools import ToolName  # noqa: PLC0415 — cheap, avoids a cycle

    return tuple(get_args(ToolName))


def _excluded_of(harness: Any) -> list[tuple[Any, str]]:
    """``harness.excluded_tools()`` — ``[]`` for a harness without a policy (a test double)."""

    getter = getattr(harness, "excluded_tools", None)
    if not callable(getter):
        return []
    return list(cast("Iterable[tuple[Any, str]]", getter()))


# --- the grammar -------------------------------------------------------------


@dataclass(frozen=True)
class _Merged:
    """The list both files make, as pi merges it, and the file whose list is the base."""

    entries: tuple[tuple[Scope, str], ...]
    base: Scope | None
    """The file whose list REPLACES the built-in set (it has a plain name, or is
    ``[]`` taken alone); ``None`` when the base is every built-in."""


def _replacing(entries: Sequence[str]) -> bool:
    """pi ``resolveDefaultTools``: a plain name, or an empty list, replaces the base."""

    return not entries or any(not _is_modifier(e) for e in entries)


def merge_default_tools(
    global_list: ScopeList | None, project_list: ScopeList | None
) -> _Merged | None:
    """pi ``mergeDefaultTools``, keeping which file each entry came from. ``None``: neither has it."""

    def _present(lst: ScopeList | None) -> bool:
        return lst is not None and lst.present

    def _pairs(lst: ScopeList) -> tuple[tuple[Scope, str], ...]:
        return tuple((lst.scope, e) for e in lst.entries)

    if not _present(project_list):
        if not _present(global_list):
            return None
        assert global_list is not None
        return _Merged(_pairs(global_list), "global" if _replacing(global_list.entries) else None)
    assert project_list is not None
    if not _present(global_list):
        return _Merged(
            _pairs(project_list), "project" if _replacing(project_list.entries) else None
        )
    assert global_list is not None
    if all(_is_modifier(e) for e in project_list.entries):
        # Only ``±`` (``[]`` included, vacuously): appended to the global list.
        return _Merged(
            _pairs(global_list) + _pairs(project_list),
            "global" if _replacing(global_list.entries) else None,
        )
    return _Merged(_pairs(project_list), "project")


def default_tool_value(
    name: str, merged: _Merged | None, builtin: frozenset[str]
) -> tuple[bool, Scope | None]:
    """Whether *name* is on, and the file that decided it (``None``: the default, on)."""

    if merged is None:
        return True, None
    on, decided = True, None
    if name in builtin and merged.base is not None:
        on = any(e == name for _s, e in merged.entries if not _is_modifier(e))
        decided = merged.base
    for scope, entry in merged.entries:
        if entry == f"+{name}":
            on, decided = True, scope
        elif entry == f"-{name}":
            on, decided = False, scope
    return on, decided


def mentioned_names(*lists: ScopeList | None) -> set[str]:
    """Every tool name the lists spell, plain or after ``±``."""

    out: set[str] = set()
    for lst in lists:
        if lst is None:
            continue
        for entry in lst.entries:
            out.add(entry[1:] if _is_modifier(entry) else entry)
    return out


def resolve_saved_off(
    global_list: ScopeList | None, project_list: ScopeList | None, builtin: frozenset[str]
) -> frozenset[str]:
    """The names the two files turn off. Exact: any other name is on (owner decision 3)."""

    merged = merge_default_tools(global_list, project_list)
    if merged is None:
        return frozenset()
    universe = set(builtin) | mentioned_names(global_list, project_list)
    return frozenset(n for n in universe if not default_tool_value(n, merged, builtin)[0])


class EditRefused(ValueError):
    """No edit of the file makes the one tool change without moving another (#510 verify V1).

    Raised from inside ``SettingsManager.edit_list``'s transaction, so nothing
    is written; :meth:`~.resource_selection.ResourceSelection.save` reports
    the message.
    """


class DefaultToolsGrammar:
    """``defaultTools`` as a :class:`~.resource_selection.SelectionGrammar` (ADR-0279 D15)."""

    nullable = True
    checks_both_files = True
    """A global edit is checked against this project's list too (#510 verify V1)."""

    def __init__(self, builtin: frozenset[str]) -> None:
        self.builtin = frozenset(builtin)

    def validate(self, raw: object) -> tuple[list[str] | None, list[str]]:
        """``(entries or None, problems)``. A list whose every entry is refused reads as absent.

        Refused: a value that is not a list, a member that is not a non-empty
        string, a bare ``+`` / ``-``, an entry with ``*`` (exact names only) and
        a ``!`` entry (this list has no globs). Reading such a list as ``[]``
        would turn every built-in off for a typo; absent turns nothing off.
        """

        if raw is None:
            return None, []
        if not isinstance(raw, list):
            return None, [f"expected a list of strings, got {type(raw).__name__}"]
        kept: list[str] = []
        problems: list[str] = []
        for entry in raw:
            if not isinstance(entry, str):
                problems.append(f"{entry!r} is not a string")
                continue
            stripped = entry.strip()
            body = stripped[1:] if _is_modifier(stripped) else stripped
            if not body.strip():
                problems.append(f"{entry!r} names nothing")
            elif "*" in stripped:
                problems.append(f"{entry!r} is a pattern; defaultTools takes exact names only")
            elif stripped.startswith("!"):
                problems.append(f"{entry!r} is a glob; defaultTools takes name, +name or -name")
            else:
                kept.append(stripped)
        if raw and not kept:
            return None, problems
        return kept, problems

    def evaluate_all(
        self,
        items: Sequence[ResourceItem],
        global_list: ScopeList | None,
        project_list: ScopeList | None,
    ) -> dict[str, tuple[bool, Scope | None]]:
        merged = merge_default_tools(global_list, project_list)
        return {
            item.key: default_tool_value(item.identities[0], merged, self.builtin)
            for item in items
            if item.identities and item.role == "item"
        }

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
        """The list after the smallest change making *item* ``want`` — and nothing else change.

        *entries* are the file's RAW entries; every value here is read through
        :meth:`validate`, as the launch and the panel read the file (Codex F01,
        verify V2): a refused entry — ``*``, ``!``, blank, a bare ``+``/``-`` —
        is never a plain name, an entry is matched by its stripped form, and a
        list whose every entry is refused is absent, not ``[]``. Refused
        entries stay in the file where they were.

        Codex F03: a request the list already satisfies returns it unchanged;
        otherwise the candidates, smallest first, are: drop this name's
        ``±`` entries (a modifier list that empties is removed — ``None`` —
        because ``[]`` would turn the built-ins off); drop this name's own
        plain entry; append ``+name`` / ``-name``; and for ``[]`` as the base,
        which a modifier would turn into "every built-in", ``[name]`` (a
        built-in on) or ``[name, -name]`` (an extension tool off). EACH
        CANDIDATE IS CHECKED against every name the change could reach — the
        built-ins, every name either list spells, and one name neither does —
        and the first that changes this name alone wins, so a replacing list
        never turns into a modifier one and no other tool moves.

        What "no other tool moves" covers (verify V1): a project edit, the
        project file over the global one; a global edit, the global file
        alone — every folder without a project list reads only it — AND,
        given *project_list*, the two files together, since a project list of
        only ``±`` entries (``[]`` too) builds on the global one. The target
        is the edited file's own view; a project list that overrides it is
        the save's reported mismatch. No candidate passing raises
        :class:`EditRefused`, and nothing is written.
        """

        if not item.identities:
            return entries
        name = item.identities[0]
        views = self._views(entries, scope, global_list, project_list, base_dir)
        if self._value(name, views[0]) == want:
            return entries
        modifier = ("+" if want else "-") + name
        candidates: list[list[str] | None] = []
        if entries is None:
            candidates.append([modifier])
        else:
            pairs = [(e, self._normal(e)) for e in entries]
            kept_pairs = [(e, n) for e, n in pairs if n not in (f"+{name}", f"-{name}")]
            kept = [e for e, _n in kept_pairs]
            candidates.append(None if entries and not kept else kept)
            if not want and any(n == name for _e, n in kept_pairs):
                candidates.append([e for e, n in kept_pairs if n != name])
            candidates.append([*kept, modifier])
            if all(n is None for _e, n in kept_pairs):
                candidates.append(
                    [*kept, name] if name in self.builtin else [*kept, name, f"-{name}"]
                )
        lists = [lst for view in views for lst in view]
        universe = self.builtin | mentioned_names(*lists) | {name, _UNNAMED}
        before = [self._values(view, universe) for view in views]
        first_moved: list[dict[str, bool]] | None = None
        for candidate in candidates:
            after_views = self._views(candidate, scope, global_list, project_list, base_dir)
            after = [self._values(view, universe) for view in after_views]
            if after[0][name] != want:
                continue
            moved = [
                {n: a[n] for n in universe if n != name and a[n] != b[n]}
                for a, b in zip(after, before, strict=True)
            ]
            if not any(moved):
                return candidate
            if first_moved is None:
                first_moved = moved
        # A project ``[]`` stands alone only while the global file has no list
        # (verify r1 R1): the setup a global save is refused in.
        bare = (
            scope == "global"
            and project_list is not None
            and project_list.present
            and not project_list.entries
            and not self._read(entries, scope, base_dir).present
        )
        raise EditRefused(self._refusal(name, want, scope, first_moved or [], bare_project=bare))

    def _normal(self, entry: object) -> str | None:
        """*entry* as the read path keeps it (stripped), or ``None`` when it refuses it."""

        kept, _problems = self.validate([entry])
        return kept[0] if kept else None

    def _read(self, entries: list[str] | None, scope: Scope, base_dir: str) -> ScopeList:
        kept, _problems = self.validate(entries)
        return ScopeList(scope, tuple(kept or ()), base_dir, present=kept is not None)

    def _views(
        self,
        entries: list[str] | None,
        scope: Scope,
        global_list: ScopeList | None,
        project_list: ScopeList | None,
        base_dir: str,
    ) -> list[tuple[ScopeList | None, ScopeList | None]]:
        """The ways the edited file is read: its own view first (the target's), then the rest."""

        own = self._read(entries, scope, base_dir)
        if scope == "project":
            return [(global_list, own)]
        views: list[tuple[ScopeList | None, ScopeList | None]] = [(own, None)]
        if project_list is not None and project_list.present:
            views.append((own, project_list))
        return views

    def _value(self, name: str, view: tuple[ScopeList | None, ScopeList | None]) -> bool:
        return default_tool_value(name, merge_default_tools(*view), self.builtin)[0]

    def _values(
        self, lists: tuple[ScopeList | None, ScopeList | None], universe: Iterable[str]
    ) -> dict[str, bool]:
        merged = merge_default_tools(*lists)
        return {n: default_tool_value(n, merged, self.builtin)[0] for n in universe}

    def _refusal(
        self,
        name: str,
        want: bool,
        scope: Scope,
        moved: list[dict[str, bool]],
        *,
        bare_project: bool = False,
    ) -> str:
        """Why no edit was written, naming what the smallest one would also turn on or off."""

        def _turns(changed: dict[str, bool]) -> str:
            parts: list[str] = []
            for value in (True, False):
                shown = sorted(n for n, v in changed.items() if v is value and n != _UNNAMED)
                if changed.get(_UNNAMED) is value:
                    shown.append("the tools no list names")
                if shown:
                    parts.append(f"{', '.join(shown)} {'on' if value else 'off'}")
            return " and ".join(parts)

        here = moved[1] if len(moved) > 1 else {}
        alone = moved[0] if moved else {}
        word = "on" if want else "off"
        # What to do first: the panel wraps the reason, a narrow one cuts its end.
        # "Global" and "This project" are the panel's own words for the files.
        if scope == "global" and here:
            why = (
                " (a project [] means no built-ins only while Global has no list)"
                if bare_project
                else ""
            )
            return (
                f"{name}: save it to This project instead — on Global it would also turn "
                f"{_turns(here)} here{why}"
            )
        also = _turns(alone)
        return (
            f'{name}: edit "{_SETTINGS_KEY}" by hand — turning it {word} in the {scope} file '
            f"would also {f'turn {also}' if also else 'change other tools'}"
        )


def default_tools_lists(
    manager: SettingsManager | None,
    *,
    project_trusted: bool,
    global_base: str = "",
    project_base: str = "",
) -> SettingsLists:
    """Both files' ``defaultTools`` as this process loaded them (launch, ``/reload``, a rebuild)."""

    if manager is None:
        return SettingsLists()
    grammar = DefaultToolsGrammar(frozenset())
    problems: list[str] = []
    labels = {
        scope: str(manager.scope_path(scope) or f"the {scope} settings")
        for scope in ("global", "project")
    }

    def _one(scope: Scope) -> ScopeList | None:
        if scope == "project" and not project_trusted:
            return None
        settings = (
            manager.get_global_settings() if scope == "global" else manager.get_project_settings()
        )
        entries, bad = grammar.validate(getattr(settings, "default_tools", None))
        problems.extend(f'{labels[scope]}: "{_SETTINGS_KEY}" entry {p}; it is ignored' for p in bad)
        base = global_base if scope == "global" else project_base
        return ScopeList(scope, tuple(entries or ()), base, present=entries is not None)

    return SettingsLists(_one("global"), _one("project"), tuple(problems), labels)


# --- the policy --------------------------------------------------------------


def build_tool_policy(
    parsed: Any, *, saved_off: frozenset[str] = frozenset(), builtin: frozenset[str] | None = None
) -> ToolPolicy:
    """The run's rules from its flags (or a profile's overlay of them) and the saved selection.

    A flag the user typed is tagged with the flag; one an agent profile set
    (``/agents use``, ``--agent``) is tagged ``profile`` — ``Args.provided``
    records which. ``--tools ''`` names nothing and so allows everything, as
    :func:`~aelix_coding_agent.cli.entry._resolve_active_tools` reads it.
    """

    if builtin is None:
        from aelix_coding_agent.tools import ALL_TOOL_NAMES  # noqa: PLC0415

        builtin = ALL_TOOL_NAMES
    provided = set(getattr(parsed, "provided", ()) or ())

    def _tag(field: str, flag: str) -> str:
        return flag if field in provided else "profile"

    tools = list(getattr(parsed, "tools", None) or [])
    return ToolPolicy(
        exclude=tuple(dict.fromkeys(getattr(parsed, "exclude_tools", None) or ())),
        no_tools=bool(getattr(parsed, "no_tools", False)),
        no_tools_tag=_tag("no_tools", "--no-tools"),
        no_builtin_tools=bool(getattr(parsed, "no_builtin_tools", False)),
        no_builtin_tag=_tag("no_builtin_tools", "--no-builtin-tools"),
        allow=frozenset(tools) if tools else None,
        allow_tag=_tag("tools", "--tools"),
        builtin=frozenset(builtin),
        saved_off=saved_off,
    )


def delegated_exclusions(policy: ToolPolicy) -> tuple[str, ...]:
    """What a delegated child is started with as ``--exclude-tools`` (#510, owner decision B).

    The run's own patterns as typed, then — sorted — every name the
    ``defaultTools`` setting keeps off in this process now
    (:meth:`ToolPolicy.saved_off_in_force`: a ``--tools`` that names one
    keeps it on, and one turned back on by ``/tool`` or RPC is on). A child
    reads its own settings, and an untrusted one never reads the project's,
    so a tool turned off only there would otherwise come back in it.
    Suppress-only, like #512's MCP names: this only ever removes. Left out: a
    name one of the patterns already removes. A name no ``--exclude-tools``
    entry can carry (``tool_policy.carriable_pattern``) stays in, and the
    spawn is refused with the reason (``exclude_tools_refusal``): an MCP
    tool's name is its server's, verbatim, and leaving it out would give the
    child that tool (verify r1 R3).
    """

    covered = tool_name_matcher(policy.exclude)
    names = sorted(n for n in policy.saved_off_in_force() if not covered(n))
    return (*policy.exclude, *names)


def launch_tool_warnings(harness: Any, parsed: Any, lists: SettingsLists) -> list[str]:
    """What the launch says once on stderr about the tool flags and ``defaultTools`` (D8).

    After the first build, when the registry — built-in, extension and MCP
    tools — is known: an ``--exclude-tools`` entry that matches nothing (a
    typo), a ``--tools`` name an exclusion removes (it wins, decision 4), the
    settings problems, and a ``defaultTools`` name no tool loaded at launch has.
    """

    state = getattr(harness, "state", None)
    registered = [t.name for t in getattr(state, "tools", None) or []]
    excluded = [t.name for t, _r in _excluded_of(harness)]
    known = [*registered, *excluded]
    out: list[str] = []
    for pattern in dict.fromkeys(getattr(parsed, "exclude_tools", None) or ()):
        if not any(tool_pattern_matches(pattern, n) for n in known):
            # "at launch" (verify V6): a lazy ``register_tool`` or an RPC
            # client's MCP tool can still match it later, and is excluded then.
            out.append(f'--exclude-tools "{pattern}" matches no tool loaded at launch')
    named = [n for n in getattr(parsed, "tools", None) or [] if n in set(excluded)]
    if named:
        out.append(
            f"--tools names {', '.join(named)}, which --exclude-tools removes; "
            f"{'it is' if len(named) == 1 else 'they are'} off"
        )
    out.extend(f"settings: {p}" for p in lists.problems)
    universe = set(known)
    from aelix_coding_agent.tools import ALL_TOOL_NAMES  # noqa: PLC0415

    universe |= set(ALL_TOOL_NAMES)
    for scope_list in (lists.global_list, lists.project_list):
        if scope_list is None:
            continue
        for entry in scope_list.entries:
            target = entry[1:] if _is_modifier(entry) else entry
            if target not in universe:
                out.append(
                    f'settings: {lists.label(scope_list.scope)}: "{_SETTINGS_KEY}" entry '
                    f'"{entry}" names no tool loaded at launch'
                )
    return out


# --- the /tool panel ---------------------------------------------------------


def _first_line(text: str) -> str:
    """A tool description's first sentence — what a panel row has room for.

    Built-in descriptions are a paragraph written for the model; the row and
    the detail strip show the sentence that says what the tool is.
    """

    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            head, sep, _rest = line.partition(". ")
            return head if sep else line.removesuffix(".")
    return ""


def _extension_label(ext: Any) -> str:
    """``weather.py``, ``jira-tools``, ``status (built-in)`` — what a group header names."""

    manifest = getattr(ext, "manifest", None)
    plugin = getattr(manifest, "plugin", None)
    name = getattr(plugin, "name", None)
    if name:
        return str(name)
    raw = str(getattr(ext, "name", "") or "?")
    if manifest is None and raw.endswith("Extension") and os.sep not in raw and "/" not in raw:
        return f"{raw[: -len('Extension')].lower() or raw} (built-in)"
    if os.sep in raw or "/" in raw:
        return os.path.basename(raw.rstrip("/" + os.sep)) or raw
    return raw


def _lock_text(lock: ToolLock) -> tuple[str, LockKind]:
    """``(reason, kind)`` for a policy lock, in the panel's words."""

    if lock.tag == "profile":
        return f"{lock.reason} (/agents use --none clears it)", "profile"
    return f"{lock.reason} (this run only)", "cli"


class ToolKind:
    """The ``/tool`` panel's view of the live harness (``ResourceKind``, ADR-0283)."""

    kind: str = "tool"
    settings_key: str = _SETTINGS_KEY
    effect: Effect = "next_request"

    def __init__(
        self,
        *,
        harness: Callable[[], Any],
        gate: ApplyGate | None = None,
        builtin: frozenset[str] | None = None,
        mcp_statuses: Callable[[], Sequence[Any]] = lambda: (),
        extension_errors: Callable[[], Sequence[Any]] = lambda: (),
        extra_rows: Callable[[], Sequence[tuple[ResourceItem, Lock]]] = lambda: (),
        extension_tiers: Callable[[], Mapping[str, str]] = lambda: {},
    ) -> None:
        if builtin is None:
            from aelix_coding_agent.tools import ALL_TOOL_NAMES  # noqa: PLC0415

            builtin = ALL_TOOL_NAMES
        self._harness = harness
        self._gate = gate
        self.builtin = frozenset(builtin)
        self.grammar = DefaultToolsGrammar(self.builtin)
        self._mcp_statuses = mcp_statuses
        self._extension_errors = extension_errors
        self._extra_rows = extra_rows
        """#25's seam: rows for extensions that are off, with their locks."""
        self._extension_tiers = extension_tiers
        """#25's seam (round-1 verify R6): by ``Extension.name``, the tier of each
        extension whose label another one shares, for its group title."""
        self._order = _builtin_order()

    # --- reading -----------------------------------------------------------

    def _policy(self) -> ToolPolicy:
        policy = getattr(self._harness(), "tool_policy", None)
        return policy if isinstance(policy, ToolPolicy) else ToolPolicy()

    def _tools(self) -> list[Any]:
        """Registered tools, then the excluded ones, each name once."""

        harness = self._harness()
        state = getattr(harness, "state", None)
        out: list[Any] = []
        seen: set[str] = set()
        excluded = [t for t, _r in _excluded_of(harness)]
        for tool in [*(getattr(state, "tools", None) or []), *excluded]:
            name = getattr(tool, "name", None)
            if isinstance(name, str) and name not in seen:
                seen.add(name)
                out.append(tool)
        return out

    def _servers(self) -> list[Any]:
        try:
            return list(self._mcp_statuses() or ())
        except Exception:  # noqa: BLE001 — a render path never raises
            return []

    def items(self) -> list[ResourceItem]:
        harness = self._harness()
        runner = getattr(harness, "extension_runner", None)
        extensions = list(getattr(runner, "extensions", None) or [])
        owner: dict[str, tuple[int, str]] = {}
        tiers = self._tiers()
        for index, ext in enumerate(extensions):
            ext_label = _extension_label(ext)
            tier = tiers.get(str(getattr(ext, "name", "")))
            if tier:
                # Two groups both titled ``EXTENSION  weather.py`` (R6): the
                # tier, as the off placeholder and ``/extension`` show it.
                ext_label = f"{ext_label} ({tier})"
            for name in getattr(ext, "tools", None) or {}:
                if name not in self.builtin and name not in owner:
                    owner[name] = (index, ext_label)
        servers = self._servers()
        server_names = [str(getattr(s, "name", "")) for s in servers]
        rows: list[tuple[tuple[int, int, int], ResourceItem]] = []
        for position, tool in enumerate(self._tools()):
            name = tool.name
            description = _first_line(getattr(tool, "description", "") or "")
            if name in self.builtin:
                rank = self._order.index(name) if name in self._order else len(self._order)
                key, group, label = (0, 0, rank), "builtin", "BUILT-IN"
            elif name in owner:
                index, ext_label = owner[name]
                key, group, label = (1, index, position), f"ext:{index}", f"EXTENSION  {ext_label}"
            else:
                server = next((s for s in server_names if s and name.startswith(f"{s}__")), None)
                if server is not None:
                    key = (2, server_names.index(server), position)
                    group, label = f"mcp:{server}", f"MCP  {server}"
                else:
                    key, group, label = (3, 0, position), "other", "OTHER"
            rows.append(
                (
                    key,
                    ResourceItem(
                        kind="tool",
                        key=name,
                        label=name,
                        identities=(name,),
                        description=description,
                        group=group,
                        group_label=label,
                    ),
                )
            )
        for index, status in enumerate(servers):
            state = getattr(status, "state", "")
            if state not in ("disabled", "failed"):
                continue
            name = str(getattr(status, "name", "?"))
            rows.append(
                (
                    (2, index, 1 << 30),
                    ResourceItem(
                        kind="tool",
                        key=f"mcp:{name}",
                        label=name,
                        description=(
                            "server off: no tools this session"
                            if state == "disabled"
                            else "did not connect at launch: no tools this session"
                        ),
                        group=f"mcp:{name}",
                        group_label=f"MCP  {name}",
                        role="placeholder",
                    ),
                )
            )
        for offset, err in enumerate(self._failed_extensions()):
            label, _reason = err
            rows.append(
                (
                    (1, 1 << 20, offset),
                    ResourceItem(
                        kind="tool",
                        key=f"ext-failed:{label}",
                        label=label,
                        description="failed to load: its tools are not registered",
                        group=f"ext-failed:{label}",
                        group_label=f"EXTENSION  {label}",
                        role="placeholder",
                    ),
                )
            )
        for offset, (item, _lock) in enumerate(self._extra()):
            rows.append(((1, 1 << 21, offset), replace(item, role="placeholder")))
        rows.sort(key=lambda r: r[0])
        return [item for _k, item in rows]

    def _failed_extensions(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        try:
            errors = list(self._extension_errors() or ())
        except Exception:  # noqa: BLE001
            return out
        for err in errors:
            path = str(getattr(err, "path", "") or "?")
            label = os.path.basename(path.rstrip("/" + os.sep)) or path
            out.append((label, str(getattr(err, "error", "") or "unknown error")))
        return out

    def _extra(self) -> list[tuple[ResourceItem, Lock]]:
        try:
            return list(self._extra_rows() or ())
        except Exception:  # noqa: BLE001
            return []

    def _tiers(self) -> Mapping[str, str]:
        try:
            return dict(self._extension_tiers() or {})
        except Exception:  # noqa: BLE001 — a render path never raises
            return {}

    def locks(self) -> dict[str, Lock]:
        policy = self._policy()
        locks: dict[str, Lock] = {}
        for tool in self._tools():
            name = tool.name
            excluded = policy.excluded(name)
            if excluded is not None:
                locks[name] = Lock(False, f"{excluded} (this run only)", tag="excluded", kind="cli")
                continue
            lock = policy.lock(name)
            if lock is not None:
                reason, kind = _lock_text(lock)
                locks[name] = Lock(lock.value, reason, tag=lock.tag, kind=kind)
        # The pointer first (verify V5): the line is cut at the right edge, and
        # a file path or an error is what can be cut, not where to look.
        for status in self._servers():
            state = getattr(status, "state", "")
            name = str(getattr(status, "name", "?"))
            if state == "disabled":
                config = getattr(status, "config", None)
                where = (
                    f'"enabled": false in {config}' if config else '"enabled": false in mcp.json'
                )
                locks[f"mcp:{name}"] = Lock(
                    False, f"see /mcp · {where}", tag="server off", kind="unavailable"
                )
            elif state == "failed":
                locks[f"mcp:{name}"] = Lock(
                    False, "see /mcp · its launch connect failed", tag="failed", kind="unavailable"
                )
        for label, reason in self._failed_extensions():
            locks[f"ext-failed:{label}"] = Lock(
                False,
                f"see /extension · failed to load: {reason}",
                tag="failed",
                kind="unavailable",
            )
        for item, lock in self._extra():
            locks[item.key] = lock
        return locks

    def applied(self) -> set[str]:
        """The tools the applied selection leaves on (locks and exclusions aside)."""

        saved_off = self._policy().saved_off
        state = getattr(self._harness(), "state", None)
        return {t.name for t in getattr(state, "tools", None) or [] if t.name not in saved_off}

    def live(self) -> set[str]:
        """The tools the model can call right now."""

        state = getattr(self._harness(), "state", None)
        tools = [t.name for t in getattr(state, "tools", None) or []]
        active = getattr(state, "active_tool_names", None)
        return set(tools) if active is None else set(active) & set(tools)

    def notices(self) -> list[str]:
        policy = self._policy()
        notes: list[str] = []
        if policy.no_tools:
            source = (
                "The agent profile" if policy.no_tools_tag == "profile" else policy.no_tools_tag
            )
            notes.append(f"{source}: every tool is off for this run.")
            return notes
        if policy.allow is not None:
            source = (
                "The agent profile's tools:" if policy.allow_tag == "profile" else policy.allow_tag
            )
            notes.append(f"{source}: only the tools it names can be on this run.")
        if policy.no_builtin_tools:
            source = (
                "The agent profile" if policy.no_builtin_tag == "profile" else policy.no_builtin_tag
            )
            notes.append(f"{source}: the built-in tools are off for this run.")
        return notes

    def off_reason(self, name: str) -> str | None:
        """Why *name* is not callable now, for a list or an RPC refusal."""

        return self._policy().refusal(name)

    # --- applying ----------------------------------------------------------

    def busy(self) -> bool:
        return harness_busy(self._harness, self._gate)

    async def wait_until_idle(self) -> None:
        await wait_harness_idle(self._harness, self._gate)

    async def apply(self, enabled: set[str]) -> ApplyResult:
        """Apply what changed since the last applied selection — a DELTA (owner decision 2).

        1. The new saved-off set: the unlocked tools *enabled* leaves out; a
           locked or unlisted name keeps what the policy had.
        2. PREPARE and COMMIT in one call: ``set_tool_policy(strict=True)``
           computes the prompt for the candidate set first and raises with
           nothing changed when the rebuilder fails (ADR-0279 D7). Only the
           tools whose saved value moved change; a tool an extension narrowed
           off stays off, and one it never touched keeps its state.
        3. ``resources_update`` with ``resources["tools"]`` the new active set
           and ``previous_resources["tools"]`` the old one. ``set_active_tools``
           itself still emits nothing (``tests/pi_parity``).
        """

        harness = self._harness()
        if harness is None or not callable(getattr(harness, "set_tool_policy", None)):
            return ApplyResult(False, warning="this session has no tool policy to apply")
        policy = self._policy()
        locks = self.locks()
        state = harness.state
        registered = {t.name for t in state.tools}
        unlocked = {name for name in registered if name not in locks}
        saved_off = (policy.saved_off - unlocked) | {n for n in unlocked if n not in enabled}
        newly_on = sorted((policy.saved_off & unlocked) - saved_off)
        newly_off = sorted((saved_off - policy.saved_off) & unlocked)
        if not newly_on and not newly_off:
            return ApplyResult(True)
        before = list(self.live_order())
        try:
            harness.set_tool_policy(
                replace(policy, saved_off=frozenset(saved_off)), turn_on=newly_on, strict=True
            )
        except Exception as exc:  # noqa: BLE001 — nothing applied; said so
            return ApplyResult(
                False, warning=f"not applied: the system prompt could not be rebuilt ({exc})"
            )
        after = list(self.live_order())
        on = tuple(n for n in after if n not in before)
        off = tuple(n for n in before if n not in after)
        warning = None
        resources = dict(getattr(state, "resources", None) or {})
        resources["tools"] = before
        state.resources = resources
        try:
            await harness.set_resources({**resources, "tools": after})
        except Exception as exc:  # noqa: BLE001 — applied; the observer failed
            warning = f"applied, but a resources_update handler failed: {exc}"
        return ApplyResult(True, turned_on=on, turned_off=off, warning=warning)

    def live_order(self) -> list[str]:
        state = getattr(self._harness(), "state", None)
        tools = [t.name for t in getattr(state, "tools", None) or []]
        active = getattr(state, "active_tool_names", None)
        if active is None:
            return tools
        allowed = set(active)
        return [n for n in tools if n in allowed]


@dataclass(frozen=True)
class ToolControl:
    """What a host needs for ``/tool``: the shared service and the tools kind."""

    selection: ResourceSelection
    kind: ToolKind


__all__ = [
    "DefaultToolsGrammar",
    "EditRefused",
    "ToolControl",
    "ToolKind",
    "build_tool_policy",
    "default_tool_value",
    "default_tools_lists",
    "delegated_exclusions",
    "launch_tool_warnings",
    "merge_default_tools",
    "mentioned_names",
    "resolve_saved_off",
]
