"""#414 - an assistant message whose stream never sent ``start`` still gets ``message_start``.

pi's loop emits ``message_start`` for a message that has no partial - its
``done`` or ``error`` arrives first - right before ``message_end``, carrying the
final message (``packages/agent/src/agent-loop.ts:451-453`` @ 3daa20280). Every
built-in aelix adapter sends ``start`` only once its stream is open, so a request
that failed before that - a ``502``, a refused key - ended with ``message_end``
alone. Measured on 529b997e with a real ``aelix --mode json`` against a local
server answering ``502`` (``.omc/probes/414-live/impl/``): anthropic,
openai-responses, openai-completions and google-generative-ai all printed
``message_end`` for the assistant with no ``message_start``.

The loop now gives such a message its ``message_start``
(:func:`aelix_agent_core.loop._with_message_start`), so every adapter - and an
extension's stream function - is covered. These rows pin the order at the loop,
through a real adapter, and at each consumer: the harness's listeners, the hook
bus, the session (``message_start`` writes nothing), the TUI renderer, rpc and
``--mode json``.

Hermetic: fake keys, ``httpx.MockTransport``, no socket.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from aelix_agent_core import (
    AgentContext,
    AgentLoopConfig,
    AgentTool,
    agent_loop,
    default_convert_to_llm,
)
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.types import MessageEndEvent, MessageStartEvent, MessageUpdateEvent
from aelix_ai import clear_providers
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult

from tests.env_sandbox import sandbox_home

_ERROR_TEXT = "Error code: 502 - fake upstream failure"


def _failed() -> AssistantMessage:
    return AssistantMessage(
        content=[], stop_reason="error", error_message=_ERROR_TEXT, provider="p", model="m"
    )


def _terminal(kind: str, message: AssistantMessage) -> AssistantMessageEvent:
    if kind == "error":
        return AssistantErrorEvent(
            reason="error", error=message, error_message=message.error_message
        )
    if kind == "done":
        return AssistantDoneEvent(reason="stop", message=message)
    return AssistantEndEvent(message=message)


def _stream_without_start(kind: str = "error", *, stray_update: bool = False) -> Any:
    """A stream that ends before it starts: what every adapter does on an HTTP error."""

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        if stray_update:
            yield TextDeltaEvent(delta="lost")
        message = (
            _failed()
            if kind == "error"
            else AssistantMessage(
                content=[TextContent(text="whole answer")], stop_reason="end_turn"
            )
        )
        yield _terminal(kind, message)

    return fn


def _assistant_events(events: list[Any]) -> list[Any]:
    return [
        e
        for e in events
        if isinstance(e, MessageStartEvent | MessageUpdateEvent | MessageEndEvent)
        and isinstance(e.message, AssistantMessage)
    ]


async def _run_loop(
    stream_fn: Any, emit: Any = None, tools: list[AgentTool] | None = None
) -> tuple[list[Any], list[Any]]:
    events: list[Any] = []

    async def collect(event: Any) -> Any:
        events.append(event)
        return await emit(event) if emit is not None else None

    new = await agent_loop(
        [UserMessage(content=[TextContent(text="hi")])],
        AgentContext(system_prompt="", messages=[], tools=list(tools or [])),
        AgentLoopConfig(model=Model(id="m", provider="p"), convert_to_llm=default_convert_to_llm),
        emit=collect,
        stream_fn=stream_fn,
    )
    return events, new


# === the loop ===============================================================


@pytest.mark.parametrize("kind", ["error", "done", "end"])
async def test_a_message_with_no_start_gets_its_message_start(kind: str) -> None:
    events, _new = await _run_loop(_stream_without_start(kind, stray_update=True))

    assistant = _assistant_events(events)
    assert [type(e).__name__ for e in assistant] == ["MessageStartEvent", "MessageEndEvent"]
    # pi's start carries the final message; here the very object message_end carries.
    assert assistant[0].message is assistant[1].message
    # And every message_end of the run has its message_start (the prompt's pair too).
    starts = [e for e in events if isinstance(e, MessageStartEvent)]
    ends = [e for e in events if isinstance(e, MessageEndEvent)]
    assert [s.message for s in starts] == [e.message for e in ends]


async def test_two_terminal_events_get_one_message_start() -> None:
    """The wrapper's latch (review round 2): aelix's loop keeps reading after a terminal
    event (pi's returns), so a stream that yields two of them ends its message twice. The
    first end gets the one ``message_start``; the second gets none."""

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield _terminal("error", _failed())
        yield _terminal("error", _failed())

    events, _new = await _run_loop(fn)

    assert [type(e).__name__ for e in _assistant_events(events)] == [
        "MessageStartEvent",
        "MessageEndEvent",
        "MessageEndEvent",
    ]


@pytest.mark.parametrize("kind", ["error", "done", "end"])
async def test_a_later_message_of_the_run_gets_its_own_message_start(kind: str) -> None:
    """Review round 4 (round 3's cross review, F2): the start is per message, not per run.
    In one run the first answer streams - its own ``start`` - and calls a tool; the tool
    runs; the next answer ends before its stream starts (its request failed - a ``502``
    after a tool call, the ordinary case - or its ``done`` or ``end`` came with no
    ``start``). pi synthesizes the start per ``streamAssistantResponse`` call
    (``agent-loop.ts:451-453``), so that second message gets exactly one ``message_start`` of
    its own. An implementation with one latch per run - once any ``message_start`` was seen
    in the run, never synthesize another - passed every row before this one."""

    first_partial = AssistantMessage(content=[])
    first = AssistantMessage(
        content=[ToolCallContent(tool_call_id="t1", tool_name="echo", input={})],
        stop_reason="tool_use",
    )
    second = (
        _failed()
        if kind == "error"
        else AssistantMessage(content=[TextContent(text="whole answer")], stop_reason="end_turn")
    )
    requests: list[int] = []

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        requests.append(len(requests) + 1)
        if len(requests) == 1:
            yield AssistantStartEvent(partial=first_partial)
            yield AssistantDoneEvent(reason="toolUse", message=first)
        else:
            yield _terminal(kind, second)

    async def echo(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    events, _new = await _run_loop(fn, tools=[AgentTool(name="echo", execute=echo)])

    assert requests == [1, 2]
    pairs = [
        (type(e).__name__, type(e.message).__name__)
        for e in events
        if isinstance(e, MessageStartEvent | MessageEndEvent)
    ]
    assert pairs == [
        ("MessageStartEvent", "UserMessage"),
        ("MessageEndEvent", "UserMessage"),
        ("MessageStartEvent", "AssistantMessage"),
        ("MessageEndEvent", "AssistantMessage"),
        ("MessageStartEvent", "ToolResultMessage"),
        ("MessageEndEvent", "ToolResultMessage"),
        ("MessageStartEvent", "AssistantMessage"),
        ("MessageEndEvent", "AssistantMessage"),
    ]
    assistant = [
        e
        for e in events
        if isinstance(e, MessageStartEvent | MessageEndEvent)
        and isinstance(e.message, AssistantMessage)
    ]
    # The streamed first answer keeps its own start (the partial); nothing is added to it.
    assert assistant[0].message is first_partial
    # The second answer: exactly one start, carrying the very object its end carries.
    second_end = assistant[3].message
    assert assistant[2].message is second_end
    assert [e for e in events if isinstance(e, MessageStartEvent) and e.message is second_end] == [
        assistant[2]
    ]


async def test_a_started_message_gets_exactly_one_message_start() -> None:
    partial = AssistantMessage(content=[])
    final = AssistantMessage(content=[TextContent(text="hello")], stop_reason="end_turn")

    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=partial)
        yield TextDeltaEvent(delta="hello")
        yield AssistantDoneEvent(reason="stop", message=final)

    events, _new = await _run_loop(fn)

    assistant = _assistant_events(events)
    assert [type(e).__name__ for e in assistant] == [
        "MessageStartEvent",
        "MessageUpdateEvent",
        "MessageEndEvent",
    ]
    assert assistant[0].message is partial


async def test_a_message_end_replacement_still_lands() -> None:
    """The wrapper returns what ``message_end`` returns: a hook's replacement still
    reaches the loop's messages (ADR-0145)."""

    replacement = AssistantMessage(content=[], stop_reason="error", error_message="rewritten")

    async def replace_assistant_end(event: Any) -> Any:
        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            return replacement
        return None

    events, new = await _run_loop(_stream_without_start("error"), emit=replace_assistant_end)

    assert new[-1] is replacement
    assistant = _assistant_events(events)
    assert [type(e).__name__ for e in assistant] == ["MessageStartEvent", "MessageEndEvent"]
    assert assistant[0].message.error_message == _ERROR_TEXT


# === a real adapter: anthropic's own 502 (the measured case) ================


async def test_anthropics_own_502_gets_its_message_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_ai.providers import anthropic as anthropic_mod
    from anthropic import AsyncAnthropic

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            502,
            json={
                "type": "error",
                "error": {"type": "api_error", "message": "fake upstream failure"},
            },
        )

    def client(**kw: Any) -> Any:
        return AsyncAnthropic(
            api_key=kw.get("api_key"),
            base_url=kw.get("base_url"),
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(anthropic_mod, "create_async_client", client)
    model = Model(
        api="anthropic-messages",
        id="claude-haiku-4-5",
        provider="anthropic",
        base_url="https://anthropic.invalid",
    )
    events: list[Any] = []

    async def collect(event: Any) -> None:
        events.append(event)

    await agent_loop(
        [UserMessage(content=[TextContent(text="hi")])],
        AgentContext(system_prompt="", messages=[], tools=[]),
        AgentLoopConfig(
            model=model, convert_to_llm=default_convert_to_llm, api_key="sk-ant-fake-414"
        ),
        emit=collect,
        stream_fn=anthropic_mod.stream_anthropic,
    )

    assert len(requests) == 1
    assistant = _assistant_events(events)
    assert [type(e).__name__ for e in assistant] == ["MessageStartEvent", "MessageEndEvent"]
    assert assistant[1].message.stop_reason == "error"
    assert "502" in (assistant[1].message.error_message or "")


# === the consumers ==========================================================


def _harness(session: Session | None = None) -> AgentHarness:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p"),
            stream_fn=_stream_without_start("error"),
            session=session,
        )
    )
    harness._state.auto_retry_enabled = False
    harness._state.auto_compaction_enabled = False
    return harness


