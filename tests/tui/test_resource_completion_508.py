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
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from _polling import quit_within as _quit_within  # sibling helper (prepend mode)
from _polling import wait_until
from aelix_agent_core.harness.prompt_templates import PromptTemplate
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
    _one_row,
    resource_commands,
)
from aelix_coding_agent.tui.descriptors import DescriptorRenderer
from aelix_coding_agent.tui.selection_panel import (
    SelectionModel,
    cells,
    plan_layout,
    render_panel,
)
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from tests.tui.test_run_tui_smoke import FakeHarness, FakeRuntime, _BusExtRuntime, _FakeExtRuntime

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
    modals: list[str] | None = None,
) -> list[Any]:
    resources = ResourceCommands(templates or [], skills or [])
    completer = DescriptorCommandCompleter(
        lambda: routes or {},
        builtins=_BUILTINS if builtins is None else builtins,
        get_ext_commands=lambda: list(ext or []),
        get_resources=lambda: resources,
        get_modal_commands=lambda: list(modals or []),
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
        templates=[(n, "t") for n in ("help", "tools", "deploy", "route", "panel", "ok")],
        ext=[("deploy", "ext deploy")],
        routes={"ns:route": _Route("route", "the route")},
        modals=["panel"],
    )
    meta = {c.text: c.display_meta_text for c in out}
    # The input loop's order: a built-in or its alias, an extension command and
    # a management modal each take the name before a template does. A route
    # takes none — nothing dispatches through it — so the template runs and
    # its row says so (Codex F1, F2).
    assert meta["/help"] == "List available commands"
    assert meta["/tools"] == "Same as /tool"
    assert meta["/deploy"] == "ext deploy"
    assert "/panel" not in meta
    assert meta["/route"] == "t"
    assert meta["/ok"] == "t"
    assert _texts(out).count("/deploy") == 1 and _texts(out).count("/route") == 1


def test_a_modal_holds_its_name_before_a_template() -> None:
    kw: dict[str, Any] = {
        "builtins": [],
        "templates": [("panel", "TEMPLATE panel"), ("plain", "TEMPLATE plain")],
        "modals": ["panel"],
    }
    # Enter on /panel opens the modal (codex F1: the row said TEMPLATE).
    assert [(c.text, c.display_meta_text) for c in _menu("/p", **kw)] == [
        ("/plain", "TEMPLATE plain")
    ]
    # A route that describes the modal is its row.
    routes = {"r": _Route("panel", "Open the panel")}
    assert [(c.text, c.display_meta_text) for c in _menu("/pa", routes=routes, **kw)] == [
        ("/panel", "Open the panel")
    ]


def test_a_route_never_hides_what_runs_under_its_name() -> None:
    routes = {
        "a": _Route("rt", "ROUTE rt"),
        "b": _Route("deploy", "ROUTE deploy"),
        "c": _Route("skill:x", "ROUTE skill:x"),
        "d": _Route("lone", "ROUTE lone"),
    }
    kw: dict[str, Any] = {
        "builtins": [],
        "routes": routes,
        "templates": [("rt", "TEMPLATE rt")],
        "ext": [("deploy", "EXT deploy")],
        "skills": [("x", "SKILL x", "global")],
    }

    def rows(typed: str) -> list[tuple[str, str]]:
        return [(c.text, c.display_meta_text) for c in _menu(typed, **kw)]

    # Codex F2 / verify V1: the template and the extension command run, so
    # their rows stand, with their own descriptions, and the route's goes.
    assert rows("/rt") == [("/rt", "TEMPLATE rt")]
    assert rows("/dep") == [("/deploy", "EXT deploy")]
    # Verify V4: a route of a skill line's name hid the skill found by its bare
    # name, and stood in for it under /skill:. (A real route cannot be named
    # ``skill:…`` — the payload's pattern refuses the colon — but a route
    # store is any mapping.)
    assert rows("/x") == [("/skill:x", "[global] SKILL x")]
    assert rows("/skill:") == [("/skill:x", "[global] SKILL x")]
    # A route with nothing under its name keeps its row (ADR-0107: routes are
    # metadata); Enter on it says "Unknown command", as it did before #508.
    assert rows("/lo") == [("/lone", "ROUTE lone")]


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


