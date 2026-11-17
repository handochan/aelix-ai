"""#795, #796 (ADR-0304 D10, D11) — the ``/agents`` panel and the ``/agents`` subcommands.

The panel is ``selection_panel.py`` over a real ``AgentKind`` and a real
``ResourceSelection`` on scratch settings files and profile folders: three
groups in layout-A order, a shadowed row, one placeholder per file that did
not load, an untrusted folder's summary row (no file opened), and ADR-0304
D10's "not in use now" words, which come before the ``project`` provenance
word. ``/agents`` reads the APPLIED view through ``CommandContext.agent_delegation``:
an ``ON`` column, the identity line, ``/agents show``'s two rows and ``/agents
run``'s dim note; without it every one is left out.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.agents import discovery
from aelix_coding_agent.agents.discovery import discover_profiles
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.agents.service import AgentProfileService
from aelix_coding_agent.cli.args import Args
from aelix_coding_agent.core.agent_selection import AgentDirs, AgentKind, delegation_view
from aelix_coding_agent.core.resource_selection import ResourceSelection
from aelix_coding_agent.tui.commands import BUILTIN_COMMANDS, CommandContext, match_command
from aelix_coding_agent.tui.selection_panel import (
    POLICY_PENDING,
    SelectionModel,
    SelectionPanelBody,
    plan_layout,
    render_panel,
)
from aelix_coding_agent.tui.shell import agents_effect_note, agents_panel_opener

from tests.tui.test_agents_run_command import _Bench as _RunBench
from tests.tui.test_agents_run_command import _FakeRuntime, _ok_result, _render

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _plain(rows: list[str]) -> list[str]:
    return [_SGR.sub("", r) for r in rows]


@pytest.fixture(autouse=True)
def _no_settings_path_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_SETTINGS_PATH", raising=False)


def _profile(folder: Path, name: str, extra: str = "", description: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    desc = description if description is not None else f"{name} agent"
    path.write_text(
        f"---\nname: {name}\ndescription: {desc}\n{extra}---\n\nDo it.\n", encoding="utf-8"
    )
    return path


class _Harness:
    is_idle = True

    def __init__(self, tools: tuple[str, ...] = ("read", "agent")) -> None:
        self.tools = tools
        self.state = SimpleNamespace(resources={})

    def _action_get_active_tools(self) -> list[str]:
        return list(self.tools)

    async def set_resources(self, resources: dict[str, Any]) -> None:
        return None

    async def wait_for_idle(self) -> None:
        return None


class _Scene:
    """The live-check scene of contract §5.7 TUI 1, on scratch paths."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        trusted: bool = True,
        identity: str | None = "orchestrator",
        tools: tuple[str, ...] = ("read", "agent"),
    ) -> None:
        bundled = tmp_path / "bundled"
        _profile(bundled, "explorer")
        _profile(bundled, "general-purpose")
        monkeypatch.setattr(discovery, "builtin_agents_dir", lambda: bundled)
        self.agent = tmp_path / "agent"
        self.work = tmp_path / "work"
        users = self.agent / "agents"
        _profile(users, "orchestrator", "agents: [explorer]\n")
        _profile(users, "reviewer")
        _profile(users, "explorer", description="the user's explorer")
        (users / "broken.md").write_text("---\nname: Broken_Name\n---\n\nbody\n", encoding="utf-8")
        _profile(self.work / ".aelix" / "agents", "local")
        (self.agent / "settings.json").write_text(
            json.dumps({"agents": ["-reviewer"]}), encoding="utf-8"
        )
        (self.work / ".aelix" / "settings.json").write_text(
            json.dumps({"agents": ["+reviewer"]}), encoding="utf-8"
        )
        self.trusted = trusted
        self.manager = SettingsManager.create(self.work, self.agent, project_trusted=trusted)
        self.selection = ResourceSelection(
            self.manager,
            global_base=str(self.agent),
            project_base=str(self.work / ".aelix"),
            project_trusted=trusted,
        )
        self.holder: dict[str, Any] = {"result": self.selection.read_lists("agents")[0]}
        found = discover_profiles(self.work, project_trusted=trusted, agent_dir=str(self.agent))
        self.identity = next((p for p in found.profiles if p.name == identity), None)
        self.harness = _Harness(tools)
        self.kind = AgentKind(
            holder=self.holder,
            delegation=self.view,
            harness=lambda: self.harness,
            read_lists=lambda: self.selection.read_lists("agents"),
            cwd=str(self.work),
            project_trusted=trusted,
            dirs=AgentDirs(
                project=str(self.work / ".aelix" / "agents"),
                user=str(users),
                bundled=str(bundled),
            ),
        )

    def view(self) -> Any:
        found = discover_profiles(
            self.work, project_trusted=self.trusted, agent_dir=str(self.agent)
        )
        return delegation_view(found, self.holder["result"], self.identity)

    def model(self) -> SelectionModel:
        return SelectionModel(self.selection.snapshot(self.kind), "Agents", ("profile", "profiles"))


