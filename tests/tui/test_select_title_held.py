"""#399: an extension's ``select`` title and ``confirm`` message wrap, and an
approving key waits until all of it has been drawn.

pi's ``examples/extensions/permission-gate.ts`` asks through ``ctx.ui.select``
with the whole command in the title. Ported (``.omc/probes/399-live/impl/kit``),
aelix drew that title as one row cut at the screen's edge with no marker, and
Enter ran the command whose tail was never on screen (#389 verify round 1,
reproduced on ``8f7d98aa``). pi's ``ExtensionSelectorComponent`` wraps the title
(``extension-selector.ts`` line 48).

#389's round 2 tried this and was reverted: a short-title picker whose options
were taller than the modal never took Enter (the title was withheld and so
never "seen"), keys typed before the first paint were taken when the title was
predicted to fit, a mutant holding only option 0 passed every test, and a CR in
a title was drawn as a space. Each of those has a row here.

These rows paint the REAL control with prompt-toolkit's own ``write_to_screen``
and press keys through the dialog's own key bindings.
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from typing import Any

import aelix_coding_agent.tui.context as context_mod
import pytest
from aelix_coding_agent.tui.context import AelixTUIContext
from prompt_toolkit.data_structures import Size

_TAIL = "TAIL_MARKER_399"
_COMMAND = "true " + " ".join(f"arg{i:03d}" for i in range(400)) + f" ; echo {_TAIL} >> out.txt"
#: pi's permission-gate.ts title (its ⚠️ left out: pyte and prompt-toolkit
#: disagree about VS16, which is not what these rows are about).
_GATE_TITLE = f"Dangerous command:\n\n  {_COMMAND}\n\nAllow?"


class _Chrome:
    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self.app = types.SimpleNamespace(
            output=types.SimpleNamespace(get_size=lambda: Size(rows=rows, columns=cols))
        )
        self.buffer = types.SimpleNamespace(text="")
        self.invalidated = 0

    def invalidate(self) -> None:
        self.invalidated += 1


class _Dialog:
    """``select`` / ``confirm`` held open with a fake ``show_modal``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.box: dict[str, Any] = {}
        self.screens: list[str] = []
        self.chrome = _Chrome()

        async def show_modal(_chrome: Any, build: Any, **_kw: Any) -> Any:
            result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self.box["window"] = build(result)
            self.box["result"] = result
            return await result

        monkeypatch.setattr(context_mod, "show_modal", show_modal)
        self.ctx = AelixTUIContext.__new__(AelixTUIContext)
        self.ctx.chrome = self.chrome  # type: ignore[assignment]

    async def open(self, coro: Any) -> None:
        self.task = asyncio.ensure_future(coro)
        for _ in range(50):
            if "window" in self.box:
                return
            await asyncio.sleep(0)
        raise AssertionError("the dialog never opened")

    def paint(self, width: int, height: int) -> str:
        from prompt_toolkit.layout.containers import to_container
        from prompt_toolkit.layout.mouse_handlers import MouseHandlers
        from prompt_toolkit.layout.screen import Screen, WritePosition

        # A real Application bumps ``render_counter`` on every paint, which is
        # what a ``FormattedTextControl`` (aelix's own pickers) caches by; the
        # DummyApplication here never does, so its cache is dropped instead.
        cache = getattr(self.box["window"].content, "_fragment_cache", None)
        if cache is not None:
            cache.clear()
        screen = Screen()
        to_container(self.box["window"]).write_to_screen(
            screen, MouseHandlers(), WritePosition(0, 0, width, height), "", False, None
        )
        self.screen = screen
        self.size = (width, height)
        text = "\n".join(
            "".join(screen.data_buffer[y][x].char for x in range(width)).rstrip()
            for y in range(height)
        )
        self.screens.append(text)
        return text

    def cells(self) -> list[list[tuple[str, str]]]:
        width, height = self.size
        return [
            [
                (self.screen.data_buffer[y][x].char, self.screen.data_buffer[y][x].style)
                for x in range(width)
            ]
            for y in range(height)
        ]

    def reverse_runs(self) -> list[str]:
        runs: list[str] = []
        for row in self.cells():
            run = ""
            for char, style in row:
                if "reverse" in style.split():
                    run += char
                elif run:
                    runs.append(run)
                    run = ""
            if run:
                runs.append(run)
        return runs

    def press(self, key: str) -> None:
        target = {"enter": "c-m", "space": " ", "backspace": "c-h"}.get(key, key)
        kb = self.box["window"].content.key_bindings
        for binding in kb.bindings:
            if tuple(getattr(k, "value", str(k)) for k in binding.keys) == (target,):
                binding.handler(types.SimpleNamespace(data=key))
                return
        raise AssertionError(f"no binding for {key}")

    @property
    def answered(self) -> bool:
        return bool(self.box["result"].done())

    async def answer(self) -> Any:
        return await asyncio.wait_for(self.task, timeout=2)

    def type_text(self, text: str) -> None:
        """Keys with no binding of their own reach the type-to-filter ``<any>``."""

        kb = self.box["window"].content.key_bindings
        (binding,) = [
            b for b in kb.bindings if tuple(getattr(k, "value", str(k)) for k in b.keys) == ("<any>",)
        ]
        for ch in text:
            binding.handler(types.SimpleNamespace(data=ch))

    def page_until_released(self, width: int, height: int, held: str, limit: int = 80) -> int:
        presses = 0
        while f"{held} held" in self.screens[-1]:
            assert presses < limit, f"still held after {limit} pages"
            self.press("pagedown")
            self.paint(width, height)
            presses += 1
        return presses

    async def close(self) -> None:
        if not self.answered:
            self.press("escape")
        await self.answer()


def _own(own: bool) -> dict[str, bool]:
    return {"own": True} if own else {}


# === the title: named, wrapped, nothing dropped ================================


def test_a_title_that_fits_and_names_nothing_is_returned_as_it_was() -> None:
    from aelix_coding_agent.tui.context import _title_rows

    assert _title_rows("Select Model", 80) == ["Select Model"]
    assert _title_rows("a\n\nb", 80) == ["a", "", "b"]
    assert _title_rows("", 80) == [""]


@pytest.mark.parametrize("width", [20, 40, 80, 120])
def test_a_long_title_wraps_keeps_every_character_and_fits_the_width(width: int) -> None:
    from aelix_coding_agent.tui.context import _title_rows
    from aelix_coding_agent.tui.width import cells_at_most

    rows = _title_rows(_COMMAND, width)
    assert len(rows) > 1
    assert all(cells_at_most(row) <= width for row in rows)
    assert "".join(rows) == _COMMAND
    assert any(_TAIL in row for row in rows), "a word is split across rows"


