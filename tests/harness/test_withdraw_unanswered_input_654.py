"""#654 (ADR-0286) — a withdrawing abort takes unanswered input out of context.

Every row asserts the four places input can linger: the next request, live state,
the reopened JSONL file and the next-turn queue. The provider is a script of steps,
so the abort always lands where the row says it does.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    BeforeAgentStartResult,
    InputWithdrawnHookEvent,
    MessageEndEventResult,
    SessionBeforeCompactResult,
)
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    JsonlSessionStorage,
    LocalFileSystem,
    MemorySessionStorage,
    Session,
    SessionError,
)
from aelix_agent_core.types import AgentEvent, AgentTool, InputWithdrawnEvent
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    Model,
    TextDeltaEvent,
)
from aelix_ai.tools import ToolResult

_RETRYABLE = "overloaded_error: provider returned error"
_OVERFLOW = "prompt is too long: 213462 tokens > 200000 maximum"


def _shape(messages: list[Any]) -> list[str]:
    """Each message as text, a call or a result; an empty aborted marker drops out."""

    out: list[str] = []
    for message in messages:
        if isinstance(message, ToolResultMessage):
            out.append("<result>")
            continue
        if isinstance(message, AssistantMessage) and message.stop_reason == "error":
            out.append("<failed>")
            continue
        for block in message.content:
            if isinstance(block, TextContent):
                out.append(block.text)
            elif isinstance(block, ToolCallContent):
                out.append("<call>")
    return out


Step = Callable[["_Provider"], Awaitable[None]] | None


class _Provider:
    """One entry per request: ``hang``, ``mid_text``, ``tool``, ``answer``, ``failed``, ``overflow``."""

    def __init__(self, *script: str, before: dict[int, Step] | None = None) -> None:
        self.script = list(script)
        self.before = before or {}
        self.seen: list[list[str]] = []
        self.reached = asyncio.Event()

    async def __call__(self, _model: Model, context: Any, _options: Any) -> AsyncIterator[Any]:
        self.seen.append(_shape(context.messages))
        step = self.script.pop(0) if self.script else "answer"
        hook = self.before.get(len(self.seen))
        if hook is not None:
            await hook(self)
        if step == "raises_when_cancelled":
            self.reached.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError("the adapter turned the cancel into this") from None
        if step == "big_answer":
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text="answer")], usage={"input": 199_000, "output": 10}
                )
            )
            return
        if step == "hang" or step == "mid_text":
            if step == "mid_text":
                partial = AssistantMessage(content=[TextContent(text="PARTIAL")])
                yield AssistantStartEvent(partial=partial)
                yield TextDeltaEvent(content_index=0, delta="PARTIAL", partial=partial)
            self.reached.set()
            await asyncio.Event().wait()
        if step == "tool":
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[ToolCallContent(tool_call_id="c1", tool_name="edit", input={})],
                    stop_reason="tool_use",
                )
            )
            return
        if step in ("failed", "overflow"):
            yield AssistantErrorEvent(
                reason="error",
                error=AssistantMessage(
                    stop_reason="error",
                    error_message=_RETRYABLE if step == "failed" else _OVERFLOW,
                ),
            )
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))


@dataclass
class _Run:
    harness: AgentHarness
    provider: _Provider
    path: Path | None
    session: Session | None
    events: list[AgentEvent] = field(default_factory=list)

    @property
    def withdrawn(self) -> list[InputWithdrawnEvent]:
        return [e for e in self.events if isinstance(e, InputWithdrawnEvent)]

    async def disk(self) -> list[str]:
        assert self.path is not None
        reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(self.path)))
        return _shape((await reopened.build_context()).messages)

    def state(self) -> list[str]:
        return _shape(self.harness.state.messages)

    async def next_request(self) -> list[str]:
        self.provider.script.append("answer")
        await self.harness.prompt("NEW")
        return self.provider.seen[-1]


async def _start(tmp_path: Path, provider: _Provider, attached: bool, **options: Any) -> _Run:
    path = tmp_path / "session.jsonl" if attached else None
    session = None
    if path is not None:
        session = Session(
            await JsonlSessionStorage.create(
                LocalFileSystem(), str(path), cwd=str(tmp_path), session_id="issue-654"
            )
        )
    harness = AgentHarness(AgentHarnessOptions(stream_fn=provider, session=session, **options))
    harness.state.auto_retry_enabled = False
    harness.state.auto_compaction_enabled = False
    run = _Run(harness, provider, path, session)
    harness.subscribe(lambda event: run.events.append(event))
    return run


async def _abort_at_reached(run: _Run, text: str = "CANCELLED", **abort: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(run.harness.prompt(text))
    await asyncio.wait_for(run.provider.reached.wait(), timeout=5)
    await asyncio.sleep(0)
    await run.harness.abort(**abort)
    return task


async def _message_entries(run: _Run) -> int:
    assert run.session is not None
    return len([e for e in await run.session.get_entries() if e.type == "message"])


def _edit_tool(execute: Callable[..., Awaitable[ToolResult]] | None = None) -> AgentTool:
    async def done(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[TextContent(text="file written")])

    return AgentTool(name="edit", execute=execute or done)


# === The rule's table ========================================================


@pytest.mark.parametrize("attached", [False, True])
@pytest.mark.parametrize("step", ["hang", "mid_text"])
async def test_esc_before_an_answer_withdraws_the_whole_prompt(
    tmp_path: Path, attached: bool, step: str
) -> None:
    run = await _start(tmp_path, _Provider(step), attached)
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    assert await task == []

    assert run.state() == []
    assert run.harness._next_turn_queue == []
    assert [(_shape(e.messages), e.prompt_withdrawn) for e in run.withdrawn] == [
        (["CANCELLED"], True)
    ]
    if attached:
        assert await run.disk() == []
    assert await run.next_request() == ["NEW"]
    assert run.state() == ["NEW", "answer"]
    if attached:
        assert await run.disk() == ["NEW", "answer"]


@pytest.mark.parametrize("attached", [False, True])
async def test_esc_on_an_in_flight_retry_takes_the_prompt_and_its_failed_answer(
    tmp_path: Path, attached: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)
    run = await _start(tmp_path, _Provider("failed", "hang"), attached)
    run.harness.state.auto_retry_enabled = True
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    assert await task == []

    assert run.state() == []
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    if attached:
        assert await run.disk() == []
    assert await run.next_request() == ["NEW"]


@pytest.mark.parametrize("attached", [False, True])
async def test_esc_in_the_retry_countdown_withdraws_after_the_turn_closed(
    tmp_path: Path, attached: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 60_000)
    run = await _start(tmp_path, _Provider("failed"), attached)
    run.harness.state.auto_retry_enabled = True
    waiting = asyncio.Event()
    run.harness.subscribe(
        lambda e: waiting.set() if getattr(e, "type", "") == "auto_retry_start" else None
    )
    task = asyncio.create_task(run.harness.prompt("CANCELLED"))
    await asyncio.wait_for(waiting.wait(), timeout=5)
    await asyncio.sleep(0)
    run.harness.abort_retry(withdraw_unanswered=True)
    await asyncio.wait_for(task, timeout=5)

    assert run.state() == []
    types = [e.type for e in run.events]
    assert types.index("agent_end") < types.index("auto_retry_end") < types.index("input_withdrawn")
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    if attached:
        assert await run.disk() == []
    assert await run.next_request() == ["NEW"]


@pytest.mark.parametrize("attached", [False, True])
async def test_esc_while_a_tool_runs_withdraws_nothing(tmp_path: Path, attached: bool) -> None:
    provider = _Provider("tool")

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        provider.reached.set()
        await asyncio.Event().wait()
        return ToolResult(content=[])

    run = await _start(tmp_path, provider, attached)
    run.harness.state.tools = [_edit_tool(execute)]
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.state() == ["CANCELLED", "<call>"]
    assert run.withdrawn == []
    if attached:
        assert await run.disk() == ["CANCELLED", "<call>"]


@pytest.mark.parametrize("attached", [False, True])
async def test_esc_after_a_tool_ran_withdraws_nothing(tmp_path: Path, attached: bool) -> None:
    run = await _start(tmp_path, _Provider("tool", "hang"), attached)
    run.harness.state.tools = [_edit_tool()]
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    kept = ["CANCELLED", "<call>", "<result>"]
    assert run.state() == kept
    assert run.withdrawn == []
    assert run.harness._next_turn_queue == []
    if attached:
        assert await run.disk() == kept
        assert await run.next_request() == [*kept, "NEW"]


@pytest.mark.parametrize("attached", [False, True])
async def test_a_steer_after_a_tool_result_is_withdrawn_and_the_rest_stays(
    tmp_path: Path, attached: bool
) -> None:
    provider = _Provider("tool", "hang")
    run = await _start(tmp_path, provider, attached)

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        await run.harness.steer("STEER")
        return ToolResult(content=[TextContent(text="file written")])

    run.harness.state.tools = [_edit_tool(execute)]
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert provider.seen[-1][-1] == "STEER"
    kept = ["CANCELLED", "<call>", "<result>"]
    assert run.state() == kept
    assert [(_shape(e.messages), e.prompt_withdrawn) for e in run.withdrawn] == [(["STEER"], False)]
    if attached:
        assert await run.disk() == kept
        assert await _message_entries(run) == 4  # what stayed was not written again
        assert await run.next_request() == [*kept, "NEW"]


@pytest.mark.parametrize("attached", [False, True])
async def test_a_follow_up_after_an_answer_is_withdrawn_and_the_answer_stays(
    tmp_path: Path, attached: bool
) -> None:
    holder: dict[str, _Run] = {}

    async def queue_follow_up(_provider: _Provider) -> None:
        await holder["run"].harness.follow_up("FOLLOW")

    provider = _Provider("answer", "hang", before={1: queue_follow_up})
    run = await _start(tmp_path, provider, attached)
    holder["run"] = run
    task = await _abort_at_reached(run, text="ASKED", withdraw_unanswered=True)
    await task

    assert provider.seen[-1] == ["ASKED", "answer", "FOLLOW"]
    assert run.state() == ["ASKED", "answer"]
    assert [(_shape(e.messages), e.prompt_withdrawn) for e in run.withdrawn] == [
        (["FOLLOW"], False)
    ]
    if attached:
        assert await run.disk() == ["ASKED", "answer"]
        assert await _message_entries(run) == 3  # what stayed was not written again
        assert await run.next_request() == ["ASKED", "answer", "NEW"]


async def test_a_compaction_inside_the_prompt_is_a_barrier(tmp_path: Path) -> None:
    """Overflow recovery compacted: the summary's tail cannot be withdrawn from."""

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary="SUMMARY",
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=99,
        )

    provider = _Provider("overflow", "hang")
    run = await _start(tmp_path, provider, True, _summarizer_override=summarise)
    assert run.session is not None
    for _ in range(6):
        await run.session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    run.harness.state.auto_compaction_enabled = True
    run.harness.state.model = Model(
        id="m", provider="p", api="unknown", context_window=200_000, max_tokens=4096
    )
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.withdrawn == []
    assert "CANCELLED" in await run.disk()


