"""#611 S1c — ``agent(action="spawn"|"wait"|"status"|"stop"|"result")``: delegation as a job (ADR-0295).

AT WHAT LEVEL. The REAL ``AgentsExtension`` hook and ``execute()`` over a real
``_ExtensionRuntime``/``ExtensionContext`` pair (``test_tool_and_security``'s
bench, built here so the host's RPC answer can be set), the REAL job service
bound to a harness with the kernel inbox on, and a REAL ``PrintChannel``
whose child is a stdlib stub that writes the print-mode stream
(``test_print_channel_spawn``'s ``_stub``): the processes, the barrier, the
records and the notice are the product's own. The gate is forced on per test
(``jobs/activation.py`` ``ACTIVE``), as every jobs test does; the first test
pins what the tool is with it off.

Fails on ``6caaae09``: the parser refuses every action but ``run`` and
``resume``, and nothing registers an ``agent`` job kind.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness
from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent, ToolCallHookEvent
from aelix_agent_core.session import Session
from aelix_agents import jobs_client
from aelix_agents.child_session import CHILD_SESSION_TYPE, USAGE_TYPE
from aelix_agents.consent import CANCEL_OPTION
from aelix_agents.extension import AgentsExtension
from aelix_agents.jobs_client import (
    KIND,
    NOTICE_BODY_MAX_BYTES,
    DelegationJobs,
    DelegationKindAdapter,
    wait_bounds,
)
from aelix_agents.print_channel import AGENT_TOOL_NAME, PrintChannel
from aelix_agents.tool import (
    _BACKGROUND_HEAD,
    _BACKGROUND_REFUSED,
    AGENT_TOOL_PARAMETERS,
    BACKGROUND_HEAD_MAX_BYTES,
    build_description,
)
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionContext,
    _ExtensionRuntime,
)
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.contract import ClientOutcome
from aelix_coding_agent.jobs.notice import NOTICE_MAX_BYTES
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE
from aelix_coding_agent.jobs.service import JobService

from tests.agents_ext.test_child_session_records import _parent
from tests.agents_ext.test_print_channel_spawn import _stub
from tests.agents_ext.test_tool_and_security import _FakeUI, _text, _write_profile
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
REAL_CHILD = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "a service-owned child on win32 runs the job's real Job-object evidence legs, which "
        "ADR-0293 proves against an injected api only until #611 S3's Windows acceptance"
    ),
)

_DONE = ("done", "failed", "stopped", "lost")


def _script(answer: str = "the background answer", *, gate: Path | None = None, partial: str | None = None) -> str:
    """A stub child: optionally says ``partial``, waits for ``gate`` to exist, answers, then ``print_done``."""

    wait = (
        f"\nwhile not os.path.exists({str(gate)!r}):\n    time.sleep(0.02)\n" if gate is not None else ""
    )
    said = f"say({partial!r})\n" if partial is not None else ""
    return _stub(
        "start()\n"
        'emit({"type": "tool_execution_start", "tool_name": "read"})\n'
        + said
        + wait
        + f"say({answer!r})\n"
        "done()\n"
        'if os.environ.get("AELIX_JOBS_PROTOCOL") == "1":\n'
        '    emit({"type": "aelix_print_done"})\n'
    )


@dataclass
class _Rig:
    ext: AgentsExtension
    api: ExtensionAPI
    extension: Extension
    runtime: _ExtensionRuntime
    ctx: ExtensionContext
    ui: _FakeUI
    service: JobService
    session: Session
    harness: AgentHarness
    cwd: Path
    agent_dir: Path

    @property
    def tool(self) -> Any:
        return self.extension.tools[AGENT_TOOL_NAME]

    async def call(self, args: dict[str, Any], *, tool_call_id: str | None = None) -> ToolResult:
        """The hook, then ``execute()``, as the kernel runs them (``test_tool_and_security._call``)."""

        tcid = tool_call_id or f"tc-{uuid.uuid4().hex[:6]}"
        event = ToolCallHookEvent(tool_call_id=tcid, tool_name=AGENT_TOOL_NAME, args=dict(args))
        blocked = await self.extension.handlers["tool_call"][0](event, self.ctx)
        if blocked is not None and blocked.block:
            return ToolResult(content=[TextContent(text=blocked.reason or "")], is_error=True)
        return await self.tool.execute(dict(args), ToolExecutionContext(tool_call_id=tcid))

    async def prompt_started(self, source: str = "interactive") -> None:
        handler = self.extension.handlers["before_agent_start"][0]
        await handler(BeforeAgentStartHookEvent(source=source), self.ctx)  # type: ignore[arg-type]

    def jobs(self) -> list[Any]:
        return [s for s in self.service.snapshots() if s.kind == KIND]


@pytest.fixture
def jobs_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(activation, "ACTIVE", True)
    yield
    activation.install_service(None)


async def _rig(
    tmp_path: Path,
    *,
    script: str | None = None,
    channel: Any = None,
    settings: dict[str, Any] | None = None,
    background_host: Any = None,
    bucket: bool = True,
    posture: PermissionMode = PermissionMode.DEFAULT,
    has_ui: bool = False,
    answers: tuple[object, ...] = (),
    profile_extra: str = "",
) -> _Rig:
    service = fast_service(settings=(lambda: settings) if settings is not None else None)
    activation.install_service(service)
    agent_dir = tmp_path / "agent"
    (agent_dir / "agents").mkdir(parents=True, exist_ok=True)
    cwd = tmp_path / "project"
    cwd.mkdir(parents=True, exist_ok=True)
    _write_profile(agent_dir / "agents" / "scout.md", "scout", extra=profile_extra)
    if bucket:
        session, _repo, _path = await _parent(tmp_path, cwd=cwd)
    else:
        session = await make_session(tmp_path)
    runtime = _ExtensionRuntime()
    ui = _FakeUI(*answers)
    if has_ui:
        runtime.bind_ui(ui)  # type: ignore[arg-type]
    extension = Extension(name="aelix-agents")
    api = ExtensionAPI(extension, runtime)
    ext = AgentsExtension(
        posture=PermissionPosture(mode=posture),
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        project_trusted=False,
        session=lambda: session,
        background_host=background_host,
    )
    ext(api)
    if ext.runtime is not None:
        ext.runtime.channel = (
            channel
            if channel is not None
            else PrintChannel(
                grace=2.0,
                argv_builder=lambda *_a, **_k: [sys.executable, "-c", script or _script()],
            )
        )
    ctx = ExtensionContext(
        runtime,
        cwd=str(cwd),
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: ["read", "bash"],
        get_system_prompt=lambda: "system",
        is_project_trusted=lambda: False,
    )
    harness = make_harness(session)
    await service.bind(harness)
    return _Rig(
        ext=ext,
        api=api,
        extension=extension,
        runtime=runtime,
        ctx=ctx,
        ui=ui,
        service=service,
        session=session,
        harness=harness,
        cwd=cwd,
        agent_dir=agent_dir,
    )


async def _settled(rig: _Rig, job_id: str | None = None) -> Any:
    def done() -> bool:
        snaps = [rig.service.snapshot(job_id)] if job_id else rig.jobs()
        return bool(snaps) and all(
            s is not None and s.state in _DONE and s.notice_state != "none" for s in snaps
        )

    await wait_for(done)
    return rig.service.snapshot(job_id) if job_id else rig.jobs()[0]


def _spawned_id(result: ToolResult) -> str:
    text = _text(result)
    assert not result.is_error, text
    return text.split("(id ", 1)[1].split(";", 1)[0]


# === the gate ====================================================================


async def test_with_the_gate_off_the_tool_is_what_it_was(tmp_path: Path) -> None:
    """``ACTIVE`` off: the schema key for key, the description, and a spawn refused as before #611."""

    assert not activation.is_active()
    runtime = _ExtensionRuntime()
    extension = Extension(name="aelix-agents")
    ext = AgentsExtension(cwd=str(tmp_path), agent_dir=str(tmp_path / "agent"))
    ext(ExtensionAPI(extension, runtime))
    tool = extension.tools[AGENT_TOOL_NAME]
    assert tool.parameters == AGENT_TOOL_PARAMETERS
    assert tool.description == build_description("")
    assert _BACKGROUND_HEAD not in tool.description
    assert ext.runtime is not None and ext.runtime.host.reserve is None
    event = ToolCallHookEvent(
        tool_call_id="t", tool_name=AGENT_TOOL_NAME, args={"action": "spawn", "profile": "x", "task": "y"}
    )
    blocked = await extension.handlers["tool_call"][0](event, None)  # type: ignore[arg-type]
    assert blocked is not None and blocked.block
    assert blocked.reason == "action='spawn' is not an action. Use one of: run, resume."
    event = ToolCallHookEvent(
        tool_call_id="t2", tool_name=AGENT_TOOL_NAME, args={"profile": "x", "task": "y", "background": True}
    )
    blocked = await extension.handlers["tool_call"][0](event, None)  # type: ignore[arg-type]
    assert blocked is not None and blocked.reason == _BACKGROUND_REFUSED


