"""#508 — prompt templates and skills in the ``/`` menu.

``/skill:<name>`` and ``/<template>`` ran, but the menu never listed them, so
the feature looked absent. These pin what the menu offers (pi's order, the
enabled skills only, nothing that would not run as typed), how a row is drawn
(one row, controls named, cut by cells), and that ``run_tui`` hands the live
harness to the completer at every call site.
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from _polling import quit_within as _quit_within  # sibling helper (prepend mode)
from _polling import wait_until
from aelix_ai.settings import SettingsManager
from aelix_coding_agent.core.resource_selection import ResourceSelection, SettingsLists
from aelix_coding_agent.core.skill_selection import (
    SkillControl,
    SkillKind,
    SkillSource,
    load_skill_catalog,
)
from aelix_coding_agent.tui import completion as completion_mod
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    BuiltinCommand,
    CommandContext,
    build_help_renderable,
)
from aelix_coding_agent.tui.completion import (
    DescriptorCommandCompleter,
    ResourceCommands,
    resource_commands,
)
from aelix_coding_agent.tui.selection_panel import cells
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime, _BusExtRuntime

_SHELL_SRC = (
    Path(__file__).resolve().parents[2]
    / "packages/aelix-coding-agent/src/aelix_coding_agent/tui/shell.py"
)

_BUILTINS = [
    BuiltinCommand("help", "List available commands"),
    BuiltinCommand("skill", "Turn skills on or off", aliases=("skills",)),
    BuiltinCommand("tool", "Turn tools on or off", aliases=("tools",)),
    BuiltinCommand("quit", "Exit Aelix", aliases=("exit",)),
]


@dataclass
class _Route:
    command: str
    description: str = ""


def _menu(
    text: str,
    *,
    templates: list[tuple[str, str]] | None = None,
    skills: list[tuple[str, str, str]] | None = None,
    ext: list[tuple[str, str]] | None = None,
    routes: dict[str, Any] | None = None,
    builtins: list[BuiltinCommand] | None = None,
) -> list[Any]:
    resources = ResourceCommands(templates or [], skills or [])
    completer = DescriptorCommandCompleter(
        lambda: routes or {},
        builtins=_BUILTINS if builtins is None else builtins,
        get_ext_commands=lambda: list(ext or []),
        get_resources=lambda: resources,
    )
    doc = Document(text=text, cursor_position=len(text))
    return list(completer.get_completions(doc, CompleteEvent()))


def _texts(out: list[Any]) -> list[str]:
    return [c.text for c in out]


# --- what is offered -------------------------------------------------------


def test_the_menu_lists_templates_and_skills_in_pis_order() -> None:
    out = _menu(
        "/",
        templates=[("zzreview", "Demo template")],
        skills=[("zzdemo", "Demo skill", "--skill")],
        ext=[("deploy", "Ship it")],
        routes={"ns:route": _Route("route", "A route")},
    )
    # pi ``createBaseAutocompleteProvider``: built-ins, templates, extension
    # commands, skills; descriptor routes stay right after the built-ins.
    assert _texts(out) == [
        "/help", "/skill", "/skills", "/tool", "/tools", "/quit", "/exit",
        "/route", "/zzreview", "/deploy", "/skill:zzdemo",
    ]  # fmt: skip
    by_text = {c.text: c.display_meta_text for c in out}
    assert by_text["/zzreview"] == "Demo template"
    assert by_text["/skill:zzdemo"] == "[--skill] Demo skill"
    assert next(c for c in out if c.text == "/skill:zzdemo").display_text == "skill:zzdemo"


def test_the_issue_repro_rows() -> None:
    kw: dict[str, Any] = {
        "templates": [("zzreview", "Demo template")],
        "skills": [("zzdemo", "Demo skill", "--skill")],
    }
    # The issue's table: /sk offered `skills` alone, /skill:z and /zz nothing.
    assert _texts(_menu("/sk", **kw)) == ["/skill", "/skills", "/skill:zzdemo"]
    assert _texts(_menu("/skill:z", **kw)) == ["/skill:zzdemo"]
    assert _texts(_menu("/zz", **kw)) == ["/zzreview", "/skill:zzdemo"]
    assert _texts(_menu("/skill:nope", **kw)) == []


def test_a_skill_is_found_by_its_name_without_the_prefix() -> None:
    skills = [("zzdemo", "Z", "global"), ("security", "S", "global")]
    # pi #8813 / #9944: bare-name matches come first, then the skills that
    # match only as ``skill:…``.
    assert _texts(_menu("/zz", skills=skills)) == ["/skill:zzdemo"]
    assert _texts(_menu("/s", skills=skills, builtins=[])) == [
        "/skill:security",
        "/skill:zzdemo",
    ]
    # Accepting a bare-name row replaces the whole typed word with the full line.
    row = _menu("/zz", skills=skills)[0]
    assert row.start_position == -len("/zz")


def test_an_alias_is_a_row_of_its_own() -> None:
    out = _menu("/to")
    assert [(c.text, c.display_meta_text) for c in out] == [
        ("/tool", "Turn tools on or off"),
        ("/tools", "Same as /tool"),
    ]
    # Typed in full, the completer still offers it (prompt-toolkit then hides a
    # lone row that would insert nothing, as it does for ``/help`` typed in full).
    assert _texts(_menu("/tools")) == ["/tools"]
    assert _texts(_menu("/ex")) == ["/exit"]


def test_the_real_registry_offers_tools_and_skills() -> None:
    out = _menu("/to", builtins=BUILTIN_COMMANDS)
    assert _texts(out) == ["/tool", "/tools"]
    tool = next(c for c in BUILTIN_COMMANDS if c.name == "tool")
    assert out[0].display_meta_text == tool.description
    assert _texts(_menu("/skill", builtins=BUILTIN_COMMANDS)) == ["/skill", "/skills"]


# --- one name, offered by what runs it -------------------------------------


def test_a_name_is_offered_by_what_runs_when_it_is_typed() -> None:
    out = _menu(
        "/",
        builtins=_BUILTINS,
        templates=[("help", "t"), ("tools", "t"), ("deploy", "t"), ("route", "t"), ("ok", "t")],
        ext=[("deploy", "ext deploy")],
        routes={"ns:route": _Route("route", "the route")},
    )
    meta = {c.text: c.display_meta_text for c in out}
    # A built-in or its alias, an extension command and a descriptor route each
    # take the name before a template does (the input loop's order).
    assert meta["/help"] == "List available commands"
    assert meta["/tools"] == "Same as /tool"
    assert meta["/deploy"] == "ext deploy"
    assert meta["/route"] == "the route"
    assert meta["/ok"] == "t"
    assert _texts(out).count("/deploy") == 1


def test_a_command_hides_a_template_even_when_it_does_not_match_the_typed_text() -> None:
    # ``/pd`` must not offer the skill when ``/skill:pdf`` runs the extension's
    # command, although the extension's row itself does not match ``pd``.
    skills = [("pdf", "the skill", "global")]
    ext = [("skill:pdf", "the extension")]
    assert _texts(_menu("/pd", skills=skills, ext=ext)) == []
    assert [(c.text, c.display_meta_text) for c in _menu("/skill:", skills=skills, ext=ext)] == [
        ("/skill:pdf", "the extension")
    ]
    assert _texts(_menu("/de", templates=[("deploy", "t")], ext=[("deployx", "x")])) == [
        "/deploy",
        "/deployx",
    ]
    assert _texts(_menu("/dep", templates=[("deploy", "t")], ext=[("deploy", "x")])) == ["/deploy"]


@pytest.mark.parametrize(
    "name",
    ["two words", "tab\tname", "esc\x1bname", "bidi‮name", "nl\nname", ""],
)
def test_a_name_that_cannot_be_typed_is_not_offered(name: str) -> None:
    out = _menu("/", builtins=[], templates=[(name, "t")], skills=[(name, "s", "global")])
    assert out == []


def test_a_template_named_like_a_skill_line_is_not_offered() -> None:
    # ``/skill:x`` always goes to the skill path; the template can never run.
    assert _menu("/", builtins=[], templates=[("skill:x", "t")]) == []


# --- how a row is drawn ------------------------------------------------------


def test_a_description_is_one_row_with_its_controls_named() -> None:
    out = _menu(
        "/",
        builtins=[],
        skills=[
            ("multi", "First line.\n  Second line.\n", "global"),
            ("esc", "Colour \x1b[31mRED", "project"),
            ("tab", "a\tb", ""),
        ],
        ext=[("e\x1bxt", "ext \x1b[2J desc")],
    )
    meta = {c.display_text: c.display_meta_text for c in out}
    assert meta["skill:multi"] == "[global] First line. Second line."
    assert meta["skill:esc"] == "[project] Colour ^[[31mRED"
    assert meta["skill:tab"] == "a b"
    # Not file-sourced, but drawn the same way: an extension's name and
    # description cannot write to the terminal either.
    assert meta["e^[xt"] == "ext ^[[2J desc"
    for c in out:
        assert "\x1b" not in c.display_text + c.display_meta_text


@pytest.mark.parametrize("columns", [40, 80, 120])
def test_a_description_is_cut_by_cells_to_the_menu_column(
    monkeypatch: pytest.MonkeyPatch, columns: int
) -> None:
    monkeypatch.setattr(completion_mod, "_menu_columns", lambda: columns)
    out = _menu(
        "/skill:",
        builtins=[],
        skills=[
            ("kr-long", "한국어 설명이 아주 길게 이어지는 스킬입니다 " * 8, "global"),
            ("plain", "short", "global"),
        ],
    )
    widest = max(cells(c.display_text) for c in out)
    # prompt-toolkit's menu: name column (+2), description (+2), scrollbar (1).
    room = columns - (widest + 2) - 2 - 1
    long, short = out
    assert cells(long.display_meta_text) <= room
    assert long.display_meta_text.endswith("…")
    assert cells(long.display_meta_text) >= room - 1  # a wide cluster can leave one cell
    assert short.display_meta_text == "[global] short"
    assert (widest + 2) + (cells(long.display_meta_text) + 2) + 1 <= columns


def test_a_long_name_does_not_squeeze_every_description() -> None:
    long = "a-very-long-skill-name-that-goes-on-and-on-for-sixty-chars-x"
    out = _menu("/", builtins=_BUILTINS[:1], skills=[(long, "Long name", "global")])
    help_row, skill_row = out
    # pi's autocomplete name column: 32 cells, the 2-cell gap included.
    assert cells(skill_row.display_text) == 30 and skill_row.display_text.endswith("…")
    assert skill_row.text == f"/skill:{long}"  # Tab still inserts the whole line
    assert help_row.display_meta_text == "List available commands"


def test_only_the_head_of_a_long_description_is_read_and_a_cut_still_says_so() -> None:
    from aelix_coding_agent.tui.completion import _menu_text

    # Line breaks collapse over the whole text, so deep indentation is not a cut.
    assert _menu_text("a\n" + " " * 300 + "b", 20) == "a b"
    # A head that fits because it is all zero-width marks still ends in "…".
    marks = _menu_text("a" + "\u0301" * 200 + "zz", 10)
    assert marks.endswith("…") and "zz" not in marks
    assert _menu_text("x" * 5000, 12) == "x" * 11 + "…"
    assert _menu_text("short", 0) == ""
    # A control at either end is named, not stripped as whitespace.
    assert _menu_text("\x1fx\u2028", 20) == "^_x<U+2028>"


def test_no_room_for_a_description_draws_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(completion_mod, "_menu_columns", lambda: 10)
    out = _menu("/skill:", builtins=[], skills=[("kr-long", "x" * 50, "global")])
    assert [c.display_meta_text for c in out] == [""]


# --- live, and contained -----------------------------------------------------


def test_the_sources_are_read_every_keystroke() -> None:
    templates: list[tuple[str, str]] = []
    skills: list[tuple[str, str, str]] = []
    completer = DescriptorCommandCompleter(
        lambda: {}, builtins=[], get_resources=lambda: ResourceCommands(templates, skills)
    )
    doc = Document(text="/zz", cursor_position=3)
    assert list(completer.get_completions(doc, CompleteEvent())) == []
    templates.append(("zzreview", "t"))
    skills.append(("zzdemo", "s", ""))
    assert _texts(list(completer.get_completions(doc, CompleteEvent()))) == [
        "/zzreview",
        "/skill:zzdemo",
    ]


def test_a_faulty_resource_source_leaves_the_commands() -> None:
    def boom() -> ResourceCommands:
        raise RuntimeError("boom")

    completer = DescriptorCommandCompleter(lambda: {}, builtins=_BUILTINS, get_resources=boom)
    doc = Document(text="/he", cursor_position=3)
    assert _texts(list(completer.get_completions(doc, CompleteEvent()))) == ["/help"]


def test_a_faulty_route_source_no_longer_drops_the_rest() -> None:
    def boom() -> dict[str, Any]:
        raise RuntimeError("boom")

    completer = DescriptorCommandCompleter(
        boom,
        builtins=[],
        get_ext_commands=lambda: [("deploy", "d")],
        get_resources=lambda: ResourceCommands([("zzreview", "t")]),
    )
    doc = Document(text="/", cursor_position=1)
    assert _texts(list(completer.get_completions(doc, CompleteEvent()))) == [
        "/zzreview",
        "/deploy",
    ]


@dataclass
class _Skill:
    name: str
    description: str
    file_path: str = ""


@dataclass
class _Template:
    name: str
    description: str = ""


@dataclass
class _Harness:
    skills: list[Any] = field(default_factory=list)
    prompt_templates: list[Any] = field(default_factory=list)


def test_resource_commands_reads_the_harness_as_the_input_loop_does() -> None:
    harness = _Harness([_Skill("zzdemo", "Demo skill")], [_Template("zzreview", "Demo")])
    got = resource_commands(harness, skill_source=lambda s: "global")
    assert got == ResourceCommands([("zzreview", "Demo")], [("zzdemo", "Demo skill", "global")])
    # enableSkillCommands off: no /skill:<name> runs, so none is offered.
    off = resource_commands(harness, skill_commands_enabled=False)
    assert off == ResourceCommands([("zzreview", "Demo")], [])
    # A duck-typed harness without the lists, and a faulty source, never raise.
    assert resource_commands(object()) == ResourceCommands([], [])

    def boom(_skill: object) -> str:
        raise RuntimeError("boom")

    assert resource_commands(harness, skill_source=boom).skills == [("zzdemo", "Demo skill", "")]


# --- the skills kind: the tag and the panel's line ---------------------------


def _write_skill(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} does a thing.\n---\nbody\n", encoding="utf-8"
    )


def _kind(tmp_path: Path, *, enabled: bool = True, off: tuple[str, ...] = ()) -> SkillKind:
    _write_skill(tmp_path / "g", "pdf")
    _write_skill(tmp_path / "g", "deploy")
    _write_skill(tmp_path / "c", "zzdemo")
    from aelix_coding_agent.core.resource_filter import ScopeList

    holder = {
        "result": load_skill_catalog(
            [SkillSource("global", str(tmp_path / "g")), SkillSource("cli", str(tmp_path / "c"))],
            SettingsLists(ScopeList("global", off, str(tmp_path))),
        )
    }
    return SkillKind(
        holder=holder,
        harness=lambda: None,
        cwd=str(tmp_path),
        project_trusted=True,
        project_skills_dir=str(tmp_path / "p"),
        skill_commands_enabled=lambda: enabled,
    )


def test_source_word_names_where_a_skill_came_from(tmp_path: Path) -> None:
    kind = _kind(tmp_path)
    catalog = kind._catalog()
    words = {s.name: kind.source_word(s) for s in catalog.skills}
    assert words == {"zzdemo": "--skill", "pdf": "global", "deploy": "global"}
    assert kind.source_word(_Skill("ghost", "x", "/nowhere/SKILL.md")) is None
    assert kind.source_word(object()) is None


def test_the_panel_says_how_to_run_a_skill(tmp_path: Path) -> None:
    on = _kind(tmp_path).notices()
    assert "Run an enabled skill with /skill:<name>; type /skill: to list them." in on
    off = _kind(tmp_path, enabled=False).notices()
    assert "/skill:<name> is turned off in settings (enableSkillCommands)." in off
    assert not any(n.startswith("Run an enabled skill") for n in off)


# --- /help and the headless list --------------------------------------------


def _render(renderable: object, width: int = 100) -> str:
    console = Console(width=width, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_help_names_each_alias_beside_its_command() -> None:
    out = _render(build_help_renderable(BUILTIN_COMMANDS))
    for command in BUILTIN_COMMANDS:
        names = ", ".join(f"/{n}" for n in (command.name, *command.aliases))
        row = next(line for line in out.splitlines() if f"│ {names} " in line)
        assert command.description in row
    assert "/tool, /tools" in out and "/skill, /skills" in out and "/quit, /exit" in out


async def test_the_headless_skill_list_says_how_to_run_one() -> None:
    committed: list[object] = []
    ctx = CommandContext(
        chrome=object(),  # type: ignore[arg-type]
        harness=_Harness([_Skill("zz\x1bdemo", "Demo [bold]skill[/bold]")]),  # type: ignore[arg-type]
        commit=committed.append,
        cwd="/",
        commands=list(BUILTIN_COMMANDS),
    )
    skill = next(c for c in BUILTIN_COMMANDS if c.name == "skill")
    assert skill.handler is not None
    await skill.handler(ctx, "")
    out = _render(committed[-1])
    assert "Run one with /skill:<name>." in out
    # File-sourced text: shown, not obeyed — no markup, no ESC (#179).
    assert "zz^[demo" in out and "Demo [bold]skill[/bold]" in out


# --- run_tui hands the live harness to the completer -------------------------


class _ResourceHarness(FakeHarness):
    """No EventBus: ``run_tui``'s own fallback call site installs the completer."""

    def __init__(self) -> None:
        super().__init__()
        self.skills: list[Any] = []
        self.prompt_templates: list[Any] = []


