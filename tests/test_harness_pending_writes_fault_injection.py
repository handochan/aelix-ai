"""Issue #301 — one refused pending session write must not take the queue.

``AgentHarness.flush_pending_session_writes`` detaches the whole queue before
it starts calling ``session.append_*``. Before this suite existed, one raising
item ended the loop and every already-detached item behind it was gone with no
record: a label, a leaf move and a model-change record disappeared together and
the only report was that ``prompt()`` raised the storage error.

Measured with ``.omc/specs/301-measure.py`` on 91eeb12 — 8 queued, 1 delivered,
6 collateral losses, ``save_point`` reporting ``had_pending_mutations=False``.

These tests inject the fault at the storage seam, which is where a real failure
arrives (ADR-0242: the JSONL store raises ``storage`` for a refused write and
``invalid_entry`` for an entry it cannot serialize).

Most of them queue onto ``h._pending_session_writes`` directly, because what is
under test is the drain rather than who filled it.
``test_set_model_and_set_thinking_level_queue_through_the_public_api`` covers
the other side: the three real push sites, driven through their public API, so
a setter that stopped queueing cannot leave this file green.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    PendingCustomMessageWrite,
    PendingCustomWrite,
    PendingLabelWrite,
    PendingLeafWrite,
    PendingMessageWrite,
    PendingModelChangeWrite,
    PendingSessionInfoWrite,
    PendingThinkingLevelChangeWrite,
)
from aelix_agent_core.harness.hooks import SavePointHookEvent
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


class _RefusingStorage(MemorySessionStorage):
    """Memory storage that refuses every entry of one type.

    Stands in for a disk that is full, a file whose writer lock degraded, or
    an entry the JSONL encoder cannot serialize — all of which surface at this
    exact seam as a raise out of ``append_entry``.
    """

    def __init__(self, refuse_type: str) -> None:
        super().__init__()
        self._refuse_type = refuse_type
        self.refusals = 0

    async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
        if entry.type == self._refuse_type:
            self.refusals += 1
            raise OSError(28, "No space left on device")
        return await super().append_entry(entry)


_CORE_LOGGER = "aelix_agent_core.harness.core"


def _core_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """WARNING-or-worse records from the harness logger only.

    ``caplog.at_level(..., logger=...)`` sets the level on one logger but
    ``caplog.records`` still collects every record that reaches the root
    handler. Filtering on level alone made these assertions hostage to any
    unrelated warning raised during the flush.
    """

    return [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == _CORE_LOGGER
    ]


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            )
        )

    return fn


def _queue_all_eight(h: AgentHarness, seed_id: str) -> None:
    """Queue one of each variant; the ``model_change`` one is the fault."""

    h._pending_session_writes.extend(
        [
            PendingMessageWrite(
                message=UserMessage(content=[TextContent(text="m1")])
            ),
            PendingModelChangeWrite(provider="anthropic", model_id="claude-x"),
            PendingThinkingLevelChangeWrite(thinking_level="high"),
            PendingCustomWrite(custom_type="ct", data={"x": 1}),
            PendingCustomMessageWrite(
                custom_type="cm", content="text", display=True, details=None
            ),
            PendingLabelWrite(target_id=seed_id, label="checkpoint"),
            PendingSessionInfoWrite(name="my session"),
            PendingLeafWrite(target_id=seed_id),
        ]
    )


async def _seeded(
    refuse_type: str = "model_change",
) -> tuple[AgentHarness, Session, _RefusingStorage, str]:
    storage = _RefusingStorage(refuse_type)
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    return h, session, storage, seed_id


async def test_one_refused_write_does_not_drop_the_writes_behind_it() -> None:
    """The heart of #301: everything after the failure still reaches the session."""

    h, session, storage, seed_id = await _seeded()
    _queue_all_eight(h, seed_id)

    failed = await h.flush_pending_session_writes()

    assert storage.refusals == 1
    assert failed == 1
    types = [e.type for e in await session.get_entries()]
    # Seed + 7 of the 8 queued writes. Only the refused ``model_change`` is
    # missing; the six that were queued behind it are all present and in
    # order.
    assert types == [
        "message",  # seed
        "message",
        # "model_change" — refused by the storage
        "thinking_level_change",
        "custom",
        "custom_message",
        "label",
        "session_info",
        "leaf",
    ]
    assert h._pending_session_writes == []