async def test_an_rpc_host_refuses_the_background_actions_until_612(tmp_path: Path, jobs_on: Any) -> None:
    """Jobs on, an RPC host: the tool is the pre-#611 one and the kind is not registered (#612)."""

    rig = await _rig(tmp_path, background_host=lambda: False)
    assert rig.tool.parameters == AGENT_TOOL_PARAMETERS
    assert rig.service.registry.get(KIND) is None
    assert rig.ext.runtime is not None and rig.ext.runtime.host.reserve is None
    result = await rig.call({"action": "spawn", "profile": "scout", "task": "t"})
    assert result.is_error and "is not an action" in _text(result)
    await rig.service.shutdown()


def test_the_background_paragraph_stays_inside_its_own_budget_and_names_every_action() -> None:
    assert len(_BACKGROUND_HEAD.encode("utf-8")) <= BACKGROUND_HEAD_MAX_BYTES
    for action in ("spawn", "status", "wait", "stop", "result"):
        assert f'action="{action}"' in _BACKGROUND_HEAD


# === spawn ========================================================================


@REAL_CHILD
async def test_spawn_returns_once_launched_and_one_notice_comes_back(tmp_path: Path, jobs_on: Any) -> None:
    """The call returns at launch; the run's records are confirmed and linked; ONE notice, after them."""

    rig = await _rig(tmp_path)
    result = await rig.call({"action": "spawn", "profile": "scout", "task": "count files"})
    agent_id = _spawned_id(result)
    assert "Do not wait, sleep or poll" in _text(result)
    snap = await _settled(rig)
    assert snap.state == "done" and snap.brief == "the background answer"
    (notice,) = rig.harness.undelivered_notices()
    assert "the background answer" in notice.body
    assert f'agent(action="result", id="{agent_id}")' in notice.body
    assert len(notice.body.encode("utf-8")) <= NOTICE_MAX_BYTES
    # The order ADR-0293 §7 fixes: the client's own records before cleanup and
    # the outcome, every one of them carrying the job's id.
    entries = await rig.session.get_entries()
    shape = [
        (e.custom_type, e.data.get("fact") or e.data.get("phase") or e.data.get("state"))
        for e in entries
        if e.type == "custom" and e.custom_type in (JOB_FACT_TYPE, CHILD_SESSION_TYPE, USAGE_TYPE)
    ]
    assert shape == [
        (JOB_FACT_TYPE, "start"),
        (CHILD_SESSION_TYPE, "start"),
        (USAGE_TYPE, "pending"),
        (JOB_FACT_TYPE, "process"),
        (CHILD_SESSION_TYPE, "settle"),
        (USAGE_TYPE, "final"),
        (JOB_FACT_TYPE, "cleanup"),
        (JOB_FACT_TYPE, "outcome"),
    ]
    linked = {e.data.get("job_id") for e in entries if e.type == "custom"}
    assert linked == {snap.job_id}
    await rig.service.shutdown()


@REAL_CHILD
@pytest.mark.parametrize("bucket", [True, False], ids=["session-file", "no-session-file"])
async def test_agent_id_session_or_key(tmp_path: Path, jobs_on: Any, bucket: bool) -> None:
    """Codex r1 #15: the agent id is the child's session id, or its spawn key when it has no file."""

    rig = await _rig(tmp_path, bucket=bucket)
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await _settled(rig)
    starts = [
        e.data
        for e in await rig.session.get_entries()
        if e.type == "custom" and e.custom_type == CHILD_SESSION_TYPE and e.data.get("phase") == "start"
    ]
    (start,) = starts
    if bucket:
        assert start["child"] is not None and agent_id == start["child"]["session_id"]
    else:
        assert start["child"] is None and agent_id == start["key"]
    status = await rig.call({"action": "status", "id": agent_id})
    assert not status.is_error and f"(id {agent_id})" in _text(status)
    await rig.service.shutdown()


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ({"tasks": ["a", "b"]}, "ONE 'task'"),
        ({"mode": "parallel", "tasks": ["a"]}, "ONE 'task'"),
        ({"mode": "chain", "task": "a"}, "takes no mode='chain'"),
        ({"task": "a", "id": "x"}, "takes no 'id'"),
        ({"task": "a", "timeout": 5}, "no time limit"),
    ],
)
async def test_spawn_takes_one_task_and_nothing_else(
    tmp_path: Path, jobs_on: Any, args: dict[str, Any], words: str
) -> None:
    rig = await _rig(tmp_path)
    result = await rig.call({"action": "spawn", "profile": "scout", **args})
    assert result.is_error and words in _text(result)
    assert rig.jobs() == []
    await rig.service.shutdown()


