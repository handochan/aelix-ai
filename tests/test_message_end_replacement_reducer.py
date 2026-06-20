"""P0 #7 Wave 3 (ADR-0145) — ``message_end`` replacement reducer.

Supersedes the old ``test_message_end_remains_observational.py`` pin tests
(ADR-0013 + ADR-0018 "observational only" was a layer mix-up: pi DOES
implement a ``message_end`` replacement reducer in its extension-runner layer
``runner.ts:714`` ``emitMessageEnd``; aelix mirrors that layer).

The replacement is REAL — same failure class as the thinking no-op (ADR-0135)
if it were a dangling reducer — so these tests assert it on all three planes:

1. registry wiring (``HOOK_RESULT_TYPES`` / ``_REDUCERS``);
2. the pure reducer (chain, role mismatch, None);
3. end-to-end through the harness: the loop return + ``state.messages`` (no
   session) and the PERSISTED session entry (with a session) all reflect the
   replacement, not the original.

``AgentMessage`` is a frozen dataclass — the replacement is an identity swap,
never in-place mutation.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core import AgentTool
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    _REDUCERS,
    HOOK_RESULT_TYPES,
    MessageEndEventResult,
    MessageEndHookEvent,
    _reducer_message_end,
)
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_coding_agent.extensions.api import (
    ExtensionContext,
    _ExtensionRuntime,
)

# === Stream mock ===========================================================


def _stream(text: str = "original") -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text=text)],
                stop_reason="end_turn",
            )
        )

    return fn


def _ctx() -> ExtensionContext:
    return ExtensionContext(
        _ExtensionRuntime(),
        cwd=".",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )


def _text(msg: Any) -> str:
    return "".join(
        c.text for c in msg.content if isinstance(c, TextContent)
    )


# === 1. Registry wiring (NEW behavior; replaces the old pin tests) =========


def test_message_end_result_type_is_message_end_event_result() -> None:
    """The old pin asserted ``HOOK_RESULT_TYPES['message_end'] is None``.

    ADR-0145 flips it to :class:`MessageEndEventResult` — the replacement
    reducer is now registered.
    """

    assert HOOK_RESULT_TYPES["message_end"] is MessageEndEventResult


def test_message_end_reducer_is_replacement_reducer() -> None:
    """The old pin asserted ``_REDUCERS['message_end'] is _reducer_observational``."""

    assert _REDUCERS["message_end"] is _reducer_message_end


def test_message_end_event_result_default_is_no_replacement() -> None:
    assert MessageEndEventResult().message is None


# === 2. Pure reducer ========================================================


async def test_reducer_no_handlers_returns_none() -> None:
    original = AssistantMessage(content=[TextContent(text="x")])
    out = await _reducer_message_end(
        [], MessageEndHookEvent(message=original), _ctx()
    )
    assert out is None


async def test_reducer_handler_returns_none_keeps_original() -> None:
    original = AssistantMessage(content=[TextContent(text="x")])

    async def handler(_e: Any, _c: Any) -> None:
        return None

    out = await _reducer_message_end(
        [(handler, "throw")], MessageEndHookEvent(message=original), _ctx()
    )
    assert out is None  # None == "no replacement"


async def test_reducer_replaces_with_same_role() -> None:
    original = AssistantMessage(content=[TextContent(text="orig")])
    replacement = AssistantMessage(content=[TextContent(text="redacted")])

    async def handler(_e: Any, _c: Any) -> MessageEndEventResult:
        return MessageEndEventResult(message=replacement)

    out = await _reducer_message_end(
        [(handler, "throw")], MessageEndHookEvent(message=original), _ctx()
    )
    assert out is replacement
    assert _text(out) == "redacted"


async def test_reducer_sequential_chain_second_sees_first() -> None:
    """Handler N sees handler N-1's replacement (pi ``{...event, message}``)."""

    original = AssistantMessage(content=[TextContent(text="a")])
    seen: list[str] = []

    async def first(e: Any, _c: Any) -> MessageEndEventResult:
        seen.append(_text(e.message))
        return MessageEndEventResult(
            message=AssistantMessage(content=[TextContent(text="b")])
        )

    async def second(e: Any, _c: Any) -> MessageEndEventResult:
        seen.append(_text(e.message))
        return MessageEndEventResult(
            message=AssistantMessage(content=[TextContent(text="c")])
        )

    out = await _reducer_message_end(
        [(first, "throw"), (second, "throw")],
        MessageEndHookEvent(message=original),
        _ctx(),
    )
    # First saw the original "a"; the second saw the first's replacement "b".
    assert seen == ["a", "b"]
    assert _text(out) == "c"


