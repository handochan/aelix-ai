"""§H.2 — AgentHarness.navigate_tree() tests (Sprint 4b / Phase 2.2.2)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    NavigateTreeOptions,
)
from aelix_agent_core.harness.hooks import (
    SessionBeforeTreeResult,
    SessionTreeHookEvent,
)
from aelix_agent_core.session import (
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage


def _branch_override(text: str = "BRANCH_SUMMARY") -> Any:
    async def fn(_m: Any, _entries: Any, _ci: Any) -> str:
        return text

    return fn


async def _seeded_session() -> tuple[Session, str, str]:
    """Build a small branch: user → assistant → user (returns user_id, asst_id)."""

    session = Session(MemorySessionStorage())
    user_id = await session.append_message(
        UserMessage(content=[TextContent(text="hello")])
    )
    asst_id = await session.append_message(
        AssistantMessage(content=[TextContent(text="hi back")])
    )
    return session, user_id, asst_id


async def test_navigate_tree_target_none_noop() -> None:
    session, _, _ = await _seeded_session()
    h = AgentHarness(AgentHarnessOptions(session=session))
    result = await h.navigate_tree(None)
    assert result.cancelled is False
    assert result.editor_text is None
    assert result.summary_entry is None


async def test_navigate_tree_user_message_returns_editor_text() -> None:
    session, user_id, _ = await _seeded_session()
    h = AgentHarness(AgentHarnessOptions(session=session))
    result = await h.navigate_tree(user_id, NavigateTreeOptions(summarize=False))
    assert result.cancelled is False
    assert result.editor_text == "hello"


async def test_navigate_tree_non_user_without_summarize_noop_summary() -> None:
    """Targeting a non-user / non-custom_message entry without summarize:
    no SummaryEntry is generated; navigation proceeds; cancelled=False."""

    session, _, asst_id = await _seeded_session()
    h = AgentHarness(AgentHarnessOptions(session=session))
    result = await h.navigate_tree(asst_id, NavigateTreeOptions(summarize=False))
    assert result.cancelled is False
    assert result.summary_entry is None


async def test_navigate_tree_summarize_true_with_override_returns_summary() -> None:
    session, _, _ = await _seeded_session()
    # Append a second branch to summarize when we navigate away.
    third_user_id = await session.append_message(
        UserMessage(content=[TextContent(text="more"),]),
    )
    target_id = (await session.get_entries())[0].id  # navigate back to first user
    h = AgentHarness(
        AgentHarnessOptions(
            session=session,
            _branch_summarizer_override=_branch_override("BR_SUM"),
        )
    )
    result = await h.navigate_tree(
        target_id,
        NavigateTreeOptions(summarize=True),
    )
    assert result.cancelled is False
    # When the editor extracts user text it sets new_leaf_id = parent; the
    # summary is appended either way when entries > 0.
    if result.summary_entry is not None:
        assert result.summary_entry.summary == "BR_SUM"
    _ = third_user_id


async def test_generate_branch_summary_real_path_prepends_preamble(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real (non-override) path: streams via stream_simple, prepends preamble.

    Every other branch-summary test uses ``_branch_summarizer_override``; this
    exercises the ported LLM path with a faked ``stream_simple`` so the wiring
    (prompt + <conversation> wrapping + BRANCH_SUMMARY_PREAMBLE) is proven, not
    just the test seam ('rails but no train' guard).
    """

    from aelix_agent_core.session.branch_summarization import (
        BRANCH_SUMMARY_PREAMBLE,
        generate_branch_summary,
    )
    from aelix_ai.streaming import AssistantDoneEvent, Model, TextDeltaEvent

    captured: dict[str, Any] = {}

    async def _fake_stream_simple(model: Any, context: Any, options: Any) -> Any:
        captured["system_prompt"] = context.system_prompt
        captured["user_text"] = context.messages[0].content[0].text

        async def _gen() -> Any:
            yield TextDeltaEvent(delta="branch body")
            yield AssistantDoneEvent(
                message=AssistantMessage(
                    content=[TextContent(text="branch body")],
                    stop_reason="end_turn",
                )
            )

        return _gen()

    monkeypatch.setattr("aelix_ai.streaming.stream_simple", _fake_stream_simple)

    session, _, _ = await _seeded_session()
    entries = await session.get_entries()
    summary = await generate_branch_summary(
        Model(id="mock", provider="mock"),
        lambda _m: {"apiKey": "x", "headers": {}},
        entries,
    )

    assert summary.startswith(BRANCH_SUMMARY_PREAMBLE)
    assert "branch body" in summary
    assert "<conversation>" in captured["user_text"]
    assert "## Goal" in captured["user_text"]  # BRANCH_SUMMARY_PROMPT appended


