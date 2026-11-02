"""#510 (ADR-0283 §6) — the shared panel's new pieces, as ``/tool`` draws them.

Lock tags and kinds pick the status word and the state line; placeholder and
info rows are not counted, saved or toggled; a header never stands alone at
the BOTTOM of the window either (#509 verify R5); a tool an extension turned
off reads "off now"; an effect of ``reload`` words pending rows for #25; and
the panel body's key outcomes are its own, apart from the host.
"""

from __future__ import annotations

import dataclasses
import re
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.core.resource_selection import (
    ItemState,
    Lock,
    ResourceItem,
    ScopeStatus,
    Snapshot,
)
from aelix_coding_agent.tui.commands import BUILTIN_COMMANDS, CommandContext, match_command
from aelix_coding_agent.tui.selection_panel import (
    SelectionModel,
    SelectionPanelBody,
    cells,
    plan_layout,
    render_panel,
)

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _plain(rows: list[str]) -> list[str]:
    return [_SGR.sub("", r) for r in rows]


def _tool(name: str, group: str = "builtin", label: str = "BUILT-IN", **kw: Any) -> ResourceItem:
    return ResourceItem(
        kind="tool",
        key=kw.pop("key", name),
        label=name,
        identities=kw.pop("identities", (name,)),
        description=kw.pop("description", f"{name} things"),
        group=group,
        group_label=label,
        **kw,
    )


def _snap(states: list[ItemState], notices: tuple[str, ...] = ()) -> Snapshot:
    return Snapshot(
        "tool",
        tuple(states),
        {
            "global": ScopeStatus("global", "/g/settings.json", True),
            "project": ScopeStatus("project", "/p/.aelix/settings.json", True),
        },
        notices=notices,
    )


def _panel_states() -> list[ItemState]:
    excluded = Lock(False, "--exclude-tools bash (this run only)", tag="excluded", kind="cli")
    named = Lock(True, "named by --tools (this run only)", tag="--tools", kind="cli")
    server = Lock(
        False,
        'see /mcp · "enabled": false in ~/.aelix/agent/mcp.json',
        tag="server off",
        kind="unavailable",
    )
    return [
        ItemState(_tool("read"), True, True, None, named),
        ItemState(_tool("bash"), True, True, None, excluded),
        ItemState(_tool("edit"), False, True, "project", named),  # saved off, locked on
        ItemState(_tool("grep"), False, True, "global"),  # off · pending
        ItemState(_tool("find"), True, True, None, live=False),  # an extension narrowed it
        ItemState(
            _tool(
                "github",
                "mcp:github",
                "MCP  github",
                key="mcp:github",
                identities=(),
                role="placeholder",
                description="server off: no tools this session",
            ),
            True,
            False,
            None,
            server,
        ),
    ]


def _model(states: list[ItemState] | None = None) -> SelectionModel:
    return SelectionModel(_snap(states or _panel_states()), "Tools", ("tool", "tools"))


def _frame(model: SelectionModel, width: int = 80, rows: int = 17) -> list[str]:
    return _plain(render_panel(model, plan_layout(model, width, rows)))


def _select(model: SelectionModel, key: str) -> None:
    model.cursor = [s.item.key for s in model.visible()].index(key)


def test_the_status_column_shows_each_locks_tag_and_placeholders_are_not_counted() -> None:
    model = _model()
    frame = _frame(model, 80, 24)
    text = "\n".join(frame)
    assert "excluded" in text and "--tools" in text and "server off" in text
    assert "( ) bash" in text and "(✓) read" in text and "( ) github" in text
    assert "off · pending" in text and "off now" in text
    # Five tools (the github placeholder is not one), one off, three locked.
    assert "5 tools · 1 off · 3 locked" in frame[1]


def test_a_search_counts_matching_tools_not_placeholder_rows() -> None:
    """TUI sub-lane: "N matches" counts as "N tools" does — the github placeholder is no tool."""

    model = _model()
    model.search = "server off"  # only the placeholder's description has it
    assert "0 matches" in _frame(model, 80, 24)[1]
    model.search = "i"  # every "<name> things" description, and the github placeholder
    frame = _frame(model, 80, 24)
    assert "5 matches" in frame[1] and "( ) github" in "\n".join(frame)


