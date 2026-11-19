"""#612 (ADR-0308, contract r5 §7.4, §7.5) — an RPC host answers a delegated child's approval request.

``ui/enable {enabled: true, childApprovals: true}`` makes the host the one who
answers: the process's ``PermissionExtension`` gets the plane's dialog client as
its ``child_approver``. Absent, ``false``, ``enabled`` not ``true``, or no
delegation seam: nothing is bound, and the plane's own earlier binding is taken
back; ``ui/enable`` without the param answers as it did in v1. The request is an
``extension_ui_request`` with ``method: "childApproval"`` and three choices; the
reply is a choice id, and every other ending is ``cancel`` with one of three
reasons (§7 review L3): the host gone, no answer within the bound, the host's
own cancel.

Fails on ``567753c0``: ``ui/enable`` ignored ``childApprovals`` (and answered
``{"enabled": true}``), ``PermissionExtension`` had no ``child_approver`` and
``RpcCommandUI`` no ``child_approval``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.builtin.permission import PermissionExtension
from aelix_coding_agent.rpc import control_ui
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.control_ui import CHILD_APPROVAL_CHOICES, RpcCommandUI
from aelix_coding_agent.subagent_contract import ChildApprovalRequest

from tests.rpc.test_children_family_612 import ChildrenRig


def _request(**kwargs: Any) -> ChildApprovalRequest:
    base: dict[str, Any] = {
        "request_id": "ap-1",
        "child_id": "sub-3f2a",
        "profile": "general-purpose",
        "tool_name": "bash",
        "kind": "bash",
        "args": {"command": "make"},
        "call_id": "call_1",
        "session_id": "s-1",
        "parent_tool_call_id": "call_p",
        "cwd": "/repo/sub",
        "parent_cwd": "/repo",
        "epoch": "e-1",
    }
    base.update(kwargs)
    return ChildApprovalRequest(**base)


async def _plane(tmp_path: Path, *, permissions: Any = "new") -> tuple[RpcControlPlane, PermissionExtension | None, list[dict[str, Any]]]:
    rig = await ChildrenRig.create(tmp_path, list)
    frames: list[dict[str, Any]] = []
    ext = PermissionExtension() if permissions == "new" else permissions
    plane = RpcControlPlane(rig.runtime, emit=frames.append, permissions=ext)
    return plane, ext, frames


# === ui/enable ===================================================================


async def test_child_approvals_bind_the_planes_dialog_and_unbind_when_turned_off(tmp_path: Path) -> None:
    plane, ext, _frames = await _plane(tmp_path)
    assert ext is not None and plane.controls is not None
    assert await plane.handle("ui/enable", {"enabled": True, "childApprovals": True}) == {
        "enabled": True,
        "childApprovals": True,
    }
    assert ext.child_approver == plane.ui.child_approval
    assert await plane.handle("ui/enable", {"enabled": True, "childApprovals": False}) == {
        "enabled": True,
        "childApprovals": False,
    }
    assert ext.child_approver is None
    await plane.handle("ui/enable", {"enabled": True, "childApprovals": True})
    assert await plane.handle("ui/enable", {"enabled": True}) == {"enabled": True}, "v1's answer, byte for byte"
    assert ext.child_approver is None, "absent is off"
    assert await plane.handle("ui/enable", {"enabled": False, "childApprovals": True}) == {
        "enabled": False,
        "childApprovals": False,
    }
    assert ext.child_approver is None


async def test_another_approver_is_never_taken_back_by_the_plane(tmp_path: Path) -> None:
    plane, ext, _frames = await _plane(tmp_path)
    assert ext is not None

    async def tui(_request: Any) -> tuple[str, str | None]:
        return ("yes", None)

    ext.child_approver = tui
    await plane.handle("ui/enable", {"enabled": True, "childApprovals": False})
    assert ext.child_approver is tui


async def test_no_permission_extension_or_no_delegation_seam_binds_nothing(tmp_path: Path) -> None:
    plane, _ext, _frames = await _plane(tmp_path, permissions=None)
    assert await plane.handle("ui/enable", {"enabled": True, "childApprovals": True}) == {
        "enabled": True,
        "childApprovals": False,
    }
    plane2, ext2, _ = await _plane(tmp_path / "b")
    assert ext2 is not None
    plane2.controls = None  # a runtime with no delegation seam (the WebSocket host's ``bare``)
    assert (await plane2.handle("ui/enable", {"enabled": True, "childApprovals": True}))["childApprovals"] is False
    assert ext2.child_approver is None


# === the dialog =================================================================


async def _answered(ui: RpcCommandUI, frames: list[dict[str, Any]], reply: dict[str, Any] | None) -> tuple[str, str | None]:
    asking = asyncio.ensure_future(ui.child_approval(_request()))
    for _ in range(100):
        if frames:
            break
        await asyncio.sleep(0.005)
    (frame,) = frames
    if reply is not None:
        ui.respond({"id": frame["id"], **reply})
    return await asyncio.wait_for(asking, 5)


async def test_the_request_names_the_child_and_its_call_and_offers_three_choices() -> None:
    frames: list[dict[str, Any]] = []
    ui = RpcCommandUI(frames.append)
    ui.enabled = True
    assert await _answered(ui, frames, {"value": "yes_session"}) == ("yes_session", None)
    (frame,) = frames
    assert frame["type"] == "extension_ui_request" and frame["method"] == "childApproval"
    assert frame["title"] == "general-purpose agent sub-3f2a asks · in sub: Run shell command?"
    assert frame["child"] == {
        "id": "sub-3f2a", "profile": "general-purpose", "sessionId": "s-1", "jobId": None,
        "parentToolCallId": "call_p", "background": False, "cwd": "/repo/sub", "epoch": "e-1",
    }
    assert frame["tool"] == {"callId": "call_1", "name": "bash", "kind": "bash", "args": {"command": "make"}}
    assert frame["choices"] == [dict(c) for c in CHILD_APPROVAL_CHOICES]
    assert "options" not in frame, "no generic options: a generic dialog can approve nothing"


@pytest.mark.parametrize(
    ("reply", "answer"),
    [
        ({"value": "yes"}, ("yes", None)),
        ({"value": "no"}, ("no", None)),
        ({"value": "redirect"}, ("cancel", "the host cancelled the request")),
        ({"value": "Yes"}, ("cancel", "the host cancelled the request")),
        ({"cancelled": True}, ("cancel", "the host cancelled the request")),
        # V-F5 U1 (fix round 1): a cancelled reply that also names a choice approves nothing.
        ({"cancelled": True, "value": "yes"}, ("cancel", "the host cancelled the request")),
        ({"cancelled": True, "value": "yes_session"}, ("cancel", "the host cancelled the request")),
    ],
)
async def test_any_value_but_an_offered_choice_is_a_cancel(reply: dict[str, Any], answer: tuple[str, str | None]) -> None:
    frames: list[dict[str, Any]] = []
    ui = RpcCommandUI(frames.append)
    ui.enabled = True
    assert await _answered(ui, frames, reply) == answer


async def test_no_answer_within_the_bound_and_the_host_gone_have_their_own_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control_ui, "DIALOG_TIMEOUT_SECONDS", 0.05)
    frames: list[dict[str, Any]] = []
    ui = RpcCommandUI(frames.append)
    ui.enabled = True
    assert await _answered(ui, frames, None) == ("cancel", "no answer from the host within 0.05 s")
    frames.clear()
    monkeypatch.setattr(control_ui, "DIALOG_TIMEOUT_SECONDS", 30.0)
    asking = asyncio.ensure_future(ui.child_approval(_request()))
    for _ in range(100):
        if frames:
            break
        await asyncio.sleep(0.005)
    ui.close()
    assert await asyncio.wait_for(asking, 5) == ("cancel", "the host is gone")


async def test_dialogs_off_raises_so_the_arbiter_answers_cancel_naming_it() -> None:
    ui = RpcCommandUI(lambda _frame: None)
    ext = PermissionExtension()
    ext.child_approver = ui.child_approval
    assert await ext.decide_for_child(_request()) == ("cancel", "the approval prompt failed (RuntimeError)")


async def test_shutdown_ends_an_open_child_dialog_as_the_host_gone(tmp_path: Path) -> None:
    plane, ext, frames = await _plane(tmp_path)
    assert ext is not None
    await plane.handle("ui/enable", {"enabled": True, "childApprovals": True})
    asking = asyncio.ensure_future(ext.decide_for_child(_request()))
    for _ in range(100):
        if any(f.get("method") == "childApproval" for f in frames):
            break
        await asyncio.sleep(0.005)
    plane.begin_shutdown()
    assert await asyncio.wait_for(asking, 5) == ("cancel", "the host is gone")


async def _open_child_dialog(plane: RpcControlPlane, ext: PermissionExtension, frames: list[dict[str, Any]]) -> tuple[asyncio.Future[Any], dict[str, Any]]:
    asking = asyncio.ensure_future(ext.decide_for_child(_request()))
    for _ in range(200):
        asked = [f for f in frames if f.get("method") == "childApproval"]
        if asked:
            return asking, asked[0]
        await asyncio.sleep(0.005)
    raise AssertionError("no childApproval frame")


@pytest.mark.parametrize(
    "off",
    [
        {"enabled": True, "childApprovals": False},
        {"enabled": True},
        {"enabled": False, "childApprovals": False},
        {"enabled": False},
    ],
)
async def test_turning_child_approvals_off_ends_the_open_request_as_no_one_to_answer(
    tmp_path: Path, off: dict[str, Any]
) -> None:
    """#612 fix round 1, P-L3 (P's ``p3_turn_off_with_open_dialog.py`` shape): the host turns
    ``childApprovals`` off with a request open. That request ends at once, "no one can answer
    approvals here", the gate's lock is free, and the host's later "yes" approves nothing.

    Fails on ``111e02d7``: the request stays open (the lock held) and the later "yes" approves."""

    plane, ext, frames = await _plane(tmp_path)
    assert ext is not None
    await plane.handle("ui/enable", {"enabled": True, "childApprovals": True})
    asking, frame = await _open_child_dialog(plane, ext, frames)
    await plane.handle("ui/enable", off)
    assert ext.child_approver is None
    for _ in range(20):
        if asking.done():
            break
        await asyncio.sleep(0)
    assert asking.done(), "the open request outlived the turn-off"
    plane.ui.respond({"id": frame["id"], "value": "yes"})
    assert await asking == ("cancel", "no one can answer approvals here")
    assert not ext._lock.locked() and plane.ui.pending == {}
    assert await ext.decide_for_child(_request(request_id="ap-2")) == ("cancel", "no one can answer approvals here")


async def test_a_turn_off_leaves_the_planes_other_dialogs_open() -> None:
    """Guard: only ``childApproval`` dialogs are withdrawn; a command's ``select`` stays open."""

    frames: list[dict[str, Any]] = []
    ui = RpcCommandUI(frames.append)
    ui.enabled = True
    selecting = asyncio.ensure_future(ui.select("pick", ["a", "b"]))
    asking = asyncio.ensure_future(ui.child_approval(_request()))
    for _ in range(100):
        if len(frames) == 2:
            break
        await asyncio.sleep(0.005)
    ui.withdraw_child_approvals()
    assert await asyncio.wait_for(asking, 5) == ("cancel", "no one can answer approvals here")
    assert not selecting.done()
    (select_frame,) = [f for f in frames if f["method"] == "select"]
    ui.respond({"id": select_frame["id"], "value": "b"})
    assert await asyncio.wait_for(selecting, 5) == "b"