async def test_the_threshold_check_after_a_countdown_esc_never_sees_the_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``abort_retry`` leaves the abort flag alone, so that check does run."""

    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 60_000)
    summarised: list[list[str]] = []

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        summarised.append(_shape([*prep.messages_to_summarize, *prep.turn_prefix_messages]))
        return CompactResult(
            summary="SUMMARY",
            first_kept_entry_id=prep.first_kept_entry_id,
            tokens_before=99,
        )

    run = await _start(
        tmp_path, _Provider("big_answer", "failed"), True, _summarizer_override=summarise
    )
    assert run.session is not None
    for _ in range(6):
        await run.session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    run.harness.state.model = Model(
        id="m", provider="p", api="unknown", context_window=200_000, max_tokens=4096
    )
    await run.harness.prompt("ANSWERED")

    run.harness.state.auto_retry_enabled = True
    run.harness.state.auto_compaction_enabled = True
    waiting = asyncio.Event()
    run.harness.subscribe(
        lambda e: waiting.set() if getattr(e, "type", "") == "auto_retry_start" else None
    )
    task = asyncio.create_task(run.harness.prompt("CANCELLED"))
    await asyncio.wait_for(waiting.wait(), timeout=5)
    await asyncio.sleep(0)
    run.harness.abort_retry(withdraw_unanswered=True)
    await asyncio.wait_for(task, timeout=5)

    assert summarised, "the threshold check did not run, so this proves nothing"
    assert "CANCELLED" not in [text for shape in summarised for text in shape]
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    assert "CANCELLED" not in await run.disk()


@pytest.mark.parametrize("attached", [False, True])
async def test_a_raise_out_of_the_turn_after_esc_still_withdraws(
    tmp_path: Path, attached: bool
) -> None:
    """An adapter that answers the cancel with an exception takes the failure path."""

    run = await _start(tmp_path, _Provider("raises_when_cancelled"), attached)
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    with pytest.raises(RuntimeError, match="turned the cancel"):
        await task

    assert run.state() == []
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    if attached:
        assert await run.disk() == []


# === Delivered once ==========================================================


@pytest.mark.parametrize("attached", [False, True])
async def test_a_drained_next_turn_goes_back_to_its_queue_and_reaches_the_model_once(
    tmp_path: Path, attached: bool
) -> None:
    run = await _start(tmp_path, _Provider("hang"), attached)
    await run.harness.next_turn("QUEUED")
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.provider.seen[0] == ["QUEUED", "CANCELLED"]
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    assert _shape(run.harness._next_turn_queue) == ["QUEUED"]
    assert run.state() == []
    if attached:
        assert await run.disk() == []
    assert await run.next_request() == ["QUEUED", "NEW"]
    assert run.harness._next_turn_queue == []
    run.provider.script.append("answer")
    await run.harness.prompt("LATER")
    assert run.provider.seen[-1] == ["QUEUED", "NEW", "answer", "LATER"]


async def test_a_hooks_injected_message_is_regenerated_rather_than_duplicated(
    tmp_path: Path,
) -> None:
    run = await _start(tmp_path, _Provider("hang"), True)

    def inject(_event: Any, _ctx: Any) -> BeforeAgentStartResult:
        return BeforeAgentStartResult(
            messages=[UserMessage(content=[TextContent(text="INJECTED")])]
        )

    run.harness.hooks.on("before_agent_start", inject)
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.provider.seen[0] == ["INJECTED", "CANCELLED"]
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    assert await run.disk() == []
    assert run.harness._next_turn_queue == []
    assert await run.next_request() == ["INJECTED", "NEW"]


async def test_the_first_message_of_a_session_leaves_a_second_root(tmp_path: Path) -> None:
    run = await _start(tmp_path, _Provider("hang"), True)
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task
    assert await run.disk() == []

    assert await run.next_request() == ["NEW"]
    assert run.path is not None
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(run.path)))
    roots = [
        e.message  # type: ignore[union-attr]
        for e in await reopened.get_entries()
        if e.type == "message" and e.parent_id is None
    ]
    assert _shape(roots) == ["CANCELLED", "NEW"]
    assert _shape((await reopened.build_context()).messages) == ["NEW", "answer"]


# === A cancel before the commit ==============================================


def _parking_listener(run: _Run, text: str) -> asyncio.Event:
    """Park the turn in ``message_start`` of the message ``text`` (an async subscriber)."""

    parked = asyncio.Event()

    async def listener(event: AgentEvent) -> None:
        if event.type == "message_start" and _shape([event.message]) == [text]:
            parked.set()
            await asyncio.Event().wait()

    run.harness.subscribe(listener)
    return parked


async def test_an_uncommitted_live_input_does_not_come_back_through_the_queue(
    tmp_path: Path,
) -> None:
    run = await _start(tmp_path, _Provider(), True)
    parked = _parking_listener(run, "CANCELLED")
    task = asyncio.create_task(run.harness.prompt("CANCELLED"))
    await asyncio.wait_for(parked.wait(), timeout=5)
    await run.harness.abort(withdraw_unanswered=True)
    await task

    assert run.provider.seen == []
    assert run.harness._next_turn_queue == []
    assert [(_shape(e.messages), e.prompt_withdrawn) for e in run.withdrawn] == [
        (["CANCELLED"], True)
    ]
    assert await run.disk() == []


async def test_drained_inputs_keep_their_order_when_the_cancel_lands_between_them(
    tmp_path: Path,
) -> None:
    run = await _start(tmp_path, _Provider(), True)
    await run.harness.next_turn("Q1")
    await run.harness.next_turn("Q2")
    await run.harness.next_turn("Q3")
    parked = _parking_listener(run, "Q2")
    task = asyncio.create_task(run.harness.prompt("CANCELLED"))
    await asyncio.wait_for(parked.wait(), timeout=5)
    await run.harness.abort(withdraw_unanswered=True)
    await task

    assert _shape(run.harness._next_turn_queue) == ["Q1", "Q2", "Q3"]
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    assert run.state() == []
    assert await run.disk() == []


# === Hook replacement and neighbours =========================================


@pytest.mark.parametrize("attached", [False, True])
async def test_the_event_carries_the_original_and_the_replacement_leaves_the_stores(
    tmp_path: Path, attached: bool
) -> None:
    run = await _start(tmp_path, _Provider("hang"), attached)

    def replace_it(event: Any, _ctx: Any) -> MessageEndEventResult | None:
        if isinstance(event.message, UserMessage):
            return MessageEndEventResult(
                message=UserMessage(content=[TextContent(text="REPLACED")])
            )
        return None

    run.harness.hooks.on("message_end", replace_it)
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.provider.seen[0] == ["REPLACED"]
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    assert run.state() == []
    if attached:
        assert await run.disk() == []


async def test_a_replaced_follow_up_comes_back_as_it_was_queued(tmp_path: Path) -> None:
    holder: dict[str, _Run] = {}

    async def queue_follow_up(_provider: _Provider) -> None:
        await holder["run"].harness.follow_up("FOLLOW")

    provider = _Provider("answer", "hang", before={1: queue_follow_up})
    run = await _start(tmp_path, provider, True)
    holder["run"] = run

    def replace_it(event: Any, _ctx: Any) -> MessageEndEventResult | None:
        if _shape([event.message]) == ["FOLLOW"]:
            return MessageEndEventResult(
                message=UserMessage(content=[TextContent(text="REPLACED")])
            )
        return None

    run.harness.hooks.on("message_end", replace_it)
    task = await _abort_at_reached(run, text="ASKED", withdraw_unanswered=True)
    await task

    assert provider.seen[-1] == ["ASKED", "answer", "REPLACED"]
    assert [_shape(e.messages) for e in run.withdrawn] == [["FOLLOW"]]
    assert await run.disk() == ["ASKED", "answer"]


async def test_a_failure_after_what_already_ran_is_not_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model's call and the tool's result stay, and there is nothing to give back."""

    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 60_000)
    run = await _start(tmp_path, _Provider("tool", "failed"), True)
    run.harness.state.tools = [_edit_tool()]
    run.harness.state.auto_retry_enabled = True
    waiting = asyncio.Event()
    run.harness.subscribe(
        lambda e: waiting.set() if getattr(e, "type", "") == "auto_retry_start" else None
    )
    task = asyncio.create_task(run.harness.prompt("KEPT"))
    await asyncio.wait_for(waiting.wait(), timeout=5)
    await asyncio.sleep(0)
    run.harness.abort_retry(withdraw_unanswered=True)
    await asyncio.wait_for(task, timeout=5)

    assert run.withdrawn == []
    assert run.state() == ["KEPT", "<call>", "<result>"]
    assert await run.disk() == ["KEPT", "<call>", "<result>", "<failed>"]


