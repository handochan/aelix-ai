"""Sprint 6h₅c · Phase 4.16 — :func:`create_agent_session_runtime`
``assert_session_cwd_exists`` at Pi line ``:391`` (P-370).

Pi parity: ``createAgentSessionRuntime`` (``agent-session-runtime.ts:382-400``)
runs ``assertSessionCwdExists`` BEFORE constructing the runtime so a
stored-cwd-missing condition fails LOUD at bootstrap rather than later
when the wire layer reads through to the missing path.

Pi parity note (Aelix divergence per ADR-0081 P-346): Pi passes
``undefined`` for ``fallbackCwd`` at the factory site. Aelix mirrors by
passing ``fallback_cwd=None``; the :func:`get_missing_session_cwd_issue`
helper silently returns :data:`None` when no fallback is available
because the Pi error format requires a fallback to render. The
observable bootstrap invariant is therefore that the assertion is CALLED
BEFORE construction — the assertion's raise path is covered by the
``switch_session`` tests (which supply a fallback).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import create_agent_session_runtime
from aelix_agent_core.session import (
    JsonlSessionCreateOptions,
    JsonlSessionRepo,
    LocalFileSystem,
    Session,
)
from aelix_agent_core.session.session_cwd import (
    MissingSessionCwdError,
    SessionCwdIssue,
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


async def _factory(new_sess: Session) -> AgentHarness:
    return _new_harness(session=new_sess)


async def test_factory_asserts_cwd_before_construction(
    tmp_path: Path,
) -> None:
    """Pi line ``:391``: the assertion's raise propagates through the
    factory BEFORE the runtime is constructed.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))
    meta = await session.get_metadata()

    # Stub the assertion so we can observe propagation order through the
    # factory without relying on the helper's fallback-cwd short-circuit
    # (P-346: Pi assertion at :391 passes ``undefined`` for fallbackCwd;
    # Aelix's helper returns silently in that case).
    from aelix_agent_core.session import session_cwd as _sc

    original_assert = _sc.assert_session_cwd_exists
    assert_called_at: list[str] = []

    async def raising_assert(
        s: Any, *, fallback_cwd: Any, fs: Any
    ) -> None:
        assert_called_at.append("called")
        raise MissingSessionCwdError(
            SessionCwdIssue(
                session_cwd=meta.cwd,
                fallback_cwd="/fake",
                session_file=meta.path,
            )
        )

    _sc.assert_session_cwd_exists = raising_assert  # type: ignore[assignment]

    # Track runtime construction so we can assert the assertion ran
    # FIRST.
    from aelix_agent_core.runtime import agent_session_runtime as _mod

    original_init = _mod.AgentSessionRuntime.__init__
    construction_order: list[str] = []

    def spy_init(self: Any, *args: Any, **kwargs: Any) -> None:
        construction_order.append("runtime_constructed")
        original_init(self, *args, **kwargs)

    _mod.AgentSessionRuntime.__init__ = spy_init  # type: ignore[method-assign]

    harness = _new_harness(session=session)
    try:
        with pytest.raises(MissingSessionCwdError) as exc_info:
            await create_agent_session_runtime(
                harness, _factory, repo=repo, fs=fs
            )
        assert exc_info.value.issue.session_cwd == meta.cwd
        # The assertion fired, and because it raised, construction
        # never began (Pi ordering invariant — ``:391`` runs BEFORE
        # the ``new AgentSessionRuntime(...)`` call).
        assert assert_called_at == ["called"]
        assert construction_order == []
    finally:
        _sc.assert_session_cwd_exists = original_assert  # type: ignore[assignment]
        _mod.AgentSessionRuntime.__init__ = original_init  # type: ignore[method-assign]


async def test_factory_skips_assert_when_no_session(
    tmp_path: Path,
) -> None:
    """In-memory harness (no Session bound) → assertion is skipped, the
    runtime is constructed.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    # No session attached; assertion has nothing to check.
    from aelix_agent_core.session import session_cwd as _sc

    original_assert = _sc.assert_session_cwd_exists
    assert_calls: list[int] = []

    async def spy_assert(*args: Any, **kwargs: Any) -> None:
        assert_calls.append(1)
        await original_assert(*args, **kwargs)

    _sc.assert_session_cwd_exists = spy_assert  # type: ignore[assignment]

    harness = _new_harness(session=None)
    try:
        runtime = await create_agent_session_runtime(
            harness, _factory, repo=repo, fs=fs
        )
        assert runtime.harness is harness
        # No assertion call when no session is bound.
        assert assert_calls == []
    finally:
        _sc.assert_session_cwd_exists = original_assert  # type: ignore[assignment]


async def test_factory_uses_harness_session_for_cwd(
    tmp_path: Path,
) -> None:
    """Happy path: the assertion runs against ``harness._session`` and,
    when the cwd exists, the runtime is constructed successfully.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    from aelix_agent_core.session import session_cwd as _sc

    original_assert = _sc.assert_session_cwd_exists
    seen_sessions: list[Any] = []

    async def spy_assert(s: Any, *, fallback_cwd: Any, fs: Any) -> None:
        seen_sessions.append(s)
        await original_assert(s, fallback_cwd=fallback_cwd, fs=fs)

    _sc.assert_session_cwd_exists = spy_assert  # type: ignore[assignment]

    harness = _new_harness(session=session)
    try:
        runtime = await create_agent_session_runtime(
            harness, _factory, repo=repo, fs=fs
        )
        # Pi ``:391`` — the assertion runs against the harness's own
        # session (NOT the factory's return).
        assert seen_sessions == [session]
        assert runtime.harness is harness
    finally:
        _sc.assert_session_cwd_exists = original_assert  # type: ignore[assignment]
