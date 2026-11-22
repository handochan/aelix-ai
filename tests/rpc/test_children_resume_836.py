"""#836 (ADR-0318) — RPC ``children/resume``: admission, the plane's checks, and the real runtime behind it.

``children/resume {id, task}`` is admitted as ``agents/run`` is (mutating,
cancellable, idle only), checks its params, binds the plane's dialogs for the
runtime's consent when the host enabled them and puts the previous UI back,
and answers ``{result}`` with ``details`` and ``tool_trail`` cut as
``agents/run`` cuts them. Driven here over the bundled runtime itself
(``test_host_resume_836``'s scene), so the plane reaches the real
``resume_for_host``.

Fails on ``0436193c``: ``children/resume`` is "Unsupported runtime control method".
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.rpc.control import (
    CANCELLABLE_METHODS,
    CONCURRENT_METHODS,
    MUTATING_METHODS,
    READ_ONLY_METHODS,
    TURN_SAFE_METHODS,
    RpcControlPlane,
)
from aelix_coding_agent.rpc.control_children import RESULT_TEXT_MAX
from aelix_coding_agent.subagent_contract import SubagentResult

from tests.agents_ext.test_agent_background_611 import jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_agent_resume_610 import _delegated
from tests.agents_ext.test_host_resume_836 import _host


class _Runtime:
    """The command runtime the plane binds its dialogs on (``_ExtensionRuntime``'s two members)."""

    def __init__(self, subagents: Any) -> None:
        self.subagents = subagents
        self.ui: Any = "the previous ui"
        self.bound: list[Any] = []

    def bind_ui(self, ui: Any) -> None:
        self.bound.append(ui)
        self.ui = ui


def _plane(subagents: Any) -> tuple[RpcControlPlane, _Runtime]:
    runtime = _Runtime(subagents)
    harness = SimpleNamespace(runtime=runtime, phase="idle", current_model=None)
    return RpcControlPlane(SimpleNamespace(harness=harness)), runtime


def test_children_resume_is_admitted_as_agents_run_is() -> None:
    assert "children/resume" in MUTATING_METHODS and "children/resume" in CANCELLABLE_METHODS
    assert "children/resume" not in TURN_SAFE_METHODS | CONCURRENT_METHODS | READ_ONLY_METHODS


async def test_the_plane_resumes_through_the_bundled_runtime(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    _host(scene, posture=PermissionMode.DEFAULT)
    plane, runtime = _plane(scene.runtime)
    assert "children/resume" in plane.capabilities()["methods"]
    answer = await plane.handle("children/resume", {"id": scene.child_id, "task": "keep going"})
    result = answer["result"]
    assert (result["ok"], result["status"], result["profile"]) == (True, "ok", "scout")
    assert result["session_id"] == scene.child_id
    assert len(scene.channel.plans) == 2
    assert runtime.bound == ["the previous ui"], "dialogs off: the previous UI is put back"


async def test_a_refusal_is_the_runtimes_sentence(tmp_path: Path) -> None:
    scene = await _delegated(tmp_path)
    _host(scene, posture=PermissionMode.DEFAULT)
    plane, _runtime = _plane(scene.runtime)
    with pytest.raises(ValueError, match="^Resume refused: no agent this conversation delegated"):
        await plane.handle("children/resume", {"id": "nope", "task": "go"})


class _Recording:
    """A ``HostResume`` that records what the plane bound and answers a long result."""

    def __init__(self, runtime: _Runtime | None = None) -> None:
        self.runtime = runtime
        self.ui_seen: Any = None
        self.ui_passed: Any = "not asked"

    async def resume_for_host(
        self,
        id: str,  # noqa: A002
        task: str,
        *,
        on_event: Any = None,
        ui: Any = None,
    ) -> SubagentResult:
        self.ui_seen = self.runtime.ui if self.runtime is not None else None
        self.ui_passed = ui
        long = "d" * (RESULT_TEXT_MAX + 5)
        if task == "at the bound":
            long = "d" * RESULT_TEXT_MAX
        return SubagentResult(
            id="sub-1",
            profile="scout",
            ok=True,
            status="ok",
            summary="s",
            details=long,
            tool_trail=long,
        )


@pytest.mark.parametrize("dialogs", [True, False])
async def test_dialogs_are_the_planes_while_it_runs_and_long_texts_are_cut(dialogs: bool) -> None:
    fake = _Recording()
    plane, runtime = _plane(fake)
    fake.runtime = runtime
    if dialogs:
        await plane.handle("ui/enable", {"enabled": True})
    result = (await plane.handle("children/resume", {"id": "s-1", "task": "go"}))["result"]
    assert fake.ui_seen is (plane.ui if dialogs else "the previous ui")
    assert fake.ui_passed is (plane.ui if dialogs else None), "passed for the consent too (C1)"
    assert runtime.ui == "the previous ui", "put back after the run"
    for key in ("details", "tool_trail"):
        assert len(result[key]) == RESULT_TEXT_MAX and result[key + "Truncated"] is True


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"task": "go"}, "id must be a non-empty string: the agent's session id"),
        ({"id": " ", "task": "go"}, "id must be a non-empty string: the agent's session id"),
        ({"id": 3, "task": "go"}, "id must be a non-empty string: the agent's session id"),
        ({"id": "s-1"}, "task must be a non-empty string: the agent's next instruction"),
        (
            {"id": "s-1", "task": "  "},
            "task must be a non-empty string: the agent's next instruction",
        ),
        ({"id": "s-1", "task": "a\udc80"}, "The instruction holds"),
    ],
)
async def test_the_params_are_checked_before_the_runtime_is_asked(
    params: dict[str, Any], error: str
) -> None:
    fake = _Recording()
    plane, runtime = _plane(fake)
    fake.runtime = runtime
    with pytest.raises(ValueError) as refused:
        await plane.handle("children/resume", params)
    assert str(refused.value).startswith(error), refused.value
    assert fake.ui_seen is None and runtime.bound == [], "the runtime was not asked"


