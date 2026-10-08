"""#376 — the kernel half: which model a branch last ran on, and what the session records.

:func:`resolve_resumed_model` is pi's ``getBranchSelection``
(``core/virtual-models.ts:128-145`` at ``pi@1cedd3272``) without virtual models:
walking back from the leaf, the first ``model_change`` or the first assistant
message naming its provider and model. aelix never writes a ``model_change`` for
a session's launch model, so the responses are most sessions' whole record.

And the one writer of ``model_change`` the harness has, an in-turn
``set_model``, recorded ``model.api`` as the provider (``anthropic-messages``), a
name no registry resolves: the restore read a record it could not use.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    PendingModelChangeWrite,
)
from aelix_agent_core.session.context import resolve_resumed_model
from aelix_agent_core.session.entries import MessageEntry, ModelChangeEntry
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

_TS = "2026-10-08T00:00:00.000Z"


def _user(id: str) -> MessageEntry:
    return MessageEntry(
        id=id, parent_id=None, timestamp=_TS, message=UserMessage(content=[TextContent(text="q")])
    )


def _answer(id: str, provider: str | None, model: str | None) -> MessageEntry:
    return MessageEntry(
        id=id,
        parent_id=None,
        timestamp=_TS,
        message=AssistantMessage(
            content=[TextContent(text="a")], provider=provider, model=model, api="x"
        ),
    )


def _change(id: str, provider: str, model_id: str) -> ModelChangeEntry:
    return ModelChangeEntry(
        id=id, parent_id=None, timestamp=_TS, provider=provider, model_id=model_id
    )


def test_nothing_recorded_is_none() -> None:
    assert resolve_resumed_model([]) is None
    assert resolve_resumed_model([_user("a")]) is None


def test_a_response_is_a_record() -> None:
    """The case every pre-#376 session is: no ``model_change``, responses only."""

    entries = [_user("a"), _answer("b", "anthropic", "claude-haiku-4-5")]
    assert resolve_resumed_model(entries) == ("anthropic", "claude-haiku-4-5")


def test_the_latest_record_wins() -> None:
    later_change = [
        _user("a"),
        _answer("b", "anthropic", "claude-haiku-4-5"),
        _change("c", "openrouter", "anthropic/claude-sonnet-4.5"),
    ]
    later_answer = [
        _change("a", "openrouter", "anthropic/claude-sonnet-4.5"),
        _user("b"),
        _answer("c", "anthropic", "claude-haiku-4-5"),
    ]
    assert resolve_resumed_model(later_change) == ("openrouter", "anthropic/claude-sonnet-4.5")
    assert resolve_resumed_model(later_answer) == ("anthropic", "claude-haiku-4-5")


def test_an_answer_without_names_does_not_erase_the_record() -> None:
    """A turn refused before any request carries no provenance (``api='unknown'``)."""

    entries = [
        _user("a"),
        _answer("b", "anthropic", "claude-haiku-4-5"),
        _user("c"),
        _answer("d", None, None),
        _answer("e", "", ""),
    ]
    assert resolve_resumed_model(entries) == ("anthropic", "claude-haiku-4-5")


def _stream() -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return fn


async def test_an_in_turn_set_model_records_the_provider_not_the_api() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    other = Model(id="claude-haiku-4-5", provider="anthropic", api="anthropic-messages")
    queued: list[Any] = []

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.set_model(other)
        queued.extend(
            w for w in h._pending_session_writes if isinstance(w, PendingModelChangeWrite)
        )
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    assert [(w.provider, w.model_id) for w in queued] == [("anthropic", "claude-haiku-4-5")]
    await h.dispose()


# === Review round 2: an IDLE set_model is recorded (pi ``setModel``) ===========
#
# pi's ``AgentSession.setModel`` appends a ``model_change`` on every call
# (``agent-session.ts:2485`` at ``pi@1cedd3272``). Here only the in-turn queue
# did, and the ``/model`` picker, ``/model <id>`` and ``/agents use`` all run
# idle, so ``/model X`` then quit reopened on the model the last answer named.


_SONNET_MODEL = Model(id="claude-sonnet-4-5", provider="anthropic", api="anthropic-messages")


async def _model_changes(session: Any) -> list[tuple[str, str]]:
    return [
        (e.provider, e.model_id) for e in await session.get_entries() if e.type == "model_change"
    ]


async def test_an_idle_set_model_records_a_model_change() -> None:
    from aelix_agent_core.session import MemorySessionStorage, Session

    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))

    await h.set_model(_SONNET_MODEL)
    # Re-picking the live model is recorded too: it may be one the session
    # never recorded (no model_change is written for a launch model).
    await h.set_model(_SONNET_MODEL)

    assert await _model_changes(session) == [("anthropic", "claude-sonnet-4-5")] * 2
    await h.dispose()


async def test_a_placeholder_is_never_recorded_idle_or_in_turn() -> None:
    """The late-provider hold's ``Model(id, provider)`` (#367): ``api='unknown'``."""

    from aelix_agent_core.session import MemorySessionStorage, Session

    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    placeholder = Model(id="m1", provider="sessext")

    await h.set_model(placeholder)

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.set_model(placeholder)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    assert await _model_changes(session) == []
    await h.dispose()


