"""Sprint 6h₅a · Phase 4.14 — extension event wiring tests
(P-307/P-308/P-340/P-341/P-342/P-343/P-355).

Pi parity invariants verified here:

  - ``_teardown_current`` order is ``emit_shutdown → invalidate → dispose``
    (P-340 ORDERING CORRECTION from Sprint 6h₄b reversed order).
  - ``dispose`` order is ``emit_shutdown(quit) → invalidate → dispose``
    (P-355 BLOCKING FIX — matches ``_teardown_current``; the W2 §J
    "intentional asymmetry" was based on a spec misread of Pi
    ``:366-373``; Pi has no asymmetry).
  - ``session_before_switch`` / ``session_before_fork`` cancel hooks
    short-circuit the replace and ``_teardown_current`` is NEVER called.
  - ``session_start`` is emitted from the NEW harness's runner AFTER
    ``rebind_session`` with the correct ``reason`` + ``previous_session_file``.
  - ``previous_session_file`` is snapshotted BEFORE teardown.
  - Missing handlers ⇒ shutdown emit is skipped (gated by
    ``has_handlers``).

All tests run over a real tmp-path :class:`JsonlSessionRepo` driving
the public replace API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    SessionBeforeForkHookEvent,
    SessionBeforeForkResult,
    SessionBeforeSwitchHookEvent,
    SessionBeforeSwitchResult,
    SessionShutdownHookEvent,
    SessionStartHookEvent,
)
from aelix_agent_core.runtime import AgentSessionRuntime
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


async def _create_session(repo: JsonlSessionRepo, cwd: str) -> Session:
    return await repo.create(JsonlSessionCreateOptions(cwd=cwd))


def _make_runtime(
    harness: AgentHarness,
    repo: JsonlSessionRepo,
    fs: LocalFileSystem,
    factory: Any | None = None,
) -> AgentSessionRuntime:
    async def _default_factory(new_sess: Session) -> AgentHarness:
        return _new_harness(session=new_sess)

    return AgentSessionRuntime(
        harness, factory or _default_factory, repo=repo, fs=fs
    )


# === P-340 — `_teardown_current` ordering correction ============================


async def test_teardown_current_order_emits_shutdown_before_invalidate_and_dispose(
    tmp_path: Path,
) -> None:
    """Pi parity: ``teardownCurrent`` order is
    ``emit_shutdown → invalidate → dispose`` (Sprint 6h₅a P-340).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    target = await _create_session(repo, str(tmp_path))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)

    call_order: list[str] = []
    captured_shutdown: list[SessionShutdownHookEvent] = []

    def _shutdown_handler(event: Any, ctx: Any) -> None:
        call_order.append("shutdown_emitted")
        captured_shutdown.append(event)

    old_h.hooks.on("session_shutdown", _shutdown_handler)

    runtime.set_before_session_invalidate(
        lambda: call_order.append("invalidate_called")
    )

    original_dispose = old_h.dispose

    async def _dispose_spy() -> None:
        call_order.append("harness_disposed")
        await original_dispose()

    old_h.dispose = _dispose_spy  # type: ignore[method-assign]

    await runtime.switch_session(target_meta.path)
    assert call_order == [
        "shutdown_emitted",
        "invalidate_called",
        "harness_disposed",
    ]
    # P-340 — reason + target_session_file propagated.
    assert len(captured_shutdown) == 1
    assert captured_shutdown[0].reason == "resume"
    assert captured_shutdown[0].target_session_file == target_meta.path


# === P-355 — `dispose` ordering matches `_teardown_current` =====================