def _frame(model: SelectionModel, width: int = 120, rows: int = 30) -> list[str]:
    return _plain(render_panel(model, plan_layout(model, width, rows)))


def _row(frame: list[str], label: str) -> str:
    return next(r for r in frame if f" {label} " in r and ("[" in r or "(" in r))


def _select(model: SelectionModel, key: str) -> None:
    model.cursor = [s.item.key for s in model.visible()].index(key)


def _state(model: SelectionModel, key: str) -> Any:
    return next(s for s in model.states if s.item.key == key)


def test_three_groups_in_layout_a_order_with_their_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _Scene(tmp_path, monkeypatch)
    frame = _frame(scene.model())
    text = "\n".join(frame)
    heads = [
        i for i, r in enumerate(frame) if r.strip().startswith(("PROJECT", "GLOBAL", "BUNDLED"))
    ]
    assert [frame[i].split()[0] for i in heads] == ["PROJECT", "GLOBAL", "BUNDLED"]
    # display_path is os.path.relpath, as the skills panel's is: `.aelix\agents/` on win32
    # (tests/cli/test_agents_skills_509.py builds its labels the same way).
    assert f"PROJECT  {os.path.join('.aelix', 'agents')}/" in text
    keys = [s.item.key for s in scene.model().visible()]
    assert keys[0] == "local"
    assert keys.index("orchestrator") < keys.index(
        "explorer@" + str(tmp_path / "bundled" / "explorer.md")
    )


def test_a_shadowed_row_is_locked_and_space_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _Scene(tmp_path, monkeypatch)
    model = scene.model()
    key = "explorer@" + str(tmp_path / "bundled" / "explorer.md")
    assert model.status(_state(model, key))[0] == "shadowed"
    _select(model, key)
    refusal = model.toggle()
    assert refusal is not None and "shadowed by the global profile of the same name" in refusal
    assert model.edits == {}


def test_a_file_with_two_errors_is_one_failed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _Scene(tmp_path, monkeypatch)
    model = scene.model()
    failed = [s for s in model.states if s.item.key.startswith("failed:")]
    assert len(failed) == 1
    (row,) = failed
    assert row.item.label == "broken.md"
    assert model.status(row)[0] == "failed"
    _select(model, row.item.key)
    frame = "\n".join(_frame(model))
    assert "see /agents list · did not load: name contains invalid characters" in frame


def test_the_not_in_use_words_and_the_project_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _Scene(tmp_path, monkeypatch)
    model = scene.model()
    # reviewer: turned on by THIS project's file, outside orchestrator's list.
    reviewer = _state(model, "reviewer")
    assert reviewer.decided_by == "project"
    assert model.status(reviewer)[0] == "not listed"
    _select(model, "reviewer")
    line = _frame(model)[-3:]
    assert any("Saved on (this project's file)" in r for r in _frame(model)), line
    # The reason is whole at 80 columns (verifier nit: it was cut after "'orchestr").
    whole = "Saved on (this project's file) · now off (not listed by 'orchestrator')"
    assert any(r.strip() == whole for r in _frame(model, 80, 24))
    # A global-decided, not-listed row says the same word.
    assert model.status(_state(model, "orchestrator"))[0] == "not listed"
    # An unsaved row still shows its own word.
    _select(model, "orchestrator")
    model.toggle()
    assert model.status(_state(model, "orchestrator"))[0] == "* unsaved"


