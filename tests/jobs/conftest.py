"""Fixtures for the job service tests (#722).

``jobs_active`` is the documented test seam of
:mod:`aelix_coding_agent.jobs.activation`: it sets ``ACTIVE`` for one test and
puts it back. Every product check reads the attribute at call time.
"""

from __future__ import annotations

import asyncio
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