async def test_without_a_host_resume_runtime_it_is_refused_and_not_listed() -> None:
    plane, _runtime = _plane(SimpleNamespace(contract_version=1))
    assert "children/resume" not in plane.capabilities()["methods"]
    with pytest.raises(ValueError, match="cannot resume an agent"):
        await plane.handle("children/resume", {"id": "s-1", "task": "go"})
    plane, _runtime = _plane(None)
    with pytest.raises(ValueError, match="Subagents are disabled"):
        await plane.handle("children/resume", {"id": "s-1", "task": "go"})
    bare = RpcControlPlane(
        SimpleNamespace(harness=SimpleNamespace(phase="idle", current_model=None))
    )
    with pytest.raises(ValueError, match="Subagents are disabled"):
        await bare.handle("children/resume", {"id": "s-1", "task": "go"})


async def test_a_text_at_the_cut_is_kept_whole() -> None:
    fake = _Recording()
    plane, runtime = _plane(fake)
    fake.runtime = runtime
    result = (await plane.handle("children/resume", {"id": "s-1", "task": "at the bound"}))[
        "result"
    ]
    assert len(result["details"]) == RESULT_TEXT_MAX and "detailsTruncated" not in result


# === fix round 1: consent in a process that reopened its session (Codex C1) =========


async def _first_run(tmp_path: Path) -> tuple[Any, Any, str]:
    """A model delegation on ``test_agent_background_611``'s rig, under ``auto-accept-edits``, and its child's id."""

    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session

    from tests.agents_ext.test_agent_background_611 import _rig
    from tests.agents_ext.test_agent_resume_610 import _Channel, _received_task

    channel = _Channel()
    rig = await _rig(tmp_path, channel=channel, posture=PermissionMode.AUTO_ACCEPT)
    first = await rig.call({"profile": "scout", "task": "read the scratch project"})
    assert not first.is_error
    path = channel.plans[0].session_path
    await _received_task(path)
    child = Session(await JsonlSessionStorage.open(LocalFileSystem(), path))
    return rig, channel, (await child.get_metadata()).id


