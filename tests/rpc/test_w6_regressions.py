"""Sprint 6d W6 regression suite (ADR-0058).

Pins must-fix outcomes from the W4 code review + W5 Pi-parity audit so a
future PR that reintroduces the drift trips a named test.

Roster (Pi parity / Sprint 6d):

- **P-115 BLOCKING** — ``_handle_bash`` emits the 4/5-key Pi ``BashResult``
  shape (``output``, ``exitCode``, ``cancelled``, ``truncated``,
  optional ``fullOutputPath``).
- **W4 M1** — ``_handle_get_state`` resolves ``session_file`` via the
  ``JsonlSessionStorage._file_path`` attribute.
- **P-116** — ``is_streaming`` is True for every non-idle harness phase
  (turn, compaction, branch_summary).
- **P-117** — ``_handle_new_session`` rejects ``parent_session`` with a
  Sprint-6f deferral error envelope.
- **P-118** — ``_handle_get_state`` reads only public ``AgentHarness``
  properties — no ``_``-prefixed attribute access.
- **P-119 / W4 m2** — ``_handle_prompt`` logs synchronous failures to
  stderr instead of silently suppressing them.
- **P-120** — Parse failures always return ``command="parse"`` regardless
  of the user-claimed ``type``.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
from aelix_agent_core.session.session import Session
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import (
    _handle_bash,
    _handle_command,
    _handle_get_state,
    _handle_new_session,
    _handle_prompt,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandBash,
    RpcCommandGetState,
    RpcCommandNewSession,
    RpcCommandPrompt,
    RpcErrorResponse,
    RpcSuccessResponse,
)


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


def _make_harness(**kwargs: Any) -> AgentHarness:
    options = AgentHarnessOptions(
        model=Model(id="mock", provider="mock"),
        stream_fn=_quiet_stream_fn(),
        **kwargs,
    )
    return AgentHarness(options)


# === P-115 BLOCKING — bash 4/5-key Pi BashResult shape =========================


async def test_handle_bash_returns_pi_4_key_shape(tmp_path: Path) -> None:
    """P-115 — ``_handle_bash`` emits Pi ``BashResult`` ``{output, exitCode,
    cancelled, truncated}`` for the unspilled case (no ``fullOutputPath``).
    """

    harness = _make_harness(cwd=str(tmp_path))
    response = await _handle_bash(
        harness, RpcCommandBash(command="echo hi", id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    data = response.data
    assert isinstance(data, dict)
    # Required keys exactly.
    assert set(data.keys()) >= {"output", "exitCode", "cancelled", "truncated"}
    # Forbidden legacy keys.
    assert "truncation" not in data
    assert data["cancelled"] is False
    assert isinstance(data["truncated"], bool)
    assert data["exitCode"] == 0
    assert "hi" in data["output"]
    await harness.dispose()


async def test_handle_bash_omits_full_output_path_when_absent(
    tmp_path: Path,
) -> None:
    """P-115 — ``fullOutputPath`` is OMITTED (not emitted as ``None``) when
    the executor did not spill to disk."""

    harness = _make_harness(cwd=str(tmp_path))
    response = await _handle_bash(
        harness, RpcCommandBash(command="echo small", id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert isinstance(response.data, dict)
    assert "fullOutputPath" not in response.data
    await harness.dispose()


# === W4 M1 — session_file resolves via JsonlSessionStorage._file_path =========


async def test_get_state_session_file_resolves_via_file_path(
    tmp_path: Path,
) -> None:
    """W4 M1 — ``RpcSessionState.sessionFile`` is the storage's actual
    ``_file_path``. The attribute lookup was previously ``_path`` and
    always returned ``None``.
    """

    fs = LocalFileSystem()
    storage = await JsonlSessionStorage.create(
        fs,
        str(tmp_path / "session.jsonl"),
        cwd=str(tmp_path),
        session_id="sess-w6",
    )
    session = Session(storage)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            session=session,
        )
    )
    response = await _handle_get_state(
        harness, RpcCommandGetState(id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert isinstance(response.data, dict)
    session_file = response.data["sessionFile"]
    assert isinstance(session_file, str)
    assert session_file.endswith("session.jsonl")
    await harness.dispose()


# === P-116 — is_streaming covers every non-idle phase ==========================


async def test_is_streaming_true_during_compaction_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P-116 — ``is_streaming`` reports True for any non-idle phase, not
    just ``"turn"`` (the prior proxy missed tool execution + compaction).
    """

    harness = _make_harness()
    # Force the phase machine into ``compaction`` (no real compact loop
    # required for the read-only get_state check).
    harness._phase = "compaction"
    response = await _handle_get_state(
        harness, RpcCommandGetState(id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert isinstance(response.data, dict)
    assert response.data["isStreaming"] is True
    assert response.data["isCompacting"] is True
    await harness.dispose()


async def test_is_streaming_false_when_idle() -> None:
    harness = _make_harness()
    response = await _handle_get_state(
        harness, RpcCommandGetState(id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert isinstance(response.data, dict)
    assert response.data["isStreaming"] is False
    await harness.dispose()


# === P-117 — new_session rejects parent_session ================================


async def test_new_session_rejects_parent_session_with_deferral_error() -> None:
    """P-117 — explicit error envelope when caller supplies ``parent_session``
    (Sprint 6f session-tree navigation per ADR-0058).
    """

    harness = _make_harness()
    response = await _handle_new_session(
        harness,
        RpcCommandNewSession(parent_session="/some/path.jsonl", id="r"),
    )
    assert isinstance(response, RpcErrorResponse)
    assert response.command == "new_session"
    assert "parent_session" in response.error
    assert "ADR-0058" in response.error
    await harness.dispose()


async def test_new_session_without_parent_returns_cancelled_false() -> None:
    """P-117 control — None parent path still routes through the
    cancel-aware happy path.
    """

    harness = _make_harness()
    response = await _handle_new_session(
        harness, RpcCommandNewSession(parent_session=None, id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert response.data == {"cancelled": False}
    await harness.dispose()


# === P-118 — get_state reads no private harness attributes =====================


async def test_get_state_uses_only_public_harness_surface() -> None:
    """P-118 — the ``_handle_get_state`` body must not access any
    ``_``-prefixed attribute on the harness OBJECT (queue-mode reads via
    `harness._steering_queue.mode` are allowed because that's a public
    field on the queue, but harness-side reads must go through the new
    ``pending_message_count`` / ``session_file`` / ``session_name``
    properties).
    """

    import ast
    import inspect

    src = inspect.getsource(_handle_get_state)
    tree = ast.parse(src)
    bad: list[str] = []
    for node in ast.walk(tree):
        # Direct ``harness._foo`` reads are forbidden by P-118.
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "harness"
            and node.attr.startswith("_")
        ):
            bad.append(f"harness.{node.attr}")
    assert not bad, (
        f"_handle_get_state should not read private harness attrs; "
        f"found: {sorted(set(bad))}"
    )


# === P-119 / W4 m2 — prompt synchronous failure logs to stderr =================


async def test_handle_prompt_logs_failure_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P-119 — synchronous failures during the prompt task must reach
    stderr (previously suppressed via ``contextlib.suppress(Exception)``).
    """

    harness = _make_harness()

    async def _raise(message: str, *, source: str = "interactive") -> Any:
        raise RuntimeError("simulated failure during prompt")

    harness.prompt = _raise  # type: ignore[assignment]
    response = await _handle_prompt(
        harness, RpcCommandPrompt(message="x", id="r")
    )
    # Response is emitted IMMEDIATELY — Pi parity (the error surfaces
    # through the event/log channel, not the response envelope).
    assert isinstance(response, RpcSuccessResponse)
    # Drain the fire-and-forget task so the print() lands.
    pending = list(harness._pending_tasks)
    for task in pending:
        with contextlib.suppress(Exception):
            await task
    captured = capsys.readouterr()
    assert "prompt task failed" in captured.err
    assert "simulated failure" in captured.err
    await harness.dispose()


# === P-120 — parse failures always emit command="parse" ========================


async def test_parse_error_always_uses_parse_command() -> None:
    """P-120 — even when the malformed payload claimed a valid ``type``,
    the parse-error envelope uses ``command="parse"`` (Pi parity).
    """

    dispatch = build_dispatch_table()
    harness = _make_harness()
    # Valid-looking ``type`` but missing required ``provider`` field.
    response = await _handle_command(
        harness, {"type": "set_model", "provider": None}, dispatch
    )
    assert isinstance(response, RpcErrorResponse)
    assert response.command == "parse"
    await harness.dispose()


async def test_parse_error_with_unknown_type_uses_parse_command() -> None:
    dispatch = build_dispatch_table()
    harness = _make_harness()
    response = await _handle_command(
        harness, {"type": "frobnicate", "foo": "bar"}, dispatch
    )
    assert isinstance(response, RpcErrorResponse)
    assert response.command == "parse"
    await harness.dispose()


# === P-118 — harness exposes the new public properties =========================


async def test_harness_exposes_pending_message_count_property() -> None:
    """P-118 — ``pending_message_count`` is a public property summing
    steer + follow_up queue lengths.
    """

    harness = _make_harness()
    assert harness.pending_message_count == 0
    await harness.steer("one")
    assert harness.pending_message_count == 1
    await harness.follow_up("two")
    assert harness.pending_message_count == 2
    await harness.dispose()


async def test_harness_session_file_property_none_without_session() -> None:
    harness = _make_harness()
    assert harness.session_file is None
    await harness.dispose()


async def test_harness_session_name_property_reads_cached_value() -> None:
    harness = _make_harness()
    assert harness.session_name is None
    harness._cached_session_name = "demo"
    assert harness.session_name == "demo"
    await harness.dispose()