@pytest.mark.parametrize(
    ("key", "line"),
    [
        ("bash", "Locked off: --exclude-tools bash (this run only)"),
        ("read", "Locked on: named by --tools (this run only)"),
        ("edit", "Saved off (this project's file) · Locked on: named by --tools"),
        ("mcp:github", 'Unavailable: see /mcp · "enabled": false in ~/.aelix/agent/mcp.json'),
        ("find", "Saved on · now off (turned off this run)"),
        ("grep", "Saved off · now on · Enter applies the saved value"),
    ],
)
def test_the_state_line_follows_the_lock_kind(key: str, line: str) -> None:
    model = _model()
    _select(model, key)
    assert any(line in row for row in _frame(model, 120, 24)), _frame(model, 120, 24)


@pytest.mark.parametrize("width", [50, 80])
def test_where_to_manage_an_unavailable_row_survives_a_narrow_terminal(width: int) -> None:
    """Verify V5: the pointer comes first, so a long config path is what the edge cuts.

    The reasons are :class:`ToolKind`'s own, for a config outside the home
    directory (an ``AELIX_CODING_AGENT_DIR`` elsewhere is printed in full).
    """

    from types import SimpleNamespace

    from aelix_coding_agent.core.tool_selection import ToolKind

    long_path = "/tmp/a-scratch-home-directory/agent/mcp.json"
    kind = ToolKind(
        harness=lambda: None,
        builtin=frozenset(),
        mcp_statuses=lambda: [
            SimpleNamespace(name="github", state="disabled", config=long_path),
            SimpleNamespace(name="jira", state="failed", config=None),
        ],
        extension_errors=lambda: [
            SimpleNamespace(path="/x/broken.py", error="RuntimeError: " + "boom " * 20)
        ],
    )
    locks = kind.locks()
    rows = []
    for key, label in (
        ("mcp:github", "github"),
        ("mcp:jira", "jira"),
        ("ext-failed:broken.py", "broken.py"),
    ):
        item = _tool(label, key, label.upper(), key=key, identities=(), role="placeholder")
        rows.append(ItemState(item, True, False, None, locks[key]))
    model = _model([ItemState(_tool("read"), True, True, None), *rows])
    for key, pointer in (
        ("mcp:github", "Unavailable: see /mcp ·"),
        ("mcp:jira", "Unavailable: see /mcp ·"),
        ("ext-failed:broken.py", "Unavailable: see /extension ·"),
    ):
        _select(model, key)
        frame = _frame(model, width, 24)
        assert all(len(r) <= width for r in frame)
        assert any(pointer in row for row in frame), (key, frame)


def test_a_locked_on_tool_an_extension_narrowed_says_it_is_off_now() -> None:
    named = Lock(True, "named by --tools (this run only)", tag="--tools", kind="cli")
    model = _model([ItemState(_tool("bash"), True, True, None, named, live=False)])
    assert any(
        "Locked on: named by --tools (this run only) · off now (turned off this run)" in r
        for r in _frame(model, 120, 24)
    )


def test_space_on_a_locked_or_unavailable_row_says_why_and_changes_nothing() -> None:
    model = _model()
    _select(model, "bash")
    assert model.toggle() == "bash is locked: --exclude-tools bash (this run only)"
    _select(model, "mcp:github")
    refusal = model.toggle()
    assert refusal is not None and refusal.startswith("github is unavailable:")
    assert model.edits == {}
    _select(model, "grep")
    assert model.toggle() is None and model.edits == {"grep": True}


def test_an_info_row_draws_a_dot_and_is_status_only() -> None:
    info = ItemState(
        _tool("demo", "mcp-servers", "MCP SERVERS", key="srv:demo", identities=(), role="info"),
        True,
        True,
        None,
    )
    model = _model([ItemState(_tool("read"), True, True, None), info])
    frame = "\n".join(_frame(model))
    assert " ●  demo" in frame
    _select(model, "srv:demo")
    assert model.toggle() is not None and model.edits == {}
    assert model.counts() == (1, 0, 0)


def _many_groups() -> SelectionModel:
    states = []
    for g in range(6):
        for i in range(g % 3 + 1):
            item = _tool(f"g{g}t{i}", f"g{g}", f"EXTENSION  pack-{g}")
            states.append(ItemState(item, True, True, None))
    return _model(states)


