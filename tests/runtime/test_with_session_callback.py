"""Sprint 6h₅b · Phase 4.15 — ``with_session`` callback plumbing tests
(ADR-0083, P-358).

Pi parity: ``finishSessionReplacement`` (``agent-session-runtime.ts:172-173``).
Order: teardown → apply → setup → rebind → session_start emit →
with_session(create_replaced_session_context()).

All 3 public replace APIs (``switch_session`` / ``new_session`` / ``fork``)
accept the optional ``with_session`` callback. ``import_from_jsonl``
deliberately omits it per Pi (no callback in Pi signature).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import (
    AgentSessionRuntime,
    ReplacedSessionContext,
)
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
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


def _make_runtime(
    harness: AgentHarness,
    repo: JsonlSessionRepo,
    fs: LocalFileSystem,
) -> AgentSessionRuntime:
    async def _factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return AgentSessionRuntime(harness, _factory, repo=repo, fs=fs)


async def test_switch_session_accepts_with_session_and_invokes_it(
    tmp_path: Path,
) -> None:
    """``switch_session(path, with_session=cb)`` invokes ``cb`` exactly
    once after rebind on the NEW harness.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    received: list[Any] = []

    async def cb(ctx: ReplacedSessionContext) -> None:
        received.append(ctx)

    await runtime.switch_session(target_meta.path, with_session=cb)
    assert len(received) == 1
    # Protocol structural check.
    assert isinstance(received[0], ReplacedSessionContext)


async def test_new_session_accepts_with_session(tmp_path: Path) -> None:
    """``new_session(with_session=cb)`` invokes the callback."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    received: list[Any] = []

    async def cb(ctx: ReplacedSessionContext) -> None:
        received.append(ctx)

    await runtime.new_session(with_session=cb)
    assert len(received) == 1
    assert isinstance(received[0], ReplacedSessionContext)


async def test_fork_accepts_with_session(tmp_path: Path) -> None:
    """``fork(entry_id, with_session=cb)`` invokes the callback."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    entry_id = await source.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    received: list[Any] = []

    async def cb(ctx: ReplacedSessionContext) -> None:
        received.append(ctx)

    await runtime.fork(entry_id, position="at", with_session=cb)
    assert len(received) == 1
    assert isinstance(received[0], ReplacedSessionContext)


async def test_with_session_receives_context_bound_to_new_harness(
    tmp_path: Path,
) -> None:
    """The ctx handed to ``with_session`` is the FRESH context built on
    the post-rebind (NEW) harness — sending a user message through it
    enqueues onto the NEW harness's ``_next_turn_queue``, not the OLD.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)

    async def cb(ctx: ReplacedSessionContext) -> None:
        await ctx.send_user_message("post-replace")

    await runtime.switch_session(target_meta.path, with_session=cb)

    new_h = runtime.harness
    assert new_h is not old_h
    # Message landed on the NEW harness's queue, not the OLD one.
    assert len(new_h._next_turn_queue) == 1
    assert len(old_h._next_turn_queue) == 0


async def test_with_session_callback_exception_propagates(
    tmp_path: Path,
) -> None:
    """Raises inside ``with_session`` propagate to the caller — no
    swallow per Pi parity.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    async def boom(ctx: ReplacedSessionContext) -> None:
        raise RuntimeError("with_session blew up")

    with pytest.raises(RuntimeError, match=r"with_session blew up"):
        await runtime.switch_session(target_meta.path, with_session=boom)


async def test_with_session_runs_after_rebind_callback(
    tmp_path: Path,
) -> None:
    """Ordering: ``with_session`` runs AFTER the rebind callback (Pi
    parity — rebind is step 4, with_session is step 6).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    target_meta = await target.get_metadata()

    runtime = _make_runtime(_new_harness(session=source), repo, fs)

    order: list[str] = []

    async def rebind_cb(h: AgentHarness, reason: str = "resume") -> None:
        order.append("rebind")

    async def with_session_cb(ctx: ReplacedSessionContext) -> None:
        order.append("with_session")

    runtime.set_rebind_session(rebind_cb)
    await runtime.switch_session(target_meta.path, with_session=with_session_cb)
    assert order == ["rebind", "with_session"]