async def test_reducer_role_mismatch_skips_and_warns_no_raise(
    caplog: Any,
) -> None:
    """Role mismatch → original kept, warning logged, NO raise (pi emits an
    error event; aelix logs)."""

    original = AssistantMessage(content=[TextContent(text="orig")])
    # A different role (user) — must be skipped.
    bad = UserMessage(content=[TextContent(text="evil")])

    async def handler(_e: Any, _c: Any) -> MessageEndEventResult:
        return MessageEndEventResult(message=bad)

    with caplog.at_level(logging.WARNING):
        out = await _reducer_message_end(
            [(handler, "throw")], MessageEndHookEvent(message=original), _ctx()
        )

    # Skipped → no replacement → None (original kept by the caller).
    assert out is None
    assert any(
        "same role" in r.getMessage() or "role" in r.getMessage()
        for r in caplog.records
    )


async def test_reducer_role_mismatch_then_valid_keeps_only_valid() -> None:
    """A mismatching handler is skipped but a later same-role handler still
    applies (the chain continues past the skip)."""

    original = AssistantMessage(content=[TextContent(text="orig")])
    bad = UserMessage(content=[TextContent(text="bad-role")])
    good = AssistantMessage(content=[TextContent(text="good")])

    async def mismatched(_e: Any, _c: Any) -> MessageEndEventResult:
        return MessageEndEventResult(message=bad)

    async def valid(e: Any, _c: Any) -> MessageEndEventResult:
        # Sees the ORIGINAL (the mismatch was skipped, not chained).
        assert _text(e.message) == "orig"
        return MessageEndEventResult(message=good)

    out = await _reducer_message_end(
        [(mismatched, "throw"), (valid, "throw")],
        MessageEndHookEvent(message=original),
        _ctx(),
    )
    assert out is good


# === 3. End-to-end: no session (loop return + state.messages) ===============