async def test_a_refused_write_is_reported_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Visibility: WARNING, not DEBUG. Silence is how #301 happened."""

    h, _session, _storage, seed_id = await _seeded()
    _queue_all_eight(h, seed_id)

    with caplog.at_level(logging.WARNING, logger="aelix_agent_core.harness.core"):
        await h.flush_pending_session_writes()

    # Filter on the logger as well as the level. ``at_level`` was scoped to
    # ``aelix_agent_core.harness.core``, but ``caplog.records`` collects the
    # whole root, so any unrelated WARNING emitted during the flush would
    # otherwise break an assertion that has nothing to do with it.
    warnings = _core_warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    # The record has to say WHICH write was lost — a bare "a write failed" is
    # not actionable.
    assert "PendingModelChangeWrite" in message
    assert "No space left on device" in message
    # First failure of the flush, so it carries the traceback. See
    # ``test_one_traceback_per_flush_not_one_per_failure`` for the rest.
    assert bool(warnings[0].exc_info)


async def test_every_refusal_is_counted_and_reported() -> None:
    """Two failures in one drain are both counted; the rest still land."""

    storage = _RefusingStorage("custom")
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingCustomWrite(custom_type="a"),
            PendingLabelWrite(target_id=seed_id, label="keep-me"),
            PendingCustomWrite(custom_type="b"),
            PendingLeafWrite(target_id=seed_id),
        ]
    )

    failed = await h.flush_pending_session_writes()

    assert failed == 2
    assert storage.refusals == 2
    types = [e.type for e in await session.get_entries()]
    assert types == ["message", "label", "leaf"]


async def test_flush_of_an_empty_queue_reports_no_failures() -> None:
    h, _session, _storage, _seed_id = await _seeded()
    assert await h.flush_pending_session_writes() == 0


async def test_a_clean_flush_reports_no_failures() -> None:
    h, session, _storage, seed_id = await _seeded(refuse_type="nothing-refused")
    _queue_all_eight(h, seed_id)

    assert await h.flush_pending_session_writes() == 0
    assert len(await session.get_entries()) == 9


async def test_turn_end_does_not_lose_the_turn_over_a_refused_write() -> None:
    """The production call site (``core.py`` turn_end projection).

    On the base build this raised the storage error out of ``prompt()`` —
    a failed session write killed the user's turn. It must not.
    """

    h, session, storage, seed_id = await _seeded()
    save_points: list[SavePointHookEvent] = []
    h.hooks.on("save_point", lambda e, _c: save_points.append(e))  # type: ignore[arg-type]

    async def in_turn(event: Any, _ctx: Any) -> Any:
        if not h._pending_session_writes:
            _queue_all_eight(h, seed_id)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]

    messages = await h.prompt("hello")

    assert messages  # the turn completed
    assert storage.refusals == 1
    assert h._pending_session_writes == []
    types = [e.type for e in await session.get_entries()]
    assert "leaf" in types and "label" in types and "session_info" in types
    assert "model_change" not in types


async def test_save_point_reports_the_failed_writes() -> None:
    """``save_point`` must stop claiming everything was committed.

    Measured on the base build: ``had_pending_mutations=False`` and no failure
    count at all, after six records had just been thrown away.
    """

    h, _session, _storage, seed_id = await _seeded()
    save_points: list[SavePointHookEvent] = []
    h.hooks.on("save_point", lambda e, _c: save_points.append(e))  # type: ignore[arg-type]

    async def in_turn(event: Any, _ctx: Any) -> Any:
        if not h._pending_session_writes:
            _queue_all_eight(h, seed_id)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hello")

    assert len(save_points) == 1
    assert save_points[0].had_pending_mutations is True
    assert save_points[0].failed_writes == 1