def test_a_project_profile_reads_agents_run_when_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _Scene(tmp_path, monkeypatch, identity=None)
    model = scene.model()
    local = _state(model, "local")
    assert model.status(local)[0] == "/agents run"
    _select(model, "local")
    assert any(
        "Saved on · now off (only /agents run starts a project profile)" in r for r in _frame(model)
    )


def test_the_banners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = _Scene(tmp_path, monkeypatch, tools=("read",))
    text = "\n".join(_frame(scene.model()))
    assert "The model has no agent tool in this session; choices still save." in text
    assert "This session runs as 'orchestrator', which may delegate only to: explorer." in text
    assert "project profile" not in text.split("PROJECT")[0]


def test_an_untrusted_folder_is_one_summary_row_and_no_file_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _Scene(tmp_path, monkeypatch, trusted=False)
    project_agents = str(scene.work / ".aelix" / "agents")
    opened: list[str] = []
    real_open = builtins.open
    real_read = Path.read_text

    def _spy_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def _spy_read(self: Path, *args: Any, **kwargs: Any) -> str:
        opened.append(str(self))
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _spy_open)
    monkeypatch.setattr(Path, "read_text", _spy_read)
    model = scene.model()
    monkeypatch.setattr(builtins, "open", real_open)
    monkeypatch.setattr(Path, "read_text", real_read)
    assert not [p for p in opened if p.startswith(project_agents)]
    assert not [p for p in opened if p.endswith(".aelix/settings.json")]
    summary = _state(model, "untrusted:project")
    assert summary.item.label == "1 profile not loaded"
    assert (
        summary.item.description
        == "This folder is not trusted, so its agent profiles are not loaded."
    )
    ok, reason = model.scope_writable("project")
    assert not ok and "not trusted" in (reason or "")


def _applied_from_files(scene: _Scene) -> None:
    scene.holder["result"] = scene.selection.read_lists("agents")[0]


def _no_project_entries(scene: _Scene) -> None:
    (scene.work / ".aelix" / "settings.json").write_text("{}", encoding="utf-8")
    _applied_from_files(scene)


async def test_a_change_of_lists_that_moves_no_row_is_shown_apart_from_rows_and_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 6 r1: applied ``-reviewer``, saved ``!rev*`` — no row moves, and the panel
    still says the saved list is not the one in use; an unsaved edit stays its own
    state; once applied, the line goes."""

    scene = _Scene(tmp_path, monkeypatch, identity=None)
    _no_project_entries(scene)
    same = scene.model()
    assert not same.snapshot.policy_pending
    _select(same, "general-purpose")
    same.toggle()  # an unsaved edit alone is not a saved change
    assert POLICY_PENDING not in "\n".join(_frame(same))
    (scene.agent / "settings.json").write_text(json.dumps({"agents": ["!rev*"]}), encoding="utf-8")
    model = scene.model()
    assert [s.item.key for s in model.states if s.pending] == []
    assert model.snapshot.policy_pending
    assert model.status(_state(model, "reviewer"))[0] == "off"
    for width, rows in ((120, 40), (80, 24)):
        frame = _frame(model, width, rows)
        (row,) = [r for r in frame if POLICY_PENDING in r]  # one row, uncut, at 80 columns
    _select(model, "general-purpose")
    model.toggle()
    assert model.status(_state(model, "general-purpose"))[0] == "* unsaved"
    assert POLICY_PENDING in "\n".join(_frame(model))
    # Enter with no edit applies the lists, and the summary says so.
    body = SelectionPanelBody(scene.selection, scene.kind, title="Agents")
    body.plan(120, 30)
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    said = " ".join(" ".join(str(line).split()) for line in lines)
    assert "Agents: nothing to save" in said
    assert "The saved list is in use now; no row changed." in said
    assert scene.holder["result"].global_list.entries == ("!rev*",)
    after = scene.model()
    assert not after.snapshot.policy_pending
    assert POLICY_PENDING not in "\n".join(_frame(after))
    # A file that cannot be read says so instead: nothing is applied from it.
    (scene.agent / "settings.json").write_text("{not json", encoding="utf-8")
    broken = scene.model()
    assert broken.snapshot.unreadable and not broken.snapshot.policy_pending
    assert POLICY_PENDING not in "\n".join(_frame(broken))


async def test_an_enter_that_moves_a_row_and_applies_the_lists_does_not_say_no_row_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r2 F3 (D10): the saved list differs from the applied one (``!rev*`` for
    ``-reviewer``) AND the user turns a row off; Enter applies both, and the summary
    names the row and the effect, never "no row changed"."""

    scene = _Scene(tmp_path, monkeypatch, identity=None)
    _no_project_entries(scene)
    (scene.agent / "settings.json").write_text(json.dumps({"agents": ["!rev*"]}), encoding="utf-8")
    body = SelectionPanelBody(
        scene.selection,
        scene.kind,
        title="Agents",
        noun=("profile", "profiles"),
        effect_note=lambda: agents_effect_note(scene.harness),
    )
    body.plan(120, 30)
    assert body.model.snapshot.policy_pending
    _select(body.model, "general-purpose")
    assert body.handle("space") == "handled"
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    said = " ".join(" ".join(str(line).split()) for line in lines)
    assert "off general-purpose" in said
    assert "The model gets the new agent list from your next message" in said
    assert "no row changed" not in said
    assert scene.holder["result"].global_list.entries == ("!rev*", "-general-purpose")


