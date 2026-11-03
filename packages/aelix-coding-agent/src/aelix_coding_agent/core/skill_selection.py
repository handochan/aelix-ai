"""Skills under the resource selection contract (#511, ADR-0279).

Three things live here:

* :func:`load_skill_catalog` — the one scan: every source directory in
  precedence order, same-name skills deduplicated (``--skill`` > project >
  global > bundled; the losers are kept as *shadowed*), the settings lists
  evaluated, and the enabled subset computed.
* :func:`effective_skills` — THE choke point. Every ``set_skills`` call, the
  first build's ``<available_skills>`` and the rebuild hook read the enabled
  list through it, so an off skill cannot reach the model by a path that
  forgot the filter (``tests/core/test_skill_selection_511.py`` holds an AST
  gate over ``packages/``).
* :class:`SkillKind` — the ``/skill`` panel's :class:`ResourceKind`.

The holder is the ``{"result": ...}`` dict ADR-0196 introduced. Its result is
a :class:`SkillCatalog` — a :class:`LoadSkillsResult` whose ``skills`` are the
ENABLED ones — so a reader of ``holder["result"].skills`` that predates this
module gets the filtered list rather than a leak. A plain ``LoadSkillsResult``
(tests, embedders) still works and means "everything is on".
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from aelix_agent_core.harness.skills import LoadSkillsResult, Skill, load_skills

from .resource_filter import (
    ScopeList,
    Target,
    effective,
    forget_resolved_paths,
    matches_any,
    source_entries,
    source_path,
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
from .skill_dirs import (
    AGENTS_DIR_NAME,
    parent_relative,
    path_key,
    project_agents_skill_dirs,
    skipped_entry,
)

if TYPE_CHECKING:
    pass

SkillTier = Literal["cli", "project", "global", "bundled"]
_TIER_ORDER: dict[str, int] = {"cli": 0, "project": 1, "global": 2, "bundled": 3}
_TIER_WORD: dict[str, str] = {
    "cli": "--skill",
    "project": "project",
    "global": "global",
    "bundled": "bundled",
}
# The panel lists groups in the layout-A order the owner approved; precedence
# (``_TIER_ORDER``) decides which same-name skill wins, not where it is drawn.
_GROUP_ORDER: dict[str, int] = {"project": 0, "global": 1, "bundled": 2, "cli": 3}
_UNTRUSTED_KEY = "untrusted:project"


@dataclass(frozen=True)
class SkillSource:
    tier: SkillTier
    directory: str
    agents: bool = False
    """An Agent Skills directory (``~/.agents/skills``, ``.agents/skills``, #509):
    scanned by pi's rule for them, ``load_skills(agents=True)``."""


@dataclass(frozen=True)
class ShadowedSkill:
    skill: Skill
    source: SkillSource
    winner: Skill
    winner_source: SkillSource


@dataclass(frozen=True)
class SkillCatalog(LoadSkillsResult):
    """What was found and what is on. ``skills`` (inherited) is the ENABLED list."""

    discovered: list[Skill] = field(default_factory=list)
    """Every winner, on or off, in precedence order."""
    shadowed: list[ShadowedSkill] = field(default_factory=list)
    origins: dict[str, SkillSource] = field(default_factory=dict)
    """``file_path`` → the source it came from, for winners and losers alike."""
    problems: list[str] = field(default_factory=list)
    """Settings problems and entries that match nothing — warned once per launch."""

    def with_enabled(self, names: set[str]) -> SkillCatalog:
        return dataclasses.replace(self, skills=[s for s in self.discovered if s.name in names])


def effective_skills(holder: dict[str, Any]) -> list[Skill]:
    """The enabled skills — the ONLY list ``set_skills`` and the prompt catalog may take.

    ADR-0279 D6. The factory's first build and every rebuild, the rebuild
    hook's ``skills_provider``, ``/agents use`` (and its rollback), ``/reload``
    and the ``/skill`` panel's apply all read it here.
    """

    return list(holder["result"].skills)


def _target(skill: Skill, source: SkillSource | None) -> Target:
    return Target((skill.name,), skill.file_path, source.tier if source is not None else None)


def load_skill_catalog(
    sources: Sequence[SkillSource],
    lists: SettingsLists,
    *,
    settings_sources: bool = True,
    check_entries: bool = True,
) -> SkillCatalog:
    """Scan *sources* (plus the plain path entries of *lists*), dedupe, filter.

    *settings_sources* is false under ``--no-skills``: a settings path is a
    discovered source, not one named on the command line. *check_entries*
    turns on the "matches nothing" warnings, which are noise when nothing was
    scanned.
    """

    forget_resolved_paths()  # a symlink may point elsewhere since the last load (MF1)
    ordered = list(sources)
    problems = list(lists.problems)
    if settings_sources:
        tiers: tuple[tuple[ScopeList | None, SkillTier], ...] = (
            (lists.project_list, "project"),
            (lists.global_list, "global"),
        )
        for scope_list, tier in tiers:
            if scope_list is None:
                continue
            for entry in source_entries(scope_list.entries):
                directory = source_path(entry, scope_list.base_dir)
                if not os.path.isdir(directory):
                    problems.append(
                        f'{lists.label(scope_list.scope)}: "skills" entry "{entry}" '
                        "is not a directory; it is ignored"
                    )
                    continue
                ordered.append(SkillSource(tier, directory))
    ordered.sort(key=lambda s: _TIER_ORDER[s.tier])

    diagnostics = []
    seen_diagnostics: set[tuple[str, str, str]] = set()
    winners: list[Skill] = []
    winner_source: dict[str, SkillSource] = {}
    by_name: dict[str, Skill] = {}
    shadowed: list[ShadowedSkill] = []
    origins: dict[str, SkillSource] = {}
    seen_files: set[tuple[int, int] | str] = set()
    seen_dirs: set[tuple[tuple[int, int] | str, bool]] = set()
    for source in ordered:
        # One scan per directory AND rule (#509 review F2). A directory named
        # by a settings path or ``--skill`` is read by aelix's rule (root ``.md``
        # files are skills) and the same directory found as ``.agents/skills``
        # by the Agent Skills rule (grouped ``.md`` files are); keyed by the
        # directory alone, whichever came first erased what only the other
        # finds. A file both find is still taken once, below.
        scan = (path_key(source.directory), source.agents)
        if scan in seen_dirs:
            continue
        seen_dirs.add(scan)
        result = load_skills(source.directory, agents=source.agents)
        # Both rules read a ``SKILL.md`` alike, so the second scan of one
        # directory repeated the first one's warnings at launch; each is said
        # once (#509 review R1).
        for diag in result.diagnostics:
            said = (diag.code, diag.path, diag.message)
            if said not in seen_diagnostics:
                seen_diagnostics.add(said)
                diagnostics.append(diag)
        for skill in result.skills:
            real = path_key(skill.file_path)
            if real in seen_files:
                continue  # the same file reached twice (``--skill`` into a scanned dir)
            seen_files.add(real)
            origins[skill.file_path] = source
            winner = by_name.get(skill.name)
            if winner is not None:
                shadowed.append(ShadowedSkill(skill, source, winner, winner_source[winner.name]))
                continue
            by_name[skill.name] = skill
            winner_source[skill.name] = source
            winners.append(skill)

    enabled: set[str] = set()
    for skill in winners:
        if origins[skill.file_path].tier == "cli":
            enabled.add(skill.name)
            continue
        on, _scope = effective(
            _target(skill, origins[skill.file_path]), lists.global_list, lists.project_list
        )
        if on:
            enabled.add(skill.name)

    if check_entries:
        targets = [_target(s, origins[s.file_path]) for s in winners]
        targets += [_target(s.skill, s.source) for s in shadowed]
        for scope_list in (lists.global_list, lists.project_list):
            if scope_list is None:
                continue
            for entry in scope_list.entries:
                if split_entry(entry)[0] and not matches_any(
                    entry, targets, scope_list.base_dir, list_scope=scope_list.scope
                ):
                    problems.append(
                        f'{lists.label(scope_list.scope)}: "skills" entry "{entry}" matches no skill'
                    )

    return SkillCatalog(
        skills=[s for s in winners if s.name in enabled],
        diagnostics=diagnostics,
        discovered=winners,
        shadowed=shadowed,
        origins=origins,
        problems=problems,
    )


def count_untrusted_skills(directory: str, *, agents: bool = False) -> int:
    """How many skills an untrusted project directory holds, from a listing alone (ADR-0279 D3).

    No file is opened. The loader's own rules decide, as far as a listing shows
    them (``load_skills``), so the count is never more than trusting the
    folder would load (#509 review F6): a ``SKILL.md`` in *directory* itself is
    its one skill (the loader reads no further), hidden entries and
    ``node_modules`` are skipped (``skill_dirs.skipped_entry``), a
    subdirectory with a ``SKILL.md`` counts, and so does a root ``.md`` file —
    except in an Agent Skills directory (*agents*, #509), whose root ``.md``
    files are not skills. One level only, and ignore files are not read: a
    skill deeper down or in a grouped ``.md`` is not counted, and one an ignore
    file excludes is (ADR-0281 Limits).
    """

    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return 0
    if "SKILL.md" in names and os.path.isfile(os.path.join(directory, "SKILL.md")):
        return 1
    count = 0
    for name in names:
        if skipped_entry(name):
            continue
        path = os.path.join(directory, name)
        if os.path.isdir(path):
            count += os.path.isfile(os.path.join(path, "SKILL.md"))
        elif name.endswith(".md") and not agents and os.path.isfile(path):
            count += 1
    return count


def _short(path: str, home: str) -> str:
    if home and (path == home or path.startswith(home + os.sep)):
        return "~" + path[len(home) :]
    return path


class SkillKind:
    """The ``/skill`` panel's view of the skills holder (``ResourceKind``)."""

    kind: str = "skill"
    settings_key: str = "skills"
    effect: Effect = "next_request"

    def __init__(
        self,
        *,
        holder: dict[str, Any],
        harness: Callable[[], Any],
        cwd: str,
        project_trusted: bool,
        project_skills_dir: str,
        no_skills: Callable[[], bool] = lambda: False,
        skill_commands_enabled: Callable[[], bool] = lambda: True,
        gate: ApplyGate | None = None,
    ) -> None:
        self._holder = holder
        self._gate = gate
        self._harness = harness
        self._cwd = cwd
        self._project_trusted = project_trusted
        self._project_skills_dir = project_skills_dir
        self._no_skills = no_skills
        self._skill_commands_enabled = skill_commands_enabled
        self._home = str(Path.home())
        # A loaded skill's path may be the resolved one (macOS ``/tmp`` is
        # ``/private/tmp``); either spelling of the project counts as inside it.
        self._real_cwd = os.path.realpath(cwd)

    # --- reading -----------------------------------------------------------

    def _catalog(self) -> SkillCatalog:
        result = self._holder["result"]
        if isinstance(result, SkillCatalog):
            return result
        return SkillCatalog(skills=list(result.skills), discovered=list(result.skills))

    def _display_dir(self, directory: str) -> str:
        return self.display_path(directory) + "/"

    def _group(self, source: SkillSource | None) -> tuple[str, str]:
        if source is None:
            return "global:", "GLOBAL"
        if source.tier == "bundled":
            return "bundled:", "BUNDLED"
        if source.tier == "cli":
            return "cli:", "THIS RUN  --skill"
        word = "PROJECT" if source.tier == "project" else "GLOBAL"
        return f"{source.tier}:{source.directory}", f"{word}  {self._display_dir(source.directory)}"

    def display_path(self, path: str | None) -> str:
        """*path* relative to the project when inside it, else with ``~`` for the home directory.

        A parent's ``.agents/skills`` (#509) is relative too, as the trust
        question names it — ``../../.agents/skills/`` — and bounded however
        deep the cwd is (``skill_dirs.parent_relative``). By full path its
        group header was cut from the right at 80 columns, and every parent
        shares the long prefix that was kept, so each read the same (verify
        V1). The user's own ``~/.agents/skills`` keeps its ``~``: it is GLOBAL,
        not a parent's.
        """

        if not path:
            return ""
        for cwd in (self._cwd, self._real_cwd):
            if path.startswith(cwd + os.sep):
                return os.path.relpath(path, cwd)
        short = _short(path, self._home)
        if not short.startswith(os.path.join("~", AGENTS_DIR_NAME, "")):
            for cwd in (self._cwd, self._real_cwd):
                relative = parent_relative(path, cwd)
                if relative is not None:
                    return relative
        return short

    def items(self) -> list[ResourceItem]:
        catalog = self._catalog()
        rows: list[tuple[int, int, ResourceItem]] = []
        for index, skill in enumerate(catalog.discovered):
            source = catalog.origins.get(skill.file_path)
            group, label = self._group(source)
            order = _GROUP_ORDER[source.tier] if source is not None else 1
            rows.append(
                (
                    order,
                    index,
                    ResourceItem(
                        kind="skill",
                        key=skill.name,
                        label=skill.name,
                        description=skill.description,
                        group=group,
                        group_label=label,
                        path=skill.file_path,
                        identities=(skill.name,),
                        invocation=skill.name,
                        scope=source.tier if source is not None else None,
                        source_base=source.directory if source is not None else None,
                        location=self.display_path(skill.file_path),
                    ),
                )
            )
        base = len(catalog.discovered)
        for offset, lost in enumerate(catalog.shadowed):
            group, label = self._group(lost.source)
            rows.append(
                (
                    _GROUP_ORDER[lost.source.tier],
                    base + offset,
                    ResourceItem(
                        kind="skill",
                        key=f"{lost.skill.name}@{lost.skill.file_path}",
                        label=lost.skill.name,
                        description=lost.skill.description,
                        group=group,
                        group_label=label,
                        path=lost.skill.file_path,
                        identities=(lost.skill.name,),
                        # Shadowed: ``/skill:<name>`` expands the winner, never this.
                        invocation="",
                        scope=lost.source.tier,
                        source_base=lost.source.directory,
                        location=self.display_path(lost.skill.file_path),
                    ),
                )
            )
        untrusted = self._untrusted_dirs()
        for position, (key, directory, agents) in enumerate(untrusted):
            count = count_untrusted_skills(directory, agents=agents)
            if not count:
                continue
            rows.append(
                (
                    _GROUP_ORDER["project"],
                    position - len(untrusted),
                    ResourceItem(
                        kind="skill",
                        key=key,
                        label=f"{count} skill{'s' if count != 1 else ''} not loaded",
                        description="This folder is not trusted, so its skills are not read.",
                        group=f"project-untrusted:{directory}" if agents else "project-untrusted",
                        group_label=f"PROJECT  {self._display_dir(directory)}",
                        path=None,
                        identities=(),
                        scope="project",
                        source_base=directory,
                    ),
                )
            )
        # A group's rows stay together (#509): a shadowed row sorts after every
        # winner, so ordering by index alone drew a second header for its
        # directory below the next one's — with ``.agents/skills`` a project or
        # global tier has several directories.
        first: dict[str, int] = {}
        for _order, index, item in rows:
            first[item.group] = min(first.get(item.group, index), index)
        rows.sort(key=lambda r: (r[0], first[r[2].group], r[1]))
        return [r[2] for r in rows]

    def _untrusted_dirs(self) -> list[tuple[str, str, bool]]:
        """``(row key, directory, agents)`` for each untrusted project directory (ADR-0279 D3).

        ``.aelix/skills``, then each ``.agents/skills`` the scan would read were
        the folder trusted (#509), nearest first. None when trusted, or under
        ``--no-skills`` (nothing but ``--skill`` is scanned then).
        """

        if self._project_trusted or self._no_skills():
            return []
        out = [(_UNTRUSTED_KEY, self._project_skills_dir, False)]
        out += [
            (f"{_UNTRUSTED_KEY}:{d}", str(d), True) for d in project_agents_skill_dirs(self._cwd)
        ]
        return out

    def locks(self) -> dict[str, Lock]:
        catalog = self._catalog()
        locks: dict[str, Lock] = {}
        for skill in catalog.discovered:
            source = catalog.origins.get(skill.file_path)
            if source is not None and source.tier == "cli":
                locks[skill.name] = Lock(True, "forced on by --skill or the agent profile")
        for lost in catalog.shadowed:
            winner = _TIER_WORD[lost.winner_source.tier]
            locks[f"{lost.skill.name}@{lost.skill.file_path}"] = Lock(
                False,
                f"shadowed by the {winner} skill of the same name "
                f"({self.display_path(lost.winner.file_path)})",
                tag="shadowed",
                kind="shadowed",
            )
        locks[_UNTRUSTED_KEY] = Lock(False, "this folder is not trusted; /trust to decide")
        for key, _directory, _agents in self._untrusted_dirs():
            locks[key] = locks[_UNTRUSTED_KEY]
        return locks

    def applied(self) -> set[str]:
        harness = self._harness()
        names = {getattr(s, "name", None) for s in (getattr(harness, "skills", None) or [])}
        return {n for n in names if isinstance(n, str)}

    def notices(self) -> list[str]:
        notes: list[str] = []
        if self._no_skills():
            notes.append("--no-skills: only skills named on the command line are loaded this run.")
        if not self._read_tool_on():
            notes.append(
                "The read tool is off, so the model is shown no skill list; choices still save."
            )
        if not self._skill_commands_enabled():
            notes.append("/skill:<name> is turned off in settings (enableSkillCommands).")
        else:
            # #508 — the panel is where a user looks for their skills, and it
            # never said how to run one. A banner is one row cut to the panel:
            # this one fits the 49 cells it has at 50 columns (verify V3).
            notes.append("Run one with /skill:<name>; /skill: lists them.")
        return notes

    def source_word(self, skill: object) -> str | None:
        """Where an enabled *skill* came from — ``project``, ``global``, ``bundled``
        or ``--skill`` — for the ``/`` menu's tag (#508); ``None`` when unknown."""

        source = self._catalog().origins.get(getattr(skill, "file_path", None) or "")
        return _TIER_WORD[source.tier] if source is not None else None

    def _read_tool_on(self) -> bool:
        harness = self._harness()
        state = getattr(harness, "state", None)
        if state is None:
            return True
        active = getattr(state, "active_tool_names", None)
        names = active if active is not None else [t.name for t in getattr(state, "tools", [])]
        return "read" in names

    def off_reason(self, name: str) -> str | None:
        """Why ``/skill:<name>`` does not expand, when the skill exists but is off."""

        catalog = self._catalog()
        enabled = {s.name for s in catalog.skills}
        if name in enabled:
            return None
        if any(s.name == name for s in catalog.discovered):
            return f"{name} is off — /skill to turn it on"
        return None

    # --- applying ----------------------------------------------------------

    def busy(self) -> bool:
        """A turn is running, or a harness is being built (:class:`ApplyGate`)."""

        return harness_busy(self._harness, self._gate)

    async def wait_until_idle(self) -> None:
        """Until the LIVE harness is idle — or has been replaced (C8).

        ``/reload``, ``/new`` and ``/resume`` swap the harness; a waiter parked
        on the old one's idle event returns as soon as the swap is seen, and
        :meth:`ResourceSelection.apply`'s loop re-checks the new one.
        """

        await wait_harness_idle(self._harness, self._gate)

    async def apply(self, enabled: set[str]) -> ApplyResult:
        """Prepare, then commit, then tell the hook bus (ADR-0279 D6, D7; C9, C10).

        1. PREPARE: the candidate catalog goes into the holder and the harness
           computes the prompt it would install (``prepare_system_prompt``,
           which raises where ``rebuild_system_prompt`` would swallow). If that
           fails, the holder is put back and NOTHING is applied — the result
           says so rather than reporting a change the prompt does not show.
        2. COMMIT, with no ``await`` since the caller's idle check: the
           harness's skills and its prompt, from the same applied snapshot the
           rebuild hook reads, so a later tool rebuild describes the same list.
        3. ``resources_update`` with ``previous_resources["skills"]`` the names
           the session ran with and ``resources["skills"]`` the new ones, every
           other bucket kept. A handler that raises leaves the change applied
           and is reported as the observer's failure, not the apply's.
        """

        harness = self._harness()
        catalog = self._catalog()
        previous = self._holder["result"]
        before = [s.name for s in (getattr(harness, "skills", None) or [])]
        candidate = catalog.with_enabled(enabled)
        self._holder["result"] = candidate
        if harness is None:
            return ApplyResult(True)
        prepare = getattr(harness, "prepare_system_prompt", None)
        try:
            prompt = prepare() if callable(prepare) else None
        except Exception as exc:  # noqa: BLE001 — nothing applied; said so
            self._holder["result"] = previous
            return ApplyResult(
                False, warning=f"not applied: the system prompt could not be rebuilt ({exc})"
            )
        harness.set_skills(effective_skills(self._holder))
        state = getattr(harness, "state", None)
        if prompt is not None and state is not None:
            state.system_prompt = prompt
        elif not callable(prepare):
            harness.rebuild_system_prompt()
        after = [s.name for s in harness.skills]
        on = tuple(n for n in after if n not in before)
        off = tuple(n for n in before if n not in after)
        if not on and not off:
            return ApplyResult(True)
        warning = None
        if state is not None:
            resources = dict(getattr(state, "resources", None) or {})
            # The event's ``previous_resources`` is the state's dict before the
            # call; give it the list the session actually ran with until now.
            resources["skills"] = before
            state.resources = resources
            try:
                await harness.set_resources({**resources, "skills": after})
            except Exception as exc:  # noqa: BLE001 — applied; the observer failed
                warning = f"applied, but a resources_update handler failed: {exc}"
        return ApplyResult(True, turned_on=on, turned_off=off, warning=warning)


@dataclass(frozen=True)
class SkillControl:
    """What a host needs for ``/skill``: the shared service and the skills kind."""

    selection: ResourceSelection
    kind: SkillKind


__all__ = [
    "ApplyGate",
    "SkillCatalog",
    "SkillControl",
    "SkillKind",
    "SkillSource",
    "ShadowedSkill",
    "count_untrusted_skills",
    "effective_skills",
    "load_skill_catalog",
]