@pytest.mark.parametrize(
    ("char", "name"),
    [
        ("\r", "^M"),
        ("\x1b", "^["),
        ("\t", "^I"),
        ("\x07", "^G"),
        ("\x7f", "^?"),
        ("\x9b", "<U+009B>"),
        ("‮", "<U+202E>"),
    ],
)
async def test_a_control_character_in_a_title_is_drawn_by_name_in_reverse_video(
    monkeypatch: pytest.MonkeyPatch, char: str, name: str
) -> None:
    """Codex on #389 round 2: a CR in a title was drawn as a space, so
    ``echo a\\recho b`` and ``echo a echo b`` looked alike. Each character the
    approval prompt names is named here too, by the same function."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(f"run a{char}b now", ["Yes", "No"]))
    screen = dialog.paint(80, 10)
    assert f"a{name}b" in screen
    assert dialog.reverse_runs() == [name]
    await dialog.close()


async def test_a_title_cannot_hide_its_own_tail_with_an_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(f"echo a;\x1b[8m echo {_TAIL}", ["Yes", "No"]))
    screen = dialog.paint(80, 10)
    assert f"^[[8m echo {_TAIL}" in screen
    await dialog.close()


@pytest.mark.parametrize(
    "title", ["Theme", "That session is already open in another terminal:\n  /a/b", "a\nb\nc"]
)
@pytest.mark.parametrize("height", [14, 6, 4, 3])
async def test_a_title_that_fits_paints_exactly_as_the_legacy_frame(
    monkeypatch: pytest.MonkeyPatch, title: str, height: int
) -> None:
    """A title aelix's pickers pass (it fits, it names nothing) is drawn cell for
    cell as ``_picker_frame`` drew it in a plain ``FormattedTextControl`` — also
    when the options and detail under it are taller than the modal, where the
    rows below are cut from the bottom as they always were (#389 round 2 withheld
    the title there instead)."""

    from aelix_coding_agent.tui.context import (
        _PICK_DIM,
        _PICK_RST,
        _PICK_SEL,
        _picker_frame,
        _visible_len,
    )
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.containers import to_container
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.mouse_handlers import MouseHandlers
    from prompt_toolkit.layout.screen import Screen, WritePosition

    hint = "↑/↓ move · type to filter · Enter select · Esc cancel"
    detail = [f"detail {k}" for k in range(6)]
    dialog = _Dialog(monkeypatch)
    await dialog.open(
        dialog.ctx.select(title, ["red", "green"], detail=lambda _i: detail, own=True)
    )
    dialog.paint(80, height)
    got = dialog.cells()
    body = [
        f"{_PICK_SEL}▸ red{_PICK_RST}",
        "  green",
        f"{_PICK_DIM}  (1/2){_PICK_RST}",
        *(f"{_PICK_DIM}{d}{_PICK_RST}" for d in detail),
    ]
    width = max(_visible_len(title), _visible_len("(1/2)"), _visible_len(hint), 7, 8)
    legacy = Window(
        FormattedTextControl(_picker_frame(title, body, hint, width), focusable=True),
        dont_extend_height=True,
    )
    screen = Screen()
    to_container(legacy).write_to_screen(
        screen, MouseHandlers(), WritePosition(0, 0, 80, height), "", False, None
    )
    want = [
        [(screen.data_buffer[y][x].char, screen.data_buffer[y][x].style) for x in range(80)]
        for y in range(height)
    ]
    assert got == want
    dialog.press("enter")
    assert await dialog.answer() == "red"


# === the hold ==================================================================


@pytest.mark.parametrize(("width", "height"), [(80, 18), (120, 20), (40, 12)])
async def test_the_permission_gate_title_holds_every_approving_key_until_drawn(
    monkeypatch: pytest.MonkeyPatch, width: int, height: int
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, ["Yes", "No"]))
    first = dialog.paint(width, height)
    assert _TAIL not in first
    assert "Enter held" in first
    # The options keep their place under the scrolled title.
    assert "▸ Yes" in first and "  No" in first
    for key in ("enter", "c-j", "space"):
        dialog.press(key)
        assert not dialog.answered, f"{key!r} answered a question never drawn"
        dialog.paint(width, height)
    presses = dialog.page_until_released(width, height, "Enter")
    assert presses > 0
    assert any(_TAIL in s for s in dialog.screens)
    dialog.press("enter")
    assert await dialog.answer() == "Yes"


@pytest.mark.parametrize("downs", [1, 2])
async def test_every_option_is_held_not_only_the_first(
    monkeypatch: pytest.MonkeyPatch, downs: int
) -> None:
    """#389 round 2: a mutant that held only option 0 passed 862 tests. The
    second and the last option are held too, and Enter answers the highlighted
    one once the title has been drawn."""

    options = ["Yes", "Yes, for this session", "No"]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, options))
    dialog.paint(80, 18)
    for _ in range(downs):
        dialog.press("down")
    assert f"▸ {options[downs]}" in dialog.paint(80, 18)
    for key in ("enter", "space", "c-j"):
        dialog.press(key)
        assert not dialog.answered, f"option {downs} answered by {key!r} while held"
    dialog.page_until_released(80, 18, "Enter")
    dialog.press("enter")
    assert await dialog.answer() == options[downs]


@pytest.mark.parametrize("key", ["escape", "c-c"])
async def test_escape_and_ctrl_c_answer_while_held(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, ["Yes", "No"]))
    assert "Enter held" in dialog.paint(80, 18)
    dialog.press(key)
    assert await dialog.answer() is None


async def test_an_extension_select_drops_an_enter_typed_before_its_first_paint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing has been on screen before the first paint, so an approving key
    then is dropped (not queued), as the approval prompt drops it. A short
    title is answerable from that first paint on."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select("Allow?", ["Yes", "No"]))
    for key in ("enter", "space", "c-j"):
        dialog.press(key)
        assert not dialog.answered, f"{key!r} before the first paint was taken"
    dialog.paint(80, 10)
    dialog.press("enter")
    assert await dialog.answer() == "Yes"


@pytest.mark.parametrize("title", ["Settings", _GATE_TITLE])
async def test_an_own_picker_takes_a_key_typed_before_its_first_paint(
    monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    """aelix's own pickers keep the pre-paint behaviour they always had (#389
    round 2: holding it made 16 tests that drive /settings and /resume lose
    their key)."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, ["a", "b"], own=True))
    dialog.press("down")
    dialog.press("enter")
    assert await dialog.answer() == "b"


@pytest.mark.parametrize(
    ("title_rows", "options", "size"),
    [
        (14, ["Allow", "Allow file edits", "Cancel"], (80, 16)),
        # The verify round's case: 5 title rows over 8 options in the modal an
        # 80x16 terminal gives (11 rows). Round 1 drew 1 of the 5 rows, held
        # Enter and wanted 4 PgDn.
        (5, [f"opt{k}" for k in range(8)], (80, 11)),
        (5, [f"opt{k}" for k in range(8)], (80, 7)),
    ],
)
async def test_an_extension_title_that_fits_is_drawn_whole_and_its_options_scroll(
    monkeypatch: pytest.MonkeyPatch,
    title_rows: int,
    options: list[str],
    size: tuple[int, int],
) -> None:
    """Review round 2, decision 2: a title that fits the modal (with its
    highlighted option) is drawn whole and never held; the OPTION rows scroll
    in the room left, the highlighted one always on screen with the counter.
    The last option (spawn consent's ``Cancel``) is reached with the arrows."""

    width, height = size
    title = "\n".join(f"line {i:02d}" for i in range(title_rows))
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, options))
    screen = dialog.paint(width, height)
    assert all(f"line {i:02d}" in screen for i in range(title_rows))
    assert "held" not in screen and "hidden" not in screen
    assert f"▸ {options[0]}" in screen and f"(1/{len(options)})" in screen
    for k in range(1, len(options)):
        dialog.press("down")
        screen = dialog.paint(width, height)
        assert f"▸ {options[k]}" in screen, f"option {k} highlighted off screen"
        assert f"({k + 1}/{len(options)})" in screen
        assert all(f"line {i:02d}" in screen for i in range(title_rows))
    dialog.press("enter")
    assert await dialog.answer() == options[-1]