@pytest.mark.usefixtures("jobs_on", "own_agent_dir")
@pytest.mark.parametrize("answer", ["Cancel", "yes"])
async def test_a_fresh_binding_asks_the_hosts_dialog_before_its_first_prompt(
    tmp_path: Path, answer: str
) -> None:
    """Codex C1's in-process probe: no hook has run on the new binding.

    Fails without fix round 1: no dialog was asked and the agent was resumed.
    """

    from aelix_agents.extension import AgentsExtension
    from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

    rig, channel, child_id = await _first_run(tmp_path)
    try:
        command = _ExtensionRuntime()
        fresh = AgentsExtension(
            posture=rig.ext.posture,
            agent_dir=str(rig.agent_dir),
            cwd=str(rig.cwd),
            project_trusted=False,
            session=lambda: rig.session,
        )
        fresh(ExtensionAPI(Extension(name="aelix-agents"), command))
        assert fresh.runtime is not None
        fresh.runtime.channel = channel
        assert fresh._ctx is None, "no hook has run on this binding"
        frames: list[dict[str, Any]] = []
        harness = SimpleNamespace(runtime=command, phase="idle", current_model=None)
        plane = RpcControlPlane(SimpleNamespace(harness=harness), emit=frames.append)
        await plane.handle("ui/enable", {"enabled": True})
        run = asyncio.ensure_future(
            plane.handle("children/resume", {"id": child_id, "task": "continue reading"})
        )
        for _ in range(200):
            selects = [f for f in frames if f.get("method") == "select"]
            if selects or run.done():
                break
            await asyncio.sleep(0.01)
        assert not run.done(), "the host was asked before anything ran"
        (question,) = selects
        assert "sent by the application running aelix" in question["title"]
        value = "Cancel" if answer == "Cancel" else question["options"][0]
        plane.ui.respond({"type": "extension_ui_response", "id": question["id"], "value": value})
        result = (await asyncio.wait_for(run, 5))["result"]
        if answer == "Cancel":
            assert result["status"] == "declined" and len(channel.plans) == 1
        else:
            assert result["ok"] and len(channel.plans) == 2
            assert channel.plans[1].permission_mode is PermissionMode.AUTO_ACCEPT
    finally:
        await rig.service.shutdown()


@pytest.mark.usefixtures("jobs_on")
async def test_a_fresh_rpc_process_asks_before_it_resumes(tmp_path: Path) -> None:
    """Codex C1's process probe: a real ``--mode rpc`` reopening the parent, dialogs on, no prompt.

    Fails without fix round 1: the first frame was the resume's answer, ``started:
    true``, and no ``select`` was sent.
    """

    import sys

    from tests.env_sandbox import child_env
    from tests.rpc.control_v1.test_real_child import ChildWire

    home = tmp_path / "home"
    home.mkdir()
    rig, _channel, child_id = await _first_run(tmp_path / "rig")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "aelix_coding_agent",
            "--mode",
            "rpc",
            "--offline",
            "--agents",
            "--permission-mode",
            "auto-accept-edits",
            "--session",
            rig.session.session_file,
            cwd=rig.cwd,
            env=child_env(home, AELIX_CODING_AGENT_DIR=str(rig.agent_dir), TMPDIR=str(tmp_path)),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        wire = ChildWire(proc)
        try:
            wire.send(
                {
                    "type": "aelix_control",
                    "id": "ui",
                    "method": "ui/enable",
                    "params": {"enabled": True},
                }
            )
            assert (await asyncio.wait_for(wire.response("ui"), 30))["success"] is True
            wire.send(
                {
                    "type": "aelix_control",
                    "id": "resume",
                    "method": "children/resume",
                    "params": {"id": child_id, "task": "continue reading"},
                }
            )
            first = await asyncio.wait_for(
                wire.record(
                    lambda r: (
                        (r.get("type") == "extension_ui_request" and r.get("method") == "select")
                        or (r.get("type") == "aelix_child" and r.get("event") == "child_start")
                        or (r.get("type") == "response" and r.get("id") == "resume")
                    )
                ),
                30,
            )
            assert first.get("method") == "select", first
            assert "sent by the application running aelix" in first["title"]
            wire.send({"type": "extension_ui_response", "id": first["id"], "value": "Cancel"})
            answer = await asyncio.wait_for(wire.response("resume"), 30)
            assert answer["success"] is True and answer["data"]["result"]["status"] == "declined"
        finally:
            await wire.stop()
    finally:
        await rig.service.shutdown()
