"""§E.2 — Session concrete class tests (Sprint 4a)."""

from __future__ import annotations

import pytest
from aelix_agent_core.session import (
    MemorySessionStorage,
    Session,
    SessionError,
)
from aelix_ai.messages import TextContent, UserMessage


def _new_session() -> Session:
    return Session(MemorySessionStorage())


async def test_append_message_assigns_id_and_parent() -> None:
    session = _new_session()
    msg = UserMessage(content=[TextContent(text="hi")])
    a_id = await session.append_message(msg)
    b_id = await session.append_message(msg)
    assert a_id != b_id
    entries = await session.get_entries()
    assert [e.parent_id for e in entries] == [None, a_id]


async def test_append_thinking_level_change_round_trip() -> None:
    session = _new_session()
    new_id = await session.append_thinking_level_change("high")
    entry = await session.get_entry(new_id)
    assert entry is not None
    assert entry.type == "thinking_level_change"
    assert entry.thinking_level == "high"  # type: ignore[union-attr]


async def test_append_model_change_round_trip() -> None:
    session = _new_session()
    new_id = await session.append_model_change("anthropic", "claude-x")
    entry = await session.get_entry(new_id)
    assert entry is not None
    assert entry.provider == "anthropic"  # type: ignore[union-attr]
    assert entry.model_id == "claude-x"  # type: ignore[union-attr]


async def test_append_compaction_five_params_p13() -> None:
    """P-13: ``append_compaction`` takes 5 params, not 1."""

    session = _new_session()
    msg_id = await session.append_message(
        UserMessage(content=[TextContent(text="prelude")])
    )
    cid = await session.append_compaction(
        summary="condensed",
        first_kept_entry_id=msg_id,
        tokens_before=42,
        details={"foo": "bar"},
        from_hook=True,
    )
    entry = await session.get_entry(cid)
    assert entry is not None and entry.type == "compaction"
    assert entry.summary == "condensed"  # type: ignore[union-attr]
    assert entry.first_kept_entry_id == msg_id  # type: ignore[union-attr]
    assert entry.tokens_before == 42  # type: ignore[union-attr]
    assert entry.details == {"foo": "bar"}  # type: ignore[union-attr]
    assert entry.from_hook is True  # type: ignore[union-attr]


async def test_append_label_unknown_target_raises_not_found() -> None:
    session = _new_session()
    with pytest.raises(SessionError) as exc:
        await session.append_label("nope", "x")
    assert exc.value.code == "not_found"


async def test_append_label_known_target_writes_entry() -> None:
    session = _new_session()
    msg_id = await session.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    lid = await session.append_label(msg_id, "checkpoint")
    entry = await session.get_entry(lid)
    assert entry is not None
    assert entry.target_id == msg_id  # type: ignore[union-attr]
    assert entry.label == "checkpoint"  # type: ignore[union-attr]


async def test_append_session_name_trims_and_round_trips() -> None:
    session = _new_session()
    await session.append_session_name("  My Session  ")
    assert await session.get_session_name() == "My Session"
    await session.append_session_name("")
    assert await session.get_session_name() is None


async def test_move_to_unknown_raises_not_found() -> None:
    session = _new_session()
    with pytest.raises(SessionError) as exc:
        await session.move_to("nope")
    assert exc.value.code == "not_found"


async def test_move_to_known_updates_leaf_no_summary() -> None:
    session = _new_session()
    msg_id = await session.append_message(
        UserMessage(content=[TextContent(text="hi")])
    )
    # After append, leaf is the message id.
    assert await session.get_leaf_id() == msg_id
    result = await session.move_to(msg_id)
    assert result is None
    assert await session.get_leaf_id() == msg_id


async def test_build_context_empty_returns_defaults() -> None:
    session = _new_session()
    ctx = await session.build_context()
    assert ctx.messages == []
    assert ctx.thinking_level == "off"
    assert ctx.model is None
