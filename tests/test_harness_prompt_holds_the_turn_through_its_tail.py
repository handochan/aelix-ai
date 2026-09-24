"""Issue #334 — ``prompt()`` holds the turn through its tail.

``_run``'s ``finally`` used to set the phase idle and the idle event when each
run ended. But ``prompt()`` is not done then: it still has a tail — the retry
backoff and the re-run after it, the ``auto_retry_end`` emit, overflow recovery
(a compaction and a re-run) and the threshold compaction check. For that whole
tail the harness said it was idle, so a second ``prompt()`` passed the busy
guard and ran alongside the first, the two turns shared one context (live, the
first call's re-run answered ``ALPHA\\nBRAVO`` for a prompt that asked only for
ALPHA), ``wait_for_idle()`` returned mid-prompt, ``dispose()`` tore the harness
down under a running prompt, and #321's per-call owner claim could only accept
the regressions its probe ARMs 18/18h/23c measured.

pi holds its run claim for the whole call: ``_runAgentPrompt`` sets
``_isAgentRunActive`` and clears it only in its ``finally``'s
``_emitAgentSettled()`` (``agent-session.ts:1468-1490`` @ a328aa89a). Now so
does Aelix: the claim is taken at ``prompt()``'s entry and released once, in its
``finally``; ``_run`` flips nothing; the tail's compactions nest under the claim
and hand ``"turn"`` back instead of ``"idle"``.

How this file proves "parked" and "nobody said idle": the idle event is swapped
for :class:`_Watched`, which records the phase at every ``set()`` and signals once
a waiter has found it clear. "No ``set()`` since the prompt's entry" is then an
exact, timer-free statement, and so is "exactly one ``set()``, at the release".
Every point is reached by parking on an event the harness side sets (a
subscriber, a hook, a session read) and released explicitly; the real backoff
runs only at a 60 s base delay and is ended by ``abort_retry()``, never by its
timer.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from aelix_agent_core.harness import core as core_mod
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

# Generous: a bounded wait only runs out on the defect, never on a slow box.
WAIT = 10.0
_OVERFLOW = "prompt is too long: 213462 tokens > 200000 maximum"


def _texts(messages: Any) -> list[str]:
    return [
        c.text
        for m in messages
        for c in getattr(m, "content", [])
        if hasattr(c, "text")
    ]


class _Provider:
    """Scripted per call: ``"retry"`` fails retryably, ``"overflow"`` fails with
    a context overflow, ``"big"`` answers over a 20k model's compaction
    threshold, ``"silent-overflow"`` answers with an input over a 200k window,
    ``"park"`` answers once ``release[n]`` is set; anything else
    answers at once. ``seen`` records the context each call was handed."""

    def __init__(self, script: dict[int, str]) -> None:
        self.script = script
        self.calls = 0
        self.seen: list[list[str]] = []
        self.parked = {n: asyncio.Event() for n in range(1, 10)}
        self.release = {n: asyncio.Event() for n in range(1, 10)}

    async def __call__(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        self.calls += 1
        n = self.calls
        self.seen.append(_texts(context.messages))
        kind = self.script.get(n, "ok")
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if kind in ("retry", "overflow"):
            text = "rate limit exceeded" if kind == "retry" else _OVERFLOW
            failed = AssistantMessage(content=[], stop_reason="error", error_message=text)
            yield AssistantErrorEvent(reason="error", error=failed, error_message=text)
            return
        if kind == "park":
            self.parked[n].set()
            await self.release[n].wait()
        usage = {"total_tokens": 5_000} if kind == "big" else None
        if kind == "silent-overflow":
            # A COMPLETED answer whose input is over a 200k window: pi's
            # silent overflow, compacted with ``will_retry=False``.
            usage = {"input": 250_000}
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=f"answer {n}")],
                stop_reason="end_turn",
                usage=usage,
            )
        )


class _Summariser:
    """A summariser that answers at once and counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, _model: Any, prep: CompactionPreparation, _ci: Any
    ) -> CompactResult:
        self.calls += 1
        return CompactResult(
            summary="S",
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=1,
            details={},
        )


async def _seeded_session() -> Session:
    """A session with enough behind the live turn for a compaction to cut."""

    session = Session(MemorySessionStorage())
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    return session


class _Watched(asyncio.Event):
    """The harness's idle event, watched.

    ``sets`` records the phase at every ``set()``; ``parked`` fires once a
    waiter has found the event clear. Nothing yields between that check and
    the waiter's own ``await``, so once ``parked`` is seen the waiter is on the
    event (#321's N3 technique)."""

    def __init__(self, h: AgentHarness) -> None:
        super().__init__()
        self.h = h
        self.sets: list[str] = []
        self.parked = asyncio.Event()

    def set(self) -> None:
        self.sets.append(self.h.phase)
        super().set()

    async def wait(self) -> Literal[True]:
        if not self.is_set():
            self.parked.set()
        return await super().wait()


def _watch_idle(h: AgentHarness) -> _Watched:
    watched = _Watched(h)
    watched.set()
    watched.sets.clear()
    h._idle_event = watched
    return watched


def _begin(h: AgentHarness) -> tuple[asyncio.Future[Any], asyncio.Future[Any]]:
    """Schedule ``prompt("first")`` and a ``wait_for_idle()``, awaiting nothing.

    The prompt's task is created first and the loop runs tasks in order, so
    its synchronous entry flip has cleared the event before the waiter looks.
    Nothing has run yet when this returns: a test whose hooks read the waiter
    stores it here, before its first await (see :func:`_start`)."""

    first = asyncio.ensure_future(h.prompt("first"))
    waiter = asyncio.ensure_future(h.wait_for_idle())
    return first, waiter


async def _start(h: AgentHarness, watched: _Watched) -> tuple[asyncio.Future[Any], asyncio.Future[Any]]:
    """:func:`_begin`, then wait until the ``wait_for_idle()`` is proven parked.

    The prompt runs on while this waits — how far is the event loop's business:
    on Python 3.11 ``asyncio.wait_for`` wraps the wait in a task of its own, and
    a prompt whose provider answers at once reached its threshold compaction's
    ``session_compact`` hook before this returned (branch CI run 36052006149,
    ubuntu and windows py3.11: ``KeyError: 'waiter'`` from a hook reading a
    holder filled after this call). So nothing a hook or callback reads may be
    assigned from this call's result; use :func:`_begin` for that."""

    first, waiter = _begin(h)
    await asyncio.wait_for(watched.parked.wait(), WAIT)
    return first, waiter


def _assert_idle(h: AgentHarness) -> None:
    assert h.phase == "idle", f"the phase was left at {h.phase!r}"
    assert h._idle_event.is_set(), "wait_for_idle() would never return"
    # The release drops the claim: a finished prompt's claim kept alive would
    # send a later ``compact(_claim=...)`` on an idle harness down the nested
    # path (``invalid_state``) instead of the public one. (``getattr``: the
    # base has no ``_claim``, and this file is run against it.)
    assert getattr(h, "_claim", None) is None, "the release left the prompt's claim behind"
    assert h._turn_state is None, "the release left the run's turn state behind"


