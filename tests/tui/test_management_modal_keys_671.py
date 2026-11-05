"""#671 — the keys reach a descriptor management modal.

``DescriptorRenderer.open_modal`` built the modal out of windows none of which
could take focus, so ``show_modal``'s focus failed (and ``AelixChrome.focus``
swallowed the error). The keys stayed with the input line the modal hides: Esc,
Ctrl+C and the action keys never reached the modal, and what was typed under it
went to the model on Enter.

These drive a real :class:`AelixChrome` through a VT100 output replayed into
``pyte`` and send the keys through its pipe input, so the key processor and the
focus are on the path (``test_descriptors`` calls the bindings' handlers
directly, which is why it never saw this).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import pyte
import pytest
from _polling import WAIT_CEILING, wait_until  # sibling helper (pytest prepend import mode)
from _pyte import await_first_paint
from aelix_agent_core.contracts.descriptor import ActionDescriptor, DescriptorEnvelope
from aelix_coding_agent.extensions.api import EventBus
from aelix_coding_agent.tui.chrome import _EOF, _INPUT_PLACEHOLDER, AelixChrome
from aelix_coding_agent.tui.descriptors import DescriptorRegistry, DescriptorRenderer
from aelix_coding_agent.tui.overlay import show_modal
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

_CPR_ASK = "\x1b[6n"
_CPR_RESPONSE = "\x1b[10;1R"
_ESC = "\x1b"
_CTRL_C = "\x03"


class _Footer:
    def set_status(self, key: str, text: str | None) -> None:
        return None

    def get_extension_statuses(self) -> dict[str, str]:
        return {}


class _TtyCapture(io.StringIO):
    """Says it is a terminal, so the renderer asks for the cursor position
    (``Vt100_Output.responds_to_cpr`` reads ``isatty()``) and the answer below
    is one it waits for, as a real terminal's would be."""

    def isatty(self) -> bool:
        return True


class _Live:
    def __init__(
        self, chrome: AelixChrome, pipe: PipeInput, capture: io.StringIO, size: Size
    ) -> None:
        self.chrome = chrome
        self.pipe = pipe
        self._capture = capture
        self._size = size

    def grid(self) -> list[str]:
        screen = pyte.Screen(self._size.columns, self._size.rows)
        pyte.Stream(screen).feed(self._capture.getvalue())
        return [row.rstrip() for row in screen.display]

    def dump(self) -> str:
        return "\n".join(f"{i:>2}|{row}" for i, row in enumerate(self.grid()) if row)

    def shows(self, text: str) -> bool:
        return any(text in row for row in self.grid())

    async def until(self, predicate: Callable[[], bool], what: str) -> None:
        await wait_until(predicate, what=what, detail=self.dump)


@contextlib.asynccontextmanager
async def _live(rows: int = 24, cols: int = 80) -> AsyncIterator[_Live]:
    capture = _TtyCapture()
    size = Size(rows=rows, columns=cols)
    output = Vt100_Output(capture, get_size=lambda: size, term="xterm-256color", enable_cpr=True)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        chrome = AelixChrome(
            console=Console(file=io.StringIO(), force_terminal=True, width=cols),
            pt_input=pipe,
            pt_output=output,
            time_fn=lambda: 0.0,
        )
        task = asyncio.create_task(chrome.run())
        try:
            await await_first_paint(chrome)
            await wait_until(lambda: _CPR_ASK in capture.getvalue(), what="the CPR query")
            pipe.send_text(_CPR_RESPONSE)
            await wait_until(lambda: chrome.app.renderer.height_is_known, what="the CPR answer")
            live = _Live(chrome, pipe, capture, size)
            await live.until(lambda: live.shows(_INPUT_PLACEHOLDER), "the input row")
            yield live
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)


