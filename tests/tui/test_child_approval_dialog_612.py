"""#612 (ADR-0308, contract r5 §7.4; owner Q-B) — the TUI dialog for a delegated child's request.

The same dialog this process's own calls get, with a header row naming who asks
(``<profile> agent <id> asks``, its job when it runs as one, and its directory
when that is not the parent's: commands and relative paths are judged against
it), and "Yes, for this agent" for the grant the child keeps. A request with no
``requester`` — every approval of the parent's own calls — draws byte for byte
what it drew (guard). ``tui/shell.py``'s ``_child_approver`` maps the dialog's
answer to the wire's decision words.

Fails on ``567753c0``: ``ApprovalRequest`` takes no ``requester`` and
``tui/shell.py`` has no ``_child_approver``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any

import pytest
from aelix_coding_agent.subagent_contract import ChildApprovalRequest
from aelix_coding_agent.tui.approval_dialog import (
    _ROWS,
    ApprovalDecision,
    ApprovalRequest,
    build_approval_view,
    rows_for,
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(lines: list[str]) -> list[str]:
    return [_ANSI.sub("", line) for line in lines]


def _child(**kwargs: Any) -> ChildApprovalRequest:
    base: dict[str, Any] = {
        "request_id": "ap-1",
        "child_id": "sub-3f2a",
        "profile": "general-purpose",
        "tool_name": "bash",
        "kind": "bash",
        "args": {"command": "rm -rf build"},
        "call_id": "call_1",
        "cwd": "/repo",
        "parent_cwd": "/repo",
    }
    base.update(kwargs)
    return ChildApprovalRequest(**base)


def test_the_header_names_who_asks_above_the_requests_own_title() -> None:
    request = ApprovalRequest("bash", {"command": "rm -rf build"}, "bash", requester=_child().requester)
    lines = _plain(build_approval_view(request))
    assert lines[0] == "general-purpose agent sub-3f2a asks"
    assert "Run shell command?" in lines[1], lines[:3]
    assert any("rm -rf build" in line for line in lines)


@pytest.mark.parametrize(
    ("kwargs", "header"),
    [
        ({"job_id": "8c1e2f"}, "general-purpose agent sub-3f2a asks · background job 8c1e2f"),
        ({"cwd": "/repo/pkg/web"}, "general-purpose agent sub-3f2a asks · in pkg/web"),
        ({"job_id": "8c1e2f", "cwd": "/repo/pkg"}, "general-purpose agent sub-3f2a asks · background job 8c1e2f · in pkg"),
    ],
)
def test_the_header_names_the_childs_job_and_its_own_directory(kwargs: dict[str, Any], header: str) -> None:
    child = _child(**kwargs)
    assert child.requester == header
    lines = _plain(build_approval_view(ApprovalRequest("bash", child.args, "bash", requester=child.requester), width=120))
    assert lines[0] == header


def test_a_steering_character_in_the_header_is_named_never_sent() -> None:
    child = _child(cwd="/repo/a\x1b[8mb")
    lines = build_approval_view(ApprovalRequest("bash", child.args, "bash", requester=child.requester), width=120)
    assert "\x1b[8mb" not in lines[0], "the directory's escape reached the terminal"
    assert "^[[8mb" in _plain(lines)[0]


def test_the_childs_grant_row_reads_yes_for_this_agent() -> None:
    request = ApprovalRequest("bash", {"command": "ls"}, "bash", requester="x agent sub-1 asks")
    assert [label for _d, _m, label in rows_for(request)] == ["Yes", "Yes, for this agent", "No"]
    redirect = ApprovalRequest(
        "write", {"path": "p"}, "write", yes_label="Yes, at A", redirect_label="Yes, at B", requester="x"
    )
    assert [label for _d, _m, label in rows_for(redirect)] == ["Yes, at A", "Yes, for this agent", "Yes, at B", "No"]


def test_without_a_requester_the_dialog_is_byte_for_byte_what_it_was() -> None:
    """Guard (passes on the base): the parent's own approvals are untouched."""

    request = ApprovalRequest("bash", {"command": "ls"}, "bash")
    assert rows_for(request) is _ROWS
    lines = _plain(build_approval_view(request))
    assert "Run shell command?" in lines[0]


