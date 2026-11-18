"""#123 (ADR-0306 §4.2): the harness's final-steer latch.

``steer(text, final=True)`` marks the message; the drain that returns it marks it
consumed; the next model call is made with NO tools and ends the run, whatever
the model answers. The latch is read at every drain, so a final still queued when
the run ends on an error or an abort stays unconsumed — the run ends as today —
and a new prompt starts with the flag down. The kernel knows "final" and nothing
of who asked (the band rule: ``tests/agents/test_p2_band_boundaries.py``).

Fails on ``a1606f58``: ``steer`` takes no ``final``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core import AgentTool
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolResult


class _Model:
    """Calls 1..n-1 ask for ``probe``; the ``answers`` script says what each call returns otherwise."""

    def __init__(self, *, tool_calls: int = 1, error_at: int | None = None) -> None:
        self.tool_calls = tool_calls
        self.error_at = error_at
        self.seen: list[tuple[list[str], int]] = []

    async def __call__(self, _m: Model, context: Context, _o: SimpleStreamOptions) -> AsyncIterator[AssistantMessageEvent]:
        n = len(self.seen) + 1
        self.seen.append(([f"{m.role}:{getattr(m.content[0], 'text', getattr(m.content[0], 'tool_name', ''))}" for m in context.messages if m.content], len(context.tools)))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if self.error_at == n:
            yield AssistantEndEvent(message=AssistantMessage(content=[], stop_reason="error", error_message="boom"))
            return
        if n <= self.tool_calls and context.tools:
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[ToolCallContent(tool_call_id=f"c{n}", tool_name="probe", input={"n": n})],
                    stop_reason="tool_use",
                )
            )
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text=f"answer {n}")], stop_reason="end_turn"))


def _harness(model: _Model, on_probe: Any = None) -> AgentHarness:
    async def probe(_args: Any, _ctx: Any) -> ToolResult:
        if on_probe is not None:
            await on_probe()
        return ToolResult(content=[TextContent(text="probed")])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=model,
            tools=[AgentTool(name="probe", execute=probe)],
        )
    )
    harness.state.auto_retry_enabled = False
    return harness


async def test_a_consumed_final_makes_the_next_call_tool_less_and_last() -> None:
    model = _Model(tool_calls=3)
    harness: AgentHarness

    async def during_probe() -> None:
        await harness.steer("FINAL", final=True)

    harness = _harness(model, during_probe)
    await harness.prompt("go")
    assert [tools for _msgs, tools in model.seen] == [1, 0], "one tool-less call after the final, then the end"
    assert model.seen[1][0][-1] == "user:FINAL"
    assert harness.state.messages[-1].content[0].text == "answer 2"  # type: ignore[union-attr]
    assert harness.is_idle


async def test_the_tool_less_call_is_the_last_even_when_the_model_still_asks_for_a_tool() -> None:
    """The flag is the contract, the empty tool list the baseline (ADR-0306 §4.2): a model
    that answers the tool-less call with a tool call anyway gets no third call."""

    class _Insistent(_Model):
        async def __call__(self, m: Model, context: Context, o: SimpleStreamOptions) -> AsyncIterator[AssistantMessageEvent]:
            n = len(self.seen) + 1
            self.seen.append(([], len(context.tools)))
            yield AssistantStartEvent(partial=AssistantMessage(content=[]))
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[ToolCallContent(tool_call_id=f"c{n}", tool_name="probe", input={"n": n})],
                    stop_reason="tool_use",
                )
            )

    model = _Insistent()
    harness: AgentHarness

    async def during_probe() -> None:
        if harness._final_message is None and not harness._final_consumed:
            await harness.steer("FINAL", final=True)

    harness = _harness(model, during_probe)
    await harness.prompt("go")
    assert [tools for _msgs, tools in model.seen] == [1, 0], "two calls and no more"
    assert harness.is_idle


async def test_a_final_queued_while_the_failing_response_streams_stays_unconsumed() -> None:
    """F9: the precedence leg — an ``error`` end before any drain read the final (contract r2 §4.2).

    The final is queued while call 1 streams and call 1 ends ``error``: the run
    ends on the error as it always did, no drain ran, so the final is neither
    consumed nor in the transcript — it stays queued (in a print child it dies
    with the process, and the parent's row says "never reached the model").
    A guard: it passes on ``8d7ee999``; it fails when an error end drains the
    steering queue (``mutation_probe.py error_drain``), which the test that
    carried this name until fix round 1 did not — its final was consumed before
    the error.
    """

    tools: list[int] = []
    harness: AgentHarness

    async def stream(_m: Model, context: Context, _o: SimpleStreamOptions) -> AsyncIterator[AssistantMessageEvent]:
        tools.append(len(context.tools))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        await harness.steer("FINAL", final=True)
        yield AssistantEndEvent(message=AssistantMessage(content=[], stop_reason="error", error_message="boom"))

    harness = _harness(stream)  # type: ignore[arg-type]
    await harness.prompt("go")
    assert tools == [1], "one call: the error ended the run, nothing was read after it"
    assert harness.state.messages[-1].stop_reason == "error"  # type: ignore[union-attr]
    assert harness._final_consumed is False, "no drain returned it"
    assert harness._final_message is not None, "still queued"
    said = [getattr(block, "text", "") for message in harness.state.messages for block in message.content]
    assert "FINAL" not in said, "the model never read it"


async def test_a_final_read_by_a_call_that_errors_ends_the_run_on_the_error() -> None:
    model = _Model(tool_calls=1, error_at=2)
    harness: AgentHarness

    async def during_probe() -> None:
        await harness.steer("FINAL", final=True)

    harness = _harness(model, during_probe)
    await harness.prompt("go")
    # The drain after the tool batch hands the final to call 2, which errors:
    # consumed, tool-less, and the run ends on the error as it does today.
    assert [tools for _msgs, tools in model.seen] == [1, 0]
    assert harness.state.messages[-1].stop_reason == "error"  # type: ignore[union-attr]


async def test_a_final_sent_while_the_model_streams_is_read_at_the_next_drain_only() -> None:
    model = _Model(tool_calls=1)
    harness = _harness(model)
    await harness.prompt("go")
    await harness.steer("FINAL", final=True)  # idle: queued for the next prompt's first drain
    assert harness._final_message is not None and harness._final_consumed is False
    await harness.prompt("again")
    assert [tools for _msgs, tools in model.seen][-1] == 0
    assert model.seen[-1][0][-1] == "user:FINAL" or any(
        "user:FINAL" in msgs for msgs, _ in model.seen
    )
    assert harness._final_message is None


async def test_an_abort_withdraws_a_queued_final_and_a_new_prompt_lowers_the_flag() -> None:
    model = _Model(tool_calls=1)
    harness: AgentHarness

    async def during_probe() -> None:
        await harness.steer("FINAL", final=True)
        await harness.abort()

    harness = _harness(model, during_probe)
    await harness.prompt("go")
    assert harness._final_message is None, "cleared with the queue"
    assert harness._final_consumed is False
    assert len(model.seen) == 1, "the abort ended the run before any second call"
    model2 = _Model(tool_calls=0)
    harness._options.stream_fn = model2  # type: ignore[misc]
    harness._final_consumed = True  # as a consumed final would leave it
    await harness.prompt("fresh")
    assert model2.seen[0][1] == 1, "a new prompt runs with its tools"
    assert harness._final_consumed is False


class _AsksOnTheSecondCall:
    """Call 2 asks for ``probe`` whether or not it was offered one; every other call answers."""

    def __init__(self) -> None:
        self.tools: list[int] = []

    async def __call__(self, _m: Model, context: Context, _o: SimpleStreamOptions) -> AsyncIterator[AssistantMessageEvent]:
        self.tools.append(len(context.tools))
        n = len(self.tools)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if n == 2:
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[ToolCallContent(tool_call_id="c2", tool_name="probe", input={})], stop_reason="tool_use"
                )
            )
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text=f"answer {n}")], stop_reason="end_turn"))


async def _noticed_harness(model: Any, probed: list[int]) -> AgentHarness:
    from aelix_agent_core.session import MemorySessionStorage, Session

    async def probe(_args: Any, _ctx: Any) -> ToolResult:
        probed.append(1)
        return ToolResult(content=[TextContent(text="probed")])

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=model,
            tools=[AgentTool(name="probe", execute=probe)],
            notice_inbox=True,
            session=Session(MemorySessionStorage()),
        )
    )
    harness.state.auto_retry_enabled = False
    harness.state.auto_compaction_enabled = False
    return harness


async def test_a_notice_turn_after_a_consumed_final_has_its_tools() -> None:
    """Fix round 1, F5: a consumed final belongs to the run that read it, a notice turn's included.

    Fails on ``8d7ee999``: ``prompt_notices`` did not lower the flag, so the
    notice turn's call had no tools and ended the run whatever it asked for
    (``[0, 0]``, the probe never run).
    """

    from aelix_agent_core.harness.inbox import Notice

    model = _AsksOnTheSecondCall()
    probed: list[int] = []
    harness = await _noticed_harness(model, probed)
    try:
        await harness.steer("FINAL", final=True)
        await harness.prompt("TASK")
        assert model.tools == [0], "the prompt that read the final: one call, no tools"
        harness.post_notice(Notice(notice_id="n1", custom_type="test", body="job ended"))
        result = await harness.prompt_notices()
        assert result.started
        assert model.tools == [0, 1, 1], "the notice turn is a run of its own: tools on both of its calls"
        assert probed == [1], "and the tool it asked for ran"
    finally:
        await harness.dispose()


async def test_final_consumed_says_whether_the_last_run_read_a_final_until_the_next_run_starts() -> None:
    """The read-only door a host reads between two runs (print mode's drain, F5). New in fix round 1."""

    from aelix_agent_core.harness.inbox import Notice

    model = _AsksOnTheSecondCall()
    harness = await _noticed_harness(model, [])
    try:
        assert harness.final_consumed is False
        await harness.steer("FINAL", final=True)
        assert harness.final_consumed is False, "queued is not consumed"
        await harness.prompt("TASK")
        assert harness.final_consumed is True, "still up once the run that read it is over"
        harness.post_notice(Notice(notice_id="n1", custom_type="test", body="job ended"))
        await harness.prompt_notices()
        assert harness.final_consumed is False, "lowered by the next run start"
    finally:
        await harness.dispose()


# === fix round 1, F6: "exactly one more call" was wider than the code ==========


@pytest.mark.parametrize("third", ["text", "tool_call"])
async def test_an_automatic_retry_of_the_finals_call_is_tool_less_and_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, third: str
) -> None:
    """The final's call answers 503 and the harness retries it: the retry has no tools either, and is the last.

    ADR-0306 D6 says "the next model call runs with no tools and the run ends
    after it; an automatic retry of that call is tool-less too" — two calls
    after the final here, not "exactly one". A guard (the finding asks for the
    pin): it passes on ``8d7ee999``; the mutant that lowers the flag where the
    retry re-enters ``_run`` gives ``[1, 0, 1]``.
    """

    from aelix_agent_core.harness import core

    monkeypatch.setattr(core, "_AUTO_RETRY_BASE_DELAY_MS", 1)
    tools: list[int] = []
    harness: AgentHarness

    async def model(_m: Model, context: Context, _o: SimpleStreamOptions) -> AsyncIterator[AssistantMessageEvent]:
        tools.append(len(context.tools))
        n = len(tools)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if n == 2:
            yield AssistantEndEvent(message=AssistantMessage(content=[], stop_reason="error", error_message="503 service unavailable"))
        elif n == 1 or third == "tool_call":
            call = ToolCallContent(tool_call_id=f"c{n}", tool_name="probe", input={"n": n})
            yield AssistantEndEvent(message=AssistantMessage(content=[call], stop_reason="tool_use"))
        else:
            yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn"))

    probed: list[int] = []

    async def probe(_args: Any, _ctx: Any) -> ToolResult:
        probed.append(1)
        await harness.steer("FINAL", final=True)
        return ToolResult(content=[TextContent(text="probed")])

    harness = AgentHarness(
        AgentHarnessOptions(model=Model(id="mock", provider="mock"), stream_fn=model, tools=[AgentTool(name="probe", execute=probe)])
    )
    harness.state.auto_compaction_enabled = False
    await harness.prompt("go")
    assert tools == [1, 0, 0], "the final's call and its retry, both with no tools, and no call after the retry"
    assert probed == [1], "a tool the retried call asked for is not run"
    assert harness.is_idle


async def test_an_overflow_recovery_of_the_finals_call_is_tool_less_too() -> None:
    """The final's call is refused as too long; the harness compacts and re-runs it: still no tools, still the last.

    The other re-entry of ``_run`` inside one prompt (ADR-0306 D6). A guard, as
    the retry's: it passes on ``8d7ee999``.
    """

    from aelix_agent_core.session import MemorySessionStorage, Session
    from aelix_agent_core.session.compaction import SUMMARIZATION_SYSTEM_PROMPT
    from aelix_ai import clear_providers, register_provider
    from aelix_ai.messages import UserMessage
    from aelix_ai.settings import SettingsManager
    from aelix_ai.streaming import AssistantDoneEvent, AssistantErrorEvent, TextDeltaEvent

    api = "final-overflow-123"
    overflow = "prompt is too long: 250000 tokens > 200000 maximum"
    turns: list[int] = []
    summaries: list[int] = []
    harness: AgentHarness

    async def stream(_model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        if getattr(context, "system_prompt", None) == SUMMARIZATION_SYSTEM_PROMPT:
            summaries.append(len(context.tools or []))
            yield TextDeltaEvent(delta="## Goal\nthe summary")
            yield AssistantDoneEvent(
                reason="stop",
                message=AssistantMessage(content=[TextContent(text="## Goal\nthe summary")], stop_reason="end_turn", usage={"total_tokens": 10}),
            )
            return
        turns.append(len(context.tools))
        n = len(turns)
        if n == 2:
            message = AssistantMessage(content=[], stop_reason="error", error_message=overflow)
            yield AssistantErrorEvent(reason="error", error=message, error_message=overflow)
            return
        content: list[Any] = [ToolCallContent(tool_call_id="c1", tool_name="probe", input={})] if n == 1 else [TextContent(text="done")]
        yield AssistantDoneEvent(
            reason="toolUse" if n == 1 else "stop",
            message=AssistantMessage(content=content, stop_reason="tool_use" if n == 1 else "end_turn", usage={"total_tokens": 10}),
        )

    async def probe(_args: Any, _ctx: Any) -> ToolResult:
        await harness.steer("FINAL", final=True)
        return ToolResult(content=[TextContent(text="probed")])

    async def auth(_model: Any) -> Any:
        return {"apiKey": "fake-key", "headers": {}}

    clear_providers()
    register_provider(api, stream)
    try:
        session = Session(MemorySessionStorage())
        for _ in range(6):
            await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
        harness = AgentHarness(
            AgentHarnessOptions(
                model=Model(api=api, id="m", provider="p", context_window=20_000),
                session=session,
                tools=[AgentTool(name="probe", execute=probe)],
                get_api_key_and_headers=auth,
                settings_manager=SettingsManager.in_memory({"retry": {"maxRetries": 2, "baseDelayMs": 1}}),
            )
        )
        await harness.prompt("go")
        assert summaries, "the overflow was recovered by a compaction (else this pins nothing)"
        assert turns == [1, 0, 0], "the final's call and its re-run after the compaction, both with no tools, then the end"
        assert harness.state.messages[-1].content[0].text == "done"  # type: ignore[union-attr]
    finally:
        clear_providers()