@pytest.mark.parametrize(("columns", "name_cells"), [(50, 16), (80, 26), (120, 30)])
def test_a_long_name_does_not_squeeze_every_description(
    monkeypatch: pytest.MonkeyPatch, columns: int, name_cells: int
) -> None:
    monkeypatch.setattr(completion_mod, "_menu_columns", lambda: columns)
    long = "a-very-long-skill-name-that-goes-on-and-on-for-sixty-chars-x"
    out = _menu("/", builtins=_BUILTINS[:1], skills=[(long, "Long name " * 12, "--skill")])
    help_row, skill_row = out
    # pi's autocomplete name column: 32 cells, the 2-cell gap included — and a
    # third of a narrow terminal at most: at 50 columns a 30-cell name left
    # the description 15 cells, ten of them the tag (verify V3).
    assert cells(skill_row.display_text) == name_cells and skill_row.display_text.endswith("…")
    assert skill_row.text == f"/skill:{long}"  # Tab still inserts the whole line
    assert help_row.display_meta_text == "List available commands"
    room = columns - (name_cells + 2) - 2 - 1
    meta = skill_row.display_meta_text
    assert meta.startswith("[--skill] Long name") and room - 1 <= cells(meta) <= room


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


def _whole_row(text: str) -> str:
    """The one-row rule applied to the whole text, the slow and obvious way."""

    def run(m: re.Match[str]) -> str:
        return " " if "\n" in m.group() or "\r" in m.group() else " " * len(m.group())

    return re.sub(r"[ \t\r\n]+", run, text).strip(" ")


def test_the_head_is_the_whole_text_cut() -> None:
    # ``_one_row`` stops once the head is decided; what it returns must be what
    # the rule over the whole text would have given, cut there.
    rng = random.Random(508)
    for _ in range(4000):
        text = "".join(rng.choice("ab \t\r\n\x1f ") for _ in range(rng.randrange(0, 40)))
        keep = rng.randrange(0, 30)
        whole = _whole_row(text)
        assert _one_row(text, keep) == (whole[:keep], len(whole) > keep), (text, keep)
    # A blank line's indentation goes with its breaks: one space, not two.
    assert _one_row("a \n \n b", 20) == ("a b", False)


def test_a_long_run_of_spaces_costs_what_the_head_costs() -> None:
    # Codex F3: ``[ \t]*[\r\n]+[ \t]*`` retried at every space of a run with no
    # break in it — 5 000, 10 000 and 20 000 spaces in one valid description
    # took 79, 287 and 1 123 ms for one key. Linear now, and only the head is
    # read: 1 000 000 spaces take a few ms here. In this order a quadratic
    # scan fails at 20 000 instead of never finishing the larger runs.
    for n in (20_000, 100_000, 1_000_000):
        description = "a" + " " * n + "b"
        completer = DescriptorCommandCompleter(
            lambda: {},
            builtins=[],
            get_resources=lambda d=description: ResourceCommands([("space", d)]),
        )
        start = time.perf_counter()
        ((text, meta),) = _drive(completer, "/space")
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"{n} spaces: {elapsed:.3f} s"
        assert text == "/space" and meta.startswith("a    ") and meta.endswith("…")


@pytest.mark.parametrize("size", [2_500, 5_000])
def test_a_large_catalog_is_drawn_once(monkeypatch: pytest.MonkeyPatch, size: int) -> None:
    # Codex F4: a 4 096-entry cache took two entries a row and evicted the
    # next scan's rows: 2 500 templates were drawn whole at every key (0 hits,
    # ~330 ms), 5 000 at ~640 ms. Counted, not timed: every call is a row drawn.
    drawn: list[str] = []
    real = completion_mod._menu_text

    def counting(text: str, width: int) -> str:
        drawn.append(text)
        return real(text, width)

    monkeypatch.setattr(completion_mod, "_menu_text", counting)
    monkeypatch.setattr(completion_mod, "_menu_columns", lambda: 80)
    templates = [(f"tpl-{i:05d}", f"Template {i}: " + "lorem ipsum " * 80) for i in range(size)]
    completer = DescriptorCommandCompleter(
        lambda: {}, builtins=[], get_resources=lambda: ResourceCommands(list(templates))
    )
    # Cold, the names are drawn and a description only when the menu reads
    # it (the first 200 for its column, then the rows on screen).
    menu = list(completer.get_completions(Document("/", 1), CompleteEvent()))
    assert len(drawn) == size
    assert all(c.display_meta_text for c in menu[:200]) and len(drawn) == size + 200
    first = _drive(completer, "/")
    assert len(first) == size and len(drawn) == 2 * size
    drawn.clear()
    assert _drive(completer, "/") == first
    assert drawn == []
    # Typing on and deleting back draws nothing again either.
    _drive(completer, "/tpl-01")
    drawn.clear()
    assert _drive(completer, "/") == first
    assert drawn == []