def _modal(*actions: ActionDescriptor) -> DescriptorEnvelope:
    return DescriptorEnvelope(
        kind="management-modal",
        namespace="descx",
        id="modal-dm1",
        payload={  # type: ignore[arg-type]
            "kind": "management-modal",
            "command": "dm1",
            "title": "MODAL dm1",
            "view": "form",
            "actions": [a.model_dump(mode="json") for a in actions],
        },
    )


_ACT1 = ActionDescriptor(plugin_id="descx", action="act1")


def _renderer(chrome: AelixChrome, bus: EventBus, confirm: Any = None) -> DescriptorRenderer:
    return DescriptorRenderer(
        chrome,
        _Footer(),  # type: ignore[arg-type]
        DescriptorRegistry(),
        event_bus=bus,
        confirm=confirm,
    )


async def _open(live: _Live, renderer: DescriptorRenderer, env: DescriptorEnvelope) -> None:
    renderer.open_modal(env)
    await live.until(
        lambda: live.shows("MODAL dm1") and not live.shows(_INPUT_PLACEHOLDER),
        "the modal to replace the input row",
    )


async def _closed(live: _Live) -> None:
    await live.until(
        lambda: not live.shows("MODAL dm1") and live.shows(_INPUT_PLACEHOLDER),
        "the modal to close and the input row to come back",
    )
    assert live.chrome.is_modal_open() is False
    assert live.chrome.app.layout.has_focus(live.chrome._input_window)


@pytest.mark.parametrize(("rows", "cols"), [(24, 80), (40, 120)])
async def test_esc_and_ctrl_c_close_the_modal_and_the_input_comes_back(
    rows: int, cols: int
) -> None:
    bus = EventBus()
    actions: list[Any] = []
    bus.on("plugin_action", actions.append)
    async with _live(rows, cols) as live:
        renderer = _renderer(live.chrome, bus)
        for key in (_ESC, _CTRL_C):
            await _open(live, renderer, _modal(_ACT1))
            assert live.shows("[1] act1")
            live.pipe.send_text(key)
            await _closed(live)
    assert actions == []


async def test_a_key_before_the_modal_is_first_drawn_still_reaches_it() -> None:
    """prompt_toolkit caches the merged key bindings per focused window and set
    of controls; one built before the first render after the mount had no parent
    for the modal's window, and left the modal's own keys out while it was open.
    The key goes to the key processor in the loop turn the modal mounts in,
    before any render (CI ran into it with an Esc sent at once)."""

    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.keys import Keys

    bus = EventBus()
    async with _live() as live:
        chrome = live.chrome
        _renderer(chrome, bus).open_modal(_modal(_ACT1))
        for _ in range(20):
            if chrome.is_modal_open():
                break
            await asyncio.sleep(0)
        assert chrome.is_modal_open()
        drawn = chrome.app.render_counter

        def _press() -> None:
            chrome.app.key_processor.feed(KeyPress(Keys.ControlC, _CTRL_C))
            chrome.app.key_processor.process_keys()

        chrome.app.context.copy().run(_press)
        assert chrome.app.render_counter == drawn, "a render ran first: this did not test the race"
        await wait_until(lambda: not chrome.is_modal_open(), what="the modal to close", detail=live.dump)
        assert chrome._last_ctrl_c is None, "the chrome's Ctrl+C took it: the modal's bindings were left out"


async def test_the_action_key_reaches_the_extension_and_closes_the_modal() -> None:
    bus = EventBus()
    actions: list[Any] = []
    bus.on("plugin_action", actions.append)
    async with _live() as live:
        renderer = _renderer(live.chrome, bus)
        await _open(live, renderer, _modal(_ACT1))
        live.pipe.send_text("1")
        await _closed(live)
        await wait_until(lambda: actions != [], what="the plugin_action emit")
    assert actions == [_ACT1.model_dump(mode="json")]


