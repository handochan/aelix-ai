"""#686 — a tool result carries its call's name, down to the request.

A Gemini model gets no id on ``functionResponse``, so ``name`` is all that says which call a
response answers, and it went out as ``""`` for every result a tool produced. An OpenAI-compatible
provider marked ``requires_tool_result_name`` got no ``name`` at all. Read here where it matters:
the payload the adapter builds from what the next request carries, each kind of result on both
paths, the session reopened, and a session saved before results had names.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import MessageEndEventResult
from aelix_agent_core.loop import _CUT_TEXT
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent, ToolResultMessage
from aelix_ai.providers._google_shared import convert_messages as google_convert_messages
from aelix_ai.providers._openai_compat import detect_compat
from aelix_ai.providers.openai_completions import convert_messages as openai_convert_messages
from aelix_ai.streaming import Context, Model
from aelix_ai.tools import ToolCancelled, ToolResult

from tests.harness._cancelled_turn import (
    CANCEL_WAYS,
    MODEL,
    NOT_STARTED,
    Provider,
    Run,
    finished_tool,
    held_tool,
    results_of,
    settle,
    start,
    text_of,
)

GEMINI = Model(
    id="gemini-2.5-flash",
    name="gemini-2.5-flash",
    api="google-generative-ai",
    provider="google",
    input=["text"],
)
NAMED_PROVIDER = Model(
    id="named",
    api="openai-completions",
    provider="named",
    base_url="https://example.invalid/v1",
    input=["text"],
    max_tokens=1024,
)


def google_responses(messages: list[Any]) -> list[dict[str, Any]]:
    contents = google_convert_messages(GEMINI, Context(messages=list(messages)))
    return [
        part["functionResponse"]
        for content in contents
        for part in content["parts"]
        if "functionResponse" in part
    ]


def google_names(messages: list[Any]) -> list[str]:
    responses = google_responses(messages)
    assert all("id" not in r for r in responses), "a Gemini model gets no id, the name is all"
    return [r["name"] for r in responses]


def named_provider_names(messages: list[Any], *, requires: bool = True) -> list[str | None]:
    compat = replace(detect_compat(NAMED_PROVIDER), requires_tool_result_name=requires)
    out = openai_convert_messages(NAMED_PROVIDER, Context(messages=list(messages)), compat)
    return [m.get("name") for m in out if m["role"] == "tool"]


def names_of(messages: list[Any]) -> dict[str, str]:
    return {m.tool_call_id: m.tool_name for m in messages if isinstance(m, ToolResultMessage)}


def _mode(sequential: bool) -> str | None:
    return "sequential" if sequential else None


# === What the loop makes =====================================================


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_finished_result_reaches_the_payload_with_its_name(
    tmp_path: Path, sequential: bool
) -> None:
    tools = [
        finished_tool("alpha", "A", sequential=sequential),
        finished_tool("beta", "B", sequential=sequential),
    ]
    run = await start(tmp_path, [("c1", "alpha"), ("c2", "beta")], tools)
    await run.harness.prompt("GO")

    second_request = run.provider.seen[-1]
    assert names_of(second_request) == {"c1": "alpha", "c2": "beta"}
    assert google_names(second_request) == ["alpha", "beta"]
    assert named_provider_names(second_request) == ["alpha", "beta"]


async def test_a_parallel_result_keeps_its_own_name_whatever_order_they_end_in(
    tmp_path: Path,
) -> None:
    beta_ended = asyncio.Event()

    async def alpha(_args: Any, _ctx: Any) -> ToolResult:
        await beta_ended.wait()
        return ToolResult(content=[TextContent(text="A")])

    async def beta(_args: Any, _ctx: Any) -> ToolResult:
        beta_ended.set()
        return ToolResult(content=[TextContent(text="B")])

    tools = [AgentTool(name="alpha", execute=alpha), AgentTool(name="beta", execute=beta)]
    run = await start(tmp_path, [("c1", "alpha"), ("c2", "beta")], tools)
    await run.harness.prompt("GO")

    assert names_of(run.provider.seen[-1]) == {"c1": "alpha", "c2": "beta"}
    assert google_names(run.provider.seen[-1]) == ["alpha", "beta"]


@pytest.mark.parametrize("sequential", [False, True])
async def test_an_immediate_result_is_named_after_the_call(
    tmp_path: Path, sequential: bool
) -> None:
    tools = [finished_tool("real", "R", sequential=sequential)]
    run = await start(tmp_path, [("c1", "nope"), ("c2", "real")], tools)
    await run.harness.prompt("GO")

    second_request = run.provider.seen[-1]
    assert text_of(next(m for m in second_request if isinstance(m, ToolResultMessage))) == (
        "Unknown tool: nope"
    )
    assert names_of(second_request) == {"c1": "nope", "c2": "real"}
    assert google_names(second_request) == ["nope", "real"]


@pytest.mark.parametrize("way", CANCEL_WAYS)
@pytest.mark.parametrize("sequential", [False, True])
async def test_a_result_kept_through_a_cancel_and_the_one_it_cut_have_their_names(
    tmp_path: Path, sequential: bool, way: str
) -> None:
    reached = asyncio.Event()
    tools = [
        finished_tool("reader", "READ", sequential=sequential),
        held_tool("slow", reached, sequential=sequential),
    ]
    run = await start(tmp_path, [("c1", "reader"), ("c2", "slow")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel(way)

    state = run.harness.state.messages
    assert results_of(state) == {"c1": ("READ", False), "c2": (_CUT_TEXT, True)}
    assert names_of(state) == {"c1": "reader", "c2": "slow"}
    assert google_names(state) == ["reader", "slow"]
    assert named_provider_names(state) == ["reader", "slow"]


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_result_the_tool_said_on_the_way_out_has_its_name(
    tmp_path: Path, sequential: bool
) -> None:
    reached = asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as cancel:
            raise ToolCancelled(ToolResult(content=[TextContent(text="SAID")])) from cancel
        return ToolResult(content=[])

    tool = AgentTool(name="sayer", execute=execute, execution_mode=_mode(sequential))
    run = await start(tmp_path, [("c1", "sayer")], [tool])
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    assert results_of(run.harness.state.messages) == {"c1": ("SAID", True)}
    assert names_of(run.harness.state.messages) == {"c1": "sayer"}
    assert google_names(run.harness.state.messages) == ["sayer"]


async def test_a_call_the_turn_stopped_waiting_for_has_its_name(tmp_path: Path) -> None:
    reached, released = asyncio.Event(), asyncio.Event()

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 12
        while not released.is_set() and loop.time() < deadline:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(0.002)
        return ToolResult(content=[])

    run = await start(tmp_path, [("c1", "stubborn")], [AgentTool(name="stubborn", execute=execute)])
    task = run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.harness.abort()
    await asyncio.sleep(0)
    await run.harness.abort()
    await settle(task)
    released.set()

    assert results_of(run.harness.state.messages) == {"c1": (_CUT_TEXT, True)}
    assert names_of(run.harness.state.messages) == {"c1": "stubborn"}
    assert google_names(run.harness.state.messages) == ["stubborn"]


async def test_a_call_that_never_began_still_goes_out_named(tmp_path: Path) -> None:
    reached = asyncio.Event()
    tools = [
        held_tool("first", reached, sequential=True),
        finished_tool("later", "NEVER", sequential=True),
    ]
    run = await start(tmp_path, [("c1", "first"), ("c2", "later")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    state = run.harness.state.messages
    assert names_of(state) == {"c1": "first"}
    responses = google_responses(state)
    assert [r["name"] for r in responses] == ["first", "later"]
    assert responses[1]["response"] == {"error": NOT_STARTED}


# === What a session holds ====================================================


@pytest.mark.parametrize("sequential", [False, True])
async def test_a_reopened_session_keeps_the_names(tmp_path: Path, sequential: bool) -> None:
    reached = asyncio.Event()
    tools = [
        finished_tool("reader", "READ", sequential=sequential),
        held_tool("slow", reached, sequential=sequential),
    ]
    run = await start(tmp_path, [("c1", "reader"), ("c2", "slow")], tools)
    run.begin()
    await asyncio.wait_for(reached.wait(), timeout=5)
    await run.cancel()

    reopened = await run.reopened()
    assert names_of(reopened) == {"c1": "reader", "c2": "slow"}
    assert google_names(reopened) == ["reader", "slow"]
    assert named_provider_names(reopened) == ["reader", "slow"]


def _strip_result_names(path: Path) -> int:
    """What a session saved before #686 holds: result lines with no name to read back."""

    stripped = 0
    lines = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        message = row.get("message") if isinstance(row, dict) else None
        if isinstance(message, dict) and "tool_name" in message and "tool_call_id" in message:
            del message["tool_name"]
            stripped += 1
        lines.append(json.dumps(row))
    path.write_text("\n".join(lines) + "\n")
    return stripped