async def test_listeners_hooks_and_the_session_see_one_pair() -> None:
    session = Session(MemorySessionStorage())
    harness = _harness(session)
    seen: list[Any] = []
    hooked: list[Any] = []

    async def on_start(event: Any, _ctx: Any) -> None:
        hooked.append(event.message)

    harness.hooks.on("message_start", on_start)
    harness.subscribe(seen.append)

    await harness.prompt("hi")

    assistant = _assistant_events(seen)
    assert [type(e).__name__ for e in assistant] == ["MessageStartEvent", "MessageEndEvent"]
    assert [m.role for m in hooked] == ["user", "assistant"]
    assert hooked[1].error_message == _ERROR_TEXT
    # message_start writes nothing: the session holds the prompt and the failure once each.
    entries = [e for e in await session.get_entries() if e.type == "message"]
    assert [e.message.role for e in entries] == ["user", "assistant"]
    await harness.dispose()


async def test_the_tui_renderer_reports_the_failure_once() -> None:
    """The renderer's ``message_start`` resets its per-message state; the error that
    ``message_end`` then reports is committed once and still read by the shell."""

    from aelix_coding_agent.tui.render import EventRenderer

    commits: list[Any] = []
    renderer = EventRenderer(commit=commits.append, set_tail=lambda _t: None, width=100)
    harness = _harness()
    harness.subscribe(renderer.on_agent_event)

    await harness.prompt("hi")

    texts = [str(getattr(c, "plain", c)) for c in commits]
    assert [t for t in texts if "fake upstream failure" in t] == [f"✖ {_ERROR_TEXT}"]
    assert renderer.take_reported_error() == _ERROR_TEXT
    assert renderer.take_turn_ended_in_error() is True
    await harness.dispose()