@REAL_CHILD
async def test_a_launch_failure_is_the_calls_error_and_no_notice_follows(tmp_path: Path, jobs_on: Any) -> None:
    """Codex r3 #13/#14 for this kind: a spawn that cannot start is the call's error; its slot comes back."""

    channel = PrintChannel(argv_builder=lambda *_a, **_k: [str(tmp_path / "no-such-binary")])
    rig = await _rig(tmp_path, channel=channel)
    result = await rig.call({"action": "spawn", "profile": "scout", "task": "t"})
    assert result.is_error and "did not start" in _text(result)
    await wait_for(lambda: all(s.state in _DONE for s in rig.jobs()))
    assert rig.harness.undelivered_notices() == []
    assert rig.service.admission.count(KIND) == 0
    # Admitted and tried: charged, as ``_run`` charges a child that died at once (verify N3).
    assert rig.ext.runtime is not None and rig.ext.runtime._delegations_this_prompt == 1
    await rig.service.shutdown()


# === wait, status, stop, result =========================================================


def test_wait_bounds() -> None:
    """D1 (owner, 2026-11-07): 30 s minimum, 120 s default, 30 min maximum, and the clamp is said."""

    assert wait_bounds(None) == (120.0, None)
    assert wait_bounds(5) == (30.0, "(raised to 30 s, the minimum)")
    assert wait_bounds(30) == (30.0, None)
    assert wait_bounds(600) == (600.0, None)
    assert wait_bounds(1800) == (1800.0, None)
    assert wait_bounds(7200) == (1800.0, "(cut to 1800 s, the maximum)")


