"""Agent profiles under the resource selection contract (#795, #796, ADR-0304).

Two things live here:

* :func:`delegation_view` — THE ONE PLACE the three inputs of "may the model
  delegate to this profile" meet: one discovery result (ADR-0196), the applied
  ``agents`` settings lists (ADR-0279 D1's per-item grammar, ADR-0304 D1) and
  the session identity's ``agents:`` list (ADR-0304 D4). The ``agent`` tool's
  roster and every door aelix opens to the model ask it, through
  ``aelix_agents.runtime.delegation_for``; product-core reads it through
  ``AgentProfileService.delegation`` (``/agents list``, ``/agents show``, RPC
  ``agents/list`` and the panel). ``tests/agents/test_delegation_filter_gate_795.py``
  holds the shape: ``delegation_view(`` is called in exactly those two places.
* :class:`AgentKind` — the ``/agents`` panel's :class:`ResourceKind`, and
  :func:`build_agent_control`, the one mode-independent factory (ADR-0304 D14).

Band rule (``tests/agents/test_p2_band_boundaries.py``): no spawn behaviour, no
cap, no consent here. The 24-name cap on the identity list in the empty-roster
sentence lives in ``aelix_agents`` beside ``MAX_ROSTER``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from .resource_filter import (
    Scope,
    ScopeList,
    Target,
    effective,
    forget_resolved_paths,
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
    from aelix_ai.settings import SettingsManager

    from aelix_coding_agent.agents.discovery import ProfileDiscoveryResult
    from aelix_coding_agent.agents.profile import AgentProfile

Verdict = Literal["ok", "none_listed", "unknown", "not_listed", "project", "off"]
"""ADR-0304 D5, in the order they are taken: the refusals no settings change can
cure come first, so the model is never told "only the user can turn it on"
about a profile that turning on would not unlock."""

EmptyReason = Literal[
    "none_found", "none_listed", "listed_found_off", "listed_none_found", "all_off"
]
"""Why :attr:`DelegationView.available` is empty (ADR-0304 D7's five sentences)."""


def profile_tier(profile: AgentProfile) -> str:
    """The resource-filter scope of a profile's tier (ADR-0279 D2's reach rule).

    ``project`` for the trusted project tier, ``bundled`` for the wheel's, and
    ``global`` for the user's (and anything else): a path in the project file
    reaches project profiles only, a path in the global file every other tier.
    """

    if profile.scope == "project":
        return "project"
    if profile.scope == "bundled":
        return "bundled"
    return "global"


def _target(profile: AgentProfile) -> Target:
    return Target((profile.name,), profile.file_path, profile_tier(profile))


@dataclass(frozen=True)
class DelegationView:
    """One evaluation of the three inputs (ADR-0304 D5). Every answer comes from it."""

    discovered: ProfileDiscoveryResult
    """The ONE discovery result the view was taken over, losers included."""
    profiles: tuple[AgentProfile, ...]
    """The winners, sorted by name."""
    states: Mapping[str, tuple[bool, Scope | None]]
    """Winner name → ``(on, the file that decided it)``; ``(True, None)`` by default."""
    identity: str | None
    """The session identity's name, or ``None``."""
    allowlist: tuple[str, ...] | None
    """The identity's ``agents:`` (``None``: no identity, or the key is absent)."""
    available: tuple[AgentProfile, ...]
    """The winners the lists leave on and the identity allows, sorted by name."""
    unknown_listed: tuple[str, ...]
    """The allowlist's names no winner has, in written order."""
    _by_name: Mapping[str, AgentProfile] = field(default_factory=dict, repr=False)

    def winner(self, name: str) -> AgentProfile | None:
        """The discovered profile that holds *name*, or ``None``."""

        return self._by_name.get(name)

    def on(self, name: str) -> bool:
        """Whether the applied lists leave *name* on (``True`` for an unknown name)."""

        return self.states.get(name, (True, None))[0]

    def verdict(self, name: str) -> Verdict:
        """The first of ADR-0304 D5's verdicts that applies to *name*."""

        if self.allowlist == ():
            return "none_listed"
        profile = self._by_name.get(name)
        if profile is None:
            return "unknown"
        if self.allowlist is not None and name not in self.allowlist:
            return "not_listed"
        if profile.scope == "project":
            return "project"
        if not self.on(name):
            return "off"
        return "ok"

    def empty_reason(self) -> EmptyReason | None:
        """Why nothing is :attr:`available`, or ``None`` when something is."""

        if self.available:
            return None
        if not self.profiles:
            return "none_found"
        if self.allowlist == ():
            return "none_listed"
        if self.allowlist is not None:
            found = [n for n in self.allowlist if n in self._by_name]
            return "listed_found_off" if found else "listed_none_found"
        return "all_off"


def delegation_view(
    discovered: ProfileDiscoveryResult,
    lists: SettingsLists | None,
    identity: AgentProfile | None,
) -> DelegationView:
    """Evaluate *discovered* under the applied *lists* and *identity*'s ``agents:``.

    *lists* ``None`` is a host that wired no lists: every profile is on, which
    keeps every embedder and test that predates ADR-0304 unchanged. *identity*
    ``None`` (or one with no ``agents:`` key) allows every profile that is on.
    Neither list widens the other (ADR-0304 D5).

    The resolved-path cache is cleared first, so a symlink re-pointed since the
    last evaluation is judged by its new target (ADR-0279 MF1; Codex 3).
    """

    forget_resolved_paths()
    by_name = {p.name: p for p in discovered.profiles}
    profiles = tuple(sorted(discovered.profiles, key=lambda p: p.name))
    if lists is None:
        states: dict[str, tuple[bool, Scope | None]] = {p.name: (True, None) for p in profiles}
    else:
        states = {
            p.name: effective(_target(p), lists.global_list, lists.project_list) for p in profiles
        }
    allowlist = identity.agents if identity is not None else None
    available = tuple(
        p for p in profiles if states[p.name][0] and (allowlist is None or p.name in allowlist)
    )
    unknown = tuple(n for n in (allowlist or ()) if n not in by_name)
    return DelegationView(
        discovered=discovered,
        profiles=profiles,
        states=states,
        identity=identity.name if identity is not None else None,
        allowlist=allowlist,
        available=available,
        unknown_listed=unknown,
        _by_name=by_name,
    )


def agent_list_problems(lists: SettingsLists, discovered: ProfileDiscoveryResult) -> list[str]:
    """ADR-0304 D13's warnings about the ``agents`` lists, after discovery.

    The lists' own problems (an entry that is not a non-empty string, a
    ``!glob`` past ADR-0279 D1's limits), a plain path entry — which would add
    a profile directory, not supported — with the ``+name`` / ``-name`` hint
    when it names a found profile, and an entry that matches no profile,
    shadowed ones included. The failed-load line is the caller's
    (``cli/entry.py``), which reads the settings manager.
    """

    problems = list(lists.problems)
    names = {p.name for p in discovered.profiles}
    targets = [_target(p) for p in discovered.profiles]
    targets += [_target(s.profile) for s in discovered.shadowed]
    for scope_list in (lists.global_list, lists.project_list):
        if scope_list is None:
            continue
        label = lists.label(scope_list.scope)
        for entry in source_entries(scope_list.entries):
            hint = (
                f'; to turn the profile {entry!r} on or off, write "+{entry}" or "-{entry}"'
                if entry in names
                else ""
            )
            problems.append(
                f'{label}: "agents" entry "{entry}" would add a profile directory, '
                f"which is not supported; it is ignored{hint}"
            )
        for entry in scope_list.entries:
            if split_entry(entry)[0] and not matches_any(
                entry, targets, scope_list.base_dir, list_scope=scope_list.scope
            ):
                problems.append(f'{label}: "agents" entry "{entry}" matches no agent profile')
    return problems


# === The /agents panel (ADR-0304 D10) =======================================

_GROUP_ORDER: dict[str, int] = {"project": 0, "global": 1, "bundled": 2}
_UNTRUSTED_KEY = "untrusted:project"
_FAILED_PREFIX = "failed:"
_TIER_WORD: dict[str, str] = {"project": "project", "global": "global", "bundled": "bundled"}


def _short(path: str, home: str) -> str:
    if home and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


@dataclass(frozen=True)
class AgentDirs:
    """The three directories a panel row can come from."""

    project: str
    user: str
    bundled: str


class AgentKind:
    """The ``/agents`` panel's view of the applied ``agents`` lists (``ResourceKind``).

    Rows: the discovered winners, the profiles that lost a name collision
    (locked ``shadowed``), one locked placeholder per profile file that did not
    load, and, in an untrusted folder, one locked summary row counted from a
    directory listing (no file opened). ``applied()`` is what the applied lists
    leave on; ``live()`` is what the model may start now; ``apply`` replaces
    the applied LISTS (ADR-0304 D3), read synchronously before its first
    ``await``.
    """

    kind: str = "agent"
    settings_key: str = "agents"
    effect: Effect = "next_request"

    def __init__(
        self,
        *,
        holder: dict[str, Any],
        delegation: Callable[[], Any],
        harness: Callable[[], Any],
        read_lists: Callable[[], tuple[SettingsLists, tuple[str, ...]]],
        cwd: str,
        project_trusted: bool,
        dirs: AgentDirs,
        gate: ApplyGate | None = None,
        agent_tool_on: Callable[[], bool] | None = None,
    ) -> None:
        self._holder = holder
        self._delegation = delegation
        self._harness = harness
        self._read_lists = read_lists
        self._cwd = cwd
        self._real_cwd = os.path.realpath(cwd)
        self._project_trusted = project_trusted
        self._dirs = dirs
        self._gate = gate
        self._agent_tool_on = agent_tool_on if agent_tool_on is not None else self._tool_on
        self._home = str(Path.home())

    # --- reading -----------------------------------------------------------

    def _view(self) -> Any:
        return self._delegation()

    def display_path(self, path: str | None) -> str:
        """*path* relative to the project when inside it, else with ``~``."""

        if not path:
            return ""
        for cwd in (self._cwd, self._real_cwd):
            if path.startswith(cwd + os.sep):
                return os.path.relpath(path, cwd)
        return _short(path, self._home)

    def _group(self, tier: str) -> tuple[str, str]:
        if tier == "bundled":
            return "bundled:", "BUNDLED"
        if tier == "project":
            return "project:", f"PROJECT  {self.display_path(self._dirs.project)}/"
        return "global:", f"GLOBAL  {self.display_path(self._dirs.user)}/"

    def _profile_row(self, profile: AgentProfile, key: str) -> ResourceItem:
        tier = profile_tier(profile)
        group, label = self._group(tier)
        return ResourceItem(
            kind="agent",
            key=key,
            label=profile.name,
            description=profile.description,
            group=group,
            group_label=label,
            path=profile.file_path,
            identities=(profile.name,),
            scope=tier,
            source_base=os.path.dirname(profile.file_path),
            location=self.display_path(profile.file_path),
        )

    def _failed_files(self, view: Any) -> dict[str, tuple[str, str]]:
        """``path → (tier, first error)`` for each profile file that did not load (opus 9)."""

        tiers = {
            os.path.realpath(self._dirs.bundled): "bundled",
            os.path.realpath(self._dirs.user): "global",
        }
        if self._project_trusted:
            tiers[os.path.realpath(self._dirs.project)] = "project"
        out: dict[str, tuple[str, str]] = {}
        for diag in view.discovered.diagnostics:
            if diag.type != "error" or not diag.path.endswith(".md") or diag.path in out:
                continue
            tier = tiers.get(os.path.realpath(os.path.dirname(diag.path)))
            if tier is not None:
                out[diag.path] = (tier, diag.message)
        return out

    def items(self) -> list[ResourceItem]:
        view = self._view()
        rows: list[tuple[int, int, ResourceItem]] = []
        for index, profile in enumerate(view.profiles):
            item = self._profile_row(profile, profile.name)
            rows.append((_GROUP_ORDER[item.scope or "global"], index, item))
        base = len(rows)
        for offset, lost in enumerate(view.discovered.shadowed):
            item = self._profile_row(lost.profile, f"{lost.profile.name}@{lost.profile.file_path}")
            rows.append((_GROUP_ORDER[item.scope or "global"], base + offset, item))
        base = len(rows)
        for offset, (path, (tier, _error)) in enumerate(self._failed_files(view).items()):
            group, label = self._group(tier)
            rows.append(
                (
                    _GROUP_ORDER[tier],
                    base + offset,
                    ResourceItem(
                        kind="agent",
                        key=f"{_FAILED_PREFIX}{path}",
                        label=os.path.basename(path),
                        description="did not load: the model is not offered it",
                        group=group,
                        group_label=label,
                        path=path,
                        identities=(),
                        scope=tier,
                        role="placeholder",
                        location=self.display_path(path),
                    ),
                )
            )
        count = self._untrusted_count()
        if count:
            group, label = self._group("project")
            rows.append(
                (
                    _GROUP_ORDER["project"],
                    -1,
                    ResourceItem(
                        kind="agent",
                        key=_UNTRUSTED_KEY,
                        label=f"{count} profile{'s' if count != 1 else ''} not loaded",
                        description=(
                            "This folder is not trusted, so its agent profiles are not loaded."
                        ),
                        group="project-untrusted",
                        group_label=label,
                        path=None,
                        identities=(),
                        scope="project",
                        source_base=self._dirs.project,
                        role="placeholder",
                    ),
                )
            )
        # A group's rows stay together, as SkillKind keeps them (#509).
        first: dict[str, int] = {}
        for _order, index, item in rows:
            first[item.group] = min(first.get(item.group, index), index)
        rows.sort(key=lambda r: (r[0], first[r[2].group], r[1]))
        return [r[2] for r in rows]

    def _untrusted_count(self) -> int:
        if self._project_trusted:
            return 0
        from aelix_coding_agent.agents.discovery import count_untrusted_profiles

        return count_untrusted_profiles(self._dirs.project)

    def locks(self) -> dict[str, Lock]:
        view = self._view()
        locks: dict[str, Lock] = {}
        for lost in view.discovered.shadowed:
            winner = lost.winner
            locks[f"{lost.profile.name}@{lost.profile.file_path}"] = Lock(
                False,
                f"shadowed by the {_TIER_WORD[profile_tier(winner)]} profile of the same "
                f"name ({self.display_path(winner.file_path)})",
                tag="shadowed",
                kind="shadowed",
            )
        for path, (_tier, error) in self._failed_files(view).items():
            locks[f"{_FAILED_PREFIX}{path}"] = Lock(
                False,
                f"see /agents list · did not load: {error}",
                tag="failed",
                # ``status``: the state line is the reason itself, pointer first
                # (ADR-0283 D6), as ADR-0304 D10 words it.
                kind="status",
            )
        locks[_UNTRUSTED_KEY] = Lock(False, "this folder is not trusted; /trust to decide")
        return locks

    def applied(self) -> set[str]:
        view = self._view()
        return {p.name for p in view.profiles if view.on(p.name)}

    def live(self) -> set[str]:
        """The names the model may start now (verdict ``ok``)."""

        view = self._view()
        return {p.name for p in view.profiles if view.verdict(p.name) == "ok"}

    def live_notes(self) -> dict[str, tuple[str, str]]:
        """ADR-0304 D10's "not in use now" words, by row key."""

        view = self._view()
        notes: dict[str, tuple[str, str]] = {}
        for profile in view.profiles:
            verdict = view.verdict(profile.name)
            # Short enough that "Saved on (this project's file) · now off (…)"
            # keeps its reason at 80 columns; the banner names the identity's list.
            if verdict in ("not_listed", "none_listed"):
                notes[profile.name] = ("not listed", f"not listed by {view.identity!r}")
            elif verdict == "project":
                notes[profile.name] = ("/agents run", "only /agents run starts a project profile")
        return notes

    def applied_policy(self) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
        """The applied lists' entries, in :attr:`Snapshot.policy`'s shape (Codex 4)."""

        lists = self._holder.get("result")
        if lists is None:
            return None, None
        return _entries(lists.global_list), _entries(lists.project_list)

    def notices(self) -> list[str]:
        notes: list[str] = []
        if not self._agent_tool_on():
            notes.append("The model has no agent tool in this session; choices still save.")
        view = self._view()
        if view.allowlist == ():
            notes.append(
                f"This session runs as {view.identity!r}, which delegates to no profile "
                "(agents: [])."
            )
        elif view.allowlist is not None:
            notes.append(
                f"This session runs as {view.identity!r}, which may delegate only to: "
                f"{', '.join(view.allowlist)}."
            )
        return notes

    def _tool_on(self) -> bool:
        harness = self._harness()
        getter = getattr(harness, "_action_get_active_tools", None)
        if not callable(getter):
            return True
        try:
            return "agent" in {str(name) for name in cast("Iterable[object]", getter() or ())}
        except Exception:  # noqa: BLE001 — unreadable: say nothing
            return True

    # --- applying ----------------------------------------------------------

    def busy(self) -> bool:
        return harness_busy(self._harness, self._gate)

    async def wait_until_idle(self) -> None:
        await wait_harness_idle(self._harness, self._gate)

    async def apply(self, enabled: set[str]) -> ApplyResult:
        """Replace the applied LISTS with the files' lists, then tell the hook bus.

        ADR-0304 D3: the lists, not the *enabled* names, so a profile file a
        later discovery finds is decided by them. They are read before the
        first ``await``, with none between the caller's snapshot and here, so
        the names they leave on are *enabled* (ADR-0279 D6's "nothing yields").
        An unreadable file applies nothing. ``resources_update`` carries
        ``resources["agents"]`` (the names in use before and after) when they
        differ; a handler that raises is the observer's failure.
        """

        lists, unreadable = self._read_lists()
        if unreadable:
            return ApplyResult(False, warning=f"kept what was applied: {unreadable[0]}")
        before = self.applied()
        self._holder["result"] = lists
        after = self.applied()
        on = tuple(sorted(after - before))
        off = tuple(sorted(before - after))
        if not on and not off:
            return ApplyResult(True)
        harness = self._harness()
        state = getattr(harness, "state", None) if harness is not None else None
        warning = None
        if harness is not None and state is not None:
            resources = dict(getattr(state, "resources", None) or {})
            resources["agents"] = sorted(before)
            state.resources = resources
            try:
                await harness.set_resources({**resources, "agents": sorted(after)})
            except Exception as exc:  # noqa: BLE001 — applied; the observer failed
                warning = f"applied, but a resources_update handler failed: {exc}"
        return ApplyResult(True, turned_on=on, turned_off=off, warning=warning)


def _entries(scope_list: ScopeList | None) -> tuple[str, ...] | None:
    return tuple(scope_list.entries) if scope_list is not None else None


@dataclass(frozen=True)
class AgentControl:
    """What a host needs for ``/agents``: the shared service and the agents kind."""

    selection: ResourceSelection
    kind: AgentKind


def build_agent_control(
    settings_manager: SettingsManager | None,
    *,
    cwd: str,
    project_trusted: bool,
    global_base: str,
    project_base: str,
    agent_dir: str,
    holder: dict[str, Any],
    delegation: Callable[[], Any],
    harness: Callable[[], Any],
    gate: ApplyGate | None = None,
) -> AgentControl:
    """The ``/agents`` panel's selection and kind, built once for every mode (ADR-0304 D14).

    The TUI hands it to the panel; #624 hands the same object to RPC.
    """

    from aelix_coding_agent.agents.discovery import (
        builtin_agents_dir,
        project_agents_dir,
        user_agents_dir,
    )

    selection = ResourceSelection(
        settings_manager,
        global_base=global_base,
        project_base=project_base,
        project_trusted=project_trusted,
    )
    kind = AgentKind(
        holder=holder,
        delegation=delegation,
        harness=harness,
        read_lists=lambda: selection.read_lists("agents"),
        cwd=cwd,
        project_trusted=project_trusted,
        dirs=AgentDirs(
            project=str(project_agents_dir(cwd)),
            user=str(user_agents_dir(agent_dir)),
            bundled=str(builtin_agents_dir()),
        ),
        gate=gate,
    )
    return AgentControl(selection=selection, kind=kind)


__all__ = [
    "AgentControl",
    "AgentDirs",
    "AgentKind",
    "DelegationView",
    "EmptyReason",
    "Verdict",
    "agent_list_problems",
    "build_agent_control",
    "delegation_view",
    "profile_tier",
]