async def test_an_entry_flushed_between_retry_attempts_stays_on_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)
    holder: dict[str, _Run] = {}

    async def change_level(_provider: _Provider) -> None:
        await holder["run"].harness.set_thinking_level("high")

    run = await _start(tmp_path, _Provider("failed", "hang", before={1: change_level}), True)
    holder["run"] = run
    run.harness.state.auto_retry_enabled = True
    run.harness.state.model = Model(id="m", provider="p", reasoning=True)
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.path is not None
    reopened = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(run.path)))
    branch = await reopened.get_branch()
    assert [e.type for e in branch] == ["thinking_level_change"]
    assert branch[0].parent_id is None
    assert (await reopened.build_context()).thinking_level == "high"
    assert await run.disk() == []


async def test_a_message_written_between_attempts_stays_without_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No session: the pending write mirrors into state, behind the input it follows."""

    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 1)
    holder: dict[str, _Run] = {}

    async def write(_provider: _Provider) -> None:
        await holder["run"].harness.append_message(
            UserMessage(content=[TextContent(text="WRITTEN")])
        )

    run = await _start(tmp_path, _Provider("failed", "hang", before={1: write}), False)
    holder["run"] = run
    run.harness.state.auto_retry_enabled = True
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.state() == ["WRITTEN"]


# === What keeps the input ====================================================


@pytest.mark.parametrize("how", ["abort", "dispose", "cancel"])
async def test_every_other_way_out_keeps_the_input(tmp_path: Path, how: str) -> None:
    """#320's rule still holds for a default abort, teardown and an outer cancel."""

    run = await _start(tmp_path, _Provider("hang"), True)
    task = asyncio.create_task(run.harness.prompt("KEPT"))
    await asyncio.wait_for(run.provider.reached.wait(), timeout=5)
    await asyncio.sleep(0)
    if how == "abort":
        await run.harness.abort()
        await task
    elif how == "dispose":
        await run.harness.dispose()
        await task
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert run.state() == ["KEPT"]
    assert run.withdrawn == []
    assert run.harness._next_turn_queue == []
    assert await run.disk() == ["KEPT"]