@pytest.mark.parametrize(
    ("decision", "word"),
    [
        (ApprovalDecision.YES, "yes"),
        (ApprovalDecision.YES_SESSION, "yes_session"),
        (ApprovalDecision.NO, "no"),
        (ApprovalDecision.REDIRECT, "redirect"),
        (ApprovalDecision.CANCEL, "cancel"),
        ("garbage", "cancel"),
    ],
)
async def test_the_tuis_child_approver_asks_the_dialog_and_answers_in_the_wires_words(decision: Any, word: str) -> None:
    from aelix_coding_agent.tui.shell import _child_approver

    seen: list[ApprovalRequest] = []

    async def dialog(request: ApprovalRequest) -> Any:
        seen.append(request)
        return decision

    child = _child(job_id="j1", yes_label=None)
    assert await _child_approver(dialog)(child) == (word, None)
    (shown,) = seen
    assert (shown.tool_name, shown.kind, shown.args) == ("bash", "bash", {"command": "rm -rf build"})
    assert shown.requester == "general-purpose agent sub-3f2a asks · background job j1"


# === fix round 1: the hold on approving keys (P-L4) and the wait behind an open modal (V-F3) ===
#
# The lead's calls (fix round 1): a child's dialog takes no approving key for
# ``CHILD_APPROVE_HOLD_SECONDS`` after its first paint — owner Q-B opens it at once,
# often while the user types to the parent — and it opens only when no modal is open
# (any dialog, panel or consent prompt), otherwise it waits behind it (#612's body:
# "열린 대화창이 없으면 즉시 열고, 있으면 그 뒤에 줄을 선다").


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    import aelix_coding_agent.tui.approval_dialog as approval_mod

    fake = _Clock()
    monkeypatch.setattr(approval_mod, "_clock", fake)
    return fake


def _child_dialog_request(**kwargs: Any) -> ApprovalRequest:
    return ApprovalRequest("bash", {"command": "rm -rf build"}, "bash", requester="general-purpose agent sub-3f2a asks", **kwargs)


@pytest.mark.parametrize(
    ("key", "decision"),
    [
        ("enter", ApprovalDecision.YES),
        ("c-j", ApprovalDecision.YES),
        ("1", ApprovalDecision.YES),
        ("y", ApprovalDecision.YES),
        ("Y", ApprovalDecision.YES),
        ("2", ApprovalDecision.YES_SESSION),
        ("s", ApprovalDecision.YES_SESSION),
        ("S", ApprovalDecision.YES_SESSION),
    ],
)
async def test_a_childs_dialog_takes_no_approving_key_in_its_first_half_second(
    clock: _Clock, key: str, decision: ApprovalDecision
) -> None:
    """P-L4: an approving key 0.49 s after the first paint is dropped (not queued); at 0.5 s
    the same key answers. Fails on ``111e02d7``: the first key approves."""

    from aelix_coding_agent.tui.approval_dialog import CHILD_APPROVE_HOLD_SECONDS
    from test_approval_dialog import _Open  # sibling harness: the real dialog, painted and keyed

    assert CHILD_APPROVE_HOLD_SECONDS == 0.5
    async with _Open({}, width=120, request=_child_dialog_request()) as dialog:
        dialog.paint(120, 30)
        clock.now += 0.49
        dialog.press(key)
        assert not dialog.answered, f"{key!r} approved a child's call 0.49 s after it was drawn"
        dialog.paint(120, 30)
        assert not dialog.answered, "the held key was queued and answered at the next paint"
        clock.now += 0.01
        dialog.press(key)
        assert await dialog.answer() is decision