def _assert_held(
    h: AgentHarness, watched: _Watched, waiter: asyncio.Future[Any], phase: str
) -> None:
    """The claim is still held: the phase, the event, and nobody said idle."""

    assert h.phase == phase, f"the tail runs with phase {h.phase!r}"
    assert not h._idle_event.is_set(), "the idle event is set mid-prompt"
    assert watched.sets == [], (
        f"the idle event was set mid-prompt, with the phase at {watched.sets}"
    )
    assert not waiter.done(), "a wait_for_idle() parked before the prompt returned"
    assert h.is_idle is False
    assert h._make_context().is_idle() is False


async def _assert_everything_is_refused(
    h: AgentHarness, watched: _Watched, waiter: asyncio.Future[Any], phase: str
) -> None:
    _assert_held(h, watched, waiter, phase)
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(h.prompt("second"), WAIT)
    assert refused.value.code == "busy"
    # A refused prompt releases nothing: its guard raises before the ``try``
    # whose ``finally`` gives the phase back unconditionally.
    _assert_held(h, watched, waiter, phase)
    with pytest.raises(AgentHarnessError) as compact_refused:
        await asyncio.wait_for(h.compact(), WAIT)
    assert compact_refused.value.code == "busy"
    with pytest.raises(AgentHarnessError) as navigate_refused:
        await asyncio.wait_for(h.navigate_tree(None), WAIT)
    assert navigate_refused.value.code == "busy"
    _assert_held(h, watched, waiter, phase)


async def _assert_released_once(
    h: AgentHarness, watched: _Watched, waiter: asyncio.Future[Any]
) -> None:
    _assert_idle(h)
    await asyncio.wait_for(waiter, WAIT)
    assert watched.sets == ["idle"], (
        f"the idle event must be set exactly once, at the release: {watched.sets}"
    )


def _on_settled(h: AgentHarness) -> asyncio.Event:
    """Set once any run of the turn has ended (``settled`` fires inside
    ``_run``). Parking "the tail's branch read" keys on this, never on
    ``phase == "idle"`` — that predicate was the defect."""

    settled = asyncio.Event()

    async def handler(*_args: Any) -> None:
        settled.set()

    h.hooks.on("settled", handler)  # type: ignore[call-overload]
    return settled


def _park_the_first_branch_read_after(
    session: Session, after: asyncio.Event
) -> tuple[asyncio.Event, asyncio.Event]:
    real_get_branch = session.get_branch
    reached = asyncio.Event()
    release = asyncio.Event()

    async def get_branch(*args: Any, **kwargs: Any) -> Any:
        if after.is_set() and not reached.is_set():
            reached.set()
            await release.wait()
        return await real_get_branch(*args, **kwargs)

    session.get_branch = get_branch  # type: ignore[method-assign]
    return reached, release


def _park_the_hook(h: AgentHarness, hook: str) -> tuple[asyncio.Event, asyncio.Event]:
    reached = asyncio.Event()
    release = asyncio.Event()

    async def handler(*_args: Any) -> None:
        if not reached.is_set():
            reached.set()
            await release.wait()

    h.hooks.on(hook, handler)  # type: ignore[call-overload]
    return reached, release


class _Backoff:
    """The retry backoff, made a sequence instead of a race.

    Installs itself as ``_handle_retryable_error``; its i-th call pops the
    error assistant as the real one does, sets ``entered[i]``, parks until
    ``release[i]`` and then retries (``True``) or gives the retry up
    (``False`` — what ``abort_retry()`` or an exhausted budget returns).
    ``counts`` also bumps ``_retry_attempt`` as the real one does, so the
    prompt's closing ``auto_retry_end`` emit runs."""

    def __init__(self, h: AgentHarness, verdicts: list[bool], *, counts: bool = False) -> None:
        self.h = h
        self.verdicts = verdicts
        self.counts = counts
        self.entered = [asyncio.Event() for _ in verdicts]
        self.release = [asyncio.Event() for _ in verdicts]
        self.n = 0
        h._handle_retryable_error = self  # type: ignore[method-assign]

    async def __call__(self, message: Any) -> bool:
        i = self.n
        self.n += 1
        if self.counts:
            self.h._retry_attempt += 1
        messages = self.h._state.messages
        if messages and isinstance(messages[-1], AssistantMessage):
            messages.pop()
        self.entered[i].set()
        await self.release[i].wait()
        return self.verdicts[i]


def _in_backoff_signal(h: AgentHarness) -> asyncio.Event:
    in_backoff = asyncio.Event()

    def watch(event: object) -> None:
        if isinstance(event, AutoRetryStartEvent):
            in_backoff.set()

    h.subscribe(watch)
    return in_backoff


def _retrying_harness(
    provider: _Provider, session: Session | None = None
) -> AgentHarness:
    h = AgentHarness(
        AgentHarnessOptions(
            session=session or Session(MemorySessionStorage()), stream_fn=provider
        )
    )
    h._state.auto_retry_enabled = True
    h._state.auto_compaction_enabled = False
    return h


async def _compacting_harness(
    provider: _Provider, *, overflow: bool
) -> tuple[AgentHarness, Session, _Summariser]:
    session = await _seeded_session()
    summariser = _Summariser()
    h = AgentHarness(
        AgentHarnessOptions(
            session=session, stream_fn=provider, _summarizer_override=summariser
        )
    )
    h._state.auto_compaction_enabled = True
    h._state.auto_retry_enabled = False
    if overflow:
        h._state.model = SimpleNamespace(context_window=200_000)  # type: ignore[assignment]
    else:
        h._state.model = Model(id="m20k", context_window=20_000)
    return h, session, summariser


# === Every point of the tail refuses a second prompt =======================

_POINTS = [
    "retry-backoff",
    "auto-retry-end",
    "overflow-read",
    "overflow-compaction",
    "threshold-read",
    "threshold-compaction",
    "release-flush",
]