async def test_a_default_abort_retry_keeps_the_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 60_000)
    run = await _start(tmp_path, _Provider("failed"), True)
    run.harness.state.auto_retry_enabled = True
    waiting = asyncio.Event()
    run.harness.subscribe(
        lambda e: waiting.set() if getattr(e, "type", "") == "auto_retry_start" else None
    )
    task = asyncio.create_task(run.harness.prompt("KEPT"))
    await asyncio.wait_for(waiting.wait(), timeout=5)
    await asyncio.sleep(0)
    run.harness.abort_retry()
    await asyncio.wait_for(task, timeout=5)

    assert run.withdrawn == []
    assert await run.disk() == ["KEPT", "<failed>"]


@pytest.mark.parametrize("how", ["abort", "abort_retry"])
async def test_a_stale_request_made_while_idle_does_not_touch_the_next_prompt(
    tmp_path: Path, how: str
) -> None:
    run = await _start(tmp_path, _Provider("hang"), True)
    if how == "abort":
        await run.harness.abort(withdraw_unanswered=True)
    else:
        run.harness.abort_retry(withdraw_unanswered=True)
    task = await _abort_at_reached(run, text="KEPT")
    await task

    assert run.withdrawn == []
    assert await run.disk() == ["KEPT"]


# === The event ===============================================================