class _ModalHarness(_ResourceHarness):
    """A harness with an EventBus, so ``_wire_descriptors`` installs the completer."""

    def __init__(self) -> None:
        super().__init__()
        self.runtime = _BusExtRuntime()


def _drive(completer: Any, text: str) -> list[tuple[str, str]]:
    doc = Document(text=text, cursor_position=len(text))
    return [(c.text, c.display_meta_text) for c in completer.get_completions(doc, CompleteEvent())]


async def _menu_from_run_tui(
    harness: FakeHarness,
    text: str,
    cwd: Path,
    *,
    sm: SettingsManager | None,
    skill_control: SkillControl | None = None,
    mutate: Any = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        runtime, chrome = FakeRuntime(harness), AelixChrome()
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(cwd),
                chrome=chrome,
                install_signal_handlers=False,
                settings_manager=sm,
                skill_control=skill_control,
            )
        )
        await wait_until(lambda: chrome.app.is_running)
        await wait_until(lambda: _drive(chrome.buffer.completer, "/he") != [])
        first = _drive(chrome.buffer.completer, text)
        if mutate is not None:
            mutate()
        second = _drive(chrome.buffer.completer, text)
        pipe.send_text("/quit\n")
        await _quit_within(task)
    return first, second


@pytest.mark.parametrize("harness_type", [_ResourceHarness, _ModalHarness])
async def test_run_tui_offers_the_live_templates_and_skills(
    harness_type: type[_ResourceHarness], tmp_path: Path
) -> None:
    harness = harness_type()
    harness.skills = [_Skill("zzdemo", "Demo skill")]
    harness.prompt_templates = [_Template("zzreview", "Demo template")]
    sm = SettingsManager.in_memory({})

    def reload() -> None:
        # A /reload or panel apply replaces the lists; the next key sees it.
        harness.skills = [_Skill("zznew", "New skill")]
        harness.prompt_templates = []

    first, second = await _menu_from_run_tui(harness, "/zz", tmp_path, sm=sm, mutate=reload)
    assert first == [("/zzreview", "Demo template"), ("/skill:zzdemo", "Demo skill")]
    assert second == [("/skill:zznew", "New skill")]