@pytest.mark.parametrize("point", _POINTS)
async def test_a_second_prompt_is_refused_at_every_point_of_the_first_ones_tail(
    point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE issue. At each await of ``prompt()``'s tail: the phase is the
    prompt's (``"turn"``, or ``"compaction"`` inside a nested compaction), the
    idle event has not been set since the entry, a ``wait_for_idle()`` parked
    before the prompt is still parked, and ``prompt()`` / ``compact()`` /
    ``navigate_tree()`` are all refused ``busy``. Then the one release: idle,
    the waiter woken, exactly one ``set()``, and the next prompt accepted.

    On the base the retry backoff, the ``auto_retry_end`` emit and both branch
    reads ran with phase ``'idle'`` and a set idle event, and a second
    ``prompt()`` was ACCEPTED there (#334 design probe T1/A7/A12/A13o); inside
    the two nested compactions the phase said ``'compaction'`` but the event
    had already been set once, by ``_run``'s ``finally``, waking every waiter
    (critic probe K1t)."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)
    phase = "turn"
    release: Callable[[], None]

    if point == "retry-backoff":
        provider = _Provider({1: "retry"})
        h = _retrying_harness(provider)
        reached = _in_backoff_signal(h)
        release = h.abort_retry
    elif point == "auto-retry-end":
        provider = _Provider({1: "retry"})
        h = _retrying_harness(provider)
        backoff = _Backoff(h, [True], counts=True)
        backoff.release[0].set()
        reached = asyncio.Event()
        release_end = asyncio.Event()

        async def on_end(event: object) -> None:
            if isinstance(event, AutoRetryEndEvent) and not reached.is_set():
                reached.set()
                await release_end.wait()

        h.subscribe(on_end)
        release = release_end.set
    elif point in ("overflow-read", "overflow-compaction"):
        provider = _Provider({1: "overflow"})
        h, session, _ = await _compacting_harness(provider, overflow=True)
        if point == "overflow-read":
            reached, release_ev = _park_the_first_branch_read_after(session, _on_settled(h))
        else:
            reached, release_ev = _park_the_hook(h, "session_before_compact")
            phase = "compaction"
        release = release_ev.set
    elif point in ("threshold-read", "threshold-compaction"):
        provider = _Provider({1: "big"})
        h, session, _ = await _compacting_harness(provider, overflow=False)
        if point == "threshold-read":
            reached, release_ev = _park_the_first_branch_read_after(session, _on_settled(h))
        else:
            reached, release_ev = _park_the_hook(h, "session_before_compact")
            phase = "compaction"
        release = release_ev.set
    else:  # release-flush
        provider = _Provider({1: "retry"})
        session = Session(MemorySessionStorage())
        h = _retrying_harness(provider, session)
        backoff = _Backoff(h, [False])
        reached = asyncio.Event()
        release_append = asyncio.Event()

    watched = _watch_idle(h)
    first, waiter = await _start(h, watched)

    if point == "release-flush":
        await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
        await h.set_thinking_level("high")
        # Deterministic precondition: in the tail the setting is a mid-turn
        # write, queued for the release flush. On the base the phase said
        # idle here and it was appended at once — nothing to park on.
        assert [type(w).__name__ for w in h._pending_session_writes] == [
            "PendingThinkingLevelChangeWrite"
        ]
        real_append = session.append_thinking_level_change

        async def parked_append(level: str) -> Any:
            reached.set()
            await release_append.wait()
            return await real_append(level)

        session.append_thinking_level_change = parked_append  # type: ignore[method-assign]
        backoff.release[0].set()  # the retry is given up: only the release writes
        release = release_append.set

    await asyncio.wait_for(reached.wait(), WAIT)
    await _assert_everything_is_refused(h, watched, waiter, phase)

    release()
    await asyncio.wait_for(first, WAIT)
    await _assert_released_once(h, watched, waiter)
    await asyncio.wait_for(h.prompt("next"), WAIT)
    assert provider.seen[-1][-1] == "next"


# === What the refusal buys ================================================


async def test_the_retried_turn_sees_only_its_own_prompt() -> None:
    """The overlap's worst symptom: two turns sharing one context. On the base a
    second prompt ran inside the first one's backoff, and the first one's
    re-run then carried the second's message and answer — the provider saw
    ``[['first'], ['first', 'second'], ['first', 'second', 'answer 2']]``
    (design probe A18); live, the re-run answered ``ALPHA\\nBRAVO``."""

    provider = _Provider({1: "retry"})
    h = _retrying_harness(provider)
    backoff = _Backoff(h, [True])
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)

    try:
        await asyncio.wait_for(h.prompt("second"), WAIT)
        outcome = "accepted"
    except AgentHarnessError as exc:
        outcome = exc.code
    backoff.release[0].set()
    await asyncio.wait_for(first, WAIT)

    assert outcome == "busy"
    assert provider.seen == [["first"], ["first"]]


async def test_cancelling_the_first_prompt_in_its_backoff_gives_the_phase_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#321 ARM 7's surviving half: with no second prompt able to get in, a
    cancel in the backoff simply releases. Green on the base by construction;
    it pins that the release runs on the cancel path too."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)
    provider = _Provider({1: "retry"})
    h = _retrying_harness(provider)
    in_backoff = _in_backoff_signal(h)
    watched = _watch_idle(h)
    first, waiter = await _start(h, watched)
    await asyncio.wait_for(in_backoff.wait(), WAIT)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, WAIT)

    await _assert_released_once(h, watched, waiter)
    await asyncio.wait_for(h.prompt("next"), WAIT)


async def test_the_threshold_compaction_runs_inside_the_claim() -> None:
    """#321 ARM 12's surviving half. A successful turn over the threshold
    compacts from inside the prompt: the compaction is nested under the claim,
    so it needs no idle harness (the base's ``compact() requires idle harness``
    raise is gone with the gap that let a second prompt make it busy), and the
    idle event is not set until the prompt returns. On the base
    ``session_compact`` saw ``idle_event=False`` but a waiter parked before the
    prompt had already RETURNED, woken by ``_run``'s ``finally`` (critic probe
    K1t)."""

    provider = _Provider({1: "big"})
    h, _session, summariser = await _compacting_harness(provider, overflow=False)
    watched = _watch_idle(h)
    seen: list[tuple[str, str, bool, list[str], bool, str]] = []
    holder: dict[str, asyncio.Future[Any]] = {}

    async def on_compact(event: Any, *_args: Any) -> None:
        try:
            await h.prompt("from inside session_compact")
            refused = "accepted"
        except AgentHarnessError as exc:
            refused = exc.code
        seen.append(
            (
                event.reason,
                h.phase,
                h._idle_event.is_set(),
                list(watched.sets),
                holder["waiter"].done(),
                refused,
            )
        )

    h.hooks.on("session_compact", on_compact)  # type: ignore[call-overload]
    # The hook reads the waiter: store it before the first await (``_start``
    # let the prompt reach this hook first on py3.11, CI run 36052006149).
    first, waiter = _begin(h)
    holder["waiter"] = waiter
    await asyncio.wait_for(watched.parked.wait(), WAIT)

    await asyncio.wait_for(first, WAIT)

    assert seen == [("threshold", "compaction", False, [], False, "busy")]
    assert summariser.calls == 1
    await _assert_released_once(h, watched, waiter)


async def test_the_overflow_rerun_runs_inside_the_claim() -> None:
    """The overflow compaction nests and hands ``"turn"`` back, then the
    re-run runs under the same claim. The nested ``finally`` must leave the
    idle event CLEAR: setting it there, even with the phase right, woke a
    waiter parked before the prompt during the re-run's provider call (critic
    sabotage S-b) — exactly what the base did, via ``_run``'s and
    ``compact()``'s ``finally`` (critic probe K1)."""

    provider = _Provider({1: "overflow", 2: "park"})
    h, _session, summariser = await _compacting_harness(provider, overflow=True)
    watched = _watch_idle(h)
    first, waiter = await _start(h, watched)

    await asyncio.wait_for(provider.parked[2].wait(), WAIT)
    assert summariser.calls == 1
    await _assert_everything_is_refused(h, watched, waiter, "turn")

    provider.release[2].set()
    await asyncio.wait_for(first, WAIT)
    await _assert_released_once(h, watched, waiter)


async def test_the_silent_overflow_compaction_runs_inside_the_claim() -> None:
    """A completed answer over the window takes the overflow path with no
    re-run: ``compact(reason="overflow", will_retry=False, _claim=claim)``.
    Without its claim that ``compact()`` is refused busy — in silence, the
    recovery swallows the error at DEBUG — and the threshold check then
    compacts instead, with reason ``"threshold"`` (#334 verification sabotage
    M4b: dropping only that ``_claim=`` left every other test green)."""

    provider = _Provider({1: "silent-overflow"})
    h, _session, summariser = await _compacting_harness(provider, overflow=True)
    reasons: list[tuple[str, bool, str]] = []

    async def on_before(event: Any, *_args: Any) -> None:
        reasons.append((event.reason, event.will_retry, h.phase))

    compacted: list[str] = []

    async def on_compact(event: Any, *_args: Any) -> None:
        compacted.append(event.reason)

    h.hooks.on("session_before_compact", on_before)  # type: ignore[call-overload]
    h.hooks.on("session_compact", on_compact)  # type: ignore[call-overload]
    await asyncio.wait_for(h.prompt("first"), WAIT)

    assert compacted == ["overflow"]
    assert reasons == [("overflow", False, "compaction")]
    assert summariser.calls == 1
    assert provider.calls == 1  # no re-run: the answer completed
    _assert_idle(h)


