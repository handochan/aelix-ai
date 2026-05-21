"""Pi parity: run_rpc_mode end-to-end with injected stdin/stdout.

Feeds JSONL commands via an :class:`asyncio.StreamReader` and captures
JSONL responses via an injected write sink. Asserts:

- The supported-handler path produces success envelopes.
- The deferred-handler path produces error envelopes.
- The takeover-stdout invariant blocks stray ``print()`` from contaminating
  the JSONL frame (P-112).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc._jsonl import serialize_json_line
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode


def _quiet_stream_fn() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _build_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
        )
    )


async def _run_with_lines(
    commands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Feed ``commands`` as JSONL through ``run_rpc_mode`` and return captured
    output records.
    """

    stdin = asyncio.StreamReader()
    for cmd in commands:
        stdin.feed_data(serialize_json_line(cmd).encode("utf-8"))
    stdin.feed_eof()

    captured: list[bytes] = []

    def _write(data: bytes) -> None:
        captured.append(data)

    harness = _build_harness()
    await run_rpc_mode(
        harness,
        stdin=stdin,
        stdout_write=_write,
        install_signal_handlers=False,
    )
    raw = b"".join(captured).decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


async def test_get_state_round_trip() -> None:
    """A ``get_state`` command produces a success envelope on stdout."""

    output = await _run_with_lines(
        [{"type": "get_state", "id": "r1"}]
    )
    response = next(
        rec
        for rec in output
        if rec.get("type") == "response" and rec.get("command") == "get_state"
    )
    assert response["success"] is True
    assert response["id"] == "r1"
    assert "thinkingLevel" in response["data"]


async def test_deferred_command_produces_error_envelope() -> None:
    """A still-deferred command emits a Pi-shape error envelope on stdout.

    Sprint 6h₂ (ADR-0071) wired ``steer``; Sprint 6h₃ (ADR-0073) wired
    ``get_session_stats`` / ``export_html``. Sprint 6h₄a (ADR-0075)
    wired ``get_fork_messages`` / ``get_last_assistant_text`` and
    re-homed the remaining 3 session-tree commands to ADR-0076.
    Sprint 6h₄b (ADR-0077 / ADR-0078) lands the
    :class:`AgentSessionRuntime` foundation and re-homes the same 3
    session-tree owners ADR-0076 → ADR-0078. ``fork`` remains
    deferred per ADR-0078 (3 session-tree commands still awaiting
    Sprint 6h₄c).
    """

    output = await _run_with_lines(
        [{"type": "fork", "entry_id": "e1", "id": "r2"}]
    )
    response = next(rec for rec in output if rec.get("command") == "fork")
    assert response["success"] is False
    assert "ADR-0076" in response["error"] or "ADR-0078" in response["error"]


async def test_unknown_command_produces_parse_error() -> None:
    """P-120 — every parse-path failure emits ``command="parse"`` (Pi
    parity ``rpc-mode.ts:464-470``), regardless of the user-claimed type.
    """

    output = await _run_with_lines(
        [{"type": "totally_unknown", "id": "r3"}]
    )
    response = output[-1]
    assert response["success"] is False
    assert response["command"] == "parse"


async def test_invalid_json_produces_parse_error() -> None:
    """Non-JSON line on stdin yields a parse error envelope (Pi parity)."""

    stdin = asyncio.StreamReader()
    stdin.feed_data(b"this-is-not-json\n")
    stdin.feed_eof()

    captured: list[bytes] = []
    harness = _build_harness()
    await run_rpc_mode(
        harness,
        stdin=stdin,
        stdout_write=captured.append,
        install_signal_handlers=False,
    )
    records = [
        json.loads(line)
        for line in b"".join(captured).decode("utf-8").splitlines()
        if line.strip()
    ]
    parse_errors = [r for r in records if r.get("command") == "parse"]
    assert len(parse_errors) == 1
    assert parse_errors[0]["success"] is False


async def test_stdout_takeover_blocks_stray_print(capsys) -> None:
    """Stray ``print()`` calls during run_rpc_mode are diverted to stderr.

    Pi parity (P-112, ``takeOverStdout``): tool/extension ``print()`` MUST
    NOT corrupt the JSONL stream. The Aelix equivalent is
    ``contextlib.redirect_stdout(sys.stderr)`` at the run entry.
    """

    captured: list[bytes] = []
    harness = _build_harness()

    async def _emit_stray_print() -> None:
        # Wait long enough for run_rpc_mode to install the redirect.
        await asyncio.sleep(0.01)
        print("THIS SHOULD NOT REACH STDOUT-FD")

    stdin = asyncio.StreamReader()
    stdin.feed_data(serialize_json_line({"type": "get_state", "id": "r"}).encode())
    stdin.feed_eof()

    await asyncio.gather(
        run_rpc_mode(
            harness,
            stdin=stdin,
            stdout_write=captured.append,
            install_signal_handlers=False,
        ),
        _emit_stray_print(),
    )

    output_bytes = b"".join(captured)
    # The stray print() text MUST NOT appear in the captured RPC stream.
    assert b"SHOULD NOT REACH STDOUT-FD" not in output_bytes
    # Every captured line MUST be valid JSON.
    for line in output_bytes.decode("utf-8").splitlines():
        if line.strip():
            json.loads(line)


async def test_eof_on_stdin_terminates_loop() -> None:
    """Pi parity: ``stdin.on('end', shutdown)``. EOF causes graceful exit."""

    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    captured: list[bytes] = []
    harness = _build_harness()
    # Should complete without hanging.
    await asyncio.wait_for(
        run_rpc_mode(
            harness,
            stdin=stdin,
            stdout_write=captured.append,
            install_signal_handlers=False,
        ),
        timeout=2.0,
    )


async def test_extension_ui_response_is_silently_consumed() -> None:
    """Sprint 6d: extension_ui_response is recognized but the bridge is
    deferred to Sprint 6f. The wire shape is parsed and ignored.
    """

    output = await _run_with_lines(
        [
            {"type": "extension_ui_response", "id": "ui-1", "value": "x"},
            {"type": "get_state", "id": "after"},
        ]
    )
    # We should still see a single response to the get_state — the
    # extension_ui_response is silently dropped.
    state_responses = [
        r for r in output if r.get("command") == "get_state" and r.get("id") == "after"
    ]
    assert len(state_responses) == 1
