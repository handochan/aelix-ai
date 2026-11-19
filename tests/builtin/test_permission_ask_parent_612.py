"""#612 (ADR-0308, contract r5 §7.2, §7.4) — the child's gate asks its parent; the parent's arbiter decides.

The child's half: :class:`PermissionExtension` with ``ask_parent`` bound (a
``--command-inbox=2`` launch) sends a call its branch (d) would have blocked on
to (h) under the gate's lock and asks the parent; the answer is mapped as the
TUI dialog's is, a ``plan`` found after the answer blocks it and records no
grant, and a change too large to send is blocked unsent. What still never
asks: ``plan`` (blocked before), ``yolo`` (nothing to ask), a read-only tool,
an ``auto-accept-edits`` in-project write.

The parent's half: :meth:`PermissionExtension.decide_for_child`, the arbiter —
``plan`` before asking, again under the lock, raced against the open dialog and
after the answer; no approver; an approver that raises; one dialog at a time
behind this process's own prompt.

Fails on ``567753c0``: ``PermissionExtension`` takes no ``ask_parent`` (a
TypeError at construction) and has no ``decide_for_child``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_coding_agent.builtin.permission import (
    _HEADLESS_BLOCK_REASON,
    CHILD_NO_APPROVER_REASON,
    CHILD_PLAN_REASON,
    PermissionExtension,
)
from aelix_coding_agent.builtin.permission_mode import (
    PLAN_BLOCKED,
    PermissionMode,
    PermissionPosture,
)
from aelix_coding_agent.modes.print_inbox import (
    APPROVAL_REQUEST_MAX_BYTES,
    ApprovalTooLarge,
    ParentApprovals,
)

from tests.builtin.gate_tools import BUILTIN_CONTEXT


class _Ctx:
    def __init__(self, cwd: str = "/proj") -> None:
        self.has_ui = False
        self.ui = None
        self.cwd = cwd


class _Parent:
    """``ask_parent``: records each request and answers from a queue (or waits on a gate)."""

    def __init__(self, *answers: tuple[str, str | None]) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[Any, str]] = []
        self.gate: asyncio.Event | None = None
        self.arrived = asyncio.Event()

    async def __call__(self, request: Any, tool_call_id: str) -> tuple[str, str | None]:
        self.asked.append((request, tool_call_id))
        self.arrived.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.answers.pop(0) if self.answers else ("yes", None)


def _child(mode: PermissionMode = PermissionMode.DEFAULT, parent: _Parent | None = None) -> PermissionExtension:
    ext = PermissionExtension(posture=PermissionPosture(mode=mode), headless_default="block")
    ext.ask_parent = parent if parent is not None else _Parent()
    return ext


def _event(tool: str = "bash", args: dict[str, Any] | None = None, call_id: str = "c1") -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id=call_id,
        tool_name=tool,
        args=args if args is not None else {"command": "make build"},
        context=BUILTIN_CONTEXT,
    )


# === the child's gate ===========================================================


async def test_a_default_child_asks_its_parent_instead_of_blocking() -> None:
    parent = _Parent(("yes", None))
    ext = _child(parent=parent)
    assert await ext._on_tool_call(_event(), _Ctx()) is None  # type: ignore[arg-type]
    ((request, call_id),) = parent.asked
    assert call_id == "c1"
    assert (request.tool_name, request.kind, request.args) == ("bash", "bash", {"command": "make build"})


def test_without_ask_parent_the_child_is_blocked_headless_as_before() -> None:
    """Guard (passes on the base): no channel, today's verdict."""

    ext = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.DEFAULT), headless_default="block")
    result = asyncio.run(ext._on_tool_call(_event(), _Ctx()))  # type: ignore[arg-type]
    assert result is not None and result.block and result.reason == _HEADLESS_BLOCK_REASON


