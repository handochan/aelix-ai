"""Pi parity: every :class:`RpcCommand` variant round-trips through the
parse/serialize path, and ``RpcSessionState.to_json`` / ``from_json``
preserve the Pi-shape camelCase ↔ snake_case mapping.

Covers §C of the binding spec.
"""

from __future__ import annotations

from aelix_coding_agent.rpc.rpc_types import (
    RPC_COMMAND_TYPES,
    RpcCommandBash,
    RpcCommandCompact,
    RpcCommandExportHtml,
    RpcCommandFork,
    RpcCommandNewSession,
    RpcCommandPrompt,
    RpcCommandSetModel,
    RpcCommandSetThinkingLevel,
    RpcCommandSwitchSession,
    RpcErrorResponse,
    RpcSessionState,
    RpcSuccessResponse,
    command_to_json,
    parse_rpc_command,
    parse_rpc_response,
)


def test_all_28_command_types_parseable() -> None:
    """Pi parity: ``parse_rpc_command`` accepts every Pi RpcCommand discriminator."""

    for cmd_type in RPC_COMMAND_TYPES:
        # Minimal payloads — required fields filled with defaults so the
        # dispatcher exercises every constructor.
        payload: dict[str, object] = {"type": cmd_type}
        if cmd_type in ("prompt", "steer", "follow_up"):
            payload["message"] = "hi"
        elif cmd_type == "set_model":
            payload["provider"] = "p"
            payload["modelId"] = "m"
        elif cmd_type == "set_thinking_level":
            payload["level"] = "medium"
        elif cmd_type in ("set_steering_mode", "set_follow_up_mode"):
            payload["mode"] = "all"
        elif cmd_type == "bash":
            payload["command"] = "echo hi"
        cmd = parse_rpc_command(payload)
        assert cmd.type == cmd_type


def test_parse_rejects_missing_type() -> None:
    import pytest

    with pytest.raises(ValueError, match="missing 'type'"):
        parse_rpc_command({"message": "no type"})


def test_parse_rejects_unknown_type() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown RpcCommand type"):
        parse_rpc_command({"type": "does_not_exist"})


def test_command_to_json_snake_to_camel_remap() -> None:
    """Multi-word snake_case fields are remapped to camelCase on the wire."""

    cmd = RpcCommandSetModel(provider="p", model_id="m", id="req_1")
    wire = command_to_json(cmd)
    assert wire == {
        "type": "set_model",
        "provider": "p",
        "modelId": "m",
        "id": "req_1",
    }


def test_command_to_json_drops_none_fields() -> None:
    cmd = RpcCommandPrompt(message="hi")
    wire = command_to_json(cmd)
    # ``images``, ``streaming_behavior``, ``id`` are all None and dropped.
    assert wire == {"type": "prompt", "message": "hi"}


def test_command_to_json_keeps_streaming_behavior_when_set() -> None:
    cmd = RpcCommandPrompt(message="hi", streaming_behavior="steer")
    wire = command_to_json(cmd)
    assert wire["streamingBehavior"] == "steer"


def test_command_to_json_roundtrips_through_parse_rpc_command() -> None:
    """Every command serialized → parsed → equal to the original."""

    cases: list[object] = [
        RpcCommandPrompt(message="hi", id="r1"),
        RpcCommandSetModel(provider="p", model_id="m"),
        RpcCommandCompact(custom_instructions="instr"),
        RpcCommandBash(command="ls"),
        RpcCommandNewSession(parent_session="parent.jsonl"),
        RpcCommandExportHtml(output_path="/tmp/out.html"),
        RpcCommandSwitchSession(session_path="other.jsonl"),
        RpcCommandFork(entry_id="entry-1"),
        RpcCommandSetThinkingLevel(level="high"),
    ]
    for original in cases:
        wire = command_to_json(original)
        parsed = parse_rpc_command(wire)
        assert parsed == original


