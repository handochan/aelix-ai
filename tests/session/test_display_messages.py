"""Issue #62 (ADR-0183) — display-tier context derivation tests.

``build_display_messages`` / ``select_display_entries`` / ``CustomMessage``:
the rich display tier preserves ``custom_type``/``display``/``details`` for
TUI renderer dispatch over the SAME compaction boundary as the LLM tier,
while ``build_session_context`` (the LLM tier) stays byte-identical
(``custom_message`` flattened to ``UserMessage``).
"""

from __future__ import annotations

from aelix_agent_core.session.context import (
    BRANCH_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_PREFIX,
    CustomMessage,
    build_display_messages,
    build_session_context,
    select_display_entries,
)
from aelix_agent_core.session.entries import (
    BranchSummaryEntry,
    CompactionEntry,
    CustomMessageEntry,
    LabelEntry,
    MessageEntry,
    ThinkingLevelChangeEntry,
)
from aelix_ai.messages import TextContent, UserMessage

_TS = "2026-07-04T00:00:00Z"


def _msg(id_: str, text: str, parent: str | None = None) -> MessageEntry:
    return MessageEntry(
        id=id_,
        parent_id=parent,
        timestamp=_TS,
        message=UserMessage(content=[TextContent(text=text)]),
    )


def _custom(
    id_: str,
    *,
    ctype: str = "status",
    content: object = "hello",
    display: bool = True,
    details: object = None,
) -> CustomMessageEntry:
    return CustomMessageEntry(
        id=id_,
        parent_id=None,
        timestamp=_TS,
        custom_type=ctype,
        content=content,
        display=display,
        details=details,
    )


def test_display_tier_preserves_custom_fields() -> None:
    out = build_display_messages(
        [_msg("1", "hi"), _custom("2", display=False, details={"k": 1})]
    )
    assert len(out) == 2
    custom = out[1]
    assert isinstance(custom, CustomMessage)
    assert custom.role == "custom"
    assert custom.custom_type == "status"
    assert custom.display is False  # preserved — the RENDERER gates on it
    assert custom.details == {"k": 1}
    assert custom.content == "hello"


def test_llm_tier_still_flattens_custom_to_user_message() -> None:
    """Regression pin: the LLM tier is untouched by the display-tier split."""
    (msg,) = build_session_context([_custom("1")]).messages
    assert isinstance(msg, UserMessage)
    assert msg.content[0].text == "hello"


def test_display_and_llm_tiers_share_compaction_boundary() -> None:
    entries = [
        _msg("1", "dropped-prefix"),
        _msg("2", "kept-pre"),
        _custom("3", ctype="pre-custom"),
        CompactionEntry(
            id="4",
            parent_id="3",
            timestamp=_TS,
            summary="the summary",
            first_kept_entry_id="2",
            tokens_before=100,
        ),
        _msg("5", "post"),
    ]
    llm = build_session_context(entries).messages
    display = build_display_messages(entries)
    # summary + kept-pre + pre-custom + post, in identical order on both tiers.
    assert len(llm) == len(display) == 4
    assert llm[0].content[0].text.startswith(COMPACTION_SUMMARY_PREFIX)
    assert display[0].content[0].text.startswith(COMPACTION_SUMMARY_PREFIX)
    assert isinstance(display[2], CustomMessage)  # surviving custom stays rich
    assert isinstance(llm[2], UserMessage)  # …and stays flattened for the LLM
    for messages in (llm, display):
        assert not any(
            "dropped-prefix" in str(getattr(m, "content", "")) for m in messages
        )


def test_select_display_entries_without_compaction_passes_through() -> None:
    entries = [
        _msg("1", "a"),
        ThinkingLevelChangeEntry(
            id="2", parent_id="1", timestamp=_TS, thinking_level="high"
        ),
        _custom("3"),
    ]
    assert select_display_entries(entries) == entries


def test_state_only_entries_produce_no_display_messages() -> None:
    out = build_display_messages(
        [
            ThinkingLevelChangeEntry(
                id="1", parent_id=None, timestamp=_TS, thinking_level="high"
            ),
            LabelEntry(id="2", parent_id="1", timestamp=_TS, target_id="1", label="x"),
        ]
    )
    assert out == []