async def test_save_point_on_a_healthy_turn_reports_zero_failures() -> None:
    """The default stays the committed save point this event always meant."""

    h, _session, _storage, seed_id = await _seeded(refuse_type="nothing-refused")
    save_points: list[SavePointHookEvent] = []
    h.hooks.on("save_point", lambda e, _c: save_points.append(e))  # type: ignore[arg-type]

    async def in_turn(event: Any, _ctx: Any) -> Any:
        if not h._pending_session_writes:
            _queue_all_eight(h, seed_id)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hello")

    assert len(save_points) == 1
    assert save_points[0].had_pending_mutations is True
    assert save_points[0].failed_writes == 0


async def test_an_abort_still_cancels_the_flush_and_keeps_the_tail() -> None:
    """``BaseException`` is not caught — an aborted flush stays aborted.

    Without that, ``except Exception`` growing to ``except BaseException``
    would silently turn an abort into "the write failed, carry on".

    But re-raising *alone* was #301 wearing an abort for a costume: the queue
    is detached before the first await, so the items the loop had not reached
    yet existed nowhere. They go back on the queue, in order, and a later
    flush writes them. WHICH flush that is on a real aborted turn is pinned by
    ``test_an_aborted_turn_writes_its_tail_before_prompt_returns`` below: it is
    the abort close-out's synthetic ``turn_end``, not the ``finally`` safety
    net, which gets there to find an empty queue.
    """

    class _CancellingStorage(MemorySessionStorage):
        async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
            if entry.type == "custom":
                raise asyncio.CancelledError()
            return await super().append_entry(entry)

    storage = _CancellingStorage()
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingCustomWrite(custom_type="ct"),
            PendingSessionInfoWrite(name="tail-1"),
            PendingLabelWrite(target_id=seed_id, label="tail-2"),
        ]
    )

    with pytest.raises(asyncio.CancelledError):
        await h.flush_pending_session_writes()

    # Nothing behind the cancellation point was attempted...
    assert [e.type for e in await session.get_entries()] == ["message"]
    # ...and nothing behind it was lost either. The cancelled item itself is
    # NOT put back: its ``append_*`` was already in flight and may have landed.
    assert [type(e).__name__ for e in h._pending_session_writes] == [
        "PendingSessionInfoWrite",
        "PendingLabelWrite",
    ]

    # The next flush delivers them.
    assert await h.flush_pending_session_writes() == 0
    assert [e.type for e in await session.get_entries()] == [
        "message",
        "session_info",
        "label",
    ]


async def test_a_requeued_tail_goes_in_front_of_writes_queued_since() -> None:
    """FIFO survives the requeue.

    ``append_message`` keeps pushing onto the queue during a turn, so the tail
    handed back by a cancelled flush has to be *prepended*, not assigned.
    """

    class _CancellingStorage(MemorySessionStorage):
        async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
            if entry.type == "custom":
                # A write that lands on the queue while the flush is awaiting.
                h._pending_session_writes.append(
                    PendingLabelWrite(target_id=seed_id, label="queued-later")
                )
                raise asyncio.CancelledError()
            return await super().append_entry(entry)

    session = Session(_CancellingStorage())
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingCustomWrite(custom_type="ct"),
            PendingSessionInfoWrite(name="was-already-queued"),
        ]
    )

    with pytest.raises(asyncio.CancelledError):
        await h.flush_pending_session_writes()

    assert [type(e).__name__ for e in h._pending_session_writes] == [
        "PendingSessionInfoWrite",  # queued first, still first
        "PendingLabelWrite",  # arrived during the flush, still last
    ]


