"""Bug #122 regression — post-``/resume`` stats must reflect the resumed
session, not read ZERO / stale.

Root cause: a freshly built :class:`AgentHarness` never seeds
``_state.messages`` from its session (``options.initial_messages`` is the only
seam and no factory passes it). On ``/resume``,
``AgentSessionRuntime.switch_session`` → ``_finish_session_replacement`` ran with
``setup=None``, so the message-rebuild was skipped and the new harness started
with ``_state.messages == []``. Then ``get_session_stats`` /
``_get_context_usage_safe`` (which read ``_state.messages`` directly) reported
ZERO for /context, /cost, /session, /stats until the next turn.

The fix moves the ``_state.messages`` rebuild OUT of the ``if setup is not None:``
guard so it runs on EVERY replacement (resume included). It is a REPLACE
assignment, so it is idempotent (a re-switch does not accumulate) and the next
turn only ``extend``s the delta on top.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
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


def _new_harness(session: Session | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _target_with_history(
    repo: JsonlSessionRepo, cwd: str
) -> tuple[Session, Any]:
    """A target session carrying 2 persisted messages (1 user + 1 assistant
    with a token/cost ``usage`` payload) so the stats are non-zero."""

    target = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    await target.append_message(UserMessage(content=[TextContent(text="hello")]))
    await target.append_message(
        AssistantMessage(
            content=[TextContent(text="hi there")],
            stop_reason="end_turn",
            usage={"input": 120, "output": 40, "cost": {"total": 0.02}},
        )
    )
    return target, await target.get_metadata()


async def test_switch_session_seeds_state_messages_from_persisted_history(
    tmp_path: Path,
) -> None:
    """THE REGRESSION: after ``switch_session`` the resumed session's persisted
    history is in ``_state.messages`` and ``get_session_stats`` is non-zero."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    _target, target_metadata = await _target_with_history(repo, str(tmp_path))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )
    # Pre-condition (the bug's starting point): a fresh harness on an empty
    # source session has NO in-memory messages.
    assert runtime.harness._state.messages == []

    await runtime.switch_session(target_metadata.path)

    # Post-fix: the persisted history seeds the new harness's live state.
    assert len(runtime.harness._state.messages) == 2

    stats = await runtime.harness.get_session_stats()
    assert stats.total_messages == 2  # was 0 before the fix
    assert stats.user_messages == 1
    assert stats.assistant_messages == 1
    assert stats.tokens.input == 120
    assert stats.tokens.output == 40
    assert stats.tokens.total == 160
    assert stats.cost == pytest.approx(0.02)


async def test_switch_session_stats_are_stable_and_not_double_counted(
    tmp_path: Path,
) -> None:
    """No double-count: the read is idempotent, a re-switch REPLACES (does not
    accumulate), and one real turn only EXTENDS the delta on top of the
    persisted history (counted exactly once)."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    _target, target_metadata = await _target_with_history(repo, str(tmp_path))

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    runtime = AgentSessionRuntime(
        _new_harness(session=source), _factory, repo=repo, fs=fs
    )

    await runtime.switch_session(target_metadata.path)
    baseline = len(runtime.harness._state.messages)
    assert baseline == 2

    # (resume → read) is stable: reading stats does not mutate the transcript.
    stats1 = await runtime.harness.get_session_stats()
    stats2 = await runtime.harness.get_session_stats()
    assert stats1.total_messages == stats2.total_messages == 2

    # A second switch to the SAME session REPLACES the message list — it must
    # NOT accumulate into 4 (the fix is an assignment, not an append).
    await runtime.switch_session(target_metadata.path)
    assert len(runtime.harness._state.messages) == 2

    # (resume → one turn → read): a real turn extends the transcript; the
    # persisted history is still counted exactly once, so total minus the turn
    # delta returns to the resumed baseline.
    await runtime.harness.prompt("continue")
    after = len(runtime.harness._state.messages)
    delta = after - baseline
    assert delta > 0  # the turn appended at least the user + assistant messages

    stats_after = await runtime.harness.get_session_stats()
    assert stats_after.total_messages == after  # stats track live state
    assert stats_after.total_messages - delta == baseline  # persisted counted once