def test_branch_summary_wrapped_and_empty_skipped() -> None:
    out = build_display_messages(
        [
            BranchSummaryEntry(
                id="1", parent_id=None, timestamp=_TS, from_id="0", summary="took a detour"
            ),
            BranchSummaryEntry(
                id="2", parent_id="1", timestamp=_TS, from_id="0", summary=""
            ),
        ]
    )
    (msg,) = out
    assert isinstance(msg, UserMessage)  # summaries stay LLM-shaped on both tiers
    assert msg.content[0].text.startswith(BRANCH_SUMMARY_PREFIX)
    assert "took a detour" in msg.content[0].text


def _old_build_messages(path_entries: list) -> list:
    """Reference reimplementation of the PRE-#62 build_session_context boundary
    (git show HEAD~:...context.py) — the golden the refactor must match."""
    from aelix_agent_core.session.context import (
        create_branch_summary_message,
        create_compaction_summary_message,
        create_custom_message,
    )

    compaction = None
    for entry in path_entries:
        if entry.type == "compaction":
            compaction = entry

    messages: list = []

    def _append(entry: object) -> None:
        if entry.type == "message":
            messages.append(entry.message)
        elif entry.type == "custom_message":
            messages.append(
                create_custom_message(
                    entry.custom_type, entry.content, entry.display,
                    entry.details, entry.timestamp,
                )
            )
        elif entry.type == "branch_summary" and entry.summary:
            messages.append(
                create_branch_summary_message(
                    entry.summary, entry.from_id, entry.timestamp
                )
            )

    if compaction is not None:
        messages.append(
            create_compaction_summary_message(
                compaction.summary, compaction.tokens_before, compaction.timestamp
            )
        )
        cidx = next(
            (i for i, e in enumerate(path_entries)
             if e.type == "compaction" and e.id == compaction.id), -1
        )
        found = False
        for i in range(cidx):
            entry = path_entries[i]
            if entry.id == compaction.first_kept_entry_id:
                found = True
            if found:
                _append(entry)
        for i in range(cidx + 1, len(path_entries)):
            _append(path_entries[i])
    else:
        for entry in path_entries:
            _append(entry)
    return messages


def _texts(messages: list) -> list:
    out = []
    for m in messages:
        c = getattr(m, "content", None)
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.append("|".join(getattr(b, "text", "?") for b in c))
        else:
            out.append(str(c))
    return out


def test_build_session_context_byte_identical_to_pre_refactor() -> None:
    """Golden: the LLM tier is unchanged by the display-tier split, incl. the
    multi-compaction / missing-first-kept / compaction-first edges."""
    from aelix_agent_core.session.context import build_session_context

    scenarios = [
        # plain, no compaction
        [_msg("1", "a"), _custom("2"), _msg("3", "b")],
        # single compaction, normal boundary
        [
            _msg("1", "drop"), _msg("2", "keep"),
            CompactionEntry(id="3", parent_id="2", timestamp=_TS, summary="s",
                            first_kept_entry_id="2", tokens_before=1),
            _msg("4", "post"),
        ],
        # TWO compactions — last one wins; the earlier compaction entry must be
        # dropped both pre- and post-refactor (it sat before the chosen one).
        [
            _msg("1", "x"),
            CompactionEntry(id="2", parent_id="1", timestamp=_TS, summary="s1",
                            first_kept_entry_id="1", tokens_before=1),
            _msg("3", "mid"),
            CompactionEntry(id="4", parent_id="3", timestamp=_TS, summary="s2",
                            first_kept_entry_id="3", tokens_before=2),
            _msg("5", "post"),
        ],
        # first_kept_entry_id matches NOTHING → nothing pre-boundary kept.
        [
            _msg("1", "a"), _msg("2", "b"),
            CompactionEntry(id="3", parent_id="2", timestamp=_TS, summary="s",
                            first_kept_entry_id="ZZZ", tokens_before=1),
            _msg("4", "post"),
        ],
        # compaction is the FIRST entry.
        [
            CompactionEntry(id="1", parent_id=None, timestamp=_TS, summary="s",
                            first_kept_entry_id="1", tokens_before=1),
            _msg("2", "post"),
        ],
    ]
    for entries in scenarios:
        got = build_session_context(entries).messages
        want = _old_build_messages(entries)
        assert _texts(got) == _texts(want), f"mismatch for {entries!r}"
        assert [type(m).__name__ for m in got] == [type(m).__name__ for m in want]