async def test_dispose_uses_quit_reason_with_emit_first_order() -> None:
    """Pi parity ``:366-373``: ``dispose`` order is
    ``emit_shutdown(quit) → invalidate → dispose`` — same as
    ``_teardown_current`` (Sprint 6h₅a W5 P-355 BLOCKING FIX; the
    W2 §J "intentional asymmetry" was based on a spec misread).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs)
    h = _new_harness()
    runtime = _make_runtime(h, repo, fs)

    call_order: list[str] = []
    captured: list[SessionShutdownHookEvent] = []

    def _on_shutdown(event: Any, ctx: Any) -> None:
        call_order.append("shutdown_emitted")
        captured.append(event)

    h.hooks.on("session_shutdown", _on_shutdown)

    runtime.set_before_session_invalidate(
        lambda: call_order.append("invalidate_called")
    )

    original_dispose = h.dispose

    async def _spy() -> None:
        call_order.append("harness_disposed")
        await original_dispose()

    h.dispose = _spy  # type: ignore[method-assign]

    await runtime.dispose()
    assert call_order == [
        "shutdown_emitted",
        "invalidate_called",
        "harness_disposed",
    ]
    assert len(captured) == 1
    assert captured[0].reason == "quit"
    assert captured[0].target_session_file is None


# === P-338 — `session_before_switch` cancel short-circuits ========================


async def test_session_before_switch_cancel_short_circuits_switch(
    tmp_path: Path,
) -> None:
    """A handler returning :class:`SessionBeforeSwitchResult(cancel=True)`
    aborts ``switch_session`` BEFORE ``_teardown_current`` fires.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    target = await _create_session(repo, str(tmp_path))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)

    seen_events: list[SessionBeforeSwitchHookEvent] = []

    def _cancel_handler(event: Any, ctx: Any) -> SessionBeforeSwitchResult:
        seen_events.append(event)
        return SessionBeforeSwitchResult(cancel=True)

    old_h.hooks.on("session_before_switch", _cancel_handler)

    invalidate_calls: list[int] = []
    runtime.set_before_session_invalidate(lambda: invalidate_calls.append(1))

    result = await runtime.switch_session(target_meta.path)
    assert result.cancelled is True
    # Cancel short-circuits BEFORE teardown: no invalidate, harness intact.
    assert invalidate_calls == []
    assert runtime.harness is old_h
    # Payload propagation.
    assert len(seen_events) == 1
    assert seen_events[0].reason == "resume"
    assert seen_events[0].target_session_file == target_meta.path


async def test_session_before_fork_cancel_short_circuits_fork(
    tmp_path: Path,
) -> None:
    """Symmetric to switch cancel — for ``fork``."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    entry_id = await source.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)

    seen: list[SessionBeforeForkHookEvent] = []

    def _cancel_handler(event: Any, ctx: Any) -> SessionBeforeForkResult:
        seen.append(event)
        return SessionBeforeForkResult(cancel=True)

    old_h.hooks.on("session_before_fork", _cancel_handler)

    invalidate_calls: list[int] = []
    runtime.set_before_session_invalidate(lambda: invalidate_calls.append(1))

    result = await runtime.fork(entry_id, position="before")
    assert result.cancelled is True
    assert invalidate_calls == []
    assert runtime.harness is old_h
    assert len(seen) == 1
    assert seen[0].entry_id == entry_id
    assert seen[0].position == "before"


# === P-343 — session_start emit on NEW harness with reason + previous ============


async def test_session_start_emits_on_new_harness_after_switch(
    tmp_path: Path,
) -> None:
    """``session_start`` is emitted on the NEW harness's runner with
    ``reason="resume"`` and ``previous_session_file == OLD.session_file``.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    source_path = source.session_file
    target = await _create_session(repo, str(tmp_path))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)

    captured: list[SessionStartHookEvent] = []

    def _record_session_start(event: Any, ctx: Any) -> None:
        captured.append(event)

    # The session_start handler MUST attach to the NEW harness (the OLD
    # bus is disposed by step 1 of _finish_session_replacement). The
    # factory registers it on each freshly built harness.
    async def _factory(new_sess: Session) -> AgentHarness:
        h = _new_harness(session=new_sess)
        h.hooks.on("session_start", _record_session_start)
        return h

    runtime = _make_runtime(old_h, repo, fs, factory=_factory)
    await runtime.switch_session(target_meta.path)

    assert len(captured) == 1
    assert captured[0].reason == "resume"
    assert captured[0].previous_session_file == source_path


