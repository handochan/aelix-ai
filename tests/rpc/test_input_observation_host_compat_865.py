"""Compatibility with host overrides and call shapes from before input observation."""

import asyncio
import inspect
from pathlib import Path

import pytest
from aelix_agent_core.harness import core
from aelix_agent_core.harness.core import AgentHarness
from aelix_coding_agent.rpc.control import RpcControlPlane

from tests.rpc.control_v1 import rigs

pytestmark = pytest.mark.usefixtures("rig_env")
rig_env = rigs.rig_env


def test_control_plane_constructor_keeps_its_embedding_signature():
    parameters = tuple(inspect.signature(RpcControlPlane).parameters.values())
    assert tuple(p.name for p in parameters) == (
        "runtime_host", "permission_posture", "settings_manager", "auth_storage", "model_registry",
        "agent_service", "extension_errors", "emit", "selections", "jobs", "permissions",
        "transcript",
    )
    assert parameters[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY and p.default is None for p in parameters[1:])


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