async def test_an_own_picker_keeps_main_s_layout_where_an_extension_s_scrolls_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same question as aelix's own picker: main's layout, the title whole
    and the rows below it cut from the bottom (``Cancel`` among them)."""

    title = "\n".join(f"line {i:02d}" for i in range(14))
    options = ["Allow", "Allow file edits", "Cancel"]
    own = _Dialog(monkeypatch)
    await own.open(own.ctx.select(title, options, own=True))
    screen = own.paint(80, 16)
    assert "line 13" in screen and "Cancel" not in screen and "held" not in screen
    own.press("enter")
    assert await own.answer() == "Allow"


async def test_an_extension_title_that_fits_is_drawn_whole_when_its_options_cannot_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When even the option rows leave the title no room, a title that fits the
    modal is drawn whole and the rows below it are cut, as before - it is not
    scrolled (that would hold Enter on a question that fits the screen)."""

    title = "\n".join(f"T{i:02d}" for i in range(5))
    options = [f"opt{k}" for k in range(12)]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, options))
    screen = dialog.paint(60, 9)
    assert all(f"T{i:02d}" in screen for i in range(5))
    assert "held" not in screen
    dialog.press("enter")
    assert await dialog.answer() == "opt0"


@pytest.mark.parametrize("own", [True, False])
@pytest.mark.parametrize("height", [3, 4, 6, 8, 11, 16, 17, 19, 22])
async def test_a_short_title_over_tall_options_answers_enter_after_one_paint(
    monkeypatch: pytest.MonkeyPatch, own: bool, height: int
) -> None:
    """#389 round 2's regression: /model (30 models) at 80x22, /settings at
    80x16 and an 8-option select at 80x16 never took Enter, because with no
    room left the title was never drawn and so never 'seen'. A title that is
    drawn is never held."""

    models = [f"{i + 1}. mock-{i:02d} (mockv)" for i in range(30)]
    detail = [f"detail row {k}" for k in range(12)]
    dialog = _Dialog(monkeypatch)
    await dialog.open(
        # ``own`` is passed only when set, so the extension half of this row
        # runs against code that has no ``own`` at all (8f7d98aa, #389 round 2).
        dialog.ctx.select("Select Model", models, detail=lambda _i: detail, **_own(own))
    )
    screen = dialog.paint(80, height)
    assert "Select Model" in screen
    assert "held" not in screen
    dialog.press("down")
    dialog.paint(80, height)
    dialog.press("enter")
    assert await dialog.answer() == models[1]


@pytest.mark.parametrize("own", [True, False])
@pytest.mark.parametrize("title_rows", [1, 2, 5, 9, 17, 40])
@pytest.mark.parametrize("n_options", [1, 3, 8, 12])
@pytest.mark.parametrize("height", [3, 5, 9, 14, 19])
async def test_the_title_is_never_withheld_and_enter_is_always_reachable(
    monkeypatch: pytest.MonkeyPatch, own: bool, title_rows: int, n_options: int, height: int
) -> None:
    """Every paint draws a title row and the highlighted option, and paging
    always ends the hold. aelix's own picker is main's: never held, the rows
    past the modal cut from the bottom."""

    title = "\n".join(f"T{i:02d}" for i in range(title_rows))
    options = [f"opt{k}" for k in range(n_options)]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, options, **_own(own)))
    screen = dialog.paint(60, height)
    assert "T00" in screen
    if own:
        assert "held" not in screen
    else:
        assert "▸ opt0" in screen
        dialog.page_until_released(60, height, "Enter", limit=60)
        assert any(f"T{title_rows - 1:02d}" in s for s in dialog.screens)
    dialog.press("enter")
    assert await dialog.answer() == "opt0"


async def test_a_resize_forgets_what_was_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, ["Yes", "No"]))
    dialog.paint(80, 18)
    dialog.page_until_released(80, 18, "Enter")
    assert "Enter held" in dialog.paint(60, 18)
    dialog.press("enter")
    assert not dialog.answered
    await dialog.close()


# === confirm ===================================================================


async def test_a_long_confirm_message_wraps_and_holds_y_until_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.confirm("Run this command?", _COMMAND))
    first = dialog.paint(80, 12)
    assert "y held" in first and _TAIL not in first
    for key in ("y", "Y"):
        dialog.press(key)
        assert not dialog.answered
    dialog.press("enter")  # Enter is never an answer in a confirm
    assert not dialog.answered
    dialog.paint(80, 12)
    dialog.page_until_released(80, 12, "y")
    assert any(f"{_TAIL} >> out.txt [y/n]" in s for s in dialog.screens)
    dialog.press("y")
    assert await dialog.answer() is True


@pytest.mark.parametrize("key", ["n", "N", "escape", "c-c"])
async def test_a_confirm_refusal_answers_while_held(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.confirm("Run this command?", _COMMAND))
    assert "y held" in dialog.paint(80, 12)
    dialog.press(key)
    assert await dialog.answer() is False


async def test_a_confirm_names_a_cr_and_drops_a_y_typed_before_its_first_paint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.confirm("Quit?", "echo a\recho b"))
    dialog.press("y")
    assert not dialog.answered
    screen = dialog.paint(80, 6)
    assert "echo a^Mecho b [y/n]" in screen
    assert dialog.reverse_runs() == ["^M"]
    dialog.press("y")
    assert await dialog.answer() is True

    own = _Dialog(monkeypatch)
    await own.open(own.ctx.confirm("Remove credentials", "Remove x?", own=True))
    own.press("y")
    assert await own.answer() is True


# === the callers whose text is an extension's or the model's ==================


async def test_spawn_consent_shows_the_whole_task_and_holds_until_it_is_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0199 S4 as amended by #399: the task is no longer cut at 300
    characters before the select, nor the directory elided at 68. The select
    wraps them, keeps ``Cancel`` on screen, and holds every option."""

    from aelix_agents.consent import CANCEL_OPTION, request_spawn_consent
    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from aelix_coding_agent.subagent_contract import ResolvedProfile

    profile = AgentProfile(
        name="writer",
        description="d",
        body="b",
        file_path="/home/a/.aelix/agents/writer.md",
        scope="user",
        approval_mode="auto",
    )
    resolved = ResolvedProfile(
        name="writer", profile=profile, source_path=profile.file_path, scope="user"
    )
    task = (
        "Review the module and then " + " ".join(f"step{i:03d}" for i in range(120)) + " " + _TAIL
    )
    cwd = "/w/" + "deep-directory-name/" * 6 + "end"
    dialog = _Dialog(monkeypatch)
    ctx = types.SimpleNamespace(has_ui=True, ui=dialog.ctx)
    await dialog.open(request_spawn_consent(ctx, resolved, task, PermissionMode.DEFAULT, cwd=cwd))
    first = dialog.paint(80, 19)
    assert "Enter held" in first and CANCEL_OPTION in first
    dialog.press("enter")
    assert not dialog.answered
    dialog.page_until_released(80, 19, "Enter")
    drawn = "\n".join(dialog.screens)
    assert _TAIL in drawn and "deep-directory-name/end" in drawn
    dialog.press("enter")
    grant = await dialog.answer()
    assert grant.consented


async def test_the_permission_fallback_select_holds_the_whole_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host that binds aelix's UI without the approval dialog asks through
    ``ctx.ui.select`` with the whole command in the title (#389). That select
    holds it now, and drops an Enter typed before the first paint."""

    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_agent_core.types import AgentContext
    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture

    from tests.builtin.gate_tools import BUILTIN_TOOLS

    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.DEFAULT))
    event = ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="bash",
        args={"command": _COMMAND},
        context=AgentContext(tools=list(BUILTIN_TOOLS.values())),
    )
    dialog = _Dialog(monkeypatch)
    ctx = types.SimpleNamespace(has_ui=True, cwd="/proj", ui=dialog.ctx)
    await dialog.open(perm._on_tool_call(event, ctx))  # type: ignore[arg-type]
    dialog.press("enter")
    assert not dialog.answered
    assert "Enter held" in dialog.paint(80, 18)
    dialog.press("enter")
    assert not dialog.answered
    dialog.page_until_released(80, 18, "Enter")
    dialog.press("enter")
    assert await dialog.answer() is None  # "Yes": the call is allowed


