"""Sprint 6h₆ (Phase 5a-ii, ADR-0089) — ``modes/print_mode.py`` tests.

Covers:
  - Text-mode terminal printout of TextContent blocks.
  - JSON-mode line-delimited event streaming.
  - Error-stop-reason path → exit code 1 + stderr emit.
  - Aborted-stop-reason path → exit code 1.
  - Initial-message + residual messages loop integration.

The harness is driven by a mock ``stream_fn`` that emits a single
TextContent assistant message — enough to exercise every print-mode
branch without external providers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import (
    JsonlSessionRepo,
    LocalFileSystem,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.modes.print_mode import run_print_mode


def _ok_stream(reply: str = "hello-from-mock") -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=reply)],
                stop_reason="end_turn",
            )
        )

    return fn


def _error_stream(message: str = "boom") -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[],
                stop_reason="error",
                error_message=message,
            )
        )

    return fn


def _aborted_stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[],
                stop_reason="aborted",
            )
        )

    return fn


def _new_harness(stream_fn: Any) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=stream_fn,
        )
    )


def _new_runtime(harness: AgentHarness) -> AgentSessionRuntime:
    async def _noop(_s: Any) -> AgentHarness:
        return harness

    return AgentSessionRuntime(
        harness,
        _noop,
        repo=JsonlSessionRepo(fs=LocalFileSystem()),
        fs=LocalFileSystem(),
    )


# === Text-mode happy path =====================================================


async def test_text_mode_prints_text_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _new_harness(_ok_stream("hello"))
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="text",
        messages=[],
        initial_message="ping",
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "hello\n" in captured.out


async def test_text_mode_no_initial_no_messages_no_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _new_harness(_ok_stream())
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="text",
        messages=[],
        initial_message=None,
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    # No turn was driven — nothing to print.
    assert captured.out == ""


async def test_text_mode_residual_messages_loop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _new_harness(_ok_stream("last-reply"))
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="text",
        messages=["second", "third"],
        initial_message="first",
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    # The last assistant message corresponds to the LAST prompt.
    assert "last-reply\n" in captured.out


# === Text-mode error stop reason ============================================


async def test_text_mode_error_stop_reason_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _new_harness(_error_stream("api-failure"))
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="text",
        messages=[],
        initial_message="trigger-error",
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "api-failure" in captured.err


async def test_text_mode_aborted_stop_reason_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _new_harness(_aborted_stream())
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="text",
        messages=[],
        initial_message="trigger-abort",
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    # Default fallback message when error_message is None.
    assert "Request aborted" in captured.err


# === JSON-mode streaming ====================================================


async def test_json_mode_emits_events_one_per_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _new_harness(_ok_stream("json-reply"))
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="json",
        messages=[],
        initial_message="json-prompt",
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    # Every non-empty line must be valid JSON.
    import json as _json

    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines, "JSON mode must emit at least one event line"
    for line in lines:
        # Should not raise.
        _json.loads(line)


async def test_json_mode_no_text_terminal_printout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """JSON mode does NOT emit the text-mode terminal printout."""

    harness = _new_harness(_ok_stream("must-not-appear-plain"))
    runtime = _new_runtime(harness)
    await run_print_mode(
        runtime,
        mode="json",
        messages=[],
        initial_message="hi",
    )
    captured = capsys.readouterr()
    # The reply text appears INSIDE JSON; it must NOT appear as a bare
    # "must-not-appear-plain\n" line (the text-mode tail).
    bare_lines = [
        line
        for line in captured.out.splitlines()
        if line == "must-not-appear-plain"
    ]
    assert bare_lines == []


# === Exception handling =====================================================


async def test_exception_in_prompt_returns_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any exception during the prompt path is caught and returns 1."""

    async def boom_stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        raise RuntimeError("explicit failure")
        yield  # pragma: no cover — make it an async generator

    harness = _new_harness(boom_stream)
    runtime = _new_runtime(harness)
    exit_code = await run_print_mode(
        runtime,
        mode="text",
        messages=[],
        initial_message="trigger",
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "explicit failure" in captured.err
