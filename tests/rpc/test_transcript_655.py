"""#655 replay coordinates from the CLI RPC wire."""

from pathlib import Path

import pytest
from aelix_ai.messages import TextContent, UserMessage

from tests.rpc.control_v1 import rigs

pytestmark = pytest.mark.usefixtures("rig_env")
rig_env = rigs.rig_env


async def test_transcript_repeats_equal_payloads_with_distinct_keys(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        first = rig.values["entry_id"]
        second = await rig.harness.session.append_message(
            UserMessage(content=[TextContent(text="hello")])
        )
        for request_id in ("first", "second"):
            reply = await rigs.request(rig.wire, request_id, "transcript/list", {})
            assert reply["success"], reply
            page = reply["data"]
            assert page["totalMessages"] == 2
            assert page["leafId"] == second
            assert page["nextCursor"] is None
            a, b = page["entries"]
            assert a["message"] == b["message"]
            assert (a["entryId"], a["parentEntryId"], a["sequence"]) == (first, None, 1)
            assert (b["entryId"], b["parentEntryId"], b["sequence"]) == (second, first, 2)


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


async def _page(rig, request_id, **params):
    reply = await rigs.request(rig.wire, request_id, "transcript/list", params)
    assert reply["success"], reply
    return reply["data"]


async def test_compacted_branch_cursor_pins_history_and_rebind_fences_it(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        first = rig.values["entry_id"]
        second = await session.append_message(_user("second"))
        compact = await session.append_compaction("earlier summary", second, 5)
        third = await session.append_message(_user("after compaction"))
        page = await _page(rig, "page-one", limit=1)
        assert page["leafId"] == third and page["totalMessages"] == 3
        await session.get_storage().set_leaf_id(first)
        fourth = await session.append_message(_user("different path"))
        held = await _page(rig, "page-two", cursor=page["nextCursor"])
        assert [(r["entryId"], r["parentEntryId"], r["sequence"]) for r in held["entries"]] == [
            (second, first, 2),
            (third, compact, 3),
        ]
        assert [r["entryId"] for r in (await _page(rig, "current"))["entries"]] == [first, fourth]
        assert (await _page(rig, "metadata", leafId=compact))["totalMessages"] == 2

        rig.wire.send(
            {"type": "switch_session", "id": "reopen", "sessionPath": rig.values["session_file"]}
        )
        assert (await rigs._wait(rig.wire.response("reopen"), "reopen", rig.wire))["success"]
        stale = await rigs.request(
            rig.wire, "stale", "transcript/list", {"cursor": page["nextCursor"]}
        )
        assert not stale["success"] and stale["error"] == "Stale transcript cursor"
        reopened = await _page(rig, "reopened", leafId=third)
        assert reopened["bindingId"] != page["bindingId"]
        assert reopened["nativeSessionId"] == page["nativeSessionId"]
        assert reopened["entries"][1:] == held["entries"]


async def test_selected_forward_parent_and_cycles_refuse_before_recovery(tmp_path: Path) -> None:
    import json

    timestamp = "2026-11-20T00:00:00Z"

    def record(entry_id, parent, **extra):
        return {
            "type": "custom",
            "customType": "fixture",
            "id": entry_id,
            "parentId": parent,
            "timestamp": timestamp,
            **extra,
        }

    async with rigs.open_rig("full", tmp_path) as rig:
        first = rig.values["entry_id"]
        path = Path(rig.values["session_file"])
        records = [
            record("forward", "later"),
            record("later", None),
            record("cycle-1", "cycle-2"),
            record("cycle-2", "cycle-1"),
            record("self", "self"),
            record("marker", "later", type="leaf", targetId=first),
        ]
        content = path.read_bytes() + b"".join((json.dumps(r) + "\n").encode() for r in records)
        for recovered in (False, True):
            path.write_bytes(content + (b"{torn" if recovered else b""))
            assert (await _page(rig, f"valid-{recovered}")).get("recovered") == (
                True if recovered else None
            )
            for leaf in ("forward", "cycle-1", "cycle-2", "self"):
                reply = await rigs.request(
                    rig.wire, f"{recovered}-{leaf}", "transcript/list", {"leafId": leaf}
                )
                assert reply["error"] == "invalidTree", reply
            assert path.read_bytes() == content + (b"{torn" if recovered else b"")


async def test_cursor_refuses_changed_recovery_with_unchanged_tip_and_flag(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path, setup="transcript-recovered") as rig:
        page = await _page(rig, "torn-one", limit=1)
        assert page["recovered"] is True
        path = Path(rig.values["session_file"])
        path.write_bytes(path.read_bytes() + b"{another-torn\n")
        fresh = await _page(rig, "torn-fresh")
        assert fresh["leafId"] == page["leafId"] and fresh["recovered"] is True
        stale = await rigs.request(
            rig.wire, "torn-stale", "transcript/list", {"cursor": page["nextCursor"]}
        )
        assert not stale["success"] and stale["error"] == "Stale transcript cursor"


@pytest.mark.parametrize("round_trip", [False, True], ids=["same-file", "via-new-session"])
async def test_same_file_reopen_refuses_every_selector_until_writer_settles(
    tmp_path: Path, round_trip: bool,
) -> None:
    import json

    from aelix_agent_core.session.fs import NotAttempted
    from aelix_agent_core.session.storage import SessionError

    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        storage = session.get_storage()
        seed = rig.values["entry_id"]
        withdrawn = await session.append_message(_user("withdrawn"))
        survivor = await session.append_message(_user("survivor"))
        path = Path(rig.values["session_file"])
        append = storage._fs.append_file
        mutation_count = 0
        allow = False

        async def fault(target: str, text: str) -> None:
            nonlocal mutation_count
            mutation_count += 1
            if json.loads(text).get("type") == "leaf":
                await append(target, text)
                raise OSError("fixture lost leaf acknowledgement")
            if not allow:
                raise NotAttempted("fixture holds the survivor")
            await append(target, text)

        storage._fs.append_file = fault
        with pytest.raises(SessionError, match="may have reached"):
            await storage.withdraw_tail(seed, lambda entry: entry.id == withdrawn)
        original = path.read_bytes()
        first_count = mutation_count
        pending = await rigs.request(rig.wire, "pending", "transcript/list", {})
        assert pending["error"] == "settlementPending"
        assert mutation_count == first_count and path.read_bytes() == original

        if round_trip:
            rig.wire.send({"type": "new_session", "id": "new"})
            assert (await rigs._wait(rig.wire.response("new"), "new session", rig.wire))["success"]
        before_reopen = mutation_count
        rig.wire.send({"type": "switch_session", "id": "swap", "sessionPath": str(path)})
        assert (await rigs._wait(rig.wire.response("swap"), "same-file swap", rig.wire))["success"]
        if round_trip:
            assert mutation_count == before_reopen, "the incoming binding retried the old writer"
        new = rig.runtime.harness.session.get_storage()
        assert new is not storage
        first_count = mutation_count
        for index, params in enumerate(({}, {"leafId": None}, {"leafId": survivor})):
            reply = await rigs.request(rig.wire, f"pending-{index}", "transcript/list", params)
            assert reply["error"] == "settlementPending"
        assert mutation_count == first_count and path.read_bytes() == original
        allow = True
        await storage.resolve_pending()
        settled = path.read_bytes()
        settled_count = mutation_count
        current = await _page(rig, "settled")
        assert [row["sequence"] for row in current["entries"]] == [1, 2]
        assert current["entries"][1]["entryId"] != survivor
        assert current["entries"][1]["message"]["content"][0]["text"] == "survivor"
        assert mutation_count == settled_count and path.read_bytes() == settled
        assert (await _page(rig, "former", leafId=survivor))["totalMessages"] == 3


async def test_live_frames_bind_the_decoded_write_and_tool_occurrences(tmp_path: Path) -> None:
    from dataclasses import replace

    from aelix_agent_core.harness.hooks import MessageEndEventResult
    from aelix_ai.messages import AssistantMessage, ToolCallContent, ToolResultMessage
    from aelix_ai.streaming import AssistantDoneEvent

    async with rigs.open_rig("full", tmp_path) as rig:
        harness = rig.harness
        session = harness.session
        finished = False

        async def stream(*_):
            nonlocal finished
            if finished:
                yield AssistantDoneEvent(
                    reason="stop", message=AssistantMessage(stop_reason="stop")
                )
                return
            finished = True
            calls = [ToolCallContent(tool_call_id="same", tool_name="fixture_echo", input={})] * 2
            yield AssistantDoneEvent(
                reason="toolUse", message=AssistantMessage(content=calls, stop_reason="toolUse")
            )

        async def rewrite(event, _):
            if isinstance(event.message, ToolResultMessage):
                return MessageEndEventResult(
                    message=replace(
                        event.message,
                        tool_call_id="changed",
                        content=[TextContent(text="accepted")],
                    )
                )
            return None

        harness._options.stream_fn = stream
        harness.hooks.on("message_end", rewrite)
        await harness.prompt("duplicate call IDs")
        page = await _page(rig, "live-results")
        results = [r for r in page["entries"] if r["message"]["role"] == "toolResult"]
        assert len(results) == 2 and results[0]["entryId"] != results[1]["entryId"]
        frames = rig.wire.records
        assert "transcript" not in next(f for f in frames if f["type"] == "agent_end")
        starts = [f for f in frames if f["type"] == "tool_execution_start"]
        assert [f["tool_call_id"] for f in starts] == ["same", "same"]
        streams = [f["transcript"]["streamId"] for f in starts]
        assert len(set(streams)) == 2
        for stream_id, row in zip(streams, results, strict=True):
            joined = [f for f in frames if f.get("transcript", {}).get("streamId") == stream_id]
            assert [f["type"] for f in joined] == [
                "tool_execution_start",
                "tool_execution_update",
                "tool_execution_end",
                "message_start",
                "message_end",
            ]
            end = joined[-1]
            assert end["message"]["tool_call_id"] == "same"
            assert row["message"]["tool_call_id"] == "changed"
            assert end["transcript"]["status"] == "stored" and end["transcript"]["replaced"] is True
            assert (
                await _page(
                    rig,
                    f"direct-{stream_id}",
                    leafId=end["transcript"]["entryId"],
                    firstSequence=end["transcript"]["sequence"],
                    limit=1,
                )
            )["entries"] == [row]
        assert len(await session.get_entries()) == page["totalMessages"]


async def test_wire_reply_budget_fragments_reassemble_and_keep_the_next_row(tmp_path: Path) -> None:
    import base64
    import json

    from aelix_coding_agent.rpc.rpc_mode import _reply_bytes
    from aelix_coding_agent.rpc.rpc_types import RpcSuccessResponse

    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        text = "a" + "한界" * 2_800_000
        large = await session.append_message(_user(text))
        later = await session.append_message(_user("later"))
        scan = {"firstSequence": 2}
        chunks = bytearray()
        count = 0
        while True:
            request_id = chr(0) * 168 + '\\"é'
            reply = await rigs.request(rig.wire, request_id, "transcript/list", scan)
            assert reply["success"], {k: v for k, v in reply.items() if k != "data"}
            data = reply["data"]
            body = _reply_bytes(
                RpcSuccessResponse(command="aelix_control", id=request_id, data=data)
            )
            assert len(body) <= 1_048_576 and body.endswith(b"\n")
            (row,) = data["entries"]
            if row["entryId"] == later:
                assert row["sequence"] == 3 and row["message"]["content"][0]["text"] == "later"
                assert data["nextCursor"] is None
                break
            assert row["entryId"] == large and row["sequence"] == 2
            part = row["messagePart"]
            assert part["offset"] == len(chunks)
            payload = base64.b64decode(part["base64"], validate=True)
            assert 0 < len(payload) <= 196_608
            chunks.extend(payload)
            count += 1
            if scan.get("cursor") and count == 2:
                assert data == await _page(rig, "retry-page", **scan)
            assert data["nextCursor"]
            scan = {"cursor": data["nextCursor"]}
            # Wire records are an in-memory capture, so drop already checked replies.
            rig.wire.records[:] = [f for f in rig.wire.records if f.get("id") != request_id]
        assert count > 80 and len(chunks) > 16 * 1024**2
        assert json.loads(chunks)["content"][0]["text"] == text


async def test_request_id_bound_and_encoding_fallback_remain_correlated(tmp_path: Path) -> None:
    import json

    from aelix_coding_agent.rpc.rpc_mode import _reply_bytes
    from aelix_coding_agent.rpc.rpc_types import RpcErrorResponse, RpcSuccessResponse

    request_id = ('"' * 3) + (chr(0) * 165) + ("𐀀" * 2) + "ab"
    assert len(json.dumps(request_id)) == 1024
    async with rigs.open_rig("full", tmp_path) as rig:
        assert (await _page(rig, request_id))["totalMessages"] == 1
        refused_id = request_id + "a"
        reply = await rigs.request(rig.wire, refused_id, "transcript/list", {})
        assert reply["id"] == refused_id and reply["error"] == "Invalid transcript request ID"
        from aelix_coding_agent.rpc.control_transcript import request_id_valid

        assert not request_id_valid(chr(0xD800))
        for response in (
            RpcSuccessResponse(command="aelix_control", id=request_id, data={"bad": chr(0xD800)}),
            RpcErrorResponse(command="aelix_control", id=request_id, error="x" * 1_048_576),
        ):
            encoded = _reply_bytes(response, bounded=True)
            assert len(encoded) < 2048
            assert json.loads(encoded)["id"] == request_id
            assert json.loads(encoded)["success"] is False


async def test_import_accepts_duplicates_but_transcript_refuses_them(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        target = Path(rig.values["session_file"])
        source = tmp_path / target.name
        original = target.read_bytes()
        damaged = original + original.splitlines()[-1] + b"\n"
        source.write_bytes(damaged)
        assert not (await rig.runtime.import_from_jsonl(str(source))).cancelled
        assert target.read_bytes() == damaged
        refusal = await rigs.request(rig.wire, "after-import", "transcript/list", {})
        assert refusal["id"] == "after-import" and refusal["error"] == "invalidTree"


async def test_a_read_crossing_an_abandoned_transition_refuses(tmp_path: Path) -> None:
    import asyncio

    async with rigs.open_rig("full", tmp_path) as rig:
        first = await _page(rig, "before-fence")
        storage = rig.harness.session.get_storage()
        snapshot = storage.transcript_snapshot
        captured = asyncio.Event()
        release = asyncio.Event()

        async def park_after_capture():
            cut = await snapshot()
            captured.set()
            await release.wait()
            return cut

        storage.transcript_snapshot = park_after_capture
        pending = asyncio.create_task(rigs.request(rig.wire, "crossed", "transcript/list", {}))
        try:
            await asyncio.wait_for(captured.wait(), 10)
            async with rig.runtime._transition():
                refused = await rigs.request(rig.wire, "in-transition", "transcript/list", {})
                assert not refused["success"]
        finally:
            release.set()
        reply = await pending
        assert reply["error"] == "Transcript binding is changing", reply
        assert await _page(rig, "same-binding") == first


async def test_stale_same_file_fallback_hides_discovery_until_a_fresh_open(tmp_path: Path) -> None:
    from unittest.mock import patch

    from aelix_agent_core.session.storage import SessionError

    async with rigs.open_rig("full", tmp_path) as rig:
        first = await _page(rig, "before-stale")
        repo = rig.runtime._repo
        open_session = repo.open
        opens = 0

        async def fail_post_settlement(*args, **kwargs):
            nonlocal opens
            opens += 1
            if opens == 2:
                raise SessionError("storage", "fixture could not reopen")
            return await open_session(*args, **kwargs)

        with patch.object(repo, "open", fail_post_settlement):
            await rig.runtime.switch_session(rig.values["session_file"])
        assert opens == 2
        capability = await rigs.request(rig.wire, "stale-cap", "capabilities", {})
        assert "transcript/list" not in capability["data"]["methods"]
        refused = await rigs.request(rig.wire, "stale-direct", "transcript/list", {"leafId": None})
        assert not refused["success"]
        await rig.runtime.switch_session(rig.values["session_file"])
        fresh = await _page(rig, "fresh-after-stale")
        assert fresh["bindingId"] != first["bindingId"]
        assert fresh["nativeSessionId"] == first["nativeSessionId"]
        assert fresh["entries"] == first["entries"]


async def test_all_three_roles_preserve_equal_payloads_and_distinct_sequences(
    tmp_path: Path,
) -> None:
    from aelix_ai.messages import AssistantMessage, ToolResultMessage

    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        user = _user("repeated")
        answer = AssistantMessage(content=[TextContent(text="repeated")], timestamp=1)
        result = ToolResultMessage(tool_call_id="reused", content=[], timestamp=None)
        occurrences = [user, answer, result] * 2
        ids = [await session.append_message(message) for message in occurrences]
        page = await _page(rig, "roles")
        rows = page["entries"][1:]
        assert len(set(ids)) == 6
        assert [row["entryId"] for row in rows] == ids
        assert [row["sequence"] for row in rows] == [2, 3, 4, 5, 6, 7]
        assert [row["parentEntryId"] for row in rows] == [rig.values["entry_id"], *ids[:-1]]
        assert [row["message"] for row in rows[:3]] == [row["message"] for row in rows[3:]]


async def test_clone_fork_and_import_preserve_their_native_namespaces(tmp_path: Path) -> None:
    import shutil

    async with rigs.open_rig("full", tmp_path) as rig:
        runtime = rig.runtime
        session = rig.harness.session
        tip = await session.append_message(_user("second"))
        original = await _page(rig, "original")
        source = await session.get_metadata()
        copy = tmp_path / "copy.jsonl"
        shutil.copyfile(rig.values["session_file"], copy)

        rig.wire.send({"type": "clone", "id": "clone"})
        assert (await rigs._wait(rig.wire.response("clone"), "classic clone", rig.wire))["success"]
        cloned = await _page(rig, "cloned")
        assert cloned["nativeSessionId"] != original["nativeSessionId"]
        assert cloned["entries"] == original["entries"] and cloned["leafId"] == tip

        await runtime.fork(original["entries"][0]["entryId"])
        empty = await _page(rig, "fork-before")
        assert empty["nativeSessionId"] != cloned["nativeSessionId"]
        assert empty["leafId"] is None and empty["entries"] == []

        await runtime.import_from_jsonl(str(copy))
        imported = await _page(rig, "imported")
        assert imported["nativeSessionId"] == original["nativeSessionId"]
        assert imported["entries"] == original["entries"]

        copied = await runtime._repo.fork_from(source, str(tmp_path))
        assert (await copied.get_metadata()).id != source.id
        snapshot = await copied.get_storage().transcript_snapshot()
        assert [entry.id for entry in snapshot.messages(tip)] == [
            row["entryId"] for row in original["entries"]
        ]


async def test_oversized_coordinates_return_a_bounded_error_and_keep_the_leaf(
    tmp_path: Path,
) -> None:
    import json

    from aelix_agent_core.session.entries import CustomEntry, MessageEntry

    async with rigs.open_rig("full", tmp_path) as rig:
        storage = rig.harness.session.get_storage()
        parent = "p" * 1_048_238
        stamp = "2026-11-20T00:00:00Z"
        await storage.append_entry(
            CustomEntry(id=parent, parent_id=None, timestamp=stamp, custom_type="wide")
        )
        await storage.append_entry(
            MessageEntry(
                id="child", parent_id=parent, timestamp=stamp, message=_user("x" * 1_100_000)
            )
        )
        reply = await rigs.request(
            rig.wire,
            "wide-parent",
            "transcript/list",
            {"leafId": "child", "firstSequence": 1, "limit": 1},
        )
        assert reply == {
            "id": "wide-parent",
            "type": "response",
            "command": "aelix_control",
            "success": False,
            "error": "Transcript coordinates exceed reply bound",
        }
        assert len(json.dumps(reply).encode() + b"\n") <= 1_048_576
        suffix = await _page(rig, "without-payload", firstSequence=2)
        assert suffix["leafId"] == "child" and suffix["totalMessages"] == 1
        assert suffix["entries"] == [] and suffix["nextCursor"] is None


async def test_receipt_with_an_undecodable_ancestor_is_storage_only(tmp_path: Path) -> None:
    from aelix_agent_core.session.entries import CustomEntry

    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        await session.get_storage().append_entry(
            CustomEntry(
                id="bad-stamp", parent_id=await session.get_leaf_id(), timestamp="",
                custom_type="fixture",
            )
        )
        await rig.harness.prompt("append under an invalid line")
        ends = [frame for frame in rig.wire.records if frame["type"] == "message_end"]
        assert [frame["message"]["role"] for frame in ends] == ["user", "assistant"]
        for frame in ends:
            assert frame.get("transcript", {}).get("status") == "storageOnly", frame
            assert set(frame["transcript"]) == {"nativeSessionId", "bindingId", "streamId", "status"}
        page = await _page(rig, "active")
        assert page["recovered"] is True
        assert [row["sequence"] for row in page["entries"]] == [1]


async def test_receipts_with_a_created_invalid_header_are_storage_only(tmp_path: Path) -> None:
    import asyncio

    from aelix_agent_core.runtime import create_agent_session_runtime
    from aelix_agent_core.session import (
        JsonlSessionCreateOptions,
        JsonlSessionRepo,
        LocalFileSystem,
    )
    from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

    from tests.rpc.test_runtime_control_lifecycle_594 import Wire

    seed = rigs.seed_files(tmp_path)
    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(seed.sessions))
    session = await repo.create(JsonlSessionCreateOptions(cwd=""))
    gate = asyncio.Event()
    gate.set()

    async def factory(session):
        return await rigs.build_harness("full", seed, gate, session)

    harness = await factory(session)
    runtime = await create_agent_session_runtime(harness, factory, repo=repo, fs=fs)
    rigs.hold_session_writer(runtime)
    wire = Wire()
    task = asyncio.create_task(
        run_rpc_mode(
            harness, runtime_host=runtime, stdin=wire.reader, stdout_write=wire.write,
            install_signal_handlers=False,
        )
    )
    try:
        await rigs.request(wire, "ready", "capabilities", {})
        await harness.prompt("ordinary content")
        ends = [frame for frame in wire.records if frame["type"] == "message_end"]
        assert [frame["message"]["role"] for frame in ends] == ["user", "assistant"]
        refused = await rigs.request(wire, "read", "transcript/list", {})
        assert refused["error"] == "Transcript unavailable"
        assert [frame["transcript"]["status"] for frame in ends] == ["storageOnly"] * 2
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(task, 15)


async def test_open_duplicate_ids_block_new_root_receipt(tmp_path: Path) -> None:
    from aelix_agent_core.session import (
        JsonlSessionStorage,
        LocalFileSystem,
        MessageEntry,
        SessionError,
    )

    fs = LocalFileSystem()
    path = str(tmp_path / "duplicate.jsonl")
    duplicate = MessageEntry(id="same", parent_id=None, timestamp="2026-11-20", message=_user("seed"))
    await JsonlSessionStorage.create(
        fs, path, cwd=str(tmp_path), session_id="duplicate", entries=[duplicate, duplicate],
    )
    storage = await JsonlSessionStorage.open(fs, path)
    appended = await storage.append_message_receipt(
        lambda entry_id, _: MessageEntry(
            id=entry_id, parent_id=None, timestamp="2026-11-20", message=_user("new root"),
        )
    )
    with pytest.raises(SessionError) as refused:
        snapshot = await storage.transcript_snapshot()
        snapshot.messages(appended.entry_id)
    assert refused.value.code == "invalidTree"
    assert appended.stored is None


async def test_same_file_settlement_refreshes_the_receipt_index(tmp_path: Path) -> None:
    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, MessageEntry

    fs = LocalFileSystem()
    path = str(tmp_path / "inherited.jsonl")
    root = MessageEntry(id="root", parent_id=None, timestamp="2026-11-20", message=_user("root"))
    outgoing = await JsonlSessionStorage.create(
        fs, path, cwd=str(tmp_path), session_id="inherited", entries=[root],
    )
    successor = await JsonlSessionStorage.open(fs, path)
    await outgoing.append_entry(
        MessageEntry(id="new", parent_id="root", timestamp="2026-11-20", message=_user("new"))
    )
    successor.inherit_settlement(outgoing)
    appended = await successor.append_message_receipt(
        lambda entry_id, parent_id: MessageEntry(
            id=entry_id, parent_id=parent_id, timestamp="2026-11-20", message=_user("successor"),
        )
    )
    snapshot = await successor.transcript_snapshot()
    rows = snapshot.messages(appended.entry_id)
    assert [row.id for row in rows] == ["root", "new", appended.entry_id]
    assert appended.stored is not None
    assert appended.stored.sequence == len(rows)


async def test_a_tool_turn_qualifies_receipts_without_whole_session_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_agent_core.session import LocalFileSystem

    async with rigs.open_rig("full", tmp_path, setup="transcript-live") as rig:
        path = Path(rig.values["session_file"])
        reads: list[str] = []
        read_file = LocalFileSystem.read_text_file

        async def read(fs: LocalFileSystem, file_path: str) -> str:
            if Path(file_path) == path:
                reads.append(file_path)
            return await read_file(fs, file_path)

        monkeypatch.setattr(LocalFileSystem, "read_text_file", read)
        await rig.harness.prompt("fixture tools")
        assert reads == []
        ends = [frame for frame in rig.wire.records if frame["type"] == "message_end"]
        assert [frame["message"]["role"] for frame in ends] == [
            "user", "assistant", "toolResult", "assistant",
        ]
        for index, frame in enumerate(ends, start=2):
            receipt = frame["transcript"]
            assert receipt["status"] == "stored" and receipt["sequence"] == index
            page = await _page(
                rig, f"direct-{index}", leafId=receipt["entryId"], firstSequence=index, limit=1,
            )
            row, = page["entries"]
            assert (row["entryId"], row["sequence"]) == (receipt["entryId"], index)
        assert reads  # The same filesystem sees the directed reads.


async def test_receipt_append_refuses_a_reserved_id_without_writing(tmp_path: Path) -> None:
    import json

    from aelix_agent_core.session import MessageEntry, SessionError

    stamp = "2026-11-20T00:00:00Z"
    async with rigs.open_rig("full", tmp_path) as rig:
        path = Path(rig.values["session_file"])
        orphan = {
            "type": "custom", "customType": "fixture", "id": "reserved",
            "parentId": "missing", "timestamp": stamp,
        }
        before = path.read_bytes() + b"{torn\n" + (json.dumps(orphan) + "\n").encode()
        path.write_bytes(before)
        rig.wire.send({"type": "switch_session", "id": "reopen", "sessionPath": str(path)})
        assert (await rigs._wait(rig.wire.response("reopen"), "reopen", rig.wire))["success"]
        storage = rig.runtime.session.get_storage()

        def build(_allocated: str, parent: str | None) -> MessageEntry:
            return MessageEntry(id="reserved", parent_id=parent, timestamp=stamp, message=_user("new"))

        page = await _page(rig, "before")
        with pytest.raises(SessionError, match="Duplicate session entry ID") as refusal:
            await storage.append_message_receipt(build)
        assert refusal.value.code == "invalidTree"
        assert path.read_bytes() == before
        assert await _page(rig, "after") == page


@pytest.mark.parametrize("shape", ["pruned", "skipped"])
async def test_append_ids_are_reserved_even_when_the_decoder_drops_the_entry(
    tmp_path: Path, shape: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from types import SimpleNamespace

    from aelix_agent_core.session import jsonl_storage
    from aelix_agent_core.session.entries import CustomEntry
    from aelix_agent_core.session.storage import SessionError

    reserved = "abcdabcd"
    fresh = "dddddddd"
    stamp = "2026-11-20T00:00:00Z"
    async with rigs.open_rig("full", tmp_path) as rig:
        path = Path(rig.values["session_file"])
        line = {
            "type": "custom", "customType": "fixture", "id": reserved, "parentId": "missing",
            "timestamp": stamp if shape == "pruned" else "",
        }
        damaged = path.read_bytes() + b"{torn\n" + (json.dumps(line) + "\n").encode()
        path.write_bytes(damaged)
        rig.wire.send({"type": "switch_session", "id": "reopen", "sessionPath": str(path)})
        assert (await rigs._wait(rig.wire.response("reopen"), "reopen", rig.wire))["success"]
        session = rig.runtime.session
        storage = session.get_storage()
        with pytest.raises(SessionError, match="Duplicate session entry ID") as refusal:
            await storage.append_entry(
                CustomEntry(
                    id=reserved, parent_id=await storage.get_leaf_id(),
                    timestamp=stamp, custom_type="fixture",
                )
            )
        assert refusal.value.code == "invalidTree"
        assert path.read_bytes() == damaged
        ids = iter([reserved, fresh])
        monkeypatch.setattr(jsonl_storage, "uuid", SimpleNamespace(
            uuid4=lambda: SimpleNamespace(hex=next(ids)),
        ))
        assert await session.append_message(_user("a fresh generated ID")) == fresh
        assert (await _page(rig, "after"))["entries"][-1]["entryId"] == fresh


async def test_empty_id_is_a_correlated_refusal(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        reply = await rigs.request(rig.wire, "", "transcript/list", {})
        assert reply.get("id") == "" and reply["error"] == "Invalid transcript request ID"


async def test_recovered_implicit_forward_leaf_refuses(tmp_path: Path) -> None:
    import json

    async with rigs.open_rig("full", tmp_path) as rig:
        path = Path(rig.values["session_file"])
        first = rig.values["entry_id"]
        stamp = "2026-11-20T00:00:00Z"
        records = [
            {"type": "custom", "customType": "fixture", "id": "tip", "parentId": "future"},
            {"type": "custom", "customType": "fixture", "id": "future", "parentId": first},
            {"type": "leaf", "id": "pick", "parentId": first, "targetId": "tip"},
        ]
        with path.open("ab") as stream:
            for record in records:
                stream.write((json.dumps({**record, "timestamp": stamp}) + "\n").encode())
            stream.write(b"{torn\n")
        reply = await rigs.request(rig.wire, "forward", "transcript/list", {})
        assert reply["id"] == "forward" and reply["error"] == "invalidTree"


async def test_interleaved_fragment_cursors_keep_their_payloads(tmp_path: Path) -> None:
    import base64

    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        ids = [await session.append_message(_user(char * 1_100_000)) for char in "xy"]
        pages = [
            await _page(rig, f"start-{i}", leafId=entry_id, firstSequence=i + 2)
            for i, entry_id in enumerate(ids)
        ]
        for i, char in enumerate("xy"):
            page = await _page(rig, f"continue-{i}", cursor=pages[i]["nextCursor"])
            (row,) = page["entries"]
            chunk = base64.b64decode(row["messagePart"]["base64"], validate=True)
            assert row["entryId"] == ids[i] and chunk == char.encode() * len(chunk)


async def test_two_individually_fitting_rows_are_paged_in_order(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        ids = [await session.append_message(_user(char * 570_000)) for char in "ab"]
        first = await _page(rig, "first", firstSequence=2, limit=2)
        assert [row["entryId"] for row in first["entries"]] == ids[:1]
        assert "message" in first["entries"][0]
        second = await _page(rig, "next", cursor=first["nextCursor"])
        assert [row["entryId"] for row in second["entries"]] == ids[1:]
        assert "message" in second["entries"][0] and second["nextCursor"] is None


async def test_a_live_fragmented_receipt_points_to_its_first_part(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        await rig.harness.prompt("x" * 1_100_000)
        user = next(
            frame for frame in rig.wire.records
            if frame["type"] == "message_end" and frame["message"]["role"] == "user"
        )
        assert user.get("transcript", {}).get("status") == "stored", list(user)
        receipt = user["transcript"]
        page = await _page(
            rig, "large", leafId=receipt["entryId"], firstSequence=receipt["sequence"], limit=1,
        )
        (row,) = page["entries"]
        assert row["entryId"] == receipt["entryId"] and row["messagePart"]["offset"] == 0


@pytest.mark.parametrize("parent", [chr(0xD800), "p" * 1_048_576], ids=["surrogate", "wide-placeholder"])
async def test_unreturnable_coordinates_keep_the_classic_end_with_storage_only(
    tmp_path: Path, parent: str,
) -> None:
    from dataclasses import replace

    from aelix_agent_core.harness.hooks import MessageEndEventResult
    from aelix_agent_core.session.entries import CustomEntry

    async with rigs.open_rig("full", tmp_path) as rig:
        await rig.harness.session.get_storage().append_entry(
            CustomEntry(id=parent, parent_id=None, timestamp="2026-11-20", custom_type="fixture")
        )

        async def make_placeholder(event, _ctx):
            if event.message.role == "user" and parent != chr(0xD800):
                return MessageEndEventResult(
                    message=replace(event.message, content=[TextContent(text=chr(0xD800))])
                )
            return None

        rig.harness.hooks.on("message_end", make_placeholder)
        await rig.harness.prompt("encodable original")
        user = next(
            frame for frame in rig.wire.records
            if frame["type"] == "message_end" and frame["message"]["role"] == "user"
        )
        assert user.get("transcript", {}).get("status") == "storageOnly", user
        assert "entryId" not in user["transcript"]
        reply = await rigs.request(rig.wire, "unreturnable", "transcript/list", {})
        assert reply["error"] == "Transcript coordinates exceed reply bound"


async def test_recovered_snapshot_revalidates_a_previously_cached_leaf(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        tip = await rig.harness.session.append_message(_user("cached"))
        assert (await _page(rig, "before", leafId=tip))["totalMessages"] == 2
        path = Path(rig.values["session_file"])
        header, _, leaf = path.read_bytes().splitlines()
        path.write_bytes(b"\n".join((header, b"{torn", leaf, b"")))
        reply = await rigs.request(rig.wire, "pruned", "transcript/list", {"leafId": tip})
        assert reply["id"] == "pruned" and reply["error"] == "Unknown transcript leaf"


async def test_reply_reserves_space_for_a_required_cursor(tmp_path: Path) -> None:
    from aelix_coding_agent.rpc.rpc_mode import _reply_bytes
    from aelix_coding_agent.rpc.rpc_types import RpcSuccessResponse

    async with rigs.open_rig("full", tmp_path) as rig:
        session = rig.harness.session
        await session.append_message(_user("x"))
        data = await _page(rig, "size", firstSequence=2, limit=1)
        size = len(_reply_bytes(RpcSuccessResponse(id="size", command="aelix_control", data=data)))
        wide = await session.append_message(_user("x" * (1_048_576 - size + 1)))
        await session.append_message(_user("following"))
        data = await _page(rig, "next", firstSequence=3, limit=1)
        (row,) = data["entries"]
        assert row["entryId"] == wide
        assert row["messagePart"]["offset"] == 0 and data["nextCursor"]


async def test_fragment_cache_respects_its_byte_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        await rig.harness.session.append_message(_user("x" * 1_100_000))
        before = await _page(rig, "first-part", firstSequence=2)
        assert before["nextCursor"]

        from aelix_coding_agent.rpc import control_transcript

        original = control_transcript.StoredMessage.encode
        encodes = 0

        def count(stored):
            nonlocal encodes
            encodes += 1
            return original(stored)

        monkeypatch.setattr(control_transcript.StoredMessage, "encode", count)
        await _page(rig, "cached", cursor=before["nextCursor"])
        assert encodes == 0

        monkeypatch.setattr(control_transcript, "_MAX_FRAGMENT_CACHE", 1_000_000)
        await rig.harness.session.append_message(_user("y" * 1_100_000))
        first = await _page(rig, "over-budget", firstSequence=3)
        assert encodes == 1
        await _page(rig, "over-budget-next", cursor=first["nextCursor"])
        assert encodes == 2


async def test_recovery_prunes_lost_leaf_markers_and_invalidates_old_cursor(tmp_path: Path) -> None:
    import json

    async with rigs.open_rig("full", tmp_path) as rig:
        tip = await rig.harness.session.append_message(_user("second"))
        path = Path(rig.values["session_file"])
        with path.open("ab") as stream:
            stream.write(b"{torn\n")
        before = await _page(rig, "before", leafId=tip, limit=1)
        records = [
            {"type": "leaf", "id": "known-pick", "parentId": tip, "targetId": tip},
            {"type": "leaf", "id": "lost-pick", "parentId": tip, "targetId": "missing"},
            {"type": "custom", "customType": "fixture", "id": "child", "parentId": "lost-pick"},
        ]
        with path.open("ab") as stream:
            for record in records:
                stream.write((json.dumps({**record, "timestamp": "2026-11-20"}) + "\n").encode())
        current = await _page(rig, "active")
        assert current["leafId"] == tip and current["recovered"] is True
        good_marker = await _page(rig, "known", leafId="known-pick")
        assert good_marker["entries"] == current["entries"]
        stale = await rigs.request(
            rig.wire, "stale", "transcript/list", {"cursor": before["nextCursor"]},
        )
        assert stale["error"] == "Stale transcript cursor"


async def test_a_fitting_complete_receipt_does_not_need_fragment_framing(tmp_path: Path) -> None:
    from aelix_agent_core.session.entries import CustomEntry
    from aelix_coding_agent.rpc.rpc_mode import _reply_bytes
    from aelix_coding_agent.rpc.rpc_types import RpcSuccessResponse

    async with rigs.open_rig("full", tmp_path) as rig:
        storage = rig.harness.session.get_storage()

        async def receipt_below(parent: str):
            await storage.append_entry(
                CustomEntry(id=parent, parent_id=None, timestamp="2026-11-20", custom_type="root")
            )
            first = len(rig.wire.records)
            await rig.harness.prompt("short")
            frame = next(
                frame for frame in rig.wire.records[first:]
                if frame["type"] == "message_end" and frame["message"]["role"] == "user"
            )
            assert frame.get("transcript", {}).get("status") == "stored", frame
            return frame["transcript"]["entryId"]

        sample = await receipt_below("short")
        request_id = chr(0) * 170 + "xx"
        data = await _page(rig, request_id, leafId=sample, limit=1)
        size = len(_reply_bytes(RpcSuccessResponse(id=request_id, command="aelix_control", data=data)))
        # Leave room for a recovery flag, but less than a fragment cursor costs.
        wide = await receipt_below("p" * (1_048_576 - size - 32 + len("short")))
        page = await _page(rig, request_id[:-2] + "yy", leafId=wide, limit=1)
        (row,) = page["entries"]
        assert row["entryId"] == wide and row["message"]["content"][0]["text"] == "short"