async def test_a_title_rides_the_rule_only_when_its_end_is_on_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``── title ───`` puts the title three cells in. A title that fits the
    frame's rule but not the screen goes on its own row, where it wraps, so a
    title counted as drawn is never cut at the screen's edge."""

    title = "x" * 35 + "END"
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, ["Yes", "No"]))
    screen = dialog.paint(40, 12)
    assert "END" in screen
    assert screen.splitlines()[0] == title
    dialog.press("enter")
    assert await dialog.answer() == "Yes"


# === review round 2: aelix's own pickers are main's, byte for byte ===========

_SETTINGS_TITLE = "Settings — select to change (Esc to close)"


def _main_render(title: str, options: list[str], idx: int, detail: list[str]) -> Any:
    """Main's (``dfb4ddcc``) ``select`` render for an unfiltered list, copied as
    the oracle: the own picker must paint exactly what this paints."""

    from aelix_coding_agent.tui.context import (
        _PICK_DIM,
        _PICK_RST,
        _PICK_SEL,
        _picker_frame,
        _visible_len,
    )

    viewport = 8
    items = list(enumerate(options))
    start = max(0, min(idx - viewport // 2, len(items) - viewport))
    end = min(len(items), start + viewport)
    counter = f"({idx + 1}/{len(items)})"
    hint = "↑/↓ move · type to filter · Enter select · Esc cancel"
    width = max(
        [_visible_len(title), _visible_len(counter), _visible_len(hint)]
        + [_visible_len(items[i][1]) + 2 for i in range(start, end)]
        + [_visible_len(d) for d in detail]
    )
    body: list[str] = []
    if start > 0:
        body.append(f"{_PICK_DIM}  ⋮{_PICK_RST}")
    for i in range(start, end):
        text = items[i][1]
        body.append(f"{_PICK_SEL}▸ {text}{_PICK_RST}" if i == idx else f"  {text}")
    if end < len(items):
        body.append(f"{_PICK_DIM}  ⋮{_PICK_RST}")
    body.append(f"{_PICK_DIM}  {counter}{_PICK_RST}")
    for d in detail:
        body.append(f"{_PICK_DIM}{d}{_PICK_RST}")
    return _picker_frame(title, body, hint, width)


def _paint_legacy(text: Any, width: int, height: int) -> list[list[tuple[str, str]]]:
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.containers import to_container
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.mouse_handlers import MouseHandlers
    from prompt_toolkit.layout.screen import Screen, WritePosition

    legacy = Window(FormattedTextControl(text, focusable=True), dont_extend_height=True)
    screen = Screen()
    to_container(legacy).write_to_screen(
        screen, MouseHandlers(), WritePosition(0, 0, width, height), "", False, None
    )
    return [
        [(screen.data_buffer[y][x].char, screen.data_buffer[y][x].style) for x in range(width)]
        for y in range(height)
    ]


def _own_cases() -> list[tuple[str, list[str], list[str]]]:
    from aelix_coding_agent.cli.project_trust import (
        format_project_trust_prompt,
        project_trust_options,
    )

    cwd = Path("/home/someone/projects/" + "nested-folder/" * 4 + "repo")
    return [
        (
            format_project_trust_prompt(cwd),
            project_trust_options(cwd, include_session_only=False),
            [],
        ),
        (_SETTINGS_TITLE, [f"Setting {k:02d}    value" for k in range(18)], ["help row"]),
        ("Select Model", [f"{i + 1}. mock-{i:02d} (mockv)" for i in range(30)], ["d1", "d2"]),
        ("Theme", ["✱ default", "  light", "  dark"], []),
        (
            "That session is already open in another terminal:\n  /a/" + "b/" * 50 + "s.jsonl",
            ["Fork and continue here (a new session file, same history)", "Cancel"],
            [],
        ),
    ]


@pytest.mark.parametrize("case", range(5))
@pytest.mark.parametrize(
    "size", [(12, 3), (20, 3), (12, 1), (80, 11), (80, 17), (80, 19), (120, 35)]
)
async def test_an_own_picker_paints_and_answers_as_main_at_every_size(
    monkeypatch: pytest.MonkeyPatch, case: int, size: tuple[int, int]
) -> None:
    """Review round 2, decision 1 (verify and Codex round 1): round 1 wrapped
    /trust's question to 8 rows, so at 80x16 'Do not trust' (the only answer
    that saves a revocation) and its counter fell off the modal; /settings held
    Enter after a paint at 12x8 and lost its choices at 20x8. aelix's own
    picker now paints cell for cell what main painted, at every size, and
    Enter after a paint answers the highlighted row at every size."""

    title, options, detail = _own_cases()[case]
    width, height = size
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, options, detail=lambda _i: detail, own=True))
    for idx in (0, 1):
        dialog.paint(width, height)
        assert dialog.cells() == _paint_legacy(
            _main_render(title, options, idx, detail), width, height
        ), f"own picker differs from main at {size}, row {idx}"
        if idx == 0:
            dialog.press("down")
    dialog.press("enter")
    assert await dialog.answer() == options[1]


def _keys(window: Any) -> list[tuple[str, ...]]:
    return [
        tuple(getattr(k, "value", str(k)) for k in binding.keys)
        for binding in window.content.key_bindings.bindings
    ]


async def test_an_own_picker_and_confirm_have_main_s_keys_and_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No PgUp/PgDn/Ctrl+Up/Ctrl+Down and no hold on aelix's own dialogs (on
    main those keys fell through to the type-to-filter ``<any>`` binding); the
    window is main's plain ``FormattedTextControl``."""

    from prompt_toolkit.layout.controls import FormattedTextControl

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, ["a", "b"], own=True))
    window = dialog.box["window"]
    assert isinstance(window.content, FormattedTextControl)
    assert _keys(window) == [
        ("up",),
        ("down",),
        ("c-m",),
        ("c-j",),
        (" ",),
        ("escape",),
        ("c-c",),
        ("c-h",),
        ("<any>",),
    ]
    await dialog.close()

    confirm = _Dialog(monkeypatch)
    await confirm.open(confirm.ctx.confirm("Remove credentials", _COMMAND, own=True))
    window = confirm.box["window"]
    assert isinstance(window.content, FormattedTextControl)
    assert window.content.text == f"Remove credentials\n{_COMMAND} [y/n]"
    assert _keys(window) == [
        ("y",),
        ("Y",),
        ("n",),
        ("N",),
        ("escape",),
        ("c-c",),
        ("c-m",),
        ("c-j",),
    ]
    confirm.paint(80, 3)
    confirm.press("y")
    assert await confirm.answer() is True