@pytest.mark.parametrize("key", ["p", "3"])
async def test_a_childs_redirect_row_is_held_the_same_way(clock: _Clock, key: str) -> None:
    from test_approval_dialog import _Open

    request = ApprovalRequest(
        "write", {"path": "a.txt", "content": "x"}, "write",
        yes_label="Yes, at A", redirect_label="Yes, at B", requester="x agent sub-1 asks",
    )
    async with _Open({}, width=120, request=request) as dialog:
        dialog.paint(120, 30)
        dialog.press(key)
        assert not dialog.answered
        clock.now += 0.5
        dialog.press(key)
        assert await dialog.answer() is ApprovalDecision.REDIRECT


@pytest.mark.parametrize(("key", "decision"), [("3", ApprovalDecision.NO), ("n", ApprovalDecision.NO), ("escape", ApprovalDecision.CANCEL), ("c-c", ApprovalDecision.CANCEL)])
async def test_no_and_esc_answer_a_childs_dialog_at_once(clock: _Clock, key: str, decision: ApprovalDecision) -> None:
    """Guard (passes on ``111e02d7``): refusing is never held."""

    from test_approval_dialog import _Open

    async with _Open({}, width=120, request=_child_dialog_request()) as dialog:
        dialog.paint(120, 30)
        dialog.press(key)
        assert await dialog.answer() is decision


async def test_the_parents_own_dialog_takes_yes_at_its_first_paint(clock: _Clock) -> None:
    """Guard (passes on ``111e02d7``): a dialog with no requester is not held."""

    from test_approval_dialog import _Open

    async with _Open({}, width=120, request=ApprovalRequest("bash", {"command": "rm -rf build"}, "bash")) as dialog:
        dialog.paint(120, 30)
        dialog.press("1")
        assert await dialog.answer() is ApprovalDecision.YES


async def test_a_childs_approver_opens_its_dialog_only_once_no_modal_is_open() -> None:
    """V-F3: ``_child_approver`` waits on ``until_no_modal`` before it asks the dialog."""

    from aelix_coding_agent.tui.shell import _child_approver

    clear = asyncio.Event()
    shown: list[ApprovalRequest] = []

    async def dialog(request: ApprovalRequest) -> Any:
        shown.append(request)
        return ApprovalDecision.NO

    asking = asyncio.ensure_future(_child_approver(dialog, until_no_modal=clear.wait)(_child()))
    for _ in range(10):
        await asyncio.sleep(0)
    assert shown == [] and not asking.done()
    clear.set()
    assert await asyncio.wait_for(asking, 5) == ("no", None)
    assert len(shown) == 1


def _real_chrome() -> Any:
    import io

    from aelix_coding_agent.tui.chrome import AelixChrome
    from rich.console import Console

    return AelixChrome(console=Console(file=io.StringIO(), force_terminal=True, width=120))


def _plain_modal(label: str) -> tuple[Any, dict[str, asyncio.Future[Any]]]:
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    window = Window(FormattedTextControl(label, focusable=True))
    holder: dict[str, asyncio.Future[Any]] = {}

    def build(result: asyncio.Future[Any]) -> Any:
        holder["result"] = result
        return window

    return build, holder