async def test_rpc_prints_the_pair() -> None:
    from aelix_coding_agent.rpc._jsonl import serialize_json_line
    from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

    stdin = asyncio.StreamReader()
    stdin.feed_data(serialize_json_line({"type": "prompt", "message": "hi", "id": "r1"}).encode())
    captured: list[bytes] = []

    async def eof_after_agent_end() -> None:
        for _ in range(3000):  # a 30 s guard on a condition, not a timed wait
            if b'"agent_end"' in b"".join(captured):
                break
            await asyncio.sleep(0.01)
        stdin.feed_eof()

    harness = _harness()
    await asyncio.gather(
        run_rpc_mode(
            harness, stdin=stdin, stdout_write=captured.append, install_signal_handlers=False
        ),
        eof_after_agent_end(),
    )

    records = [
        json.loads(line) for line in b"".join(captured).decode("utf-8").splitlines() if line.strip()
    ]
    pairs = [
        r["type"]
        for r in records
        if r.get("type") in ("message_start", "message_end")
        and (r.get("message") or {}).get("role") == "assistant"
    ]
    assert pairs == ["message_start", "message_end"]
    starts = [
        r
        for r in records
        if r.get("type") == "message_start" and r["message"]["role"] == "assistant"
    ]
    assert starts[0]["message"]["error_message"] == _ERROR_TEXT