# === review round 2: the rows sabotage left green =============================


async def test_a_resize_from_narrow_to_wide_forgets_what_was_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify round 1's v-resize-keeps-seen: with the width left out of what
    was drawn, paging the title at 60 columns and widening to 120 approved on
    Enter with the 120-column rows never drawn. The rows are re-wrapped, so
    what was drawn is forgotten in either direction."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, ["Yes", "No"]))
    dialog.paint(60, 18)
    dialog.page_until_released(60, 18, "Enter")
    assert "Enter held" in dialog.paint(120, 18)
    for key in ("enter", "space", "c-j"):
        dialog.press(key)
        assert not dialog.answered, f"{key!r} answered after a resize"
    # The view stays at the bottom; the re-wrapped rows above it are reached
    # with PgUp, and only then is Enter taken.
    presses = 0
    while "Enter held" in dialog.screens[-1]:
        assert presses < 40
        dialog.press("pageup")
        dialog.paint(120, 18)
        presses += 1
    assert presses > 0
    dialog.press("enter")
    assert await dialog.answer() == "Yes"


@pytest.mark.parametrize("kind", ["select", "confirm"])
async def test_ctrl_up_and_ctrl_down_scroll_the_title_one_row(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Codex round 1: Ctrl+Up reversed (both dialogs) passed every test."""

    dialog = _Dialog(monkeypatch)
    coro = (
        dialog.ctx.select(_GATE_TITLE, ["Yes", "No"])
        if kind == "select"
        else dialog.ctx.confirm("Run?", _COMMAND)
    )
    await dialog.open(coro)
    view = dialog.box["window"].content.title
    dialog.paint(80, 12)
    dialog.press("pagedown")
    dialog.paint(80, 12)
    top = view.top
    assert top > 1
    dialog.press("c-up")
    dialog.paint(80, 12)
    assert view.top == top - 1
    dialog.press("c-down")
    dialog.press("c-down")
    dialog.paint(80, 12)
    assert view.top == top + 1
    await dialog.close()


async def test_a_held_title_keeps_a_title_row_its_footer_and_the_highlighted_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision 2's minimum for a held dialog: one title row, the footer and
    the highlighted option (Codex round 1: at 80x8 spawn consent drew no
    option at all)."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, ["Yes", "No"]))
    rows = dialog.paint(80, 3).splitlines()
    assert rows[0] == "Dangerous command:"
    assert "Enter held" in rows[1]
    assert rows[2] == "▸ Yes"
    dialog.press("down")
    assert dialog.paint(80, 3).splitlines()[2] == "▸ No"
    dialog.press("enter")
    assert not dialog.answered
    dialog.page_until_released(80, 3, "Enter", limit=60)
    dialog.press("enter")
    assert await dialog.answer() == "No"


@pytest.mark.parametrize("height", [1, 2])
async def test_too_small_for_a_held_title_says_so_and_only_a_refusal_answers(
    monkeypatch: pytest.MonkeyPatch, height: int
) -> None:
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_GATE_TITLE, ["Yes", "No"]))
    # Everything drawn once at a usable size first: the too-small paint still
    # holds (no option is on screen), however much was seen before.
    dialog.paint(80, 18)
    dialog.page_until_released(80, 18, "Enter")
    screen = dialog.paint(80, height)
    assert "too small" in screen and "Esc cancels" in screen
    for key in ("enter", "space", "c-j", "pagedown"):
        dialog.press(key)
        dialog.paint(80, height)
        assert not dialog.answered, f"{key!r} answered on a too-small screen"
    dialog.press("escape")
    assert await dialog.answer() is None

    confirm = _Dialog(monkeypatch)
    await confirm.open(confirm.ctx.confirm("Run?", _COMMAND))
    assert "too small" in confirm.paint(80, height)
    confirm.press("y")
    assert not confirm.answered
    confirm.press("n")
    assert await confirm.answer() is False


def _writer() -> Any:
    from aelix_coding_agent.agents.profile import AgentProfile
    from aelix_coding_agent.subagent_contract import ResolvedProfile

    profile = AgentProfile(
        name="writer",
        description="d",
        body="b",
        file_path="/home/a/.aelix/agents/writer.md",
        scope="user",
        approval_mode="auto",
    )
    return ResolvedProfile(name="writer", profile=profile, source_path=profile.file_path, scope="user")


async def test_spawn_consent_drops_a_pre_paint_enter_and_its_cancel_answers_while_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify round 1's v-consent-own: consent passing ``own=True`` took an
    Enter typed before its first paint. And the decision's 'every option except
    cancel': spawn consent's own ``Cancel`` answers while the task is held."""

    from aelix_agents.consent import CANCEL_OPTION, request_spawn_consent
    from aelix_coding_agent.builtin.permission_mode import PermissionMode

    task = "Review " + " ".join(f"step{i:03d}" for i in range(160)) + " " + _TAIL
    dialog = _Dialog(monkeypatch)
    ctx = types.SimpleNamespace(has_ui=True, ui=dialog.ctx)
    await dialog.open(
        request_spawn_consent(ctx, _writer(), task, PermissionMode.DEFAULT, cwd="/w")
    )
    dialog.press("enter")
    assert not dialog.answered, "an Enter typed before the consent dialog's first paint"
    screen = dialog.paint(80, 19)
    assert "Enter held" in screen
    dialog.press("enter")
    assert not dialog.answered
    while f"▸ {CANCEL_OPTION}" not in dialog.paint(80, 19):
        dialog.press("down")
    assert "Enter held" in dialog.screens[-1]
    dialog.press("enter")
    grant = await dialog.answer()
    assert not grant.consented


async def test_spawn_consent_at_a_three_row_modal_shows_an_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex round 1, spawn consent in an 80x8 terminal (a 3-row modal): the
    title, its footer and the rule filled it and no option was drawn."""

    from aelix_agents.consent import request_spawn_consent
    from aelix_coding_agent.builtin.permission_mode import PermissionMode

    dialog = _Dialog(monkeypatch)
    ctx = types.SimpleNamespace(has_ui=True, ui=dialog.ctx)
    await dialog.open(
        request_spawn_consent(ctx, _writer(), "Review the module", PermissionMode.DEFAULT, cwd="/w")
    )
    rows = dialog.paint(80, 3).splitlines()
    assert "Enter held" in rows[1]
    assert rows[2].startswith("▸ ")
    await dialog.close()


async def test_the_permission_fallback_s_no_answers_while_its_yes_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_agent_core.harness.hooks import ToolCallHookEvent
    from aelix_agent_core.types import AgentContext
    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture

    from tests.builtin.gate_tools import BUILTIN_TOOLS

    perm = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.DEFAULT))
    event = ToolCallHookEvent(
        tool_call_id="t1",
        tool_name="bash",
        args={"command": _COMMAND},
        context=AgentContext(tools=list(BUILTIN_TOOLS.values())),
    )
    dialog = _Dialog(monkeypatch)
    ctx = types.SimpleNamespace(has_ui=True, cwd="/proj", ui=dialog.ctx)
    await dialog.open(perm._on_tool_call(event, ctx))  # type: ignore[arg-type]
    assert "Enter held" in dialog.paint(80, 18)
    dialog.press("down")
    assert "▸ Yes, for this session" in dialog.paint(80, 18)
    dialog.press("enter")
    assert not dialog.answered, "an approving row answered while held"
    dialog.press("down")
    assert "▸ No" in dialog.paint(80, 18)
    dialog.press("enter")
    result = await dialog.answer()
    assert result is not None and result.block


async def test_select_with_cancel_names_the_rows_only_to_a_select_that_declares_it() -> None:
    from aelix_coding_agent.extensions.ext_ui import select_with_cancel

    seen: list[tuple[Any, ...]] = []

    class Plain:
        async def select(self, title: str, options: list[str], opts: Any = None) -> str:
            seen.append((title, options, opts))
            return options[0]

    class Spy:
        async def select(self, *args: Any, **kwargs: Any) -> str:
            seen.append((args, kwargs))
            return "x"

    class Declares:
        async def select(
            self, title: str, options: list[str], *, cancel_options: tuple[str, ...] = ()
        ) -> str:
            seen.append((title, options, cancel_options))
            return options[-1]

    assert await select_with_cancel(Plain(), "t", ["a", "Cancel"], ("Cancel",)) == "a"  # type: ignore[arg-type]
    assert await select_with_cancel(Spy(), "t", ["a"], ("Cancel",)) == "x"  # type: ignore[arg-type]
    assert await select_with_cancel(Declares(), "t", ["a", "Cancel"], ("Cancel",)) == "Cancel"  # type: ignore[arg-type]
    assert seen == [
        ("t", ["a", "Cancel"], None),
        (("t", ["a"]), {}),
        ("t", ["a", "Cancel"], ("Cancel",)),
    ]


# === review round 3 ===========================================================
#
# Verify round 2 left two too-small recovery mutants green; Codex round 2 found
# a title split inside a grapheme cluster, an option label's newline drawn as
# ``^J``, and a scroll that counted rows as drawn before any repaint.

#: Five title rows: drawn whole in a tall modal, too small at two rows.
_FIVE = "\n".join(f"row {k}" for k in range(5))
_E = "é"  # e + COMBINING ACUTE ACCENT: one grapheme cluster, one cell


@pytest.mark.parametrize("kind", ["select", "confirm"])
async def test_a_title_drawn_whole_after_a_too_small_paint_answers(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Verify round 2's too-small-sticky mutant (no ``_too_small = False`` where
    the title is drawn whole): after one too-small paint, the terminal grows, the
    whole question is on screen, and Enter (``y``) never answered."""

    dialog = _Dialog(monkeypatch)
    coro = (
        dialog.ctx.select(_FIVE, ["Yes", "No"])
        if kind == "select"
        else dialog.ctx.confirm("Run?", _FIVE)
    )
    await dialog.open(coro)
    assert "too small" in dialog.paint(80, 2)
    screen = dialog.paint(80, 30)
    assert "row 4" in screen and "held" not in screen
    dialog.press("enter" if kind == "select" else "y")
    assert await dialog.answer() == ("Yes" if kind == "select" else True)


@pytest.mark.parametrize("kind", ["select", "confirm"])
async def test_a_scrolled_title_after_a_too_small_paint_is_released_by_paging(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Verify round 2's scroll-path-too-small-not-reset mutant: after a
    too-small paint, paging through the whole (taller) title never released
    the approving key."""

    held = "Enter" if kind == "select" else "y"
    dialog = _Dialog(monkeypatch)
    coro = (
        dialog.ctx.select(_GATE_TITLE, ["Yes", "No"])
        if kind == "select"
        else dialog.ctx.confirm("Run?", _COMMAND)
    )
    await dialog.open(coro)
    assert "too small" in dialog.paint(80, 2)
    assert f"{held} held" in dialog.paint(80, 12)
    assert dialog.page_until_released(80, 12, held) > 0
    assert any(_TAIL in s for s in dialog.screens)
    dialog.press("enter" if kind == "select" else "y")
    assert await dialog.answer() == ("Yes" if kind == "select" else True)


@pytest.mark.parametrize("kind", ["select", "confirm"])
async def test_paging_with_no_repaint_releases_nothing(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Codex round 2's surviving mutant: ``_BodyViewport.scroll`` marking the
    rows it scrolls to as seen, before any paint, approved after N PgDn typed
    ahead of the screen. Rows count as seen only when a paint draws them."""

    held = "Enter" if kind == "select" else "y"
    dialog = _Dialog(monkeypatch)
    coro = (
        dialog.ctx.select(_GATE_TITLE, ["Yes", "No"])
        if kind == "select"
        else dialog.ctx.confirm("Run?", _COMMAND)
    )
    await dialog.open(coro)
    assert f"{held} held" in dialog.paint(80, 12)
    for _ in range(80):
        dialog.press("pagedown")
    dialog.press("enter" if kind == "select" else "y")
    assert not dialog.answered, "PgDn typed ahead of the screen released the hold"
    # One paint draws only the last page: the pages between were never drawn.
    assert f"{held} held" in dialog.paint(80, 12)
    dialog.press("enter" if kind == "select" else "y")
    assert not dialog.answered
    await dialog.close()


@pytest.mark.parametrize(
    "options",
    [
        ["line1\nline2", "Other"],
        ["Other", "line1\nline2\nline3"],
        ["\x1b[31mred", "green", "blue"],
        ["a\rb", "x"],
    ],
)
async def test_an_extension_select_draws_its_option_rows_as_main(
    monkeypatch: pytest.MonkeyPatch, options: list[str]
) -> None:
    """Codex round 2: an option label with a newline was drawn as
    ``line1^Jline2`` (main drew two rows). Option rows are #179's, so an
    extension's select whose title fits draws them cell for cell as main."""

    for idx in (0, 1):
        dialog = _Dialog(monkeypatch)
        await dialog.open(dialog.ctx.select("Choose", options))
        if idx:
            dialog.press("down")
        screen = dialog.paint(80, 20)
        assert "^J" not in screen
        assert dialog.cells() == _paint_legacy(_main_render("Choose", options, idx, []), 80, 20)
        dialog.press("enter")
        assert await dialog.answer() == options[idx]


@pytest.mark.parametrize("height", [3, 4, 6, 9, 12])
async def test_a_multiline_option_label_keeps_its_rows_when_the_options_scroll(
    monkeypatch: pytest.MonkeyPatch, height: int
) -> None:
    options = ["one\ntwo", "three", "four\nfive", *(f"o{k}" for k in range(6))]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select("Choose", options))
    dialog.press("down")
    dialog.press("down")
    screen = dialog.paint(40, height)
    assert "^J" not in screen
    assert "▸ four" in screen and "five" in screen
    assert len(screen.splitlines()) <= height
    dialog.press("enter")
    assert await dialog.answer() == "four\nfive"


def test_a_title_wraps_by_grapheme_cluster() -> None:
    """Codex round 2: ``_title_rows`` cut by code point and counted each mark as
    a cell, so ``"x" + e-acute * 80`` (81 cells) took three rows at 80 columns
    and an accent could land on a row of its own. pi wraps by grapheme
    (``Intl.Segmenter``)."""

    from aelix_coding_agent.tui.context import _title_rows
    from aelix_coding_agent.tui.width import cells_by_cluster

    title = "x" + _E * 80
    rows = _title_rows(title, 80)
    assert len(rows) == 2, rows
    assert "".join(rows) == title
    assert not any(row.startswith("́") for row in rows)
    assert all(cells_by_cluster(row) <= 80 for row in rows)
    assert _title_rows("x" + _E * 40, 80) == ["x" + _E * 40], "a title that fits is kept"


@pytest.mark.parametrize(
    ("lead", "cluster"),
    [
        (79, _E),  # its mark would fall on the last column, which prompt-toolkit drops
        (78, _E + "̂"),  # two marks on one letter
        (76, "\U0001f469‍\U0001f4bb"),  # woman + ZWJ + laptop
        (77, "\U0001f1f0\U0001f1f7"),  # two regional indicators: a flag
        (78, "각"),  # a Hangul syllable spelled in jamo
    ],
)
async def test_a_cluster_at_the_wrap_is_never_split(
    monkeypatch: pytest.MonkeyPatch, lead: int, cluster: str
) -> None:
    from aelix_coding_agent.tui.approval_dialog import _cut
    from aelix_coding_agent.tui.context import _title_rows
    from aelix_coding_agent.tui.width import cells_by_cluster

    title = "x" * lead + cluster + " tail end"
    rows = _title_rows(title, 80)
    assert "".join(rows) == title
    assert sum(row.count(cluster) for row in rows) == 1, rows
    assert all(cells_by_cluster(row) <= 80 for row in rows)
    # The approval prompt's rows come from the same function.
    cut = ["".join(f[1] for f in row) for row in _cut([("", ch) for ch in title], 80)]
    assert cut == rows

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, ["Yes", "No"]))
    dialog.paint(80, 12)
    painted = [
        "".join(dialog.screen.data_buffer[y][x].char for x in range(80)) for y in range(12)
    ]
    assert sum(row.count(cluster) for row in painted) == 1, painted[:3]
    dialog.press("enter")
    assert await dialog.answer() == "Yes"