def _fault_the_next_build_context(
    session: Session, fault: str
) -> tuple[Callable[[], None], asyncio.Event]:
    """Arm the NEXT ``build_context()`` — a re-run's — to raise or to park.
    It is ``_run``'s first await, before its ``try``."""

    real_build_context = session.build_context
    armed = {"on": False}
    reached = asyncio.Event()

    async def build_context() -> Any:
        if armed["on"]:
            armed["on"] = False
            reached.set()
            if fault == "raise":
                raise RuntimeError("session storage unavailable")
            await asyncio.Event().wait()
        return await real_build_context()

    session.build_context = build_context  # type: ignore[method-assign]

    def arm() -> None:
        armed["on"] = True

    return arm, reached


@pytest.mark.parametrize("fault", ["raise", "cancel"])
@pytest.mark.parametrize("site", ["retry", "overflow"])
async def test_a_rerun_that_fails_to_start_gives_the_phase_back(site: str, fault: str) -> None:
    """#321 ARMs 13/17's surviving half, at both re-run sites. The re-run's
    ``build_context()`` raises or is cancelled before ``_run``'s ``try``; the
    prompt's ``finally`` releases, once. Red on the base, but not for a missing
    release: #321's clause gave the phase back there too (design probes
    R13/O13), and ``_run``'s own ``finally`` had already set the idle event, so
    it was set twice (``['idle', 'idle']``). It pins the one release on both
    sites."""

    provider = _Provider({1: site})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    h._state.model = Model(id="m20k", context_window=20_000)
    arm, reached = _fault_the_next_build_context(session, fault)
    if site == "retry":
        h._state.auto_retry_enabled = True
        h._state.auto_compaction_enabled = False
        backoff = _Backoff(h, [True])
        backoff.release[0].set()
    else:
        h._state.auto_retry_enabled = False
        h._state.auto_compaction_enabled = True

        async def compact(*_args: Any, **_kwargs: Any) -> None:
            return None

        h.compact = compact  # type: ignore[method-assign]

    async def arm_after_the_first_run(*_args: Any) -> None:
        arm()

    unsubscribe = h.hooks.on("settled", arm_after_the_first_run)  # type: ignore[call-overload]
    watched = _watch_idle(h)
    first, waiter = await _start(h, watched)
    if fault == "cancel":
        await asyncio.wait_for(reached.wait(), WAIT)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, WAIT)
    else:
        with pytest.raises(RuntimeError, match="session storage unavailable"):
            await asyncio.wait_for(first, WAIT)

    await _assert_released_once(h, watched, waiter)
    unsubscribe()
    await asyncio.wait_for(h.prompt("next"), WAIT)
    await asyncio.wait_for(h.dispose(), WAIT)


# === Who meets the held turn ==============================================


async def test_dispose_during_the_backoff_waits_for_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dispose()`` aborts and waits whenever the phase is not idle. On the
    base the backoff said idle, so it skipped both and tore the harness down
    under a running prompt (design probe T3: ``#1 done: False;
    retry_aborted: False``). Now it aborts — ``abort()`` wakes the backoff
    through ``abort_retry()`` — and returns after the prompt."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)
    provider = _Provider({1: "retry"})
    h = _retrying_harness(provider)
    in_backoff = _in_backoff_signal(h)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_backoff.wait(), WAIT)

    await asyncio.wait_for(h.dispose(), WAIT)

    done, retry_aborted = first.done(), h._state.retry_aborted
    if not done:  # the base: end the orphaned prompt before failing
        h.abort_retry()
        await asyncio.wait_for(first, WAIT)
    assert done, "dispose() returned while the prompt was still in its backoff"
    assert retry_aborted
    assert provider.calls == 1
    _assert_idle(h)


@pytest.mark.parametrize("kind", ["set_thinking_level", "append_message"])
async def test_a_setting_changed_during_the_tail_is_recorded_by_the_time_the_prompt_returns(
    kind: str,
) -> None:
    """In the tail the phase says ``"turn"``, so a setter takes the mid-turn
    route onto the pending queue — and with no re-run to flush it (the retry
    is given up here) only the release's flush writes it. On the base
    ``set_thinking_level`` appended at once (idle) and ``append_message`` put
    the message in ``state.messages`` only: it never reached the session
    (design probe T11)."""

    provider = _Provider({1: "retry"})
    session = Session(MemorySessionStorage())
    h = _retrying_harness(provider, session)
    backoff = _Backoff(h, [False])
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)

    if kind == "set_thinking_level":
        await h.set_thinking_level("high")
    else:
        await h.append_message(UserMessage(content=[TextContent(text="appended")]))
    backoff.release[0].set()
    await asyncio.wait_for(first, WAIT)

    assert h._pending_session_writes == []
    entries = await session.get_branch()
    if kind == "set_thinking_level":
        assert [
            e.thinking_level for e in entries if e.type == "thinking_level_change"
        ] == ["high"]
    else:
        assert "appended" in [
            t for e in entries if e.type == "message" for t in _texts([e.message])
        ]


async def _parked_release_flush(
    *, also_model: bool = False
) -> tuple[AgentHarness, Session, asyncio.Future[Any], asyncio.Event]:
    """#1 given up after its backoff, with a thinking-level write (and, with
    ``also_model``, a model change behind it) queued in the tail, and the
    release flush parked on the thinking level's session append."""

    provider = _Provider({1: "retry"})
    session = Session(MemorySessionStorage())
    h = _retrying_harness(provider, session)
    backoff = _Backoff(h, [False])
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(backoff.entered[0].wait(), WAIT)
    await h.set_thinking_level("high")
    # Deterministic precondition — on the base it was appended at once, and
    # there was no release flush to park.
    assert [type(w).__name__ for w in h._pending_session_writes] == [
        "PendingThinkingLevelChangeWrite"
    ]
    if also_model:
        await h.set_model(Model(id="queued-model", context_window=100_000))
    reached = asyncio.Event()
    release = asyncio.Event()
    real_append = session.append_thinking_level_change

    async def parked_append(level: str) -> Any:
        reached.set()
        await release.wait()
        return await real_append(level)

    session.append_thinking_level_change = parked_append  # type: ignore[method-assign]
    backoff.release[0].set()
    await asyncio.wait_for(reached.wait(), WAIT)
    return h, session, first, release


async def test_writes_made_while_the_release_flush_awaits_are_written_before_the_prompt_returns() -> None:
    """The release flush loops. A write made while it awaits a session append
    still meets phase ``"turn"`` and lands on the fresh queue; one pass left it
    pending on an idle harness (critic probe K2 on the one-pass version:
    ``STILL PENDING on an idle harness: ['PendingModelChangeWrite',
    'PendingMessageWrite']``), where the next idle setter's direct append would
    overtake it."""

    h, session, first, release = await _parked_release_flush()
    assert h.phase == "turn"
    await h.set_model(Model(id="late-model", context_window=100_000))
    await h.append_message(UserMessage(content=[TextContent(text="late-append")]))
    assert [type(w).__name__ for w in h._pending_session_writes] == [
        "PendingModelChangeWrite",
        "PendingMessageWrite",
    ]

    release.set()
    await asyncio.wait_for(first, WAIT)

    _assert_idle(h)
    assert h._pending_session_writes == []
    entries = await session.get_branch()
    tail = [e.type for e in entries][-3:]
    assert tail == ["thinking_level_change", "model_change", "message"]
    assert _texts([entries[-1].message]) == ["late-append"]


