"""Sprint 6h₁₂-compaction — LLM summarizer + real findCutPoint port.

Covers the conversation-compaction work that wires ``/compact`` to a real
provider call (ADR-0038 follow-up):

- :func:`prepare_compaction` now runs the backward token-budget walk,
  populates a non-empty ``messages_to_summarize`` when there is enough
  content, maps ``first_kept_entry_id`` to the real cut entry (not the head),
  returns ``None`` below ``KEEP_RECENT_TOKENS``, and never leaves a
  :class:`ToolResultMessage` as the first kept message.
- :func:`_serialize_conversation` renders user/assistant/tool-result turns,
  annotates errored tool results, truncates with "… (truncated)" suffix.
- :func:`compact` streams a summary from a registered (fake) provider and
  raises :class:`AgentHarnessError` on :class:`AssistantErrorEvent`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarnessError
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.session.compaction import (
    KEEP_RECENT_TOKENS,
    TOOL_RESULT_MAX_CHARS,
    CompactionPreparation,
    _serialize_conversation,
    compact,
    prepare_compaction,
)
from aelix_ai import clear_providers, register_provider
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
)

# ``asyncio_mode = "auto"`` (pyproject.toml) auto-detects async tests, so no
# per-test asyncio marker is needed here.


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_providers()
    yield
    clear_providers()


_BIG = "y" * 30_000  # ~7500 tokens via estimate_tokens (chars // 4)


async def _branch_with(messages: list[Any]) -> list[Any]:
    """Build a real branch of SessionTreeEntry objects from raw messages."""

    session = Session(MemorySessionStorage())
    for msg in messages:
        await session.append_message(msg)
    return await session.get_branch()


# === prepare_compaction =====================================================


async def test_prepare_compaction_populates_messages_to_summarize() -> None:
    # Six large messages — older ones exceed the keep-recent budget, so a
    # non-empty summarize prefix must exist.
    msgs = [UserMessage(content=[TextContent(text=_BIG)]) for _ in range(6)]
    branch = await _branch_with(msgs)

    prep = prepare_compaction(branch)
    assert prep is not None
    assert len(prep.messages_to_summarize) >= 1
    # The summarized prefix is strictly smaller than the whole conversation
    # (the recent tail is kept).
    assert len(prep.messages_to_summarize) < len(msgs)
    assert prep.tokens_before > 0


async def test_prepare_compaction_returns_none_below_threshold() -> None:
    # A tiny conversation is well below KEEP_RECENT_TOKENS — nothing to compact.
    branch = await _branch_with(
        [UserMessage(content=[TextContent(text="hi there")])]
    )
    assert prepare_compaction(branch) is None
    # Sanity: the seed is far under the budget.
    assert KEEP_RECENT_TOKENS > 1000


async def test_prepare_compaction_first_kept_entry_id_is_cut_entry() -> None:
    """first_kept_entry_id must point to the cut entry, not the head entry.

    build_session_context uses first_kept_entry_id to decide which entries
    to include after a compaction — if it pointed to the head, nothing would
    be dropped and context would never shrink.
    """

    msgs = [UserMessage(content=[TextContent(text=_BIG)]) for _ in range(6)]
    session = Session(MemorySessionStorage())
    for msg in msgs:
        await session.append_message(msg)
    branch = await session.get_branch()

    prep = prepare_compaction(branch)
    assert prep is not None

    # Collect the ids of all message-bearing entries (same order as the pairs
    # build in prepare_compaction).
    message_entry_ids = [e.id for e in branch if e.type == "message"]
    cut = len(prep.messages_to_summarize)
    # The first KEPT entry id must be the entry at `cut`, not entry[0].
    expected_first_kept_id = message_entry_ids[cut]
    assert prep.first_kept_entry_id == expected_first_kept_id
    assert prep.first_kept_entry_id != message_entry_ids[0], (
        "first_kept_entry_id must NOT be the head entry"
    )


async def test_prepare_compaction_never_cuts_on_tool_result() -> None:
    # Sizes chosen so the backward token walk first crosses KEEP_RECENT_TOKENS
    # (20000) *at* the ToolResultMessage (index 2): the impl must back the cut
    # up to the preceding tool-call message so the tool result stays kept with
    # its call.
    #   idx4 (~5000) + idx3 (~5000) + idx2 (~12500) = 22500 >= 20000 at idx2.
    tail = "z" * 20_000  # ~5000 tokens
    big_result = "r" * 50_000  # ~12500 tokens
    msgs: list[Any] = [
        UserMessage(content=[TextContent(text="x" * 20_000)]),  # 0
        AssistantMessage(
            content=[
                ToolCallContent(tool_call_id="c1", tool_name="read", input={"p": "x"})
            ]
        ),  # 1
        ToolResultMessage(
            tool_call_id="c1", content=[TextContent(text=big_result)]
        ),  # 2 — must NOT be first-kept
        AssistantMessage(content=[TextContent(text=tail)]),  # 3
        UserMessage(content=[TextContent(text=tail)]),  # 4
    ]
    branch = await _branch_with(msgs)

    prep = prepare_compaction(branch)
    assert prep is not None
    # Whatever the cut, the first KEPT message (= first message NOT in the
    # summarize prefix) must not be a ToolResultMessage.
    full = [e.message for e in branch if e.type == "message"]
    cut = len(prep.messages_to_summarize)
    assert cut < len(full)
    assert not isinstance(full[cut], ToolResultMessage)


# === _serialize_conversation ================================================


def test_serialize_renders_user_assistant_tool_result() -> None:
    msgs = [
        UserMessage(content=[TextContent(text="hello"), ImageContent(data="b64")]),
        AssistantMessage(
            content=[
                ThinkingContent(thinking="pondering"),
                TextContent(text="hi"),
                ToolCallContent(
                    tool_call_id="c1", tool_name="grep", input={"q": "foo"}
                ),
            ]
        ),
        ToolResultMessage(
            tool_call_id="c1", content=[TextContent(text="match found")]
        ),
    ]
    out = _serialize_conversation(msgs)

    assert "User: hello [image]" in out
    assert "Assistant:" in out
    assert "[thinking] pondering" in out
    assert "[tool_call grep] " in out
    assert '"q": "foo"' in out
    assert "Tool result: match found" in out


def test_serialize_truncates_tool_result() -> None:
    long_text = "a" * (TOOL_RESULT_MAX_CHARS + 5000)
    out = _serialize_conversation(
        [ToolResultMessage(tool_call_id="c1", content=[TextContent(text=long_text)])]
    )
    body = out[len("Tool result: ") :]
    # Body is the truncated text + " … (truncated)" suffix.
    assert body.endswith(" … (truncated)")
    assert body.startswith("a" * TOOL_RESULT_MAX_CHARS)


def test_serialize_error_tool_result_label() -> None:
    out = _serialize_conversation(
        [ToolResultMessage(tool_call_id="c1", content=[TextContent(text="oops")], is_error=True)]
    )
    assert out.startswith("Tool result [error]: oops")


# === compact (real provider stream) =========================================


_CANNED_SUMMARY = "## Goal\nFinish the compaction wiring."


def _register_fake_provider(summary: str = _CANNED_SUMMARY) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[Any]:
        captured["context"] = context
        captured["options"] = options
        # Stream a delta, then the terminal done event with the full text.
        yield TextDeltaEvent(delta=summary)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text=summary)], stop_reason="end_turn"
            ),
        )

    register_provider("openai-completions", fake_stream)
    return captured


async def test_compact_streams_summary_from_provider() -> None:
    captured = _register_fake_provider()
    model = Model(api="openai-completions", id="gpt-4o-mini", provider="openai")

    prep = CompactionPreparation(
        first_kept_entry_id="entry-1",
        messages_to_summarize=[
            UserMessage(content=[TextContent(text="please refactor foo()")]),
            AssistantMessage(content=[TextContent(text="done")]),
        ],
        tokens_before=12_345,
    )

    result = await compact(
        model,
        lambda _m: {"apiKey": "k", "headers": {"x": "1"}},
        prep,
        "focus on the refactor",
    )

    assert result.summary == _CANNED_SUMMARY
    assert result.first_kept_entry_id == "entry-1"
    assert result.tokens_before == 12_345
    assert result.details is None
    # The summarizer saw the conversation wrapped as DATA + the custom focus.
    ctx = captured["context"]
    user_text = ctx.messages[0].content[0].text
    assert "<conversation>" in user_text
    assert "please refactor foo()" in user_text
    assert "Additional focus: focus on the refactor" in user_text
    assert captured["options"].api_key == "k"


async def test_compact_awaitable_auth_resolved() -> None:
    _register_fake_provider("## Goal\nawaitable auth path")
    model = Model(api="openai-completions", id="gpt-4o-mini", provider="openai")

    async def async_auth(_m: Model) -> dict[str, Any]:
        return {"apiKey": "async-key", "headers": {}}

    prep = CompactionPreparation(
        first_kept_entry_id="e2",
        messages_to_summarize=[UserMessage(content=[TextContent(text="x")])],
        tokens_before=7,
    )
    result = await compact(model, async_auth, prep)
    assert result.summary == "## Goal\nawaitable auth path"


async def test_compact_raises_on_assistant_error_event_no_delta() -> None:
    """AssistantErrorEvent with NO preceding deltas must raise, not return empty."""

    async def error_stream(
        _m: Model, _c: Context, _o: SimpleStreamOptions
    ) -> AsyncIterator[Any]:
        yield AssistantErrorEvent(
            reason="error",
            error_message="provider exploded",
        )

    register_provider("openai-completions", error_stream)
    model = Model(api="openai-completions", id="gpt-4o-mini", provider="openai")
    prep = CompactionPreparation(
        first_kept_entry_id="e3",
        messages_to_summarize=[UserMessage(content=[TextContent(text="hi")])],
        tokens_before=1,
    )

    with pytest.raises(AgentHarnessError) as exc:
        await compact(model, lambda _m: {"apiKey": "k"}, prep)
    assert exc.value.code == "compaction"
    assert "provider exploded" in str(exc.value)


async def test_compact_raises_on_assistant_error_event_after_delta() -> None:
    """AssistantErrorEvent after a TextDelta must raise, NOT return partial text."""

    async def partial_then_error(
        _m: Model, _c: Context, _o: SimpleStreamOptions
    ) -> AsyncIterator[Any]:
        yield TextDeltaEvent(delta="partial sum")
        yield AssistantErrorEvent(
            reason="error",
            error_message="stream cut",
        )

    register_provider("openai-completions", partial_then_error)
    model = Model(api="openai-completions", id="gpt-4o-mini", provider="openai")
    prep = CompactionPreparation(
        first_kept_entry_id="e4",
        messages_to_summarize=[UserMessage(content=[TextContent(text="hi")])],
        tokens_before=1,
    )

    with pytest.raises(AgentHarnessError) as exc:
        await compact(model, lambda _m: {"apiKey": "k"}, prep)
    assert exc.value.code == "compaction"
    assert "stream cut" in str(exc.value)
