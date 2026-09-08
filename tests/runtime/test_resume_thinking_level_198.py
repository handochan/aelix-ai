"""Issue #198 (in-session seam) — ``/resume``, ``/new`` and ``/fork`` must not
throw the thinking level away.

Aelix rebuilds the harness on every session swap (pi does not — its
``AgentSession`` survives ``switchSession``, so ``agent.state.thinkingLevel``
carries over for free), and ``_finish_session_replacement`` rebuilt
``_state.messages`` only. So ``/resume`` landed on the kernel default ``off``
even when the level had been set in that very process.

Two halves here: the target session's own recorded level wins, and when it has
none the live level is carried forward AND written down — otherwise ``/new`` at
``medium`` produces a session whose file says ``off``, and the headline bug
returns the next time that session is opened.

Modelled on ``tests/runtime/test_switch_session_stats_122.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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

_REASONING_MODEL = Model(
    id="m",
    api="anthropic",
    provider="anthropic",
    reasoning=True,
    thinking_level_map={"low": 2048, "medium": 8192, "high": 16384},
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


def _new_harness(
    session: Session | None = None, *, thinking_level: str | None = None
) -> AgentHarness:
    options: dict[str, Any] = {
        "model": _REASONING_MODEL,
        "stream_fn": _stream(),
        "session": session,
    }
    if thinking_level is not None:
        options["thinking_level"] = thinking_level
    return AgentHarness(AgentHarnessOptions(**options))


async def _runtime(
    repo: JsonlSessionRepo,
    fs: LocalFileSystem,
    source: Session,
    *,
    live_level: str | None = None,
) -> AgentSessionRuntime:
    """A runtime whose factory builds the NEW harness the way the product does:
    from ``AgentHarnessOptions`` alone, with NO knowledge of the level the old
    harness was running at. That ignorance is the bug under test."""

    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return AgentSessionRuntime(
        _new_harness(session=source, thinking_level=live_level),
        _factory,
        repo=repo,
        fs=fs,
    )


async def _session_at(
    repo: JsonlSessionRepo, cwd: str, *levels: str
) -> tuple[Session, Any]:
    session = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    await session.append_message(UserMessage(content=[TextContent(text="hello")]))
    for level in levels:
        await session.append_thinking_level_change(level)
    return session, await session.get_metadata()


async def _levels_on(session: Session) -> list[str]:
    return [
        e.thinking_level  # type: ignore[union-attr]
        for e in await session.get_branch()
        if e.type == "thinking_level_change"
    ]


async def test_switch_session_restores_level_from_target_session(
    tmp_path: Path,
) -> None:
    """THE REGRESSION: ``/resume`` into a session recorded at ``high`` lands at
    ``high``, not the fresh harness's ``off``."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    _target, meta = await _session_at(repo, str(tmp_path), "high")
    runtime = await _runtime(repo, fs, source)

    await runtime.switch_session(meta.path)

    assert runtime.harness._state.thinking_level == "high"
    # The swap READ the level; it did not re-write it. Without this assertion
    # the ``not target_has_level`` half of the append guard is unprotected —
    # measured: mutating it away leaves the whole 198 set green while every
    # session swap grows the target's JSONL by one redundant entry.
    assert await _levels_on(await repo.open(meta)) == ["high"]


async def test_switch_session_carries_live_level_when_target_has_no_entry(
    tmp_path: Path,
) -> None:
    """Every session created before this fix has zero level entries. Without the
    carry-forward, ``/resume`` into one of them still lands on ``off`` — the
    issue title, unfixed."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    _target, meta = await _session_at(repo, str(tmp_path))
    runtime = await _runtime(repo, fs, source, live_level="medium")

    await runtime.switch_session(meta.path)

    assert runtime.harness._state.thinking_level == "medium"
    # ...and it is recorded on disk, so reopening the target yields ``medium``
    # too rather than snapping back to ``off``.
    assert await _levels_on(await repo.open(meta)) == ["medium"]


async def test_switch_session_target_off_beats_live_level(tmp_path: Path) -> None:
    """An explicit ``off`` on the target is a decision, not an absence: it wins
    over the carried level and nothing is appended."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    _target, meta = await _session_at(repo, str(tmp_path), "high", "off")
    runtime = await _runtime(repo, fs, source, live_level="medium")

    await runtime.switch_session(meta.path)

    assert runtime.harness._state.thinking_level == "off"
    assert await _levels_on(await repo.open(meta)) == ["high", "off"]


async def test_new_session_keeps_live_level(tmp_path: Path) -> None:
    """``/new`` keeps the level you are working at AND writes it into the new
    session, so ``--continue`` into it later still says ``medium``."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = await _runtime(repo, fs, source, live_level="medium")

    await runtime.new_session()

    assert runtime.harness._state.thinking_level == "medium"
    new_session = runtime.session
    assert new_session is not None
    assert await _levels_on(new_session) == ["medium"]
    assert (await new_session.build_context()).thinking_level == "medium"


async def test_new_session_at_off_writes_nothing(tmp_path: Path) -> None:
    """The default level is the kernel's unset sentinel — carrying it forward
    must not stamp a control entry into every fresh session."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = await _runtime(repo, fs, source)

    await runtime.new_session()

    new_session = runtime.session
    assert new_session is not None
    assert await _levels_on(new_session) == []
    assert runtime.harness._state.thinking_level == "off"


async def test_switch_session_clamps_unsupported_target_level(
    tmp_path: Path,
) -> None:
    """A target left at ``xhigh`` on a model that tops out at ``high`` resumes
    at ``high``. Drop the ``model=`` argument and this is the test that fails."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    _target, meta = await _session_at(repo, str(tmp_path), "xhigh")
    runtime = await _runtime(repo, fs, source)

    await runtime.switch_session(meta.path)

    assert runtime.harness._state.thinking_level == "high"
