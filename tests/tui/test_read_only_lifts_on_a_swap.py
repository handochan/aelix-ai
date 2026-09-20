"""#137 — "Open read-only" is a state, not a life sentence.

The refusal in ``_input_loop`` names three ways out — "/fork makes a writable
copy of it; /resume and /new switch to another session" — and review found all
three broken in the same way: ``read_only`` was a plain ``bool`` parameter,
passed by value from ``run_tui`` and never reassigned. The commands ran, the
runtime really did publish a new file and take its writer lock, the TUI printed
``⎇ Forked session``, and the very next prompt was refused again — about a file
nobody else had. Killing aelix was the only way out.

So the predicate is derived from the LIVE session instead, and this file drives
the real :class:`AgentSessionRuntime` through the transition rather than a
double: what has to be true is that the same terminal is writable afterwards.
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
    ReadOnlySessionStorage,
    Session,
    SessionWriterLock,
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
from aelix_coding_agent.tui.shell import _session_refuses_writes


def _stream() -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _harness(session: Session | None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            session=session,
        )
    )


async def _factory(new_session: Session) -> AgentHarness:
    return _harness(new_session)


async def test_fork_out_of_a_read_only_session_makes_this_terminal_writable(
    tmp_path: Path,
) -> None:
    """The blocker, end to end: read-only in, ``/fork``, writable out."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    await theirs.append_message(UserMessage(content=[TextContent(text="their turn")]))
    theirs_path = theirs.session_file
    assert theirs_path is not None

    # What ``entry.py`` hands a terminal that chose "Open read-only": the same
    # file, wrapped so it cannot be written, and no lock at all.
    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        viewer = Session(ReadOnlySessionStorage(theirs.get_storage()))
        runtime = AgentSessionRuntime(_harness(viewer), _factory, repo=repo, fs=fs)
        runtime.set_writer_lock(None)

        assert _session_refuses_writes(runtime, at_startup=True) is True

        entry_id = (await viewer.get_entries())[-1].id
        result = await runtime.fork(entry_id, position="before")
        assert result.cancelled is False

        assert _session_refuses_writes(runtime, at_startup=True) is False, (
            "the escape hatch the refusal advertises actually lets go"
        )
        assert runtime.session is not None
        assert runtime.session.session_file != theirs_path
        assert runtime.writer_lock is not None, "and this terminal owns the fork"
        await runtime.session.append_message(
            UserMessage(content=[TextContent(text="mine now")])
        )
    finally:
        other_terminal.release()


async def test_new_out_of_a_read_only_session_makes_this_terminal_writable(
    tmp_path: Path,
) -> None:
    """``/new`` is the second of the three the refusal names."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path / "sessions"))
    theirs = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    theirs_path = theirs.session_file
    assert theirs_path is not None

    other_terminal = SessionWriterLock(theirs_path)
    assert other_terminal.try_acquire() is True
    try:
        viewer = Session(ReadOnlySessionStorage(theirs.get_storage()))
        runtime = AgentSessionRuntime(_harness(viewer), _factory, repo=repo, fs=fs)
        runtime.set_writer_lock(None)
        assert _session_refuses_writes(runtime, at_startup=True) is True

        result = await runtime.new_session()
        assert result.cancelled is False
        assert _session_refuses_writes(runtime, at_startup=True) is False
        assert runtime.writer_lock is not None
    finally:
        other_terminal.release()


def test_a_runtime_without_a_session_keeps_the_answer_it_was_given() -> None:
    """``tests/tui/`` drives the loop with doubles that have no session.

    Those must not be silently promoted to writable — the predicate falls back
    to what ``run_tui`` was told, the way every other optional runtime surface
    in that module degrades.
    """

    class _Double:
        pass

    assert _session_refuses_writes(_Double(), at_startup=True) is True
    assert _session_refuses_writes(_Double(), at_startup=False) is False

    class _Raises:
        @property
        def session(self) -> Any:
            raise RuntimeError("no session here")

    assert _session_refuses_writes(_Raises(), at_startup=True) is True
