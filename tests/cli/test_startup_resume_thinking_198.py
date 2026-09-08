"""Issue #198 (startup seam) — ``--continue``/``--resume``/``--session``/``--fork``
must come back at the level the session was left at.

The startup harness is built directly by the factory, which never reads the
resumed session's ``thinking_level_change`` entries: measured on ``main``, a
session whose context said ``high`` produced a harness reporting ``off``.
``entry._seed_startup_state`` (the #122 message seed, widened) now restores the
level from the same single ``get_branch()`` read, clamped to the resumed model,
and reports whether a level was applied so the TUI's ``defaultThinkingLevel``
seed knows not to fire over it.

Modelled on ``tests/cli/test_startup_resume_stats_122.py`` (real
``JsonlSessionRepo`` under ``tmp_path``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
from aelix_coding_agent.cli.entry import _seed_startup_state


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


_REASONING_MODEL = Model(
    id="m",
    api="anthropic",
    provider="anthropic",
    reasoning=True,
    thinking_level_map={"low": 2048, "medium": 8192, "high": 16384},
)


def _startup_harness(
    session: Session,
    *,
    model: Model | None = None,
    thinking_level: str | None = None,
) -> AgentHarness:
    """Built exactly as the startup factory builds it: bound to the resumed
    session, no ``initial_messages``, ``thinking_level`` only when ``--thinking``
    (or a profile's ``thinking:``) supplied one."""

    options: dict[str, Any] = {
        "model": model or Model(id="mock", provider="mock"),
        "stream_fn": _stream(),
        "session": session,
    }
    if thinking_level is not None:
        options["thinking_level"] = thinking_level
    return AgentHarness(AgentHarnessOptions(**options))


async def _session_at(
    repo: JsonlSessionRepo, cwd: str, *levels: str
) -> Session:
    session = await repo.create(JsonlSessionCreateOptions(cwd=cwd))
    await session.append_message(UserMessage(content=[TextContent(text="hello")]))
    for level in levels:
        await session.append_thinking_level_change(level)
    return session


async def test_startup_restores_level_from_session(tmp_path: Path) -> None:
    """THE REGRESSION: a resumed session recording ``high`` starts at ``high``."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path), "high")
    harness = _startup_harness(session, model=_REASONING_MODEL)

    assert harness.state.thinking_level == "off"  # the bug's starting point

    restored = await _seed_startup_state(harness, session, cli_level=None)

    assert harness.state.thinking_level == "high"
    assert restored is True
    # The #122 message seed still runs off the same branch read.
    assert len(harness.state.messages) == 1


async def test_cli_thinking_wins_over_session(tmp_path: Path) -> None:
    """ADR-0196 order: ``--thinking`` (and a profile's ``thinking:``) outranks
    the session. It already reached the harness through
    ``AgentHarnessOptions.thinking_level``, so the seam must leave it alone."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path), "high")
    harness = _startup_harness(
        session, model=_REASONING_MODEL, thinking_level="low"
    )

    restored = await _seed_startup_state(harness, session, cli_level="low")

    assert harness.state.thinking_level == "low"
    # True so the TUI settings seed does not overwrite the CLI level either.
    assert restored is True


async def test_no_entry_leaves_state_untouched(tmp_path: Path) -> None:
    """A session with no recorded level changes nothing.

    The harness starts at a non-``off`` sentinel on purpose: a mutation that
    restores the fold's ``"off"`` initial value instead of "nothing recorded"
    fails here rather than passing silently.
    """

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path))
    harness = _startup_harness(
        session, model=_REASONING_MODEL, thinking_level="medium"
    )

    restored = await _seed_startup_state(harness, session, cli_level=None)

    assert harness.state.thinking_level == "medium"
    assert restored is False


async def test_startup_restores_explicit_off(tmp_path: Path) -> None:
    """A session last set to ``off`` reports ``restored=True`` even though the
    assignment is a no-op — that boolean is what stops the TUI's
    ``defaultThinkingLevel`` seed refilling a level the user turned off."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path), "high", "off")
    harness = _startup_harness(session, model=_REASONING_MODEL)

    restored = await _seed_startup_state(harness, session, cli_level=None)

    assert harness.state.thinking_level == "off"
    assert restored is True


async def test_startup_clamps_unsupported_session_level(tmp_path: Path) -> None:
    """The session was left at ``xhigh``; this model tops out at ``high``. The
    level is clamped, not dropped — drop the ``model=`` argument from the helper
    call and this is the test that fails."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path), "xhigh")
    harness = _startup_harness(session, model=_REASONING_MODEL)

    restored = await _seed_startup_state(harness, session, cli_level=None)

    assert harness.state.thinking_level == "high"
    assert restored is True


async def _levels_on(session: Session) -> list[str]:
    return [
        e.thinking_level  # type: ignore[union-attr]
        for e in await session.get_branch()
        if e.type == "thinking_level_change"
    ]


async def test_cli_thinking_is_recorded_for_the_next_launch(
    tmp_path: Path,
) -> None:
    """``--thinking`` is the one level source that was never written down, so
    ``aelix --thinking high`` then ``--continue`` came back at ``off`` — the
    headline bug, reached through the flag instead of the picker. Measured on
    the first cut of this fix: ``ENTRY TYPES: []`` and a relaunch reporting
    ``restored=False level=off``. The flag is now carried into a session that
    has no level of its own, exactly as the in-session seam carries the live
    level."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path))
    meta = await session.get_metadata()
    harness = _startup_harness(
        session, model=_REASONING_MODEL, thinking_level="high"
    )

    assert await _seed_startup_state(harness, session, cli_level="high") is True
    assert await _levels_on(await repo.open(meta)) == ["high"]

    # Relaunch over the same session WITHOUT the flag: it remembers.
    reopened = await repo.open(meta)
    relaunched = _startup_harness(reopened, model=_REASONING_MODEL)
    restored = await _seed_startup_state(relaunched, reopened, cli_level=None)

    assert relaunched.state.thinking_level == "high"
    assert restored is True


async def test_cli_thinking_does_not_overwrite_the_sessions_own_level(
    tmp_path: Path,
) -> None:
    """The flag outranks the session for THIS process only. A session that
    already chose a level keeps it on disk, so dropping the flag next launch
    returns you to the session's level and not to the flag's."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path), "high")
    meta = await session.get_metadata()
    harness = _startup_harness(
        session, model=_REASONING_MODEL, thinking_level="low"
    )

    await _seed_startup_state(harness, session, cli_level="low")

    assert harness.state.thinking_level == "low"
    assert await _levels_on(await repo.open(meta)) == ["high"]


async def test_cli_thinking_off_writes_nothing(tmp_path: Path) -> None:
    """``off`` is the kernel's unset sentinel, so recording it would make every
    ``--thinking off`` launch grow the file and would make "nobody chose"
    indistinguishable from "chose off" for the session that follows. Same
    ``!= "off"`` half as the in-session guard."""

    repo = JsonlSessionRepo(fs=LocalFileSystem(), sessions_root=str(tmp_path))
    session = await _session_at(repo, str(tmp_path))
    meta = await session.get_metadata()
    harness = _startup_harness(
        session, model=_REASONING_MODEL, thinking_level="off"
    )

    await _seed_startup_state(harness, session, cli_level="off")

    assert await _levels_on(await repo.open(meta)) == []