@REAL_CHILD
async def test_wait_never_stops_the_agent_and_the_third_empty_wait_is_refused(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wait`` ends the wait, not the child (#604); a third empty one in a prompt is refused (D1)."""

    monkeypatch.setattr(jobs_client, "WAIT_MIN_SECONDS", 0.05)
    monkeypatch.setattr(jobs_client, "WAIT_DEFAULT_SECONDS", 0.1)
    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    for _ in range(2):
        waited = await rig.call({"action": "wait", "id": agent_id, "timeout": 0.01})
        assert not waited.is_error and "Still running" in _text(waited)
        assert "raised to 0 s, the minimum" in _text(waited)
    (snap,) = rig.jobs()
    assert snap.state == "running"
    third = await rig.call({"action": "wait", "id": agent_id})
    assert third.is_error and "Wait refused" in _text(third)
    assert rig.jobs()[0].state == "running"
    # A new prompt starts a new count; a wait that sees it finish resets it.
    await rig.prompt_started()
    gate.write_text("")
    monkeypatch.setattr(jobs_client, "WAIT_DEFAULT_SECONDS", 10.0)
    finished = await rig.call({"action": "wait", "id": agent_id})
    assert not finished.is_error and "arrives as a notice right after this call" in _text(finished)
    snap = rig.jobs()[0]
    assert snap.state == "done" and snap.notice_state == "pending"
    await rig.service.shutdown()


@REAL_CHILD
async def test_status_and_wait_after_delivery(tmp_path: Path, jobs_on: Any) -> None:
    """Codex r1 #15: once the notice was placed, status and wait still answer — from the service's summary."""

    rig = await _rig(tmp_path)
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await _settled(rig)
    started = await rig.harness.prompt_notices()
    assert started.started
    await wait_for(lambda: rig.jobs()[0].notice_state == "delivered")
    status = await rig.call({"action": "status", "id": agent_id})
    assert "done" in _text(status) and "result delivered" in _text(status)
    waited = await rig.call({"action": "wait", "id": agent_id})
    assert not waited.is_error and "done" in _text(waited)
    assert "already given to you as a notice" in _text(waited)
    assert rig.harness.undelivered_notices() == []
    await rig.service.shutdown()


@REAL_CHILD
async def test_stop_after_delivery_says_its_result_was_given(tmp_path: Path, jobs_on: Any) -> None:
    """Verify S2: ``stop`` on an agent whose notice was placed promises no notice.

    Fails on ``b387b4b5``: "It is done. Its result — partial, if it was
    stopped — comes back as a notice."
    """

    rig = await _rig(tmp_path)
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await _settled(rig)
    assert (await rig.harness.prompt_notices()).started
    await wait_for(lambda: rig.jobs()[0].notice_state == "delivered")
    stopped = await rig.call({"action": "stop", "id": agent_id})
    text = _text(stopped)
    assert not stopped.is_error and "Nothing to stop: it already ended (done)" in text
    assert "already given to you as a notice" in text and "comes back" not in text
    assert rig.harness.undelivered_notices() == []
    await rig.service.shutdown()


async def test_a_notice_turn_keeps_the_prompts_empty_wait_count(tmp_path: Path, jobs_on: Any) -> None:
    """Codex #10: two empty waits, then a notice turn — the third is still refused.

    Fails on ``b387b4b5``: ``empty_waits_after_notice=0``.
    """

    rig = await _rig(tmp_path)
    jobs = rig.ext._delegation_jobs
    assert jobs is not None
    await rig.prompt_started()
    jobs.empty_waits = 2
    await rig.prompt_started(source="notice")
    assert jobs.empty_waits == 2
    await rig.prompt_started(source="interactive")
    assert jobs.empty_waits == 0
    await rig.service.shutdown()


@POSIX
async def test_stop_goes_through_the_barrier_and_its_partial_result_comes_back(
    tmp_path: Path, jobs_on: Any
) -> None:
    """``stop``: the job ends ``stopped`` only after its process is gone; the notice carries what it said."""

    gate = tmp_path / "never"
    rig = await _rig(tmp_path, script=_script(gate=gate, partial="halfway there"))
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await wait_for(lambda: (rig.jobs()[0].progress or {}).get("last_text") == "halfway there")
    stopped = await rig.call({"action": "stop", "id": agent_id})
    assert not stopped.is_error, _text(stopped)
    snap = await _settled(rig)
    assert snap.state == "stopped"
    (notice,) = rig.harness.undelivered_notices()
    assert "was stopped" in notice.body and "halfway there" in notice.body
    # Verify N4: the job's own stop reason, not the foreground "stop, /new, …".
    # Fails on ``b387b4b5``: "stopped by the session (stop, /new, /resume, /fork or quit)".
    result = _text(await rig.call({"action": "result", "id": agent_id}))
    assert 'stopped by you (agent(action="stop"))' in result
    assert "/new" not in result and 'stopped by you (agent(action="stop"))' in notice.body
    facts = [e.data for e in await rig.session.get_entries() if e.type == "custom" and e.custom_type == JOB_FACT_TYPE]
    (process,) = [f for f in facts if f["fact"] == "process"]
    with pytest.raises(ProcessLookupError):
        os.kill(process["evidence"]["pid"], 0)
    await rig.service.shutdown()


@REAL_CHILD
async def test_result_rereads_the_whole_result_and_a_long_one_points_there(tmp_path: Path, jobs_on: Any) -> None:
    """The 16 KiB framed cap (owner, 2026-11-07) points to ``agent(action="result")`` and the session file."""

    answer = "".join(f"line {i:05d} of a long answer\n" for i in range(1400))  # ~37 KiB
    rig = await _rig(tmp_path, script=_script(answer))
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await _settled(rig)
    (notice,) = rig.harness.undelivered_notices()
    assert len(notice.body.encode("utf-8")) <= NOTICE_MAX_BYTES
    assert (
        f'the whole result: agent(action="result", id="{agent_id}"), or its delegated session '
        f"file (Session: {agent_id})" in notice.body
    )
    assert str(tmp_path) not in notice.body, "no path (ADR-0243 §5)"
    assert len(notice.body.encode("utf-8")) > NOTICE_BODY_MAX_BYTES - 1024
    pages: list[str] = []
    offset = 0
    while True:
        result = await rig.call({"action": "result", "id": agent_id, "offset": offset})
        assert not result.is_error
        text = _text(result)
        body, _sep, more = text.partition("\n\n… more: ")
        pages.append(body)
        if not more:
            break
        offset = int(more.rsplit("offset=", 1)[1].rstrip(")"))
    whole = "".join(pages)
    assert whole.startswith("line 00000 of a long answer")
    assert "line 01399 of a long answer" in whole
    assert len(pages) >= 2
    await rig.service.shutdown()


@pytest.mark.parametrize(
    "answer",
    [
        "[aelix system notice" * 2000,  # escaping lengthens it by 8 bytes a time
        "<background-job-result" * 1500,  # by 3
        "plain text, " * 2000,
        # 12,000 bytes raw, under the body budget; 16,800 escaped, over the frame's.
        "[aelix system notice" * 600,
    ],
    ids=["forged-header", "forged-tag", "plain", "fits-raw-not-escaped"],
)
def test_a_cut_notice_keeps_both_pointers_in_an_uncut_footer(answer: str) -> None:
    """Codex #12 and verify N1: the body is cut by its ESCAPED size, the frame never
    cuts it again, and the next step — which the frame never cuts — names
    ``agent(action="result", id=…)``, the child's session and that the job id
    in the header names the same agent.

    Fails on ``b387b4b5`` (forged-header): ``size=15871 has_child_pointer=False``
    and the frame's own ``job(action="result"`` recut. ``fits-raw-not-escaped``
    kills verify-r2's J02 (the entry check measuring raw bytes): the body goes
    uncut and the frame cuts it with ``job(action="result"``.
    """

    from aelix_coding_agent.jobs import notice as nt

    payload = {"text": answer, "agent_id": "child123", "session_id": "child123"}
    body = DelegationKindAdapter(DelegationJobs(), session=lambda: None).render_notice(
        ClientOutcome("done", payload=payload)
    )
    framed = nt.frame(
        job_id="0123456789abcdef",
        kind=KIND,
        label="agent",
        title="scout: t",
        status="done",
        elapsed=1,
        body=body.body,
        next_action=body.next_action,
    )
    assert len(nt.escape_body(body.body).encode("utf-8")) <= NOTICE_BODY_MAX_BYTES
    assert 'job(action="result"' not in framed, "the frame did not have to cut it again"
    footer = framed.split("</background-job-result>", 1)[1]
    assert 'agent(action="result", id="child123")' in footer
    assert "(Session: child123)" in footer
    assert "The job id above names the same agent; either id works" in footer
    assert len(framed.encode("utf-8")) <= NOTICE_MAX_BYTES - nt.PLACEMENT_LINE_RESERVE


@REAL_CHILD
async def test_another_sessions_agent_is_not_found(tmp_path: Path, jobs_on: Any) -> None:
    """Codex r1 #5 for this client: an id from another session reads, waits on and stops nothing."""

    rig = await _rig(tmp_path)
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await _settled(rig)
    other = await make_session(tmp_path, name="other.jsonl", session_id="other")
    await rig.service.bind(make_harness(other))
    for action in ("status", "wait", "stop", "result"):
        result = await rig.call({"action": action, "id": agent_id})
        assert result.is_error and "No background agent" in _text(result), action
    await rig.service.shutdown()


# === budget and admission ============================================================


async def test_a_notice_turn_keeps_the_prompts_delegation_budget(tmp_path: Path, jobs_on: Any) -> None:
    """A background result's turn is not a prompt: it must not buy the next twelve delegations."""

    rig = await _rig(tmp_path)
    runtime = rig.ext.runtime
    assert runtime is not None
    await rig.prompt_started()
    runtime._delegations_this_prompt = 7
    await rig.prompt_started(source="notice")
    assert runtime._delegations_this_prompt == 7
    await rig.prompt_started(source="interactive")
    assert runtime._delegations_this_prompt == 0
    await rig.service.shutdown()


@REAL_CHILD
async def test_a_spawn_is_charged_against_the_prompt_budget(tmp_path: Path, jobs_on: Any) -> None:
    rig = await _rig(tmp_path)
    runtime = rig.ext.runtime
    assert runtime is not None
    runtime._delegations_this_prompt = 11
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    assert runtime.remaining_delegation_budget() == 0
    refused = await rig.call({"action": "spawn", "profile": "scout", "task": "t2"})
    assert refused.is_error and "maximum number" in _text(refused)
    await _settled(rig)
    await rig.service.shutdown()


@REAL_CHILD
async def test_no_free_slot_refuses_before_any_dialog(tmp_path: Path, jobs_on: Any) -> None:
    """F = 0 refuses before consent (contract §2.7): no dialog is shown, nothing starts.

    The refusal counts delegated agents, as the model knows them, whatever door
    the call came through (verify S3). Fails on ``b387b4b5``: "1 background jobs
    of this kind already run".
    """

    gate = tmp_path / "go"
    rig = await _rig(
        tmp_path,
        script=_script(gate=gate),
        settings={"limits": {"agents": 1}},
        has_ui=True,
        profile_extra="approval_mode: ask",
    )

    async def baseline(title: str, options: list[str], opts: object = None) -> object:
        rig.ui.calls.append((title, list(options)))
        return options[0]

    rig.ui.select = baseline  # type: ignore[method-assign]
    # The first spawn asks (``approval_mode: ask``) and is allowed; it holds the one slot.
    first = await rig.call({"action": "spawn", "profile": "scout", "task": "t"})
    assert not first.is_error, _text(first)
    dialogs = len(rig.ui.calls)
    assert dialogs == 1
    for args in (
        {"profile": "scout", "task": "foreground"},
        {"action": "spawn", "profile": "scout", "task": "another"},
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b"]},
    ):
        refused = await rig.call(args)
        assert _text(refused) == (
            "Delegation refused: 1 delegated agent already runs; this limit is set in settings "
            "backgroundJobs.limits.agents. Stop one or wait; do not retry."
        ), args
        assert refused.is_error and "background jobs" not in _text(refused), args
    assert len(rig.ui.calls) == dialogs, "no dialog for a call that has no slot"
    gate.write_text("")
    await _settled(rig)
    assert rig.service.admission.count(KIND) == 0
    await rig.service.shutdown()


@REAL_CHILD
async def test_batch_sized_to_free_slots_never_refuses_after_consent(tmp_path: Path, jobs_on: Any) -> None:
    """Codex r1 #17: F = min(4, tasks, free) is reserved before the dialog; the per-call semaphore is F."""

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate), settings={"limits": {"agents": 3}})
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "holds one"}))
    assert rig.service.admission.count(KIND) == 1
    seen: list[int] = []
    count = rig.service.admission.count

    async def watch() -> None:
        while True:
            seen.append(count(KIND))
            await asyncio.sleep(0.01)

    watcher = asyncio.ensure_future(watch())
    runtime = rig.ext.runtime
    assert runtime is not None
    live: list[int] = []
    original = runtime._run

    async def counting(*args: Any, **kwargs: Any) -> Any:
        live.append(len(runtime._children) + 1)
        return await original(*args, **kwargs)

    runtime._run = counting  # type: ignore[method-assign]
    gate.write_text("")
    result = await rig.call({"profile": "scout", "mode": "parallel", "tasks": ["a", "b", "c", "d"]})
    watcher.cancel()
    assert not result.is_error, _text(result)
    assert "refused" not in _text(result).lower()
    assert max(seen) <= 3, "the call took F = 2 slots beside the one the job held"
    assert max(live) <= 2
    await _settled(rig)
    assert rig.service.admission.count(KIND) == 0
    await rig.service.shutdown()