async def test_a_prompt_cancelled_in_its_release_flush_still_releases() -> None:
    """The flush sits inside the release's ``try``; the phase goes back in the
    inner ``finally``, so a cancel in the flush still releases. The flush
    hands back the writes it had not attempted (#301's rule): the one in
    flight is not requeued, the one behind it is."""

    h, _session, first, _release = await _parked_release_flush(also_model=True)
    assert h._pending_session_writes == []  # both detached by the flush
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, WAIT)

    _assert_idle(h)
    assert [type(w).__name__ for w in h._pending_session_writes] == [
        "PendingModelChangeWrite"
    ]
    await asyncio.wait_for(h.prompt("next"), WAIT)


async def _cancelled_with_medium_left_queued() -> tuple[AgentHarness, Session, asyncio.Event]:
    """Codex's C4 shape: ``medium`` is set while the release flush is parked on
    ``high``'s append (the phase still says "turn", so it queues), then the
    prompt is cancelled. The release goes idle with ``medium`` still queued —
    the cancel must release (#321) — and ``high``'s append, cancelled in
    flight, never landed. The parked append is freed for everything after."""

    h, session, first, release = await _parked_release_flush()
    await h.set_thinking_level("medium")
    assert [type(w).__name__ for w in h._pending_session_writes] == [
        "PendingThinkingLevelChangeWrite"
    ]
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, WAIT)
    _assert_idle(h)
    assert [w.thinking_level for w in h._pending_session_writes] == ["medium"]  # type: ignore[union-attr]
    release.set()
    return h, session, release


async def _thinking_levels(session: Session) -> list[str | None]:
    entries = await session.get_branch()
    return [e.thinking_level for e in entries if e.type == "thinking_level_change"]


async def test_a_level_set_after_a_cancelled_release_goes_behind_the_one_it_left_queued() -> None:
    """C4 (a). A newer idle ``set_thinking_level`` must not reach the session
    ahead of the older queued one. On the #334 commit before the fix it was
    appended at once and the next turn's flush wrote ``medium`` after it —
    Codex: ``release cancel: thinking writes ['low', 'medium']`` — so a resume
    restored the older level."""

    h, session, _release = await _cancelled_with_medium_left_queued()

    await asyncio.wait_for(h.set_thinking_level("low"), WAIT)

    assert h._pending_session_writes == []
    assert await _thinking_levels(session) == ["medium", "low"]
    await asyncio.wait_for(h.prompt("next"), WAIT)
    assert await _thinking_levels(session) == ["medium", "low"]
    assert (await session.build_context()).thinking_level == "low"


async def test_the_next_prompt_writes_what_a_cancelled_release_left_queued_first() -> None:
    """The next ``prompt()`` drains the leftover before it writes its own
    messages — pi flushes its pending messages "before the new prompt"
    (``agent-session.ts:1669-1671`` @ a328aa89a). Before the fix the leftover
    waited for the new turn's ``turn_end`` flush and landed after the new
    user message and its answer."""

    h, session, _release = await _cancelled_with_medium_left_queued()

    await asyncio.wait_for(h.prompt("next"), WAIT)

    assert h._pending_session_writes == []
    kinds = [
        e.thinking_level if e.type == "thinking_level_change" else _texts([e.message])
        for e in await session.get_branch()
        if e.type in ("thinking_level_change", "message")
    ]
    assert kinds.index("medium") < kinds.index(["next"]), kinds


async def test_dispose_writes_what_a_cancelled_release_left_queued() -> None:
    """C4 (b). Nothing queued is lost at ``dispose()`` (the runtime's session
    replace goes through it). Before the fix: Codex ``release cancel + dispose:
    pending ['PendingThinkingLevelChangeWrite'] persisted_thinking []``."""

    h, session, _release = await _cancelled_with_medium_left_queued()

    await asyncio.wait_for(h.dispose(), WAIT)

    assert h._pending_session_writes == []
    assert await _thinking_levels(session) == ["medium"]


async def test_a_compaction_writes_what_a_cancelled_release_left_queued_first() -> None:
    """A public ``compact()`` drains the leftover before it reads the branch,
    so the leftover is on disk ahead of anything the compaction writes. (This
    session has nothing to cut, so the compaction itself ends in "Nothing to
    compact" — after the drain.) Before the fix it stayed queued."""

    h, session, _release = await _cancelled_with_medium_left_queued()

    with pytest.raises(AgentHarnessError, match="Nothing to compact"):
        await asyncio.wait_for(h.compact(), WAIT)

    assert h._pending_session_writes == []
    assert await _thinking_levels(session) == ["medium"]
    _assert_idle(h)


async def test_a_tree_navigation_writes_what_a_cancelled_release_left_queued_on_the_old_branch() -> None:
    """``navigate_tree()`` drains the leftover before it moves the leaf, so the
    write lands on the branch it was made on. Before the fix it stayed queued,
    and the next flush would have put it on the branch navigated to."""

    h, session, _release = await _cancelled_with_medium_left_queued()
    old_leaf = await session.get_leaf_id()
    first_user = next(e for e in await session.get_branch() if e.type == "message")

    await asyncio.wait_for(h.navigate_tree(first_user.id), WAIT)

    assert h._pending_session_writes == []
    written = [e for e in await session.get_entries() if e.type == "thinking_level_change"]
    assert [e.thinking_level for e in written] == ["medium"]
    assert written[0].parent_id == old_leaf
    assert await session.get_leaf_id() != written[0].id
    _assert_idle(h)


async def test_a_write_made_while_a_drain_is_appending_waits_for_it() -> None:
    """The drain detaches the queue before it appends, so an empty queue does
    not mean nothing is in flight. Here the drain started by ``low`` is parked
    on ``medium``'s append with ``low`` detached behind it; ``x`` set meanwhile
    must still go behind both, not straight to the session."""

    h, session, first, release = await _parked_release_flush()
    await h.set_thinking_level("medium")
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, WAIT)
    # ``release`` stays clear: every thinking-level append parks on it again.
    called: list[str] = []
    reached_again = asyncio.Event()
    parked_append = session.append_thinking_level_change

    async def recording_append(level: str) -> Any:
        called.append(level)
        reached_again.set()
        return await parked_append(level)

    session.append_thinking_level_change = recording_append  # type: ignore[method-assign]
    low = asyncio.ensure_future(h.set_thinking_level("low"))
    await asyncio.wait_for(reached_again.wait(), WAIT)
    assert called == ["medium"]
    assert h._pending_session_writes == []  # medium and low both detached

    # ``x``'s select hook runs, then nothing in the setter awaits before its
    # write: the test resumes only once ``x`` has parked — on the drain, or
    # (the defect) inside a direct append.
    selected = asyncio.Event()

    async def on_select(event: Any, *_args: Any) -> None:
        if event.level == "x":
            selected.set()

    h.hooks.on("thinking_level_select", on_select)  # type: ignore[call-overload]
    x = asyncio.ensure_future(h.set_thinking_level("x"))
    await asyncio.wait_for(selected.wait(), WAIT)
    assert called == ["medium"], "x went straight to the session past the drain"
    assert [w.thinking_level for w in h._pending_session_writes] == ["x"]  # type: ignore[union-attr]

    release.set()
    await asyncio.wait_for(asyncio.gather(low, x), WAIT)

    assert h._pending_session_writes == []
    assert called == ["medium", "low", "x"]
    assert await _thinking_levels(session) == ["medium", "low", "x"]