@pytest.mark.parametrize("stacked", [False, True], ids=["one-modal", "two-stacked"])
async def test_a_childs_dialog_waits_behind_an_open_modal_and_opens_when_none_is_left(stacked: bool) -> None:
    """V-F3, V's probe shape (``probes/tui/test_probe_child_dialog_over_open_dialog.py``): the
    real chrome, ``show_modal``, the real dialog and the arbiter. A consent-like dialog is open
    (and, stacked, a second over it): the child's dialog does not mount over them; once the last
    closes it opens, and its own keys answer it. Fails on ``111e02d7``: the child's dialog
    mounted on top and the first was stacked under it."""

    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
    from aelix_coding_agent.tui.approval_dialog import run_approval_dialog
    from aelix_coding_agent.tui.overlay import show_modal
    from aelix_coding_agent.tui.shell import _child_approver
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from tests.event_waits import wait_until

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _real_chrome()
        app_task = asyncio.create_task(chrome.run())
        try:
            await wait_until(lambda: chrome.app.is_running, what="the app")
            builds = [_plain_modal("Delegate to agent 'x'? [consent-like dialog]")]
            if stacked:
                builds.append(_plain_modal("a select a turn opened over it"))
            opened = [asyncio.ensure_future(show_modal(chrome, build)) for build, _h in builds]
            await wait_until(lambda: all("result" in h for _b, h in builds), what="the modals")

            async def run_dialog(request: object) -> object:
                return await run_approval_dialog(
                    request=request, show_modal=show_modal, chrome=chrome, render_diff=None, width=lambda: 120  # type: ignore[arg-type]
                )

            ext = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.DEFAULT))
            ext.child_approver = _child_approver(run_dialog, until_no_modal=chrome.until_no_modal)
            on_top = chrome._modal
            asking = asyncio.ensure_future(ext.decide_for_child(_child(job_id="j1")))
            for _ in range(20):
                await asyncio.sleep(0)
            assert chrome._modal is on_top, "the child's dialog mounted over the open modal"
            assert len(chrome._modal_stack) == (1 if stacked else 0)
            assert not asking.done()
            for (_b, holder), task in reversed(list(zip(builds, opened, strict=True))):
                holder["result"].set_result(None)
                await asyncio.wait_for(task, 5)
                if task is not opened[0]:
                    for _ in range(10):
                        await asyncio.sleep(0)
                    assert not asking.done(), "it opened while a modal was still open"
            await wait_until(lambda: chrome.is_modal_open(), what="the child's dialog to open")
            assert chrome._modal is not on_top and chrome._modal_stack == []
            pipe.send_text("n")
            assert await asyncio.wait_for(asking, 5) == ("no", None)
            assert chrome._no_modal_waiters == []
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(app_task, timeout=3)


async def test_a_tighten_while_it_waits_behind_a_modal_answers_no_and_leaves_no_waiter() -> None:
    """V-F3 with the arbiter's plan race: the wait is cancelled with the dialog that never opened."""

    from aelix_coding_agent.builtin.permission import CHILD_PLAN_REASON, PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
    from aelix_coding_agent.tui.overlay import show_modal
    from aelix_coding_agent.tui.shell import _child_approver
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from tests.event_waits import wait_until

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _real_chrome()
        app_task = asyncio.create_task(chrome.run())
        try:
            await wait_until(lambda: chrome.app.is_running, what="the app")
            build, holder = _plain_modal("a panel")
            first = asyncio.ensure_future(show_modal(chrome, build))
            await wait_until(lambda: "result" in holder, what="the modal")
            shown: list[object] = []

            async def run_dialog(request: object) -> object:
                shown.append(request)
                raise AssertionError("no dialog may open")

            posture = PermissionPosture(mode=PermissionMode.DEFAULT)
            ext = PermissionExtension(posture=posture)
            ext.child_approver = _child_approver(run_dialog, until_no_modal=chrome.until_no_modal)
            asking = asyncio.ensure_future(ext.decide_for_child(_child()))
            await wait_until(lambda: chrome._no_modal_waiters, what="the child's wait")
            posture.set(PermissionMode.PLAN)
            assert await asyncio.wait_for(asking, 5) == ("no", CHILD_PLAN_REASON)
            assert chrome._no_modal_waiters == [] and shown == []
            assert not ext._lock.locked()
            holder["result"].set_result(None)
            await asyncio.wait_for(first, 5)
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(app_task, timeout=3)