@pytest.mark.parametrize(
    ("mode", "tool", "args", "asks"),
    [
        (PermissionMode.PLAN, "bash", {"command": "make"}, False),
        (PermissionMode.YOLO, "bash", {"command": "make"}, False),
        (PermissionMode.DEFAULT, "read", {"path": "a.txt"}, False),
        (PermissionMode.DEFAULT, "write", {"path": "a.txt", "content": "x"}, True),
        (PermissionMode.DEFAULT, "edit", {"path": "a.txt", "oldText": "a", "newText": "b"}, True),
        (PermissionMode.AUTO_ACCEPT, "bash", {"command": "make"}, True),
        (PermissionMode.AUTO_ACCEPT, "write", {"path": "a.txt", "content": "x"}, False),
        (PermissionMode.AUTO_ACCEPT, "write", {"path": "/etc/hosts", "content": "x"}, True),
    ],
)
async def test_what_asks_is_what_the_base_would_block(
    tmp_path: Any, mode: PermissionMode, tool: str, args: dict[str, Any], asks: bool
) -> None:
    parent = _Parent(("yes", None))
    ext = _child(mode, parent)
    ctx = _Ctx(cwd=str(tmp_path))
    if "path" in args and not args["path"].startswith("/"):
        args = {**args, "path": str(tmp_path / args["path"])}
    result = await ext._on_tool_call(_event(tool, args), ctx)  # type: ignore[arg-type]
    assert bool(parent.asked) is asks
    if mode is PermissionMode.PLAN:
        assert result is not None and result.block


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (("no", None), "Denied by the user."),
        (("cancel", None), "Denied by the user (cancelled)."),
        (("no", CHILD_PLAN_REASON), f"Not approved: {CHILD_PLAN_REASON}."),
        (("cancel", "the agent was asked to wrap up"), "Not approved: the agent was asked to wrap up."),
        (("redirect", None), "Denied by the user (unexpected redirect)."),
    ],
)
async def test_each_refusal_is_the_childs_models_sentence(answer: tuple[str, str | None], reason: str) -> None:
    ext = _child(parent=_Parent(answer))
    result = await ext._on_tool_call(_event(), _Ctx())  # type: ignore[arg-type]
    assert result is not None and result.block and result.reason == reason


async def test_yes_for_this_agent_records_the_childs_own_grant() -> None:
    parent = _Parent(("yes_session", None))
    ext = _child(parent=parent)
    assert await ext._on_tool_call(_event(args={"command": "git log --oneline"}), _Ctx()) is None  # type: ignore[arg-type]
    assert await ext._on_tool_call(_event(args={"command": "git log -3"}, call_id="c2"), _Ctx()) is None  # type: ignore[arg-type]
    assert len(parent.asked) == 1, "the second call matched the child's own grant (its wildcard)"


async def test_a_plan_set_while_it_asked_blocks_a_yes_and_records_no_grant_so_a_parked_sibling_does_not_run() -> None:
    """§7 review L1 (``p1-gate-lock-posture.txt``): re-check, THEN grant."""

    parent = _Parent(("yes_session", None), ("yes_session", None))
    parent.gate = asyncio.Event()
    ext = _child(parent=parent)
    first = asyncio.ensure_future(ext._on_tool_call(_event(args={"command": "git status"}), _Ctx()))  # type: ignore[arg-type]
    await asyncio.wait_for(parent.arrived.wait(), 5)
    sibling = asyncio.ensure_future(
        ext._on_tool_call(_event(args={"command": "git status"}, call_id="c2"), _Ctx())  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)
    ext.posture.set(PermissionMode.PLAN)
    parent.gate.set()
    for task in (first, sibling):
        result = await asyncio.wait_for(task, 5)
        assert result is not None and result.block and result.reason.startswith(PLAN_BLOCKED)
    assert ext._session_allows == set()


