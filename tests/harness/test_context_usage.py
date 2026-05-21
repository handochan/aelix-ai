"""Sprint 6h₅c · Phase 4.16 — :meth:`AgentHarness._get_context_usage_safe`
real impl + helpers (P-369).

Pi parity: ``getContextUsage`` (``agent-session.ts:2946-2990``) +
``calculateContextTokens`` / ``estimateTokens`` /
``estimateContextTokens`` (``compaction.ts:135-279``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session.compaction import (
    calculate_context_tokens,
    estimate_context_tokens,
    estimate_tokens,
)
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
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


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _make_harness(
    *,
    model: Model | None = None,
    initial_messages: list[Any] | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=model,
            stream_fn=_stream(),
            initial_messages=initial_messages or [],
        )
    )


async def test_returns_none_when_no_model() -> None:
    """Pi parity: model is :data:`None` → :data:`None`."""

    harness = _make_harness(model=None)
    try:
        assert await harness._get_context_usage_safe() is None
    finally:
        await harness.dispose()


async def test_returns_none_when_context_window_zero() -> None:
    """Pi parity: ``context_window <= 0`` short-circuits to :data:`None`."""

    harness = _make_harness(model=Model(id="mock", provider="mock"))
    try:
        # Default Model.context_window is 0.
        assert await harness._get_context_usage_safe() is None
    finally:
        await harness.dispose()


async def test_returns_estimate_when_no_compaction() -> None:
    """No session bound → heuristic estimate path returns a full triple."""

    msgs = [
        UserMessage(content=[TextContent(text="hello world " * 10)]),
        AssistantMessage(
            content=[TextContent(text="response " * 10)],
            stop_reason="end_turn",
            usage={"total_tokens": 200},
        ),
    ]
    model = Model(id="m", provider="p", context_window=8000)
    harness = _make_harness(model=model, initial_messages=msgs)
    try:
        usage = await harness._get_context_usage_safe()
        assert usage is not None
        assert usage.context_window == 8000
        # 200 / 8000 = 2.5%
        assert usage.tokens == 200
        assert abs(usage.percent - 2.5) < 0.01
    finally:
        await harness.dispose()


async def test_short_circuits_when_compaction_has_no_post_usage(
    tmp_path: Path,
) -> None:
    """A compaction entry with no post-compaction assistant usage →
    :class:`ContextUsage(tokens=None, percent=None)` sentinel.
    """

    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
    from aelix_agent_core.session.session import Session

    fs = LocalFileSystem()
    file_path = str(tmp_path / "ctx.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(tmp_path), session_id="ctx-test"
    )
    session = Session(storage)
    # Seed: append a user message, then a compaction entry, then nothing.
    user_id = await session.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    await session.append_compaction(
        summary="rolled up",
        first_kept_entry_id=user_id,
        tokens_before=100,
    )

    model = Model(id="m", provider="p", context_window=4000)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=model, stream_fn=_stream(), session=session
        )
    )
    try:
        usage = await harness._get_context_usage_safe()
        assert usage is not None
        # Sentinel triple: compaction without post-usage.
        assert usage.tokens is None
        assert usage.percent is None
        assert usage.context_window == 4000
    finally:
        await harness.dispose()


async def test_walks_post_compaction_assistant_usage(
    tmp_path: Path,
) -> None:
    """A compaction with a post-compaction assistant message carrying a
    positive ``usage`` → full :class:`ContextUsage` triple.
    """

    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
    from aelix_agent_core.session.session import Session

    fs = LocalFileSystem()
    file_path = str(tmp_path / "ctx-post.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(tmp_path), session_id="ctx-post"
    )
    session = Session(storage)
    user_id = await session.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    await session.append_compaction(
        summary="rolled",
        first_kept_entry_id=user_id,
        tokens_before=100,
    )
    # Post-compaction assistant message with usage.
    await session.append_message(
        AssistantMessage(
            content=[TextContent(text="post")],
            stop_reason="end_turn",
            usage={"total_tokens": 500},
        )
    )

    model = Model(id="m", provider="p", context_window=4000)
    msgs = [
        UserMessage(content=[TextContent(text="hi")]),
        AssistantMessage(
            content=[TextContent(text="post")],
            stop_reason="end_turn",
            usage={"total_tokens": 500},
        ),
    ]
    harness = AgentHarness(
        AgentHarnessOptions(
            model=model,
            stream_fn=_stream(),
            session=session,
            initial_messages=msgs,
        )
    )
    try:
        usage = await harness._get_context_usage_safe()
        assert usage is not None
        assert usage.tokens == 500
        assert usage.context_window == 4000
        assert abs(usage.percent - 12.5) < 0.01
    finally:
        await harness.dispose()


def test_compaction_helpers_pi_shape() -> None:
    """Pi parity: ``calculateContextTokens`` / ``estimateTokens`` /
    ``estimateContextTokens`` — verify the shape contracts (image=4800
    chars per Pi ``compaction.ts:264``).
    """

    # calculate_context_tokens — total_tokens preferred, snake/camel both read.
    assert calculate_context_tokens(None) == 0
    assert calculate_context_tokens({"total_tokens": 100}) == 100
    assert calculate_context_tokens({"totalTokens": 200}) == 200
    assert calculate_context_tokens(
        {"input_tokens": 10, "output_tokens": 20, "cache_read": 5, "cache_write": 3}
    ) == 38

    # estimate_tokens — chars/4 with ImageContent contributing 4800 chars.
    msg = UserMessage(content=[ImageContent(mime_type="image/png", data="x")])
    assert estimate_tokens(msg) == 4800 // 4  # 1200

    # text path: 4-char buckets.
    msg_text = UserMessage(content=[TextContent(text="a" * 16)])
    assert estimate_tokens(msg_text) == 4

    # tool_call serialization counts JSON length + tool_name.
    msg_tool = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="t", tool_name="echo", input={"k": "v"}
            )
        ]
    )
    expected = (len('{"k": "v"}') + len("echo")) // 4
    assert estimate_tokens(msg_tool) == expected

    # estimate_context_tokens — uses last eligible assistant usage + trailing.
    msgs: list[Any] = [
        UserMessage(content=[TextContent(text="prompt")]),
        AssistantMessage(
            content=[TextContent(text="resp")],
            stop_reason="end_turn",
            usage={"total_tokens": 50},
        ),
    ]
    result = estimate_context_tokens(msgs)
    assert result.tokens == 50  # last assistant usage, no trailing.

    # With trailing messages after the eligible assistant.
    msgs_trailing: list[Any] = [
        UserMessage(content=[TextContent(text="p")]),
        AssistantMessage(
            content=[TextContent(text="r")],
            stop_reason="end_turn",
            usage={"total_tokens": 50},
        ),
        UserMessage(content=[TextContent(text="a" * 16)]),  # +4 tokens
    ]
    assert estimate_context_tokens(msgs_trailing).tokens == 54

    # Aborted/error stop_reasons are skipped during reverse walk.
    msgs_aborted: list[Any] = [
        AssistantMessage(
            content=[TextContent(text="ok")],
            stop_reason="end_turn",
            usage={"total_tokens": 100},
        ),
        AssistantMessage(
            content=[TextContent(text="oops")],
            stop_reason="aborted",
            usage={"total_tokens": 999},
        ),
    ]
    # 'aborted' last → walk back to end_turn=100. The aborted message
    # still appears as a trailing message → +len("oops")//4 = 1 token.
    assert estimate_context_tokens(msgs_aborted).tokens == 101

    # No eligible assistant: sum of all estimate_tokens.
    msgs_no_assistant: list[Any] = [
        UserMessage(content=[TextContent(text="a" * 16)]),
        UserMessage(content=[TextContent(text="b" * 16)]),
    ]
    assert estimate_context_tokens(msgs_no_assistant).tokens == 8


def test_estimate_tokens_counts_thinking_content() -> None:
    """Sprint 6h₅c W4 MEDIUM (ADR-0085) — :class:`ThinkingContent`
    contributes to the token estimate via the explicit branch BEFORE
    the ``hasattr(block, "text")`` catch-all. Without the explicit
    branch, the catch-all reads ``block.text`` which
    :class:`ThinkingContent` lacks → silently zero contribution.
    """

    from aelix_ai.messages import ThinkingContent

    # ``thinking`` field is 32 chars → 8 tokens at the 4-chars-per-token
    # Pi heuristic.
    msg = AssistantMessage(
        content=[ThinkingContent(thinking="a" * 32)]
    )
    assert estimate_tokens(msg) == 8


async def test_extension_context_get_context_usage_returns_real_usage(
    tmp_path: Path,
) -> None:
    """Sprint 6h₅c P-374 W6 MAJOR fix — :meth:`_ExtensionContext.get_context_usage`
    returns a real :class:`ContextUsage` triple when a model is set
    (replaces the Sprint 5a ``return None`` stub). Verifies the sync
    heuristic bridge path is wired through the extension-context surface.
    """

    msgs = [
        UserMessage(content=[TextContent(text="hello " * 8)]),
        AssistantMessage(
            content=[TextContent(text="reply " * 8)],
            stop_reason="end_turn",
            usage={"total_tokens": 120},
        ),
    ]
    model = Model(id="m", provider="p", context_window=2000)
    harness = _make_harness(model=model, initial_messages=msgs)
    try:
        ctx = harness._make_context()
        usage = ctx.get_context_usage()
        assert usage is not None
        assert usage.context_window == 2000
        # Heuristic estimate path uses the last assistant usage
        # (120) as the baseline + trailing chars/4 = 0 here.
        assert usage.tokens == 120
        # 120 / 2000 = 6%
        assert abs(usage.percent - 6.0) < 0.01
    finally:
        await harness.dispose()


async def test_extension_context_get_context_usage_returns_none_without_model(
    tmp_path: Path,
) -> None:
    """Sprint 6h₅c P-374 — model is :data:`None` → bridge returns
    :data:`None` (same short-circuit Pi uses, mirrored through the sync
    heuristic path).
    """

    harness = _make_harness(model=None)
    try:
        ctx = harness._make_context()
        assert ctx.get_context_usage() is None
    finally:
        await harness.dispose()