async def test_no_session_replacement_flows_into_state_and_return() -> None:
    """No session: ``state.messages`` AND the ``prompt`` return receive the
    replacement via the loop identity-swap → ``new_messages``."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream("original")))

    redacted = AssistantMessage(
        content=[TextContent(text="REDACTED")], stop_reason="end_turn"
    )

    async def handler(e: Any, _c: Any) -> Any:
        # Only replace the assistant reply, not the user echo.
        if isinstance(e.message, AssistantMessage):
            return MessageEndEventResult(message=redacted)
        return None

    h.hooks.on("message_end", handler)  # type: ignore[arg-type]
    out = await h.prompt("hi")

    # Loop return: the assistant entry is the replacement object.
    assistant_out = [m for m in out if isinstance(m, AssistantMessage)]
    assert len(assistant_out) == 1
    assert assistant_out[0] is redacted

    # state.messages: the replacement landed, the original "original" did not.
    state_assistants = [
        m for m in h.state.messages if isinstance(m, AssistantMessage)
    ]
    assert state_assistants[-1] is redacted
    assert _text(state_assistants[-1]) == "REDACTED"
    assert all(_text(m) != "original" for m in state_assistants)


# === 3b. End-to-end: WITH session (PERSISTED message is the replacement) ====


async def test_session_persists_the_replacement_not_the_original() -> None:
    """The strongest guarantee: with a Session attached, the PERSISTED entry
    is the REPLACEMENT, not the original. This requires the emit-closure
    reorder (reduce BEFORE ``session.append_message``)."""

    session = Session(MemorySessionStorage())
    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_stream("original"), session=session)
    )

    redacted = AssistantMessage(
        content=[TextContent(text="REDACTED")], stop_reason="end_turn"
    )

    async def handler(e: Any, _c: Any) -> Any:
        if isinstance(e.message, AssistantMessage):
            return MessageEndEventResult(message=redacted)
        return None

    h.hooks.on("message_end", handler)  # type: ignore[arg-type]
    await h.prompt("hi")

    entries = await session.get_entries()
    persisted = [
        e.message  # type: ignore[union-attr]
        for e in entries
        if getattr(e, "type", None) == "message"
    ]
    assistant_persisted = [
        m for m in persisted if isinstance(m, AssistantMessage)
    ]
    assert len(assistant_persisted) == 1
    # The PERSISTED message is the replacement, not the original.
    assert _text(assistant_persisted[0]) == "REDACTED"
    assert assistant_persisted[0] is redacted


async def test_session_no_handler_persists_original() -> None:
    """No message_end handler → the original is persisted unchanged (zero
    regression to the observational/no-op path)."""

    session = Session(MemorySessionStorage())
    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_stream("original"), session=session)
    )
    await h.prompt("hi")

    entries = await session.get_entries()
    persisted = [
        e.message  # type: ignore[union-attr]
        for e in entries
        if getattr(e, "type", None) == "message"
    ]
    assistant_persisted = [
        m for m in persisted if isinstance(m, AssistantMessage)
    ]
    assert _text(assistant_persisted[0]) == "original"


async def test_emit_returns_none_when_no_replacement() -> None:
    """When no handler replaces the message, the message_end emit returns
    None (the loop then keeps the original ``final`` object — no swap)."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream("original")))
    out = await h.prompt("hi")
    # The original streamed object survived into state (identity preserved).
    assistant_out = [m for m in out if isinstance(m, AssistantMessage)]
    assert _text(assistant_out[0]) == "original"
    state_assistants = [
        m for m in h.state.messages if isinstance(m, AssistantMessage)
    ]
    assert state_assistants[-1] is assistant_out[0]


# === 4. Tool-result message_end replacement (LENS A/D case (c)) ==============
#
# The CRITICAL/HIGH regression: the parallel tool-execution path (the Pi-parity
# DEFAULT, ``tool_execution="parallel"``) previously DISCARDED the replacement
# returned by ``_emit_tool_result_message`` — the swapped ToolResultMessage was
# persisted to the session but never reached ``new_messages`` → ``_state.messages``,
# diverging in-memory state from the session and feeding the LLM the unredacted
# original on the next turn. The two sequential call sites captured it; the
# parallel site did not. A single parametrized test over ``tool_execution`` pins
# BOTH paths permanently.


def _tool_call_stream(
    tool_calls: list[ToolCallContent], final_text: str = "done"
) -> Any:
    """A 2-turn mock: turn 1 emits the given tool calls, turn 2 finalizes."""

    idx = {"i": 0}

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        i = idx["i"]
        idx["i"] += 1
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        if i == 0:
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=list(tool_calls), stop_reason="tool_use"
                )
            )
        else:
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text=final_text)],
                    stop_reason="end_turn",
                )
            )

    return fn


async def _echo_execute(
    args: dict[str, Any], ctx: ToolExecutionContext
) -> ToolResult:
    return ToolResult(content=[TextContent(text="ORIGINAL")])