async def test_what_is_typed_under_the_modal_does_not_reach_the_input() -> None:
    bus = EventBus()
    async with _live() as live:
        renderer = _renderer(live.chrome, bus)
        await _open(live, renderer, _modal(_ACT1))
        live.pipe.send_text("hello under the modal")
        live.pipe.send_text("\r")
        # A key sent after them: once it has closed the modal, the ones before
        # it have been through the key processor.
        live.pipe.send_text(_ESC)
        await _closed(live)
        assert live.chrome.buffer.text == ""
        assert live.chrome._input_queue.empty()


async def test_an_action_with_confirm_asks_after_the_modal_is_down() -> None:
    # The modal and the confirm dialog share the one in-flow slot. Dispatched
    # from inside the modal, the dialog mounted first and the closing modal's
    # unmount then took it down, leaving the confirm unanswerable.
    bus = EventBus()
    actions: list[Any] = []
    bus.on("plugin_action", actions.append)
    asked: list[str] = []

    async with _live() as live:
        chrome = live.chrome

        async def confirm(message: str) -> bool:
            asked.append(message)

            def build(result: asyncio.Future[Any]) -> Window:
                kb = KeyBindings()
                kb.add("y")(lambda _e: result.set_result(True))
                kb.add("n")(lambda _e: result.set_result(False))
                return Window(
                    FormattedTextControl(f"{message} [y/n]", focusable=True, key_bindings=kb),
                    dont_extend_height=True,
                )

            return bool(await show_modal(chrome, build))

        renderer = _renderer(chrome, bus, confirm=confirm)
        act = ActionDescriptor(plugin_id="descx", action="wipe", confirm="Really wipe?")
        await _open(live, renderer, _modal(act))
        live.pipe.send_text("1")
        await live.until(
            lambda: live.shows("Really wipe? [y/n]") and not live.shows("MODAL dm1"),
            "the confirm dialog in place of the modal",
        )
        live.pipe.send_text("y")
        await _closed(live)
        await wait_until(lambda: actions != [], what="the confirmed plugin_action emit")
    assert asked == ["Really wipe?"]
    assert actions == [act.model_dump(mode="json")]


async def test_a_modal_that_cannot_take_focus_is_refused_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The entry point every capturing modal goes through: a failed focus is no
    # longer swallowed, and the modal does not stay up holding no keys. The
    # shape #671's modal had: a container none of whose windows can take focus
    # (a bare Window is focused whatever its control says, and its bindings do
    # run).
    async with _live() as live:

        def build(_result: asyncio.Future[Any]) -> HSplit:
            return HSplit([Window(FormattedTextControl("NOT FOCUSABLE"), dont_extend_height=True)])

        with (
            caplog.at_level(logging.DEBUG, logger="aelix_coding_agent.tui.overlay"),
            pytest.raises(ValueError, match="focusable window"),
        ):
            # Bounded: before #671 this awaited a result nothing could set.
            await asyncio.wait_for(show_modal(live.chrome, build), timeout=WAIT_CEILING)
        # Refused before it is mounted (fix round 1, Codex M): the focus is not
        # even tried.
        assert any("no window that can take focus" in r.getMessage() for r in caplog.records)
        await live.until(
            lambda: not live.shows("NOT FOCUSABLE") and live.shows(_INPUT_PLACEHOLDER),
            "the refused modal to be gone",
        )
        assert live.chrome.app.layout.has_focus(live.chrome._input_window)


# --- the chrome's own keys under a modal (#671 fix round 1, Codex H + verify S1) ---
#
# The chrome's key bindings are the Application's, so they apply under any modal
# for every key the modal does not bind itself — and a key the modal takes with
# ``<any>`` loses to them (prompt_toolkit puts the specific binding last). Those
# that act on the input line read or wrote the one the modal hides: Alt+Enter
# queued it for the model, Ctrl+D quit, Tab and Ctrl+Space completed in it,
# Alt+Up and Ctrl+G and Ctrl+V filled it, and with a modal that does not take
# Enter or Ctrl+C itself, Enter submitted it and Ctrl+C cleared it or quit.

