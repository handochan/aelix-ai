"""Fixtures for the job service tests (#722).

``jobs_active`` is the documented test seam of
:mod:`aelix_coding_agent.jobs.activation`: it sets ``ACTIVE`` for one test and
puts it back. Every product check reads the attribute at call time.
"""

from __future__ import annotations

import asyncio
import sys
import time
import warnings
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import AssistantEndEvent, AssistantMessageEvent, AssistantStartEvent
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.service import JobService

from tests.jobs import _sleeper


@pytest.fixture(autouse=True)
async def _reap_sleepers() -> AsyncIterator[None]:
    yield
    await _sleeper.reap_all()


@pytest.fixture
def jobs_active(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(activation, "ACTIVE", True)
    _sleeper.Sleeper.instances.clear()
    yield
    activation.install_service(None)


class RecordingModel:
    """A stream that records each context's texts and answers ``answer`` (or an error)."""

    def __init__(self, answer: str = "ok") -> None:
        self.answer = answer
        self.contexts: list[list[str]] = []
        self.fail_next: list[bool] = []

    async def __call__(
        self, _model: Any, context: Any, _options: Any = None
    ) -> AsyncIterator[AssistantMessageEvent]:
        texts = [
            block.text
            for message in context.messages
            for block in (getattr(message, "content", []) or [])
            if isinstance(block, TextContent)
        ]
        self.contexts.append(texts)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        failing = self.fail_next.pop(0) if self.fail_next else False
        if failing:
            yield AssistantEndEvent(
                message=AssistantMessage(content=[], stop_reason="error", error_message="boom")
            )
            return
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=self.answer)], stop_reason="end_turn"
            )
        )


async def make_session(tmp_path: Path, name: str = "s.jsonl", session_id: str = "s") -> Session:
    storage = await JsonlSessionStorage.create(
        LocalFileSystem(), str(tmp_path / name), cwd="/repo", session_id=session_id
    )
    return Session(storage)


async def reopen(tmp_path: Path, name: str = "s.jsonl") -> Session:
    return Session(await JsonlSessionStorage.open(LocalFileSystem(), str(tmp_path / name)))


def make_harness(session: Session | None, model: RecordingModel | None = None) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(stream_fn=model or RecordingModel(), session=session, notice_inbox=True)
    )


def fast_service(**kwargs: Any) -> JobService:
    defaults: dict[str, Any] = {
        "interval": 0.05,
        "exit_grace": 0.3,
        "cleanup_bound": 3.0,
        "stop_grace": 0.2,
    }
    defaults.update(kwargs)
    return JobService(**defaults)


async def wait_for(predicate: Any, timeout: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


def _describe(service: Any, job_ids: Any) -> str:
    parts = []
    for job_id in job_ids:
        snap = service.snapshot(job_id)
        if snap is None:
            parts.append(f"{job_id}: gone")
        else:
            parts.append(f"{job_id}: {snap.state} (reason {snap.unresolved_reason!r})")
    return "; ".join(parts)


async def wait_settled(
    service: Any,
    job_ids: Any,
    *,
    since: float,
    state: str | tuple[str, ...] = "stopped",
    slots: tuple[str, int] | None = None,
    timeout: float = 10.0,
) -> float:
    """#751: wait for every job in ``job_ids`` to reach ``state`` (and ``slots``' kind to hold that count).

    ``JobService.stop()`` returns at ``cleanup_bound`` (3.0 s in ``fast_service``)
    even while the job's cleanup still runs, and on Windows CI a sleeper's stop
    has outlasted it (#751). The cases care that the stop ENDS in ``state``, not
    that it ends inside that bound, so they wait for it here; a stop that really
    hangs or ends ``unresolved`` still fails, naming each job's state, its
    reason and how long since ``since`` (``time.monotonic()`` before the stop).
    Returns the seconds from ``since`` until it was reached. On win32 it reports
    them with ``warnings.warn``, the one channel the ``-q`` log keeps.
    """

    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    wanted = (state,) if isinstance(state, str) else state

    def done() -> bool:
        snaps = [service.snapshot(job_id) for job_id in job_ids]
        if any(snap is None or snap.state not in wanted for snap in snaps):
            return False
        return slots is None or service.admission.count(slots[0]) == slots[1]

    while not done():
        if loop.time() > end:
            count = "" if slots is None else f"; {slots[0]} holds {service.admission.count(slots[0])} slots"
            raise AssertionError(
                f"not {state} {time.monotonic() - since:.2f}s after the stop began: "
                f"{_describe(service, job_ids)}{count}"
            )
        await asyncio.sleep(0.02)
    took = time.monotonic() - since
    if sys.platform == "win32":
        warnings.warn(f"#751 sleeper stop: {state} after {took:.3f}s", stacklevel=2)
    return took