def test_the_four_banners_keep_their_order_and_the_pending_line_is_yellow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r2 F3 (D8, D9): ADR-0304 D10 numbers the banners 1-4 — no agent tool, the
    identity's list, a saved change of lists, a settings problem — and draws the third
    yellow, as the fourth. Read from the terminal's cells (pyte), not from an SGR shape."""

    import pyte

    scene = _Scene(tmp_path, monkeypatch, tools=("read",))
    (scene.agent / "settings.json").write_text(
        json.dumps({"agents": ["-reviewer", 123]}), encoding="utf-8"
    )
    _no_project_entries(scene)
    (scene.agent / "settings.json").write_text(
        json.dumps({"agents": ["!rev*", 123]}), encoding="utf-8"
    )
    model = scene.model()
    assert model.snapshot.policy_pending and model.snapshot.problems
    raw = render_panel(model, plan_layout(model, 120, 40))
    screen = pyte.Screen(120, len(raw))
    pyte.Stream(screen).feed("\r\n".join(raw))
    banners = [
        "The model has no agent tool in this session; choices still save.",
        "This session runs as 'orchestrator', which may delegate only to: explorer.",
        POLICY_PENDING,
        "⚠ ",
    ]
    at = [next(y for y, row in enumerate(screen.display) if b in row) for b in banners]
    assert at == sorted(at) and len(set(at)) == 4, at

    def fg(y: int) -> str:
        x = screen.display[y].index(next(c for c in screen.display[y] if c != " "))
        return screen.buffer[y][x].fg

    assert [fg(y) for y in at] == ["default", "default", "brown", "brown"]


