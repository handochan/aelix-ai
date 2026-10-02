"""#367 verify round 4, B1 — ``AgentHarness.hold_turns``, the turn gate.

The CLI holds the turns of a harness whose ``session_start`` runs while its
route is undecided (a pending launch, a rebuild under the late-provider hold).
A placeholder model alone did not do it: a handler could ``set_model`` and then
trigger a turn, which ran on the model it set. The gate refuses every entry
that sends a request, whatever model is current. A handler's ``trigger_turn``
runs as a refused turn (verify round 5): its waiters wake, nothing is sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

_REASON = "held for the test"


def _harness(seen: list[Context]) -> AgentHarness:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        seen.append(context)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return AgentHarness(AgentHarnessOptions(model=Model(id="mock", provider="mock"), stream_fn=fn))


def _users(context: Context) -> list[str]:
    return [
        " ".join(getattr(c, "text", "") for c in m.content)
        for m in context.messages
        if isinstance(m, UserMessage)
    ]


async def test_a_held_harness_refuses_a_prompt_before_it_claims_the_turn() -> None:
    seen: list[Context] = []
    harness = _harness(seen)
    harness.hold_turns(_REASON)
    assert harness.turns_held == _REASON
    with pytest.raises(AgentHarnessError) as info:
        await harness.prompt("hi")
    assert (info.value.code, str(info.value)) == ("invalid_state", _REASON)
    assert (seen, harness.is_idle, harness.messages) == ([], True, [])


async def test_a_trigger_turn_while_held_runs_as_a_refused_turn() -> None:
    """#367 verify round 5. Round 5 queued a gated ``trigger_turn`` message, so
    no turn started and none ended: a handler awaiting its turn's
    ``agent_end`` waited forever (and ``session_start`` with it). It now runs
    as a REFUSED turn — the events a turn that cannot reach its model emits
    (compared with a real one, a model with no adapter), the gate's reason as
    the error answer — and nothing is streamed. The ``next_turn`` queue is left
    for the next real prompt."""

    unreachable = AgentHarness(AgentHarnessOptions(model=Model(id="m1", provider="nowhere")))
    expected: list[str] = []
    unreachable.subscribe(lambda event, *a: expected.append(event.type))
    with pytest.raises(Exception, match="No provider registered"):
        await unreachable.prompt("hi")

    seen: list[Context] = []
    harness = _harness(seen)
    events: list[str] = []
    ended = asyncio.Event()

    def _listen(event: Any, *args: Any) -> None:
        events.append(event.type)
        if event.type == "agent_end":
            ended.set()

    harness.subscribe(_listen)
    harness.hold_turns(_REASON)
    action: Any = harness._action_send_message
    action(UserMessage(content=[TextContent(text="queued-before")]))
    action(UserMessage(content=[TextContent(text="hook-prompt")]), trigger_turn=True)
    await asyncio.wait_for(ended.wait(), timeout=5)
    await harness.wait_for_idle()
    assert seen == []
    assert events == expected
    answer = harness.messages[-1]
    assert isinstance(answer, AssistantMessage)
    assert (answer.stop_reason, answer.error_message) == ("error", _REASON)
    assert (harness.is_idle, harness.turns_held) == (True, _REASON)
    harness.hold_turns(None)
    await harness.prompt("hi")
    # No session here, so the refused turn's own message is not in the
    # history (a failed turn's prompt lives in the session); the queued one
    # rides with the prompt.
    assert [_users(c) for c in seen] == [["queued-before", "hi"]]


async def test_a_trigger_turn_while_a_refused_turn_runs_is_queued() -> None:
    """Asked while a turn (here the refused one) runs, a trigger is queued as an
    ungated busy trigger is; it rides with the next real prompt."""

    seen: list[Context] = []
    harness = _harness(seen)
    harness.hold_turns(_REASON)
    action: Any = harness._action_send_message
    action(UserMessage(content=[TextContent(text="first")]), trigger_turn=True)
    await asyncio.sleep(0)
    assert not harness.is_idle
    action(UserMessage(content=[TextContent(text="second")]), trigger_turn=True)
    await harness.wait_for_idle()
    harness.hold_turns(None)
    await harness.prompt("hi")
    assert [_users(c) for c in seen] == [["second", "hi"]]


async def test_a_held_harness_refuses_a_manual_compaction() -> None:
    harness = _harness([])
    harness.hold_turns(_REASON)
    with pytest.raises(AgentHarnessError) as info:
        await harness.compact()
    assert str(info.value) == _REASON


async def test_a_held_harness_refuses_a_summarising_tree_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#367 Codex pass 7, C-T. A branch summary is a request: with the turn-gate
    check removed from ``navigate_tree(summarize=True)`` Codex's mutant sent
    one ("/late/v1/chat/completions" under the gate) and all six #367 / #344 /
    #362 test files still passed. A held harness with a seeded branch refuses
    it with the gate's reason, and nothing reaches the wire."""

    import httpx
    from aelix_agent_core.harness.core import NavigateTreeOptions
    from aelix_agent_core.session import MemorySessionStorage, Session
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    sent: list[str] = []

    def _record(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(400, json={"error": {"message": "recorded", "code": 400}})

    real_init = httpx.AsyncClient.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.update(transport=httpx.MockTransport(_record), mounts={}, trust_env=False)
        kwargs.pop("proxy", None)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    session = Session(MemorySessionStorage())
    target = await session.append_message(UserMessage(content=[TextContent(text="first")]))
    await session.append_message(AssistantMessage(content=[TextContent(text="answer")]))
    await session.append_message(UserMessage(content=[TextContent(text="branch")]))
    late = Model(
        id="m1", provider="late", api="openai-completions", base_url="http://127.0.0.1:9/late/v1"
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            session=session,
            model=late,
            get_api_key_and_headers=lambda _: {"apiKey": "fake", "headers": {}},
        )
    )
    harness.hold_turns(_REASON)
    with pytest.raises(AgentHarnessError) as info:
        await asyncio.wait_for(
            harness.navigate_tree(target, NavigateTreeOptions(summarize=True)), timeout=5
        )
    assert (info.value.code, str(info.value)) == ("invalid_state", _REASON)
    assert (sent, harness.is_idle, harness.turns_held) == ([], True, _REASON)


async def test_a_lifted_gate_runs_turns_as_before() -> None:
    seen: list[Context] = []
    harness = _harness(seen)
    harness.hold_turns(_REASON)
    harness.hold_turns(None)
    await harness.prompt("hi")
    assert [_users(c) for c in seen] == [["hi"]]