async def test_agent_end_never_carries_what_was_withdrawn_and_the_bus_hears_it_once(
    tmp_path: Path,
) -> None:
    run = await _start(tmp_path, _Provider("hang"), True)
    heard: list[InputWithdrawnHookEvent] = []
    run.harness.hooks.on("input_withdrawn", lambda e, _ctx: heard.append(e))
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await run.harness.abort(withdraw_unanswered=True)
    await task

    assert len(run.withdrawn) == 1
    assert [(_shape(e.messages), e.prompt_withdrawn) for e in heard] == [(["CANCELLED"], True)]
    types = [e.type for e in run.events]
    assert types.index("input_withdrawn") < types.index("turn_end") < types.index("agent_end")
    end = next(e for e in run.events if e.type == "agent_end")
    assert _shape(end.messages) == []
    # ``print_mode`` reads the stop reason off the marker.
    assert run.harness.state.messages[-1].stop_reason == "aborted"  # type: ignore[union-attr]


async def test_a_second_turn_of_the_harness_has_its_own_tracker(tmp_path: Path) -> None:
    """A prompt that finished leaves nothing for the next one's abort to take."""

    run = await _start(tmp_path, _Provider("answer", "hang"), True)
    await run.harness.prompt("DONE")
    task = await _abort_at_reached(run, withdraw_unanswered=True)
    await task

    assert run.state() == ["DONE", "answer"]
    assert [_shape(e.messages) for e in run.withdrawn] == [["CANCELLED"]]
    assert await run.disk() == ["DONE", "answer"]
    assert run.harness._unanswered is None


# === Session.withdraw_tail ===================================================


async def _sessions(tmp_path: Path) -> list[Session]:
    return [
        Session(MemorySessionStorage()),
        Session(
            await JsonlSessionStorage.create(
                LocalFileSystem(), str(tmp_path / "s.jsonl"), cwd=str(tmp_path), session_id="s"
            )
        ),
    ]


async def test_withdraw_tail_keeps_survivors_in_order_on_fresh_ids(tmp_path: Path) -> None:
    gone = UserMessage(content=[TextContent(text="GONE")])

    def is_gone(entry: Any) -> bool:
        return entry.type == "message" and entry.message is gone

    for session in await _sessions(tmp_path):
        keep = await session.append_message(UserMessage(content=[TextContent(text="KEEP")]))
        await session.append_message(gone)
        custom_id = await session.append_custom_entry("note", {"n": 1})
        await session.append_model_change("p", "m")

        await session.withdraw_tail(keep, is_gone)

        branch = await session.get_branch()
        assert [e.type for e in branch] == ["message", "custom", "model_change"]
        assert branch[0].id == keep
        assert branch[1].id != custom_id
        assert branch[1].parent_id == keep
        assert branch[2].parent_id == branch[1].id