async def test_a_combining_title_that_fits_two_rows_is_drawn_whole_and_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex round 2, cat 1: the 81-cell title in a three-row modal (two title
    rows and the highlighted option) was held behind a footer."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select("x" + _E * 80, ["Yes", "No"]))
    screen = dialog.paint(80, 3)
    assert "held" not in screen
    assert screen.count(_E) == 80
    assert screen.splitlines()[2] == "▸ Yes"
    dialog.press("enter")
    assert await dialog.answer() == "Yes"

    short = _Dialog(monkeypatch)
    await short.open(short.ctx.select("x" + _E * 40, ["Yes", "No"]))
    assert short.paint(80, 16).count(_E) == 40, "an accent was split off its letter"
    await short.close()


async def test_select_declared_passes_only_what_a_select_declares() -> None:
    """Review round 3: ``/extension new`` and ``/agents run``'s project-agent
    confirm reach ``runtime.ui.select`` and pass ``own=True`` through this; a
    host whose ``select`` does not declare it is called as before."""

    from aelix_coding_agent.extensions.ext_ui import select_declared

    seen: list[Any] = []

    async def plain(title: str, options: list[str]) -> str:
        seen.append((title, options))
        return options[0]

    async def declares(title: str, options: list[str], *, own: bool = False) -> str:
        seen.append((title, options, own))
        return options[-1]

    assert await select_declared(plain, "t", ["a", "b"], own=True) == "a"
    assert await select_declared(declares, "t", ["a", "b"], own=True) == "b"
    assert seen == [("t", ["a", "b"]), ("t", ["a", "b"], True)]