async def test_a_model_set_while_turns_are_held_is_not_recorded() -> None:
    """Under the CLI's turn gate a handler's ``set_model`` is put back to the
    placeholder (#367): it never was the session's model."""

    from aelix_agent_core.session import MemorySessionStorage, Session

    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h.hold_turns("held")
    await h.set_model(_SONNET_MODEL)
    h.hold_turns(None)

    assert await _model_changes(session) == []
    await h.dispose()


async def test_a_model_select_handler_that_answers_leaves_only_its_answer() -> None:
    """The record follows the live model: a handler that ``set_model``s another
    model in its ``model_select`` records that one, and the outer call records
    nothing after it (the state no longer holds its model)."""

    from aelix_agent_core.session import MemorySessionStorage, Session

    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    haiku = Model(id="claude-haiku-4-5", provider="anthropic", api="anthropic-messages")

    async def answer(event: Any, _ctx: Any = None) -> None:
        if event.model is _SONNET_MODEL:
            await h.set_model(haiku)

    h.hooks.on("model_select", answer)  # type: ignore[arg-type]
    await h.set_model(_SONNET_MODEL)

    assert h.state.model is haiku
    assert await _model_changes(session) == [("anthropic", "claude-haiku-4-5")]
    await h.dispose()


async def test_an_idle_set_model_on_a_read_only_session_switches_and_records_nothing() -> None:
    """#137 / ADR-0244: a terminal looking at a session another one owns writes
    nothing to it. ``/model`` there switched the live model before #376 and must
    not start failing on the record it cannot write."""

    from aelix_agent_core.session import MemorySessionStorage, ReadOnlySessionStorage, Session

    inner = MemorySessionStorage()
    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_stream(), session=Session(ReadOnlySessionStorage(inner)))
    )

    await h.set_model(_SONNET_MODEL)

    assert h.state.model is _SONNET_MODEL
    assert await _model_changes(Session(inner)) == []
    await h.dispose()


# === Review round 3 — the idle record keeps #334's queue order ================
#
# ``set_model``'s idle append goes BEHIND a write still queued or being drained
# (``if self._pending_session_writes or self._drain_lock.locked()``), as
# ``set_thinking_level`` does (the C4 rows of
# ``test_harness_prompt_holds_the_turn_through_its_tail.py``). Written straight
# to the session instead, an idle pick lands AHEAD of an in-turn
# ``model_change`` a cancelled release flush left queued, the stale model is the
# session's last record, and the next ``--continue`` restores it (review round 2
# verify: the ``if False`` mutant survived every row).


async def test_an_idle_pick_after_a_cancelled_release_goes_behind_the_queued_change() -> None:
    import asyncio

    import pytest

    from tests.test_harness_prompt_holds_the_turn_through_its_tail import (
        WAIT,
        _assert_idle,
        _parked_release_flush,
    )

    h, session, first, release = await _parked_release_flush(also_model=True)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, WAIT)
    _assert_idle(h)
    assert [type(w).__name__ for w in h._pending_session_writes] == ["PendingModelChangeWrite"]
    release.set()
    picked = Model(id="picked", provider="p", api="anthropic-messages", context_window=100_000)

    await asyncio.wait_for(h.set_model(picked), WAIT)

    assert h._pending_session_writes == []
    assert await _model_changes(session) == [("p", "queued-model"), ("p", "picked")]
    assert resolve_resumed_model(await session.get_branch()) == ("p", "picked")
    await asyncio.wait_for(h.dispose(), WAIT)


async def test_an_idle_pick_while_a_drain_is_appending_waits_for_it() -> None:
    """The queue is EMPTY while a drain holds what it detached: the lock is the
    other half of the condition. ``medium`` and ``low`` are detached and parked;
    the pick must land after both."""

    import asyncio

    from tests.test_harness_prompt_holds_the_turn_through_its_tail import (
        WAIT,
        _an_idle_drain_parked_on_medium,
    )

    h, session, gate, low = await _an_idle_drain_parked_on_medium()
    picked = Model(id="picked", provider="p", api="anthropic-messages", context_window=100_000)
    selected = asyncio.Event()

    async def on_select(event: Any, *_args: Any) -> None:
        if event.model is picked:
            selected.set()

    h.hooks.on("model_select", on_select)  # type: ignore[call-overload]
    pick = asyncio.ensure_future(h.set_model(picked))
    await asyncio.wait_for(selected.wait(), WAIT)
    await asyncio.sleep(0)
    assert await _model_changes(session) == [], "the pick went straight to the session past the drain"

    gate.set()
    await asyncio.wait_for(asyncio.gather(low, pick), WAIT)

    kinds = [
        e.thinking_level if e.type == "thinking_level_change" else (e.provider, e.model_id)
        for e in await session.get_branch()
        if e.type in ("thinking_level_change", "model_change")
    ]
    assert kinds == ["medium", "low", ("p", "picked")], kinds
    await asyncio.wait_for(h.dispose(), WAIT)