async def test_a_grant_made_before_a_tighten_admits_no_call_parked_on_the_lock_after_it() -> None:
    """#612 fix round 1, P-M2 = V-F4 (V's probe shape): the first call is answered "Yes, for
    this agent" (a grant), the tighten to ``plan`` lands in the turn its verdict runs, and the
    sibling parked on the gate's lock wakes under ``plan``: blocked by plan, never asked.

    Fails on ``111e02d7``: the sibling passes on the grant (``None``, it runs)."""

    parent = _Parent(("yes_session", None))
    parent.gate = asyncio.Event()
    ext = _child(parent=parent)
    loop = asyncio.get_running_loop()

    class _Allows(set):  # type: ignore[type-arg]
        def add(self, rule: str) -> None:  # type: ignore[override]
            set.add(self, rule)
            loop.call_soon(ext.posture.set, PermissionMode.PLAN)  # the tighten's dispatch, same turn

    ext._session_allows = _Allows(ext._session_allows)  # type: ignore[assignment]
    first = asyncio.ensure_future(ext._on_tool_call(_event(args={"command": "git status"}), _Ctx()))  # type: ignore[arg-type]
    await asyncio.wait_for(parent.arrived.wait(), 5)
    sibling = asyncio.ensure_future(
        ext._on_tool_call(_event(args={"command": "git status --short"}, call_id="c2"), _Ctx())  # type: ignore[arg-type]
    )
    for _ in range(3):
        await asyncio.sleep(0)
    assert ext.posture.get() is PermissionMode.DEFAULT
    parent.gate.set()
    assert await asyncio.wait_for(first, 5) is None
    result = await asyncio.wait_for(sibling, 5)
    assert ext.posture.get() is PermissionMode.PLAN
    assert result is not None and result.block and result.reason.startswith(PLAN_BLOCKED), result
    assert [call_id for _r, call_id in parent.asked] == ["c1"]


async def test_a_parked_call_after_a_granted_one_and_a_tighten_is_blocked_by_plan() -> None:
    """P-M2 (P's probe shape): A is answered "Yes, for this agent", C is open when the parent
    tightens to ``plan`` and answered no, and B — parked on the lock since before, and matching
    A's grant — wakes under ``plan``: blocked, never asked. A call entering after the tighten is
    blocked at entry (control).

    Fails on ``111e02d7``: B passes on A's grant."""

    gates = {"A": asyncio.Event(), "C": asyncio.Event()}
    asked: list[str] = []

    async def ask_parent(_request: Any, call_id: str) -> tuple[str, str | None]:
        asked.append(call_id)
        await gates[call_id].wait()
        return ("yes_session", None) if call_id == "A" else ("no", CHILD_PLAN_REASON)

    ext = _child()
    ext.ask_parent = ask_parent
    a = asyncio.ensure_future(ext._on_tool_call(_event(args={"command": "git status"}, call_id="A"), _Ctx()))  # type: ignore[arg-type]
    c = asyncio.ensure_future(
        ext._on_tool_call(_event("write", {"path": "/proj/x.txt", "content": "x"}, call_id="C"), _Ctx())  # type: ignore[arg-type]
    )
    b = asyncio.ensure_future(ext._on_tool_call(_event(args={"command": "git status -s"}, call_id="B"), _Ctx()))  # type: ignore[arg-type]
    for _ in range(5):
        await asyncio.sleep(0)
    gates["A"].set()
    assert await asyncio.wait_for(a, 5) is None
    await asyncio.wait_for(_until(lambda: asked == ["A", "C"]), 5)
    ext.posture.set(PermissionMode.PLAN)
    gates["C"].set()
    assert (await asyncio.wait_for(c, 5)).block  # type: ignore[union-attr]
    result = await asyncio.wait_for(b, 5)
    assert result is not None and result.block and result.reason.startswith(PLAN_BLOCKED), result
    assert "B" not in asked
    control = await ext._on_tool_call(_event(args={"command": "git status -s"}, call_id="B2"), _Ctx())  # type: ignore[arg-type]
    assert control is not None and control.reason.startswith(PLAN_BLOCKED)


async def _until(predicate: Any) -> None:
    while not predicate():
        await asyncio.sleep(0)


async def test_a_change_too_large_to_show_is_blocked_unsent() -> None:
    approvals = ParentApprovals()
    written: list[str] = []
    approvals.bind(written.append)
    ext = PermissionExtension(posture=PermissionPosture(mode=PermissionMode.DEFAULT), headless_default="block")
    ext.ask_parent = approvals.ask
    big = {"path": "/proj/big.txt", "content": "y" * APPROVAL_REQUEST_MAX_BYTES}
    result = await ext._on_tool_call(_event("write", big), _Ctx())  # type: ignore[arg-type]
    assert result is not None and result.block
    assert result.reason.startswith("This change is too large to show for approval (")
    assert result.reason.endswith(" bytes; the limit is 1 MiB).")
    assert written == []


