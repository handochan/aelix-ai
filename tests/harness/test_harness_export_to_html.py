"""Sprint 6h₃ (ADR-0073, P-270/P-279/P-281) — harness ``export_to_html``
integration tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
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


async def _make_jsonl_session_harness(
    tmp_path: Path,
    initial_messages: list[Any] | None = None,
    session_id: str = "export-test",
) -> tuple[AgentHarness, str]:
    """Build a harness with a real JSONL-backed session for Pi error parity.

    Pi parity (P-279): the harness's ``export_to_html`` requires a
    non-None session + an on-disk JSONL file. In-memory sessions raise.
    """

    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
    from aelix_agent_core.session.session import Session

    fs = LocalFileSystem()
    file_path = str(tmp_path / f"{session_id}.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(tmp_path), session_id=session_id
    )
    session = Session(storage)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
            session=session,
            initial_messages=initial_messages or [],
        )
    )
    return harness, file_path


async def test_export_to_html_in_memory_session_raises(tmp_path: Path) -> None:
    """Pi parity (P-279 W6): export raises on in-memory session
    (no JSONL backing file).
    """

    harness = _make_harness()
    try:
        with pytest.raises(RuntimeError, match="in-memory"):
            harness.export_to_html()
    finally:
        await harness.dispose()


async def test_export_to_html_default_path_uses_pi_shape(
    tmp_path: Path,
) -> None:
    """Pi parity (P-281 W6): omitted ``output_path`` → cwd-relative
    ``aelix-session-<basename>.html``.
    """

    import os

    harness, _file_path = await _make_jsonl_session_harness(tmp_path)
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        path = harness.export_to_html()
        p = Path(path)
        assert p.exists()
        # Pi-shape default: aelix-session-<basename>.html
        assert p.name == "aelix-session-export-test.html"
        body = p.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in body
    finally:
        os.chdir(cwd)
        await harness.dispose()


async def test_export_to_html_writes_to_supplied_path(tmp_path: Path) -> None:
    """Pi parity: ``output_path`` → file at that exact path."""

    out = tmp_path / "session.html"
    harness, _file_path = await _make_jsonl_session_harness(tmp_path)
    try:
        path = harness.export_to_html(str(out))
        assert path == str(out.resolve())
        assert out.exists()
    finally:
        await harness.dispose()


async def test_export_to_html_renders_initial_messages(tmp_path: Path) -> None:
    """Pi parity: ``state.messages`` flows into the emitter."""

    msgs = [
        UserMessage(content=[TextContent(text="prompt")]),
        AssistantMessage(content=[TextContent(text="reply")]),
    ]
    out = tmp_path / "session.html"
    harness, _file_path = await _make_jsonl_session_harness(
        tmp_path, initial_messages=msgs
    )
    try:
        path = harness.export_to_html(str(out))
        body = Path(path).read_text(encoding="utf-8")
        assert "prompt" in body
        assert "reply" in body
        assert 'section class="user"' in body
        assert 'section class="assistant"' in body
    finally:
        await harness.dispose()


async def test_export_to_html_uses_cached_session_name_as_title(
    tmp_path: Path,
) -> None:
    """Pi parity: ``cachedSessionName`` becomes the HTML ``<title>`` /
    ``<h1>``. Falls back to the default ``"Aelix Session"`` when unset.
    """

    out = tmp_path / "session.html"
    harness, _file_path = await _make_jsonl_session_harness(tmp_path)
    try:
        # Sprint 5b §E exposed ``_cached_session_name`` as the sync read
        # cache; ``set_session_name`` populates it through the action.
        harness._cached_session_name = "My Project"  # noqa: SLF001
        path = harness.export_to_html(str(out))
        body = Path(path).read_text(encoding="utf-8")
        assert "<title>My Project</title>" in body
        assert "<h1>My Project</h1>" in body
    finally:
        await harness.dispose()


async def test_export_to_html_default_title_when_session_name_unset(
    tmp_path: Path,
) -> None:
    """Pi parity: default ``"Aelix Session"`` title."""

    out = tmp_path / "session.html"
    harness, _file_path = await _make_jsonl_session_harness(tmp_path)
    try:
        path = harness.export_to_html(str(out))
        body = Path(path).read_text(encoding="utf-8")
        assert "<title>Aelix Session</title>" in body
    finally:
        await harness.dispose()
