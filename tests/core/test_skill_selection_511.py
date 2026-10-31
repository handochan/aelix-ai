"""#511 (ADR-0279 D6, D7, D9, D13) — the skill catalog, the choke point and the apply.

The prompt assertions read ``AgentHarness(options).state.system_prompt``, the
real join (``tests/cli/test_skills_in_system_prompt.py`` says why nothing
weaker counts), with markers no passing build produces by accident.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness
from aelix_agent_core.harness.skills import load_skills
from aelix_agent_core.session.memory_storage import MemorySessionStorage
from aelix_agent_core.session.session import Session
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.cli.entry import (
    _build_harness_options,
    _load_skill_catalog,
    _resolve_skill_dirs,
)
from aelix_coding_agent.core.resource_filter import ScopeList
from aelix_coding_agent.core.resource_selection import ResourceSelection, SettingsLists
from aelix_coding_agent.core.skill_selection import (
    SkillCatalog,
    SkillKind,
    SkillSource,
    count_untrusted_skills,
    effective_skills,
    load_skill_catalog,
)

pytestmark = pytest.mark.usefixtures("own_agent_dir")

REPO = Path(__file__).resolve().parents[2]


def _skill(root: Path, name: str, description: str | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description or f'MARK-{name} does a thing.'}\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def _lists(
    global_entries: tuple[str, ...] = (), project: tuple[str, ...] | None = None, base: str = "/g"
) -> SettingsLists:
    return SettingsLists(
        ScopeList("global", global_entries, base),
        ScopeList("project", project, "/p") if project is not None else None,
    )


# --- D9: one name, one skill -----------------------------------------------


def test_the_first_tier_with_a_name_wins_and_the_rest_are_shadowed(tmp_path: Path) -> None:
    cli, project, global_, bundled = (tmp_path / d for d in ("cli", "project", "global", "bundled"))
    _skill(bundled, "writing-skills")
    user = _skill(global_, "writing-skills")
    _skill(project, "x")
    _skill(global_, "x")
    forced = _skill(cli, "x")
    sources = [
        SkillSource("bundled", str(bundled)),  # order given does not matter
        SkillSource("global", str(global_)),
        SkillSource("project", str(project)),
        SkillSource("cli", str(cli)),
    ]
    catalog = load_skill_catalog(sources, SettingsLists())
    by_name = {s.name: s.file_path for s in catalog.discovered}
    assert by_name == {"x": str(forced), "writing-skills": str(user)}
    lost = sorted((s.skill.name, s.source.tier, s.winner_source.tier) for s in catalog.shadowed)
    assert lost == [
        ("writing-skills", "bundled", "global"),
        ("x", "global", "cli"),
        ("x", "project", "cli"),
    ]


def test_the_same_file_reached_twice_loads_once(tmp_path: Path) -> None:
    global_ = tmp_path / "global"
    _skill(global_, "pdf")
    sources = [SkillSource("cli", str(global_ / "pdf")), SkillSource("global", str(global_))]
    catalog = load_skill_catalog(sources, SettingsLists())
    assert [s.name for s in catalog.discovered] == ["pdf"]
    assert catalog.shadowed == []
    assert catalog.origins[catalog.discovered[0].file_path].tier == "cli"


def test_the_resolver_scans_in_precedence_order(tmp_path: Path) -> None:
    dirs = [str(d) for d in _resolve_skill_dirs(Args(skills=["/abs/x"]), str(tmp_path), True)]
    assert dirs[0] == str(Path("/abs/x"))
    assert dirs[1] == str(tmp_path / ".aelix" / "skills")
    assert dirs[-1].endswith("skills") and "aelix_coding_agent" in dirs[-1]  # bundled last


# --- the filter --------------------------------------------------------------


def test_settings_turn_a_skill_off_but_never_a_cli_one(tmp_path: Path) -> None:
    global_, cli = tmp_path / "global", tmp_path / "cli"
    _skill(global_, "pdf")
    _skill(global_, "deploy")
    _skill(cli, "forced")
    sources = [SkillSource("cli", str(cli)), SkillSource("global", str(global_))]
    catalog = load_skill_catalog(sources, _lists(("-pdf", "-forced")))
    assert sorted(s.name for s in catalog.discovered) == ["deploy", "forced", "pdf"]
    assert sorted(s.name for s in catalog.skills) == ["deploy", "forced"]
    assert effective_skills({"result": catalog}) == catalog.skills


def test_a_plain_entry_adds_a_source_and_a_dead_one_is_reported(tmp_path: Path) -> None:
    extra = tmp_path / "claude-skills"
    _skill(extra, "from-claude")
    catalog = load_skill_catalog(
        [], _lists((str(extra), str(tmp_path / "missing"), "-nothing-by-this-name", "!zz*"))
    )
    assert [s.name for s in catalog.skills] == ["from-claude"]
    assert catalog.origins[catalog.skills[0].file_path].tier == "global"
    joined = "\n".join(catalog.problems)
    assert "is not a directory" in joined
    assert '"-nothing-by-this-name" matches no skill' in joined
    assert '"!zz*" matches no skill' in joined


def test_no_entry_warnings_when_nothing_was_scanned(tmp_path: Path) -> None:
    catalog = load_skill_catalog([], _lists(("-x",)), settings_sources=False, check_entries=False)
    assert catalog.problems == []


def test_a_plain_load_result_still_means_everything_is_on(tmp_path: Path) -> None:
    _skill(tmp_path, "pdf")
    loaded = load_skills([tmp_path])
    assert effective_skills({"result": loaded}) == loaded.skills


def test_load_skill_catalog_reads_both_settings_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.cli.config import get_agent_dir

    agent = Path(get_agent_dir())
    work = tmp_path / "work"
    _skill(agent / "skills", "pdf")
    _skill(agent / "skills", "deploy")
    _skill(work / ".aelix" / "skills", "conventions")
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "settings.json").write_text(json.dumps({"skills": ["-pdf", "-deploy"]}))
    (work / ".aelix" / "settings.json").write_text(json.dumps({"skills": ["+deploy", 3]}))
    manager = SettingsManager.create(work, agent, project_trusted=True)
    catalog = _load_skill_catalog(Args(), str(work), True, manager)
    names = {s.name for s in catalog.skills}
    assert "pdf" not in names
    assert {"deploy", "conventions"} <= names
    assert any("3 is not a string" in p for p in catalog.problems)

    untrusted = SettingsManager.create(work, agent, project_trusted=False)
    catalog = _load_skill_catalog(Args(), str(work), False, untrusted)
    names = {s.name for s in catalog.skills}
    assert "deploy" not in names and "conventions" not in names


def test_an_untrusted_project_is_counted_without_opening_a_file(tmp_path: Path) -> None:
    root = tmp_path / ".aelix" / "skills"
    (root / "a").mkdir(parents=True)
    (root / "a" / "SKILL.md").write_bytes(b"\xff\xfe not yaml, never parsed")
    (root / "b").mkdir()
    (root / "loose.md").write_text("x")
    (root / ".hidden").mkdir()
    assert count_untrusted_skills(str(root)) == 2
    assert count_untrusted_skills(str(tmp_path / "missing")) == 0


# --- D6: the prompt follows the setting -----------------------------------


async def _harness(tmp_path: Path, holder: dict[str, Any]) -> AgentHarness:
    session = Session(MemorySessionStorage())
    options = await _build_harness_options(
        Args(),
        session,
        skills=effective_skills(holder),
        skills_provider=lambda: effective_skills(holder),
    )
    harness = AgentHarness(options)
    harness.set_skills(effective_skills(holder))
    return harness


async def test_an_off_skill_is_not_in_the_first_prompt_and_an_apply_moves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "skills"
    _skill(root, "pdf-tools", "ZORB-ON-7721 reads PDFs.")
    _skill(root, "deploy", "ZORB-OFF-1189 deploys things.")
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(root))], _lists(("-deploy",)))
    }
    harness = await _harness(tmp_path, holder)
    prompt = harness.state.system_prompt
    assert "<available_skills>" in prompt
    assert "ZORB-ON-7721" in prompt and "ZORB-OFF-1189" not in prompt
    assert [s.name for s in harness.skills] == ["pdf-tools"]

    events: list[Any] = []
    harness.hooks.on("resources_update", lambda event, _ctx: events.append(event))
    kind = SkillKind(
        holder=holder,
        harness=lambda: harness,
        cwd=str(tmp_path),
        project_trusted=True,
        project_skills_dir=str(tmp_path / ".aelix" / "skills"),
    )
    result = await kind.apply({"deploy"})
    assert (
        result.applied and result.turned_on == ("deploy",) and result.turned_off == ("pdf-tools",)
    )
    prompt = harness.state.system_prompt
    assert "ZORB-OFF-1189" in prompt and "ZORB-ON-7721" not in prompt
    assert [s.name for s in harness.skills] == ["deploy"]
    assert len(events) == 1
    assert events[0].previous_resources["skills"] == ["pdf-tools"]
    assert events[0].resources["skills"] == ["deploy"]

    # Applying the same set again changes nothing and emits nothing.
    await kind.apply({"deploy"})
    assert len(events) == 1


class _BusyHarness:
    def __init__(self) -> None:
        self.is_idle = False
        self.idle = asyncio.Event()
        self.skills: list[Any] = []
        self.applied: list[list[str]] = []
        self.state = SimpleNamespace(resources={}, active_tool_names=None, tools=[])

    async def wait_for_idle(self) -> None:
        await self.idle.wait()

    def set_skills(self, skills: list[Any]) -> None:
        self.skills = list(skills)
        self.applied.append([s.name for s in skills])

    def rebuild_system_prompt(self) -> None:
        return None

    async def set_resources(self, resources: dict[str, Any]) -> None:
        self.state.resources = resources


async def test_an_apply_during_a_turn_waits_and_the_newest_save_wins(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _skill(root, "pdf")
    _skill(root, "deploy")
    agent = tmp_path / "agent"
    agent.mkdir()
    settings = agent / "settings.json"
    settings.write_text(json.dumps({"skills": ["-pdf"]}))
    manager = SettingsManager.create(tmp_path / "work", agent, project_trusted=True)
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())
    }
    harness = _BusyHarness()
    kind = SkillKind(
        holder=holder,
        harness=lambda: harness,
        cwd=str(tmp_path / "work"),
        project_trusted=True,
        project_skills_dir=str(tmp_path / "work" / ".aelix" / "skills"),
    )
    selection = ResourceSelection(
        manager,
        global_base=str(agent),
        project_base=str(tmp_path / "work" / ".aelix"),
        project_trusted=True,
    )
    reports: list[Any] = []
    first = await selection.apply(kind, on_deferred=reports.append)
    assert first.deferred and not first.applied
    settings.write_text(json.dumps({"skills": ["-deploy"]}))  # a later save
    second = await selection.apply(kind, on_deferred=reports.append)
    assert second.deferred
    await asyncio.sleep(0)
    assert harness.applied == []  # nothing lands inside the turn
    harness.is_idle = True
    harness.idle.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert harness.applied == [["pdf"]]  # the newest save, applied once
    assert len(reports) == 1 and reports[0].applied


async def test_a_deferred_apply_whose_file_broke_meanwhile_applies_nothing(
    tmp_path: Path,
) -> None:
    """Round 2 (R2: X3def): the deferred path refuses an unreadable file too.

    The save was fine; the file broke while the turn ran. When the turn ends the
    waiter's FRESH snapshot cannot read it, so nothing is applied — pdf stays
    off — and the report says why.
    """

    root = tmp_path / "skills"
    _skill(root, "pdf")
    _skill(root, "deploy")
    agent = tmp_path / "agent"
    agent.mkdir()
    settings = agent / "settings.json"
    settings.write_text(json.dumps({"skills": ["-pdf"]}))
    manager = SettingsManager.create(tmp_path / "work", agent, project_trusted=True)
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())
    }
    before = holder["result"]
    harness = _BusyHarness()
    kind = SkillKind(
        holder=holder,
        harness=lambda: harness,
        cwd=str(tmp_path / "work"),
        project_trusted=True,
        project_skills_dir=str(tmp_path / "work" / ".aelix" / "skills"),
    )
    selection = ResourceSelection(
        manager,
        global_base=str(agent),
        project_base=str(tmp_path / "work" / ".aelix"),
        project_trusted=True,
    )
    reports: list[Any] = []
    first = await selection.apply(kind, on_deferred=reports.append)
    assert first.deferred
    settings.write_text("{ broken while the turn ran")
    harness.is_idle = True
    harness.idle.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert harness.applied == []
    assert holder["result"] is before
    assert len(reports) == 1 and not reports[0].applied
    assert (reports[0].warning or "").startswith(f"kept what was applied: {settings}")


def test_off_reason_names_an_off_skill_only(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _skill(root, "pdf")
    _skill(root, "deploy")
    holder = {
        "result": load_skill_catalog([SkillSource("global", str(root))], _lists(("-deploy",)))
    }
    kind = SkillKind(
        holder=holder,
        harness=lambda: None,
        cwd=str(tmp_path),
        project_trusted=True,
        project_skills_dir=str(tmp_path / "p"),
    )
    assert kind.off_reason("deploy") == "deploy is off — /skill to turn it on"
    assert kind.off_reason("pdf") is None
    assert kind.off_reason("nope") is None


def test_items_groups_locks_and_the_untrusted_row(tmp_path: Path) -> None:
    root, cli = tmp_path / "global", tmp_path / "cli"
    _skill(root, "pdf")
    _skill(root, "dup")
    _skill(cli, "dup")
    project_skills = tmp_path / "work" / ".aelix" / "skills"
    _skill(project_skills, "secret")
    holder = {
        "result": load_skill_catalog(
            [SkillSource("cli", str(cli)), SkillSource("global", str(root))], SettingsLists()
        )
    }
    kind = SkillKind(
        holder=holder,
        harness=lambda: None,
        cwd=str(tmp_path / "work"),
        project_trusted=False,
        project_skills_dir=str(project_skills),
    )
    items = kind.items()
    labels = [(i.group_label.split()[0], i.label) for i in items]
    assert labels[0] == ("PROJECT", "1 skill not loaded")
    assert labels[-1] == ("THIS", "dup")
    locks = kind.locks()
    shadowed = [i for i in items if i.key.startswith("dup@")]
    assert len(shadowed) == 1
    assert locks[shadowed[0].key].value is False
    assert locks[shadowed[0].key].reason.startswith("shadowed by the --skill skill")
    assert locks["dup"].value is True
    assert locks["untrusted:project"].value is False


# --- D6: the choke point is the only way in -------------------------------


def test_every_set_skills_call_goes_through_effective_skills() -> None:
    """An AST gate over ``packages/``: ``set_skills(...)``'s argument is ``effective_skills(...)``.

    Modelled on ``test_every_writer_of_the_active_set_rebuilds``. A new caller
    that hands the harness a list it built itself fails here, not in a prompt
    that quietly offers the model a skill the user turned off.
    """

    offenders: list[str] = []
    calls = 0
    for path in sorted((REPO / "packages").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "set_skills":
                continue
            calls += 1
            arg = node.args[0] if node.args else None
            ok = (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "effective_skills"
            )
            if not ok:
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert calls >= 4, "the gate found too few calls to be checking anything"
    assert offenders == [], f"set_skills without effective_skills: {offenders}"


def _team_link(tmp_path: Path) -> tuple[Path, Path, Path]:
    """``agent/skills/notes`` -> ``team-a/notes``; ``team-b/notes`` exists too."""

    root = Path(os.path.realpath(tmp_path))
    for team in ("team-a", "team-b"):
        _skill(root / team, "notes", f"notes from {team}")
    agent = root / "agent"
    (agent / "skills").mkdir(parents=True)
    link = agent / "skills" / "notes"
    try:
        link.symlink_to(root / "team-a" / "notes", target_is_directory=True)
    except OSError as exc:  # Windows without the symlink privilege
        pytest.skip(f"no symlinks here: {exc}")
    return root, agent, link


def _repoint(link: Path, target: Path) -> None:
    link.unlink()
    link.symlink_to(target, target_is_directory=True)


def test_a_reload_judges_a_repointed_skill_link_by_its_new_target(tmp_path: Path) -> None:
    """Finish pass (MF1): round 2's realpath cache outlived the load that filled it.

    The verifier's ``probe_catalog.py``: launch, then ``/reload`` after the link
    moved from team-a to team-b, under ``!<root>/team-a/**``. The second load
    must resolve the link again — team-b's notes is on.
    """

    root, agent, link = _team_link(tmp_path)
    lists = SettingsLists(global_list=ScopeList("global", (f"!{root}/team-a/**",), str(agent)))
    sources = [SkillSource("global", str(agent / "skills"))]

    def on() -> list[tuple[str, str]]:
        return [(s.name, s.description) for s in load_skill_catalog(sources, lists).skills]

    assert on() == []
    _repoint(link, root / "team-b" / "notes")
    assert on() == [("notes", "notes from team-b")]


async def test_a_panel_snapshot_judges_a_repointed_skill_link_by_its_new_target(
    tmp_path: Path,
) -> None:
    root, agent, link = _team_link(tmp_path)
    (agent / "settings.json").write_text(json.dumps({"skills": [f"!{root}/team-a/**"]}))
    manager = SettingsManager.create(root / "work", agent, project_trusted=True)
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(agent / "skills"))], SettingsLists())
    }
    kind = SkillKind(
        holder=holder,
        harness=lambda: None,
        cwd=str(root / "work"),
        project_trusted=True,
        project_skills_dir=str(root / "work" / ".aelix" / "skills"),
    )
    selection = ResourceSelection(
        manager,
        global_base=str(agent),
        project_base=str(root / "work" / ".aelix"),
        project_trusted=True,
    )
    first = selection.snapshot(kind)
    assert [s.saved for s in first.states if s.item.label == "notes"] == [False]
    _repoint(link, root / "team-b" / "notes")
    second = selection.snapshot(kind)
    assert [s.saved for s in second.states if s.item.label == "notes"] == [True]


def test_the_launch_wiring_holds_the_apply_gate_over_every_build() -> None:
    """Round 2 (R2: X2b, X2c): the production wiring of the X2 gate, by AST.

    ``_async_main`` builds ONE ``ApplyGate``; ``_harness_factory`` — the only
    caller of ``_build_harness``, and the factory the runtime and the TUI get —
    holds it for the whole build; and every ``SkillKind`` is handed that gate.
    The behaviour on each side of the wiring is the tests above; this pins the
    wiring itself, which no behavioural test reaches without a whole launch.
    """

    source = (REPO / "packages/aelix-coding-agent/src/aelix_coding_agent/cli/entry.py").read_text(
        encoding="utf-8"
    )
    main = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_main"
    )
    gates = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "ApplyGate"
    ]
    assert [ast.unparse(g.targets[0]) for g in gates] == ["skill_apply_gate"]

    factory = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_harness_factory"
    )
    held = [
        call
        for with_ in ast.walk(factory)
        if isinstance(with_, ast.With)
        and [ast.unparse(item.context_expr) for item in with_.items] == ["skill_apply_gate"]
        for call in ast.walk(with_)
        if isinstance(call, ast.Call) and ast.unparse(call.func) == "_build_harness"
    ]
    assert len(held) == 1, "_harness_factory must build under `with skill_apply_gate:`"
    builds = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "_build_harness"
    ]
    assert builds == held, "_build_harness is called outside the gated factory"
    factories = [
        ast.unparse(kw.value)
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "harness_factory"
    ]
    runtime = [
        ast.unparse(node.args[1])
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "create_agent_session_runtime"
        and len(node.args) > 1
    ]
    assert factories and set(factories) == {"_harness_factory"}
    assert runtime == ["_harness_factory"]

    kinds = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "SkillKind"
    ]
    assert kinds, "no SkillKind built at launch"
    for kind in kinds:
        assert {kw.arg: ast.unparse(kw.value) for kw in kind.keywords}.get(
            "gate"
        ) == "skill_apply_gate", f"SkillKind at line {kind.lineno} is not given the gate"


def test_the_catalog_is_a_load_result() -> None:
    assert issubclass(SkillCatalog, type(load_skills([])))


# --- C10: prepare, then commit -----------------------------------------------------


def _kind(holder: dict[str, Any], harness: Any, tmp_path: Path) -> SkillKind:
    return SkillKind(
        holder=holder,
        harness=lambda: harness,
        cwd=str(tmp_path),
        project_trusted=True,
        project_skills_dir=str(tmp_path / ".aelix" / "skills"),
    )


async def _two_skill_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AgentHarness, dict[str, Any]]:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "skills"
    _skill(root, "pdf-tools", "ZORB-ON-7721 reads PDFs.")
    _skill(root, "deploy", "ZORB-OFF-1189 deploys things.")
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(root))], _lists(("-deploy",)))
    }
    return await _harness(tmp_path, holder), holder


async def test_a_failing_prompt_rebuild_applies_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, holder = await _two_skill_harness(tmp_path, monkeypatch)
    before_prompt = harness.state.system_prompt
    before_result = holder["result"]

    def broken(_tools: Any) -> str:
        raise OSError("probe: the docs signpost could not stat a file")

    monkeypatch.setattr(harness, "_system_prompt_rebuilder", broken)
    result = await _kind(holder, harness, tmp_path).apply({"deploy"})
    assert result.applied is False and "could not be rebuilt" in (result.warning or "")
    assert holder["result"] is before_result
    assert [s.name for s in harness.skills] == ["pdf-tools"]
    assert harness.state.system_prompt == before_prompt


async def test_a_raising_resources_update_handler_leaves_the_change_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, holder = await _two_skill_harness(tmp_path, monkeypatch)
    harness.state.resources["prompt_paths"] = ["/kept"]

    def raising(_event: Any, _ctx: Any) -> None:
        raise RuntimeError("observer broke")

    harness.hooks.on("resources_update", raising)
    result = await _kind(holder, harness, tmp_path).apply({"deploy"})
    assert result.applied is True
    assert "resources_update handler failed" in (result.warning or "")
    assert "ZORB-OFF-1189" in harness.state.system_prompt
    assert harness.state.resources["prompt_paths"] == ["/kept"]  # other buckets survive


# --- C9: a rebuild inside a turn reads the APPLIED snapshot ------------------------


async def test_a_save_during_a_turn_does_not_reach_a_tool_rebuild_until_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, holder = await _two_skill_harness(tmp_path, monkeypatch)
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({"skills": ["-pdf-tools"]}))
    manager = SettingsManager.create(tmp_path / "work", agent, project_trusted=True)
    selection = ResourceSelection(
        manager,
        global_base=str(agent),
        project_base=str(tmp_path / "work" / ".aelix"),
        project_trusted=True,
    )
    kind = _kind(holder, harness, tmp_path)
    harness._phase = "turn"  # a turn an extension started is running
    harness._idle_event.clear()
    reports: list[Any] = []
    deferred = await selection.apply(kind, on_deferred=reports.append)
    assert deferred.deferred
    harness.rebuild_system_prompt()  # a tool registration mid-turn
    assert "ZORB-ON-7721" in harness.state.system_prompt  # still the applied list
    harness._phase = "idle"
    harness._idle_event.set()
    for _ in range(50):
        if reports:
            break
        await asyncio.sleep(0.02)
    assert reports and reports[0].applied
    assert "ZORB-ON-7721" not in harness.state.system_prompt


# --- C8: the idle boundary is exclusive, and follows a replaced harness ------------


async def test_a_turn_claimed_before_the_waiter_resumes_keeps_the_apply_waiting(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    _skill(root, "pdf")
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({"skills": ["-pdf"]}))
    manager = SettingsManager.create(tmp_path / "work", agent, project_trusted=True)
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())
    }
    harness = _BusyHarness()
    kind = _kind(holder, harness, tmp_path)
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(tmp_path / ".aelix"), project_trusted=True
    )
    await selection.apply(kind)
    harness.idle.set()  # the first turn ends ...
    harness.idle = asyncio.Event()  # ... and a second claims the harness in the same tick
    for _ in range(10):
        await asyncio.sleep(0.05)
    assert harness.applied == []  # still waiting: busy() is re-checked after waking
    harness.is_idle = True
    harness.idle.set()
    for _ in range(40):
        if harness.applied:
            break
        await asyncio.sleep(0.05)
    assert harness.applied == [[]]


async def test_an_apply_waiting_on_a_replaced_harness_lands_on_the_new_one(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    _skill(root, "pdf")
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({"skills": ["-pdf"]}))
    manager = SettingsManager.create(tmp_path / "work", agent, project_trusted=True)
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())
    }
    old, new = _BusyHarness(), _BusyHarness()
    live = {"h": old}
    kind = SkillKind(
        holder=holder,
        harness=lambda: live["h"],
        cwd=str(tmp_path),
        project_trusted=True,
        project_skills_dir=str(tmp_path / ".aelix" / "skills"),
    )
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(tmp_path / ".aelix"), project_trusted=True
    )
    await selection.apply(kind)
    new.is_idle = True
    live["h"] = new  # /new or /reload replaced it; the old one never goes idle
    for _ in range(40):
        if new.applied:
            break
        await asyncio.sleep(0.05)
    assert new.applied == [[]] and old.applied == []


async def test_an_apply_never_lands_while_a_replacement_harness_is_being_built(
    tmp_path: Path,
) -> None:
    """Codex X2, as a controlled interleaving of the runtime's replacement.

    The factory reads the holder, installs skills on the candidate, then awaits
    (session recording) while the runtime still points at the outgoing harness.
    Before the gate, the waiter applied to the outgoing harness and the holder
    in that window, and the candidate was installed with the old list.
    """

    from aelix_coding_agent.core.skill_selection import ApplyGate

    root = tmp_path / "skills"
    _skill(root, "pdf")
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({"skills": ["-pdf"]}))
    manager = SettingsManager.create(tmp_path / "work", agent, project_trusted=True)
    holder: dict[str, Any] = {
        "result": load_skill_catalog([SkillSource("global", str(root))], SettingsLists())
    }
    gate = ApplyGate()
    outgoing, candidate = _BusyHarness(), _BusyHarness()
    outgoing.skills = list(holder["result"].skills)
    live = {"h": outgoing}
    kind = SkillKind(
        holder=holder,
        harness=lambda: live["h"],
        cwd=str(tmp_path),
        project_trusted=True,
        project_skills_dir=str(tmp_path / ".aelix" / "skills"),
        gate=gate,
    )
    selection = ResourceSelection(
        manager, global_base=str(agent), project_base=str(tmp_path / ".aelix"), project_trusted=True
    )
    assert (await selection.apply(kind)).deferred  # a turn is running on the outgoing harness

    async def replace() -> None:  # AgentSessionRuntime._apply around the factory
        with gate:
            outgoing.is_idle = True  # torn down: idle
            outgoing.idle.set()
            candidate.set_skills(effective_skills(holder))  # the build reads the holder
            await asyncio.sleep(0.4)  # ... and awaits (session recording)
            assert outgoing.applied == [] and holder["result"].skills  # nothing landed
            live["h"] = candidate  # installed as the factory returns
        candidate.is_idle = True

    await asyncio.sleep(0.05)  # the waiter parks on the outgoing harness's idle event
    await replace()
    for _ in range(40):
        if len(candidate.applied) > 1:
            break
        await asyncio.sleep(0.05)
    assert outgoing.applied == []
    assert candidate.applied[-1] == []  # pdf off on the installed harness ...
    assert effective_skills(holder) == []  # ... and in the holder its rebuilds read
