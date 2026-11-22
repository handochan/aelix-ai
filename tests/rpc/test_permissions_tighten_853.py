"""#853 — ``permissions/set {mode, tighten: true}`` is answered during a turn when it only tightens.

The rule (the issue's, adopted as the lead's call, narrowed in fix round 1): the
request skips the turn's idle check when ``tighten`` is ``true`` and ``mode``
blocks, wherever the RPC plane serves, at least what the current mode blocks
(``rpc.control.narrows``). That is NOT the authority rank alone (Codex C3):
``auto``'s shell classifier blocks a command ``default`` and ``auto-accept-edits``
let an RPC parent run, so from ``auto`` only ``plan`` narrows. The admitted pairs
are measured here against the real gate, the child clamp and the parent's arbiter
(:func:`test_the_admitted_pairs_are_the_measured_ones`). Idle, every request
answers exactly as it does without ``tighten``. During a turn, a mode that does not
narrow, a request without ``tighten`` (or with any value but ``true``) and an
unknown mode are refused as before, "Runtime mutations require an idle session",
and the mode stays. While a classic command runs a tighten is refused as on the
base (fix round 1, verify V-F1, Codex C5), and one sent while a notice turn or a
run holds the mutation lock is answered at once (Codex C4).

What an accepted tighten applies to is what shift+tab applies to during a turn:
both are one ``PermissionPosture.set``, whose listeners and readers are the same.
Measured side by side here, from ``auto-accept-edits`` to ``plan`` (shift+tab's
next mode from there, ``CYCLE_ORDER``):

* the parent's next tool call — the gate reads the posture at each call;
* a delegated child's open approval request — answered ``no``, "the parent is in
  plan mode" (ADR-0308);
* a running foreground child — sent ``set_permission_mode`` and applying ``plan``
  (``tighten_all``, ADR-0306 D8).

Fails on ``0436193c``: every request during a turn is refused, the tightening one
included, and the mode stays. The fix round's cases fail without it: round 1 admitted
``auto`` to ``default`` and ``auto-accept-edits`` mid-turn, admitted a tighten while a
classic command ran, waited for the mutation lock, and launched a child granted
before a tighten with its old posture.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent
from aelix_coding_agent.builtin.permission import CHILD_PLAN_REASON, PermissionExtension
from aelix_coding_agent.builtin.permission_mode import (
    AUTHORITY_RANK,
    PLAN_BLOCKED,
    PermissionMode,
    PermissionPosture,
)
from aelix_coding_agent.rpc.control import CLASSIC_RUNNING, RpcControlPlane, narrows
from aelix_coding_agent.rpc.control_ui import RpcCommandUI

from tests.agents_ext._detach_rig import reap_children  # noqa: F401 — the fixture
from tests.agents_ext.test_agent_background_611 import _rig, jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_intervention_doors_123 import _applied, _channel
from tests.builtin.gate_tools import BUILTIN_CONTEXT
from tests.jobs.conftest import make_session, wait_for
from tests.rpc.test_child_approvals_rpc_612 import _request

IDLE_ONLY = "Runtime mutations require an idle session"
MODES = [mode.value for mode in PermissionMode]


class _Held:
    """A model whose first answer waits on :attr:`gate`: a turn that stays open."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()

    async def __call__(
        self, _model: Any, _context: Any, _options: Any = None
    ) -> AsyncIterator[Any]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        await self.gate.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )


async def _turn(tmp_path: Path) -> tuple[AgentHarness, _Held, asyncio.Future[Any]]:
    """A real harness in a real turn, held until the caller sets the gate."""

    model = _Held()
    harness = AgentHarness(
        AgentHarnessOptions(stream_fn=model, session=await make_session(tmp_path))
    )
    prompt = asyncio.ensure_future(harness.prompt("hold the turn"))
    await wait_for(lambda: harness.phase == "turn")
    return harness, model, prompt


async def _end(model: _Held, prompt: asyncio.Future[Any]) -> None:
    model.gate.set()
    await asyncio.wait_for(prompt, 10)


async def _answer(plane: RpcControlPlane, params: dict[str, Any]) -> tuple[str, Any]:
    try:
        return "ok", await plane.handle("permissions/set", params)
    except ValueError as error:
        return "error", str(error)