async def test_an_aborted_turn_writes_its_tail_before_prompt_returns() -> None:
    """End to end: Ctrl-C during a slow flush no longer eats the tail.

    Measured on the pre-fix build of this branch — ``abort()`` while the
    ``turn_end`` flush sat in a blocking append left the session holding only
    ``['message', 'message', 'message']`` and an empty queue: the label, the
    rename and the leaf move were gone with no WARNING and no
    ``failed_writes``. That run is reproduced against a real JSONL file, where
    the bytes can be re-read, by ``.omc/specs/301-abort-tail.py``; the storage
    here is in memory, so what this test reads is the session's entry list and
    its prose says so.

    WHICH flush delivers the tail is asserted here too, because the entry-list
    assertions below pass either way and this mechanism was written down wrong
    once already. ``prompt()`` catches the turn task's ``CancelledError`` and
    the abort close-out emits a synthetic ``TurnEndEvent``; that re-runs the
    turn_end projection's flush, and *that* call is what writes the tail. The
    ``finally`` safety net runs afterwards, on an already-empty queue.

    ``save_point`` is the observable difference: only the turn_end projection
    emits one, so a ``save_point`` on an aborted turn — with the tail already
    in the session when it fires — is the close-out's ``turn_end`` doing the
    work. Measured: delete ``TurnEndEvent`` from the close-out and this is the
    only assertion in the file that reddens, because the tail still lands, via
    the ``finally`` net instead.
    """

    class _SlowOnceStorage(MemorySessionStorage):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.block = True

        async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
            if entry.type == "custom" and self.block:
                self.block = False
                self.entered.set()
                await asyncio.sleep(3600)
            return await super().append_entry(entry)

    storage = _SlowOnceStorage()
    session = Session(storage)
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))

    async def in_turn(event: Any, _ctx: Any) -> Any:
        if not h._pending_session_writes:
            h._pending_session_writes.extend(
                [
                    PendingCustomWrite(custom_type="slow"),
                    PendingLabelWrite(target_id=seed_id, label="tail-1"),
                    PendingSessionInfoWrite(name="tail-2"),
                    PendingLeafWrite(target_id=seed_id),
                ]
            )
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]

    # The session's entries as they stood each time a ``save_point`` fired —
    # see the docstring.
    at_save_point: list[list[str]] = []

    async def on_save_point(_event: Any, _ctx: Any) -> Any:
        at_save_point.append([e.type for e in await session.get_entries()])
        return None

    h.hooks.on("save_point", on_save_point)  # type: ignore[arg-type]

    task = asyncio.ensure_future(h.prompt("hello"))
    await asyncio.wait_for(storage.entered.wait(), timeout=10)
    await h.abort()
    await asyncio.wait_for(task, timeout=10)

    types = [e.type for e in await session.get_entries()]
    assert "label" in types
    assert "session_info" in types
    assert "leaf" in types
    assert h._pending_session_writes == []

    # The tail was already in the session when the turn_end projection emitted
    # its ``save_point``, so that projection's flush is what wrote it — not the
    # ``finally`` net, which runs after this and finds nothing to do.
    assert at_save_point, (
        "no save_point fired on the aborted turn: the close-out's synthetic "
        "TurnEndEvent is what re-runs the flush that delivers the tail"
    )
    assert at_save_point[-1][-3:] == ["label", "session_info", "leaf"], at_save_point


