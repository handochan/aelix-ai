"""Sprint 6h₃ (ADR-0073, P-271) — harness ``get_session_stats`` integration tests.

Pi parity: :meth:`AgentHarness.get_session_stats` returns a
:class:`SessionStats` with ``session_id`` / ``session_file`` derived
from the harness state and per-role counts from the harness's
in-memory message list.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness._session_stats import SessionStats
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
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
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _make_harness(initial_messages: list[Any] | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            initial_messages=initial_messages or [],
        )
    )


async def test_get_session_stats_empty_harness_returns_zeros() -> None:
    """Pi parity: a fresh harness with no messages → all-zero stats."""

    harness = _make_harness()
    try:
        stats = await harness.get_session_stats()
        assert isinstance(stats, SessionStats)
        assert stats.user_messages == 0
        assert stats.assistant_messages == 0
        assert stats.tool_results == 0
        assert stats.total_messages == 0
        assert stats.tokens.total == 0
        assert stats.cost == 0.0
    finally:
        await harness.dispose()


async def test_get_session_stats_counts_initial_messages() -> None:
    """Pi parity: ``state.messages`` flows into the aggregator."""

    msgs = [
        UserMessage(content=[TextContent(text="u")]),
        AssistantMessage(content=[TextContent(text="a")]),
        ToolResultMessage(tool_call_id="t", content=[TextContent(text="ok")]),
    ]
    harness = _make_harness(initial_messages=msgs)
    try:
        stats = await harness.get_session_stats()
        assert stats.user_messages == 1
        assert stats.assistant_messages == 1
        assert stats.tool_results == 1
        assert stats.total_messages == 3
    finally:
        await harness.dispose()


async def test_get_session_stats_session_file_none_when_no_session() -> None:
    """Pi parity: ``sessionFile`` is :data:`None` when no Session attached."""

    harness = _make_harness()
    try:
        stats = await harness.get_session_stats()
        assert stats.session_file is None
    finally:
        await harness.dispose()


async def test_get_session_stats_session_file_reflects_jsonl_path(
    tmp_path: Path,
) -> None:
    """Pi parity: when a :class:`JsonlSessionStorage`-backed Session is
    attached, ``sessionFile`` is the actual ``.jsonl`` path.
    """

    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
    from aelix_agent_core.session.session import Session

    fs = LocalFileSystem()
    file_path = str(tmp_path / "stats.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(tmp_path), session_id="stats-pin"
    )
    session = Session(storage)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            session=session,
        )
    )
    try:
        stats = await harness.get_session_stats()
        assert stats.session_file == file_path
        # session_id flows from the session storage metadata.
        assert stats.session_id == "stats-pin"
    finally:
        await harness.dispose()


async def test_get_session_stats_context_usage_is_none_when_model_unknown() -> None:
    """Pi parity (P-273): ``contextUsage`` is :data:`None` when the model
    has ``context_window == 0``.

    Sprint 6h₅c (ADR-0085, P-369) replaced the Sprint 6h₃ stub with the
    real Pi algorithm; the ``mock`` Model used here defaults to
    ``context_window=0`` so the helper returns :data:`None` per the Pi
    short-circuit at ``agent-session.ts:2946-2990``.
    """

    harness = _make_harness()
    try:
        stats = await harness.get_session_stats()
        assert stats.context_usage is None
    finally:
        await harness.dispose()


async def test_get_session_stats_returns_frozen_dataclass() -> None:
    """The returned :class:`SessionStats` is frozen — callers cannot
    mutate it after the fact.
    """

    import pytest

    harness = _make_harness()
    try:
        stats = await harness.get_session_stats()
        with pytest.raises((AttributeError, Exception)):
            stats.cost = 99.0  # type: ignore[misc]
    finally:
        await harness.dispose()