async def _an_idle_drain_parked_on_medium() -> tuple[
    AgentHarness, Session, asyncio.Event, asyncio.Future[Any]
]:
    """C4's leftover ``medium``, then an idle ``set_thinking_level("low")``
    whose drain has detached ``[medium, low]`` and is parked on ``medium``'s
    append: the queue is EMPTY while the drain lock is held. ``gate`` frees
    the append; ``low`` is the setter's task."""

    h, session, first, release = await _parked_release_flush()
    await h.set_thinking_level("medium")
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, WAIT)
    gate = asyncio.Event()
    reached = asyncio.Event()
    unparked_append = type(session).append_thinking_level_change

    async def append(level: str) -> Any:
        if level == "medium":
            reached.set()
            await gate.wait()
        return await unparked_append(session, level)

    session.append_thinking_level_change = append  # type: ignore[method-assign]
    release.set()
    low = asyncio.ensure_future(h.set_thinking_level("low"))
    await asyncio.wait_for(reached.wait(), WAIT)
    # The precondition every test below leans on: nothing queued, a drain
    # still in flight.
    assert h._pending_session_writes == []
    assert h._drain_lock.locked()
    assert h.phase == "idle"
    return h, session, gate, low


async def _settle() -> None:
    """Let every runnable task run to its next suspension. Nothing here can
    finish on its own: the only drain is parked on ``gate``."""

    for _ in range(20):
        await asyncio.sleep(0)


def _branch_order(entries: Any) -> list[Any]:
    return [
        e.thinking_level if e.type == "thinking_level_change" else _texts([e.message])
        for e in entries
        if e.type in ("thinking_level_change", "message")
    ]


async def test_a_prompt_entering_while_a_drain_appends_waits_for_it() -> None:
    """The drain's fast path returns only when nothing is queued AND no drain
    holds the lock: an empty queue does not mean nothing is in flight. A
    prompt entering while an idle drain still appends ``medium`` and ``low``
    waits at its entry and writes its own messages after them. With the fast
    path checking the queue alone (#334 verification sabotage M3) the prompt
    went straight through: ``[['first'], [], ['second'], ['answer 2'],
    'medium', 'low']`` (probe P6)."""

    h, session, gate, low = await _an_idle_drain_parked_on_medium()

    second = asyncio.ensure_future(h.prompt("second"))
    await _settle()
    assert h.phase == "turn" and not second.done()
    assert ["second"] not in _branch_order(await session.get_branch()), (
        "the prompt wrote past a drain still appending older writes"
    )

    gate.set()
    await asyncio.wait_for(asyncio.gather(low, second), WAIT)

    order = _branch_order(await session.get_branch())
    assert order.index("medium") < order.index("low") < order.index(["second"]), order
    _assert_idle(h)


async def test_a_tree_navigation_entering_while_a_drain_appends_waits_for_it() -> None:
    """The same for ``navigate_tree()``: it must not move the leaf while a
    drain still holds writes made on the old branch. Under sabotage M3 it
    moved the leaf first and ``medium`` was written with ``parent_id=None``
    (probe P5)."""

    h, session, gate, low = await _an_idle_drain_parked_on_medium()
    old_leaf = await session.get_leaf_id()
    first_user = next(e for e in await session.get_branch() if e.type == "message")

    nav = asyncio.ensure_future(h.navigate_tree(first_user.id))
    await _settle()
    assert await session.get_leaf_id() == old_leaf, (
        "navigate_tree() moved the leaf while a drain still held older writes"
    )

    gate.set()
    await asyncio.wait_for(asyncio.gather(low, nav), WAIT)

    written = [e for e in await session.get_entries() if e.type == "thinking_level_change"]
    assert [e.thinking_level for e in written] == ["medium", "low"]
    assert written[0].parent_id == old_leaf
    assert written[1].parent_id == written[0].id
    assert await session.get_leaf_id() not in (old_leaf, written[1].id)
    _assert_idle(h)


async def _a_prompt_cancelled_at_its_entry_drain_with_x_queued() -> tuple[
    AgentHarness, Session, asyncio.Event, asyncio.Future[Any], asyncio.Future[Any]
]:
    """#334 verification P1's shape, up to the release: a second prompt waits
    at its entry for the idle drain, ``x`` is set under its claim (phase
    "turn", so it queues), and the prompt is cancelled there. Its release now
    waits for the drain — parked, with ``x`` still queued and not on disk."""

    h, session, gate, low = await _an_idle_drain_parked_on_medium()
    second = asyncio.ensure_future(h.prompt("second"))
    await _settle()
    assert h.phase == "turn" and not second.done()
    await h.set_thinking_level("x")
    assert [w.thinking_level for w in h._pending_session_writes] == ["x"]  # type: ignore[union-attr]

    second.cancel()
    await _settle()
    assert await _thinking_levels(session) == [], (
        "the cancelled prompt's release wrote x ahead of the drain's medium and low"
    )
    assert h.phase == "turn" and not second.done()
    assert [w.thinking_level for w in h._pending_session_writes] == ["x"]  # type: ignore[union-attr]
    return h, session, gate, low, second


async def test_a_prompt_cancelled_at_its_entry_drain_releases_behind_the_drain() -> None:
    """R1 of the #334 verification. The entry drain waited for the in-flight
    drain, but the release flushed without the lock: cancelled at its entry,
    the prompt wrote ``x`` (queued under its claim) while the other drain
    still held ``medium`` and ``low`` — on 000bc8f5 ``P1 final on disk:
    ['x', 'medium', 'low'] live x restored low``. The release now drains
    through the lock: ``x`` lands last, and the cancel still releases."""

    h, session, gate, low, second = await _a_prompt_cancelled_at_its_entry_drain_with_x_queued()

    gate.set()
    await asyncio.wait_for(low, WAIT)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second, WAIT)

    assert h._pending_session_writes == []
    assert await _thinking_levels(session) == ["medium", "low", "x"]
    assert (await session.build_context()).thinking_level == "x"
    _assert_idle(h)


async def test_a_second_cancel_while_the_release_waits_for_a_drain_still_releases() -> None:
    """"A cancel always releases" holds when the release itself is cancelled
    while it waits for the drain lock: the ``CancelledError`` leaves through
    the inner ``finally`` like the first. ``x`` stays queued, and the drain
    holding the lock writes it when its loop comes round — after ``medium``
    and ``low``."""

    h, session, gate, low, second = await _a_prompt_cancelled_at_its_entry_drain_with_x_queued()

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second, WAIT)
    _assert_idle(h)
    assert [w.thinking_level for w in h._pending_session_writes] == ["x"]  # type: ignore[union-attr]
    assert h._drain_lock.locked()  # still the setter's drain

    gate.set()
    await asyncio.wait_for(low, WAIT)

    assert h._pending_session_writes == []
    assert await _thinking_levels(session) == ["medium", "low", "x"]
    _assert_idle(h)