@pytest.mark.parametrize("viewport_rows", [2, 3, 4, 5, 6, 7])
def test_no_header_stands_alone_at_the_top_or_the_bottom(viewport_rows: int) -> None:
    """#509 verify R5: a header on the last row with its rows below the window."""

    model = _many_groups()
    layout = plan_layout(model, 80, 17)
    layout = dataclasses.replace(layout, height=layout.fixed_rows + viewport_rows, banner_rows=0)
    total = len(model.states)
    heights = set()
    for step in range(2 * total + 2):
        frame = _plain(render_panel(model, layout))
        heights.add(len(frame))
        body = frame[2 : 2 + viewport_rows]
        kinds = ["h" if r.startswith(" EXTENSION") else "i" if r.strip() else "" for r in body]
        last_item = max((i for i, k in enumerate(kinds) if k), default=-1)
        assert kinds[0] in ("h", ""), body
        for top, below in zip(kinds, kinds[1:], strict=False):
            assert not (top == "h" and below in ("h", "")), body
        assert kinds[last_item] != "h", body
        cursor = model.visible()[model.cursor].item.key
        assert any(f"▸ [✓] {cursor}" in r for r in body), (cursor, body)
        model.move(1 if step <= total else -1)
    assert heights == {layout.height}


@pytest.mark.parametrize(("width", "rows"), [(50, 24), (80, 24), (120, 40)])
def test_no_row_is_wider_than_the_width_with_cjk_and_hostile_text(width: int, rows: int) -> None:
    states = _panel_states() + [
        ItemState(
            _tool(
                "날씨_도구",
                "ext:0",
                "EXTENSION  날씨.py",
                description="서울의 날씨 \x1b[31m를 본다",
            ),
            True,
            True,
            None,
        ),
    ]
    model = _model(states)
    for key in [s.item.key for s in model.visible()]:
        _select(model, key)
        frame = render_panel(model, plan_layout(model, width, rows - 7))
        assert len(frame) == plan_layout(model, width, rows - 7).height
        for row in frame:
            assert cells(_SGR.sub("", row)) <= width, (width, row)
            assert "\x1b[31m를" not in row


def test_a_reload_effect_words_pending_rows_for_reload() -> None:
    model = _model([ItemState(_tool("pack"), False, True, None)])
    model.effect = "reload"
    frame = "\n".join(_frame(model, 120))
    assert "off · /reload" in frame and "/reload applies it" in frame


# --- the body, apart from its host ----------------------------------------------


class _Kind:
    kind = "tool"
    settings_key = "defaultTools"
    effect = "next_request"

    def items(self) -> list[ResourceItem]:
        return [s.item for s in _panel_states()]

    def locks(self) -> dict[str, Lock]:
        return {s.item.key: s.lock for s in _panel_states() if s.lock is not None}

    def applied(self) -> set[str]:
        return {"read", "bash", "edit", "grep", "find"}

    def notices(self) -> list[str]:
        return ["--tools: only the tools it names can be on this run."]

    def busy(self) -> bool:
        return False

    async def wait_until_idle(self) -> None:
        return None


class _Selection:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def snapshot(self, kind: Any) -> Snapshot:
        return _snap(_panel_states(), tuple(kind.notices()))

    def needs_new_project_file(self, scope: str, edits: Any) -> bool:
        return False

    async def save(self, kind: Any, scope: str, edits: Any) -> Any:
        self.saved.append(dict(edits))
        return SimpleNamespace(ok=False, error="disk full", written=(), changed=(), mismatches=())


async def test_the_body_claims_enter_keeps_a_failed_save_open_and_discards_on_esc() -> None:
    selection = _Selection()
    body = SelectionPanelBody(selection, _Kind(), title="Tools", noun=("tool", "tools"))  # type: ignore[arg-type]
    body.plan(80, 17)
    assert body.handle("down") == "handled"
    _select(body.model, "grep")
    assert body.handle("space") == "handled" and body.dirty
    assert body.handle("enter") == "save" and body.busy
    assert body.handle("escape") == "ignored"  # buffered after Enter: refused (X5)
    assert await body.save() is None  # the failed save stays open with its edit
    assert selection.saved == [{"grep": True}] and body.dirty and not body.busy
    assert body.handle("x", "x") == "handled" and body.model.search == "x"
    assert body.handle("escape") == "close"
    assert "1 unsaved change discarded" in str(body.discard_lines()[0])
    rows = body.render(80, body.layout.height if body.layout else 17)
    assert len(rows) == (body.layout.height if body.layout else 17)
    assert "--tools: only the tools it names" in "\n".join(_plain(rows))