def test_a_row_this_projects_file_saved_off_reads_project_as_a_skill_row_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 7 r1, measured: the shared panel says ``project`` for a row this project's
    file saved off and the session applied off — a skill row in the same state says
    the same — and the state line says off. ADR-0304 D10 follows the skills
    (owner: "skills와 똑같이")."""

    from aelix_coding_agent.core.skill_selection import SkillKind, SkillSource, load_skill_catalog

    scene = _Scene(tmp_path, monkeypatch)  # orchestrator: reviewer is not listed too
    (scene.work / ".aelix" / "settings.json").write_text(
        json.dumps({"agents": ["-reviewer"], "skills": ["-tidy"]}), encoding="utf-8"
    )
    _applied_from_files(scene)
    model = scene.model()
    reviewer = _state(model, "reviewer")
    assert (reviewer.saved, reviewer.applied, reviewer.decided_by) == (False, False, "project")
    assert model.status(reviewer)[0] == "project"
    _select(model, "reviewer")
    assert any("Saved off (this project's file) · now off" in r for r in _frame(model))

    skill = scene.agent / "skills" / "tidy" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: tidy\ndescription: t\n---\nbody\n", encoding="utf-8")
    lists = scene.selection.read_lists("skills")[0]
    holder = {
        "result": load_skill_catalog([SkillSource("global", str(skill.parent.parent))], lists)
    }
    skills = SkillKind(
        holder=holder,
        harness=lambda: None,
        cwd=str(scene.work),
        project_trusted=True,
        project_skills_dir=str(scene.work / ".aelix" / "skills"),
    )
    skill_model = SelectionModel(scene.selection.snapshot(skills), "Skills")
    tidy = _state(skill_model, "tidy")
    assert (tidy.saved, tidy.applied, tidy.decided_by) == (False, False, "project")
    assert skill_model.status(tidy)[0] == model.status(reviewer)[0] == "project"


class _TurnHarness(_Harness):
    """A harness a test holds busy, then lets go idle."""

    def __init__(self) -> None:
        super().__init__()
        self.is_idle = True
        self._idle = asyncio.Event()
        self._idle.set()

    def busy(self) -> None:
        self.is_idle = False
        self._idle.clear()

    def idle(self) -> None:
        self.is_idle = True
        self._idle.set()

    async def wait_for_idle(self) -> None:
        await self._idle.wait()


async def test_a_save_while_a_turn_runs_applies_when_it_ends_a_lists_only_change_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract §5.5 (Codex 4): Enter during a turn says it applies when the turn ends,
    and it applies then — a row's save, and a change of lists that moves no row."""

    scene = _Scene(tmp_path, monkeypatch, identity=None)
    _no_project_entries(scene)
    harness = _TurnHarness()
    scene.harness = harness
    committed: list[object] = []

    def _body() -> SelectionPanelBody:
        body = SelectionPanelBody(
            scene.selection,
            scene.kind,
            title="Agents",
            noun=("profile", "profiles"),
            effect_note=lambda: agents_effect_note(harness),
            commit=committed.append,
        )
        body.plan(120, 30)
        return body

    async def _until(done: Any) -> None:
        for _ in range(500):
            if done():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the deferred apply never landed")

    harness.busy()
    body = _body()
    _select(body.model, "general-purpose")
    assert body.handle("space") == "handled"
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    text = " ".join(" ".join(str(line).split()) for line in lines)
    assert "A turn is running; this applies when it ends." in text
    assert scene.holder["result"].global_list.entries == ("-reviewer",)
    harness.idle()
    await _until(lambda: bool(committed))
    assert "Agents applied after the turn: general-purpose off" in str(committed[0])
    assert scene.holder["result"].global_list.entries == ("-reviewer", "-general-purpose")

    (scene.agent / "settings.json").write_text(
        json.dumps({"agents": ["!rev*", "-general-purpose"]}), encoding="utf-8"
    )
    harness.busy()
    body = _body()
    assert POLICY_PENDING in "\n".join(
        _plain(render_panel(body.model, plan_layout(body.model, 120, 30)))
    )
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    text = " ".join(" ".join(str(line).split()) for line in lines)
    assert "A turn is running; this applies when it ends." in text
    assert scene.holder["result"].global_list.entries == ("-reviewer", "-general-purpose")
    harness.idle()
    await _until(lambda: scene.holder["result"].global_list.entries[0] == "!rev*")
    assert scene.holder["result"].global_list.entries == ("!rev*", "-general-purpose")


async def test_enter_writes_one_file_and_says_the_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _Scene(tmp_path, monkeypatch, identity=None)
    project_before = (scene.work / ".aelix" / "settings.json").read_bytes()
    body = SelectionPanelBody(
        scene.selection,
        scene.kind,
        title="Agents",
        noun=("profile", "profiles"),
        effect_note=lambda: agents_effect_note(scene.harness),
    )
    body.plan(120, 30)
    _select(body.model, "general-purpose")
    assert body.handle("space") == "handled" and body.dirty
    assert body.handle("enter") == "save"
    lines = await body.save()
    assert lines is not None
    text = " ".join(" ".join(str(line).split()) for line in lines)
    assert (
        "The model gets the new agent list from your next message; delegations already running "
        "keep running." in " ".join(text.split())
    )
    assert json.loads((scene.agent / "settings.json").read_text(encoding="utf-8"))["agents"] == [
        "-reviewer",
        "-general-purpose",
    ]
    assert (scene.work / ".aelix" / "settings.json").read_bytes() == project_before
    assert scene.holder["result"].global_list.entries == ("-reviewer", "-general-purpose")