async def test_a_release_with_nothing_queued_waits_for_the_drain_appending_its_write() -> None:
    """The release drains through the lock even when its OWN queue is empty.
    ``x`` is set under the second prompt's claim while that prompt waits at
    its entry for an idle setter's drain; the drain's loop comes round,
    detaches ``x`` and parks on its append — the queue is empty, the lock
    held. The prompt is cancelled there. Its release must wait for that drain:
    ``prompt()`` returns only once ``x`` is on disk. With the release draining
    only when something is queued (#334 round-4 verification sabotage M10,
    which left every test green) it went idle at once with ``x`` still in
    flight — probe H6: ``#2 done True phase idle on disk ['medium', 'low']``."""

    h, session, gate, low = await _an_idle_drain_parked_on_medium()
    # Appends completed through the wrapper below (``medium``'s is already
    # under way, inside the helper's parked one, so it is not counted), and
    # what they were at the instant ``prompt()`` returned.
    written: list[str] = []
    written_when_prompt_returned: list[list[str]] = []

    async def prompt_second() -> None:
        try:
            await h.prompt("second")
        finally:
            written_when_prompt_returned.append(list(written))

    second = asyncio.ensure_future(prompt_second())
    await _settle()
    assert h.phase == "turn" and not second.done()
    await h.set_thinking_level("x")
    assert [w.thinking_level for w in h._pending_session_writes] == ["x"]  # type: ignore[union-attr]

    x_reached = asyncio.Event()
    x_gate = asyncio.Event()
    medium_parked_append = session.append_thinking_level_change

    async def append(level: str) -> Any:
        if level == "x":
            x_reached.set()
            await x_gate.wait()
        result = await medium_parked_append(level)
        written.append(level)
        return result

    session.append_thinking_level_change = append  # type: ignore[method-assign]
    gate.set()
    await asyncio.wait_for(x_reached.wait(), WAIT)
    # The precondition: the setter's drain holds ``x`` detached, in flight.
    assert written == ["low"]
    assert await _thinking_levels(session) == ["medium", "low"]
    assert h._pending_session_writes == []
    assert h._drain_lock.locked()
    assert not second.done()

    second.cancel()
    await _settle()
    assert not second.done(), (
        "the release went idle while the drain was still appending the prompt's own write"
    )
    assert h.phase == "turn"
    assert not h._idle_event.is_set()

    x_gate.set()
    await asyncio.wait_for(low, WAIT)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second, WAIT)

    assert written_when_prompt_returned == [["low", "x"]], (
        "prompt() returned before x was on disk"
    )
    assert await _thinking_levels(session) == ["medium", "low", "x"]
    assert h._pending_session_writes == []
    _assert_idle(h)


async def test_the_drain_returns_without_suspending_when_nothing_is_queued_or_in_flight() -> None:
    """``prompt()`` passes the drain twice — at its entry and at its release —
    and with nothing queued and no drain holding the lock the drain must not
    suspend: a suspension there would be a new cancellation point on every
    prompt's happy path. Driven by hand, so no loop timing is involved: the
    first ``send`` must finish the coroutine. Under a one-line ``await
    asyncio.sleep(0)`` at the drain's start (#334 second Codex review, finding
    5) every other test stayed green (``47 passed``). The other side — with a
    drain holding the lock it suspends — is checked by hand below too, and
    pinned end to end by the entering-during-a-drain tests."""

    h = AgentHarness(
        AgentHarnessOptions(session=Session(MemorySessionStorage()), stream_fn=_Provider({}))
    )
    assert h._pending_session_writes == [] and not h._drain_lock.locked()

    drain = h._drain_pending_session_writes()
    try:
        with pytest.raises(StopIteration):
            drain.send(None)
    finally:
        drain.close()

    await h._drain_lock.acquire()  # uncontended: no suspension
    try:
        drain = h._drain_pending_session_writes()
        try:
            drain.send(None)  # parks in the lock's acquire: no StopIteration
        finally:
            drain.close()
    finally:
        h._drain_lock.release()
    assert not h._drain_lock.locked()
    assert h._pending_session_writes == []


async def test_a_prompt_cancelled_in_its_runs_closing_flush_drops_the_turn_state() -> None:
    """#335's side note: a cancel while ``_run``'s own ``finally`` awaits its
    flush skips that ``finally``'s ``_turn_state = None``, and the stale turn
    state then answers ``_current_system_prompt()`` for the idle harness. The
    release resets it too."""

    provider = _Provider({})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))

    async def queue_a_write(*_args: Any) -> None:
        # ``settled`` fires inside ``_run`` after the turn_end flush, with the
        # phase at "turn": this write is left for ``_run``'s closing flush.
        await h.append_message(UserMessage(content=[TextContent(text="late")]))

    h.hooks.on("settled", queue_a_write)  # type: ignore[call-overload]
    reached = asyncio.Event()
    real_append = session.append_message

    async def parked_append(message: Any, *args: Any, **kwargs: Any) -> Any:
        if _texts([message]) == ["late"] and not reached.is_set():
            reached.set()
            await asyncio.Event().wait()
        return await real_append(message, *args, **kwargs)

    session.append_message = parked_append  # type: ignore[method-assign]
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(reached.wait(), WAIT)
    assert h._turn_state is not None  # the precondition: still in _run's finally
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, WAIT)

    _assert_idle(h)


async def test_an_extension_that_triggers_a_turn_during_the_backoff_queues_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``send_message(trigger_turn=True)`` starts a turn only on an idle
    harness; otherwise it goes on the ``next_turn`` queue (Aelix's mid-turn
    rule, kept — pi steers it instead). On the base the backoff said idle and
    a second prompt reached the provider (design probe T2).

    The barrier is what makes "no second call" mean something: the base's
    second turn is a task that has not started when the action returns, so #1
    is driven to completion and every pinned task awaited before counting."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)
    provider = _Provider({1: "retry"})  # a second call, if any, answers at once
    h = _retrying_harness(provider)
    in_backoff = _in_backoff_signal(h)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_backoff.wait(), WAIT)

    h._action_send_message(
        UserMessage(content=[TextContent(text="from-ext")]), trigger_turn=True
    )

    h.abort_retry()
    await asyncio.wait_for(first, WAIT)
    for task in list(h._pending_tasks):
        await asyncio.wait_for(task, WAIT)
    assert provider.calls == 1
    assert _texts(h._next_turn_queue) == ["from-ext"]


async def test_a_stale_claim_does_not_let_a_compaction_in() -> None:
    """Nesting is keyed on ``is self._claim``, not on any token: a finished
    prompt's claim handed to ``compact()`` during another prompt's turn is
    still refused ``busy``."""

    provider = _Provider({2: "park"})
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(session=session, stream_fn=provider))
    claims: list[object] = []

    async def record(*_args: Any) -> None:
        claims.append(h._claim)

    h.hooks.on("before_agent_start", record)  # type: ignore[call-overload]
    await asyncio.wait_for(h.prompt("first"), WAIT)
    second = asyncio.ensure_future(h.prompt("second"))
    await asyncio.wait_for(provider.parked[2].wait(), WAIT)

    assert claims[0] is not None and claims[0] is not claims[1]
    with pytest.raises(AgentHarnessError) as refused:
        await h.compact(_claim=claims[0])
    assert refused.value.code == "busy"
    assert h.phase == "turn"

    provider.release[2].set()
    await asyncio.wait_for(second, WAIT)
    _assert_idle(h)