async def test_set_model_and_set_thinking_level_queue_through_the_public_api() -> None:
    """The real push sites, driven the way a user reaches them.

    Every other test here poked ``h._pending_session_writes`` directly, so if
    ``set_model`` stopped queueing altogether the suite stayed green. These
    two setters and the in-turn ``append_message`` are the *only* three pushes
    onto this queue — a label, a rename and a custom entry go through the
    fire-and-forget ``_pin_task`` path instead and never touch it.
    """

    storage = MemorySessionStorage()
    session = Session(storage)
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    seen: list[str] = []

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.set_model(
            Model(id="claude-from-the-setter", api="anthropic", name="m")
        )
        await h.set_thinking_level("high")
        await h.append_message(UserMessage(content=[TextContent(text="mid-turn")]))
        seen.extend(type(e).__name__ for e in h._pending_session_writes)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hello")

    assert seen == [
        "PendingModelChangeWrite",
        "PendingThinkingLevelChangeWrite",
        "PendingMessageWrite",
    ]
    # And the turn_end flush put each of them in the session.
    entries = await session.get_entries()
    by_type = {e.type: e for e in entries}
    assert by_type["model_change"].model_id == "claude-from-the-setter"  # type: ignore[union-attr]
    assert by_type["thinking_level_change"].thinking_level == "high"  # type: ignore[union-attr]
    assert h._pending_session_writes == []


async def test_a_broken_dispatcher_is_not_reported_as_a_refused_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``assert_never`` must reach the developer, not the "write lost" log.

    A ``PendingSessionWrite`` variant added without a dispatcher arm raises
    ``AssertionError`` out of ``assert_never`` — and ``AssertionError`` IS an
    ``Exception``, so a bare ``except Exception`` would have counted a bug in
    ``core.py`` as a record the disk refused.
    """

    class _NinthVariant:
        """A variant the ``match`` in the dispatcher has no arm for."""

    h, session, _storage, seed_id = await _seeded()
    h._pending_session_writes.extend(
        [
            _NinthVariant(),  # type: ignore[list-item]
            PendingLabelWrite(target_id=seed_id, label="after-the-bug"),
        ]
    )

    with (
        caplog.at_level(logging.WARNING, logger=_CORE_LOGGER),
        pytest.raises(AssertionError),
    ):
        await h.flush_pending_session_writes()

    assert _core_warnings(caplog) == []
    # The drain stopped: this is a bug, not a survivable storage refusal.
    assert [e.type for e in await session.get_entries()] == ["message"]
    assert [type(e).__name__ for e in h._pending_session_writes] == [
        "PendingLabelWrite"
    ]


async def test_one_traceback_per_flush_not_one_per_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Traceback volume.

    The tree installs no logging handler, so these records reach
    ``logging.lastResort`` → stderr. A disk that fills mid-turn refuses every
    queued write, and N full tracebacks would paint over a live TUI. The
    variant, the exception repr and the running count are on every record
    regardless, so only the first carries ``exc_info``.
    """

    storage = _RefusingStorage("custom")
    session = Session(storage)
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingCustomWrite(custom_type="a"),
            PendingCustomWrite(custom_type="b"),
            PendingCustomWrite(custom_type="c"),
        ]
    )

    with caplog.at_level(logging.WARNING, logger=_CORE_LOGGER):
        assert await h.flush_pending_session_writes() == 3

    records = _core_warnings(caplog)
    # ``exc_info=False`` is stored as ``False``, not ``None`` — assert on the
    # truth value, which is what ``Formatter.format`` branches on.
    assert [bool(r.exc_info) for r in records] == [True, False, False]
    # Every record still names the variant and the reason.
    for record in records:
        assert "PendingCustomWrite" in record.getMessage()
        assert "No space left on device" in record.getMessage()


