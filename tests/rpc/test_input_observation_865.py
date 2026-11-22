"""Native input observation over the CLI control and event wire."""

import asyncio
import inspect
from pathlib import Path

import pytest
from aelix_agent_core import runtime as runtime_module
from aelix_agent_core.harness import core
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions, TranscriptOutcome
from aelix_agent_core.harness.hooks import (
    ContextResult,
    MessageEndEventResult,
    SessionBeforeSwitchResult,
)
from aelix_agent_core.runtime.agent_session_runtime import AgentSessionRuntime
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import Model
from aelix_coding_agent.rpc.control_transcript import TranscriptService

from tests.rpc.control_v1 import rigs

pytestmark = pytest.mark.usefixtures("rig_env")
rig_env = rigs.rig_env


async def enable(rig, request_id="enable"):
    reply = await rigs.request(rig.wire, request_id, "inputs/observe", {"enabled": True})
    assert reply["success"], reply
    return reply["data"]


def observed(rig, event):
    return [
        row
        for row in rig.wire.records
        if row.get("event") == event and row["type"] == "aelix_input"
    ]


async def prompt(rig, request_id, message, **kwargs):
    rig.wire.send({"type": "prompt", "id": request_id, "message": message, **kwargs})
    assert (await rig.wire.response(request_id))["success"]
    await rig.harness.wait_for_idle()


@pytest.mark.parametrize(
    "scenario", ["repeat", "body_changed", "pass_through", "reordered", "prefix_reordered"]
)
async def test_placement_and_active_join(tmp_path: Path, scenario: str):
    async with rigs.open_rig("full", tmp_path) as rig:
        hello = await enable(rig)
        if scenario == "prefix_reordered":
            await rig.harness.session.append_message(
                AssistantMessage(content=[TextContent(text="prior answer")], stop_reason="stop")
            )

        def reduce(event, _):
            if isinstance(event.message, UserMessage):
                return MessageEndEventResult(
                    message=UserMessage(content=[TextContent(text="admitted")])
                )
            return None

        def context(event, _):
            messages = list(event.messages)
            if scenario == "body_changed":
                messages[-1] = UserMessage(content=[TextContent(text="request only")])
            if scenario == "reordered":
                messages.insert(0, messages.pop())
            if scenario == "prefix_reordered":
                messages[0], messages[1] = messages[1], messages[0]
            return ContextResult(messages=messages)

        rig.harness.hooks.on("message_end", reduce)
        if scenario != "repeat":
            rig.harness.hooks.on("context", context)
        await prompt(rig, "direct", "same", streamingBehavior="steer")
        if scenario == "repeat":
            await prompt(rig, "repeated", "same")
        consumed = observed(rig, "consumed")
        settled = observed(rig, "input_settled")
        count = 2 if scenario == "repeat" else 1
        assert len(consumed) == len(settled) == count
        assert len({row["inputId"] for row in consumed}) == count
        assert consumed[0]["requestId"] == "direct"
        assert consumed[0]["route"] == "prompt"
        assert consumed[0]["bindingId"] == hello["bindingId"]
        if scenario in ("repeat", "pass_through"):
            assert all(row["message"]["content"][0]["text"] == "admitted" for row in consumed)
        else:
            assert consumed[0]["reason"] == "contextMismatch"
            assert "message" not in consumed[0]
        assistant = next(
            row
            for row in rig.wire.records
            if row["type"] == "message_start" and row["message"]["role"] == "assistant"
        )
        assert rig.wire.records.index(consumed[0]) < rig.wire.records.index(assistant)
        assert all(row["outcome"] == "committed" for row in settled)
        page = await rigs.request(rig.wire, "page", "transcript/list", {})
        for row in settled:
            entry = next(e for e in page["data"]["entries"] if e["entryId"] == row["entryId"])
            assert entry["sequence"] == row["sequence"]
            assert entry["message"]["content"][0]["text"] == "admitted"