@REAL_CHILD
async def test_a_foreground_run_holds_its_slot_while_it_lives(tmp_path: Path, jobs_on: Any) -> None:
    """All live delegated agents count, foreground and background (ADR-0199 (d) as amended)."""

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    task = asyncio.ensure_future(rig.call({"profile": "scout", "task": "fg"}))
    await wait_for(lambda: rig.service.admission.count(KIND) == 1)
    gate.write_text("")
    result = await task
    assert not result.is_error, _text(result)
    assert rig.service.admission.count(KIND) == 0
    await rig.service.shutdown()


@REAL_CHILD
async def test_agents_run_takes_its_slot_too(tmp_path: Path, jobs_on: Any) -> None:
    """The user's door counts as well: with the one slot held, ``/agents run`` is refused, not queued."""

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate), settings={"limits": {"agents": 1}})
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    runtime = rig.ext.runtime
    assert runtime is not None
    resolved = runtime.resolve_profile("scout")
    result = await runtime.spawn(resolved, "by hand")
    assert not result.ok and "backgroundJobs.limits.agents" in (result.summary or "")
    gate.write_text("")
    await _settled(rig)
    await rig.service.shutdown()


# === the placement hint ==================================================================


@REAL_CHILD
async def test_resume_hint_checked_against_current_branch(tmp_path: Path, jobs_on: Any) -> None:
    """Codex r1 #14: the resume hint is offered only where the branch now current holds the agent's start."""

    rig = await _rig(tmp_path)
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await _settled(rig)
    adapter = DelegationKindAdapter(DelegationJobs(), session=lambda: rig.session)
    entries = await rig.session.get_entries()
    (start,) = [
        e for e in entries if e.type == "custom" and e.custom_type == CHILD_SESSION_TYPE and e.data.get("phase") == "start"
    ]
    outcome = ClientOutcome(
        "done",
        payload={
            "key": start.data["key"],
            "session_id": start.data["child"]["session_id"],
            "profile": "scout",
        },
    )
    on_branch = await adapter.finalize_at_placement(outcome, list(entries))
    assert on_branch is not None and 'agent(action="resume"' in on_branch
    off_branch = await adapter.finalize_at_placement(
        outcome, [e for e in entries if e.custom_type != CHILD_SESSION_TYPE]  # type: ignore[attr-defined]
    )
    assert off_branch is not None and "not on the conversation's current branch" in off_branch
    assert 'action="resume"' not in off_branch
    await rig.service.shutdown()


# === the runner's rules ===================================================================


@REAL_CHILD
async def test_old_runner_never_calls_an_invalidated_api(tmp_path: Path, jobs_on: Any) -> None:
    """ADR-0293 §3 for this client: a reload invalidates the API mid-run, and the job still ends ``done``."""

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    rig.runtime.invalidate("reloaded")
    with pytest.raises(Exception, match="reloaded|stale"):
        rig.api.get_active_tools()
    gate.write_text("")
    snap = await _settled(rig)
    assert snap.state == "done", snap
    (notice,) = rig.harness.undelivered_notices()
    assert "the background answer" in notice.body
    await rig.service.shutdown()


@REAL_CHILD
async def test_esc_on_a_wait_cancels_the_wait_not_the_agent(tmp_path: Path, jobs_on: Any) -> None:
    """The parent's Esc reaches its own turn only: a wait it cancels leaves the agent running (#604)."""

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    waiting = asyncio.ensure_future(rig.call({"action": "wait", "id": agent_id}))
    await asyncio.sleep(0.2)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await asyncio.sleep(0.2)
    assert rig.jobs()[0].state == "running"
    gate.write_text("")
    snap = await _settled(rig)
    assert snap.state == "done"
    await rig.service.shutdown()


@REAL_CHILD
async def test_the_child_is_asked_for_print_done_and_a_foreground_one_is_not(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AELIX_JOBS_PROTOCOL=1`` and the job's ``AELIX_JOB`` marker reach a background child only (contract §2.4).

    Both are INHERITED here too, as if whatever launched this aelix had set
    them: the background child gets its own job's marker in their place, and a
    foreground child gets neither (Codex #1). Fails on ``b387b4b5``: the
    foreground child read ``protocol=1 job=inherited:0``.
    """

    monkeypatch.setenv("AELIX_JOBS_PROTOCOL", "1")
    monkeypatch.setenv("AELIX_JOB", "inherited:0")
    echo = _stub(
        "start()\n"
        'say("protocol=%s job=%s" % (os.environ.get("AELIX_JOBS_PROTOCOL"), os.environ.get("AELIX_JOB")))\n'
        "done()\n"
    )
    rig = await _rig(tmp_path, script=echo)
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    snap = await _settled(rig)
    protocol, job = snap.brief.split(" ")
    assert protocol == "protocol=1"
    job_id, nonce = job.removeprefix("job=").split(":")
    assert job_id == snap.job_id and len(nonce) >= 8, "the job's own marker (verify V20)"
    foreground = await rig.call({"profile": "scout", "task": "fg"})
    assert "protocol=None job=None" in _text(foreground)
    await rig.service.shutdown()


@REAL_CHILD
async def test_a_door_that_brought_no_slot_takes_one_and_gives_it_back(tmp_path: Path, jobs_on: Any) -> None:
    """``_run``'s belt: a door that reserved nothing takes its slot in the await-free block (#611 S1c)."""

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate), settings={"limits": {"agents": 1}})
    runtime = rig.ext.runtime
    assert runtime is not None
    resolved = runtime.resolve_profile("scout")
    grant = type("Grant", (), {"consented": True, "mode": PermissionMode.PLAN, "disclosure": ""})()
    task = asyncio.ensure_future(runtime.spawn_granted(grant, resolved, "no reservation"))
    await wait_for(lambda: rig.service.admission.count(KIND) == 1)
    refused = await runtime.spawn_granted(grant, resolved, "a second one")
    assert not refused.ok and "backgroundJobs.limits.agents" in (refused.summary or "")
    gate.write_text("")
    result = await task
    assert result.ok, result
    assert rig.service.admission.count(KIND) == 0
    await rig.service.shutdown()