async def test_withdraw_tail_refuses_a_target_off_the_path_and_writes_nothing(
    tmp_path: Path,
) -> None:
    for session in await _sessions(tmp_path):
        first = await session.append_message(UserMessage(content=[TextContent(text="A")]))
        await session.move_to(None)
        await session.append_message(UserMessage(content=[TextContent(text="B")]))
        before = len(await session.get_entries())

        with pytest.raises(SessionError):
            await session.withdraw_tail(first, lambda _e: True)

        assert len(await session.get_entries()) == before


# === Review round 1: queued lines travel on the event =========================


async def _queue_during_the_turn(run: _Run, text: str = "CANCELLED") -> asyncio.Task[Any]:
    task = asyncio.create_task(run.harness.prompt(text))
    await asyncio.wait_for(run.provider.reached.wait(), timeout=5)
    await asyncio.sleep(0)
    await run.harness.steer("STEER")
    await run.harness.follow_up("FOLLOW")
    return task


def _said(run: _Run) -> list[tuple[list[str], bool]]:
    return [(_shape(e.messages), e.prompt_withdrawn) for e in run.withdrawn]


async def test_queued_lines_travel_behind_what_committed_in_one_event(tmp_path: Path) -> None:
    run = await _start(tmp_path, _Provider("hang"), True)
    task = await _queue_during_the_turn(run)
    await run.harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(task, timeout=5)

    assert _said(run) == [(["CANCELLED", "STEER", "FOLLOW"], True)]
    types = [e.type for e in run.events]
    assert types.index("input_withdrawn") < types.index("agent_end")
    assert await run.next_request() == ["NEW"]


async def test_queued_lines_are_said_even_when_nothing_committed_leaves(tmp_path: Path) -> None:
    run = await _start(tmp_path, _Provider("tool", "hang"), True)
    run.harness.state.tools = [_edit_tool()]
    task = await _queue_during_the_turn(run)
    await run.harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(task, timeout=5)

    assert _said(run) == [(["STEER", "FOLLOW"], False)]
    types = [e.type for e in run.events]
    assert types.index("input_withdrawn") < types.index("agent_end")
    assert run.state() == ["CANCELLED", "<call>", "<result>"]


async def test_after_a_compaction_barrier_queued_lines_are_still_said(tmp_path: Path) -> None:
    """The barrier keeps what committed. The queue never reached the model."""

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    run = await _overflow_run(tmp_path, _Provider("overflow", "hang"), summarise)
    task = await _queue_during_the_turn(run)
    await run.harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(task, timeout=5)

    assert _said(run) == [(["STEER", "FOLLOW"], False)]
    types = [e.type for e in run.events]
    assert types.index("input_withdrawn") < len(types) - 1 - types[::-1].index("agent_end")
    assert "CANCELLED" in await run.disk()


async def test_an_idle_abort_says_the_queued_lines_itself_and_only_once(tmp_path: Path) -> None:
    run = await _start(tmp_path, _Provider(), True)
    await run.harness.steer("STEER")
    await run.harness.follow_up("FOLLOW")
    await run.harness.abort(withdraw_unanswered=True)
    assert _said(run) == [(["STEER", "FOLLOW"], False)]

    await run.harness.abort(withdraw_unanswered=True)
    assert len(run.withdrawn) == 1
    assert await run.next_request() == ["NEW"]
    assert len(run.withdrawn) == 1


async def test_abort_during_a_public_compaction_says_the_queued_lines(tmp_path: Path) -> None:
    """A phase that is not idle, with no prompt alive to carry them."""

    entered = asyncio.Event()
    release = asyncio.Event()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        entered.set()
        await release.wait()
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    run = await _start(tmp_path, _Provider(), True, _summarizer_override=summarise)
    assert run.session is not None
    for _ in range(6):
        await run.session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    task = asyncio.create_task(run.harness.compact())
    await asyncio.wait_for(entered.wait(), timeout=5)
    await run.harness.steer("STEER")
    await run.harness.abort(withdraw_unanswered=True)
    assert _said(run) == [(["STEER"], False)]
    release.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(run.withdrawn) == 1


async def test_a_plain_abort_says_nothing_about_the_lines_and_the_hook_hears_both(
    tmp_path: Path,
) -> None:
    """AbortHookEvent is as it was, for either kind of abort."""

    run = await _start(tmp_path, _Provider(), True)
    heard: list[tuple[list[str], list[str]]] = []

    async def on_abort(event: Any, _ctx: Any) -> None:
        heard.append((_shape(event.cleared_steer), _shape(event.cleared_follow_up)))

    run.harness.hooks.on("abort", on_abort)
    await run.harness.steer("LOST")
    await run.harness.abort()
    await run.harness.steer("STEER")
    await run.harness.follow_up("FOLLOW")
    await run.harness.abort(withdraw_unanswered=True)

    assert heard == [(["LOST"], []), (["STEER"], ["FOLLOW"])]
    assert _said(run) == [(["STEER", "FOLLOW"], False)]


async def test_a_raising_abort_hook_does_not_lose_the_lines(tmp_path: Path) -> None:
    run = await _start(tmp_path, _Provider(), True)

    async def on_abort(_event: Any, _ctx: Any) -> None:
        raise ValueError("no")

    run.harness.hooks.on("abort", on_abort)
    await run.harness.steer("STEER")
    with pytest.raises(AgentHarnessError):
        await run.harness.abort(withdraw_unanswered=True)
    assert _said(run) == [(["STEER"], False)]


