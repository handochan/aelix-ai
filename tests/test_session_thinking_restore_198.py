"""Issue #198 — the pure helper both resume seams share.

``build_session_context`` already folds ``thinking_level_change`` last-wins into
``SessionContext.thinking_level``, but it cannot answer the question a resume
asks: *was a level ever recorded?* Its initial value is ``"off"``, so "the user
chose off" and "nobody ever chose" are the same answer.
:func:`resolve_resumed_thinking_level` separates them (``None`` = nothing to
restore) and clamps what it finds to the resumed model.
"""

from __future__ import annotations

import pytest
from aelix_agent_core.session.context import resolve_resumed_thinking_level
from aelix_agent_core.session.entries import (
    MessageEntry,
    ThinkingLevelChangeEntry,
    entry_from_json,
)
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.streaming import Model


def _level(id: str, level: str, parent: str | None) -> ThinkingLevelChangeEntry:
    return ThinkingLevelChangeEntry(
        id=id,
        parent_id=parent,
        timestamp="2026-09-08T00:00:00.000Z",
        thinking_level=level,
    )


def _message(id: str, parent: str | None) -> MessageEntry:
    return MessageEntry(
        id=id,
        parent_id=parent,
        timestamp="2026-09-08T00:00:00.000Z",
        message=UserMessage(content=[TextContent(text="hi")]),
    )


def _reasoning_model(**kwargs: object) -> Model:
    return Model(
        id="m",
        api="anthropic",
        reasoning=True,
        thinking_level_map={"low": 2048, "medium": 8192, "high": 16384},
        **kwargs,  # type: ignore[arg-type]
    )


def test_returns_fallback_when_no_entry() -> None:
    entries = [_message("a", None)]

    assert resolve_resumed_thinking_level(entries, None) is None
    assert resolve_resumed_thinking_level(entries, None, fallback="medium") == "medium"


def test_last_entry_wins() -> None:
    entries = [
        _level("a", "high", None),
        _message("b", "a"),
        _level("c", "low", "b"),
    ]

    assert resolve_resumed_thinking_level(entries, None) == "low"


def test_explicit_off_is_restored_not_treated_as_absent() -> None:
    """An entry is a decision. A session last set to ``off`` comes back ``off``
    — it does NOT fall through to the caller's fallback, which is what makes
    ``--thinking off`` and a deliberate ``/thinking off`` survive a relaunch."""

    entries = [_level("a", "high", None), _level("b", "off", "a")]

    assert resolve_resumed_thinking_level(entries, None, fallback="medium") == "off"


def test_unsupported_level_clamps_not_drops() -> None:
    """A level the resumed model cannot do is clamped, never dropped.

    ``xhigh`` is the last of ``EXTENDED_THINKING_LEVELS``, so the forward scan
    inside ``clamp_thinking_level`` finds nothing and the backward one lands on
    ``high`` — the closest thing the model can actually do.
    """

    entries = [_level("a", "xhigh", None)]

    assert resolve_resumed_thinking_level(entries, _reasoning_model()) == "high"


def test_non_reasoning_model_collapses_to_off() -> None:
    entries = [_level("a", "high", None)]
    model = Model(id="m", api="anthropic", reasoning=False)

    assert resolve_resumed_thinking_level(entries, model) == "off"


def test_fallback_is_clamped_too() -> None:
    """The carried-forward level goes through the same clamp: resuming into an
    entry-less session on a non-reasoning model must not carry ``high`` in."""

    entries = [_message("a", None)]
    model = Model(id="m", api="anthropic", reasoning=False)

    assert resolve_resumed_thinking_level(entries, model, fallback="high") == "off"


def test_a_level_entry_without_a_level_never_reaches_the_helper() -> None:
    """The helper reads ``entry.thinking_level`` behind a
    ``# type: ignore[union-attr]`` — the union narrows by ``type``, which the
    checker cannot follow. That read is total only because a
    ``thinking_level_change`` without the field cannot exist: the dataclass
    makes it required, and the JSONL reader raises ``KeyError`` on the wire
    dict long before a branch is folded. This is where a malformed entry is
    caught, so nothing downstream has to defend against one.
    """

    wire = {
        "type": "thinking_level_change",
        "id": "e1",
        "parentId": None,
        "timestamp": "2026-09-08T00:00:00.000Z",
    }

    with pytest.raises(KeyError):
        entry_from_json(wire)