async def test_an_ask_that_raises_blocks_for_safety() -> None:
    async def broken(_request: Any, _call_id: str) -> tuple[str, str | None]:
        raise RuntimeError("no parent to ask")

    ext = _child()
    ext.ask_parent = broken
    result = await ext._on_tool_call(_event(), _Ctx())  # type: ignore[arg-type]
    assert result is not None and result.reason == "Permission prompt unavailable; denied for safety (RuntimeError)."


async def test_parent_approvals_write_one_line_and_end_on_its_answer() -> None:
    approvals = ParentApprovals()
    written: list[str] = []
    approvals.bind(written.append)
    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest

    asking = asyncio.ensure_future(approvals.ask(ApprovalRequest("bash", {"command": "ls"}, "bash"), "c9"))
    await asyncio.sleep(0)
    assert len(written) == 1 and written[0].startswith('{"type": "approval_request", "id": "ap-1"')
    with pytest.raises(ValueError, match="no approval 'ap-2' is pending"):
        approvals.answer({"request": "ap-2", "decision": "yes"})
    with pytest.raises(ValueError, match="is not an approval decision"):
        approvals.answer({"request": "ap-1", "decision": "no_reason"})
    assert approvals.answer({"request": "ap-1", "decision": "no", "reason": "because"}) == {"taken": True}
    assert await asking == ("no", "because")
    with pytest.raises(ValueError, match="pending"):
        approvals.answer({"request": "ap-1", "decision": "yes"})
    with pytest.raises(ApprovalTooLarge):
        await approvals.ask(ApprovalRequest("bash", {"command": "z" * APPROVAL_REQUEST_MAX_BYTES}, "bash"), "c10")


# === the parent's arbiter ======================================================


class _Approver:
    def __init__(self, answer: tuple[str, str | None] = ("yes", None)) -> None:
        self.answer = answer
        self.gate: asyncio.Event | None = None
        self.calls: list[Any] = []
        self.cancelled = 0
        self.opened = asyncio.Event()

    async def __call__(self, request: Any) -> tuple[str, str | None]:
        self.calls.append(request)
        self.opened.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return self.answer


def _parent(mode: PermissionMode = PermissionMode.DEFAULT, approver: Any = None) -> PermissionExtension:
    ext = PermissionExtension(posture=PermissionPosture(mode=mode))
    ext.child_approver = approver
    return ext


async def test_the_arbiter_passes_on_a_humans_answer() -> None:
    approver = _Approver(("yes_session", None))
    assert await _parent(approver=approver).decide_for_child("req") == ("yes_session", None)
    assert approver.calls == ["req"]


async def test_plan_refuses_unasked_and_no_approver_cancels() -> None:
    approver = _Approver()
    assert await _parent(PermissionMode.PLAN, approver).decide_for_child("r") == ("no", CHILD_PLAN_REASON)
    assert approver.calls == []
    assert await _parent().decide_for_child("r") == ("cancel", CHILD_NO_APPROVER_REASON)


async def test_plan_set_while_it_queued_on_the_lock_answers_no_without_a_dialog() -> None:
    approver = _Approver()
    ext = _parent(approver=approver)
    await ext._lock.acquire()  # this process's own prompt holds the lock
    asking = asyncio.ensure_future(ext.decide_for_child("r"))
    await asyncio.sleep(0.01)
    assert approver.calls == [], "one dialog at a time: it waits behind the parent's own"
    ext.posture.set(PermissionMode.PLAN)
    ext._lock.release()
    assert await asyncio.wait_for(asking, 5) == ("no", CHILD_PLAN_REASON)
    assert approver.calls == []


