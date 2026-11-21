"""Input attempts follow native mutation and release boundaries."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from aelix_agent_core.harness._input_observation import _RECORD_BYTES
from aelix_agent_core.harness.hooks import (
    BeforeAgentStartResult,
    InputHandled,
    MessageEndEventResult,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import AssistantDoneEvent, AssistantStartEvent
from aelix_coding_agent.rpc.rpc_mode import _call_rpc_input

from tests.rpc.control_v1 import rigs
from tests.rpc.test_input_observation_865 import enable, observed, prompt

pytestmark = pytest.mark.usefixtures("rig_env")
rig_env = rigs.rig_env


@pytest.mark.parametrize(
    "fault", ["next-turn-detach", "direct-context-failure", "direct-context-cancel"]
)
async def test_a_native_reinsert_keeps_origin_and_advances_attempt(tmp_path: Path, fault):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        armed = True
        entered = asyncio.Event()
        session = rig.harness.session
        original = session.build_context

        async def context(*args, **kwargs):
            nonlocal armed
            if armed and fault.startswith("direct"):
                armed = False
                if fault.endswith("cancel"):
                    entered.set()
                    await asyncio.Future()
                raise OSError("fixture context fault")
            return await original(*args, **kwargs)

        async def queue(_event, _ctx):
            nonlocal armed
            if armed and not rig.harness._next_turn_queue:
                armed = False
                raise RuntimeError("fixture detach fault")

        session.build_context = context
        if fault == "next-turn-detach":
            rig.harness.hooks.on("queue_update", queue)
            await rig.harness.next_turn("restored")
        if fault.endswith("cancel"):
            task = asyncio.create_task(
                _call_rpc_input(
                    rig.harness, "prompt", "failed", rig.harness.prompt, "restored", source="rpc"
                )
            )
            await asyncio.wait_for(entered.wait(), 4)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await prompt(rig, "failed", "restored")
        failed = observed(rig, "prompt_started")[0]
        assert observed(rig, "consumed") == []
        old = observed(rig, "queued")[0] if fault == "next-turn-detach" else failed["input"]
        restored = observed(rig, "queued")[-1]
        assert restored["queuedPromptId"] == failed["promptId"]
        assert (restored["inputId"], restored["attempt"]) == (old["inputId"], 2)
        assert restored["source"] == old["source"] and restored["route"] == old["route"]
        assert restored.get("requestId") == old.get("requestId")
        assert any(
            row["inputId"] == old["inputId"]
            and row["attempt"] == 1
            and row["outcome"] == "notAdmitted"
            for row in observed(rig, "input_settled")
        )

        await prompt(rig, "second", "second prompt")
        admitted = observed(rig, "consumed")
        assert [(row["attempt"], row["message"]["content"][0]["text"]) for row in admitted] == [
            (2, "restored"),
            (1, "second prompt"),
        ]
        assert admitted[0]["inputId"] == restored["inputId"]
        assert admitted[1]["inputId"] != restored["inputId"]
        active = await rigs.request(rig.wire, "active", "transcript/list", {})
        users = [r for r in active["data"]["entries"] if r["message"]["role"] == "user"]
        assert [r["message"]["content"][0]["text"] for r in users][-2:] == [
            "restored",
            "second prompt",
        ]
        assert users[-1]["parentEntryId"] == users[-2]["entryId"]


@pytest.mark.parametrize("hook", ["input", "before_agent_start"])
async def test_held_ordinary_claim_settles_only_after_the_hook_returns(tmp_path: Path, hook):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        entered = asyncio.Event()
        continuing = asyncio.Event()

        async def hold(_event, _ctx):
            entered.set()
            await continuing.wait()

        rig.harness.hooks.on(hook, hold)
        rig.wire.send({"type": "prompt", "id": "held", "message": "parked"})
        await asyncio.wait_for(entered.wait(), 4)
        rig.wire.send({"type": "steer", "id": "steer", "message": "parked too"})
        assert (await rig.wire.response("steer"))["success"]
        rig.wire.send({"type": "abort", "id": "abort"})
        assert (await rig.wire.response("abort"))["success"]
        assert rig.harness.phase == "turn"
        assert not observed(rig, "prompt_settled")
        continuing.set()
        assert (await rig.wire.response("held"))["success"]
        await rig.harness.wait_for_idle()
        (placement,) = observed(rig, "consumed")
        assert placement["reason"] == "noModelRequest"
        (marker,) = observed(rig, "prompt_settled")
        assert marker["outcome"] == "aborted"
        assert all(row["outcome"] == "withdrawn" for row in observed(rig, "input_settled"))


async def test_unadmitted_prefix_does_not_emit_an_unknown_terminal(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        entered = asyncio.Event()
        prefix = UserMessage(content=[TextContent(text="extension prefix")])

        async def park(event, _ctx):
            if event.message is prefix:
                entered.set()
                await asyncio.Future()

        rig.harness.hooks.on(
            "before_agent_start", lambda *_: BeforeAgentStartResult(messages=[prefix])
        )
        rig.harness.hooks.on("message_start", park)
        rig.wire.send({"type": "prompt", "id": "composer", "message": "composer"})
        await asyncio.wait_for(entered.wait(), 4)
        rig.wire.send({"type": "abort", "id": "abort"})
        assert (await rig.wire.response("abort"))["success"]
        await rig.harness.wait_for_idle()

        known = set()
        for row in rig.wire.records:
            if row["type"] != "aelix_input":
                continue
            if row["event"] == "prompt_started":
                known.add((row["input"]["inputId"], row["input"]["attempt"]))
            elif row["event"] in ("queued", "consumed"):
                known.add((row["inputId"], row["attempt"]))
            elif row["event"] == "input_settled":
                assert (row["inputId"], row["attempt"]) in known
        assert not observed(rig, "consumed")
        assert [row["outcome"] for row in observed(rig, "input_settled")] == ["withdrawn"]
        assert [row["outcome"] for row in observed(rig, "prompt_settled")] == ["aborted"]


@pytest.mark.parametrize(
    "kind", ["handled", "refused", "synthetic", "failed-context", "notice-empty"]
)
async def test_release_uses_native_claim_disposition(tmp_path: Path, kind):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        harness = rig.harness
        if kind == "handled":
            harness.hooks.on("input", lambda *_: InputHandled())
            await prompt(rig, "handled", "extension handles this")
        elif kind == "refused":
            harness.set_prompt_check(lambda _: "fixture refused preflight")
            rig.wire.send({"type": "prompt", "id": "refused", "message": "refused"})
            assert not (await rig.wire.response("refused"))["success"]
            await harness.wait_for_idle()
        elif kind == "synthetic":

            async def fail(*_):
                raise RuntimeError("fixture synthetic assistant")
                yield  # Make the failing provider an async generator.

            harness._options.stream_fn = fail
            await prompt(rig, "synthetic", "committed before provider failure")
        elif kind == "failed-context":

            def fail_context(*_):
                raise RuntimeError("fixture fails before building a request")

            harness.hooks.on("context", fail_context)
            await prompt(rig, "pre-request", "committed before context failure")
        else:
            result = await harness.prompt_notices()
            assert not result.started
        (marker,) = observed(rig, "prompt_settled")
        assert (
            marker["outcome"]
            == {
                "handled": "handled",
                "refused": "failed",
                "synthetic": "failed",
                "failed-context": "failed",
                "notice-empty": "noWork",
            }[kind]
        )
        if kind in {"synthetic", "failed-context"}:
            (consumed,) = observed(rig, "consumed")
            if kind == "failed-context":
                assert consumed["reason"] == "noModelRequest"
            else:
                assert consumed["placement"] == "available"
            assistant = next(
                row
                for row in rig.wire.records
                if row["type"] == "message_start" and row["message"]["role"] == "assistant"
            )
            assert rig.wire.records.index(consumed) < rig.wire.records.index(assistant)
            assert observed(rig, "input_settled")[0]["outcome"] == "committed"
        elif kind != "notice-empty":
            assert observed(rig, "input_settled")[0]["outcome"] == "notAdmitted"
            assert not observed(rig, "consumed")
        else:
            assert observed(rig, "prompt_started")[0]["promptKind"] == "notice"


@pytest.mark.parametrize(
    "fault",
    [
        "write-refused",
        "post-append-cancel",
        "body-unencodable",
        "body-surrogate",
        "replay-proven",
        "replay-unproven",
    ],
)
async def test_settlement_uses_the_active_r9_occurrence(tmp_path: Path, fault):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        session = rig.harness.session
        storage = session.get_storage()
        original = session.append_message_receipt
        root_id = await session.get_leaf_id()
        old_id = None

        async def append(message):
            nonlocal old_id
            if message.role == "user" and fault == "write-refused":
                raise OSError("fixture definite refusal")
            receipt = await original(message)
            if message.role == "user":
                old_id = receipt.entry_id
                if fault == "post-append-cancel":
                    raise asyncio.CancelledError
            return receipt

        async def replay(_event, _ctx):
            if fault == "replay-proven":
                await session.withdraw_tail(root_id, lambda _: False)
            elif fault == "replay-unproven":
                await storage.set_leaf_id(root_id)
                await session.append_message(UserMessage(content=[TextContent(text="written")]))

        def reducer(event, _):
            if not isinstance(event.message, UserMessage):
                return None
            if fault == "body-unencodable":
                return MessageEndEventResult(
                    message=UserMessage(
                        content=[TextContent(text="written")], timestamp=float("nan")
                    )
                )
            if fault == "body-surrogate":
                return MessageEndEventResult(
                    message=UserMessage(content=[TextContent(text=chr(0xD800))])
                )
            return None

        session.append_message_receipt = append
        rig.harness.hooks.on("settled", replay)
        rig.harness.hooks.on("message_end", reducer)
        await prompt(rig, "written", "written")
        (row,) = observed(rig, "input_settled")
        (consumed,) = observed(rig, "consumed")
        assert (
            row["outcome"]
            == {
                "write-refused": "notStored",
                "post-append-cancel": "unknown",
                "body-unencodable": "storageOnly",
                "body-surrogate": "storageOnly",
                "replay-proven": "committed",
                "replay-unproven": "unknown",
            }[fault]
        )
        active = await rigs.request(rig.wire, "active", "transcript/list", {})
        assert active["success"]
        entries = {entry["entryId"]: entry for entry in active["data"]["entries"]}
        if fault == "replay-proven":
            assert old_id not in entries and row["entryId"] in entries
            assert entries[row["entryId"]]["message"] == consumed["message"]
        if fault in {"body-unencodable", "body-surrogate"}:
            assert consumed["reason"] == "bodyUnencodable"
            assert "message" not in consumed
            assert observed(rig, "prompt_settled")[0]["outcome"] == "complete"
        if fault == "post-append-cancel":
            assert old_id in entries


async def test_shared_reducer_reference_does_not_merge_withdrawals(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        shared = UserMessage(content=[TextContent(text="same shared body")])
        gate = asyncio.Queue()

        def reducer(event, _):
            return (
                MessageEndEventResult(message=shared)
                if isinstance(event.message, UserMessage)
                else None
            )

        async def stream(*_):
            release = asyncio.Event()
            gate.put_nowait(release)
            await release.wait()
            yield AssistantDoneEvent(reason="stop", message=AssistantMessage(stop_reason="stop"))

        rig.harness._options.stream_fn = stream
        rig.harness.hooks.on("message_end", reducer)
        rig.wire.send({"type": "prompt", "id": "first", "message": "first"})
        first_gate = await asyncio.wait_for(gate.get(), 4)
        rig.wire.send({"type": "steer", "id": "steer", "message": "steer"})
        assert (await rig.wire.response("steer"))["success"]
        first_gate.set()
        await asyncio.wait_for(gate.get(), 4)
        rig.wire.send({"type": "abort", "id": "stop"})
        assert (await rig.wire.response("stop"))["success"]
        await rig.harness.wait_for_idle()
        a, b = observed(rig, "consumed")
        assert a["inputId"] != b["inputId"] and a["message"] == b["message"]
        assert [(row["inputId"], row["outcome"]) for row in observed(rig, "input_settled")] == [
            (a["inputId"], "committed"),
            (b["inputId"], "withdrawn"),
        ]


@pytest.mark.parametrize("over", [0, 1])
async def test_exact_final_lf_byte_budget(tmp_path: Path, over):
    line_bytes = 1_048_576

    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        first_payload = '한\x00"'
        next_payload = ""
        assistant = AssistantMessage(stop_reason="stop")

        async def stream(*_):
            yield AssistantStartEvent(partial=assistant)
            yield AssistantDoneEvent(reason="stop", message=assistant)

        def reducer(event, _):
            if isinstance(event.message, UserMessage):
                return MessageEndEventResult(
                    message=UserMessage(content=[TextContent(text=next_payload or first_payload)])
                )
            return None

        rig.harness._options.stream_fn = stream
        rig.harness.hooks.on("message_end", reducer)
        await prompt(rig, "size-a", "short original")
        (calibration,) = observed(rig, "consumed")
        # Both claims have fixed-size UUIDs, the same request id length, and
        # one-digit event sequence and per-claim ordinal.
        compact = json.dumps(calibration, ensure_ascii=False, separators=(",", ":")).encode()
        next_payload = first_payload + ("x" * (line_bytes - len(compact) - 1 + over))
        await prompt(rig, "size-b", "short original")
        actual = observed(rig, "consumed")[-1]
        if over:
            assert actual["reason"] == "bodyTooLarge"
            assert "message" not in actual
        else:
            encoded = json.dumps(actual, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
            assert len(encoded) == line_bytes
            assert actual["message"]["content"][0]["text"] == next_payload
        assert len(observed(rig, "prompt_settled")) == 2


async def test_aggregate_body_budget_expires_before_assistant_output(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        body = UserMessage(content=[TextContent(text="b" * 900_000)])
        rig.harness.hooks.on(
            "before_agent_start", lambda *_: BeforeAgentStartResult(messages=[body] * 5)
        )
        await prompt(rig, "due", "the native prompt still runs")
        (gap,) = observed(rig, "stream_gap")
        assert gap["reason"] == "budgetExceeded"
        assistant = next(
            row for row in rig.wire.records if row.get("message", {}).get("role") == "assistant"
        )
        assert rig.wire.records.index(gap) < rig.wire.records.index(assistant)
        assert not observed(rig, "consumed")
        assert not observed(rig, "prompt_settled")


async def test_retained_body_budget_at_storage_admission(tmp_path: Path, monkeypatch):
    limit = 4 * 1_048_576

    async def measure(size):
        async with rigs.open_rig("full", tmp_path / str(size)) as rig:
            await enable(rig)
            ledger = rig.harness._input_observation
            body = UserMessage(content=[TextContent(text="b" * size)])
            rig.harness.hooks.on(
                "before_agent_start", lambda *_: BeforeAgentStartResult(messages=[body] * 4)
            )

            def reduce(event, _ctx):
                if isinstance(event.message, UserMessage):
                    return MessageEndEventResult(message=body)
                return None

            session = rig.harness.session
            append = session.append_message_receipt
            samples = []

            async def sample_write(message, *args, **kwargs):
                if isinstance(message, UserMessage):
                    buffers = [r.body.data for r in ledger._pending if r.body and r.body.data]
                    backing_bytes = sum(map(sys.getsizeof, buffers))
                    charged_bytes = ledger._bytes - len(ledger._records) * _RECORD_BYTES
                    samples.append((ledger.active, len(buffers), backing_bytes, charged_bytes))
                return await append(message, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(session, "append_message_receipt", sample_write)
                rig.harness.hooks.on("message_end", reduce)
                await prompt(rig, "bounded", "fifth")

            assert [r["reason"] for r in observed(rig, "stream_gap")] == ["budgetExceeded"]
            return samples

    # Check backing capacity on four near-limit bodies and catch an over-budget
    # fifth snapshot before the context hook can gap it.
    for size in (1_040_000, 900_000):
        samples = await measure(size)
        assert len(samples) == 5
        assert max(sample[2] for sample in samples) <= limit, samples
        assert samples[3][:2] == (True, 4)
        assert samples[4] == (False, 0, 0, 0)
        assert all(backing == charged for _, _, backing, charged in samples), samples


@pytest.mark.parametrize("overlap", [False, True])
async def test_retained_budget_includes_backing_storage_and_live_witness(
    tmp_path: Path, overlap: bool
):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        harness = rig.harness
        ledger = harness._input_observation
        prefix = UserMessage(content=[TextContent(text="prefix")])
        size = 1_039_370 if overlap else 1_040_000
        entered, continuing = asyncio.Event(), asyncio.Event()
        measured = []

        def reduce(event, _ctx):
            if isinstance(event.message, UserMessage):
                return MessageEndEventResult(
                    message=UserMessage(content=[TextContent(text="a" * size)])
                )
            return None

        async def hold(*_):
            if entered.is_set():
                return
            buffers = [record.body.data for record in ledger._pending]
            measured.append(sum(sys.getsizeof(data) for data in buffers))
            assert ledger._witness is not None
            entered.set()
            await continuing.wait()

        harness.hooks.on(
            "before_agent_start", lambda *_: BeforeAgentStartResult(messages=[prefix] * 3)
        )
        harness.hooks.on("message_end", reduce)
        harness.hooks.on("context", hold)
        rig.wire.send({"type": "prompt", "id": "bounded", "message": "composer"})
        try:
            await asyncio.wait_for(entered.wait(), 4)
            if overlap:
                for _ in range(4):
                    await harness.steer("queued")
                assert [row["reason"] for row in observed(rig, "stream_gap")] == ["budgetExceeded"]
                assert len(observed(rig, "queued")) == 3
            else:
                assert measured[0] < ledger._bytes <= 4 * 1_048_576
        finally:
            continuing.set()
        assert (await rig.wire.response("bounded"))["success"]
        await harness.wait_for_idle()
        if not overlap:
            assert len(observed(rig, "consumed")) == 4


async def test_notice_abort_in_preflight_tracks_the_run_that_actually_happens(tmp_path: Path):
    from aelix_agent_core.harness.inbox import Notice

    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        parked = asyncio.Event()
        release = asyncio.Event()
        requests = []

        async def hook(_event, _ctx):
            parked.set()
            await release.wait()
            return BeforeAgentStartResult(
                messages=[UserMessage(content=[TextContent(text="prefix")])]
            )

        async def stream(_model, context, _options):
            requests.append(context.messages)
            yield AssistantDoneEvent(reason="stop", message=AssistantMessage(stop_reason="stop"))

        harness = rig.harness
        harness._options.stream_fn = stream
        harness.hooks.on("before_agent_start", hook)
        assert harness.post_notice(Notice("fixture-id", "fixture", "notice body"))
        task = asyncio.create_task(harness.prompt_notices())
        try:
            await asyncio.wait_for(parked.wait(), 4)
            await harness.abort(withdraw_unanswered=True)
            assert not observed(rig, "prompt_settled")
        finally:
            release.set()
        assert (await task).started
        assert len(requests) == 1
        assert observed(rig, "prompt_started")[0]["promptKind"] == "notice"
        (consumed,) = observed(rig, "consumed")
        assert consumed["route"] == "prefix"
        assert consumed["message"]["content"][0]["text"] == "prefix"
        assert observed(rig, "prompt_settled")[0]["outcome"] == "complete"


async def test_partial_assistant_abort_retires_unprovable_cut(tmp_path: Path):
    from aelix_ai.streaming import TextDeltaEvent

    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)

        async def stream(*_):
            partial = AssistantMessage(content=[TextContent(text="part")])
            yield AssistantStartEvent(partial=partial)
            yield TextDeltaEvent(delta="part", partial=partial)
            await asyncio.Future()

        rig.harness._options.stream_fn = stream
        rig.wire.send({"type": "prompt", "id": "partial", "message": "about to be withdrawn"})
        delta = await asyncio.wait_for(
            rig.wire.record(lambda row: row["type"] == "message_update"), 4
        )
        rig.wire.send({"type": "abort", "id": "abort"})
        assert (await rig.wire.response("abort"))["success"]
        await rig.harness.wait_for_idle()
        (row,) = observed(rig, "consumed")
        (gap,) = observed(rig, "stream_gap")
        start = next(
            entry
            for entry in rig.wire.records
            if entry["type"] == "message_start" and entry["message"]["role"] == "assistant"
        )
        assert not any(
            frame["type"] == "message_end" and frame["message"]["role"] == "assistant"
            for frame in rig.wire.records
        )
        (turn_end,) = (frame for frame in rig.wire.records if frame["type"] == "turn_end")
        (agent_end,) = (frame for frame in rig.wire.records if frame["type"] == "agent_end")
        assert turn_end["message"]["stop_reason"] == "aborted"
        positions = [
            rig.wire.records.index(frame) for frame in (row, start, delta, turn_end, agent_end, gap)
        ]
        assert positions == sorted(positions)
        assert gap["reason"] == "unverifiable"
        assert not observed(rig, "input_settled")
        assert not observed(rig, "prompt_settled")
        await enable(rig, "fresh")


async def test_cancel_during_release_ends_epoch_before_exposing_idle(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        entered = asyncio.Event()

        async def held_settlement():
            entered.set()
            await asyncio.Future()

        harness = rig.harness
        settle = harness._settle_session_storage
        harness._settle_session_storage = held_settlement
        task = asyncio.create_task(harness.prompt("release will be interrupted"))
        try:
            await asyncio.wait_for(entered.wait(), 4)
            assert harness.phase == "turn" and not observed(rig, "prompt_settled")
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            harness._settle_session_storage = settle
            if not task.done():
                task.cancel()
                await asyncio.wait({task})
        assert harness.phase == "idle"
        (gap,) = observed(rig, "stream_gap")
        assert gap["reason"] == "unverifiable"
        assert not observed(rig, "input_settled")
        assert not observed(rig, "prompt_settled")


async def test_unproved_shared_reference_through_callback_is_unavailable(tmp_path: Path):
    from aelix_agent_core.harness.hooks import ContextResult

    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        shared = UserMessage(content=[TextContent(text="body")])
        rig.harness.hooks.on(
            "before_agent_start", lambda *_: BeforeAgentStartResult(messages=[shared, shared])
        )
        rig.harness.hooks.on(
            "context", lambda event, _: ContextResult(messages=list(event.messages))
        )
        await prompt(rig, "ambiguous", "direct")
        rows = observed(rig, "consumed")
        assert len(rows) == 3
        assert all(row["reason"] == "contextMismatch" for row in rows)
        assert all(row["outcome"] == "committed" for row in observed(rig, "input_settled"))


async def test_logical_withdrawal_wins_if_storage_rollback_refuses(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        entered = asyncio.Event()

        async def stream(*_):
            entered.set()
            await asyncio.Future()
            yield

        async def refuse_rollback(*_):
            raise OSError("fixture cannot roll back")

        rig.harness._options.stream_fn = stream
        rig.harness.session.withdraw_tail = refuse_rollback
        rig.wire.send({"type": "prompt", "id": "direct", "message": "still physically active"})
        await asyncio.wait_for(entered.wait(), 4)
        rig.wire.send({"type": "abort", "id": "abort"})
        assert (await rig.wire.response("abort"))["success"]
        await rig.harness.wait_for_idle()
        (row,) = observed(rig, "input_settled")
        assert row["outcome"] == "withdrawn" and "entryId" not in row
        active = await rigs.request(rig.wire, "active", "transcript/list", {})
        assert (
            active["data"]["entries"][-1]["message"]["content"][0]["text"]
            == "still physically active"
        )


async def test_cancelling_a_shared_prefix_restores_the_detached_queue_slot(tmp_path: Path):
    async with rigs.open_rig("full", tmp_path) as rig:
        await enable(rig)
        harness = rig.harness
        shared = UserMessage(content=[TextContent(text="shared before reduction")])
        entered = asyncio.Event()

        def prefix(*_):
            if not entered.is_set():
                return BeforeAgentStartResult(messages=[shared])
            return None

        async def hold(event, _ctx):
            if event.message is shared and not entered.is_set():
                entered.set()
                await asyncio.Future()

        harness.hooks.on("before_agent_start", prefix)
        harness.hooks.on("message_start", hold)
        harness._action_send_message(shared)
        task = asyncio.create_task(harness.prompt("also pending"))
        try:
            await asyncio.wait_for(entered.wait(), 4)
        finally:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        queued = observed(rig, "queued")
        original, restored, _ = queued
        assert restored["inputId"] == original["inputId"]
        assert restored["attempt"] == 2 and restored["route"] == "nextTurn"
        await prompt(rig, "next", "new submission")
        admitted = observed(rig, "consumed")
        assert admitted[0]["inputId"] == original["inputId"]
        assert admitted[0]["route"] == "nextTurn"
        assert admitted[0]["message"]["content"][0]["text"] == "shared before reduction"