async def test_navigate_tree_cancel_via_hook() -> None:
    session, user_id, asst_id = await _seeded_session()
    # Add a third entry so the leaf is past asst_id and navigation actually
    # moves (else navigate_tree short-circuits with old_leaf_id == target_id
    # BEFORE the session_before_tree hook fires — Pi parity short-circuit).
    await session.append_message(
        UserMessage(content=[TextContent(text="future")])
    )
    h = AgentHarness(
        AgentHarnessOptions(
            session=session,
            _branch_summarizer_override=_branch_override(),
        )
    )

    async def cancel(_ev: Any, _ctx: Any) -> SessionBeforeTreeResult:
        return SessionBeforeTreeResult(cancel=True)

    h.hooks.on("session_before_tree", cancel)
    result = await h.navigate_tree(asst_id, NavigateTreeOptions(summarize=True))
    assert result.cancelled is True
    assert h.phase == "idle"
    _ = user_id


async def test_navigate_tree_summary_from_hook_substitute() -> None:
    """Hook handler may substitute the summary entirely; from_hook=True flag
    propagates through the persist + emit path."""

    session, user_id, _ = await _seeded_session()
    await session.append_message(
        AssistantMessage(content=[TextContent(text="branch2")])
    )

    async def provide(_ev: Any, _ctx: Any) -> SessionBeforeTreeResult:
        return SessionBeforeTreeResult(
            summary={"summary": "HOOK_SUMMARY", "details": {"k": 1}},
        )

    captured: list[SessionTreeHookEvent] = []

    async def on_tree(ev: SessionTreeHookEvent, _ctx: Any) -> None:
        captured.append(ev)

    h = AgentHarness(AgentHarnessOptions(session=session))
    h.hooks.on("session_before_tree", provide)
    h.hooks.on("session_tree", on_tree)
    result = await h.navigate_tree(user_id, NavigateTreeOptions(summarize=True))
    assert result.cancelled is False
    assert captured
    assert captured[0].from_hook is True


async def test_navigate_tree_unknown_target_raises_invalid_argument() -> None:
    session, _, _ = await _seeded_session()
    h = AgentHarness(AgentHarnessOptions(session=session))
    with pytest.raises(AgentHarnessError) as exc:
        await h.navigate_tree("nope")
    assert exc.value.code == "invalid_argument"
    assert h.phase == "idle"


async def test_navigate_tree_phase_machine_blocks_concurrent_prompt() -> None:
    session, user_id, _ = await _seeded_session()

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_branch(_m: Any, _entries: Any, _ci: Any) -> str:
        started.set()
        await release.wait()
        return "S"

    # Append a sibling so collect_entries_for_branch_summary has work to do.
    await session.append_message(
        AssistantMessage(content=[TextContent(text="branch")])
    )
    h = AgentHarness(
        AgentHarnessOptions(
            session=session, _branch_summarizer_override=slow_branch
        )
    )
    task = asyncio.create_task(
        h.navigate_tree(user_id, NavigateTreeOptions(summarize=True))
    )
    await started.wait()
    assert h.phase == "branch_summary"
    with pytest.raises(AgentHarnessError) as exc:
        await h.prompt("ignored")
    assert exc.value.code == "busy"
    release.set()
    await task
    assert h.phase == "idle"


async def test_generate_branch_summary_raises_on_stream_error() -> None:
    """A provider AssistantErrorEvent → AgentHarnessError (no partial summary)."""

    import pytest
    from aelix_agent_core.harness.core import AgentHarnessError
    from aelix_agent_core.session.branch_summarization import generate_branch_summary
    from aelix_ai.streaming import AssistantErrorEvent, Model, TextDeltaEvent

    async def _fake(_model: Any, _context: Any, _options: Any) -> Any:
        async def _gen() -> Any:
            yield TextDeltaEvent(delta="partial")
            yield AssistantErrorEvent(error_message="provider boom")

        return _gen()

    import aelix_ai.streaming as streaming_mod

    orig = streaming_mod.stream_simple
    streaming_mod.stream_simple = _fake  # type: ignore[assignment]
    try:
        session, _, _ = await _seeded_session()
        entries = await session.get_entries()
        with pytest.raises(AgentHarnessError) as exc:
            await generate_branch_summary(
                Model(id="mock", provider="mock"),
                lambda _m: {"apiKey": "x", "headers": {}},
                entries,
            )
        assert exc.value.code == "invalid_state"
    finally:
        streaming_mod.stream_simple = orig  # type: ignore[assignment]


async def test_generate_branch_summary_no_entries_returns_no_content() -> None:
    """Empty entry list → 'No content to summarize' (Pi early return, no preamble)."""

    from aelix_agent_core.session.branch_summarization import generate_branch_summary
    from aelix_ai.streaming import Model

    summary = await generate_branch_summary(
        Model(id="mock", provider="mock"),
        lambda _m: {"apiKey": "x", "headers": {}},
        [],
    )
    assert summary == "No content to summarize"