TIGHTENS = [
    (start, mode)
    for start in MODES
    for mode in MODES
    if narrows(PermissionMode(start), PermissionMode(mode))
]


@pytest.mark.parametrize(("start", "mode"), TIGHTENS, ids=[f"{s}->{m}" for s, m in TIGHTENS])
async def test_a_tighten_during_a_turn_is_answered_and_sets_the_mode(
    tmp_path: Path, start: str, mode: str
) -> None:
    harness, model, prompt = await _turn(tmp_path)
    posture = PermissionPosture(mode=PermissionMode(start))
    plane = RpcControlPlane(SimpleNamespace(harness=harness), permission_posture=posture)
    try:
        assert await _answer(plane, {"mode": mode, "tighten": True}) == ("ok", {"mode": mode})
        assert posture.get() is PermissionMode(mode)
        assert harness.phase == "turn", "answered during the turn, not after it"
    finally:
        await _end(model, prompt)


WIDENS = [
    (start, mode)
    for start in MODES
    for mode in MODES
    if not narrows(PermissionMode(start), PermissionMode(mode))
]
REFUSED = [
    *[(start, {"mode": mode, "tighten": True}) for start, mode in WIDENS],
    ("yolo", {"mode": "plan"}),
    ("yolo", {"mode": "plan", "tighten": False}),
    ("yolo", {"mode": "plan", "tighten": "true"}),
    ("yolo", {"mode": "plan", "tighten": 1}),
    ("yolo", {"mode": "plan", "tighten": None}),
    ("yolo", {"mode": "nope", "tighten": True}),
    ("yolo", {"mode": ["plan"], "tighten": True}),
    ("yolo", {"tighten": True}),
]


@pytest.mark.parametrize(("start", "params"), REFUSED, ids=[f"{s} {p}" for s, p in REFUSED])
async def test_during_a_turn_a_widening_or_no_tighten_is_refused_and_the_mode_stays(
    tmp_path: Path, start: str, params: dict[str, Any]
) -> None:
    harness, model, prompt = await _turn(tmp_path)
    posture = PermissionPosture(mode=PermissionMode(start))
    plane = RpcControlPlane(SimpleNamespace(harness=harness), permission_posture=posture)
    try:
        assert await _answer(plane, params) == ("error", IDLE_ONLY)
        assert posture.get() is PermissionMode(start)
    finally:
        await _end(model, prompt)


IDLE = [(start, mode) for start in MODES for mode in [*MODES, "nope"]]


@pytest.mark.parametrize(("start", "mode"), IDLE, ids=[f"{s}->{m}" for s, m in IDLE])
async def test_idle_every_request_answers_as_it_does_without_tighten(start: str, mode: str) -> None:
    """A guard, not a bite on the base: idle, ``tighten`` changes no answer and no mode (a widening included)."""

    answers = []
    for extra in ({}, {"tighten": True}, {"tighten": False}):
        posture = PermissionPosture(mode=PermissionMode(start))
        plane = RpcControlPlane(
            SimpleNamespace(harness=SimpleNamespace(phase="idle")), permission_posture=posture
        )
        answers.append((await _answer(plane, {"mode": mode, **extra}), posture.get()))
    assert answers[0] == answers[1] == answers[2], answers


async def test_a_tighten_with_no_posture_is_refused_as_before(tmp_path: Path) -> None:
    harness, model, prompt = await _turn(tmp_path)
    plane = RpcControlPlane(SimpleNamespace(harness=harness))
    try:
        assert await _answer(plane, {"mode": "plan", "tighten": True}) == ("error", IDLE_ONLY)
    finally:
        await _end(model, prompt)
    assert await _answer(plane, {"mode": "plan", "tighten": True}) == (
        "error",
        "This RPC host has no permission posture",
    )


async def test_tighten_on_another_method_skips_nothing(tmp_path: Path) -> None:
    """Only ``permissions/set`` reads ``tighten``: another mutation sent with it is refused as before."""

    harness, model, prompt = await _turn(tmp_path)
    posture = PermissionPosture(mode=PermissionMode.YOLO)
    plane = RpcControlPlane(SimpleNamespace(harness=harness), permission_posture=posture)
    try:
        with pytest.raises(ValueError, match=f"^{IDLE_ONLY}$"):
            await plane.handle("tools/set_active", {"names": [], "mode": "plan", "tighten": True})
        assert posture.get() is PermissionMode.YOLO
    finally:
        await _end(model, prompt)