def test_the_drawn_rows_follow_the_catalog_and_the_width(monkeypatch: pytest.MonkeyPatch) -> None:
    columns = {"n": 120}
    monkeypatch.setattr(completion_mod, "_menu_columns", lambda: columns["n"])
    old = "old description " * 20
    templates = [("zzreview", old)]
    completer = DescriptorCommandCompleter(
        lambda: {}, builtins=[], get_resources=lambda: ResourceCommands(list(templates))
    )
    ((_, wide),) = _drive(completer, "/zz")
    columns["n"] = 50
    ((_, narrow),) = _drive(completer, "/zz")
    # The name column "zzreview" + 2, the description's 2 cells and the scrollbar.
    assert cells(narrow) == 50 - 10 - 2 - 1 < cells(wide)
    # A /reload that edits the description: the new one is drawn, and nothing
    # drawn for the old catalog is kept.
    templates[0] = ("zzreview", "new description")
    assert _drive(completer, "/zz") == [("/zzreview", "new description")]
    assert old not in {text for text, _ in completer._drawn}


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


@pytest.mark.parametrize("description", [123, None, ["a"], b"text"])
def test_an_extension_description_that_is_not_text_is_drawn_empty(description: Any) -> None:
    # #508 R1V1 — ``register_command`` is typed ``description: str | None`` and
    # nothing holds an extension to it. ``description=123`` raised from the
    # menu's lazy description at every redraw, and the screen stayed on
    # "Press ENTER to continue".
    completer = DescriptorCommandCompleter(
        lambda: {},
        builtins=[],
        get_ext_commands=lambda: [("zbad", description), ("zbok", "fine")],
    )
    rows, _ = completer._rows("zb")
    assert [meta for _, _, meta in rows] == ["", "fine"]
    out = list(completer.get_completions(Document("/zb", 3), CompleteEvent()))
    for _ in range(2):  # read again at every redraw
        assert [(c.display_text, c.display_meta_text) for c in out] == [
            ("zbad", ""),
            ("zbok", "fine"),
        ]


