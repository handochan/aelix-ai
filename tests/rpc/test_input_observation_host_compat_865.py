"""Compatibility with host overrides and call shapes from before input observation."""

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from aelix_agent_core import AgentContext, AgentLoopConfig, agent_loop, default_convert_to_llm
from aelix_agent_core.harness import core
from aelix_agent_core.harness.core import AgentHarness
from aelix_agent_core.runtime import agent_session_runtime
from aelix_agent_core.session.jsonl_storage import JsonlSessionStorage
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import Model
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.control_transcript import TranscriptService

from tests.rpc.control_v1 import rigs
from tests.test_agent_loop import _make_mock_stream

pytestmark = pytest.mark.usefixtures("rig_env")
rig_env = rigs.rig_env


async def test_capabilities_reads_host_transcript_availability_once_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[str] = []

    class HostRuntime(agent_session_runtime.AgentSessionRuntime):
        @property
        def stopped(self) -> str | None:
            reads.append("stopped")
            return super().stopped

        @property
        def transcript_transitioning(self) -> bool:
            reads.append("transcript_transitioning")
            return super().transcript_transitioning

    monkeypatch.setattr(agent_session_runtime, "AgentSessionRuntime", HostRuntime)
    async with rigs.open_rig("full", tmp_path) as rig:
        assert isinstance(rig.runtime, HostRuntime)
        reads.clear()
        response = await rigs.request(rig.wire, "caps", "capabilities", {})
        assert response["success"], response
        assert reads == ["stopped", "transcript_transitioning"]


def test_control_wildcard_import_preserves_host_input_names() -> None:
    class InputObservationService:
        pass

    host_names = {
        "INPUTS_METHOD": object(),
        "INPUTS_UNSUPPORTED": object(),
        "InputObservationService": InputObservationService,
    }
    namespace = host_names.copy()
    exec("from aelix_coding_agent.rpc.control import *", namespace)
    assert {name: namespace[name] for name in host_names} == host_names
    assert namespace["RpcControlPlane"] is RpcControlPlane


def test_control_plane_constructor_keeps_its_embedding_signature():
    parameters = tuple(inspect.signature(RpcControlPlane).parameters.values())
    assert tuple(p.name for p in parameters) == (
        "runtime_host", "permission_posture", "settings_manager", "auth_storage", "model_registry",
        "agent_service", "extension_errors", "emit", "selections", "jobs", "permissions",
        "transcript", "session_lock_errors",
    )
    assert parameters[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in parameters[1:])
    # #868 added the one non-None default; everything before it is the pre-#865 shape.
    assert [p.default for p in parameters[1:]] == [None] * 11 + [False]