# === what it applies to: what shift+tab applies to ====================================


def _bash() -> ToolCallHookEvent:
    return ToolCallHookEvent(
        tool_call_id="t1", tool_name="bash", args={"command": "touch x"}, context=BUILTIN_CONTEXT
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the stub children's pipes are POSIX here")
@pytest.mark.usefixtures("jobs_on", "reap_children")
@pytest.mark.parametrize("writer", ["shift+tab", "permissions/set tighten"])
async def test_a_tighten_reaches_what_shift_tab_reaches_during_a_turn(
    tmp_path: Path, writer: str
) -> None:
    applied = tmp_path / "applied.txt"
    rig = await _rig(
        tmp_path, channel=_channel(STUB_APPLIED=str(applied)), posture=PermissionMode.AUTO_ACCEPT
    )
    posture: Any = rig.ext.posture
    gate = PermissionExtension(posture=posture)
    asked = asyncio.Event()

    async def approver(_request: Any) -> tuple[str, str | None]:
        asked.set()
        await asyncio.Event().wait()  # the human has not answered
        return ("yes", None)

    gate.child_approver = approver
    ctx: Any = SimpleNamespace(
        has_ui=False, ui=None, cwd=str(rig.cwd)
    )  # an RPC host's: no approval UI
    harness, model, prompt = await _turn(tmp_path / "turn")
    plane = RpcControlPlane(SimpleNamespace(harness=harness), permission_posture=posture)
    run = asyncio.ensure_future(rig.call({"profile": "scout", "task": "go"}))
    try:
        await wait_for(lambda: _applied(applied) == ["launch auto-accept-edits"])
        runtime = rig.ext.runtime
        assert runtime is not None
        await wait_for(
            lambda: any(r.stream.last_text == "halfway" for r in runtime._children.values())
        )
        question = asyncio.ensure_future(gate.decide_for_child(_request()))
        await asyncio.wait_for(asked.wait(), 5)
        before = await gate._on_tool_call(_bash(), ctx)
        assert before is None, "auto-accept-edits on a host with no approval UI lets the call run"

        if writer == "shift+tab":
            assert posture.cycle() is PermissionMode.PLAN
        else:
            assert await plane.handle("permissions/set", {"mode": "plan", "tighten": True}) == {
                "mode": "plan"
            }
            assert harness.phase == "turn"

        assert await asyncio.wait_for(question, 5) == ("no", CHILD_PLAN_REASON)
        await wait_for(lambda: _applied(applied) == ["launch auto-accept-edits", "plan"])
        (row,) = runtime._children.values()
        assert row.granted_mode is PermissionMode.PLAN and row.stopped is False
        after = await gate._on_tool_call(_bash(), ctx)
        assert after is not None and after.block and (after.reason or "").startswith(PLAN_BLOCKED)
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
        await _end(model, prompt)
        await rig.service.shutdown()


# === which pairs narrow: measured on the real gates (fix round 1, Codex C3) ============

ALLOW, ASK, BLOCK = 0, 1, 2
"""A probe's verdict, ordered: a mode at least as tight answers each probe no lower."""


def _probes(cwd: Path) -> list[tuple[str, ToolCallHookEvent, str]]:
    """One call per branch of the gate (ADR-0253 provenance), with the shell verdict to stub."""

    outside = str(cwd.parent / "outside.txt")
    events = [
        ("read", "read", {"path": "a.txt"}, "ask"),
        ("write in the project", "write", {"path": "a.txt", "content": "x"}, "ask"),
        ("write outside it", "write", {"path": outside, "content": "x"}, "ask"),
        ("write .env", "write", {"path": ".env", "content": "x"}, "ask"),
        ("edit in the project", "edit", {"path": "a.txt", "edits": []}, "ask"),
        ("bash the classifier allows", "bash", {"command": "ls"}, "allow"),
        ("bash the classifier asks about", "bash", {"command": "make"}, "ask"),
        ("bash the classifier denies", "bash", {"command": "make clean"}, "deny"),
    ]
    probes = [
        (
            label,
            ToolCallHookEvent(
                tool_call_id=label, tool_name=name, args=args, context=BUILTIN_CONTEXT
            ),
            shell,
        )
        for label, name, args, shell in events
    ]
    # Not aelix's: provenance ``None`` (an MCP or extension tool).
    probes.append(
        (
            "a tool aelix did not build",
            ToolCallHookEvent(tool_call_id="mcp", tool_name="srv__do", args={}),
            "ask",
        )
    )
    return probes


async def _gate_verdict(
    mode: PermissionMode,
    event: ToolCallHookEvent,
    shell: str,
    cwd: Path,
    *,
    ui: Any,
    child: bool,
    asks_parent: bool,
) -> int:
    """What the REAL gate answers ``event`` under ``mode``; no shell runs (the classifier is stubbed)."""

    asked: list[str] = []

    async def ask(_request: Any, call_id: str) -> tuple[str, str | None]:
        asked.append(call_id)
        return ("yes", None)

    gate = PermissionExtension(
        posture=PermissionPosture(mode=mode),
        headless_default="block" if child else "allow",
        ask_parent=ask if asks_parent else None,
    )
    gate._auto_classify_bash = lambda _args: shell  # type: ignore[method-assign]
    ctx: Any = SimpleNamespace(has_ui=ui is not None, ui=ui, cwd=str(cwd))
    result = await gate._on_tool_call(event, ctx)
    if result is not None and result.block:
        return BLOCK
    return ASK if asked else ALLOW


async def _arbiter_verdict(mode: PermissionMode, *, approver: bool) -> int:
    """The parent's answer to a child's request (ADR-0308): a human is ASK; ``no`` or ``cancel`` BLOCK."""

    gate = PermissionExtension(posture=PermissionPosture(mode=mode))
    if approver:

        async def human(_request: Any) -> tuple[str, str | None]:
            return ("yes", None)

        gate.child_approver = human
    decision, _reason = await gate.decide_for_child(_request())
    return ASK if decision == "yes" else BLOCK


async def _what_mode_blocks(mode: PermissionMode, cwd: Path) -> dict[tuple[str, ...], int]:
    """Every verdict ``mode`` gives, by configuration, wherever an RPC plane serves it.

    * the parent's own calls: headless, and with RPC dialogs on (``ui/enable``),
      which the gate does not take for tool approval (``provides_tool_approval``);
    * the parent's arbiter, with and without child approvals (``childApprovals``);
    * each delegated child under ``mode``: its floor after a tighten (``tighten_all``'s
      ``inherit``) and the posture a new spawn of each ``approval_mode`` gets, with
      and without a UI and a parent to ask — through the child's own gate.
    """

    from aelix_agents.posture import child_permission_mode

    dialogs = RpcCommandUI(lambda _frame: None)
    dialogs.enabled = True
    verdicts: dict[tuple[str, ...], int] = {}
    for label, event, shell in _probes(cwd):
        for host, ui in (("headless", None), ("ui/enable", dialogs)):
            verdicts[("parent", host, label)] = await _gate_verdict(
                mode, event, shell, cwd, ui=ui, child=False, asks_parent=False
            )
    for approver in (False, True):
        verdicts[("arbiter", f"childApprovals={approver}")] = await _arbiter_verdict(
            mode, approver=approver
        )
    for asks in (False, True):
        postures = {"floor": child_permission_mode("inherit", mode, "user", asks_parent=asks)}
        for approval in ("inherit", "auto", "ask", "deny"):
            for has_ui in (False, True):
                postures[f"spawn {approval} ui={has_ui}"] = child_permission_mode(
                    approval, mode, "user", has_ui=has_ui, asks_parent=asks
                )
        for where, posture in postures.items():
            for label, event, shell in _probes(cwd):
                verdicts[("child", f"asks={asks}", where, label)] = await _gate_verdict(
                    posture, event, shell, cwd, ui=None, child=True, asks_parent=asks
                )
    return verdicts


@pytest.mark.usefixtures("own_agent_dir")
async def test_the_admitted_pairs_are_the_measured_ones(tmp_path: Path) -> None:
    """A mid-turn tighten goes only where every measured verdict is at least as tight (Codex C3).

    Fails without fix round 1: round 1's rank rule admitted ``auto`` to ``default`` and to
    ``auto-accept-edits``, which let the parent run the command ``auto``'s
    classifier denies.
    """

    blocks = {mode: await _what_mode_blocks(mode, tmp_path) for mode in PermissionMode}
    measured = {
        (start.value, mode.value)
        for start in PermissionMode
        for mode in PermissionMode
        if AUTHORITY_RANK[mode] <= AUTHORITY_RANK[start]
        and all(blocks[mode][key] >= verdict for key, verdict in blocks[start].items())
    }
    # The cell that breaks the rank: a headless parent's denied shell command.
    deny = ("parent", "headless", "bash the classifier denies")
    assert (blocks[PermissionMode.AUTO][deny], blocks[PermissionMode.DEFAULT][deny]) == (
        BLOCK,
        ALLOW,
    )
    assert measured == {
        ("plan", "plan"),
        ("default", "default"),
        ("default", "plan"),
        ("auto-accept-edits", "auto-accept-edits"),
        ("auto-accept-edits", "default"),
        ("auto-accept-edits", "plan"),
        ("auto", "auto"),
        ("auto", "plan"),
        ("yolo", "yolo"),
        ("yolo", "auto"),
        ("yolo", "auto-accept-edits"),
        ("yolo", "default"),
        ("yolo", "plan"),
    }
    plane = RpcControlPlane(SimpleNamespace(harness=SimpleNamespace(phase="turn")))
    admitted = set()
    for start in PermissionMode:
        plane.posture = PermissionPosture(mode=start)
        admitted |= {
            (start.value, mode.value)
            for mode in PermissionMode
            if plane.tightens("permissions/set", {"mode": mode.value, "tighten": True})
        }
    assert admitted == measured


@pytest.mark.usefixtures("own_agent_dir")
@pytest.mark.parametrize("wanted", ["default", "auto-accept-edits"])
async def test_from_auto_a_tighten_that_would_unblock_the_classifier_is_refused(
    tmp_path: Path, wanted: str
) -> None:
    """Codex C3's probe: a headless turn in ``auto``, the classifier saying ``deny``; no shell runs."""

    harness, model, prompt = await _turn(tmp_path)
    posture = PermissionPosture(mode=PermissionMode.AUTO)
    gate = PermissionExtension(posture=posture)
    gate._auto_classify_bash = lambda _args: "deny"  # type: ignore[method-assign]
    ctx: Any = SimpleNamespace(has_ui=False, ui=None, cwd=str(tmp_path))
    plane = RpcControlPlane(SimpleNamespace(harness=harness), permission_posture=posture)
    try:
        before = await gate._on_tool_call(_bash(), ctx)
        assert before is not None and before.block
        assert await _answer(plane, {"mode": wanted, "tighten": True}) == ("error", IDLE_ONLY)
        assert posture.get() is PermissionMode.AUTO
        after = await gate._on_tool_call(_bash(), ctx)
        assert after is not None and after.block, "the denied command stays denied"
        assert await _answer(plane, {"mode": "plan", "tighten": True}) == ("ok", {"mode": "plan"})
    finally:
        await _end(model, prompt)


# === only the turn check is skipped (fix round 1, verify V-F1, Codex C5) ===============

CLASSICS = ["bash", "compact", "set_model", "new_session", "switch_session", "fork"]


@pytest.mark.parametrize("phase", ["idle", "turn"])
@pytest.mark.parametrize("classic", CLASSICS)
async def test_a_tighten_while_a_classic_command_runs_is_refused_as_before(
    classic: str, phase: str
) -> None:
    """The base's refusal, byte for byte, idle and in a turn; a classic swap included.

    Fails without fix round 1: a tighten skipped the classic check too, so it was
    answered ``{mode: plan}`` and set ``plan`` while a classic command ran.
    """

    posture = PermissionPosture(mode=PermissionMode.YOLO)
    plane = RpcControlPlane(
        SimpleNamespace(harness=SimpleNamespace(phase=phase)), permission_posture=posture
    )
    assert plane.classic_gate(classic) == (None, True)
    try:
        for params in ({"mode": "plan", "tighten": True}, {"mode": "plan"}):
            assert await _answer(plane, params) == ("error", CLASSIC_RUNNING)
            assert posture.get() is PermissionMode.YOLO
    finally:
        plane.classic_done(classic)
    assert await _answer(plane, {"mode": "plan", "tighten": True}) == ("ok", {"mode": "plan"})


# === ahead of the mutation lock (fix round 1, Codex C4, verify V-F4; the lead's call) ===


async def test_a_tighten_is_answered_while_a_notice_turn_holds_the_lock(tmp_path: Path) -> None:
    """Codex C4's probe. Fails without fix round 1: the tighten queued behind the notice turn, ``yolo`` governing it."""

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

    from tests.jobs.conftest import fast_service
    from tests.rpc.test_children_family_612 import _GatedModel
    from tests.rpc.test_children_notices_placed_in_turn_857 import _notice

    model = _GatedModel()
    harness = AgentHarness(
        AgentHarnessOptions(
            stream_fn=model, session=await make_session(tmp_path), notice_inbox=True
        )
    )
    service = fast_service()
    await service.bind(harness)
    posture = PermissionPosture(mode=PermissionMode.YOLO)
    plane = RpcControlPlane(
        SimpleNamespace(harness=harness), jobs=service, permission_posture=posture
    )
    harness.post_notice(_notice(1))
    turn = asyncio.ensure_future(plane.handle("children/notices", {}))
    widen: asyncio.Future[Any] | None = None
    try:
        await asyncio.wait_for(model.entered.wait(), 5)
        assert plane.lock.locked() and harness.phase == "turn"
        answer = await asyncio.wait_for(_answer(plane, {"mode": "plan", "tighten": True}), 2)
        assert answer == ("ok", {"mode": "plan"})
        assert posture.get() is PermissionMode.PLAN and harness.phase == "turn"
        widen = asyncio.ensure_future(_answer(plane, {"mode": "yolo", "tighten": True}))
        await asyncio.sleep(0.05)
        assert not widen.done(), "a mode that does not narrow still waits for the lock"
        model.gate.set()
        await asyncio.wait_for(turn, 5)
        assert await asyncio.wait_for(widen, 5) == ("ok", {"mode": "yolo"}), "idle, as in v1"
    finally:
        model.gate.set()
        await asyncio.gather(turn, *([widen] if widen is not None else []), return_exceptions=True)
        await service.shutdown()


@pytest.mark.parametrize("holder", ["agents/run", "children/resume"])
async def test_a_tighten_is_answered_while_a_run_holds_the_lock(
    tmp_path: Path, holder: str
) -> None:
    """Fails without fix round 1: the tighten waited for the run to end."""

    from tests.agents_ext.test_agent_resume_610 import _delegated
    from tests.agents_ext.test_host_resume_836 import _host
    from tests.rpc.test_children_resume_836 import _plane

    scene = await _delegated(tmp_path)
    _host(scene, posture=PermissionMode.DEFAULT)
    plane, _runtime = _plane(scene.runtime)
    posture = PermissionPosture(mode=PermissionMode.YOLO)
    plane.posture = posture
    scene.channel._park = True
    scene.channel.arrived.clear()
    params = (
        {"name": "scout", "task": "go"}
        if holder == "agents/run"
        else {"id": scene.child_id, "task": "go on"}
    )
    run = asyncio.ensure_future(plane.handle(holder, params))
    try:
        await asyncio.wait_for(scene.channel.arrived.wait(), 5)
        assert plane.lock.locked()
        answer = await asyncio.wait_for(_answer(plane, {"mode": "plan", "tighten": True}), 2)
        assert answer == ("ok", {"mode": "plan"}) and posture.get() is PermissionMode.PLAN
        assert not run.done()
    finally:
        scene.channel.release.set()
        await asyncio.wait_for(asyncio.gather(run, return_exceptions=True), 5)


@pytest.mark.parametrize("ended", [True, False], ids=["turn ended first", "turn still running"])
async def test_a_tighten_lands_after_a_mode_change_sent_before_it(
    tmp_path: Path, ended: bool
) -> None:
    """Behind a queued ``permissions/set`` the tighten waits, and lands in order.

    A guard where the turn ended first (it held before fix round 1 too): both
    are answered, ``yolo`` then ``plan``. Where the turn still runs when the lock
    is let go, the first is refused (idle only) and the tighten, taken under the
    lock now, still skips the turn check (``_require_quiescent(any_phase=...)``).
    """

    harness, model, prompt = await _turn(tmp_path)
    posture = PermissionPosture(mode=PermissionMode.AUTO_ACCEPT)
    plane = RpcControlPlane(SimpleNamespace(harness=harness), permission_posture=posture)
    try:
        async with plane.lock:  # what a running notice turn or run holds
            first = asyncio.ensure_future(_answer(plane, {"mode": "yolo"}))
            await asyncio.sleep(0)
            second = asyncio.ensure_future(_answer(plane, {"mode": "plan", "tighten": True}))
            await asyncio.sleep(0.05)
            assert not first.done() and not second.done()
            assert posture.get() is PermissionMode.AUTO_ACCEPT
            if ended:
                await _end(model, prompt)
        expected_first = ("ok", {"mode": "yolo"}) if ended else ("error", IDLE_ONLY)
        assert await asyncio.wait_for(first, 5) == expected_first
        assert await asyncio.wait_for(second, 5) == ("ok", {"mode": "plan"})
        assert posture.get() is PermissionMode.PLAN
        assert (harness.phase == "turn") is not ended
    finally:
        if not prompt.done():
            await _end(model, prompt)


async def test_a_tighten_waits_for_a_session_change_as_before() -> None:
    """A guard (it held before fix round 1 too): a session change finishes first; the answer is v1's."""

    posture = PermissionPosture(mode=PermissionMode.YOLO)
    plane = RpcControlPlane(
        SimpleNamespace(harness=SimpleNamespace(phase="idle")), permission_posture=posture
    )
    async with plane.lock:  # what a lifecycle method holds while ``transitioning``
        plane.transitioning += 1
        pending = asyncio.ensure_future(_answer(plane, {"mode": "plan", "tighten": True}))
        await asyncio.sleep(0.05)
        assert not pending.done() and posture.get() is PermissionMode.YOLO
        plane.transitioning -= 1
    assert await asyncio.wait_for(pending, 5) == ("ok", {"mode": "plan"})


# === a child granted before the tighten (fix round 1, Codex C6) ======================


CHILD_CASES = [
    # (start, tighten to, a parent to ask, the launch)
    ("yolo", "plan", False, "plan"),
    ("yolo", None, False, "yolo"),
    ("auto-accept-edits", "default", True, "default"),
]


@pytest.mark.usefixtures("jobs_on")
@pytest.mark.parametrize(
    ("start", "to", "asks", "launch"), CHILD_CASES, ids=["to plan", "no tighten", "asks its parent"]
)
async def test_a_child_granted_before_a_tighten_launches_with_the_tighter_floor(
    tmp_path: Path, start: str, to: str | None, asks: bool, launch: str
) -> None:
    """Codex C6's probe. Fails without fix round 1: the child launched with the posture it was granted.

    ``no tighten`` is the guard: with the posture unchanged the grant is what
    launches. ``asks its parent``: a child that asks its parent (child approvals
    on, ADR-0308) gets ``tighten_all``'s floor for such a row, ``default``, not ``plan``.
    """

    from aelix_agents.print_channel import AGENT_TOOL_NAME
    from aelix_ai.tools import ToolExecutionContext

    from tests.agents_ext.test_agent_resume_610 import _Channel

    channel = _Channel()
    rig = await _rig(tmp_path / "rig", channel=channel, posture=PermissionMode(start))
    if asks:
        rig.ext.asks_parent = lambda: True
    harness, model, prompt = await _turn(tmp_path / "turn")
    plane = RpcControlPlane(SimpleNamespace(harness=harness), permission_posture=rig.ext.posture)
    args = {"profile": "scout", "task": "read the scratch project"}
    event = ToolCallHookEvent(tool_call_id="late", tool_name=AGENT_TOOL_NAME, args=args)
    try:
        assert await rig.extension.handlers["tool_call"][0](event, rig.ctx) is None
        grant = rig.ext._pending["late"].grant
        assert (grant.mode, grant.asks_parent) == (
            PermissionMode(start),
            asks,
        ) and not channel.plans
        if to is not None:
            assert await _answer(plane, {"mode": to, "tighten": True}) == ("ok", {"mode": to})
        result = await rig.tool.execute(args, ToolExecutionContext(tool_call_id="late"))
        assert not result.is_error
        (plan,) = channel.plans
        assert (plan.permission_mode, plan.asks_parent) == (PermissionMode(launch), asks)
    finally:
        await _end(model, prompt)
        await rig.service.shutdown()


@pytest.mark.parametrize("door", ["spawn", "resume_for_host"])
async def test_a_tighten_while_a_consent_dialog_is_open_reaches_the_child(
    tmp_path: Path, door: str
) -> None:
    """Fix round 1 (Codex C6 and C4 together): ``agents/run`` and ``children/resume`` hold the lock
    while their dialog is open, so a tighten lands then; the child launches at the tighter floor.

    ``spawn`` is ``agents/run``'s door, ``resume_for_host`` ``children/resume``'s. Fails without
    fix round 1: the child launched at the ``auto-accept-edits`` the dialog offered.
    """

    import dataclasses

    from tests.agents_ext.test_agent_resume_610 import _delegated, _resolved
    from tests.agents_ext.test_host_resume_836 import _UI, _host

    scene = await _delegated(tmp_path, mode=PermissionMode.AUTO_ACCEPT)
    now = [PermissionMode.AUTO_ACCEPT]

    class _Tightened(_UI):
        async def select(self, title: str, options: list[str], opts: Any = None) -> str | None:
            now[0] = PermissionMode.PLAN  # the host's tighten, while the human reads the dialog
            return await super().select(title, options, opts)

    ui = _Tightened(answer=0)
    _host(scene, posture=PermissionMode.AUTO_ACCEPT, ui=ui)
    scene.runtime.host = dataclasses.replace(scene.runtime.host, posture=lambda: now[0])
    if door == "spawn":
        result = await scene.runtime.spawn(_resolved(), "go")
    else:
        result = await scene.runtime.resume_for_host(scene.child_id, "go")
    assert len(ui.titles) == 1 and "auto-accept-edits" in ui.options[0][0], "the dialog offered it"
    assert result.ok
    assert scene.channel.plans[-1].permission_mode is PermissionMode.PLAN


@pytest.mark.usefixtures("jobs_on")
@pytest.mark.parametrize("tighten", [True, False], ids=["tightened", "unchanged"])
async def test_a_tighten_while_a_batch_dialog_is_open_reaches_every_member(
    tmp_path: Path, tighten: bool
) -> None:
    """Verify round 2 F1: a batch reads its floor only after its one dialog, so only the grant's
    ``parent`` sees a tighten made while that dialog is open; each member launches at ``plan``.

    ``unchanged`` is the guard. Fails when ``request_spawn_consent_batch`` drops ``parent=`` from
    its grant (mutant N30): the members launch at the ``auto-accept-edits`` the dialog offered.
    """

    from aelix_agents.print_channel import AGENT_TOOL_NAME
    from aelix_ai.tools import ToolExecutionContext

    from tests.agents_ext.test_agent_resume_610 import _Channel

    channel = _Channel()
    rig = await _rig(tmp_path, channel=channel, posture=PermissionMode.AUTO_ACCEPT, has_ui=True)
    seen: list[list[str]] = []

    async def select(title: str, options: list[str], opts: Any = None) -> str:
        seen.append(list(options))
        if tighten:
            rig.ext.posture.set(PermissionMode.PLAN)  # the host's tighten while the human reads
        return options[0]

    rig.ctx.ui.select = select  # type: ignore[method-assign]
    args = {"profile": "scout", "tasks": ["read a", "read b"], "mode": "parallel"}
    event = ToolCallHookEvent(tool_call_id="b1", tool_name=AGENT_TOOL_NAME, args=args)
    try:
        assert await rig.extension.handlers["tool_call"][0](event, rig.ctx) is None
        result = await rig.tool.execute(args, ToolExecutionContext(tool_call_id="b1"))
        assert not result.is_error
        assert len(seen) == 1, "one dialog for the batch"
        launched = [plan.permission_mode for plan in channel.plans]
        assert launched == [PermissionMode.PLAN if tighten else PermissionMode.AUTO_ACCEPT] * 2
    finally:
        await rig.service.shutdown()