def test_too_small_refuses_every_key_but_escape() -> None:
    body = SelectionPanelBody(_Selection(), _Kind(), title="Tools")  # type: ignore[arg-type]
    body.plan(80, 17)
    for key in ("space", "down", "enter", "left"):
        assert body.handle(key, too_small=True) == "ignored"
    assert body.handle("escape", too_small=True) == "close"


# --- /tool and its alias ------------------------------------------------------------


def test_tool_is_the_command_and_tools_its_alias() -> None:
    assert match_command("/tool", BUILTIN_COMMANDS) is match_command("/tools", BUILTIN_COMMANDS)
    assert match_command("/tool", BUILTIN_COMMANDS).name == "tool"  # type: ignore[union-attr]


async def test_both_names_open_the_panel_and_fall_back_to_the_list_without_one() -> None:
    opened: list[str] = []
    committed: list[Any] = []

    async def _panel() -> None:
        opened.append("panel")

    command = match_command("/tools", BUILTIN_COMMANDS)
    assert command is not None and command.handler is not None
    harness: Any = SimpleNamespace(
        _action_get_all_tools=lambda: [SimpleNamespace(name="read", description="r")],
        _action_get_active_tools=lambda: ["read"],
    )
    chrome: Any = None
    ctx = CommandContext(
        harness=harness, chrome=chrome, cwd=".", commit=committed.append, tool_panel=_panel
    )
    await command.handler(ctx, "")
    assert opened == ["panel"]
    bare = CommandContext(harness=harness, chrome=chrome, cwd=".", commit=committed.append)
    await command.handler(bare, "")
    assert committed, "the headless fallback lists the active tools"


# --- verify V10 and V12 -------------------------------------------------------------


def test_a_settings_path_is_shown_with_home_and_broken_at_a_separator() -> None:
    """V12: at 50 columns the summary cut a path mid-name ("…/agent/setti" / "ngs.json")."""

    import os

    from aelix_coding_agent.core.resource_selection import ApplyResult, SaveResult
    from aelix_coding_agent.tui.selection_panel import summary

    home_path = os.path.join(os.path.expanduser("~"), ".aelix", "agent", "settings.json")
    saved = SaveResult(True, None, (home_path,), (("edit", False),), ())
    applied = ApplyResult(True, turned_off=("edit",))
    text = summary("Tools", saved, applied, effect_note="From your next message.", width=50)
    first = text.plain.splitlines()[0]
    assert first == "✓ Tools saved to " + os.path.join("~", ".aelix", "agent", "settings.json")
    # Outside the home directory the path stays whole and breaks only after a separator.
    far = os.sep + os.path.join(
        "tmp", "510-verify", "tui", "scratch-plain-50x24", "agent", "settings.json"
    )
    rows = summary(
        "Tools", SaveResult(True, None, (far,), (), ()), None, effect_note="", width=50
    ).plain.splitlines()
    assert all(cells(r) <= 50 for r in rows)
    pieces = [r.strip() for r in rows[1:]]
    assert "".join(pieces) == far and all(p.endswith(os.sep) for p in pieces[:-1])
    assert pieces[-1] == "settings.json"


@pytest.mark.parametrize(("width", "word"), [(50, "(Project)"), (80, "(This project)")])
def test_the_scope_word_on_the_state_line_fits_a_narrow_panel(width: int, word: str) -> None:
    """V12: "(This project)" was cut to "(This projec…" at 50 columns; the title row says Project."""

    model = _model(
        [ItemState(_tool("find"), True, True, None), ItemState(_tool("ls"), True, True, None)]
    )
    model.target = "project"
    _select(model, "find")
    assert model.toggle() is None
    frame = _frame(model, width, 24)
    state = next(r for r in frame if r.startswith(" Saved on"))
    assert state.rstrip().endswith(f"off after Enter {word}"), state


