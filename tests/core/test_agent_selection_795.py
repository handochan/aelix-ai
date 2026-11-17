"""#795, #796 (ADR-0304 D1-D5, D13) — ``core/agent_selection.py`` and the settings key.

``delegation_view`` is the one place the discovery result, the applied
``agents`` lists and the identity's ``agents:`` meet. Its verdict order is
ADR-0304 D5's; ``available`` is what the roster lists. The settings key loads,
round-trips and keeps ADR-0279 D2/D3's scope and trust rules. A busy apply
defers a change of LISTS that moves no row (Codex 4).
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import json
import os
from pathlib import Path
from typing import Any

import pytest
from aelix_ai.settings import SettingsManager
from aelix_ai.settings.types import SETTINGS_PY_TO_JSON, Settings
from aelix_coding_agent.agents import discovery
from aelix_coding_agent.agents.discovery import (
    ProfileDiscoveryResult,
    ShadowedProfile,
    discover_profiles,
)
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.core.agent_selection import (
    AgentDirs,
    AgentKind,
    agent_list_problems,
    delegation_view,
)
from aelix_coding_agent.core.resource_filter import ScopeList
from aelix_coding_agent.core.resource_selection import (
    ApplyGate,
    ResourceSelection,
    SettingsLists,
    settings_lists,
)

pytestmark = pytest.mark.usefixtures("_no_settings_path_override")


@pytest.fixture()
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _p(
    name: str, scope: str = "user", agents: tuple[str, ...] | None = None, path: str = ""
) -> AgentProfile:
    return AgentProfile(
        name=name,
        description=f"{name} agent",
        body="b",
        file_path=path or f"/agents/{scope}/{name}.md",
        scope=scope,  # type: ignore[arg-type]
        agents=agents,
    )


def _found(
    *profiles: AgentProfile, shadowed: tuple[ShadowedProfile, ...] = ()
) -> ProfileDiscoveryResult:
    return ProfileDiscoveryResult(list(profiles), [], list(shadowed))


def _lists(
    global_entries: tuple[str, ...] | None = (), project_entries: tuple[str, ...] | None = None
) -> SettingsLists:
    return SettingsLists(
        ScopeList("global", global_entries, "/g") if global_entries is not None else None,
        ScopeList("project", project_entries, "/p/.aelix") if project_entries is not None else None,
    )


_FOUND = _found(
    _p("explorer", "bundled"),
    _p("general-purpose", "bundled"),
    _p("reviewer", "user"),
    _p("local", "project"),
)


# === delegation_view ========================================================


def test_no_lists_and_no_identity_leave_everything_on() -> None:
    view = delegation_view(_FOUND, None, None)
    assert [p.name for p in view.available] == ["explorer", "general-purpose", "local", "reviewer"]
    assert all(view.states[n] == (True, None) for n in view.states)
    assert view.verdict("explorer") == "ok"
    assert view.verdict("local") == "project"
    assert view.verdict("nope") == "unknown"
    assert view.empty_reason() is None


@pytest.mark.parametrize(
    ("lists", "off"),
    [
        (_lists(), set()),
        (_lists(("-reviewer",)), {"reviewer"}),
        (_lists(("-reviewer",), ("+reviewer",)), set()),
        (_lists(("!gen*",)), {"general-purpose"}),
        (_lists(("-local",)), {"local"}),
        (_lists((), ("-explorer",)), {"explorer"}),
        # A path in the project file reaches project profiles only (ADR-0279 D2).
        (_lists((), ("-/agents/user/reviewer.md",)), set()),
        (_lists(("-/agents/user/reviewer.md",)), {"reviewer"}),
        # A plain entry is not ``agents`` grammar: it turns nothing off.
        (_lists(("reviewer",)), set()),
    ],
)
def test_the_lists_decide_on_and_off(lists: SettingsLists, off: set[str]) -> None:
    view = delegation_view(_FOUND, lists, None)
    assert {n for n, (on, _s) in view.states.items() if not on} == off
    assert {p.name for p in view.available} == {p.name for p in _FOUND.profiles} - off


def test_decided_by_names_the_file() -> None:
    view = delegation_view(_FOUND, _lists(("-reviewer",), ("-explorer",)), None)
    assert view.states["reviewer"] == (False, "global")
    assert view.states["explorer"] == (False, "project")
    assert view.states["local"] == (True, None)


def test_the_verdict_order() -> None:
    lists = _lists(("-reviewer", "-local", "-explorer"))
    # Not listed AND off reads not_listed; a project profile that is off reads project.
    listed = delegation_view(_FOUND, lists, _p("orchestrator", agents=("general-purpose", "local")))
    assert listed.verdict("reviewer") == "not_listed"
    assert listed.verdict("local") == "project"
    assert listed.verdict("general-purpose") == "ok"
    assert listed.verdict("nope") == "unknown"
    plain = delegation_view(_FOUND, lists, _p("orchestrator"))
    assert plain.verdict("reviewer") == "off"
    assert plain.verdict("local") == "project"
    # An empty list comes first, even before an unknown name.
    none = delegation_view(_FOUND, lists, _p("solo", agents=()))
    assert none.verdict("nope") == "none_listed"
    assert none.verdict("explorer") == "none_listed"


def test_neither_list_widens_the_other() -> None:
    view = delegation_view(_FOUND, _lists(("-reviewer",)), _p("o", agents=("reviewer", "explorer")))
    assert [p.name for p in view.available] == ["explorer"]
    assert view.unknown_listed == ()


def test_unknown_listed_keeps_the_written_order() -> None:
    view = delegation_view(_FOUND, None, _p("o", agents=("zed", "explorer", "abe")))
    assert view.unknown_listed == ("zed", "abe")


def test_settings_empty_list_turns_nothing_off_but_an_empty_agents_key_delegates_to_none() -> None:
    settings_empty = delegation_view(_FOUND, _lists(()), None)
    assert len(settings_empty.available) == 4
    identity_empty = delegation_view(_FOUND, None, _p("solo", agents=()))
    assert identity_empty.available == ()


@pytest.mark.parametrize(
    ("found", "lists", "identity", "reason"),
    [
        (_found(), None, None, "none_found"),
        (_FOUND, None, _p("solo", agents=()), "none_listed"),
        (_FOUND, _lists(("-explorer",)), _p("o", agents=("explorer", "zed")), "listed_found_off"),
        (_FOUND, None, _p("o", agents=("zed",)), "listed_none_found"),
        (_FOUND, _lists(("!*",)), None, "all_off"),
        (_FOUND, None, None, None),
    ],
)
def test_empty_reason(
    found: ProfileDiscoveryResult,
    lists: SettingsLists | None,
    identity: AgentProfile | None,
    reason: str | None,
) -> None:
    assert delegation_view(found, lists, identity).empty_reason() == reason


_ENTRIES = ("-explorer", "-reviewer", "-local", "+reviewer", "!gen*", "!*")
_IDENTITIES: tuple[tuple[str, ...] | None, ...] = (
    None,
    (),
    ("explorer",),
    ("reviewer", "local"),
    ("zed", "general-purpose"),
)


def test_property_available_is_exactly_ok_or_an_on_project_profile() -> None:
    """Over every combination of these lists and identities: the roster and the door agree.

    ADR-0304 D5 says ``available`` holds a name exactly when its verdict is
    ``ok`` or ``project``; with the verdict order the ADR fixes (``project``
    before ``off``), an OFF project profile reads ``project`` and is hidden, so
    the property measured here is ``ok``, or ``project`` and on.
    """

    checked = 0
    for size in range(3):
        for global_entries in itertools.combinations(_ENTRIES, size):
            for project_entries in ((), ("-explorer",), ("+reviewer",)):
                for allow in _IDENTITIES:
                    view = delegation_view(
                        _FOUND,
                        _lists(global_entries, project_entries),
                        _p("o", agents=allow) if allow is not None else None,
                    )
                    names = {p.name for p in view.available}
                    for profile in _FOUND.profiles:
                        verdict = view.verdict(profile.name)
                        want = verdict == "ok" or (verdict == "project" and view.on(profile.name))
                        assert (profile.name in names) == want, (
                            global_entries,
                            allow,
                            profile.name,
                        )
                        checked += 1
    assert checked > 1000


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_repointed_symlink_is_judged_by_its_new_target_without_a_reload(tmp_path: Path) -> None:
    real_a, real_b = tmp_path / "a.md", tmp_path / "b.md"
    for path in (real_a, real_b):
        path.write_text("x", encoding="utf-8")
    link = tmp_path / "agents" / "reviewer.md"
    link.parent.mkdir()
    link.symlink_to(real_a)
    found = _found(_p("reviewer", "user", path=str(link)))
    lists = _lists((f"-{real_b}",))
    assert delegation_view(found, lists, None).on("reviewer") is True
    link.unlink()
    link.symlink_to(real_b)
    assert delegation_view(found, lists, None).on("reviewer") is False


# === agent_list_problems (ADR-0304 D13) ====================================


def test_list_problems() -> None:
    shadowed = _found(
        *_FOUND.profiles,
        shadowed=(
            ShadowedProfile(_p("explorer", "bundled", path="/b/explorer.md"), _p("explorer")),
        ),
    )
    lists = SettingsLists(
        ScopeList(
            "global", ("-explorer", "reviewer", "+nobody", "!zz*", "-/b/explorer.md", "./dir"), "/g"
        ),
        None,
        problems=('/g/settings.json: "agents" entry 5 is not a string; it is ignored',),
        labels={"global": "/g/settings.json"},
    )
    problems = agent_list_problems(lists, shadowed)
    assert problems == [
        '/g/settings.json: "agents" entry 5 is not a string; it is ignored',
        '/g/settings.json: "agents" entry "reviewer" would add a profile directory, which is not '
        "supported; it is ignored; to turn the profile 'reviewer' on or off, write \"+reviewer\" "
        'or "-reviewer"',
        '/g/settings.json: "agents" entry "./dir" would add a profile directory, which is not '
        "supported; it is ignored",
        '/g/settings.json: "agents" entry "+nobody" matches no agent profile',
        '/g/settings.json: "agents" entry "!zz*" matches no agent profile',
    ]


def test_settings_lists_names_a_malformed_entry() -> None:
    class _Manager:
        def scope_path(self, scope: str) -> Path:
            return Path(f"/{scope}/settings.json")

        def get_global_settings(self) -> Settings:
            return Settings(agents=["-x", 5, ""])  # type: ignore[list-item]

        def get_project_settings(self) -> Settings:
            return Settings()

    lists = settings_lists(
        _Manager(), "agents", global_base="/g", project_base="/p", project_trusted=False
    )  # type: ignore[arg-type]
    assert lists.global_list is not None and lists.global_list.entries == ("-x",)
    assert len(lists.problems) == 2


# === The settings key =======================================================


def test_the_settings_key_is_mapped_and_counted() -> None:
    assert SETTINGS_PY_TO_JSON["agents"] == "agents"
    fields = dataclasses.fields(Settings)
    assert len(fields) == len(SETTINGS_PY_TO_JSON) == 48
    import aelix_ai.settings.types as types_module

    source = Path(types_module.__file__).read_text(encoding="utf-8")
    assert "# 48 optional fields" in source and "# all 48 are covered" in source


def _manager(
    tmp_path: Path, *, trusted: bool = True, global_json: Any = None, project_json: Any = None
) -> tuple[SettingsManager, Path, Path]:
    agent, work = tmp_path / "agent", tmp_path / "work"
    agent.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    if global_json is not None:
        (agent / "settings.json").write_text(json.dumps(global_json), encoding="utf-8")
    if project_json is not None:
        (work / ".aelix").mkdir(exist_ok=True)
        (work / ".aelix" / "settings.json").write_text(json.dumps(project_json), encoding="utf-8")
    return SettingsManager.create(work, agent, project_trusted=trusted), agent, work


async def test_the_key_loads_and_survives_a_write_of_another_key(tmp_path: Path) -> None:
    manager, agent, _work = _manager(tmp_path, global_json={"agents": ["-general-purpose"]})
    assert manager.get_global_settings().agents == ["-general-purpose"]
    manager.set_hide_thinking_block(True)
    await manager.flush()
    on_disk = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["agents"] == ["-general-purpose"]
    assert on_disk["hideThinkingBlock"] is True


@pytest.mark.parametrize("scope", ["global", "project"])
async def test_edit_list_round_trips_the_key(tmp_path: Path, scope: str) -> None:
    manager, agent, work = _manager(tmp_path, project_json={})
    written = await manager.edit_list(scope, "agents", lambda entries: [*entries, "-reviewer"])  # type: ignore[arg-type]
    assert written == ["-reviewer"]
    path = agent / "settings.json" if scope == "global" else work / ".aelix" / "settings.json"
    assert json.loads(path.read_text(encoding="utf-8"))["agents"] == ["-reviewer"]
    assert manager.read_list(scope, "agents") == ["-reviewer"]  # type: ignore[arg-type]


async def test_an_untrusted_project_file_is_never_opened_and_cannot_be_saved(
    tmp_path: Path,
) -> None:
    from aelix_ai.settings import InMemorySettingsStorage

    class _Spy:
        def __init__(self) -> None:
            self.inner = InMemorySettingsStorage()
            self.opened: list[str] = []

        def with_lock(self, scope: str, fn: Any) -> None:
            self.opened.append(scope)
            self.inner.with_lock(scope, fn)  # type: ignore[arg-type]

        def transact(self, scope: str, fn: Any) -> None:
            self.opened.append(scope)
            self.inner.with_lock(scope, fn)  # type: ignore[arg-type]

    storage = _Spy()
    storage.inner.with_lock("project", lambda _c: json.dumps({"agents": ["-reviewer"]}))
    manager = SettingsManager.from_storage(storage, project_trusted=False)  # type: ignore[arg-type]
    storage.opened.clear()
    selection = ResourceSelection(
        manager,
        global_base=str(tmp_path),
        project_base=str(tmp_path / ".aelix"),
        project_trusted=False,
    )
    lists, unreadable = selection.read_lists("agents")
    assert lists.project_list is None and unreadable == ()
    kind = _kind_over(tmp_path, selection, {"result": lists}, trusted=False)
    snapshot = selection.snapshot(kind)
    assert all(s.saved for s in snapshot.states if s.item.role == "item")
    assert not (await selection.save(kind, "project", {"reviewer": False})).ok
    assert "project" not in storage.opened


def test_a_first_project_save_asks_before_creating_the_file(tmp_path: Path) -> None:
    manager, agent, work = _manager(tmp_path)
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(work / ".aelix"), project_trusted=True
    )
    assert selection.needs_new_project_file("project", {"reviewer": False})
    assert not selection.needs_new_project_file("global", {"reviewer": False})


# === AgentKind and ResourceSelection (ADR-0304 D3, D10, D14) ===============


class _Harness:
    def __init__(self) -> None:
        self.is_idle = True
        self._idle = asyncio.Event()
        self._idle.set()
        self.state = type("State", (), {"resources": {}})()
        self.events: list[dict[str, Any]] = []

    async def wait_for_idle(self) -> None:
        await self._idle.wait()

    def busy(self) -> None:
        self.is_idle = False
        self._idle.clear()

    def idle(self) -> None:
        self.is_idle = True
        self._idle.set()

    async def set_resources(self, resources: dict[str, Any]) -> None:
        self.events.append(dict(resources))

    def _action_get_active_tools(self) -> list[str]:
        return ["read", "agent"]


def _kind_over(
    tmp_path: Path,
    selection: ResourceSelection,
    holder: dict[str, Any],
    *,
    trusted: bool = True,
    harness: _Harness | None = None,
    gate: ApplyGate | None = None,
    identity: AgentProfile | None = None,
    agent_dir: Path | None = None,
    cwd: Path | None = None,
) -> AgentKind:
    agent = agent_dir or tmp_path / "agent"
    work = cwd or tmp_path / "work"
    live = harness or _Harness()

    def view() -> Any:
        found = discover_profiles(work, project_trusted=trusted, agent_dir=str(agent))
        return delegation_view(found, holder.get("result"), identity)

    return AgentKind(
        holder=holder,
        delegation=view,
        harness=lambda: live,
        read_lists=lambda: selection.read_lists("agents"),
        cwd=str(work),
        project_trusted=trusted,
        dirs=AgentDirs(
            project=str(work / ".aelix" / "agents"),
            user=str(agent / "agents"),
            bundled=str(discovery.builtin_agents_dir()),
        ),
        gate=gate,
    )


@pytest.fixture()
def scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr(discovery, "builtin_agents_dir", lambda: bundled)
    users = tmp_path / "agent" / "agents"
    users.mkdir(parents=True)
    (tmp_path / "work").mkdir()
    (users / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: r\n---\n\nb\n", encoding="utf-8"
    )
    return tmp_path


def _write_global(tmp_path: Path, entries: list[str]) -> None:
    (tmp_path / "agent" / "settings.json").write_text(
        json.dumps({"agents": entries}), encoding="utf-8"
    )


async def test_a_busy_apply_defers_a_change_of_lists_that_moves_no_row(scene: Path) -> None:
    _write_global(scene, ["-reviewer"])
    manager = SettingsManager.create(scene / "work", scene / "agent", project_trusted=True)
    selection = ResourceSelection(
        manager,
        global_base=str(scene / "agent"),
        project_base=str(scene / "work" / ".aelix"),
        project_trusted=True,
    )
    lists, _ = selection.read_lists("agents")
    holder: dict[str, Any] = {"result": lists}
    harness = _Harness()
    kind = _kind_over(scene, selection, holder, harness=harness)
    _write_global(scene, ["!rev*"])
    harness.busy()
    results: list[Any] = []
    result = await selection.apply(kind, on_deferred=results.append)
    assert result.deferred and not result.applied
    assert holder["result"].global_list.entries == ("-reviewer",)
    harness.idle()
    for _ in range(50):
        if results:
            break
        await asyncio.sleep(0.01)
    assert results and results[0].applied
    assert holder["result"].global_list.entries == ("!rev*",)
    (scene / "agent" / "agents" / "review-new.md").write_text(
        "---\nname: review-new\ndescription: n\n---\n\nb\n", encoding="utf-8"
    )
    assert kind._view().on("review-new") is False


async def test_a_held_gate_defers_a_lists_only_change_too(scene: Path) -> None:
    _write_global(scene, ["-reviewer"])
    manager = SettingsManager.create(scene / "work", scene / "agent", project_trusted=True)
    selection = ResourceSelection(
        manager,
        global_base=str(scene / "agent"),
        project_base=str(scene / "work" / ".aelix"),
        project_trusted=True,
    )
    holder: dict[str, Any] = {"result": selection.read_lists("agents")[0]}
    gate = ApplyGate()
    kind = _kind_over(scene, selection, holder, gate=gate)
    _write_global(scene, ["!rev*"])
    results: list[Any] = []
    with gate:
        result = await selection.apply(kind, on_deferred=results.append)
        assert result.deferred
        assert holder["result"].global_list.entries == ("-reviewer",)
    for _ in range(50):
        if results:
            break
        await asyncio.sleep(0.01)
    assert holder["result"].global_list.entries == ("!rev*",)


async def test_a_busy_apply_with_the_same_lists_defers_nothing(scene: Path) -> None:
    _write_global(scene, ["-reviewer"])
    manager = SettingsManager.create(scene / "work", scene / "agent", project_trusted=True)
    selection = ResourceSelection(
        manager,
        global_base=str(scene / "agent"),
        project_base=str(scene / "work" / ".aelix"),
        project_trusted=True,
    )
    holder: dict[str, Any] = {"result": selection.read_lists("agents")[0]}
    harness = _Harness()
    kind = _kind_over(scene, selection, holder, harness=harness)
    harness.busy()
    result = await selection.apply(kind)
    assert result.applied and not result.deferred


async def test_apply_commits_the_files_lists_and_they_evaluate_to_what_it_was_passed(
    scene: Path,
) -> None:
    (scene / "agent" / "agents" / "explorer.md").write_text(
        "---\nname: explorer\ndescription: e\n---\n\nb\n", encoding="utf-8"
    )
    manager = SettingsManager.create(scene / "work", scene / "agent", project_trusted=True)
    selection = ResourceSelection(
        manager,
        global_base=str(scene / "agent"),
        project_base=str(scene / "work" / ".aelix"),
        project_trusted=True,
    )
    holder: dict[str, Any] = {"result": selection.read_lists("agents")[0]}
    harness = _Harness()
    kind = _kind_over(scene, selection, holder, harness=harness)
    _write_global(scene, ["-reviewer"])
    snapshot = selection.snapshot(kind)
    enabled = selection.enabled(snapshot)
    result = await kind.apply(enabled)
    assert result.applied and result.turned_off == ("reviewer",)
    assert kind.applied() == enabled == {"explorer"}
    assert holder["result"].global_list.entries == ("-reviewer",)
    assert harness.events == [{"agents": ["explorer"]}]
    assert harness.state.resources["agents"] == ["explorer", "reviewer"]
    # Verifier V22: an apply that moves nothing says nothing on the hook bus.
    again = await kind.apply(enabled)
    assert again.applied and again.turned_on == () and again.turned_off == ()
    assert harness.events == [{"agents": ["explorer"]}]


async def test_apply_from_an_unreadable_file_keeps_what_was_applied(scene: Path) -> None:
    manager = SettingsManager.create(scene / "work", scene / "agent", project_trusted=True)
    selection = ResourceSelection(
        manager,
        global_base=str(scene / "agent"),
        project_base=str(scene / "work" / ".aelix"),
        project_trusted=True,
    )
    before = selection.read_lists("agents")[0]
    holder: dict[str, Any] = {"result": before}
    kind = _kind_over(scene, selection, holder)
    (scene / "agent" / "settings.json").write_text("{not json", encoding="utf-8")
    result = await kind.apply(set())
    assert not result.applied and (result.warning or "").startswith("kept what was applied: ")
    assert holder["result"] is before


def test_a_skill_kind_without_applied_policy_keeps_the_row_only_check() -> None:
    from aelix_coding_agent.core.skill_selection import SkillKind

    assert not hasattr(SkillKind, "applied_policy")


def test_every_row_state_serializes(scene: Path) -> None:
    manager = SettingsManager.create(scene / "work", scene / "agent", project_trusted=True)
    selection = ResourceSelection(
        manager,
        global_base=str(scene / "agent"),
        project_base=str(scene / "work" / ".aelix"),
        project_trusted=True,
    )
    holder: dict[str, Any] = {"result": selection.read_lists("agents")[0]}
    kind = _kind_over(scene, selection, holder, identity=_p("o", agents=("explorer",)))
    snapshot = selection.snapshot(kind)
    assert snapshot.states
    for state in snapshot.states:
        json.dumps(dataclasses.asdict(state))
    reviewer = next(s for s in snapshot.states if s.item.key == "reviewer")
    assert reviewer.live is False and reviewer.live_note is not None
    assert reviewer.live_note[0] == "not listed"
    assert snapshot.policy == ((), ())  # both files read; neither holds the key