def test_no_source_description_breaks_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    # The lazy description is read inside the render, where a raise repeats at
    # every redraw: one that is not text, or that fails to draw, is drawn empty
    # whichever source wrote it, and the rest of the menu is drawn as before.
    real = completion_mod._menu_text

    def menu_text(text: str, width: int) -> str:
        if text.startswith("boom"):
            raise RuntimeError(text)
        return real(text, width)

    monkeypatch.setattr(completion_mod, "_menu_text", menu_text)
    number: Any = 123
    listed: Any = ["not", "text"]
    out = _menu(
        "/z",
        builtins=[],
        routes={"ns:zr": SimpleNamespace(command="zr", description=number)},
        templates=[("zt", number), ("ztboom", "boom template"), ("ztok", "fine")],
        ext=[("ze", "boom extension")],
        skills=[("zs", listed, "")],
    )
    for _ in range(2):  # read again at every redraw
        assert [(c.display_text, c.display_meta_text) for c in out] == [
            ("zr", ""),
            ("zt", ""),
            ("ztboom", ""),
            ("ztok", "fine"),
            ("ze", ""),
            ("skill:zs", ""),
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


_RUN_LINE = "Run one with /skill:<name>; /skill: lists them."


def test_the_panel_says_how_to_run_a_skill(tmp_path: Path) -> None:
    on = _kind(tmp_path).notices()
    assert _RUN_LINE in on
    off = _kind(tmp_path, enabled=False).notices()
    assert "/skill:<name> is turned off in settings (enableSkillCommands)." in off
    assert _RUN_LINE not in off


def test_the_panel_line_fits_a_50_column_terminal(tmp_path: Path) -> None:
    # Verify V3: the first wording was cut at 50 columns ("…; type /s…").
    kind = _kind(tmp_path)
    snapshot = ResourceSelection(
        SettingsManager.in_memory({}),
        global_base=str(tmp_path),
        project_base=str(tmp_path / "p"),
        project_trusted=True,
    ).snapshot(kind)
    model = SelectionModel(snapshot, "Skills", ("skill", "skills"))
    rows = [
        re.sub(r"\x1b\[[0-9;]*m", "", r) for r in render_panel(model, plan_layout(model, 50, 30))
    ]
    assert f" {_RUN_LINE}" in rows
    assert all(cells(r) <= 50 for r in rows)


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


# --- Enter runs what the row says (Codex F1/F2, verify V1) -------------------


class _DescriptorRuntime(_FakeExtRuntime):
    """An EventBus whose probe answers with the given descriptors."""

    def __init__(self, modules: list[dict[str, Any]]) -> None:
        super().__init__()
        from aelix_coding_agent.extensions.api import EventBus

        self.event_bus = EventBus()
        self.event_bus.on("ui:list-modules", lambda probe: probe.modules.extend(modules))


class _ExtRunner:
    """One extension command per name; each run is recorded."""

    def __init__(self, names: list[str], ran: list[str]) -> None:
        def handler(name: str) -> Any:
            async def run(_args: str, _ctx: object) -> None:
                ran.append(name)

            return run

        self._commands = {
            name: SimpleNamespace(
                invocation_name=name,
                command=SimpleNamespace(description=f"EXT {name}", handler=handler(name)),
            )
            for name in names
        }

    def get_registered_commands(self) -> list[Any]:
        return list(self._commands.values())

    def get_command(self, name: str) -> Any:
        return self._commands.get(name)


class _CollisionHarness(FakeHarness):
    def __init__(
        self, *, routes: list[str], modals: list[str], ext: list[str], templates: list[str]
    ) -> None:
        super().__init__()
        self.ran: list[str] = []
        modules: list[dict[str, Any]] = [
            {
                "kind": "command-route",
                "namespace": "t508",
                "id": f"route-{name}",
                "payload": {
                    "kind": "command-route",
                    "command": name,
                    "description": f"ROUTE {name}",
                },
            }
            for name in routes
        ] + [
            {
                "kind": "management-modal",
                "namespace": "t508",
                "id": f"modal-{name}",
                "payload": {
                    "kind": "management-modal",
                    "command": name,
                    "title": f"MODAL {name}",
                    "view": "form",
                },
            }
            for name in modals
        ]
        self.runtime = _DescriptorRuntime(modules)
        self.extension_runner = _ExtRunner(ext, self.ran)
        self.prompt_templates = [
            PromptTemplate(name, f"TEMPLATE {name}", f"TEMPLATE-BODY-{name}") for name in templates
        ]
        self.skills: list[Any] = []

    def make_command_context(self, **_kw: Any) -> object:
        return SimpleNamespace()


async def test_enter_runs_what_the_row_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _CollisionHarness(
        routes=["zrt", "zvp", "zex", "zlone"],
        modals=["zmo", "zvp"],
        ext=["zex"],
        templates=["zrt", "zmo", "zvp", "zex", "zplain"],
    )
    opened: list[str] = []
    monkeypatch.setattr(
        DescriptorRenderer, "open_modal", lambda _self, env: opened.append(env.payload.command)
    )
    names = ["zrt", "zmo", "zvp", "zex", "zlone", "zplain"]
    outcome: dict[str, tuple[str | None, str | None]] = {}
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        runtime, chrome = FakeRuntime(harness), AelixChrome()
        task = asyncio.ensure_future(
            run_tui(
                runtime,  # type: ignore[arg-type]
                cwd=str(tmp_path),
                chrome=chrome,
                install_signal_handlers=False,
            )
        )
        await wait_until(lambda: chrome.app.is_running)
        await wait_until(lambda: _drive(chrome.buffer.completer, "/zl") != [])  # probe landed
        menu = dict(_drive(chrome.buffer.completer, "/z"))
        for name in names:
            before = (len(harness.prompts), len(opened), len(harness.ran))
            pipe.send_text(f"/{name}\n")
            pipe.send_text(f"after {name}\n")
            await wait_until(lambda n=name: (f"after {n}", "interactive") in harness.prompts)
            prompts = [t for t, _ in harness.prompts[before[0] :] if not t.startswith("after ")]
            ran = (
                ["template"] * (prompts == [f"TEMPLATE-BODY-{name}"])
                + ["modal"] * (opened[before[1] :] == [name])
                + ["extension"] * (harness.ran[before[2] :] == [name])
            )
            assert len(ran) <= 1 and (prompts == [] or ran == ["template"]), (name, prompts, ran)
            outcome[name] = (menu.get(f"/{name}"), ran[0] if ran else None)
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert outcome == {
        "zrt": ("TEMPLATE zrt", "template"),  # a route alone takes no name
        "zmo": (None, "modal"),  # a modal holds its name; no row stands for it
        "zvp": ("ROUTE zvp", "modal"),  # the route describes the modal
        "zex": ("EXT zex", "extension"),  # the extension's own row, not the route's
        "zlone": ("ROUTE zlone", None),  # nothing runs: "Unknown command" (ADR-0107)
        "zplain": ("TEMPLATE zplain", "template"),
    }


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