_ALT_ENTER = "\x1b\r"
_ALT_UP = "\x1b[1;3A"
_LARGE_PASTE = "\n".join(f"pasted line {i}" for i in range(40))


class _Calls:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def hook(self, name: str) -> Callable[..., None]:
        def _call(*_args: object, **_kwargs: object) -> None:
            self.seen.append(name)

        return _call


def _bare_build(result: asyncio.Future[Any]) -> Window:
    """A capturing modal that takes Esc only — no Enter, no Ctrl+C of its own."""

    kb = KeyBindings()
    kb.add("escape")(lambda _e: result.set_result(None))
    return Window(
        FormattedTextControl("MODAL dm1 bare", focusable=True, key_bindings=kb),
        dont_extend_height=True,
    )


async def _open_kind(live: _Live, kind: str) -> asyncio.Future[Any]:
    """Open one kind of capturing modal; a future that is done once it closed."""

    from aelix_coding_agent.tui.context import AelixTUIContext
    from aelix_coding_agent.tui.footer_data import AelixFooterData

    chrome = live.chrome
    if kind == "management":
        tasks: list[asyncio.Future[Any]] = []

        def spawn(coro: Any) -> asyncio.Future[Any]:
            task = asyncio.ensure_future(coro)
            tasks.append(task)
            return task

        renderer = _renderer(chrome, EventBus())
        renderer._spawn = spawn  # type: ignore[method-assign]
        await _open(live, renderer, _modal(_ACT1))
        return tasks[0]
    if kind == "select":
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        task = asyncio.ensure_future(ctx.select("MODAL dm1 select", ["one", "two"]))
    elif kind == "confirm":
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        task = asyncio.ensure_future(ctx.confirm("MODAL dm1 confirm", "sure?"))
    else:
        task = asyncio.ensure_future(show_modal(chrome, _bare_build))
    await live.until(
        lambda: live.shows("MODAL dm1") and not live.shows(_INPUT_PLACEHOLDER),
        f"the {kind} modal to replace the input row",
    )
    return task


def _arm(chrome: AelixChrome, key: str, calls: _Calls) -> None:
    from prompt_toolkit.completion import WordCompleter

    chrome.on_follow_up = calls.hook("follow_up")
    chrome.on_steer = calls.hook("steer")
    chrome.on_dequeue = calls.hook("dequeue")
    chrome.on_external_editor = calls.hook("external_editor")
    chrome.on_image_paste = calls.hook("image_paste")
    chrome.buffer.completer = WordCompleter(["secret_complete"])
    if key == "alt-enter":
        chrome.set_running(True)
    if key in ("alt-enter", "enter", "ctrl-c", "tab", "ctrl-space", "paste"):
        chrome.set_editor_text("sec" if key in ("tab", "ctrl-space") else "HIDDEN DRAFT")


_KEYS = {
    "alt-enter": _ALT_ENTER,
    "ctrl-d": "\x04",
    "tab": "\t\t",
    "ctrl-space": "\x00",
    "alt-up": _ALT_UP,
    "ctrl-g": "\x07",
    "ctrl-v": "\x16",
    "paste": f"\x1b[200~{_LARGE_PASTE}\x1b[201~",
    # Only the bare modal leaves these to the chrome (the others take them).
    "enter": "\r",
    "ctrl-c": "\x03",
}


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        *(
            (kind, key)
            for kind in ("management", "select", "bare")
            for key in ("alt-enter", "ctrl-d", "tab", "ctrl-space", "alt-up", "ctrl-g", "ctrl-v", "paste")
        ),
        ("bare", "enter"),
        # One press arms the exit and leaves the draft (fix round 2: two quit,
        # test_ctrl_c_twice_still_quits_from_under_a_modal_that_takes_no_keys).
        ("bare", "ctrl-c"),
    ],
)
async def test_the_chrome_keys_leave_the_hidden_input_alone_under_a_modal(
    kind: str, key: str
) -> None:
    calls = _Calls()
    async with _live() as live:
        chrome = live.chrome
        _arm(chrome, key, calls)
        draft = chrome.buffer.text
        closed = await _open_kind(live, kind)
        live.pipe.send_text(_KEYS[key])
        # Esc after them closes the modal: once it has, the keys before it have
        # been through the key processor, and the modal was still up for them.
        live.pipe.send_text(_ESC)
        await wait_until(closed.done, what=f"the {kind} modal to close on Esc", detail=live.dump)
        await live.until(lambda: live.shows(_INPUT_PLACEHOLDER) or bool(draft), "the input back")
        assert calls.seen == []
        assert chrome.buffer.text == draft
        assert chrome.buffer.complete_state is None
        assert chrome._input_queue.empty(), "a line or an EOF was queued under the modal"
        assert chrome._paste_registry == {}
        chrome.set_running(False)