async def test_run_tui_binds_the_childs_approver_and_it_waits_for_the_open_modal() -> None:
    """V-F5 T1 and V-F3's wiring: ``run_tui`` binds ``child_approver`` (removing that line made
    every TUI child read-only and failed no test) to the dialog runner, behind
    ``out_chrome.until_no_modal``. Fails on ``111e02d7``: the dialog is asked while a modal is
    open."""

    import io

    import aelix_coding_agent.tui.approval_dialog as approval_mod
    from aelix_coding_agent.tui.chrome import AelixChrome
    from aelix_coding_agent.tui.overlay import show_modal
    from aelix_coding_agent.tui.shell import run_tui
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output
    from test_run_tui_smoke import FakeHarness, FakeRuntime, _quit_within, _wait  # sibling

    seen: list[Any] = []

    async def _spy(**kwargs: Any) -> ApprovalDecision:
        seen.append(kwargs["request"])
        return ApprovalDecision.YES_SESSION

    class _FakePermissionExt:
        approval_runner: Any = None
        child_approver: Any = None

    real_runner = approval_mod.run_approval_dialog
    approval_mod.run_approval_dialog = _spy  # type: ignore[assignment]
    try:
        output = Vt100_Output(io.StringIO(), get_size=lambda: Size(rows=40, columns=120), term="xterm-256color")
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
            chrome = AelixChrome(pt_input=pipe, pt_output=output)
            permission_ext = _FakePermissionExt()
            task = asyncio.ensure_future(
                run_tui(
                    FakeRuntime(FakeHarness()),  # type: ignore[arg-type]
                    cwd=".",
                    chrome=chrome,
                    install_signal_handlers=False,
                    permission_ext=permission_ext,  # type: ignore[arg-type]
                )
            )
            await _wait(lambda: chrome.app.is_running)
            await _wait(lambda: permission_ext.child_approver is not None)
            build, holder = _plain_modal("a consent-like dialog")
            first = asyncio.ensure_future(show_modal(chrome, build))
            await _wait(lambda: "result" in holder)
            asking = asyncio.ensure_future(permission_ext.child_approver(_child(job_id="j1")))
            for _ in range(20):
                await asyncio.sleep(0)
            assert seen == [] and not asking.done(), "the child's dialog was asked over an open modal"
            holder["result"].set_result(None)
            await asyncio.wait_for(first, 5)
            assert await asyncio.wait_for(asking, 5) == ("yes_session", None)
            (request,) = seen
            assert request.requester == "general-purpose agent sub-3f2a asks · background job j1"
            pipe.send_text("/quit\n")
            await _quit_within(task)
    finally:
        approval_mod.run_approval_dialog = real_runner  # type: ignore[assignment]


async def test_until_no_modal_waits_again_when_a_modal_is_mounted_before_it_resumes() -> None:
    """V-F3: the wake is a hint, the slot is the truth. A modal that closes and another that
    mounts in the same turn (before the waiter runs) keeps the child's dialog waiting."""

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import DummyOutput

    from tests.event_waits import wait_until

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _real_chrome()
        app_task = asyncio.create_task(chrome.run())
        try:
            await wait_until(lambda: chrome.app.is_running, what="the app")
            first = Window(FormattedTextControl("first", focusable=True))
            second = Window(FormattedTextControl("second", focusable=True))
            assert await asyncio.wait_for(chrome.until_no_modal(), 1) is None, "none open: at once"
            chrome.mount_modal(first, focus=first)
            waiting = asyncio.ensure_future(chrome.until_no_modal())
            await wait_until(lambda: chrome._no_modal_waiters, what="the wait")
            chrome.unmount_modal(first)
            chrome.mount_modal(second, focus=second)  # same turn: the woken waiter has not run
            for _ in range(10):
                await asyncio.sleep(0)
            assert not waiting.done(), "it returned with a modal open"
            chrome.unmount_modal(second)
            await asyncio.wait_for(waiting, 5)
            assert chrome._no_modal_waiters == []
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(app_task, timeout=3)