def test_the_summary_shortens_a_path_under_a_symlinked_home(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify r1 R4: HOME under macOS /tmp (/private/tmp) left the resolved project path long."""

    import os

    from aelix_coding_agent.tui.selection_panel import _home_short

    from tests.env_sandbox import sandbox_home

    real = tmp_path / "real"
    (real / "home").mkdir(parents=True)
    try:
        os.symlink(real, tmp_path / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot make a directory symlink here")
    home = str(sandbox_home(monkeypatch, tmp_path / "link" / "home"))
    resolved = os.path.join(os.path.realpath(home), "work", ".aelix", "settings.json")
    assert resolved != os.path.join(home, "work", ".aelix", "settings.json")
    assert _home_short(resolved) == os.path.join("~", "work", ".aelix", "settings.json")
    assert _home_short(os.path.join(home, "s.json")) == os.path.join("~", "s.json")
    assert _home_short(str(tmp_path / "elsewhere.json")) == str(tmp_path / "elsewhere.json")


def _target(text: str, word: str) -> str:
    return text.replace("This project", word)


_REFUSED = (
    "✖ Not saved: weather: save it to This project instead — on Global it would also turn "
    "bash, edit, find, grep, ls, read, write on here (a project [] means no built-ins only "
    "while Global has no list)"
)


@pytest.mark.parametrize("width", [50, 80, 120])
def test_a_refused_saves_reason_is_wrapped_in_the_detail_rows(width: int) -> None:
    """Verify r1 R1: the reason was cut at every size; "This project" is "Project" below 60."""

    model = _model()
    _select(model, "grep")
    plain = _frame(model, width, 24)
    model.message = ("error", _REFUSED)
    frame = _frame(model, width, 24)
    assert len(frame) == len(plain)  # the height does not change
    assert all(cells(r) <= width for r in frame)
    rule = max(i for i, r in enumerate(frame) if r and set(r) == {"─"})
    detail = frame[rule + 1 : -1]
    assert detail[0].startswith(" grep")  # the row's name stays
    shown = " ".join(r.strip() for r in detail[1:] if r.strip())
    word = "Project" if width < 60 else "This project"
    assert shown.startswith(f"✖ Not saved: weather: save it to {word} instead — "), shown
    assert shown == _target(_REFUSED, word)  # every word of it, at 50 columns too
    # A reason that fits one row stays on the state line, below the description.
    model.message = ("error", "✖ Not saved: weather: save it to This project instead")
    short = _frame(model, width, 24)
    assert short[:-2] == plain[:-2]
    assert short[-2].strip() == f"✖ Not saved: weather: save it to {word} instead"


async def test_a_deferred_apply_that_moved_nothing_leaves_no_line(tmp_path: Any) -> None:
    """V10: a save during a turn the files undo before it ends left a bare "applied after the turn"."""

    import asyncio

    from tests.tui.test_selection_panel_lifecycle_511 import (
        _Chrome,
        _open,
        _press,
        _setup,
    )
    from tests.tui.test_selection_panel_lifecycle_511 import (
        _model as _lifecycle_model,
    )

    selection, kind, storage = _setup(tmp_path, None)
    harness = kind._harness()
    harness.is_idle = False
    idle = asyncio.Event()

    async def _wait_for_idle() -> None:
        await idle.wait()

    harness.wait_for_idle = _wait_for_idle
    chrome = _Chrome()
    task, commits = await _open(chrome, selection, kind)
    model = _lifecycle_model(chrome)
    model.cursor = [s.item.key for s in model.visible()].index("pdf")
    await _press(chrome, "space")
    await _press(chrome, "enter")
    await task
    printed = [e for e in chrome.events if isinstance(e, tuple)]
    assert any("applies when it ends" in line for line in printed[-1][1][0].splitlines())
    # Before the turn ends the file says pdf is on again: nothing is left to apply.
    storage.with_lock("global", lambda _c: "{}")
    harness.is_idle = True
    idle.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
    assert not any("applied after the turn" in getattr(c, "plain", str(c)) for c in commits)