@pytest.mark.parametrize("key", [_ALT_ENTER, _ALT_UP], ids=["alt-enter", "alt-up"])
async def test_alt_enter_and_alt_up_leave_the_modal_open(key: str) -> None:
    # Taken and dropped, not filtered out: with no binding for the pair, the key
    # processor runs it as Esc and then the second key, and the Esc closes it.
    seen: list[str] = []

    def build(result: asyncio.Future[Any]) -> Window:
        kb = KeyBindings()
        kb.add("escape")(lambda _e: result.set_result(None))
        kb.add("x")(lambda _e: seen.append("x"))
        return Window(
            FormattedTextControl("MODAL dm1 bare", focusable=True, key_bindings=kb),
            dont_extend_height=True,
        )

    async with _live() as live:
        chrome = live.chrome
        chrome.set_running(True)
        chrome.set_editor_text("HIDDEN DRAFT")
        closed = asyncio.ensure_future(show_modal(chrome, build))
        await live.until(lambda: live.shows("MODAL dm1 bare"), "the modal")
        live.pipe.send_text(key)
        live.pipe.send_text("x")  # after the pair: once it has been seen, the pair was
        await wait_until(lambda: seen == ["x"], what="the key after the pair", detail=live.dump)
        assert not closed.done(), "the pair closed the modal"
        chrome.set_running(False)
        live.pipe.send_text(_ESC)
        await wait_until(closed.done, what="the modal to close on Esc")


async def test_the_chrome_keys_act_on_the_input_again_once_the_modal_is_down() -> None:
    # The other side of the gate: it closes with the modal.
    calls = _Calls()
    async with _live() as live:
        chrome = live.chrome
        _arm(chrome, "alt-enter", calls)
        closed = await _open_kind(live, "bare")
        live.pipe.send_text(_ESC)
        await wait_until(closed.done, what="the bare modal to close")
        await wait_until(lambda: not chrome.is_modal_open(), what="the slot to empty")
        live.pipe.send_text(_ALT_ENTER)
        await wait_until(lambda: calls.seen == ["follow_up"], what="the follow-up")
        chrome.set_running(False)
        live.pipe.send_text("\x04")
        await wait_until(lambda: not chrome._input_queue.empty(), what="the EOF")


async def test_a_turn_is_still_interrupted_under_a_modal_that_does_not_take_ctrl_c() -> None:
    # The running branch of Ctrl+C touches no input: a modal that takes Ctrl+C
    # itself wins it, and under one that does not, it still stops the turn.
    calls = _Calls()
    async with _live() as live:
        chrome = live.chrome
        chrome.on_interrupt = calls.hook("interrupt")
        chrome.set_running(True)
        closed = await _open_kind(live, "bare")
        live.pipe.send_text(_CTRL_C)
        await wait_until(lambda: calls.seen == ["interrupt"], what="the interrupt", detail=live.dump)
        assert chrome.is_modal_open()
        chrome.set_running(False)
        live.pipe.send_text(_ESC)
        await wait_until(closed.done, what="the bare modal to close")