async def test_esc_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = _Scene(tmp_path, monkeypatch)
    before = (scene.agent / "settings.json").read_bytes()
    body = SelectionPanelBody(
        scene.selection, scene.kind, title="Agents", noun=("profile", "profiles")
    )
    body.plan(120, 30)
    _select(body.model, "general-purpose")
    body.handle("space")
    assert body.handle("escape") == "close"
    assert "1 unsaved change discarded" in str(body.discard_lines()[0])
    assert (scene.agent / "settings.json").read_bytes() == before


def test_the_effect_note_with_no_agent_tool() -> None:
    assert agents_effect_note(_Harness(("read",))) == (
        "The model has no agent tool in this session; the choice applies where it has one."
    )


async def test_the_opener_runs_the_shared_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_coding_agent.tui import selection_panel

    calls: list[dict[str, Any]] = []

    async def _panel(ctx: Any, selection: Any, kind: Any, **kwargs: Any) -> None:
        calls.append({"ctx": ctx, "selection": selection, "kind": kind, **kwargs})

    monkeypatch.setattr(selection_panel, "run_selection_panel", _panel)
    control = SimpleNamespace(selection="S", kind="K")
    opener = agents_panel_opener(lambda: "CTX", control, lambda: _Harness(), lambda: 100)  # type: ignore[arg-type]
    await opener()
    (call,) = calls
    assert (call["ctx"], call["selection"], call["kind"], call["title"]) == (
        "CTX",
        "S",
        "K",
        "Agents",
    )
    assert call["noun"] == ("profile", "profiles")
    assert call["render_width_cap"]() == 100
    assert call["effect_note"]().startswith("The model gets the new agent list")


# === /agents (ADR-0304 D11) ==============================================


def _view_scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Scene:
    return _Scene(tmp_path, monkeypatch)


async def _run(ctx: CommandContext, line: str) -> str:
    committed: list[Any] = []
    ctx.commit = committed.append  # type: ignore[assignment]
    command = match_command(line, BUILTIN_COMMANDS)
    assert command is not None and command.handler is not None
    parts = line.split(maxsplit=1)
    await command.handler(ctx, parts[1] if len(parts) > 1 else "")
    return "".join(_render(item) for item in committed)


async def test_bare_agents_opens_the_panel_and_lists_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _view_scene(tmp_path, monkeypatch)
    opened: list[str] = []

    async def _panel() -> None:
        opened.append("panel")

    ctx = CommandContext(
        harness=None, chrome=None, cwd=".", commit=lambda _r: None, agent_panel=_panel
    )  # type: ignore[arg-type]
    await _run(ctx, "/agents")
    assert opened == ["panel"]
    bare = CommandContext(
        harness=None,  # type: ignore[arg-type]
        chrome=None,  # type: ignore[arg-type]
        cwd=".",
        commit=lambda _r: None,
        agent_profiles=lambda: scene.view().discovered,
    )
    listed = await _run(bare, "/agents")
    assert "Agent profiles" in listed and " ON " not in listed


async def test_agents_list_has_the_on_column_and_the_identity_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _view_scene(tmp_path, monkeypatch)
    ctx = CommandContext(
        harness=None,  # type: ignore[arg-type]
        chrome=None,  # type: ignore[arg-type]
        cwd=".",
        commit=lambda _r: None,
        agent_profiles=lambda: scene.view().discovered,
        agent_delegation=scene.view,
    )
    text = await _run(ctx, "/agents list")
    header = next(line for line in text.splitlines() if "NAME" in line)
    assert header.split()[1:4] == ["NAME", "ON", "SCOPE"]
    reviewer = next(line for line in text.splitlines() if "│ reviewer " in line)
    assert reviewer.split()[1:3] == ["reviewer", "on"]  # the project file turns it on
    assert "This session runs as 'orchestrator', which may delegate only to: explorer." in text
    scene.holder["result"] = scene.selection.read_lists("agents")[0]
    (scene.work / ".aelix" / "settings.json").write_text("{}", encoding="utf-8")
    scene.holder["result"] = scene.selection.read_lists("agents")[0]
    text = await _run(ctx, "/agents list")
    reviewer = next(line for line in text.splitlines() if "│ reviewer " in line)
    assert reviewer.split()[1:3] == ["reviewer", "off"]
    plain = CommandContext(
        harness=None,  # type: ignore[arg-type]
        chrome=None,  # type: ignore[arg-type]
        cwd=".",
        commit=lambda _r: None,
        agent_profiles=lambda: scene.view().discovered,
    )
    assert "This session runs as" not in await _run(plain, "/agents list")