@pytest.mark.parametrize(("height", "last"), [(8, "o0"), (11, "o2"), (12, "o3")])
async def test_multiline_option_labels_fill_the_room_they_are_given(
    monkeypatch: pytest.MonkeyPatch, height: int, last: str
) -> None:
    """The options window counts screen rows, not labels: counting labels
    showed one option fewer at 80x8 (``o0`` left out, a row blank), and
    trying rooms only up to the number of labels left rows blank at 11 and
    12 (``o2``, ``o3`` left out)."""

    options = ["one\ntwo", "three", "four\nfive", *(f"o{k}" for k in range(6))]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select("Choose", options))
    dialog.press("down")
    dialog.press("down")
    rows = dialog.paint(40, height).splitlines()
    assert len(rows) == height and all(rows), rows
    assert f"  {last}" in rows and f"  o{int(last[1]) + 1}" not in rows, rows
    assert rows[-2:] == ["  ⋮", "  (3/9)"]
    await dialog.close()


@pytest.mark.parametrize("wide", ["가", "\U0001f600", "\U0001f1f0\U0001f1f7", _E + "̂"])
async def test_a_title_of_wide_clusters_is_measured_in_cells_not_characters(
    monkeypatch: pytest.MonkeyPatch, wide: str
) -> None:
    """41 Hangul syllables are 41 characters and 82 cells: a title checked by
    its length was kept on one 80-column row and its tail clipped at the
    window's edge, drawn and so "seen" (review round 3's title-fit mutant
    stayed green on every row before this one)."""

    from aelix_coding_agent.tui.context import _title_rows
    from aelix_coding_agent.tui.width import cells_by_cluster

    title = wide * 41 + " TAIL"
    rows = _title_rows(title, 80)
    assert "".join(rows) == title
    assert all(cells_by_cluster(row) <= 80 for row in rows), [cells_by_cluster(r) for r in rows]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, ["Yes", "No"]))
    assert "TAIL" in dialog.paint(80, 10)
    await dialog.close()


# === review round 4 ============================================================

_DEPLOY = ["Deploy staging\n" + "\n".join(f"  detail {k}" for k in range(1, 8)), "Cancel"]