async def test_an_esc_before_the_run_withdraws_the_prompt_with_its_lines(tmp_path: Path) -> None:
    """The #336 window (#684): an Esc while ``before_agent_start`` runs ends the turn as it starts.

    ``_run`` used to clear the flag, and the turn went to the model with the
    input kept; a swap waiting for the Esc's withdraw then waited for the answer.
    """

    provider = _Provider("answer")
    run = await _start(tmp_path, provider, True)

    async def before(_event: Any, _ctx: Any) -> None:
        await run.harness.steer("STEER")
        await run.harness.abort(withdraw_unanswered=True)

    run.harness.hooks.on("before_agent_start", before)
    await run.harness.prompt("WITHDRAWN")

    assert _said(run) == [(["WITHDRAWN", "STEER"], True)]
    assert provider.seen == []
    assert await run.disk() == []
    assert run.harness.withdraw_pending is False


async def test_a_plain_abort_before_the_run_keeps_the_prompt_and_sends_nothing(
    tmp_path: Path,
) -> None:
    """#684 — what a teardown's ``dispose`` does in that window: the input stays (§2), unanswered."""

    provider = _Provider("answer")
    run = await _start(tmp_path, provider, True)

    async def before(_event: Any, _ctx: Any) -> None:
        await run.harness.abort()

    run.harness.hooks.on("before_agent_start", before)
    await run.harness.prompt("KEPT")

    assert _said(run) == []
    assert provider.seen == []
    assert await run.disk() == ["KEPT"]


@pytest.mark.parametrize("esc", [True, False], ids=["esc", "plain"])
async def test_an_abort_before_the_run_ends_it_after_the_last_of_its_inputs(
    tmp_path: Path, esc: bool
) -> None:
    """#684 — the turn ends once every input is in, not at the first.

    ``before_agent_start`` injects a message ahead of a ``next_turn`` text and the
    prompt. Ended at the first commit, a plain abort kept only ``INJECTED`` and
    put the text and the prompt back on the next-turn queue.
    """

    provider = _Provider("answer")
    run = await _start(tmp_path, provider, True)
    await run.harness.next_turn("NT")

    async def before(_event: Any, _ctx: Any) -> BeforeAgentStartResult:
        await run.harness.abort(withdraw_unanswered=esc)
        return BeforeAgentStartResult(messages=[UserMessage(content=[TextContent(text="INJECTED")])])

    run.harness.hooks.on("before_agent_start", before)
    await run.harness.prompt("WITH-NT")

    assert provider.seen == []
    if esc:
        assert _said(run) == [(["WITH-NT"], True)]
        assert run.state() == []
        assert await run.disk() == []
        assert _shape(run.harness._next_turn_queue) == ["NT"]
    else:
        assert _said(run) == []
        assert run.state() == ["INJECTED", "NT", "WITH-NT"]
        assert await run.disk() == ["INJECTED", "NT", "WITH-NT"]
        assert run.harness._next_turn_queue == []


async def test_an_esc_during_the_runs_context_read_withdraws_the_prompt(tmp_path: Path) -> None:
    """#684 — Limits 2's other half: ``_run`` reads the context before its turn task exists."""

    provider = _Provider("answer")
    run = await _start(tmp_path, provider, True)
    session: Any = run.harness._session
    reading, release = asyncio.Event(), asyncio.Event()
    read = session.build_context

    async def held() -> Any:
        reading.set()
        await release.wait()
        return await read()

    session.build_context = held
    turn = asyncio.create_task(run.harness.prompt("WITHDRAWN"))
    await asyncio.wait_for(reading.wait(), 5)
    await run.harness.abort(withdraw_unanswered=True)
    release.set()
    await asyncio.wait_for(turn, 5)

    assert _said(run) == [(["WITHDRAWN"], True)]
    assert provider.seen == []
    assert await run.disk() == []


async def test_an_abort_made_while_idle_does_not_end_the_next_prompt(tmp_path: Path) -> None:
    """#684 — the flag ``_run`` no longer clears for a first run is cleared when the prompt begins."""

    provider = _Provider("answer")
    run = await _start(tmp_path, provider, True)
    await run.harness.abort(withdraw_unanswered=True)

    await run.harness.prompt("ASKED")

    assert len(provider.seen) == 1
    assert await run.disk() == ["ASKED", "answer"]


async def test_lines_cleared_while_the_event_goes_out_are_said_once_more(tmp_path: Path) -> None:
    """An abort inside the first event finds the prompt alive and leaves them to it."""

    run = await _start(tmp_path, _Provider("hang"), True)
    again = {"done": False}

    async def listen(event: Any) -> None:
        if event.type == "input_withdrawn" and not again["done"]:
            again["done"] = True
            await run.harness.steer("LATE")
            await run.harness.abort(withdraw_unanswered=True)

    run.harness.subscribe(listen)
    task = await _queue_during_the_turn(run)
    await run.harness.abort(withdraw_unanswered=True)
    await asyncio.wait_for(task, timeout=5)

    assert _said(run) == [(["CANCELLED", "STEER", "FOLLOW"], True), (["LATE"], False)]


# === Review round 1: an Esc during the overflow summary =======================


