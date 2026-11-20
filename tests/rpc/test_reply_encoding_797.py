"""RPC replies and events carry surrogate-escaped text without losing the frame."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest
from aelix_ai.messages import AssistantMessage, ToolCallContent
from aelix_coding_agent.core.extension_selection import ExtensionInventory, RefusedRow
from aelix_coding_agent.extensions.loader import ExtensionLoadError
from aelix_coding_agent.rpc import rpc_mode
from aelix_coding_agent.rpc.control import RpcControlPlane
from aelix_coding_agent.rpc.rpc_client import RpcClient
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode
from aelix_coding_agent.rpc.rpc_types import RpcSuccessResponse

from tests.rpc.control_v1 import rigs
from tests.rpc.test_runtime_control_lifecycle_594 import Wire, _commands, _control
from tests.rpc.test_runtime_control_surfaces_594 import _harness

pytestmark = pytest.mark.usefixtures("own_agent_dir")


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON constant {value}")


def _decode(data: bytes) -> Any:
    assert data.endswith(b"\n")
    return json.loads(data.decode("utf-8", errors="strict"), parse_constant=_reject_constant)


class _StrictWire(Wire):
    def __init__(self) -> None:
        super().__init__()
        self.raw: list[bytes] = []

    def write(self, data: bytes) -> None:
        record = _decode(data)
        self.raw.append(data)
        self.records.append(record)
        self._changed.set()


@asynccontextmanager
async def _running_rpc(harness: Any) -> AsyncIterator[_StrictWire]:
    wire = _StrictWire()
    run = asyncio.create_task(
        run_rpc_mode(
            harness, stdin=wire.reader, stdout_write=wire.write, install_signal_handlers=False
        )
    )
    try:
        wire.send({"type": "get_state", "id": "ready"})
        await asyncio.wait_for(wire.response("ready"), 5)
        yield wire
    finally:
        wire.send(eof=True)
        await asyncio.wait_for(run, 10)
        await harness.dispose()


async def test_a_control_reply_replaces_surrogates_and_preserves_correlation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = {"path": "한글 \ud800", "literal": r"\ud800"}

    async def odd(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return data

    monkeypatch.setattr(RpcControlPlane, "_dispatch", odd)
    async with _running_rpc(_harness()) as wire:
        wire.send(_control("control", "mcp/status"))
        reply = await asyncio.wait_for(wire.response("control"), 5)
        assert reply["success"] is True, reply
        assert reply["data"] == {"path": "한글 \ufffd", "literal": r"\ud800"}
        assert "한글".encode() in wire.raw[-1]
        assert rb'"literal": "\\ud800"' in wire.raw[-1]

        wire.send(_control("id-\udcff", "mcp/status"))
        reply = await asyncio.wait_for(wire.response("id-\udcff"), 5)
        assert reply["success"] is False, reply
        assert reply["error"].startswith("the reply could not be written as UTF-8: ")


async def test_a_classic_reply_replaces_a_surrogate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def odd(harness: Any, payload: dict[str, Any], dispatch: Any) -> RpcSuccessResponse:
        return RpcSuccessResponse(
            command="get_state", id=payload.get("id"), data={"sessionName": "\udcff"}
        )

    async with _running_rpc(_harness()) as wire:
        monkeypatch.setattr(rpc_mode, "_handle_command", odd)
        wire.send({"type": "get_state", "id": "g1"})
        reply = await asyncio.wait_for(wire.response("g1"), 5)
        assert reply == {
            "type": "response",
            "command": "get_state",
            "success": True,
            "id": "g1",
            "data": {"sessionName": "\ufffd"},
        }


async def test_mcp_listing_refuses_to_alias_two_server_keys(tmp_path: Path) -> None:
    async with rigs.open_rig("full", tmp_path) as rig:
        target = Path(os.environ["AELIX_CODING_AGENT_DIR"]) / "mcp.json"
        config = {
            "mcpServers": {
                name: {"command": "unused", "enabled": False}
                for name in ("probe-\ufffd", "probe-\udcff")
            }
        }
        original = json.dumps(config)
        target.write_text(original, encoding="utf-8")

        reply = await rigs.request(rig.wire, "ambiguous-mcp", "selections/list", {"kind": "mcp"})

        assert reply["success"] is False, reply
        assert "selection key" in reply["error"] and "UTF-8" in reply["error"]
        assert "data" not in reply
        assert target.read_text(encoding="utf-8") == original

        del config["mcpServers"]["probe-\udcff"]
        target.write_text(json.dumps(config), encoding="utf-8")
        listed = await rigs.request(rig.wire, "valid-mcp", "selections/list", {"kind": "mcp"})
        (row,) = listed["data"]["items"]
        assert row["key"] == "probe-\ufffd"
        saved = await rigs.request(
            rig.wire,
            "set-mcp",
            "selections/set",
            {"kind": "mcp", "scope": "global", "changes": [{"key": row["key"], "on": False}]},
        )
        assert saved["success"] is True and saved["data"]["written"] == [], saved


async def test_get_messages_refuses_to_merge_two_tool_input_members() -> None:
    harness = _harness()
    surrogate_key = "slot-\udcff"
    arguments = {"slot-\ufffd": "ordinary", surrogate_key: "second"}
    await harness.append_message(
        AssistantMessage(
            content=[ToolCallContent(tool_call_id="fixture", tool_name="fixture", input=arguments)]
        )
    )

    async with _running_rpc(harness) as wire:
        wire.send({"type": "get_messages", "id": "ambiguous-messages"})
        reply = await asyncio.wait_for(wire.response("ambiguous-messages"), 5)
        assert reply["success"] is False, reply
        assert reply["error"].startswith("the reply could not be written as UTF-8: ")
        assert "data" not in reply

        wire.send({"type": "get_state", "id": "after"})
        assert (await asyncio.wait_for(wire.response("after"), 5))["success"] is True


@pytest.mark.parametrize("method", ["resources/list", "selections/list"])
async def test_inventory_keeps_healthy_rows_alongside_a_surrogate_load_error(
    method: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tests.rpc.test_runtime_control_lifecycle_594.Wire", _StrictWire)
    async with rigs.open_rig("full", tmp_path) as rig:
        params = {"kind": "extension"} if method == "selections/list" else {}
        baseline = await rigs.request(rig.wire, "before", method, params)
        assert baseline["success"] is True, baseline
        path = str(tmp_path / "broken-\udcff.py")
        message = "failed to load \ud800"
        rig.services["extension_errors"].append(ExtensionLoadError(path=path, error=message))
        key = f"refused:{path}"
        rig.services["selections"]["extension"].holder["inventory"] = ExtensionInventory(
            refused=(RefusedRow(key, "broken.py", message, path),)
        )

        reply = await rigs.request(rig.wire, "with-error", method, params)
        assert reply["success"] is True, reply
        safe_message = "failed to load \ufffd"
        safe_path = str(tmp_path / "broken-\ufffd.py")
        if method == "resources/list":
            data = reply["data"]
            assert {"source": safe_path, "message": safe_message} in data["extensionErrors"]
            assert {key: value for key, value in data.items() if key != "extensionErrors"} == {
                key: value for key, value in baseline["data"].items() if key != "extensionErrors"
            }
            assert data["commands"] and data["tools"]
        else:
            rows = reply["data"]["items"]
            assert all(row in rows for row in baseline["data"]["items"])
            display_key = "readonly:" + key.encode("utf-8", "surrogatepass").hex()
            (failed,) = [row for row in rows if row["key"] == display_key]
            assert failed["role"] == "placeholder"
            assert failed["refusal"]
            assert safe_message in failed["description"]


@pytest.mark.parametrize("event_type", ["message_update", "aelix_subagent"])
async def test_surrogate_events_keep_their_neighbors(event_type: str) -> None:
    harness = _harness()
    async with _running_rpc(harness) as wire:
        texts = ["before", "한글 😀\u2028\udcff", "after"]
        for value in texts:
            await harness._emit_to_subscribers({"type": "message_update", "probe": value})
            harness.runtime.event_bus.emit(
                "subagent_start", SimpleNamespace(id="child", profile=value, state="starting")
            )
        frames = [
            (raw, frame)
            for raw, frame in zip(wire.raw, wire.records, strict=True)
            if frame["type"] == event_type
        ]
        if event_type == "message_update":
            actual = [frame["probe"] for _, frame in frames]
        else:
            actual = [frame["data"]["profile"] for _, frame in frames]
        assert actual == ["before", "한글 😀\u2028\ufffd", "after"]
        assert '"한글 😀\u2028\ufffd"'.encode() in frames[1][0]
        wire.send({"type": "get_state", "id": "after-events"})
        assert (await asyncio.wait_for(wire.response("after-events"), 5))["success"] is True


async def test_surrogate_extension_ui_notice_reaches_the_host() -> None:
    def notify(args: str, ctx: Any) -> None:
        ctx.ui.notify("한글 \udcff")

    harness = _harness(extensions=[_commands(notify=notify)])
    async with _running_rpc(harness) as wire:
        wire.send(_control("enable", "ui/enable", enabled=True))
        assert (await asyncio.wait_for(wire.response("enable"), 5))["success"] is True
        wire.send(_control("notify", "commands/run", text="/notify"))
        reply = await asyncio.wait_for(wire.response("notify"), 5)
        notices = [
            frame for frame in map(_decode, wire.raw) if frame["type"] == "extension_ui_request"
        ]
        assert [frame["message"] for frame in notices] == ["한글 \ufffd"]
        assert reply["data"]["outcome"] == "handled"


async def test_rpc_client_replaces_surrogates_in_requests_to_the_child() -> None:
    child = textwrap.dedent(
        r"""
        import json
        import sys

        def reject_constant(value):
            raise ValueError(value)

        for raw in sys.stdin.buffer:
            cmd = json.loads(raw.decode("utf-8", errors="strict"), parse_constant=reject_constant)
            assert cmd["type"] == "set_session_name"
            assert cmd["name"] == "한글 \ufffd"
            assert '"한글 \ufffd"'.encode() in raw
            print(json.dumps({
                "type": "response", "id": cmd["id"], "command": cmd["type"], "success": True,
            }), flush=True)
        """
    )

    class EchoClient(RpcClient):
        def _build_argv(self) -> list[str]:
            return [sys.executable, "-c", child]

    client = EchoClient()
    await client.start()
    try:
        await client.set_session_name("한글 \udcff")
    finally:
        await client.stop()


def test_a_reply_utf8_can_carry_is_written_as_it_was() -> None:
    reply = RpcSuccessResponse(command="get_state", id="a", data={"name": "한글  "})
    assert rpc_mode._reply_bytes(reply) == (json.dumps(reply.to_json(), ensure_ascii=False) + "\n").encode("utf-8")


def test_a_reply_replaces_each_surrogate_and_keeps_ordinary_bytes() -> None:
    reply = RpcSuccessResponse(
        command="get_state", id="a", data={"name": "한글 😀\u2028\udcff\ud800"}
    )
    expected = RpcSuccessResponse(
        command="get_state", id="a", data={"name": "한글 😀\u2028\ufffd\ufffd"}
    )
    raw = rpc_mode._reply_bytes(reply)
    assert raw == (json.dumps(expected.to_json(), ensure_ascii=False) + "\n").encode("utf-8")
    assert _decode(raw) == expected.to_json()