# === An abort stops the tail ==============================================


async def test_an_abort_in_the_threshold_check_stops_the_compaction() -> None:
    """pi never compacts after an abort (``_handlePostAgentRun`` checks
    ``_agentRunAbortRequested`` after every await, ``agent-session.ts:1497-1528``
    @ a328aa89a). An ``abort()`` while the threshold check reads the branch
    now stops it before the summariser; on the base the compaction ran anyway
    (critic probe K4: ``summariser calls (compaction after abort): 1``)."""

    provider = _Provider({1: "big"})
    h, session, summariser = await _compacting_harness(provider, overflow=False)
    reached, release = _park_the_first_branch_read_after(session, _on_settled(h))
    compactions: list[str] = []

    async def on_compact(event: Any, *_args: Any) -> None:
        compactions.append(event.reason)

    h.hooks.on("session_compact", on_compact)  # type: ignore[call-overload]
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(reached.wait(), WAIT)

    await h.abort()
    release.set()
    await asyncio.wait_for(first, WAIT)

    assert summariser.calls == 0
    assert compactions == []
    _assert_idle(h)


async def test_disposing_a_turn_in_flight_does_not_compact_after_the_abort() -> None:
    """``dispose()`` of a turn whose context is over the threshold: the
    aborted run's tail used to call the summariser, and — now that
    ``dispose()`` waits for the whole prompt — it would have waited for that
    summarisation too. On the base the compaction ran on a disposed harness
    (critic probe K3)."""

    provider = _Provider({1: "park"})
    h, _session, summariser = await _compacting_harness(provider, overflow=False)
    # An earlier answer already over the threshold: the aborted run's estimate
    # walks back to its usage (pi's ``estimateContextTokens``).
    h._state.messages.extend(
        [
            UserMessage(content=[TextContent(text="earlier")]),
            AssistantMessage(
                content=[TextContent(text="earlier answer")],
                stop_reason="end_turn",
                usage={"total_tokens": 5_000},
            ),
        ]
    )
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(provider.parked[1].wait(), WAIT)

    await asyncio.wait_for(h.dispose(), WAIT)
    await asyncio.wait_for(first, WAIT)

    assert summariser.calls == 0


async def test_an_abort_in_the_overflow_read_stops_the_compaction_and_the_rerun() -> None:
    """The overflow recovery's own branch read: an ``abort()`` there skips the
    compaction as well as the re-run (on the base both ran)."""

    provider = _Provider({1: "overflow"})
    h, session, summariser = await _compacting_harness(provider, overflow=True)
    reached, release = _park_the_first_branch_read_after(session, _on_settled(h))
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(reached.wait(), WAIT)

    await h.abort()
    release.set()
    await asyncio.wait_for(first, WAIT)

    assert summariser.calls == 0
    assert provider.calls == 1
    _assert_idle(h)


async def test_an_abort_while_the_overflow_compaction_runs_stops_the_rerun() -> None:
    """The overflow re-run's ``_run`` cleared ``_abort_requested`` at its entry,
    so an ``abort()`` made while the overflow compaction ran was lost and the
    re-run went to the provider anyway (design probe T10: ``provider calls:
    2`` on both trees). The tail now checks the flag after the compaction."""

    provider = _Provider({1: "overflow"})
    h, _session, summariser = await _compacting_harness(provider, overflow=True)
    reached, release = _park_the_hook(h, "session_before_compact")
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(reached.wait(), WAIT)

    await h.abort()
    release.set()
    await asyncio.wait_for(first, WAIT)

    assert provider.calls == 1
    _assert_idle(h)


@pytest.mark.parametrize("hook", ["turn_end", "settled"])
async def test_an_extension_abort_after_a_failed_run_stops_its_retry(hook: str) -> None:
    """An extension's ``ctx.abort()`` only sets ``_abort_requested`` (it
    cancels no task). Made in a ``turn_end`` or ``settled`` handler of a run
    that failed retryably — after the run's loop, before ``_run`` returns — it
    now stops the retry at ``prompt()``'s retry-loop check: no backoff, one
    provider call, the prompt ends on the error. On the base ``_run``'s entry
    cleared the flag and the retry went out (review probe R2: ``provider
    calls 2, last: end_turn``). pi's ``ctx.abort()`` is ``this.abort()``
    (``agent-session.ts:3101-3106`` @ a328aa89a)."""

    provider = _Provider({1: "retry"})
    h = _retrying_harness(provider)
    backoff = _Backoff(h, [True])
    backoff.release[0].set()
    fired: list[str] = []

    async def abort_once(_event: Any, ctx: Any) -> None:
        if not fired:
            fired.append(hook)
            ctx.abort()

    h.hooks.on(hook, abort_once)  # type: ignore[call-overload]
    await asyncio.wait_for(h.prompt("first"), WAIT)

    assert fired == [hook]
    assert backoff.n == 0, "the retry backoff ran after ctx.abort()"
    assert provider.calls == 1
    last = h._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "error"
    _assert_idle(h)


async def test_an_extension_abort_in_turn_end_skips_the_threshold_compaction() -> None:
    """The same flag stops the closing threshold compaction: a ``turn_end``
    handler's ``ctx.abort()`` on a turn over the threshold leaves the
    summariser uncalled (review probe R3: ``summariser calls 0``; 1 on the
    base)."""

    provider = _Provider({1: "big"})
    h, _session, summariser = await _compacting_harness(provider, overflow=False)

    async def abort(_event: Any, ctx: Any) -> None:
        ctx.abort()

    h.hooks.on("turn_end", abort)  # type: ignore[call-overload]
    await asyncio.wait_for(h.prompt("first"), WAIT)

    assert provider.calls == 1
    assert summariser.calls == 0
    _assert_idle(h)


async def test_an_abort_during_an_async_auto_retry_start_subscriber_cancels_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``abort()`` while an async ``auto_retry_start`` subscriber holds the
    emit finds no backoff event to wake yet — only ``retry_aborted``. It now
    counts when the event is made, so the backoff ends at once and the re-run
    never goes out. On the base the whole backoff ran and the re-run reached
    the provider (critic probe K5 at a 1 s base delay: ``dispose() returned
    1.00 s after the subscriber released``, ``provider calls: 2``). At the 60 s
    base delay used here the base cannot finish inside ``WAIT``."""

    monkeypatch.setattr(core_mod, "_AUTO_RETRY_BASE_DELAY_MS", 60_000)
    provider = _Provider({1: "retry"})
    h = _retrying_harness(provider)
    in_subscriber = asyncio.Event()
    release_subscriber = asyncio.Event()
    ends: list[AutoRetryEndEvent] = []

    async def subscriber(event: object) -> None:
        if isinstance(event, AutoRetryStartEvent) and not in_subscriber.is_set():
            in_subscriber.set()
            await release_subscriber.wait()
        if isinstance(event, AutoRetryEndEvent):
            ends.append(event)

    h.subscribe(subscriber)
    first = asyncio.ensure_future(h.prompt("first"))
    await asyncio.wait_for(in_subscriber.wait(), WAIT)

    await h.abort()
    release_subscriber.set()
    await asyncio.wait_for(first, WAIT)

    assert provider.calls == 1
    assert [(e.success, e.final_error) for e in ends] == [(False, "Retry cancelled")]
    _assert_idle(h)