@pytest.mark.parametrize("tool_execution", ["sequential", "parallel"])
async def test_tool_result_replacement_lands_in_state_both_paths(
    tool_execution: str,
) -> None:
    """A ``message_end`` handler that replaces a ToolResultMessage (same role)
    must land in ``state.messages`` under BOTH execution modes.

    Pins the dropped-replacement bug at the parallel Phase-3 emit site
    (``loop.py`` ``_execute_tool_calls_parallel``): the fix mirrors the two
    sequential sites by CAPTURING the helper return. Two tool calls force/confirm
    the parallel router. Without the fix the parallel parametrization fails
    because the original ``ORIGINAL`` survives into state.
    """

    tools = [
        AgentTool(name="echo_a", execute=_echo_execute),
        AgentTool(name="echo_b", execute=_echo_execute),
    ]
    tool_calls = [
        ToolCallContent(tool_call_id="c0", tool_name="echo_a", input={}),
        ToolCallContent(tool_call_id="c1", tool_name="echo_b", input={}),
    ]

    h = AgentHarness(
        AgentHarnessOptions(
            stream_fn=_tool_call_stream(tool_calls),
            tools=tools,
            tool_execution=tool_execution,  # type: ignore[arg-type]
        )
    )

    async def handler(e: Any, _c: Any) -> Any:
        # Replace every tool-result message with a redacted SAME-ROLE message.
        if isinstance(e.message, ToolResultMessage):
            return MessageEndEventResult(
                message=ToolResultMessage(
                    tool_call_id=e.message.tool_call_id,
                    content=[TextContent(text="REDACTED")],
                    is_error=False,
                )
            )
        return None

    h.hooks.on("message_end", handler)  # type: ignore[arg-type]
    out = await h.prompt("go")

    # The returned new_messages tool-result entries are ALL the replacement.
    out_results = [m for m in out if isinstance(m, ToolResultMessage)]
    assert len(out_results) == 2
    assert [_text(m) for m in out_results] == ["REDACTED", "REDACTED"], (
        f"{tool_execution} path dropped the tool-result replacement: "
        f"{[_text(m) for m in out_results]}"
    )

    # state.messages agrees — no ORIGINAL survived.
    state_results = [
        m for m in h.state.messages if isinstance(m, ToolResultMessage)
    ]
    assert [_text(m) for m in state_results] == ["REDACTED", "REDACTED"]
    assert all(_text(m) != "ORIGINAL" for m in state_results)


@pytest.mark.parametrize("tool_execution", ["sequential", "parallel"])
async def test_tool_result_replacement_persisted_both_paths(
    tool_execution: str,
) -> None:
    """With a Session attached, the PERSISTED tool-result entry is the
    replacement under BOTH execution modes — state and session agree."""

    tools = [
        AgentTool(name="echo_a", execute=_echo_execute),
        AgentTool(name="echo_b", execute=_echo_execute),
    ]
    tool_calls = [
        ToolCallContent(tool_call_id="c0", tool_name="echo_a", input={}),
        ToolCallContent(tool_call_id="c1", tool_name="echo_b", input={}),
    ]

    session = Session(MemorySessionStorage())
    h = AgentHarness(
        AgentHarnessOptions(
            stream_fn=_tool_call_stream(tool_calls),
            tools=tools,
            tool_execution=tool_execution,  # type: ignore[arg-type]
            session=session,
        )
    )

    async def handler(e: Any, _c: Any) -> Any:
        if isinstance(e.message, ToolResultMessage):
            return MessageEndEventResult(
                message=ToolResultMessage(
                    tool_call_id=e.message.tool_call_id,
                    content=[TextContent(text="REDACTED")],
                    is_error=False,
                )
            )
        return None

    h.hooks.on("message_end", handler)  # type: ignore[arg-type]
    await h.prompt("go")

    entries = await session.get_entries()
    persisted = [
        e.message  # type: ignore[union-attr]
        for e in entries
        if getattr(e, "type", None) == "message"
    ]
    persisted_results = [
        m for m in persisted if isinstance(m, ToolResultMessage)
    ]
    assert len(persisted_results) == 2
    assert [_text(m) for m in persisted_results] == ["REDACTED", "REDACTED"]

    # In-memory state must agree with the persisted session.
    state_results = [
        m for m in h.state.messages if isinstance(m, ToolResultMessage)
    ]
    assert [_text(m) for m in state_results] == ["REDACTED", "REDACTED"]