async def test_a_large_paste_into_an_editor_dialog_arrives_as_pasted() -> None:
    # The chrome's paste binding collapsed it to the input's placeholder, which
    # only the input's submit expands: the dialog returned "[Pasted text ...]".
    from aelix_coding_agent.tui.context import AelixTUIContext
    from aelix_coding_agent.tui.footer_data import AelixFooterData

    async with _live() as live:
        ctx = AelixTUIContext(live.chrome, AelixFooterData(cwd="."))
        task = asyncio.ensure_future(ctx.editor("MODAL dm1 editor"))
        await live.until(lambda: live.shows("MODAL dm1 editor"), "the editor dialog")
        live.pipe.send_text(f"\x1b[200~{_LARGE_PASTE}\x1b[201~")
        live.pipe.send_text("\x13")  # Ctrl+S saves
        await wait_until(task.done, what="the editor dialog to save", detail=live.dump)
        assert task.result() == _LARGE_PASTE
        assert live.chrome._paste_registry == {}


@pytest.mark.parametrize("shape", ["no-focusable-control", "focusable-but-hidden"])
async def test_a_refused_modal_leaves_the_open_one_as_it_was(shape: str) -> None:
    # Codex M: the refused modal replaced the open one in the single slot before
    # its focus failed, and the cleanup emptied the slot — the confirm under it
    # stayed pending with nothing on screen to answer it. The check is
    # prompt_toolkit's own: a focusable window under a hidden container is not
    # one it focuses.
    from aelix_coding_agent.tui.context import AelixTUIContext
    from aelix_coding_agent.tui.footer_data import AelixFooterData
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.layout import ConditionalContainer

    async with _live() as live:
        chrome = live.chrome
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        outer = asyncio.ensure_future(ctx.confirm("OUTER", "Keep me answerable?"))
        await live.until(lambda: live.shows("Keep me answerable?"), "the outer confirm")
        mounted = chrome._modal
        focused = chrome.app.layout.current_window

        def build(_result: asyncio.Future[Any]) -> HSplit:
            if shape == "focusable-but-hidden":
                hidden = Window(FormattedTextControl("NOT FOCUSABLE", focusable=True))
                return HSplit([ConditionalContainer(hidden, filter=Condition(lambda: False))])
            return HSplit([Window(FormattedTextControl("NOT FOCUSABLE"), dont_extend_height=True)])

        with pytest.raises(ValueError, match="focusable window"):
            await asyncio.wait_for(show_modal(chrome, build), timeout=WAIT_CEILING)
        assert chrome._modal is mounted
        assert chrome.app.layout.current_window is focused
        await asyncio.sleep(0.2)  # a repaint, had anything changed
        assert live.shows("Keep me answerable?") and not live.shows("NOT FOCUSABLE")
        live.pipe.send_text("y")
        await wait_until(outer.done, what="the outer confirm to take y", detail=live.dump)
        assert outer.result() is True
        await live.until(lambda: live.shows(_INPUT_PLACEHOLDER), "the input row back")


# 56: a width that ends inside a hint ("[5]" fits, "action5" does not).
@pytest.mark.parametrize("cols", [50, 56, 80, 120])
@pytest.mark.parametrize("count", [0, 3, 9])
async def test_the_hint_row_says_esc_closes_at_any_width(cols: int, count: int) -> None:
    # Owner, 2026-11-05: pi's dialogs all show their cancel key. The row is cut to
    # the terminal at its items, so "[Esc] close" is never cropped or split.
    actions = [ActionDescriptor(plugin_id="descx", action=f"action{i}") for i in range(1, count + 1)]
    async with _live(24, cols) as live:
        renderer = _renderer(live.chrome, EventBus())
        await _open(live, renderer, _modal(*actions))
        grid = live.grid()
        assert any(row.rstrip().endswith("[Esc] close") for row in grid), live.dump()
        hints = [i for i in range(1, count + 1) if any(f"[{i}] action{i}" in row for row in grid)]
        assert hints == list(range(1, count + 1)), live.dump()
        assert all(len(row) <= cols for row in grid)
        live.pipe.send_text(_ESC)
        await _closed(live)