async def test_the_loop_resolves_a_host_converter_once_per_request():
    """Base reads ``config.convert_to_llm`` once per request; a host whose config binds a
    converter on each read (a property) must see one binding and the provider the request that
    binding built (verify r4 finding 1)."""

    trace: list[str] = []
    payloads: list[str] = []

    class PerRequestConfig(AgentLoopConfig):
        @property
        def convert_to_llm(self):
            turn = sum(step.startswith("resolve") for step in trace) + 1
            trace.append(f"resolve {turn}")

            def convert(messages):
                trace.append(f"convert {turn}")
                return self.converter([UserMessage(content=[TextContent(text=f"request {turn}")])])

            return convert

        @convert_to_llm.setter
        def convert_to_llm(self, fn):
            self.converter = fn

    config = PerRequestConfig(
        model=Model(id="mock", provider="mock"), convert_to_llm=default_convert_to_llm
    )
    scripted = _make_mock_stream(
        [AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")]
    )

    async def stream(model, context, options):
        payloads.append(context.messages[0].content[0].text)
        async for event in scripted(model, context, options):
            yield event

    async def emit(event):
        pass

    await agent_loop(
        [UserMessage(content=[TextContent(text="hello")])], AgentContext(), config,
        emit=emit, stream_fn=stream,
    )
    assert trace == ["resolve 1", "convert 1"]
    assert payloads == ["request 1"]


def test_a_control_plane_subclass_with_its_own_input_observation_constructs():
    """A host subclass owns the public name ``input_observation``; the plane's own state is
    private, so the base constructor call still works (verify r4 finding 2)."""

    class HostControl(RpcControlPlane):
        @property
        def input_observation(self):
            return "host input log"

    runtime = SimpleNamespace(harness=SimpleNamespace(current_model=None))
    control = HostControl(runtime)
    assert control.input_observation == "host input log"
    assert control.capabilities()["version"] == 1


def test_a_transcript_service_subclass_with_its_own_input_fence_constructs():
    """Same for ``TranscriptService`` and a host's ``input_fence`` (verify r4 finding 3)."""

    class HostTranscript(TranscriptService):
        @property
        def input_fence(self):
            return "host transcript fence"

    transcript = HostTranscript(SimpleNamespace(), lambda *args: None)
    transcript.unbind()
    assert transcript.input_fence == "host transcript fence"


async def test_a_storage_with_its_own_settled_input_coordinates_still_settles(
    tmp_path: Path, monkeypatch
):
    """The r5 sweep: ``JsonlSessionStorage`` gained a helper under a public name in round 4.
    A storage class that owns ``settled_input_coordinates`` (here the class attribute stands
    in for a host subclass, since the rig builds the bundled class) must not be called by the
    observer, which settles through its private helper."""

    monkeypatch.setattr(
        JsonlSessionStorage, "settled_input_coordinates", property(lambda self: "host cut"),
        raising=False,
    )
    async with rigs.open_rig("full", tmp_path) as rig:
        assert rig.harness.session.get_storage().settled_input_coordinates == "host cut"
        enabled = await observe_if_available(rig)
        rig.wire.send({"type": "prompt", "id": "prompt", "message": "hello"})
        assert (await rig.wire.response("prompt"))["success"]
        # A settlement that calls the host's attribute never reaches idle (round 4 hung here).
        await asyncio.wait_for(rig.harness.wait_for_idle(), 30)
        settled = [
            row for row in rig.wire.records
            if row["type"] == "aelix_input" and row["event"] == "input_settled"
        ]
        assert [row["outcome"] for row in settled] == (["committed"] if enabled else [])


@pytest.mark.parametrize("surface", ["prompt", "queue", "persist"])
async def test_classic_calls_legacy_host_helpers_and_persistence(tmp_path: Path, monkeypatch, surface):
    persisted = []

    class FormatterHost(AgentHarness):
        def _prompt(self, text):
            return "host: " + text

        async def prompt(
            self, text, *, images=None, source="interactive", on_accept=None, _refused_by=None
        ):
            return await super().prompt(
                self._prompt(text), images=images, source=source, on_accept=on_accept,
                _refused_by=_refused_by,
            )

    class QueueHost(AgentHarness):
        def _observe_enqueue(self, message):
            pytest.fail("classic queue hit a helper owned by the host")

    class PersistenceHost(AgentHarness):
        async def _persist_observed_message(self, message, answered, *, notice, tracked, replaced):
            persisted.append(message.role)
            return await super()._persist_observed_message(
                message, answered, notice=notice, tracked=tracked, replaced=replaced
            )

    host = {"prompt": FormatterHost, "queue": QueueHost, "persist": PersistenceHost}[surface]
    monkeypatch.setattr(core, "AgentHarness", host)
    async with rigs.open_rig("full", tmp_path) as rig:
        if surface == "queue":
            rig.wire.send({"type": "steer", "id": "steer", "message": "classic"})
            assert (await rig.wire.response("steer"))["success"]
            assert [m.content[0].text for m in rig.harness._steering_queue._messages] == ["classic"]
            return
        rig.wire.send({"type": "prompt", "id": "prompt", "message": "hello"})
        reply = await rig.wire.response("prompt")
        assert reply["success"], reply
        await rig.harness.wait_for_idle()
        users = [
            row["message"]["content"][0]["text"]
            for row in rig.wire.records
            if row["type"] == "message_end" and row["message"]["role"] == "user"
        ]
        assert users == (["host: hello"] if surface == "prompt" else ["hello"])
        if surface == "persist":
            assert "user" in persisted


async def observe_if_available(rig):
    result = await rigs.request(rig.wire, "enable", "inputs/observe", {"enabled": True})
    return result["success"]


@pytest.mark.parametrize("deferred", [False, True], ids=["prepend", "defer"])
async def test_host_can_route_a_classic_steer_without_false_provenance(
    tmp_path: Path, monkeypatch, deferred
):
    gate = asyncio.Event()
    tasks = []

    class Host(AgentHarness):
        async def steer(self, text, *, images=None, final=False):
            forward = super().steer
            if deferred:
                async def deliver():
                    await gate.wait()
                    await forward("host: " + text, images=images, final=final)

                tasks.append(asyncio.create_task(deliver()))
            else:
                await forward("host reminder")
                await forward("host: " + text, images=images, final=final)

    monkeypatch.setattr(core, "AgentHarness", Host)
    async with rigs.open_rig("full", tmp_path) as rig:
        enabled = await observe_if_available(rig)
        rig.wire.send({"type": "steer", "id": "client", "message": "steer"})
        assert (await rig.wire.response("client"))["success"]
        gate.set()
        await asyncio.gather(*tasks)
        expected = ["host: steer"] if deferred else ["host reminder", "host: steer"]
        assert [m.content[0].text for m in rig.harness._steering_queue._messages] == expected
        events = [r for r in rig.wire.records if r["type"] == "aelix_input"]
        assert [(r["event"], r.get("reason")) for r in events] == (
            [("stream_gap", "unverifiable")] if enabled else []
        )


@pytest.mark.parametrize("assign_first", [False, True], ids=["before-assign", "after-assign"])
async def test_failed_enable_leaves_classic_dispatch_and_all_bindings_clean(
    tmp_path: Path, monkeypatch, assign_first
):
    async with rigs.open_rig("full", tmp_path) as rig:
        storage = rig.harness.session.get_storage()
        original = type(storage).__setattr__
        reject = True

        def reject_callback(self, name, value):
            if self is storage and name == "_input_replayed" and value is not None and reject:
                if assign_first:
                    original(self, name, value)
                raise ValueError("storage rejected replay callback")
            return original(self, name, value)

        monkeypatch.setattr(type(storage), "__setattr__", reject_callback)
        reply = await rigs.request(rig.wire, "rejected", "inputs/observe", {"enabled": True})
        assert not reply["success"]
        rig.wire.send({"type": "steer", "id": "after", "message": "classic"})
        assert (await rig.wire.response("after"))["success"]
        assert not [r for r in rig.wire.records if r["type"] == "aelix_input"]
        assert getattr(rig.harness, "_input_observation", None) is None
        assert getattr(storage, "_input_replayed", None) is None
        assert getattr(rig.runtime, "_transcript_fence", None) is None
        rig.wire.send({"type": "prompt", "id": "drain", "message": "drain"})
        assert (await rig.wire.response("drain"))["success"]
        await rig.harness.wait_for_idle()

        if "storage rejected replay callback" in reply["error"]:
            reject = False
            assert await observe_if_available(rig)
            rig.wire.send({"type": "steer", "id": "observed", "message": "new epoch"})
            assert (await rig.wire.response("observed"))["success"]
            events = [r for r in rig.wire.records if r["type"] == "aelix_input"]
            assert [(r["event"], r["seq"], r["requestId"]) for r in events] == [
                ("queued", 1, "observed")
            ]
