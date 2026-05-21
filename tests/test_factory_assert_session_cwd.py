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

Sprint 6h₅d §C (P-375): the monkeypatch lane was rewritten to use the
``monkeypatch`` pytest fixture (``monkeypatch.setattr``) against the
single hoisted binding site at
``aelix_agent_core.runtime.agent_session_runtime``. Sprint 6h₅c left the
test patching ``session.session_cwd.assert_session_cwd_exists`` via a
manual ``try/finally`` block — that only worked because the factory
re-imported the symbol per call. The hoist landed in this sprint, so
the test is migrated to the proper seam alongside it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import agent_session_runtime as _mod
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
    monkeypatch: pytest.MonkeyPatch,
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

    # Sprint 6h₅d §C: patch the single hoisted binding site.
    monkeypatch.setattr(_mod, "assert_session_cwd_exists", raising_assert)

    # Track runtime construction so we can assert the assertion ran
    # FIRST.
    original_init = _mod.AgentSessionRuntime.__init__
    construction_order: list[str] = []

    def spy_init(self: Any, *args: Any, **kwargs: Any) -> None:
        construction_order.append("runtime_constructed")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(_mod.AgentSessionRuntime, "__init__", spy_init)

    harness = _new_harness(session=session)
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


async def test_factory_skips_assert_when_no_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory harness (no Session bound) → assertion is skipped, the
    runtime is constructed.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    # No session attached; assertion has nothing to check.
    original_assert = _mod.assert_session_cwd_exists
    assert_calls: list[int] = []

    async def spy_assert(*args: Any, **kwargs: Any) -> None:
        assert_calls.append(1)
        await original_assert(*args, **kwargs)

    monkeypatch.setattr(_mod, "assert_session_cwd_exists", spy_assert)

    harness = _new_harness(session=None)
    runtime = await create_agent_session_runtime(
        harness, _factory, repo=repo, fs=fs
    )
    assert runtime.harness is harness
    # No assertion call when no session is bound.
    assert assert_calls == []


async def test_factory_uses_harness_session_for_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: the assertion runs against ``harness._session`` and,
    when the cwd exists, the runtime is constructed successfully.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    session = await repo.create(JsonlSessionCreateOptions(cwd=str(tmp_path)))

    original_assert = _mod.assert_session_cwd_exists
    seen_sessions: list[Any] = []

    async def spy_assert(s: Any, *, fallback_cwd: Any, fs: Any) -> None:
        seen_sessions.append(s)
        await original_assert(s, fallback_cwd=fallback_cwd, fs=fs)

    monkeypatch.setattr(_mod, "assert_session_cwd_exists", spy_assert)

    harness = _new_harness(session=session)
    runtime = await create_agent_session_runtime(
        harness, _factory, repo=repo, fs=fs
    )
    # Pi ``:391`` — the assertion runs against the harness's own
    # session (NOT the factory's return).
    assert seen_sessions == [session]
    assert runtime.harness is harness