# === every terminal path gives the call's slots back, once (Codex #8, verify S4) =========


class _GiveBacks:
    """Every slot reserved and every slot given back, through the service's own admission."""

    def __init__(self, rig: _Rig, monkeypatch: pytest.MonkeyPatch) -> None:
        self.taken: list[int] = []
        self.given: list[int] = []
        admission = rig.service.admission
        take, give = admission.reserve_up_to, admission._give_back

        def reserve_up_to(*args: Any, **kwargs: Any) -> Any:
            reservation = take(*args, **kwargs)
            self.taken.append(reservation.slots)
            return reservation

        def give_back(key: str, slots: int) -> None:
            self.given.append(slots)
            give(key, slots)

        monkeypatch.setattr(admission, "reserve_up_to", reserve_up_to)
        monkeypatch.setattr(admission, "_give_back", give_back)

    def balanced(self) -> bool:
        return sum(self.taken) == sum(self.given)


async def test_a_call_another_extensions_hook_blocked_gives_its_slot_back(
    tmp_path: Path, jobs_on: Any
) -> None:
    """Codex #8, through the real harness: this hook approves, a later one blocks, ``execute()``
    never runs — five times, under a limit of four.

    Fails on ``b387b4b5``: ``admission=4`` and the fifth call's own "4 background
    jobs of this kind already run".
    """

    from aelix_agent_core.harness.core import AgentHarnessOptions
    from aelix_agent_core.harness.hooks import ToolCallResult
    from aelix_ai.messages import AssistantMessage, ToolCallContent
    from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model

    rig = await _rig(tmp_path, settings={"limits": {"agents": 4}})
    blocker = Extension(name="later-policy")

    async def deny(_event: Any, _ctx: Any) -> ToolCallResult:
        return ToolCallResult(block=True, reason="denied by later policy")

    blocker.handlers["tool_call"] = [deny]
    turns = 0

    async def model(*_a: Any, **_k: Any) -> Any:
        nonlocal turns
        turns += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if turns <= 5:
            message = AssistantMessage(
                content=[
                    ToolCallContent(
                        tool_call_id=f"call-{turns}",
                        tool_name=AGENT_TOOL_NAME,
                        input={"action": "spawn", "profile": "scout", "task": "t"},
                    )
                ],
                stop_reason="tool_use",
            )
        else:
            message = AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        yield AssistantEndEvent(message=message)

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="fixture", provider="fixture"),
            stream_fn=model,
            session=rig.session,
            extensions=[rig.extension, blocker],
            runtime=rig.runtime,
            cwd=str(rig.cwd),
        )
    )
    try:
        await harness.prompt("go")
        results = [
            getattr(block, "text", "")
            for message in harness.state.messages
            if getattr(message, "tool_call_id", None)
            for block in getattr(message, "content", [])
        ]
        assert results == ["denied by later policy"] * 5
        assert rig.service.admission.count(KIND) == 0
        assert rig.ext._pending == {}
    finally:
        await harness.dispose()
        await rig.service.shutdown()