async def test_disabled_extension_preserves_embedding_calls():
    calls = []

    class Host(AgentHarness):
        async def steer(self, text, *, images=None, final=False):
            calls.append(("steer", text))
            await super().steer(text, images=images, final=final)

        async def follow_up(self, text, *, images=None):
            calls.append(("follow_up", text))
            await super().follow_up(text, images=images)

        async def prompt(
            self, text, *, images=None, source="interactive", on_accept=None, _refused_by=None
        ):
            calls.append(("prompt", text))
            return []

    host = Host(AgentHarnessOptions(model=Model(id="fixture", api="anthropic")))
    host._action_send_user_message("one", deliver_as="steer")
    host._action_send_user_message("two", deliver_as="follow_up")
    host._action_send_message("three", trigger_turn=True)
    await asyncio.gather(*host._pending_tasks)
    assert calls == [("steer", "one"), ("follow_up", "two"), ("prompt", "three")]
    assert len(host._steering_queue._messages) == len(host._follow_up_queue._messages) == 1
    assert tuple(inspect.signature(TranscriptOutcome).parameters) == (
        "status",
        "stored",
        "replaced",
    )
    for name in ("steer", "follow_up", "prompt"):
        assert inspect.signature(getattr(AgentHarness, name)).parameters.keys() == (
            inspect.signature(getattr(Host, name)).parameters.keys()
        )


@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
async def test_observation_preserves_public_host_input_dispatch(tmp_path: Path, monkeypatch, enabled):
    calls = []

    class Host(AgentHarness):
        async def steer(self, text, *, images=None, final=False):
            calls.append(("steer", text))
            await super().steer(f"host: {text}", images=images, final=final)
            if text == "wire-steer":
                await super().steer("host's separate input")

        async def follow_up(self, text, *, images=None):
            calls.append(("followUp", text))
            await super().follow_up(f"host: {text}", images=images)

        async def prompt(
            self, text, *, images=None, source="interactive", on_accept=None, _refused_by=None
        ):
            calls.append(("prompt", text))
            return await super().prompt(
                f"host: {text}", images=images, source=source, on_accept=on_accept,
                _refused_by=_refused_by,
            )

    monkeypatch.setattr(core, "AgentHarness", Host)
    async with rigs.open_rig("full", tmp_path) as rig:
        if enabled:
            await enable(rig)
        for command in ("steer", "follow_up"):
            text = f"wire-{command}"
            rig.wire.send({"type": command, "id": text, "message": text})
            assert (await rig.wire.response(text))["success"]
            rig.harness._action_send_user_message(f"extension-{command}", deliver_as=command)
            await asyncio.gather(*rig.harness._pending_tasks)

        def texts(queue):
            return [message.content[0].text for message in queue._messages]

        assert texts(rig.harness._steering_queue) == [
            "host: wire-steer", "host's separate input", "host: extension-steer",
        ]
        assert texts(rig.harness._follow_up_queue) == [
            "host: wire-follow_up", "host: extension-follow_up",
        ]
        await prompt(rig, "wire-prompt", "wire-prompt")
        rig.harness._action_send_message("extension-prompt", trigger_turn=True)
        await asyncio.gather(*rig.harness._pending_tasks)
        assert calls == [
            ("steer", "wire-steer"), ("steer", "extension-steer"),
            ("followUp", "wire-follow_up"), ("followUp", "extension-follow_up"),
            ("prompt", "wire-prompt"), ("prompt", "extension-prompt"),
        ]
        assert not observed(rig, "queued")
        assert not observed(rig, "consumed")
        assert [r["reason"] for r in observed(rig, "stream_gap")] == (
            ["unverifiable"] if enabled else []
        )



@pytest.mark.parametrize("missing", [True, False], ids=["legacy", "rejecting"])
async def test_rejected_enable_leaves_classic_input_unobserved(tmp_path: Path, monkeypatch, missing):
    make_runtime = runtime_module.create_agent_session_runtime
    reject = True

    class HostRuntime:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        async def set_transcript_observer(self, install, retire):
            await self.wrapped.set_transcript_observer(install, retire)

        def __getattr__(self, name):
            if name == "_set_input_observation_fence":
                if missing:
                    raise AttributeError(name)
                return self._set_fence
            return getattr(self.wrapped, name)

        def _set_fence(self, fence):
            self.wrapped._set_input_observation_fence(fence)
            if fence is not None and reject:
                fence()
                raise ValueError("host rejected fence")

    async def factory(*args, **kwargs):
        return HostRuntime(await make_runtime(*args, **kwargs))

    monkeypatch.setattr(runtime_module, "create_agent_session_runtime", factory)
    async with rigs.open_rig("full", tmp_path) as rig:
        caps = (await rigs.request(rig.wire, "caps", "capabilities", {}))["data"]["methods"]
        reply = await rigs.request(rig.wire, "rejected", "inputs/observe", {"enabled": True})
        assert not reply["success"]
        rig.wire.send({"type": "steer", "id": "classic", "message": "classic"})
        assert (await rig.wire.response("classic"))["success"]
        assert not any(r["type"] == "aelix_input" for r in rig.wire.records)
        assert rig.harness._input_observation is None
        assert rig.harness.session.get_storage()._input_replayed is None
        assert rig.runtime.wrapped._transcript_fence is None
        assert "transcript/list" in caps
        assert ("inputs/observe" in caps) is not missing
        if not missing:
            await prompt(rig, "drain", "drain")
            reject = False
            epoch = await enable(rig)
            await prompt(rig, "observed", "observed")
            (started,) = observed(rig, "prompt_started")
            ack = next(r for r in rig.wire.records if r.get("id") == "enable")
            assert rig.wire.records.index(ack) < rig.wire.records.index(started)
            assert started["seq"] == 1 and started["subscriptionId"] == epoch["subscriptionId"]