# === 5. User-prompt message_end replacement (prompt site, LENS D case (a)) ===


async def test_user_prompt_replacement_flows_into_state_and_persisted() -> None:
    """A ``message_end`` handler that replaces the USER prompt message (same
    role) must land in ``prompt()`` return, ``state.messages``, AND the
    persisted session entry — pins the prompt-site dual-write at
    ``loop.py`` ``agent_loop`` (``new_messages[i]`` + ``current_context.messages``)."""

    session = Session(MemorySessionStorage())
    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_stream("original"), session=session)
    )

    redacted_user = UserMessage(content=[TextContent(text="REDACTED_USER")])

    async def handler(e: Any, _c: Any) -> Any:
        # Only replace the user prompt, leave the assistant reply alone.
        if isinstance(e.message, UserMessage):
            return MessageEndEventResult(message=redacted_user)
        return None

    h.hooks.on("message_end", handler)  # type: ignore[arg-type]
    out = await h.prompt("hi")

    # prompt() return carries the replacement object identity.
    user_out = [m for m in out if isinstance(m, UserMessage)]
    assert len(user_out) == 1
    assert user_out[0] is redacted_user

    # state.messages carries the replacement; the original "hi" is gone.
    state_users = [m for m in h.state.messages if isinstance(m, UserMessage)]
    assert state_users[-1] is redacted_user
    assert _text(state_users[-1]) == "REDACTED_USER"
    assert all(_text(m) != "hi" for m in state_users)

    # Persisted session entry is the replacement, not the original.
    entries = await session.get_entries()
    persisted = [
        e.message  # type: ignore[union-attr]
        for e in entries
        if getattr(e, "type", None) == "message"
    ]
    persisted_users = [m for m in persisted if isinstance(m, UserMessage)]
    assert _text(persisted_users[0]) == "REDACTED_USER"


# === 6. Steering / follow-up message_end replacement (steering site) =========


async def test_steer_message_replacement_flows_into_state() -> None:
    """A ``message_end`` handler that replaces a STEERED user message (same
    role) must land in ``state.messages`` — pins the pending-messages drain
    swap at ``loop.py`` ``_run_loop`` (lines 193-207). A pre-enqueued steer is
    drained at loop start, so a single-turn mock exercises the site."""

    session = Session(MemorySessionStorage())
    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_stream("original"), session=session)
    )

    redacted_steer = UserMessage(content=[TextContent(text="REDACTED_STEER")])

    async def handler(e: Any, _c: Any) -> Any:
        if (
            isinstance(e.message, UserMessage)
            and _text(e.message) == "STEERTEXT"
        ):
            return MessageEndEventResult(message=redacted_steer)
        return None

    h.hooks.on("message_end", handler)  # type: ignore[arg-type]
    # Enqueue BEFORE prompt: the steering queue drains at loop start, so the
    # steered message is consumed via the pending_messages path.
    await h.steer("STEERTEXT")
    await h.prompt("hi")

    state_users = [m for m in h.state.messages if isinstance(m, UserMessage)]
    # The steered replacement landed by identity; the raw "STEERTEXT" is gone.
    assert any(m is redacted_steer for m in state_users)
    assert all(_text(m) != "STEERTEXT" for m in state_users)

    # Persisted too.
    entries = await session.get_entries()
    persisted = [
        e.message  # type: ignore[union-attr]
        for e in entries
        if getattr(e, "type", None) == "message"
    ]
    persisted_users = [m for m in persisted if isinstance(m, UserMessage)]
    assert any(_text(m) == "REDACTED_STEER" for m in persisted_users)
    assert all(_text(m) != "STEERTEXT" for m in persisted_users)