async def _old_session(tmp_path: Path) -> Run:
    tools = [finished_tool("alpha", "A"), finished_tool("beta", "B")]
    run = await start(tmp_path, [("c1", "alpha"), ("c2", "beta")], tools)
    await run.harness.prompt("GO")
    assert run.path is not None
    assert _strip_result_names(run.path) == 2
    return run


async def test_an_old_session_line_with_no_name_goes_out_named_and_stays_as_it_was(
    tmp_path: Path,
) -> None:
    run = await _old_session(tmp_path)
    assert run.path is not None
    on_disk = run.path.read_bytes()

    reopened = await run.reopened()
    assert names_of(reopened) == {"c1": "", "c2": ""}
    assert google_names(reopened) == ["alpha", "beta"]
    assert named_provider_names(reopened) == ["alpha", "beta"]

    assert names_of(reopened) == {"c1": "", "c2": ""}, "the stored message is not touched"
    assert run.path.read_bytes() == on_disk


async def test_a_new_turn_on_an_old_session_sends_the_old_results_named(tmp_path: Path) -> None:
    run = await _old_session(tmp_path)
    assert run.path is not None
    session = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(run.path)))
    provider = Provider([])
    harness = AgentHarness(AgentHarnessOptions(model=MODEL, stream_fn=provider, session=session))
    harness.state.auto_retry_enabled = False
    harness.state.auto_compaction_enabled = False
    await harness.prompt("AGAIN")

    assert names_of(provider.seen[-1]) == {"c1": "", "c2": ""}
    assert google_names(provider.seen[-1]) == ["alpha", "beta"]


# === The OpenAI-compatible flag ==============================================


async def test_a_provider_that_does_not_ask_for_the_name_does_not_get_one(tmp_path: Path) -> None:
    run = await start(tmp_path, [("c1", "alpha")], [finished_tool("alpha", "A")])
    await run.harness.prompt("GO")

    assert named_provider_names(run.provider.seen[-1], requires=False) == [None]


async def test_a_replacement_that_forgets_the_name_goes_out_named(tmp_path: Path) -> None:
    run = await start(tmp_path, [("c1", "alpha")], [finished_tool("alpha", "A")])

    def redact(event: Any, _ctx: Any) -> Any:
        if isinstance(event.message, ToolResultMessage):
            return MessageEndEventResult(
                message=ToolResultMessage(
                    tool_call_id=event.message.tool_call_id,
                    content=[TextContent(text="REDACTED")],
                )
            )
        return None

    run.harness.hooks.on("message_end", redact)
    await run.harness.prompt("GO")

    assert results_of(run.provider.seen[-1]) == {"c1": ("REDACTED", False)}
    assert google_names(run.provider.seen[-1]) == ["alpha"]