async def _overflow_run(tmp_path: Path, provider: _Provider, summarise: Any, **options: Any) -> _Run:
    run = await _start(tmp_path, provider, True, _summarizer_override=summarise, **options)
    assert run.session is not None
    for _ in range(6):
        await run.session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    run.harness.state.auto_compaction_enabled = True
    run.harness.state.model = Model(
        id="m", provider="p", api="unknown", context_window=200_000, max_tokens=4096
    )
    return run


async def _compactions(run: _Run) -> int:
    assert run.session is not None
    return len([e for e in await run.session.get_entries() if e.type == "compaction"])


@pytest.mark.parametrize("stage", ["summarizer", "hook_summary"])
async def test_esc_during_the_overflow_summary_stops_before_the_barrier(
    tmp_path: Path, stage: str
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        if stage == "summarizer":
            entered.set()
            await release.wait()
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    provider = _Provider("overflow")
    run = await _overflow_run(tmp_path, provider, summarise)
    if stage == "hook_summary":

        async def hook(event: Any, _ctx: Any) -> SessionBeforeCompactResult:
            entered.set()
            await release.wait()
            return SessionBeforeCompactResult(
                compaction=CompactResult(
                    summary="FROM A HOOK",
                    first_kept_entry_id=event.preparation.first_kept_entry_id,
                    tokens_before=99,
                )
            )

        run.harness.hooks.on("session_before_compact", hook)
    task = asyncio.create_task(run.harness.prompt("CANCELLED"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await run.harness.abort(withdraw_unanswered=True)
    release.set()
    await asyncio.wait_for(task, timeout=5)

    assert await _compactions(run) == 0
    assert _said(run) == [(["CANCELLED"], True)]
    assert "CANCELLED" not in run.state()
    assert "<failed>" not in run.state()
    disk = await run.disk()
    assert "CANCELLED" not in disk
    assert "<failed>" not in disk
    ends = [e for e in run.events if getattr(e, "type", "") == "compaction_end"]
    assert [(e.aborted, e.error_message) for e in ends] == [(True, None)]
    assert len(provider.seen) == 1, "no re-run after it"
    assert "CANCELLED" not in await run.next_request()


async def test_a_plain_abort_during_the_overflow_summary_still_compacts_and_keeps_the_input(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        entered.set()
        await release.wait()
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    run = await _overflow_run(tmp_path, _Provider("overflow"), summarise)
    task = asyncio.create_task(run.harness.prompt("KEPT"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await run.harness.abort()
    release.set()
    await asyncio.wait_for(task, timeout=5)

    assert await _compactions(run) == 1
    assert run.withdrawn == []
    assert "KEPT" in await run.disk()


async def test_esc_after_the_overflow_compaction_committed_stays_a_barrier(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    run = await _overflow_run(tmp_path, _Provider("overflow"), summarise)

    async def committed(_event: Any, _ctx: Any) -> None:
        entered.set()
        await release.wait()

    run.harness.hooks.on("session_compact", committed)
    task = asyncio.create_task(run.harness.prompt("KEPT"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await run.harness.abort(withdraw_unanswered=True)
    release.set()
    await asyncio.wait_for(task, timeout=5)

    assert await _compactions(run) == 1
    assert run.withdrawn == []
    assert "KEPT" in await run.disk()


async def test_esc_during_a_threshold_summary_after_an_answer_leaves_the_compaction_alone(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        entered.set()
        await release.wait()
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    run = await _overflow_run(tmp_path, _Provider("big_answer"), summarise)
    task = asyncio.create_task(run.harness.prompt("ANSWERED"))
    await asyncio.wait_for(entered.wait(), timeout=5)
    await run.harness.abort(withdraw_unanswered=True)
    release.set()
    await asyncio.wait_for(task, timeout=5)

    assert await _compactions(run) == 1
    assert run.withdrawn == []
    assert "ANSWERED" in await run.disk()


# === Review round 1: the last caller wins =====================================


async def test_a_plain_abort_after_a_stale_withdraw_keeps_the_input(tmp_path: Path) -> None:
    """``abort_retry`` asked to withdraw with no wait running, then SIGTERM's plain abort."""

    run = await _start(tmp_path, _Provider("hang"), True)
    task = asyncio.create_task(run.harness.prompt("KEPT"))
    await asyncio.wait_for(run.provider.reached.wait(), timeout=5)
    await asyncio.sleep(0)
    run.harness.abort_retry(withdraw_unanswered=True)
    await run.harness.abort()
    await asyncio.wait_for(task, timeout=5)

    assert run.withdrawn == []
    assert await run.disk() == ["KEPT"]


async def test_a_plain_abort_retry_does_not_cancel_an_earlier_esc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 60_000)
    run = await _start(tmp_path, _Provider("failed"), True)
    run.harness.state.auto_retry_enabled = True
    waiting = asyncio.Event()
    run.harness.subscribe(
        lambda e: waiting.set() if getattr(e, "type", "") == "auto_retry_start" else None
    )
    task = asyncio.create_task(run.harness.prompt("CANCELLED"))
    await asyncio.wait_for(waiting.wait(), timeout=5)
    await asyncio.sleep(0)
    run.harness.abort_retry(withdraw_unanswered=True)
    run.harness.abort_retry()
    await asyncio.wait_for(task, timeout=5)

    assert _said(run) == [(["CANCELLED"], True)]
    assert await run.disk() == []
