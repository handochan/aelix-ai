"""Pi parity: RpcClient lifecycle against a stub stdin/stdout server.

Spawns ``sys.executable -c "<inline stub>"`` so we exercise the real
``asyncio.create_subprocess_exec`` path without needing the full Aelix
CLI. The stub echoes RPC commands as success responses.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest
from aelix_coding_agent.rpc.rpc_client import (
    RpcClient,
    RpcClientError,
    RpcClientOptions,
)

# Stub server: reads JSONL commands on stdin and emits a success response
# for each, echoing the ``id`` for correlation. Fakes a couple of
# specific commands' data shapes so the typed client methods can unwrap.
_STUB_SERVER = textwrap.dedent(
    """
    import json
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except Exception:
            sys.exit(0)
        cmd_type = cmd.get("type", "")
        cmd_id = cmd.get("id")
        if cmd_type == "get_state":
            data = {
                "model": None,
                "thinkingLevel": "off",
                "isStreaming": False,
                "isCompacting": False,
                "steeringMode": "all",
                "followUpMode": "all",
                "sessionFile": None,
                "sessionId": "stub",
                "sessionName": None,
                "autoCompactionEnabled": True,
                "messageCount": 0,
                "pendingMessageCount": 0,
            }
            response = {"type": "response", "command": cmd_type, "success": True, "data": data, "id": cmd_id}
        elif cmd_type == "bash":
            response = {
                "type": "response",
                "command": "bash",
                "success": True,
                "data": {"output": "hello", "exitCode": 0},
                "id": cmd_id,
            }
        elif cmd_type == "steer":
            response = {
                "type": "response",
                "command": "steer",
                "success": False,
                "error": "steer not implemented in Sprint 6d (ADR-0058)",
                "id": cmd_id,
            }
        else:
            response = {"type": "response", "command": cmd_type, "success": True, "id": cmd_id}
        sys.stdout.write(json.dumps(response) + "\\n")
        sys.stdout.flush()
        # Optionally emit an agent_end event after prompt so wait_for_idle resolves.
        if cmd_type == "prompt":
            sys.stdout.write(json.dumps({"type": "agent_end", "messages": []}) + "\\n")
            sys.stdout.flush()
    """
)


def _stub_options() -> RpcClientOptions:
    return RpcClientOptions(args=["-c", _STUB_SERVER, "--", "--mode", "rpc-stub"])


def _stub_client() -> RpcClient:
    """RpcClient configured to launch the inline stub instead of ``-m aelix``."""

    return _StubClient()


class _StubClient(RpcClient):
    """Override ``_build_argv`` to spawn the inline stub server."""

    def __init__(self) -> None:
        super().__init__(RpcClientOptions())

    def _build_argv(self) -> list[str]:
        return [sys.executable, "-c", _STUB_SERVER]


async def test_start_and_stop_clean_round_trip() -> None:
    client = _stub_client()
    await client.start()
    try:
        state = await client.get_state()
        assert state.session_id == "stub"
    finally:
        await client.stop()


async def test_get_state_returns_session_state() -> None:
    client = _stub_client()
    await client.start()
    try:
        state = await client.get_state()
        assert state.thinking_level == "off"
        assert state.is_streaming is False
    finally:
        await client.stop()


async def test_bash_returns_data_payload() -> None:
    client = _stub_client()
    await client.start()
    try:
        result = await client.bash("echo hello")
        assert result["output"] == "hello"
        assert result["exitCode"] == 0
    finally:
        await client.stop()


async def test_deferred_command_with_data_raises_rpc_client_error() -> None:
    """Deferred command that returns ``data`` surfaces as :class:`RpcClientError`.

    The Pi parity invariant is asymmetric: fire-and-forget commands
    (steer / follow_up / abort / set_thinking_level / set_*) await the
    response but ignore its content, so a server-side ``success: false``
    is silently swallowed by the typed client method. Commands that
    return ``data`` (set_model, cycle_model, etc.) unwrap via
    :meth:`RpcClient._unwrap` which raises :class:`RpcClientError` on
    error envelopes.
    """

    # Customise stub to error on ``set_model`` (a deferred command that
    # returns data on success — server returns error → client raises).
    class _ErrorOnSetModel(_StubClient):
        def _build_argv(self) -> list[str]:
            stub = textwrap.dedent(
                """
                import json
                import sys

                for line in sys.stdin:
                    line = line.strip()
                    if not line:
                        continue
                    cmd = json.loads(line)
                    response = {
                        "type": "response",
                        "command": "set_model",
                        "success": False,
                        "error": "set_model not implemented (ADR-0058)",
                        "id": cmd.get("id"),
                    }
                    sys.stdout.write(json.dumps(response) + "\\n")
                    sys.stdout.flush()
                """
            )
            return [sys.executable, "-c", stub]

    client = _ErrorOnSetModel()
    await client.start()
    try:
        with pytest.raises(RpcClientError) as excinfo:
            await client.set_model("anthropic", "claude-3-5")
        assert "ADR-0058" in str(excinfo.value)
        assert excinfo.value.command == "set_model"
    finally:
        await client.stop()


async def test_prompt_then_wait_for_idle_resolves_on_agent_end() -> None:
    """Stub emits ``agent_end`` after each prompt; wait_for_idle resolves.

    The listener MUST be installed before the prompt is sent so the
    ``agent_end`` event isn't missed in the race window between
    ``prompt()`` returning and ``wait_for_idle()`` subscribing.
    """

    client = _stub_client()
    await client.start()
    try:
        idle_task = asyncio.create_task(client.wait_for_idle(timeout_ms=2_000))
        # Give the listener a chance to register.
        await asyncio.sleep(0)
        await client.prompt("hi")
        await idle_task
    finally:
        await client.stop()


async def test_prompt_and_wait_collects_events() -> None:
    client = _stub_client()
    await client.start()
    try:
        events = await client.prompt_and_wait("hi", timeout_ms=2_000)
        types = [e.get("type") for e in events]
        assert "agent_end" in types
    finally:
        await client.stop()


async def test_on_event_receives_emitted_events() -> None:
    client = _stub_client()
    await client.start()
    received: list[dict] = []
    unsubscribe = client.on_event(received.append)
    try:
        await client.prompt("hi")
        # Wait briefly for the agent_end to arrive.
        for _ in range(20):
            if any(e.get("type") == "agent_end" for e in received):
                break
            await asyncio.sleep(0.05)
        assert any(e.get("type") == "agent_end" for e in received)
    finally:
        unsubscribe()
        await client.stop()


async def test_id_counter_is_monotonic() -> None:
    """Pi parity: requestId counter increments per send."""

    client = _stub_client()
    await client.start()
    try:
        await client.get_state()
        await client.get_state()
        await client.get_state()
        # After three sends the counter is at 4 (next value).
        assert next(client._next_id) == 4
    finally:
        await client.stop()


async def test_double_start_raises() -> None:
    client = _stub_client()
    await client.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            await client.start()
    finally:
        await client.stop()