async def test_a_denied_dialog_and_a_cancelled_one_give_their_slots_back(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V07, V08: no slot outlives a dialog that said no or was cancelled — spawn, run and batch."""

    rig = await _rig(
        tmp_path, settings={"limits": {"agents": 1}}, has_ui=True, profile_extra="approval_mode: ask"
    )
    spy = _GiveBacks(rig, monkeypatch)
    seen: list[list[str]] = []

    async def deny(title: str, options: list[str], opts: object = None) -> object:
        seen.append(list(options))
        return CANCEL_OPTION

    rig.ui.select = deny  # type: ignore[method-assign]
    for args in (
        {"action": "spawn", "profile": "scout", "task": "t"},
        {"profile": "scout", "task": "fg"},
        {"profile": "scout", "mode": "parallel", "tasks": ["a", "b"]},
    ):
        refused = await rig.call(args)
        assert refused.is_error, _text(refused)
        assert rig.service.admission.count(KIND) == 0, args
    assert len(seen) == 3

    async def cancel(title: str, options: list[str], opts: object = None) -> object:
        raise asyncio.CancelledError

    rig.ui.select = cancel  # type: ignore[method-assign]
    for args in (
        {"action": "spawn", "profile": "scout", "task": "t"},
        {"profile": "scout", "task": "fg"},
    ):
        with pytest.raises(asyncio.CancelledError):
            await rig.call(args)
        assert rig.service.admission.count(KIND) == 0, args
    assert rig.ext._pending == {}
    assert spy.balanced() and len(spy.taken) == 5
    await rig.service.shutdown()


async def test_approved_calls_never_run_give_their_slots_back_at_the_next_prompt_and_at_shutdown(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V25: ``_drop_pending`` gives back the slots of approvals ``execute()`` never spent."""

    rig = await _rig(tmp_path, settings={"limits": {"agents": 4}})
    spy = _GiveBacks(rig, monkeypatch)
    hook = rig.extension.handlers["tool_call"][0]

    async def approve(tcid: str, args: dict[str, Any]) -> None:
        event = ToolCallHookEvent(tool_call_id=tcid, tool_name=AGENT_TOOL_NAME, args=args)
        assert await hook(event, rig.ctx) is None

    await approve("a", {"action": "spawn", "profile": "scout", "task": "t"})
    await approve("b", {"profile": "scout", "mode": "parallel", "tasks": ["x", "y", "z"]})
    assert rig.service.admission.count(KIND) == 4
    await rig.prompt_started()
    assert rig.service.admission.count(KIND) == 0 and rig.ext._pending == {}
    await approve("c", {"profile": "scout", "mode": "chain", "tasks": ["x", "y"]})
    assert rig.service.admission.count(KIND) == 1, "a chain runs one child at a time (verify N2)"
    await rig.extension.handlers["session_shutdown"][0](None, rig.ctx)  # type: ignore[arg-type]
    assert rig.service.admission.count(KIND) == 0 and rig.ext._pending == {}
    assert spy.balanced()
    await rig.service.shutdown()


@REAL_CHILD
async def test_a_runner_that_crashes_gives_its_slot_back_once(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash before the launch (the call's error) and one after it (the job fails): one give-back each."""

    rig = await _rig(tmp_path, settings={"limits": {"agents": 1}})
    spy = _GiveBacks(rig, monkeypatch)
    runtime_type = type(rig.ext.runtime)
    original = runtime_type._run

    async def crash(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the runner crashed")

    monkeypatch.setattr(runtime_type, "_run", crash)
    refused = await rig.call({"action": "spawn", "profile": "scout", "task": "t"})
    assert refused.is_error and "the runner crashed" in _text(refused)
    assert rig.service.admission.count(KIND) == 0
    monkeypatch.setattr(runtime_type, "_run", original)

    def broken(_result: Any) -> str:
        raise RuntimeError("the runner crashed after the launch")

    monkeypatch.setattr(jobs_client, "_result_text", broken)
    started = _text(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    job_id = started.split("its job is ", 1)[1].split(")", 1)[0]
    snap = await _settled(rig, job_id)
    assert snap.state == "failed"
    assert rig.service.admission.count(KIND) == 0
    assert spy.balanced() and spy.given == [1, 1]
    await rig.service.shutdown()


@REAL_CHILD
async def test_a_resume_takes_its_slot_before_its_dialog_and_gives_it_back(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V09: a ``resume`` is refused before its dialog when no slot is free,
    holds one while it runs, and gives it back once.

    The stub child writes the user line a real child's session file would hold,
    so the agent is resumable.
    """

    gate = tmp_path / "go"
    script = _stub(
        "path = sys.argv[1]\n"
        "with open(path, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'type': 'message', 'id': 'u1', 'parentId': None,"
        " 'timestamp': '2026-11-09T00:00:00Z', 'message': {'role': 'user',"
        " 'content': [{'type': 'text', 'text': 'Task'}]}}) + '\\n')\n"
        "start()\n"
        f"while not os.path.exists({str(gate)!r}):\n"
        "    time.sleep(0.02)\n"
        "say('worked')\n"
        "done()\n"
        'if os.environ.get("AELIX_JOBS_PROTOCOL") == "1":\n'
        '    emit({"type": "aelix_print_done"})\n'
    )
    channel = PrintChannel(
        grace=2.0,
        argv_builder=lambda *_a, **k: [sys.executable, "-c", script, k.get("session_path") or ""],
    )
    rig = await _rig(
        tmp_path,
        channel=channel,
        settings={"limits": {"agents": 1}},
        has_ui=True,
        profile_extra="approval_mode: ask",
    )
    spy = _GiveBacks(rig, monkeypatch)

    async def allow(title: str, options: list[str], opts: object = None) -> object:
        rig.ui.calls.append((title, list(options)))
        return options[0]

    rig.ui.select = allow  # type: ignore[method-assign]
    gate.write_text("")
    first = await rig.call({"profile": "scout", "task": "first"})
    assert not first.is_error, _text(first)
    child_id = _text(first).split("Session: ", 1)[1].split()[0]
    gate.unlink()
    _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "holds the slot"}))
    dialogs = len(rig.ui.calls)
    resume = {"action": "resume", "profile": "scout", "id": child_id, "task": "again"}
    refused = await rig.call(resume)
    # The hook's own refusal, word for word — not ``_run``'s belt after the
    # grant, which would answer with an agent result around the same sentence.
    assert refused.is_error and _text(refused) == (
        "Delegation refused: 1 delegated agent already runs; this limit is set in settings "
        "backgroundJobs.limits.agents. Stop one or wait; do not retry."
    )
    assert len(rig.ui.calls) == dialogs, "refused before its dialog"
    gate.write_text("")
    await _settled(rig)
    gate.unlink()
    running = asyncio.ensure_future(rig.call(resume))
    await wait_for(lambda: rig.service.admission.count(KIND) == 1)
    gate.write_text("")
    resumed = await running
    assert not resumed.is_error, _text(resumed)
    assert rig.service.admission.count(KIND) == 0
    assert spy.balanced()
    await rig.service.shutdown()


@REAL_CHILD
async def test_a_branch_holding_only_the_settle_gets_no_resume_hint(tmp_path: Path, jobs_on: Any) -> None:
    """Codex #9: the parent moved branches while the agent ran, so only its settle is on the
    branch the result is placed on — the hint must not name a resume its own lookup refuses.

    Fails on ``b387b4b5``: ``branch_phases=["settle"]`` and the hint
    ``To continue this agent: agent(action="resume", …)``, which is then refused.
    """

    gate = tmp_path / "go"
    rig = await _rig(tmp_path, script=_script(gate=gate))
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    await rig.session.move_to(None)
    await rig.session.append_session_name("other branch")
    gate.write_text("")
    snap = await _settled(rig)
    job = rig.service._jobs[snap.job_id]
    branch = list(await rig.session.get_branch())
    phases = [e.data.get("phase") for e in branch if getattr(e, "custom_type", None) == CHILD_SESSION_TYPE]
    assert phases == ["settle"]
    adapter = DelegationKindAdapter(rig.ext._delegation_jobs, session=lambda: rig.session)  # type: ignore[arg-type]
    hint = await adapter.finalize_at_placement(job.outcome, branch)
    assert hint is not None and 'action="resume"' not in hint
    assert "not on the conversation's current branch" in hint
    refused = await rig.call({"action": "resume", "id": agent_id, "profile": "scout", "task": "continue"})
    assert refused.is_error
    await rig.service.shutdown()


def test_spawn_refuses_a_background_key() -> None:
    """Verify N5: ``background`` is no key of ``agent()``; a spawn says so instead of ignoring it.

    Fails on ``b387b4b5``: the call parsed.
    """

    from aelix_agents.tool import AgentCallError, parse_agent_call

    for value in (True, False):
        with pytest.raises(AgentCallError, match="takes no 'background'"):
            parse_agent_call(
                {"action": "spawn", "profile": "scout", "task": "t", "background": value},
                background=True,
            )


@REAL_CHILD
async def test_a_spawn_is_charged_once_admitted_and_two_in_one_message_meet_the_belt(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify N3: a start the service refused costs nothing; a launch tried costs one, as
    ``_run`` charges a child that died at once; two spawns approved in one
    message with room for one start one.

    Fails on ``b387b4b5``: the refused start cost one unit, and the second of
    the two spawns started (``remaining=-1``).
    """

    from aelix_coding_agent.jobs.service import JobService, JobStartRefused

    rig = await _rig(tmp_path)
    runtime = rig.ext.runtime
    assert runtime is not None
    refuse_once = [True]
    real_start = JobService.start

    async def start(self: Any, *args: Any, **kwargs: Any) -> Any:
        if refuse_once.pop() if refuse_once else False:
            raise JobStartRefused("the service refused it")
        return await real_start(self, *args, **kwargs)

    monkeypatch.setattr(JobService, "start", start)
    refused = await rig.call({"action": "spawn", "profile": "scout", "task": "t"})
    assert refused.is_error and "the service refused it" in _text(refused)
    assert runtime._delegations_this_prompt == 0
    runtime._delegations_this_prompt = 11
    hook = rig.extension.handlers["tool_call"][0]
    for tcid in ("one", "two"):
        event = ToolCallHookEvent(
            tool_call_id=tcid,
            tool_name=AGENT_TOOL_NAME,
            args={"action": "spawn", "profile": "scout", "task": tcid},
        )
        assert await hook(event, rig.ctx) is None, "each hook alone found room for one"
    first = await rig.tool.execute({}, ToolExecutionContext(tool_call_id="one"))
    second = await rig.tool.execute({}, ToolExecutionContext(tool_call_id="two"))
    assert not first.is_error, _text(first)
    assert second.is_error and "maximum number" in _text(second)
    assert runtime.remaining_delegation_budget() == 0
    await _settled(rig)
    assert rig.service.admission.count(KIND) == 0
    await rig.service.shutdown()


# === verify's surviving mutants (S4): what each one changed, pinned ======================


class _FakeService:
    """``snapshot`` / ``changed`` over one hand-set job, for the wait's own rules."""

    def __init__(self, state: str, notice_state: str = "none") -> None:
        self.state = state
        self.notice_state = notice_state
        self._waiters: list[asyncio.Future[None]] = []

    def snapshot(self, job_id: str) -> Any:
        from aelix_coding_agent.jobs.contract import JobSnapshot

        return JobSnapshot(
            job_id=job_id,
            kind=KIND,
            label="agent",
            title="scout: t",
            state=self.state,  # type: ignore[arg-type]
            owner="me",
            created=0.0,
            elapsed=1.0,
            last_activity_ago=None,
            quiet=False,
            result_waiting=self.notice_state != "delivered",
            notice_state=self.notice_state,  # type: ignore[arg-type]
            unresolved_reason="pid 1 not confirmed stopped" if self.state == "unresolved" else None,
        )

    def changed(self) -> asyncio.Future[None]:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(future)
        return future

    def touch(self) -> None:
        for future in self._waiters:
            if not future.done():
                future.set_result(None)
        self._waiters.clear()


async def test_a_wait_on_an_unresolved_agent_returns_at_once() -> None:
    """Verify V13: ``unresolved`` ends a wait — waiting cannot change it.

    Under the mutant the wait runs its whole 120 s default.
    """

    service = _FakeService("unresolved")
    text, empty = await asyncio.wait_for(
        jobs_client.wait_for_agents(service, DelegationJobs(), ["j"], requested=None), 5
    )
    assert not empty and ": unresolved" in text
    assert "An unresolved agent is not waited on" in text


class _TwoAgents:
    """``snapshot`` / ``changed`` over two hand-set jobs, ``a`` and ``b``."""

    def __init__(self, a: _FakeService, b: _FakeService) -> None:
        self.by_id = {"a": a, "b": b}

    def snapshot(self, job_id: str) -> Any:
        return self.by_id[job_id].snapshot(job_id)

    def changed(self) -> asyncio.Future[None]:
        return self.by_id["a"].changed()


async def test_a_wait_over_an_ended_agent_and_an_unresolved_one_says_each_what_is_true() -> None:
    """Verify-r2 J10: the result line is about the agents that ENDED. One ended and
    its notice was delivered, one is ``unresolved``: the ended one's result was
    given, and the unresolved one is not waited on. An unresolved agent alone,
    its own notice delivered, has no result line at all.

    A guard on ``fa742f77``: kills verify-r2's J10 (judging "delivered" over
    every agent the wait saw settle), under which the first wait says the
    result "arrives as a notice right after this call" and the second that it
    "was already given".
    """

    both = _TwoAgents(
        _FakeService("done", notice_state="delivered"),
        _FakeService("unresolved", notice_state="pending"),
    )
    text, empty = await asyncio.wait_for(
        jobs_client.wait_for_agents(both, DelegationJobs(), ["a", "b"], requested=None), 5
    )
    assert not empty
    assert "Its result was already given to you as a notice" in text
    assert "arrives as a notice right after this call" not in text
    assert "An unresolved agent is not waited on" in text

    alone = _FakeService("unresolved", notice_state="delivered")
    text, empty = await asyncio.wait_for(
        jobs_client.wait_for_agents(alone, DelegationJobs(), ["j"], requested=None), 5
    )
    assert not empty and "already given" not in text and "arrives as a notice" not in text
    assert "An unresolved agent is not waited on" in text


async def test_a_wait_that_saw_an_agent_end_gives_its_notice_time_to_be_posted() -> None:
    """Verify V28: the notice is posted within the 2 s settle loop, so it is placed right after the wait."""

    service = _FakeService("done", notice_state="none")

    async def post_later() -> None:
        await asyncio.sleep(0.2)
        service.notice_state = "pending"
        service.touch()

    poster = asyncio.ensure_future(post_later())
    text, empty = await jobs_client.wait_for_agents(service, DelegationJobs(), ["j"], requested=None)
    assert service.notice_state == "pending", "the wait returned after the notice was posted"
    assert not empty and "arrives as a notice right after this call" in text
    await poster


def test_stop_and_result_need_an_id() -> None:
    """Verify V14: ``stop`` with no id would stop nothing in particular; it is refused, as ``result`` is."""

    from aelix_agents.tool import AgentCallError, parse_agent_call

    for action in ("stop", "result"):
        with pytest.raises(AgentCallError):
            parse_agent_call({"action": action}, background=True)


@REAL_CHILD
async def test_the_runners_own_soft_leg_stops_the_agent_without_the_services_escalation(
    tmp_path: Path, jobs_on: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify V23: with the service's escalation off, the runner's own legs end the child."""

    rig = await _rig(tmp_path, script=_script(gate=tmp_path / "never"))
    monkeypatch.setattr(rig.service, "_escalate_later", lambda _job: None)
    agent_id = _spawned_id(await rig.call({"action": "spawn", "profile": "scout", "task": "t"}))
    (snap,) = rig.jobs()
    stopped = await rig.call({"action": "stop", "id": agent_id})
    assert not stopped.is_error, _text(stopped)
    await wait_for(lambda: rig.service.snapshot(snap.job_id).state == "stopped")  # type: ignore[union-attr]
    await rig.service.shutdown()