def test_success_response_to_json_drops_data_and_id_when_none() -> None:
    resp = RpcSuccessResponse(command="abort")
    assert resp.to_json() == {
        "type": "response",
        "command": "abort",
        "success": True,
    }


def test_success_response_to_json_includes_data_and_id_when_set() -> None:
    resp = RpcSuccessResponse(command="get_state", data={"x": 1}, id="r1")
    assert resp.to_json() == {
        "type": "response",
        "command": "get_state",
        "success": True,
        "data": {"x": 1},
        "id": "r1",
    }


def test_error_response_to_json() -> None:
    resp = RpcErrorResponse(command="steer", error="not implemented", id="r2")
    assert resp.to_json() == {
        "type": "response",
        "command": "steer",
        "success": False,
        "error": "not implemented",
        "id": "r2",
    }


def test_parse_rpc_response_success_envelope() -> None:
    payload = {
        "type": "response",
        "command": "get_state",
        "success": True,
        "data": {"x": 1},
        "id": "r1",
    }
    parsed = parse_rpc_response(payload)
    assert isinstance(parsed, RpcSuccessResponse)
    assert parsed.command == "get_state"
    assert parsed.data == {"x": 1}
    assert parsed.id == "r1"


def test_parse_rpc_response_error_envelope() -> None:
    payload = {
        "type": "response",
        "command": "steer",
        "success": False,
        "error": "boom",
        "id": "r2",
    }
    parsed = parse_rpc_response(payload)
    assert isinstance(parsed, RpcErrorResponse)
    assert parsed.error == "boom"


def test_rpc_session_state_to_from_json_round_trip() -> None:
    state = RpcSessionState(
        session_id="session-1",
        thinking_level="medium",
        is_streaming=False,
        is_compacting=True,
        steering_mode="one-at-a-time",
        follow_up_mode="all",
        message_count=3,
        pending_message_count=1,
        auto_compaction_enabled=False,
        model={"id": "claude-3-5-sonnet"},
        session_file="/tmp/session.jsonl",
        session_name="my session",
    )
    wire = state.to_json()
    # Pi camelCase keys on the wire.
    assert wire["thinkingLevel"] == "medium"
    assert wire["isStreaming"] is False
    assert wire["isCompacting"] is True
    assert wire["steeringMode"] == "one-at-a-time"
    assert wire["followUpMode"] == "all"
    assert wire["sessionId"] == "session-1"
    assert wire["sessionFile"] == "/tmp/session.jsonl"
    assert wire["sessionName"] == "my session"
    assert wire["autoCompactionEnabled"] is False
    assert wire["messageCount"] == 3
    assert wire["pendingMessageCount"] == 1
    parsed = RpcSessionState.from_json(wire)
    assert parsed == state


def test_rpc_session_state_default_to_json_shape_matches_pi() -> None:
    """A default RpcSessionState produces the 13-field Pi camelCase
    shape — Sprint 6h₂ (P-264) added ``autoRetryEnabled``."""

    wire = RpcSessionState().to_json()
    expected_keys = {
        "model",
        "thinkingLevel",
        "isStreaming",
        "isCompacting",
        "steeringMode",
        "followUpMode",
        "sessionFile",
        "sessionId",
        "sessionName",
        "autoCompactionEnabled",
        "autoRetryEnabled",
        "messageCount",
        "pendingMessageCount",
    }
    assert set(wire.keys()) == expected_keys


def test_parse_rpc_command_accepts_optional_id() -> None:
    cmd = parse_rpc_command({"type": "abort", "id": "req_42"})
    assert cmd.id == "req_42"


def test_parse_rpc_command_set_model_camel_case_field() -> None:
    cmd = parse_rpc_command(
        {"type": "set_model", "provider": "anthropic", "modelId": "claude-3-5"}
    )
    assert isinstance(cmd, RpcCommandSetModel)
    assert cmd.model_id == "claude-3-5"
