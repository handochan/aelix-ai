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