# === fix round 2, F2: the hold again when the child's dialog is back on top ===
#
# The verify's ``probes/tui/tui_queue_and_reexpose.py reexpose``: a spawn consent, an
# extension dialog or a panel can mount over an open child dialog; when it closes, a
# second Enter (a double press on the covering dialog) landed on the child's dialog
# and approved its call at once (``('yes', None)`` on ``510b186c``). The chrome's
# ``modal_generation`` changes with the modal on screen, and the dialog holds approving
# keys until its first paint back on top and for ``CHILD_APPROVE_HOLD_SECONDS`` from it.


class _Generation:
    """A chrome with the TUI's ``modal_generation``, moved by hand."""

    def __init__(self) -> None:
        self.value = 1

    def invalidate(self) -> None:
        return None

    def modal_generation(self) -> int:
        return self.value


async def _open_with(chrome: Any, request: ApprovalRequest) -> tuple[asyncio.Future[Any], dict[str, Any]]:
    from aelix_coding_agent.tui.approval_dialog import run_approval_dialog
    from test_approval_dialog import _build_runner_modal

    captured: dict[str, Any] = {}
    task = asyncio.ensure_future(
        run_approval_dialog(request=request, show_modal=_build_runner_modal(captured), chrome=chrome, width=120)
    )
    for _ in range(50):
        if "kb" in captured:
            break
        await asyncio.sleep(0)
    return task, captured


@pytest.mark.parametrize("key", ["enter", "1", "y", "2", "s"])
async def test_a_childs_dialog_back_on_top_takes_no_approving_key_until_half_a_second_after_its_repaint(
    clock: _Clock, key: str
) -> None:
    """F2: covered (the generation moves) after its hold ended, the dialog drops an approving key
    before its next paint and for 0.49 s after it, and takes it at 0.5 s. Fails on ``510b186c``:
    the first key after the generation moved approved (the hold counted from the first paint only)."""

    from test_approval_dialog import _paint, _press

    chrome = _Generation()
    task, captured = await _open_with(chrome, _child_dialog_request())
    try:
        _paint(captured, 120, 30)
        clock.now += 5.0  # the first paint's hold is long over
        chrome.value += 2  # a modal mounted over it, and closed: it is on top again
        _press(captured, key)
        assert not captured["result"].done(), f"{key!r} approved before the dialog was drawn again"
        _paint(captured, 120, 30)
        clock.now += 0.49
        _press(captured, key)
        assert not captured["result"].done(), f"{key!r} approved 0.49 s after the dialog was back"
        clock.now += 0.01
        _press(captured, key)
        assert await asyncio.wait_for(task, 2) in (ApprovalDecision.YES, ApprovalDecision.YES_SESSION)
    finally:
        if not task.done():
            captured["result"].cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(("key", "decision"), [("n", ApprovalDecision.NO), ("escape", ApprovalDecision.CANCEL)])
async def test_no_and_esc_answer_a_childs_dialog_back_on_top_at_once(
    clock: _Clock, key: str, decision: ApprovalDecision
) -> None:
    """Guard: refusing is never held, before the repaint either."""

    chrome = _Generation()
    task, captured = await _open_with(chrome, _child_dialog_request())
    from test_approval_dialog import _paint, _press

    _paint(captured, 120, 30)
    chrome.value += 1
    _press(captured, key)
    assert await asyncio.wait_for(task, 2) is decision


async def test_the_parents_own_dialog_back_on_top_is_not_held(clock: _Clock) -> None:
    """Guard: a dialog with no requester takes Yes whatever the generation does."""

    from test_approval_dialog import _press

    chrome = _Generation()
    task, captured = await _open_with(chrome, ApprovalRequest("bash", {"command": "ls"}, "bash"))
    from test_approval_dialog import _paint

    _paint(captured, 120, 30)
    chrome.value += 2
    _press(captured, "1")
    assert await asyncio.wait_for(task, 2) is ApprovalDecision.YES