async def test_agents_show_has_the_two_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _view_scene(tmp_path, monkeypatch)
    (scene.work / ".aelix" / "settings.json").write_text("{}", encoding="utf-8")
    scene.holder["result"] = scene.selection.read_lists("agents")[0]
    ctx = CommandContext(
        harness=None,  # type: ignore[arg-type]
        chrome=None,  # type: ignore[arg-type]
        cwd=".",
        commit=lambda _r: None,
        agent_profiles=lambda: scene.view().discovered,
        agent_delegation=scene.view,
    )
    reviewer = await _run(ctx, "/agents show reviewer")
    assert "│ delegation          off in /agents: the model cannot start it" in reviewer
    assert "every profile that is on" in reviewer
    assert "a delegated agent cannot delegate" in reviewer
    orchestrator = await _run(ctx, "/agents show orchestrator")
    rows = {
        line.split()[1]: line.split()[2:]
        for line in orchestrator.splitlines()
        if line.startswith("│ ") and len(line.split()) > 2
    }
    assert rows["delegation"][0] == "on"
    assert rows["agents"][0] == "explorer"


async def test_the_service_view_carries_the_session_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifier V76: ``AgentProfileService.delegation()`` is taken under its ``active``
    identity — ``/agents list``'s identity line and the panel's banner, both fed by
    the REAL service."""

    scene = _Scene(tmp_path, monkeypatch)
    service = AgentProfileService(
        cwd=str(scene.work),
        project_trusted=True,
        parsed=Args(),
        baseline=Args(),
        skills_holder={},
        agent_dir=str(scene.agent),
        active=scene.identity,
        agent_lists=lambda: scene.holder["result"],
    )
    ctx = CommandContext(
        harness=None,  # type: ignore[arg-type]
        chrome=None,  # type: ignore[arg-type]
        cwd=".",
        commit=lambda _r: None,
        agent_profiles=service.list,
        agent_delegation=service.delegation,
    )
    line = "This session runs as 'orchestrator', which may delegate only to: explorer."
    assert line in await _run(ctx, "/agents list")
    scene.kind._delegation = service.delegation
    model = scene.model()
    assert line in "\n".join(_frame(model))
    assert model.status(_state(model, "reviewer"))[0] == "not listed"
    service.active = None
    assert "This session runs as" not in await _run(ctx, "/agents list")


async def test_an_identity_that_delegates_to_no_profile_on_every_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifier F3 (V14, V19, V101, V102): ``agents: []`` in the panel's words and
    banner, ``/agents show``'s row and ``/agents list``'s identity line."""

    _profile(tmp_path / "agent" / "agents", "solo", "agents: []\n")
    scene = _Scene(tmp_path, monkeypatch, identity="solo")
    model = scene.model()
    for key in ("general-purpose", "reviewer", "orchestrator"):
        assert model.status(_state(model, key))[0] == "not listed", key
    _select(model, "general-purpose")
    frame = "\n".join(_frame(model))
    assert "This session runs as 'solo', which delegates to no profile (agents: [])." in frame
    assert "Saved on · now off (not listed by 'solo')" in frame
    ctx = CommandContext(
        harness=None,  # type: ignore[arg-type]
        chrome=None,  # type: ignore[arg-type]
        cwd=".",
        commit=lambda _r: None,
        agent_profiles=lambda: scene.view().discovered,
        agent_delegation=scene.view,
    )
    listed = await _run(ctx, "/agents list")
    assert "This session runs as 'solo', which delegates to no profile (agents: [])." in listed
    shown = await _run(ctx, "/agents show solo")
    rows = {
        line.split()[1]: line.split()[2:]
        for line in shown.splitlines()
        if line.startswith("│ ") and len(line.split()) > 2
    }
    assert rows["agents"][0] == "none"