async def test_session_start_emits_with_reason_new_for_new_session(
    tmp_path: Path,
) -> None:
    """``new_session`` propagates ``reason="new"``."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    source_path = source.session_file
    old_h = _new_harness(session=source)
    captured: list[SessionStartHookEvent] = []

    async def _factory(new_sess: Session) -> AgentHarness:
        h = _new_harness(session=new_sess)
        h.hooks.on("session_start", lambda e, c: captured.append(e))
        return h

    runtime = _make_runtime(old_h, repo, fs, factory=_factory)
    await runtime.new_session()
    assert len(captured) == 1
    assert captured[0].reason == "new"
    assert captured[0].previous_session_file == source_path


async def test_session_start_emits_with_reason_fork_for_fork(
    tmp_path: Path,
) -> None:
    """``fork`` propagates ``reason="fork"``."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    source_path = source.session_file
    entry_id = await source.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )

    old_h = _new_harness(session=source)
    captured: list[SessionStartHookEvent] = []

    async def _factory(new_sess: Session) -> AgentHarness:
        h = _new_harness(session=new_sess)
        h.hooks.on("session_start", lambda e, c: captured.append(e))
        return h

    runtime = _make_runtime(old_h, repo, fs, factory=_factory)
    await runtime.fork(entry_id, position="at")
    assert len(captured) == 1
    assert captured[0].reason == "fork"
    assert captured[0].previous_session_file == source_path


# === P-342 — previous_session_file snapshot timing ==============================


async def test_previous_session_file_captured_before_teardown(
    tmp_path: Path,
) -> None:
    """The snapshot must come from the OLD session — a mid-flight
    ``before_session_invalidate`` that mutates state must NOT affect it.
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    source_path = source.session_file
    target = await _create_session(repo, str(tmp_path))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)
    captured: list[SessionStartHookEvent] = []

    async def _factory(new_sess: Session) -> AgentHarness:
        h = _new_harness(session=new_sess)
        h.hooks.on("session_start", lambda e, c: captured.append(e))
        return h

    runtime = _make_runtime(old_h, repo, fs, factory=_factory)

    # Aggressive invalidate that scrubs internal state mid-flight.
    def _scrub_invalidate() -> None:
        old_h._session = None  # type: ignore[attr-defined]

    runtime.set_before_session_invalidate(_scrub_invalidate)
    await runtime.switch_session(target_meta.path)

    # Pre-teardown snapshot survived the scrub.
    assert len(captured) == 1
    assert captured[0].previous_session_file == source_path


# === Defensive — no-handlers emit is a no-op ====================================


async def test_session_shutdown_emit_is_noop_when_no_handlers_registered(
    tmp_path: Path,
) -> None:
    """``_teardown_current`` succeeds even when nobody listens."""

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    target = await _create_session(repo, str(tmp_path))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)
    runtime = _make_runtime(old_h, repo, fs)
    # No ``session_shutdown`` handler registered.
    result = await runtime.switch_session(target_meta.path)
    assert result.cancelled is False
    assert runtime.harness is not old_h


# === Cancel does not trigger session_start ======================================


async def test_session_start_not_emitted_when_switch_cancelled(
    tmp_path: Path,
) -> None:
    """A cancelled replace MUST NOT emit ``session_start`` (the new
    harness is never constructed).
    """

    fs = LocalFileSystem()
    repo = JsonlSessionRepo(fs=fs, sessions_root=str(tmp_path))
    source = await _create_session(repo, str(tmp_path))
    target = await _create_session(repo, str(tmp_path))
    target_meta = await target.get_metadata()

    old_h = _new_harness(session=source)
    old_h.hooks.on(
        "session_before_switch",
        lambda e, c: SessionBeforeSwitchResult(cancel=True),
    )
    starts: list[SessionStartHookEvent] = []

    async def _factory(new_sess: Session) -> AgentHarness:
        h = _new_harness(session=new_sess)
        h.hooks.on("session_start", lambda e, c: starts.append(e))
        return h

    runtime = _make_runtime(old_h, repo, fs, factory=_factory)
    result = await runtime.switch_session(target_meta.path)
    assert result.cancelled is True
    assert starts == []
