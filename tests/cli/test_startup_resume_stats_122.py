"""Bug #122 (startup) — ``aelix --continue``/``--resume`` must show non-zero
stats for a resumed session BEFORE the first turn.

The startup harness is built directly (``_harness_factory`` → ``AgentHarness``),
bypassing ``AgentSessionRuntime._finish_session_replacement`` (which seeds
``_state.messages`` on in-session swaps). A fresh harness never seeds
``AgentHarnessOptions.initial_messages`` from its session, so WITHOUT
``entry._seed_startup_messages`` a startup ``--continue``/``--resume`` (also
``--session``/``--fork``) into a session with history reads ZERO for /context,
/cost, /session, /stats until the first turn. This is the startup analogue of the
in-session #122 fix (tests/runtime/test_switch_session_stats_122.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.cli.entry import _seed_startup_messages


def _stream() -> Any:
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


def _startup_harness(session: Session) -> AgentHarness:
    """Built exactly as the startup factory builds it: bound to the resumed
    session but WITHOUT ``initial_messages`` (the factory never sets it)."""

    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _session_with_history(repo: JsonlSessionRepo, cwd: str) -> Session:
    session = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    await session.append_message(UserMessage(content=[TextContent(text="hello")]))
    await session.append_message(
        AssistantMessage(
            content=[TextContent(text="hi there")],
            stop_reason="end_turn",
            usage={"input": 120, "output": 40, "cost": {"total": 0.02}},
        )
    )
    return session


async def test_startup_seed_reflects_resumed_history(tmp_path: Path) -> None:
    """THE REGRESSION: a startup harness on a session WITH history reads zero
    stats until seeded; after ``_seed_startup_messages`` it reflects the
    persisted totals (non-zero) with no turn run."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await _session_with_history(repo, str(tmp_path))

    harness = _startup_harness(session)
    # Pre-condition — the bug WITHOUT the fix: a fresh startup harness has NO
    # in-memory transcript, so /stats read ZERO even though the session on disk
    # carries two messages. (This is exactly what the seed call resolves; no-op
    # the seed and the post-assertions below fail.)
    assert harness.state.messages == []
    pre = await harness.get_session_stats()
    assert pre.total_messages == 0
    assert pre.tokens.total == 0
    assert pre.cost == 0.0

    # The fix: seed from the resumed session's persisted history.
    await _seed_startup_messages(harness, session)

    assert len(harness.state.messages) == 2
    stats = await harness.get_session_stats()
    assert stats.total_messages == 2  # was 0 before the seed
    assert stats.user_messages == 1
    assert stats.assistant_messages == 1
    assert stats.tokens.input == 120
    assert stats.tokens.output == 40
    assert stats.tokens.total == 160
    assert stats.cost == pytest.approx(0.02)


async def test_startup_seed_is_noop_for_fresh_session(tmp_path: Path) -> None:
    """A cold start (fresh empty session) stays empty — the guard skips the
    assignment so a normal launch is unaffected."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    harness = _startup_harness(session)
    await _seed_startup_messages(harness, session)

    assert harness.state.messages == []
    stats = await harness.get_session_stats()
    assert stats.total_messages == 0