async def test_transcript_service_accepts_two_argument_runtime_installer():
    calls = []

    class LegacyRuntime:
        async def set_transcript_observer(self, install, retire):
            calls.append((install, retire))

    runtime = LegacyRuntime()
    service = TranscriptService(runtime, lambda *_: None)
    await service.start()
    assert calls == [(service.install, service.unbind)]
    assert inspect.signature(AgentSessionRuntime.set_transcript_observer).parameters.keys() == (
        inspect.signature(LegacyRuntime.set_transcript_observer).parameters.keys()
    )


async def test_direct_runtime_fences_at_transition_entry(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        seen = []

        def veto(*_):
            seen.extend(row["reason"] for row in observed(rig, "stream_gap"))
            return SessionBeforeSwitchResult(cancel=True)

        rig.harness.hooks.on("session_before_switch", veto)
        assert (await rig.runtime.new_session()).cancelled
        assert seen == ["bindingChanged"]


async def test_discovery_repeat_disable_and_rebind(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        capabilities = await rigs.request(rig.wire, "capabilities", "capabilities", {})
        assert "inputs/observe" in capabilities["data"]["methods"]
        await prompt(rig, "opt-out", "classic")
        assert not observed(rig, "prompt_started")
        epoch = await enable(rig)
        assert await enable(rig, "repeat-enable") == epoch
        await prompt(rig, "opt-in", "observed")
        assert observed(rig, "prompt_started")[0]["seq"] == 1
        rig.wire.send({"type": "new_session", "id": "swap"})
        assert (await rig.wire.response("swap"))["success"]
        (gap,) = observed(rig, "stream_gap")
        assert gap["reason"] == "bindingChanged"
        assert gap["subscriptionId"] == epoch["subscriptionId"]
        fresh = await enable(rig, "fresh-enable")
        assert fresh["bindingId"] != epoch["bindingId"]
        assert fresh["subscriptionId"] != epoch["subscriptionId"]
        disabled = await rigs.request(rig.wire, "disable", "inputs/observe", {"enabled": False})
        assert disabled["data"] == {"enabled": False, "bindingId": None, "subscriptionId": None}


@pytest.mark.parametrize("route", ["steer", "followUp"])
@pytest.mark.parametrize("queue_fault", [False, True])
async def test_busy_route_is_observed_at_the_actual_enqueue(tmp_path: Path, route, queue_fault):
    async with rigs.open_rig("turn", tmp_path) as rig:
        # The turn rig begins held, so end its baseline turn before enabling.
        rig.gate.set()
        await rig.harness.wait_for_idle()
        await enable(rig)
        rig.gate.clear()
        wire = rig.wire
        wire.send({"type": "prompt", "id": "first", "message": "initial"})
        assert (await wire.response("first"))["success"]
        await _first_placement(rig)

        async def fail_queue_update(_event, _ctx):
            if queue_fault:
                raise RuntimeError("fixture hook refused")

        rig.harness.hooks.on("queue_update", fail_queue_update)
        wire.send({"type": "prompt", "id": "busy", "message": "queued", "streamingBehavior": route})
        reply = await wire.response("busy")
        assert reply["success"] is not queue_fault
        (queued,) = observed(rig, "queued")
        assert queued["route"] == route and queued["requestId"] == "busy"
        assert rig.wire.records.index(queued) < rig.wire.records.index(reply)
        assert [f.get("requestId") for f in observed(rig, "consumed")] == ["first"]
        rig.gate.set()
        await rig.harness.wait_for_idle()
        direct, pending = observed(rig, "consumed")
        (marker,) = observed(rig, "prompt_settled")
        assert direct["turnIndex"] == 1 and pending["turnIndex"] == 2
        assert pending["inputId"] == queued["inputId"]
        assert pending["consumedPromptId"] == queued["queuedPromptId"] == marker["promptId"]


async def _first_placement(rig):
    async with asyncio.timeout(4):
        return await rig.wire.record(lambda row: row.get("event") == "consumed")


@pytest.mark.parametrize("mode", ["prefix", "extension-steer", "transform-plus-send"])
async def test_source_collision_keeps_occurrence_origins(tmp_path: Path, mode):
    from aelix_agent_core.harness.hooks import BeforeAgentStartResult, InputTransform

    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        shared = UserMessage(content=[TextContent(text="same")])

        async def prefix(_event, _ctx):
            if mode == "prefix":
                return BeforeAgentStartResult(messages=[shared, shared])
            if mode == "extension-steer":
                rig.harness._action_send_user_message("same", deliver_as="steer")
                await rig.wire.record(lambda row: row.get("event") == "queued")
            return None

        async def transform(_event, _ctx):
            if mode == "transform-plus-send":
                rig.harness._action_send_user_message("same")
                return InputTransform(text="same")
            return None

        rig.harness.hooks.on("input", transform)
        rig.harness.hooks.on("before_agent_start", prefix)
        await prompt(rig, "composer", "same" if mode != "transform-plus-send" else "original")
        consumed = observed(rig, "consumed")
        expected = {
            "prefix": [("extension", "prefix"), ("extension", "prefix"), ("rpc", "prompt")],
            "extension-steer": [("rpc", "prompt"), ("extension", "steer")],
            "transform-plus-send": [("extension", "nextTurn"), ("rpc", "prompt")],
        }
        assert [(row["source"], row["route"]) for row in consumed] == expected[mode]
        assert len({row["inputId"] for row in consumed}) == len(consumed)
        assert all(row["placement"] == "available" for row in consumed)
        assert [row.get("requestId") for row in consumed].count("composer") == 1
        assert all(row["message"]["content"][0]["text"] == "same" for row in consumed)


@pytest.mark.parametrize("reason", ["before-drain", "after-drain", "plain"])
async def test_abort_decides_queued_and_admitted_occurrences(tmp_path: Path, reason):
    from aelix_ai.messages import AssistantMessage
    from aelix_ai.streaming import AssistantDoneEvent

    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        entered = asyncio.Queue()
        release = asyncio.Event()

        async def stream(*_):
            entered.put_nowait(None)
            await release.wait()
            release.clear()
            yield AssistantDoneEvent(reason="stop", message=AssistantMessage(stop_reason="stop"))

        rig.harness._options.stream_fn = stream
        rig.wire.send({"type": "prompt", "id": "direct", "message": "direct"})
        assert (await rig.wire.response("direct"))["success"]
        await asyncio.wait_for(entered.get(), 4)
        rig.wire.send({"type": "steer", "id": "steer", "message": "queued"})
        assert (await rig.wire.response("steer"))["success"]
        if reason == "after-drain":
            release.set()
            await asyncio.wait_for(entered.get(), 4)
        if reason == "plain":
            await rig.harness.abort()
        else:
            rig.wire.send({"type": "abort", "id": "abort"})
            assert (await rig.wire.response("abort"))["success"]
        await rig.harness.wait_for_idle()
        (queued,) = observed(rig, "queued")
        consumed = {row["inputId"]: row for row in observed(rig, "consumed")}
        terminals = {row["inputId"]: row for row in observed(rig, "input_settled")}
        assert (queued["inputId"] in consumed) is (reason == "after-drain")
        assert terminals[queued["inputId"]]["outcome"] == "withdrawn"
        original = next(row for row in consumed.values() if row["requestId"] == "direct")
        assert terminals[original["inputId"]]["outcome"] == (
            "withdrawn" if reason == "before-drain" else "committed"
        )
        assert observed(rig, "prompt_settled")[0]["outcome"] == "aborted"


@pytest.mark.parametrize("kind", ["disabled-busy", "enabled-busy", "occupied", "malformed"])
async def test_configuration_preserves_stable_epoch_and_bounded_errors(tmp_path: Path, kind):
    async with rigs.open_rig("full", tmp_path) as rig:
        wire = rig.wire
        caps = await rigs.request(wire, "caps", "capabilities", {})
        assert "inputs/observe" in caps["data"]["methods"]
        enabled = kind == "enabled-busy"
        epoch = await enable(rig) if enabled else None
        if kind.endswith("busy"):
            rig.gate.clear()
            wire.send({"type": "prompt", "id": "busy", "message": "hold"})
            assert (await wire.response("busy"))["success"]
            if enabled:
                assert await enable(rig, "repeated-busy") == epoch
            reply = await rigs.request(wire, "change", "inputs/observe", {"enabled": not enabled})
            rig.gate.set()
            await rig.harness.wait_for_idle()
            assert reply["error"] == "Input observation requires an idle binding with empty queues"
        elif kind == "occupied":
            await rig.harness.next_turn("queued while off")
            reply = await rigs.request(wire, "change", "inputs/observe", {"enabled": True})
            assert reply["error"] == "Input observation requires an idle binding with empty queues"
            assert not observed(rig, "queued")
        else:
            for bad_id in ("", "a" * 1023, chr(0xD800), 42):
                start = len(wire.records)
                wire.send(
                    {
                        "type": "aelix_control",
                        "id": bad_id,
                        "method": "inputs/observe",
                        "params": {"enabled": True},
                    }
                )
                row = await wire.record(
                    lambda r, start=start: (
                        r.get("error") == "Invalid input observation request ID"
                        and any(r is other for other in wire.records[start:])
                    )
                )
                assert "id" not in row
            for index, params in enumerate(
                (None, [], {}, {"enabled": 1}, {"enabled": True, "x": 0})
            ):
                reply = await rigs.request(wire, f"bad-{index}", "inputs/observe", params)
                assert reply["error"] == "Invalid input observation params"
            await enable(rig)
            classic_id = "classic" * 150
            await prompt(rig, classic_id, "legacy overbounded correlation is allowed")
            (row,) = observed(rig, "consumed")
            assert row["source"] == "rpc" and "requestId" not in row


async def test_record_budget_gap_keeps_classic_queue_delivery(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        for index in range(129):
            await rig.harness.next_turn(f"line {index}")
        (gap,) = observed(rig, "stream_gap")
        assert gap["seq"] == 129 and gap["reason"] == "budgetExceeded"
        assert len(observed(rig, "queued")) == 128
        assert len(rig.harness._next_turn_queue) == 129
        await prompt(rig, "after-gap", "keep going")
        assert not observed(rig, "prompt_started")
        fresh = await enable(rig, "fresh")
        assert fresh["subscriptionId"] != gap["subscriptionId"]


async def test_budget_accounts_for_container_growth_at_enqueue(tmp_path: Path, monkeypatch):
    from aelix_agent_core.harness import _input_observation

    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        harness = rig.harness
        for _ in range(4):
            await harness.steer("seed")
        ledger = harness._input_observation
        charge = getattr(ledger, "_retained_bytes", ledger._bytes)
        limit = charge + _input_observation._RECORD_BYTES
        monkeypatch.setattr(_input_observation, "RETAINED_BYTES", limit)

        await harness.steer("grows the ledger")
        assert [row["reason"] for row in observed(rig, "stream_gap")] == ["budgetExceeded"]
        assert len(observed(rig, "queued")) == 4
        assert len(harness._steering_queue._messages) == 5


async def test_enable_acknowledgement_precedes_first_pipelined_claim(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        wire = rig.wire
        wire.send(
            {
                "type": "aelix_control",
                "id": "enable",
                "method": "inputs/observe",
                "params": {"enabled": True},
            },
            {"type": "prompt", "id": "prompt", "message": "sent with enable"},
        )
        ack = await wire.response("enable")
        assert ack["success"], ack
        assert (await wire.response("prompt"))["success"]
        await rig.harness.wait_for_idle()
        (claim,) = observed(rig, "prompt_started")
        (marker,) = observed(rig, "prompt_settled")
        assert claim["seq"] == 1 and claim["subscriptionId"] == ack["data"]["subscriptionId"]
        assert wire.records.index(ack) < wire.records.index(claim) < wire.records.index(marker)