@pytest.mark.parametrize("height", [2, 3, 5, 8, 9, 11, 12, 16])
async def test_a_short_title_over_a_tall_highlighted_option_is_drawn_as_main_and_answers(
    monkeypatch: pytest.MonkeyPatch, height: int
) -> None:
    """Codex round 3, cat 3: ``Deploy?`` over an option of eight lines and
    ``Cancel``, in an 80x13 terminal (an eight-row modal), said the terminal
    was too small and held Enter, and 20 PgDn did not release it; main drew it
    and Enter selected. The fewest rows a title that fits needs under it are
    the FIRST line of the highlighted option: the rest of a tall label is cut
    from the bottom, as main cuts it."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select("Deploy?", _DEPLOY))
    screen = dialog.paint(80, height)
    assert "too small" not in screen and "held" not in screen, screen
    assert "Deploy?" in screen and "▸ Deploy staging" in screen
    assert dialog.cells() == _paint_legacy(_main_render("Deploy?", _DEPLOY, 0, []), 80, height)
    dialog.press("enter")
    assert await dialog.answer() == _DEPLOY[0]


@pytest.mark.parametrize("height", [3, 5, 8])
async def test_a_tall_title_over_a_tall_highlighted_option_scrolls_and_answers(
    monkeypatch: pytest.MonkeyPatch, height: int
) -> None:
    """The same option under a title taller than the modal: the title scrolls
    above the first line(s) of the highlighted option instead of the dialog
    saying it is too small, and paging releases Enter."""

    title = "\n".join(f"T{i:02d}" for i in range(20)) + "\nTAIL_SECRET"
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, _DEPLOY))
    screen = dialog.paint(80, height)
    assert "too small" not in screen and "Enter held" in screen, screen
    assert "▸ Deploy staging" in screen
    assert len(screen.splitlines()) <= height
    dialog.press("enter")
    assert not dialog.answered
    dialog.page_until_released(80, height, "Enter")
    assert any("TAIL_SECRET" in s for s in dialog.screens)
    dialog.press("enter")
    assert await dialog.answer() == _DEPLOY[0]


async def test_a_title_as_wide_as_the_window_that_ends_in_a_mark_takes_two_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify round 3: without the last column kept for a closing mark
    (``cluster_cells(..., ends_row=True)`` in ``cells_by_cluster``) ``"x" * 79``
    and e + U+0301 was one 80-cell row, and prompt-toolkit, which drops a mark
    on the window's last column, painted a bare ``e``: the question on screen
    was not the one asked, and Enter answered it."""

    from aelix_coding_agent.tui.context import _title_rows
    from aelix_coding_agent.tui.width import cells_by_cluster

    title = "x" * 79 + _E
    assert cells_by_cluster(title) == 81
    assert _title_rows(title, 80) == ["x" * 79, _E]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, ["Yes", "No"]))
    dialog.paint(80, 12)
    painted = ["".join(dialog.screen.data_buffer[y][x].char for x in range(80)) for y in range(12)]
    assert sum(row.count(_E) for row in painted) == 1, painted[:3]
    assert not any(row.rstrip().endswith("x" * 79 + "e") for row in painted), painted[:3]
    # Verify round 3, nb: prompt-toolkit also writes the mark into the cell
    # after its letter, so a row ending in one drew the accent twice.
    assert not any("́" in dialog.screen.data_buffer[y][1].char for y in range(12)), painted[:3]
    assert sum(row.count("́") for row in painted) == 1, painted[:3]
    dialog.press("enter")
    assert await dialog.answer() == "Yes"


_VS16 = "\u263a\ufe0f"  # WHITE SMILING FACE + VS16: 1 cell to prompt-toolkit, 2 to Rich


async def test_a_vs16_cluster_is_measured_by_rich_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify round 3: ``cluster_cells`` without Rich's width packed 41 of
    these and `` TAIL`` into one 87-cell row (by Rich) at 80 columns, and the
    approval prompt's body (60 and ``END``, through the same ``_cut``) into one
    of 124: #389's "a row fits by every measure"."""

    from aelix_coding_agent.tui.approval_dialog import _cut
    from aelix_coding_agent.tui.context import _title_rows
    from rich.cells import cell_len

    title = _VS16 * 41 + " TAIL"
    rows = _title_rows(title, 80)
    assert "".join(rows) == title
    assert len(rows) >= 2 and all(cell_len(row) <= 80 for row in rows), [cell_len(r) for r in rows]
    body = _VS16 * 60 + "END"
    cut = ["".join(f[1] for f in row) for row in _cut([("", ch) for ch in body], 80)]
    assert "".join(cut) == body
    assert len(cut) >= 2 and all(cell_len(row) <= 80 for row in cut), [cell_len(r) for r in cut]
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, ["Yes", "No"]))
    assert "TAIL" in dialog.paint(80, 10)
    await dialog.close()


async def test_a_held_title_stays_held_and_whole_while_nothing_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex round 3, cat 4: with ``[title]`` instead of the wrapped rows in
    the no-matches branch, typing ``z`` clipped a 22-line title to one row
    (newlines drawn as ``^J``), with no scrolling and the hold released."""

    title = "\n".join(f"T{i:02d}" for i in range(21)) + "\nTAIL_SECRET"
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, ["Yes", "No"]))
    dialog.paint(80, 8)
    dialog.type_text("z")
    screen = dialog.paint(80, 8)
    assert "(no matches)" in screen
    assert "^J" not in screen and "TAIL_SECRET" not in screen
    assert screen.splitlines()[0] == "T00"
    assert "Enter held" in screen
    assert dialog.box["window"].content.holds()
    dialog.page_until_released(80, 8, "Enter")
    assert any("TAIL_SECRET" in s for s in dialog.screens)
    dialog.press("backspace")
    dialog.paint(80, 8)
    dialog.press("enter")
    assert await dialog.answer() == "Yes"


# === review round 5 ============================================================
#
# Verify round 4 left two mutants of ``_TitleHeldControl._layout`` green: the
# round 4 branch without its ``_too_small = False`` (blocking), and the
# scrolled path's label cut to one row.

#: Taller than any modal here: it always scrolls.
_TALL_TITLE = "\n".join(f"T{i:02d}" for i in range(20)) + "\nTAIL_SECRET"


@pytest.mark.parametrize(
    ("title", "small", "grown"),
    [
        ("Deploy?", 1, 2),
        ("Deploy?", 1, 5),
        ("Deploy?", 1, 8),
        ("Line one\nLine two\nLine three", 2, 6),
    ],
)
async def test_a_too_small_dialog_that_grows_to_fit_a_tall_highlighted_option_answers(
    monkeypatch: pytest.MonkeyPatch, title: str, small: int, grown: int
) -> None:
    """Verify round 4's second-branch-too-small-sticky mutant: the round 4
    branch (the title drawn whole over only the first rows of a tall
    highlighted option) without its ``_too_small = False``. Painted too small
    once, then at a size where the title fits over the FIRST row of the
    eight-line option, the question was on screen whole with nothing saying
    held, and Enter never answered (30 PgDn did not release it either)."""

    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(title, _DEPLOY))
    assert "too small" in dialog.paint(80, small)
    screen = dialog.paint(80, grown)
    assert "too small" not in screen and "held" not in screen, screen
    assert all(line in screen for line in title.splitlines()), screen
    assert "▸ Deploy staging" in screen, screen
    dialog.press("enter")
    assert dialog.answered, f"Enter held under a question drawn whole:\n{screen}"
    assert await dialog.answer() == _DEPLOY[0]


@pytest.mark.parametrize(("height", "title_rows", "label_rows"), [(5, 3, 1), (8, 4, 3), (10, 4, 5)])
async def test_a_tall_option_under_a_scrolling_title_is_cut_to_the_rows_that_leave_the_title_four(
    monkeypatch: pytest.MonkeyPatch, height: int, title_rows: int, label_rows: int
) -> None:
    """Verify round 4's scrolled-cut-to-one mutant (``scrolled[-1][:1]``): a
    scrolling title over the eight-line option gets the option's first rows,
    as many as leave the title a page of four (never fewer than the first):
    at 80x8, T00-T03, the footer and three label rows."""

    label = _DEPLOY[0].splitlines()
    dialog = _Dialog(monkeypatch)
    await dialog.open(dialog.ctx.select(_TALL_TITLE, _DEPLOY))
    rows = dialog.paint(80, height).splitlines()
    assert len(rows) == height, rows
    assert rows[:title_rows] == [f"T{i:02d}" for i in range(title_rows)], rows
    hidden = 21 - title_rows
    assert rows[title_rows].startswith(f"{hidden} of 21 lines hidden (↑0 ↓{hidden})"), rows
    assert "Enter held" in rows[title_rows], rows
    assert rows[title_rows + 1 :] == ["▸ " + label[0], *label[1:label_rows]], rows
    await dialog.close()