@pytest.fixture
def anthropic_502(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """A real CLI run's anthropic adapter, answered 502 by a MockTransport."""

    from aelix_ai.providers import anthropic as anthropic_mod
    from anthropic import AsyncAnthropic

    for name in list(os.environ):
        if re.search(r"(_API_KEY|_KEY|_TOKEN|_SECRET)$", name) or name.startswith(
            ("OPENROUTER_", "AELIX_MCP_CONFIG", "AELIX_DOTENV_")
        ):
            monkeypatch.delenv(name)
    sandbox_home(monkeypatch, tmp_path / "home")
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("AELIX_SETTINGS_PATH", str(agent / "settings.json"))
    monkeypatch.setenv("AELIX_CODING_AGENT_SESSION_DIR", str(tmp_path / "sessions"))
    (agent / "settings.json").write_text(json.dumps({"retry": {"enabled": False}}), "utf-8")
    (agent / "models.json").write_text(
        json.dumps({"providers": {"anthropic": {"apiKey": "corp-gw-fake"}}}), "utf-8"
    )
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers.get("x-api-key") or "")
        return httpx.Response(
            502,
            json={
                "type": "error",
                "error": {"type": "api_error", "message": "fake upstream failure"},
            },
        )

    def client(**kw: Any) -> Any:
        return AsyncAnthropic(
            api_key=kw.get("api_key"),
            base_url=kw.get("base_url"),
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr(anthropic_mod, "create_async_client", client)
    clear_providers()
    anthropic_mod.register_all()
    yield requests
    clear_providers()


async def test_json_mode_prints_the_pair(
    anthropic_502: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real ``_async_main`` in ``--mode json``: what a json client reads. pi's json mode
    prints its session events, ``message_start`` included, for the same failure."""

    from aelix_coding_agent.cli import entry as entry_mod

    from tests.cli.test_launch_route_344 import _FakePipedStdin

    monkeypatch.setattr(sys, "stdin", _FakePipedStdin())
    rc = await entry_mod._async_main(
        [
            "--no-session",
            "--provider",
            "anthropic",
            "--model",
            "claude-haiku-4-5",
            "--mode",
            "json",
            "-p",
            "hi",
        ]
    )

    out = capsys.readouterr()
    lines = [json.loads(line) for line in out.out.splitlines() if line.startswith("{")]
    pairs = [
        e["type"]
        for e in lines
        if e.get("type") in ("message_start", "message_end")
        and (e.get("message") or {}).get("role") == "assistant"
    ]
    assert anthropic_502 == ["corp-gw-fake"]
    assert pairs == ["message_start", "message_end"]
    assistant_end = [
        e for e in lines if e.get("type") == "message_end" and e["message"]["role"] == "assistant"
    ]
    assert "502" in assistant_end[0]["message"]["error_message"]
    assert rc == 0, out.err