async def test_plan_set_while_the_dialog_is_open_closes_it_and_answers_no() -> None:
    approver = _Approver()
    approver.gate = asyncio.Event()
    ext = _parent(approver=approver)
    asking = asyncio.ensure_future(ext.decide_for_child("r"))
    await asyncio.wait_for(approver.opened.wait(), 5)
    ext.posture.set(PermissionMode.PLAN)
    assert await asyncio.wait_for(asking, 5) == ("no", CHILD_PLAN_REASON)
    assert approver.cancelled == 1, "the dialog was closed"
    assert ext.posture.listeners == [] or all(getattr(fn, "__name__", "") != "_listen" for fn in ext.posture.listeners)


async def test_plan_after_the_answer_answers_no() -> None:
    ext = _parent()

    async def answer_then_tighten(_request: Any) -> tuple[str, str | None]:
        ext.posture.set(PermissionMode.AUTO_ACCEPT)  # not plan: the race does not fire
        ext.posture.set(PermissionMode.DEFAULT)
        return ("yes", None)

    ext.child_approver = answer_then_tighten
    assert await ext.decide_for_child("r") == ("yes", None)
    ext2 = _parent()

    async def tighten_in_the_answer(_request: Any) -> tuple[str, str | None]:
        ext2.posture.mode = PermissionMode.PLAN  # a write no listener saw
        return ("yes", None)

    ext2.child_approver = tighten_in_the_answer
    assert await ext2.decide_for_child("r") == ("no", CHILD_PLAN_REASON)


async def test_an_approver_that_raises_answers_cancel_naming_it() -> None:
    async def broken(_request: Any) -> tuple[str, str | None]:
        raise RuntimeError("RPC dialog client is unavailable")

    assert await _parent(approver=broken).decide_for_child("r") == (
        "cancel",
        "the approval prompt failed (RuntimeError)",
    )


async def test_a_cancelled_arbiter_closes_its_dialog_and_releases_the_lock() -> None:
    approver = _Approver()
    approver.gate = asyncio.Event()
    ext = _parent(approver=approver)
    asking = asyncio.ensure_future(ext.decide_for_child("r"))
    await asyncio.wait_for(approver.opened.wait(), 5)
    asking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asking
    await asyncio.sleep(0)
    assert approver.cancelled == 1 and not ext._lock.locked()


@pytest.mark.parametrize(("mode", "approver", "answer"), [
    (PermissionMode.PLAN, "set", ("no", CHILD_PLAN_REASON)),
    (PermissionMode.DEFAULT, None, ("cancel", CHILD_NO_APPROVER_REASON)),
])
async def test_a_refusal_needs_no_turn_on_the_lock(mode: PermissionMode, approver: Any, answer: tuple[str, str]) -> None:
    """``plan`` and "no one to ask" answer at once, even while this process's own prompt holds the lock."""

    ext = _parent(mode, _Approver() if approver == "set" else None)
    await ext._lock.acquire()
    try:
        assert await asyncio.wait_for(ext.decide_for_child("r"), 1) == answer
    finally:
        ext._lock.release()


async def test_a_plan_that_closed_the_dialog_answers_no_even_if_the_posture_moved_on() -> None:
    approver = _Approver()
    approver.gate = asyncio.Event()
    ext = _parent(approver=approver)
    asking = asyncio.ensure_future(ext.decide_for_child("r"))
    await asyncio.wait_for(approver.opened.wait(), 5)
    ext.posture.set(PermissionMode.PLAN)
    ext.posture.set(PermissionMode.DEFAULT)  # shift+tab on past plan before the arbiter resumed
    assert await asyncio.wait_for(asking, 5) == ("no", CHILD_PLAN_REASON)


async def test_two_answers_in_one_turn_take_the_first_and_refuse_the_second() -> None:
    from aelix_coding_agent.tui.approval_dialog import ApprovalRequest

    approvals = ParentApprovals()
    approvals.bind(lambda _line: None)
    asking = asyncio.ensure_future(approvals.ask(ApprovalRequest("bash", {"command": "ls"}, "bash"), "c1"))
    await asyncio.sleep(0)
    assert approvals.answer({"request": "ap-1", "decision": "no"}) == {"taken": True}
    with pytest.raises(ValueError, match="no approval 'ap-1' is pending"):
        approvals.answer({"request": "ap-1", "decision": "yes"})
    assert await asking == ("no", None)