def test_a_locked_on_row_off_this_run_takes_the_kinds_own_clause() -> None:
    """Verifier V95: the state line's locked-row clause is the kind's note when it has
    one, else the tools' "turned off this run"."""

    from aelix_coding_agent.core.resource_selection import ItemState, Lock, ResourceItem, Snapshot

    item = ResourceItem(
        kind="agent", key="k", label="k", description="d", group="g", group_label="G"
    )
    lock = Lock(True, "loaded by -e for this run")
    for note, clause in (
        (("not listed", "the kind's own clause"), "off now (the kind's own clause)"),
        (None, "off now (turned off this run)"),
    ):
        state = ItemState(item, True, True, None, lock, live=False, live_note=note)
        model = SelectionModel(Snapshot(kind="agent", states=(state,), scopes={}), "Agents")
        assert any(clause in row for row in _frame(model, 120, 20)), note


@pytest.mark.parametrize(
    ("allow", "lists", "note"),
    [
        (
            None,
            ("-scout",),
            "scout is off in /agents, so the model cannot delegate to it; you can still run it.",
        ),
        (
            ("explorer",),
            (),
            "orchestrator's agents: does not list scout, so the model cannot delegate to it; you can still run it.",
        ),
    ],
)
async def test_agents_run_of_a_profile_the_model_could_not_start_runs_after_a_note(
    tmp_path: Path, allow: tuple[str, ...] | None, lists: tuple[str, ...], note: str
) -> None:
    from aelix_coding_agent.agents.discovery import ProfileDiscoveryResult
    from aelix_coding_agent.core.resource_filter import ScopeList
    from aelix_coding_agent.core.resource_selection import SettingsLists

    bench = _RunBench(tmp_path)
    runtime = bench.bind(_FakeRuntime(result=_ok_result()))
    scout = AgentProfile(name="scout", description="s", body="b", file_path="/s.md", scope="user")
    identity = AgentProfile(
        name="orchestrator",
        description="o",
        body="b",
        file_path="/o.md",
        scope="user",
        agents=allow,
    )
    found = ProfileDiscoveryResult([scout, identity], [])
    settings = SettingsLists(ScopeList("global", lists, "/g"), None)
    bench.ctx.agent_delegation = lambda: delegation_view(
        found, settings, identity if allow else None
    )
    text = await bench.run("/agents run scout go")
    assert runtime.spawn_calls == [("scout", "go")]
    assert text.index(note) < text.index("Found 3 files")


def test_the_description_keeps_the_panel_words() -> None:
    command = match_command("/agents", BUILTIN_COMMANDS)
    assert command is not None
    assert command.description == ("Turn agent profiles on or off (Space toggles, Enter saves)")


# === run_tui's wiring (ADR-0304 D11) =======================================


async def test_run_tui_wires_the_panel_and_the_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from aelix_coding_agent.tui import selection_panel
    from aelix_coding_agent.tui.shell import run_tui

    from tests.tui.test_reload_extensions_25 import _Registry
    from tests.tui.test_run_tui_smoke import (
        FakeHarness,
        FakeRuntime,
        _harness_chrome,
        _quit_within,
        _spy_commits,
        _wait,
    )

    scene = _Scene(tmp_path, monkeypatch)
    opened: list[str] = []

    async def _panel(ctx: Any, selection: Any, kind: Any, **kwargs: Any) -> None:
        opened.append(kwargs["title"])

    monkeypatch.setattr(selection_panel, "run_selection_panel", _panel)
    service = SimpleNamespace(
        active=scene.identity, list=lambda: scene.view().discovered, delegation=scene.view
    )
    control = SimpleNamespace(selection=scene.selection, kind=scene.kind)
    async with _harness_chrome() as (_base, chrome, pipe):
        commits = _spy_commits(chrome)
        task = asyncio.ensure_future(
            run_tui(
                FakeRuntime(FakeHarness()),  # type: ignore[arg-type]
                cwd=".",
                chrome=chrome,
                install_signal_handlers=False,
                model_registry=_Registry(),  # type: ignore[arg-type]
                agent_service=service,  # type: ignore[arg-type]
                agent_control=control,  # type: ignore[arg-type]
            )
        )
        await _wait(lambda: chrome.app.is_running)
        pipe.send_text("/agents\n")
        await _wait(lambda: opened == ["Agents"])
        pipe.send_text("/agents list\n")
        await _wait(lambda: any("This session runs as 'orchestrator'" in c for c in commits))
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert opened == ["Agents"]
    # The identity line is drawn only from ``agent_delegation`` (the ON column
    # beside it is pinned on the rendered table above).
    assert any("This session runs as 'orchestrator'" in c for c in commits)