# --- fix round 2 (verify S1) -------------------------------------------------


class _NoKeys:
    """A ``custom()`` component: it renders, and binds no key."""

    def render(self, width: int) -> list[str]:
        return ["MODAL dm1 custom"]

    def invalidate(self) -> None:
        return None

    def handle_input(self, data: str) -> None:
        return None


async def _open_custom(live: _Live) -> tuple[asyncio.Future[Any], Callable[[object], None]]:
    """``ctx.ui.custom()`` over the input; the future it returns and its ``done``."""

    from aelix_coding_agent.tui.context import AelixTUIContext
    from aelix_coding_agent.tui.footer_data import AelixFooterData

    dones: list[Callable[[object], None]] = []

    def factory(_tui: object, _theme: object, _kb: object, done: Callable[[object], None]) -> _NoKeys:
        dones.append(done)
        return _NoKeys()

    ctx = AelixTUIContext(live.chrome, AelixFooterData(cwd="."))
    task = asyncio.ensure_future(ctx.custom(factory))  # type: ignore[arg-type]
    await live.until(
        lambda: live.shows("MODAL dm1 custom") and not live.shows(_INPUT_PLACEHOLDER),
        "the custom overlay to replace the input row",
    )
    return task, dones[0]


@pytest.mark.parametrize("kind", ["custom", "bare"])
@pytest.mark.parametrize("draft", ["", "HIDDEN DRAFT"], ids=["empty-input", "hidden-draft"])
async def test_ctrl_c_twice_still_quits_from_under_a_modal_that_takes_no_keys(
    kind: str, draft: str
) -> None:
    """Verify round 2 S1: a ``custom()`` overlay binds no key. With the idle Ctrl+C
    off under a modal, Ctrl+C twice, Ctrl+D, Esc and typing all did nothing, and
    aelix had to be killed (6aed3863: Ctrl+C twice quit). It quits again, and the
    input the modal hides is still neither read, cleared nor sent (H1)."""

    async with _live() as live:
        chrome = live.chrome
        chrome.set_editor_text(draft)
        if kind == "custom":
            closed, done = await _open_custom(live)
        else:
            closed = await _open_kind(live, "bare")
        live.pipe.send_text(_CTRL_C)
        await wait_until(
            lambda: chrome._last_ctrl_c is not None, what="the first Ctrl+C to arm", detail=live.dump
        )
        assert chrome._input_queue.empty() and chrome.buffer.text == draft
        live.pipe.send_text(_CTRL_C)
        await wait_until(
            lambda: not chrome._input_queue.empty(), what="the second Ctrl+C's EOF", detail=live.dump
        )
        assert chrome._input_queue.get_nowait() is _EOF
        assert chrome._input_queue.empty(), "the hidden draft was submitted"
        assert chrome.buffer.text == draft
        if kind == "custom":
            done(None)
        else:
            live.pipe.send_text(_ESC)
        await wait_until(closed.done, what=f"the {kind} modal to close")


@pytest.mark.parametrize("kind", ["management", "select", "confirm"])
async def test_a_modal_that_takes_ctrl_c_still_wins_it(kind: str) -> None:
    """The chrome's Ctrl+C is on under every modal now; one that binds Ctrl+C runs
    its own, and the chrome's does not arm the exit as well."""

    async with _live() as live:
        chrome = live.chrome
        closed = await _open_kind(live, kind)
        live.pipe.send_text(_CTRL_C)
        await wait_until(closed.done, what=f"the {kind} modal to close on Ctrl+C", detail=live.dump)
        await wait_until(lambda: not chrome.is_modal_open(), what="the slot to empty")
        assert chrome._last_ctrl_c is None, "the chrome's Ctrl+C ran too: the next one would quit"
        assert chrome._input_queue.empty()
