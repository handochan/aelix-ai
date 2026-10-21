"""#565 - automatic compaction keeps working after a session's first compaction.

The staleness guards (pi ``agent-session.ts`` ``_checkCompaction``: an answer
from before the latest compaction must not trigger another) compare an answer's
``timestamp`` with the compaction entry's time. pi stamps both in milliseconds
(``Date.now()``). aelix stamps a turn's messages with ``time.time()`` - seconds
(``loop.py``) - while ``get_latest_compaction_boundary_ms`` gives the entry's time
in milliseconds, so every answer after a session's first compaction read as
1000 times older than the boundary: ``_check_auto_compaction``'s Guards A and B
and ``_try_overflow_recovery``'s Guard A skipped it, and no automatic compaction
ever ran again. Measured with the real CLI on ``c64bec3f`` (every answer
reporting 190000 of a 200000 window): one compaction at turn 2, then none at
turns 3-5 (``.omc/probes/b4-a-live/r2/565-*``); in a long session the turns
then failed with ``prompt is too long`` and no recovery.

The guards now read both in milliseconds (``harness/core.py`` ``_timestamp_ms``,
the rule ``providers/_token_estimate.py`` ``_as_ms`` already used): a value below
1e11 is seconds. Sessions on disk keep their seconds; nothing is rewritten.

The rows drive the real ``prompt()`` - the loop stamps the messages - against a
scripted provider, on a memory session and on a JSONL file reopened as
``--continue`` does.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions, _timestamp_ms
from aelix_agent_core.session import (
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.session.compaction import (
    SUMMARIZATION_SYSTEM_PROMPT,
    get_latest_compaction_boundary_ms,
)
from aelix_agent_core.session.context import build_session_context
from aelix_agent_core.types import CompactionEndEvent
from aelix_ai import clear_providers, register_provider
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import AssistantDoneEvent, AssistantErrorEvent, Model, TextDeltaEvent

_API = "auto-compaction-after-a-compaction-565"
#: Threshold 20000 - 1000 = 19000; a "big" answer (19500) is past it, an "ok" one is not.
_MODEL = Model(api=_API, id="m", provider="p", context_window=20_000)
_SETTINGS = {
    "retry": {"maxRetries": 0},
    "compaction": {"reserveTokens": 1_000, "keepRecentTokens": 5},
}
_OVERFLOW = "prompt is too long: 25000 tokens > 20000 maximum"


@pytest.fixture(autouse=True)
def _providers() -> Any:
    clear_providers()
    yield
    clear_providers()


class _Provider:
    """A turn step is ``big`` (an answer reporting 19500 tokens), ``mid`` (18000),
    ``ok`` (10), ``overflow`` or ``400`` (a refusal that is neither retried nor an
    overflow); a summary always answers. Past its script a plan repeats its last
    step."""

    def __init__(self, turn: list[str]) -> None:
        self.turn = list(turn)
        self.calls = {"turn": 0, "summary": 0}
        register_provider(_API, self._stream)

    async def _stream(self, _model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        is_summary = getattr(context, "system_prompt", None) == SUMMARIZATION_SYSTEM_PROMPT
        self.calls["summary" if is_summary else "turn"] += 1
        step = "ok"
        if not is_summary and self.turn:
            step = self.turn.pop(0) if len(self.turn) > 1 else self.turn[0]
        if step in ("overflow", "400"):
            text = _OVERFLOW if step == "overflow" else "Error code: 400 - refused (probe)"
            message = AssistantMessage(content=[], stop_reason="error", error_message=text)
            yield AssistantErrorEvent(reason="error", error=message, error_message=text)
            return
        text = "## Goal\nthe summary" if is_summary else "the answer"
        yield TextDeltaEvent(delta=text)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text=text)],
                stop_reason="end_turn",
                usage={"total_tokens": {"big": 19_500, "mid": 18_000}.get(step, 10)},
            ),
        )


def _harness(session: Session) -> tuple[AgentHarness, list[Any]]:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=_MODEL,
            session=session,
            get_api_key_and_headers=_auth,
            settings_manager=SettingsManager.in_memory(_SETTINGS),
        )
    )
    events: list[Any] = []
    harness.subscribe(lambda event: events.append(event) or None)
    return harness, events


async def _auth(_model: Any) -> Any:
    return {"apiKey": "fake-key", "headers": {}}


def _ends(events: list[Any]) -> list[tuple[str, bool, str | None]]:
    """Every ``compaction_end`` but "Nothing to compact" (turn 1 has nothing to cut)."""

    return [
        (e.reason, e.will_retry, e.error_message)
        for e in events
        if isinstance(e, CompactionEndEvent) and e.error_message != "Nothing to compact"
    ]


async def _compactions(session: Session) -> int:
    return sum(1 for e in await session.get_entries() if e.type == "compaction")


async def _past_the_boundary(session: Session) -> None:
    """Wait for the wall clock to pass the latest compaction entry's time.

    A real turn always does (a provider round trip is milliseconds at least); the
    scripted provider answers at once, and the Windows clock of Python <= 3.12
    advances in about 15.6 ms ticks, so an answer could share the entry's tick
    and read as stale (pi's guard is ``<=`` too). This waits for that condition,
    not for a length of time.
    """

    boundary = get_latest_compaction_boundary_ms(await session.get_branch())
    assert boundary is not None
    while time.time() * 1000.0 <= boundary:
        await asyncio.sleep(0.001)


async def _first_compaction_then_two_short_turns(harness: AgentHarness, session: Session) -> None:
    """Turns 1-4 of a ``["big", "big", "ok", "ok", ...]`` script: the threshold
    compaction after turn 2, then two short turns for a later compaction to cut."""

    for n in (1, 2, 3, 4):
        await harness.prompt(f"turn {n}")
    assert await _compactions(session) == 1
    await _past_the_boundary(session)


# === the two cases the issue names ===============================================


async def test_a_second_threshold_compaction_runs_after_the_first() -> None:
    provider = _Provider(["big"])
    session = Session(MemorySessionStorage())
    harness, events = _harness(session)
    await harness.prompt("turn 1")  # nothing to cut yet: the only turn is kept
    await harness.prompt("turn 2")
    assert await _compactions(session) == 1
    await _past_the_boundary(session)
    await harness.prompt("turn 3")
    # c64bec3f: still 1 - the answer's seconds read as before the boundary's ms.
    assert await _compactions(session) == 2
    await _past_the_boundary(session)
    await harness.prompt("turn 4")
    assert await _compactions(session) == 3
    assert _ends(events) == [("threshold", False, None)] * 3
    assert provider.calls["summary"] >= 3


async def test_a_second_overflow_recovery_compacts_and_reruns_after_a_first_compaction() -> None:
    provider = _Provider(["big", "big", "ok", "ok", "overflow", "ok"])
    session = Session(MemorySessionStorage())
    harness, events = _harness(session)
    await _first_compaction_then_two_short_turns(harness, session)
    await harness.prompt("turn 5")  # refused as too long, compacted, sent again
    # c64bec3f: the fresh overflow error read as stale, no recovery, the turn failed.
    assert await _compactions(session) == 2
    assert _ends(events) == [("threshold", False, None), ("overflow", True, None)]
    last = harness._state.messages[-1]
    assert isinstance(last, AssistantMessage) and last.stop_reason == "end_turn"
    assert provider.calls["turn"] == 6


async def test_a_session_reopened_from_disk_compacts_again(tmp_path: Path) -> None:
    """A JSONL session written with second timestamps, reopened as ``-c`` does."""

    path = str(tmp_path / "session.jsonl")
    storage = await JsonlSessionStorage.create(LocalFileSystem(), path, cwd="/repo", session_id="s")
    _Provider(["big"])
    first, _ = _harness(Session(storage))
    await first.prompt("turn 1")
    await first.prompt("turn 2")
    assert await _compactions(first.session) == 1  # type: ignore[arg-type]
    stamps = [
        m.timestamp for m in first._state.messages if isinstance(m, AssistantMessage) and m.timestamp
    ]
    assert stamps and all(s < 1e11 for s in stamps)  # seconds, as written to the file

    reopened_session = Session(await JsonlSessionStorage.open(LocalFileSystem(), path))
    second, events = _harness(reopened_session)
    second._state.messages = list(
        build_session_context(await reopened_session.get_branch()).messages
    )
    # The kept answer from before the compaction is still stale on its own ...
    await second._check_auto_compaction()
    assert _ends(events) == []
    # ... and the next answer is not.
    await _past_the_boundary(reopened_session)
    await second.prompt("turn 3")
    assert await _compactions(reopened_session) == 2  # c64bec3f: 1


async def test_after_a_compaction_a_failed_turn_estimated_past_the_threshold_compacts() -> None:
    """Guard B: on the error path the estimate's usage source is the last good answer;
    one stamped after the compaction is not stale (pi ``:1827-1835``)."""

    _Provider(["big", "big", "mid", "400"])
    session = Session(MemorySessionStorage())
    harness, events = _harness(session)
    await harness.prompt("turn 1")
    await harness.prompt("turn 2")
    assert await _compactions(session) == 1
    await _past_the_boundary(session)
    await harness.prompt("turn 3")  # 18000: under the threshold
    assert await _compactions(session) == 1
    await harness.prompt("turn 4 " + "y" * 8_000)  # refused; 18000 + about 2000 estimated
    assert await _compactions(session) == 2  # c64bec3f: 1 (the usage source read as stale)


async def test_an_answer_stamped_in_the_compactions_own_millisecond_is_stale() -> None:
    """pi's guard is ``<=``: an answer at the boundary itself predates nothing after it."""

    from aelix_agent_core.types import CompactionStartEvent

    session = Session(MemorySessionStorage())
    await session.append_compaction("summary", "", 0)
    boundary = get_latest_compaction_boundary_ms(await session.get_branch())
    assert boundary is not None
    harness, events = _harness(session)

    def _answer(stamp: float) -> AssistantMessage:
        return AssistantMessage(
            content=[TextContent(text="a")],
            stop_reason="end_turn",
            usage={"total_tokens": 19_500},
            timestamp=stamp,
        )

    # The boundary itself, in ms (exact: a round trip through seconds may not be).
    harness._state.messages = [_answer(boundary)]
    await harness._check_auto_compaction()
    assert not any(isinstance(e, CompactionStartEvent) for e in events)
    harness._state.messages = [_answer(boundary / 1000.0 + 0.002)]  # 2 ms later, in seconds
    await harness._check_auto_compaction()
    assert any(isinstance(e, CompactionStartEvent) for e in events)


# === the interplay with #391's skip, now that the guards let answers through =====


async def test_a_refused_second_overflow_after_a_compaction_runs_no_threshold_compaction() -> None:
    """Re-run overflows again: the recovery refuses a second compact-and-retry, and no
    threshold compaction follows. Before #565 Guard A read the fresh overflow as
    stale; now the #391 review-round-2 skip passes over it (and Guard B would, on
    its usage source from before the recovery's compaction)."""

    provider = _Provider(["big", "big", "ok", "ok", "overflow"])
    session = Session(MemorySessionStorage())
    harness, events = _harness(session)
    await _first_compaction_then_two_short_turns(harness, session)
    await harness.prompt("turn 5")
    assert provider.calls["turn"] == 6  # the overflow, then its one re-run
    assert await _compactions(session) == 2  # the first one and the recovery's
    assert [e[0] for e in _ends(events)] == ["threshold", "overflow"]


async def test_a_recovered_answer_past_the_threshold_is_compacted_in_its_prompt() -> None:
    """Control: the re-run's own answer is a new answer, after the boundary."""

    provider = _Provider(["big", "big", "ok", "ok", "overflow", "big"])
    session = Session(MemorySessionStorage())
    harness, events = _harness(session)
    await _first_compaction_then_two_short_turns(harness, session)
    await harness.prompt("turn 5")
    assert provider.calls["turn"] == 6
    assert [e[0] for e in _ends(events)] == ["threshold", "overflow", "threshold"]
    assert await _compactions(session) == 3


# === the unit rule =================================================================


def test_timestamp_ms_reads_seconds_and_milliseconds_as_milliseconds() -> None:
    assert _timestamp_ms(1_761_000_000.5) == 1_761_000_000_500.0  # seconds (loop.py)
    assert _timestamp_ms(1_761_000_000_500.0) == 1_761_000_000_500.0  # ms (a rebuilt summary)
    assert _timestamp_ms(1_761_000_000) == 1_761_000_000_000.0
    assert _timestamp_ms(None) is None
    assert _timestamp_ms(True) is None
    assert _timestamp_ms("1761000000") is None


def test_an_answer_stamped_after_the_boundary_is_not_stale_and_one_before_is() -> None:
    boundary_ms = 1_761_000_000_000.0
    after = _timestamp_ms(boundary_ms / 1000.0 + 0.5)
    before = _timestamp_ms(boundary_ms / 1000.0 - 60)
    assert after is not None and after > boundary_ms
    assert before is not None and before <= boundary_ms

