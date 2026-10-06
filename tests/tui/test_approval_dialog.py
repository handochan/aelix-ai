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


async def _drive(request: ApprovalRequest, key: str) -> ApprovalDecision:
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
    """A ``kind="other"`` dialog held open, painted and keyed by hand."""

    def __init__(self, args: dict[str, Any], width: Any = 80) -> None:
        self.request = ApprovalRequest("fs__write_file", dict(args), "other")
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
        from prompt_toolkit.layout.containers import to_container
        from prompt_toolkit.layout.mouse_handlers import MouseHandlers
        from prompt_toolkit.layout.screen import Screen, WritePosition

        screen = Screen()
        to_container(self.captured["window"]).write_to_screen(
            screen, MouseHandlers(), WritePosition(0, 0, width, height), "", False, None
        )
        text = "\n".join(
            "".join(screen.data_buffer[y][x].char for x in range(width)).rstrip()
            for y in range(height)
        )
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


async def test_aelix_own_bash_dialog_is_not_held() -> None:
    """The hold is for a tool aelix did not build; bash keeps its behaviour."""

    assert await _drive(_REQ, "1") is ApprovalDecision.YES