@pytest.mark.parametrize("harness_type", [_ResourceHarness, _ModalHarness])
async def test_run_tui_drops_skills_while_skill_commands_are_off(
    harness_type: type[_ResourceHarness], tmp_path: Path
) -> None:
    harness = harness_type()
    harness.skills = [_Skill("zzdemo", "Demo skill")]
    harness.prompt_templates = [_Template("zzreview", "Demo template")]
    sm = SettingsManager.in_memory({})
    sm.set_enable_skill_commands(False)

    first, second = await _menu_from_run_tui(
        harness, "/zz", tmp_path, sm=sm, mutate=lambda: sm.set_enable_skill_commands(True)
    )
    assert first == [("/zzreview", "Demo template")]
    # The setting is read per keystroke: /settings turning it back on is live.
    assert second == [("/zzreview", "Demo template"), ("/skill:zzdemo", "Demo skill")]


async def test_run_tui_without_a_settings_manager_offers_skills(tmp_path: Path) -> None:
    harness = _ResourceHarness()
    harness.skills = [_Skill("zzdemo", "Demo skill")]
    first, _ = await _menu_from_run_tui(harness, "/skill:", tmp_path, sm=None)
    assert first == [("/skill:zzdemo", "Demo skill")]


async def test_run_tui_tags_a_skill_with_its_source(tmp_path: Path) -> None:
    kind = _kind(tmp_path, off=("-deploy",))
    harness = _ResourceHarness()
    # What the factory hands the harness: the enabled skills of the same catalog.
    harness.skills = list(kind._catalog().skills)
    sm = SettingsManager.in_memory({})
    control = SkillControl(
        selection=ResourceSelection(
            sm, global_base=str(tmp_path), project_base=str(tmp_path / "p"), project_trusted=True
        ),
        kind=kind,
    )
    first, _ = await _menu_from_run_tui(harness, "/skill:", tmp_path, sm=sm, skill_control=control)
    # The off skill (deploy) is not offered: /skill:deploy is refused. The order
    # is the harness's: precedence (--skill first), as the catalog keeps it.
    assert first == [
        ("/skill:zzdemo", "[--skill] zzdemo does a thing."),
        ("/skill:pdf", "[global] pdf does a thing."),
    ]


def test_every_completer_call_site_passes_the_resources() -> None:
    """The behavioural cases above drive both arms; this covers a new one.

    A call site added without ``get_resources=`` would bring the issue back for
    the harnesses that reach it, with every other case green.
    """

    tree = ast.parse(_SHELL_SRC.read_text(encoding="utf-8"), filename=str(_SHELL_SRC))
    wanted = {"_build_input_completer", "_wire_descriptors"}
    sites = [
        (node.func.id, node.lineno, any(kw.arg == "get_resources" for kw in node.keywords))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in wanted
    ]
    missing = [f"{name} at shell.py:{lineno}" for name, lineno, ok in sites if not ok]
    assert not missing, f"completer call sites without get_resources=: {missing}"
    assert len(sites) == 3, sites