async def test_a_cancelled_flush_says_how_many_had_already_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cancellation path is not allowed to swallow the count.

    ``flush_pending_session_writes`` re-raises a ``CancelledError`` instead of
    returning, so the ``failed`` it had accumulated reaches no ``save_point``:
    the close-out's second flush counts only the requeued tail it attempts
    itself and reports ``0``. Measured end to end in
    ``.omc/specs/301-failed-writes-accounting.py`` ARM 1 — one write refused,
    then a cancellation, and the turn's only save point read
    ``had_pending=True failed_writes=0`` with that record on no disk.

    The number cannot honestly be carried across (the two flushes attempt
    different items, and the cancelled item is deliberately in neither
    bucket), so what is fixed is the silence: one more WARNING naming the
    running total at the moment of the cancellation.
    """

    class _RefuseThenCancelStorage(MemorySessionStorage):
        async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
            if entry.type == "custom":
                raise OSError(28, "No space left on device")
            if entry.type == "label":
                raise asyncio.CancelledError()
            return await super().append_entry(entry)

    session = Session(_RefuseThenCancelStorage())
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingCustomWrite(custom_type="refused"),
            PendingLabelWrite(target_id=seed_id, label="cancels"),
            PendingSessionInfoWrite(name="tail-1"),
        ]
    )

    with (
        caplog.at_level(logging.WARNING, logger=_CORE_LOGGER),
        pytest.raises(asyncio.CancelledError),
    ):
        await h.flush_pending_session_writes()

    messages = [r.getMessage() for r in _core_warnings(caplog)]
    assert len(messages) == 2, messages
    # The per-item record for the refusal...
    assert "PendingCustomWrite" in messages[0]
    # ...and the one the cancellation adds, carrying the count that the raise
    # is about to discard.
    assert "cancelled after 1 of 3 writes" in messages[1], messages[1]
    # The un-attempted tail is still handed back, as before.
    assert [type(e).__name__ for e in h._pending_session_writes] == [
        "PendingSessionInfoWrite"
    ]


async def test_a_clean_cancellation_adds_no_extra_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No failures yet — an abort mid-flush stays as quiet as it was.

    The count is only worth a record when it is about to be lost, so the
    guard is ``if failed:``. Without it every Ctrl-C during a healthy flush
    would print a WARNING saying nothing had gone wrong.
    """

    class _CancellingStorage(MemorySessionStorage):
        async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
            if entry.type == "label":
                raise asyncio.CancelledError()
            return await super().append_entry(entry)

    session = Session(_CancellingStorage())
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingLabelWrite(target_id=seed_id, label="cancels"),
            PendingSessionInfoWrite(name="tail-1"),
        ]
    )

    with (
        caplog.at_level(logging.WARNING, logger=_CORE_LOGGER),
        pytest.raises(asyncio.CancelledError),
    ):
        await h.flush_pending_session_writes()

    assert _core_warnings(caplog) == []


async def test_failed_writes_counts_refusals_not_missing_records() -> None:
    """The narrowed contract, pinned.

    ``failed_writes`` used to be documented as how many records the session
    is missing. It is not: an ``append_*`` that raises *after* the record
    landed is counted, and the record is there. That is not a contrived
    storage — ADR-0242 re-arms the JSONL store's healing newline on any failed
    append precisely because it cannot tell a fragment from a whole record, so
    a disk that fills between a record's last byte and its newline leaves the
    record readable and the next append opens a fresh line. Measured on a real
    ``JsonlSessionStorage`` file in
    ``.omc/specs/301-failed-writes-accounting.py`` ARM 2: ``failed_writes=1``
    with that ``custom`` entry present in the bytes on disk and in the
    reloaded entries.

    Reproduced here at the same seam the rest of this file injects at, so the
    wording on :class:`SavePointHookEvent` stays honest under edit.
    """

    class _LandsThenRaisesStorage(MemorySessionStorage):
        async def append_entry(self, entry: Any) -> Any:  # type: ignore[override]
            result = await super().append_entry(entry)
            if entry.type == "custom":
                raise OSError(28, "No space left on device")
            return result

    session = Session(_LandsThenRaisesStorage())
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingCustomWrite(custom_type="lands-anyway"),
            PendingLabelWrite(target_id=seed_id, label="tail-1"),
        ]
    )

    assert await h.flush_pending_session_writes() == 1
    # Counted as failed, and present all the same.
    assert [e.type for e in await session.get_entries()] == [
        "message",
        "custom",
        "label",
    ]
