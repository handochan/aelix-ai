"""P0 #6 — compaction fidelity (pi-parity).

Deterministic tests for the entry-level cut-point machinery, split-turn
(turn-prefix) summarization, file-operation extraction (``<read-files>`` /
``<modified-files>`` tags), the ``details`` round-trip, prev-compaction
file-op seeding, and the ``max_tokens`` caps.

No real LLM calls — ``compact`` is exercised through a monkeypatched
``stream_simple`` (patched on the module OBJECT, reload-safe) so the split-turn
gather + file-op append path runs end-to-end deterministically.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_agent_core.session.compaction import (
    RESERVE_TOKENS,
    CompactionPreparation,
    FileOperations,
    _summary_max_tokens,
    compact,
    compute_file_lists,
    create_file_ops,
    extract_file_ops_from_message,
    find_cut_point,
    find_turn_start_index,
    find_valid_cut_points,
    format_file_operations,
    prepare_compaction,
)
from aelix_agent_core.session.entries import (
    CompactionEntry,
    MessageEntry,
)
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantDoneEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
)

# ``asyncio_mode = "auto"`` (pyproject.toml) auto-detects async tests.

_BIG = "y" * 30_000  # ~7500 tokens via estimate_tokens (chars // 4)


def _msg_entry(eid: str, parent: str | None, message: Any) -> MessageEntry:
    return MessageEntry(id=eid, parent_id=parent, timestamp="2026-01-01T00:00:00Z", message=message)


def _user(text: str = "u") -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant_text(text: str = "a") -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text=text)])


def _assistant_tool(name: str, path: str, *, call_id: str = "c") -> AssistantMessage:
    return AssistantMessage(
        content=[ToolCallContent(tool_call_id=call_id, tool_name=name, input={"path": path})]
    )


def _tool_result(call_id: str = "c", text: str = "ok") -> ToolResultMessage:
    return ToolResultMessage(tool_call_id=call_id, content=[TextContent(text=text)])


async def _branch_with(messages: list[Any]) -> list[Any]:
    session = Session(MemorySessionStorage())
    for msg in messages:
        await session.append_message(msg)
    return await session.get_branch()


# === find_valid_cut_points ==================================================


def test_valid_cut_points_excludes_tool_result() -> None:
    entries = [
        _msg_entry("e0", None, _user()),  # 0 — user (cut point)
        _msg_entry("e1", "e0", _assistant_tool("read", "a.py")),  # 1 — assistant (cut point)
        _msg_entry("e2", "e1", _tool_result()),  # 2 — toolResult (NOT a cut point)
        _msg_entry("e3", "e2", _assistant_text()),  # 3 — assistant (cut point)
    ]
    cut_points = find_valid_cut_points(entries, 0, len(entries))
    assert cut_points == [0, 1, 3]  # index 2 (toolResult) excluded


# === find_turn_start_index ==================================================


def test_turn_start_walks_back_to_user() -> None:
    entries = [
        _msg_entry("e0", None, _user()),  # 0 — turn start
        _msg_entry("e1", "e0", _assistant_tool("read", "a.py")),  # 1
        _msg_entry("e2", "e1", _tool_result()),  # 2
        _msg_entry("e3", "e2", _assistant_text()),  # 3
    ]
    # From entry 3, the turn starts at the user message (index 0).
    assert find_turn_start_index(entries, 3, 0) == 0


def test_turn_start_returns_minus_one_when_no_user() -> None:
    entries = [
        _msg_entry("e0", None, _assistant_text()),  # only assistants
        _msg_entry("e1", "e0", _assistant_text()),
    ]
    assert find_turn_start_index(entries, 1, 0) == -1


# === find_cut_point =========================================================


def test_cut_point_no_valid_points_returns_start() -> None:
    # All toolResult entries → no valid cut points → cut at start, not split.
    entries = [
        _msg_entry("e0", None, _tool_result()),
        _msg_entry("e1", "e0", _tool_result()),
    ]
    result = find_cut_point(entries, 0, len(entries), 20_000)
    assert result.first_kept_entry_index == 0
    assert result.is_split_turn is False
    assert result.turn_start_index == -1


def test_cut_point_user_message_not_split() -> None:
    # A big tail that crosses the budget exactly at a user message → not split.
    entries = [
        _msg_entry("e0", None, _user(_BIG)),  # 0 — summarized
        _msg_entry("e1", "e0", _user(_BIG)),  # 1
        _msg_entry("e2", "e1", _user(_BIG)),  # 2
        _msg_entry("e3", "e2", _user(_BIG)),  # 3
    ]
    result = find_cut_point(entries, 0, len(entries), 20_000)
    cut = entries[result.first_kept_entry_index]
    assert cut.message.role == "user"
    assert result.is_split_turn is False


def test_cut_point_mid_turn_is_split() -> None:
    # Cut lands on an assistant message mid-turn (after the opening user) → split.
    # Sizes: assistant tail crosses the 20k budget so the cut snaps onto an
    # assistant entry whose enclosing turn starts at e0 (user).
    entries = [
        _msg_entry("e0", None, _user("x" * 4_000)),  # 0 — turn start
        _msg_entry("e1", "e0", _assistant_text(_BIG)),  # 1
        _msg_entry("e2", "e1", _assistant_text(_BIG)),  # 2
        _msg_entry("e3", "e2", _assistant_text(_BIG)),  # 3
    ]
    result = find_cut_point(entries, 0, len(entries), 20_000)
    cut = entries[result.first_kept_entry_index]
    assert cut.message.role == "assistant"
    assert result.is_split_turn is True
    assert result.turn_start_index == 0  # the user msg that began the turn


# === file-op extraction =====================================================


def test_extract_file_ops_from_message() -> None:
    ops = create_file_ops()
    extract_file_ops_from_message(_assistant_tool("read", "r.py"), ops)
    extract_file_ops_from_message(_assistant_tool("write", "w.py"), ops)
    extract_file_ops_from_message(_assistant_tool("edit", "e.py"), ops)
    # A non-assistant message must be ignored.
    extract_file_ops_from_message(_user("ignored"), ops)
    assert ops.read == {"r.py"}
    assert ops.written == {"w.py"}
    assert ops.edited == {"e.py"}


def test_compute_file_lists_dedup_and_sort() -> None:
    ops = FileOperations(
        read={"b.py", "a.py", "shared.py"},
        written={"z.py"},
        edited={"shared.py", "m.py"},
    )
    read_files, modified_files = compute_file_lists(ops)
    # shared.py was both read AND edited → reported only as modified.
    assert read_files == ["a.py", "b.py"]
    assert modified_files == ["m.py", "shared.py", "z.py"]


def test_format_file_operations_tags() -> None:
    out = format_file_operations(["a.py", "b.py"], ["c.py"])
    assert out == (
        "\n\n<read-files>\na.py\nb.py\n</read-files>"
        "\n\n<modified-files>\nc.py\n</modified-files>"
    )


def test_format_file_operations_empty() -> None:
    assert format_file_operations([], []) == ""


# === compact (monkeypatched stream) — split-turn + file-op append ==========


def _patch_stream(monkeypatch: pytest.MonkeyPatch, text: str) -> list[SimpleStreamOptions]:
    """Patch ``stream_simple`` on the module OBJECT (reload-safe) so compact()
    runs without a real provider. Returns a list capturing the options each
    call received (so the max_tokens cap can be asserted)."""

    captured: list[SimpleStreamOptions] = []

    async def _events() -> AsyncIterator[Any]:
        yield TextDeltaEvent(delta=text)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text=text)], stop_reason="end_turn"
            ),
        )

    async def fake_stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[Any]:
        # ``compaction`` does ``await stream_simple(...)`` (the real fn is a
        # coroutine returning an async iterator), so this fake must be a
        # coroutine returning the generator — not a bare async generator.
        captured.append(options)
        return _events()

    import aelix_ai.streaming as streaming_mod

    monkeypatch.setattr(streaming_mod, "stream_simple", fake_stream)
    return captured


async def test_compact_split_turn_combines_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_stream(monkeypatch, "SUMMARY_TEXT")
    model = Model(api="openai-completions", id="m", provider="openai")

    file_ops = FileOperations(read={"r.py"}, edited={"m.py"})
    prep = CompactionPreparation(
        first_kept_entry_id="k",
        messages_to_summarize=[_user("history")],
        turn_prefix_messages=[_assistant_text("prefix")],
        is_split_turn=True,
        tokens_before=100,
        file_ops=file_ops,
    )

    result = await compact(model, lambda _m: {"apiKey": "k"}, prep)

    # Two LLM calls fired (history + turn-prefix) for the split turn.
    assert len(captured) == 2
    # The verbatim split-turn separator joins the two summaries.
    assert "**Turn Context (split turn):**" in result.summary
    assert "\n\n---\n\n" in result.summary
    # File-op tail appended, with read/modified separated and the details set.
    assert "<read-files>\nr.py\n</read-files>" in result.summary
    assert "<modified-files>\nm.py\n</modified-files>" in result.summary
    assert result.details == {"readFiles": ["r.py"], "modifiedFiles": ["m.py"]}


async def test_compact_split_turn_empty_history_uses_no_prior_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_stream(monkeypatch, "PREFIX_SUMMARY")
    model = Model(api="openai-completions", id="m", provider="openai")
    prep = CompactionPreparation(
        first_kept_entry_id="k",
        messages_to_summarize=[],  # empty history
        turn_prefix_messages=[_assistant_text("prefix")],
        is_split_turn=True,
        tokens_before=10,
    )
    result = await compact(model, lambda _m: {"apiKey": "k"}, prep)
    # Only the turn-prefix call fires (history substituted by the constant).
    assert len(captured) == 1
    assert result.summary.startswith("No prior history.")
    assert "PREFIX_SUMMARY" in result.summary


async def test_compact_max_tokens_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_stream(monkeypatch, "S")
    model = Model(api="openai-completions", id="m", provider="openai")  # max_tokens=0
    prep = CompactionPreparation(
        first_kept_entry_id="k",
        messages_to_summarize=[_user("history")],
        turn_prefix_messages=[_assistant_text("prefix")],
        is_split_turn=True,
        tokens_before=10,
    )
    await compact(model, lambda _m: {"apiKey": "k"}, prep)
    # History cap = floor(0.8 * RESERVE_TOKENS); turn-prefix = floor(0.5 * ...).
    caps = sorted(o.max_tokens for o in captured)
    assert caps == [
        int(0.5 * RESERVE_TOKENS),
        int(0.8 * RESERVE_TOKENS),
    ]


def test_summary_max_tokens_clamped_by_model() -> None:
    # model.max_tokens > 0 clamps the floor-of-reserve cap.
    small = Model(api="x", id="m", provider="p", max_tokens=1000)
    assert _summary_max_tokens(small, 0.8) == 1000
    # model.max_tokens == 0 → no model clamp → floor(fraction * reserve).
    unbounded = Model(api="x", id="m", provider="p", max_tokens=0)
    assert _summary_max_tokens(unbounded, 0.8) == int(0.8 * RESERVE_TOKENS)
    assert _summary_max_tokens(unbounded, 0.5) == int(0.5 * RESERVE_TOKENS)


# === prepare_compaction — prev-compaction file-op seeding ===================


async def test_prepare_compaction_seeds_file_ops_from_prev_compaction() -> None:
    # First message large enough to summarize; a prior compaction entry carries
    # details that must seed the new file-op sets (pi extractFileOperations).
    session = Session(MemorySessionStorage())
    big = "x" * 30_000
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text=big)]))
    branch = await session.get_branch()

    # Splice a prior (non-hook) compaction entry with details near the head so
    # boundary_start picks up its first_kept and details seed the file ops.
    first_kept_id = branch[1].id
    prev = CompactionEntry(
        id="prev-compaction",
        parent_id=branch[0].id,
        timestamp="2026-01-01T00:00:00Z",
        summary="prior summary",
        first_kept_entry_id=first_kept_id,
        tokens_before=999,
        details={"readFiles": ["seeded_read.py"], "modifiedFiles": ["seeded_mod.py"]},
        from_hook=False,
    )
    spliced = [branch[0], prev, *branch[1:]]

    prep = prepare_compaction(spliced)
    assert prep is not None
    assert prep.previous_summary == "prior summary"
    assert isinstance(prep.file_ops, FileOperations)
    # The seeded read goes into read; the seeded modified goes into edited.
    assert "seeded_read.py" in prep.file_ops.read
    assert "seeded_mod.py" in prep.file_ops.edited


async def test_prepare_compaction_details_roundtrip_via_session() -> None:
    """Full path: prepare → compact (override) → append_compaction persists
    details → re-read keeps the camelCase readFiles/modifiedFiles."""

    from aelix_agent_core.session.entries import entry_from_json, entry_to_json

    entry = CompactionEntry(
        id="c1",
        parent_id="p0",
        timestamp="2026-01-01T00:00:00Z",
        summary="s",
        first_kept_entry_id="k",
        tokens_before=1,
        details={"readFiles": ["a.py"], "modifiedFiles": ["b.py"]},
    )
    wire = entry_to_json(entry)
    assert wire["details"] == {"readFiles": ["a.py"], "modifiedFiles": ["b.py"]}
    back = entry_from_json(wire)
    assert back.details == {"readFiles": ["a.py"], "modifiedFiles": ["b.py"]}


async def test_prepare_compaction_split_turn_threads_turn_prefix() -> None:
    # Build a real branch where the cut lands mid-turn: a small opening user
    # message then several large assistant messages so the budget cut snaps
    # onto an assistant entry (split turn).
    msgs: list[Any] = [_user("x" * 4_000)]
    msgs += [_assistant_text(_BIG) for _ in range(4)]
    branch = await _branch_with(msgs)

    prep = prepare_compaction(branch)
    assert prep is not None
    assert prep.is_split_turn is True
    # The turn-prefix messages cover the opening user msg up to the cut.
    assert len(prep.turn_prefix_messages) >= 1


# === previous-summary / UPDATE_SUMMARIZATION_PROMPT summarizer path =========


def _patch_stream_capture_user_text(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> list[str]:
    """Patch ``stream_simple`` capturing each call's user_text (the wrapped
    ``<conversation>`` + prompt block) so the previous-summary threading + the
    UPDATE-prompt swap can be asserted on the actual LLM request."""

    captured: list[str] = []

    async def _events() -> AsyncIterator[Any]:
        yield TextDeltaEvent(delta=text)
        yield AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(
                content=[TextContent(text=text)], stop_reason="end_turn"
            ),
        )

    async def fake_stream(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[Any]:
        captured.append(context.messages[0].content[0].text)
        return _events()

    import aelix_ai.streaming as streaming_mod

    monkeypatch.setattr(streaming_mod, "stream_simple", fake_stream)
    return captured


async def test_compact_previous_summary_uses_update_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When preparation.previous_summary is set, _generate_summary must swap
    # SUMMARIZATION_PROMPT for UPDATE_SUMMARIZATION_PROMPT and inject a
    # <previous-summary> block into the user_text. A mutation that always uses
    # SUMMARIZATION_PROMPT or drops the injection passes every other test.
    user_texts = _patch_stream_capture_user_text(monkeypatch, "UPDATED_SUMMARY")
    model = Model(api="openai-completions", id="m", provider="openai")

    prep = CompactionPreparation(
        first_kept_entry_id="k",
        messages_to_summarize=[_user("new history")],
        is_split_turn=False,
        tokens_before=10,
        previous_summary="PRIOR",
    )
    await compact(model, lambda _m: {"apiKey": "k"}, prep)

    assert len(user_texts) == 1
    text = user_texts[0]
    # The prior summary is threaded into the request inside its own block.
    assert "<previous-summary>\nPRIOR\n</previous-summary>" in text
    # A sentinel unique to UPDATE_SUMMARIZATION_PROMPT (NOT in SUMMARIZATION_PROMPT).
    assert "PRESERVE all existing information" in text


async def test_compact_no_previous_summary_uses_base_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Twin of the UPDATE test: with no previous_summary the base prompt is used
    # and NO <previous-summary> block is injected (proves the swap is conditional).
    user_texts = _patch_stream_capture_user_text(monkeypatch, "FRESH_SUMMARY")
    model = Model(api="openai-completions", id="m", provider="openai")

    prep = CompactionPreparation(
        first_kept_entry_id="k",
        messages_to_summarize=[_user("history")],
        is_split_turn=False,
        tokens_before=10,
    )
    await compact(model, lambda _m: {"apiKey": "k"}, prep)

    assert len(user_texts) == 1
    text = user_texts[0]
    assert "<previous-summary>" not in text
    assert "PRESERVE all existing information" not in text


# === split-turn turn-prefix file-op fold (prepare_compaction) ==============


async def test_prepare_compaction_split_turn_folds_turn_prefix_file_ops() -> None:
    # The split-turn fold (`for msg in turn_prefix_messages:
    # extract_file_ops_from_message(...)`) is otherwise never exercised: every
    # split-turn fixture builds the prefix from text-only assistant messages.
    # Put a real edit tool call in the turn-prefix region and assert it lands in
    # prep.file_ops.edited. A mutation deleting the turn-prefix fold passes
    # without this.
    msgs: list[Any] = [
        _user("x" * 4_000),  # 0 — turn start (small)
        _assistant_tool("edit", "prefix_edit.py"),  # 1 — folded via turn-prefix
    ]
    msgs += [_assistant_text(_BIG) for _ in range(3)]  # 2,3,4 — big tail
    branch = await _branch_with(msgs)

    prep = prepare_compaction(branch)
    assert prep is not None
    assert prep.is_split_turn is True
    # The cut snaps onto a big assistant tail entry; the turn prefix [user, edit]
    # is summarized separately, so the edit must be folded into file_ops.
    assert "prefix_edit.py" in prep.file_ops.edited


async def test_prepare_compaction_folds_history_file_ops_from_messages() -> None:
    # Non-split path: the summarized prefix carries real read/write tool calls,
    # proving _extract_file_operations runs over the prepared messages (not just
    # the unit helper). A file that is both read AND written must end up only in
    # modified (computed later by compute_file_lists, but read-exclusion is
    # observable through prep.file_ops here as separate sets).
    msgs: list[Any] = [
        _user(_BIG),  # 0 — summarized
        _assistant_tool("read", "hist_read.py"),  # 1 — summarized
        _assistant_tool("write", "hist_write.py"),  # 2 — summarized
        _user(_BIG),  # 3 — kept (cut lands here, a user → not split)
        _user(_BIG),  # 4 — kept
        _user(_BIG),  # 5 — kept
    ]
    branch = await _branch_with(msgs)

    prep = prepare_compaction(branch)
    assert prep is not None
    assert prep.is_split_turn is False
    assert "hist_read.py" in prep.file_ops.read
    assert "hist_write.py" in prep.file_ops.written


# === prev-compaction from_hook guard (negative seeding) ====================


async def test_prepare_compaction_does_not_seed_file_ops_from_hook_compaction() -> None:
    # Twin of test_prepare_compaction_seeds_file_ops_from_prev_compaction with
    # from_hook=True: pi seeds file ops ONLY when !prevCompaction.fromHook, so a
    # hook-substituted compaction's details must NOT seed the new sets. A
    # mutation removing the `not from_hook` clause passes every other test.
    session = Session(MemorySessionStorage())
    big = "x" * 30_000
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text=big)]))
    branch = await session.get_branch()

    first_kept_id = branch[1].id
    prev = CompactionEntry(
        id="prev-compaction",
        parent_id=branch[0].id,
        timestamp="2026-01-01T00:00:00Z",
        summary="prior summary",
        first_kept_entry_id=first_kept_id,
        tokens_before=999,
        details={"readFiles": ["seeded_read.py"], "modifiedFiles": ["seeded_mod.py"]},
        from_hook=True,  # hook-substituted → details must NOT be trusted as a seed
    )
    spliced = [branch[0], prev, *branch[1:]]

    prep = prepare_compaction(spliced)
    assert prep is not None
    # previous_summary still threads through (that does not depend on from_hook).
    assert prep.previous_summary == "prior summary"
    # ...but the file-op seed is NOT applied for a from_hook compaction.
    assert "seeded_read.py" not in prep.file_ops.read
    assert "seeded_mod.py" not in prep.file_ops.edited