async def test_a_turn_off_after_the_hosts_answer_keeps_that_answer() -> None:
    """Guard: a host's "yes" that arrived before the turn-off is the answer."""

    frames: list[dict[str, Any]] = []
    ui = RpcCommandUI(frames.append)
    ui.enabled = True
    asking = asyncio.ensure_future(ui.child_approval(_request()))
    for _ in range(100):
        if frames:
            break
        await asyncio.sleep(0.005)
    ui.respond({"id": frames[0]["id"], "value": "yes"})
    ui.withdraw_child_approvals()
    assert await asyncio.wait_for(asking, 5) == ("yes", None)


async def test_a_requests_steps_reach_the_host_as_aelix_child_approval_frames() -> None:
    """``asked`` while the human is asked, ``withdrawn`` when a stop (a swap's ``session_shutdown``)
    withdraws it: through the plane's subscription to the build's hub, which stays live until the
    rebind (contract r5 §3.4, §7.6), so a host closes its dialog on the frame."""

    from types import SimpleNamespace

    from aelix_agents.child_approvals import ChildApprovals, withdraw_approval
    from aelix_agents.child_events import ChildEventHub
    from aelix_agents.host_controls import HostControls
    from aelix_agents.print_channel import RunningChild
    from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl

    hub = ChildEventHub()
    runtime = _SubagentRuntimeImpl(host=SubagentHost(cwd=lambda: ".", session=lambda: None), events=hub)
    row = RunningChild(id="sub-1", profile="general-purpose", state="running")
    runtime._children[row.id] = row
    hub.publish(runtime, row, None, ())
    frames: list[dict[str, Any]] = []
    harness = SimpleNamespace(runtime=SimpleNamespace(subagents=SimpleNamespace(controls=HostControls(hub))))
    plane = RpcControlPlane(SimpleNamespace(harness=harness), emit=frames.append)
    gate = asyncio.Event()

    async def arbiter(_request: Any) -> tuple[str, str | None]:
        await gate.wait()
        return ("yes", None)

    row.approvals = ChildApprovals(row, decide=arbiter, hub=hub)
    row.approvals.offer(
        '{"type": "approval_request", "id": "ap-1", "toolCallId": "c1", "toolName": "bash", '
        '"kind": "bash", "args": {"command": "make"}}'
    )
    withdraw_approval(row)
    steps = [f["data"]["step"] for f in frames if f.get("type") == "aelix_child" and f.get("event") == "approval"]
    assert steps == ["asked", "withdrawn"]
    assert all(f["data"]["epoch"] == hub.epoch for f in frames if f.get("type") == "aelix_child")
    assert plane.controls is not None
    gate.set()
    await asyncio.sleep(0)