async def test_a_second_enter_after_a_covering_modal_closes_does_not_approve_the_childs_call(
    clock: _Clock,
) -> None:
    """F2, the verify's ``reexpose`` shape: the real chrome, ``show_modal``, the real dialog and
    the arbiter. The child's dialog is open past its hold; a consent-like modal mounts over it
    and closes; an Enter sent at once does not approve, and one sent 0.5 s (the stand-in clock)
    after the dialog is back answers it. Fails on ``510b186c``: the first Enter answered
    ``('yes', None)``."""

    from aelix_coding_agent.builtin.permission import PermissionExtension
    from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
    from aelix_coding_agent.tui.approval_dialog import run_approval_dialog
    from aelix_coding_agent.tui.overlay import show_modal
    from aelix_coding_agent.tui.shell import _child_approver
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from tests.event_waits import wait_until

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _real_chrome()
        app_task = asyncio.create_task(chrome.run())
        try:
            await wait_until(lambda: chrome.app.is_running, what="the app")

            async def run_dialog(request: object) -> object:
                return await run_approval_dialog(
                    request=request, show_modal=show_modal, chrome=chrome, render_diff=None, width=lambda: 120  # type: ignore[arg-type]
                )

            ext = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.DEFAULT))
            ext.child_approver = _child_approver(run_dialog, until_no_modal=chrome.until_no_modal)
            asking = asyncio.ensure_future(ext.decide_for_child(_child(job_id="j1")))
            await wait_until(lambda: chrome.is_modal_open(), what="the child's dialog")
            child_modal = chrome._modal
            drawn = chrome.app.render_counter
            await wait_until(lambda: chrome.app.render_counter > drawn + 1, what="the child's dialog drawn")
            clock.now += 5.0  # its first paint's hold is long over
            build, holder = _plain_modal("Delegate to agent 'x'? [consent-like dialog over it]")
            over = asyncio.ensure_future(show_modal(chrome, build))
            await wait_until(lambda: "result" in holder, what="the modal over it")
            assert chrome._modal is not child_modal
            drawn = chrome.app.render_counter
            await wait_until(lambda: chrome.app.render_counter > drawn + 1, what="the covering modal drawn")
            holder["result"].set_result(None)  # Enter on the covering dialog closes it ...
            await asyncio.wait_for(over, 5)
            assert chrome._modal is child_modal
            pipe.send_text("\r")  # ... and the second Enter of a double press lands here
            drawn = chrome.app.render_counter
            await wait_until(lambda: chrome.app.render_counter > drawn + 1, what="the child's dialog drawn again")
            for _ in range(10):
                await asyncio.sleep(0)
            assert not asking.done(), f"the second Enter approved the child's call: {asking.result()}"
            clock.now += 0.5
            pipe.send_text("\r")
            assert await asyncio.wait_for(asking, 5) == ("yes", None)
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(app_task, timeout=3)


async def test_the_modal_generation_moves_with_the_modal_on_screen_only() -> None:
    """F2: what the dialog reads. A mount over, a close of the top one and the last close move
    it; a covered modal leaving from under the top one changes nothing on screen and does not."""

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.layout import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = _real_chrome()
        first = Window(FormattedTextControl("first", focusable=True))
        second = Window(FormattedTextControl("second", focusable=True))
        third = Window(FormattedTextControl("third", focusable=True))
        start = chrome.modal_generation()
        chrome.mount_modal(first, focus=first)
        chrome.mount_modal(second, focus=second)
        chrome.mount_modal(third, focus=third)
        assert chrome.modal_generation() == start + 3
        chrome.unmount_modal(first)  # covered: it leaves the stack, the screen keeps ``third``
        assert chrome.modal_generation() == start + 3
        chrome.unmount_modal(third)
        assert chrome._modal is second and chrome.modal_generation() == start + 4
        chrome.unmount_modal(second)
        assert chrome._modal is None and chrome.modal_generation() == start + 5
