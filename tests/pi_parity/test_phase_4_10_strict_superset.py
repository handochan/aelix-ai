"""Sprint 6h₃ · Phase 4.10 closure pin (ADR-0073 / ADR-0074).

Pi parity invariant: every Pi-verified surface in the Phase 4.10
scope (2 session-inspection RPC commands wired through the harness)
has a corresponding binding in Aelix. After Sprint 6h₃ the
dispatcher matches Pi on **24 of 29** commands; the remaining 5
session-tree commands defer to Sprint 6h₄ per ADR-0074.

Closure date: **2026-05-20**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Roster: P-268 ~ P-274.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness._session_stats import (
    SessionStats,
    SessionStatsTokens,
    aggregate_session_stats,
)
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
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
    Usage,
    UsageCost,
)
from aelix_coding_agent._export_html import export_html
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
    _handle_export_html,
    _handle_get_session_stats,
    _session_stats_to_dict,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandExportHtml,
    RpcCommandGetSessionStats,
    RpcSuccessResponse,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads(
        (_FIXTURES / "pi_session_inspection_734e08e.json").read_text()
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


# === §A — Counts: 24 supported / 5 deferred / 29 total =======================


def test_supported_count_is_24_after_sprint_6h3() -> None:
    """Sprint 6h₂ left 22 supported; Sprint 6h₃ adds 2 → 24.

    Sprint 6h₄a (ADR-0075 / P-293~P-298) wires 2 more → 26 supported.
    Sprint 6h₄c (ADR-0079 / P-323~P-331) wires the final 3 → 29.
    PHASE 4 CLOSURE. Closure pin retains the original name; the body
    asserts the live count.
    """

    assert len(SUPPORTED_COMMANDS) == 29


def test_deferred_count_is_5_after_sprint_6h3() -> None:
    """Sprint 6h₂ left 7 deferred; Sprint 6h₃ drops 2 → 5.

    Sprint 6h₄a (ADR-0075 / P-293~P-298) drops 2 more → 3 deferred.
    Sprint 6h₄c (ADR-0079 / P-323~P-331) drops the final 3 → 0.
    PHASE 4 CLOSURE.
    """

    assert len(DEFERRED_COMMANDS) == 0


def test_supported_plus_deferred_is_29() -> None:
    """Pi parity invariant: union covers the full Pi command set."""

    assert SUPPORTED_COMMANDS.isdisjoint(DEFERRED_COMMANDS)
    assert len(SUPPORTED_COMMANDS) + len(DEFERRED_COMMANDS) == 29


# === §B — 2 new commands wired ===============================================


_NEW_COMMANDS = {"get_session_stats", "export_html"}


def test_two_new_commands_in_supported() -> None:
    for cmd in _NEW_COMMANDS:
        assert cmd in SUPPORTED_COMMANDS, f"{cmd!r} missing from SUPPORTED"


def test_two_new_commands_removed_from_deferred() -> None:
    for cmd in _NEW_COMMANDS:
        assert cmd not in DEFERRED_COMMANDS, f"{cmd!r} still in DEFERRED"


def test_dispatcher_table_routes_both_new() -> None:
    """Every newly wired command resolves to a real (non-stub) handler."""

    table = build_dispatch_table()
    for cmd in _NEW_COMMANDS:
        handler = table.get(cmd)
        assert handler is not None, f"No dispatcher entry for {cmd!r}"
        name = getattr(handler, "__qualname__", repr(handler))
        assert "deferred" not in name.lower()


# === §C — Carry-forward 5 deferred all map to ADR-0074 =======================


def test_remaining_five_deferred_are_session_tree() -> None:
    """Sprint 6h₃ leaves 5 session-tree commands for Sprint 6h₄.

    Sprint 6h₄a (ADR-0075) wires the 2 read-only navigation commands
    (``get_fork_messages`` + ``get_last_assistant_text``); the
    remaining 3 commands (``switch_session`` / ``fork`` / ``clone``)
    defer to Sprint 6h₄b per ADR-0076.
    Sprint 6h₄c (ADR-0079 / P-323~P-331) wires the 3 session-tree
    commands on top of the 6h₄b runtime foundation. The DEFERRED set
    is now EMPTY (PHASE 4 CLOSURE).
    """

    assert set(DEFERRED_COMMANDS) == set()


def test_remaining_five_deferred_own_adr_0074() -> None:
    """Pi parity: each deferred owner cites ADR-0074 (Sprint 6h₃) or
    ADR-0076 (Sprint 6h₄a restated owners for the remaining 3
    session-tree commands) or ADR-0078 (Sprint 6h₄b rebrand per spec
    §D.5 — foundation lands without wiring).
    """

    for cmd, owner in DEFERRED_COMMANDS.items():
        assert (
            "ADR-0074" in owner
            or "ADR-0076" in owner
            or "ADR-0078" in owner
        ), (
            f"{cmd!r} owner string {owner!r} does not cite "
            f"ADR-0074 / ADR-0076 / ADR-0078"
        )


# === §D — SessionStats shape matches Pi (P-268) ==============================


def test_session_stats_has_ten_fields_pi_shape() -> None:
    """Pi parity: ``SessionStats`` shape is 10 fields
    (``agent-session.ts:212-223``).
    """

    fields = set(SessionStats.__dataclass_fields__.keys())
    expected = {
        "session_id",
        "user_messages",
        "assistant_messages",
        "tool_calls",
        "tool_results",
        "total_messages",
        "tokens",
        "cost",
        "session_file",
        "context_usage",
    }
    assert fields == expected


def test_session_stats_tokens_has_five_fields_pi_shape() -> None:
    """Pi parity: ``SessionStats.tokens`` sub-shape is 5 fields."""

    fields = set(SessionStatsTokens.__dataclass_fields__.keys())
    assert fields == {
        "input",
        "output",
        "cache_read",
        "cache_write",
        "total",
    }


# === §E — Wire shape camelCase (P-269) =======================================


def test_session_stats_wire_shape_camel_case() -> None:
    """Pi parity: ``_session_stats_to_dict`` emits camelCase keys."""

    stats = aggregate_session_stats("s", [])
    wire = _session_stats_to_dict(stats)
    # Required Pi camelCase keys.
    assert "sessionId" in wire
    assert "userMessages" in wire
    assert "assistantMessages" in wire
    assert "toolCalls" in wire
    assert "toolResults" in wire
    assert "totalMessages" in wire
    assert "tokens" in wire
    assert "cost" in wire
    # Tokens sub-dict.
    tokens = wire["tokens"]
    assert "cacheRead" in tokens
    assert "cacheWrite" in tokens


def test_session_stats_wire_omits_session_file_when_none() -> None:
    """Pi parity (JSON.stringify undefined-skip): ``sessionFile`` omitted."""

    stats = aggregate_session_stats("s", [], session_file=None)
    wire = _session_stats_to_dict(stats)
    assert "sessionFile" not in wire


def test_session_stats_wire_includes_session_file_when_present() -> None:
    """Pi parity: ``sessionFile`` included when supplied."""

    stats = aggregate_session_stats(
        "s", [], session_file="/tmp/x.jsonl"
    )
    wire = _session_stats_to_dict(stats)
    assert wire["sessionFile"] == "/tmp/x.jsonl"


def test_session_stats_wire_omits_context_usage_when_none() -> None:
    """Pi parity (JSON.stringify undefined-skip): ``contextUsage`` omitted."""

    stats = aggregate_session_stats("s", [])
    wire = _session_stats_to_dict(stats)
    assert "contextUsage" not in wire


# === §F — Aggregator algorithm matches Pi (P-269/P-272) ======================


def test_aggregator_algorithm_matches_pi_invariants() -> None:
    """Pi parity: aggregator follows ``getSessionStats`` algorithm."""

    msgs = [
        UserMessage(content=[TextContent(text="u")]),
        AssistantMessage(
            content=[
                TextContent(text="a"),
                ToolCallContent(
                    tool_call_id="c", tool_name="t", input={}
                ),
            ],
            usage=Usage(  # type: ignore[arg-type]
                input=100, output=50, cache_read=10, cache_write=5,
                cost=UsageCost(total=0.01),
            ),
        ),
        ToolResultMessage(
            tool_call_id="c", content=[TextContent(text="ok")]
        ),
    ]
    stats = aggregate_session_stats("s", msgs)
    # Pi parity invariants.
    assert stats.user_messages == 1
    assert stats.assistant_messages == 1
    assert stats.tool_calls == 1
    assert stats.tool_results == 1
    assert stats.total_messages == 3  # = user + assistant + toolResults
    assert stats.tokens.total == 165  # = 100+50+10+5
    assert stats.cost == 0.01


# === §G — RPC handler integration ============================================


async def test_rpc_get_session_stats_returns_pi_shape() -> None:
    """End-to-end RPC handler returns SessionStats in Pi camelCase."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    try:
        cmd = RpcCommandGetSessionStats(id="r1")
        response = await _handle_get_session_stats(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_session_stats"
        assert isinstance(response.data, dict)
        assert "sessionId" in response.data
        assert "tokens" in response.data
    finally:
        await h.dispose()


async def test_rpc_export_html_returns_path_shape(tmp_path: Path) -> None:
    """End-to-end RPC handler returns ``{path}`` shape per Pi.

    Pi parity (P-279 W6): harness requires a real JSONL-backed session;
    in-memory sessions raise via the harness precondition.
    """

    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
    from aelix_agent_core.session.session import Session

    fs = LocalFileSystem()
    file_path = str(tmp_path / "closure.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(tmp_path), session_id="closure-pin"
    )
    session = Session(storage)
    out = tmp_path / "session.html"
    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_stream(), session=session)
    )
    try:
        cmd = RpcCommandExportHtml(id="r2", output_path=str(out))
        response = await _handle_export_html(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "export_html"
        assert isinstance(response.data, dict)
        assert set(response.data.keys()) == {"path"}
        assert response.data["path"] == str(out.resolve())
    finally:
        await h.dispose()


# === §H — HTML emitter wire contract =========================================


def test_export_html_produces_valid_html5_document(tmp_path: Path) -> None:
    """Pi parity: minimal valid HTML5 document is produced."""

    out = tmp_path / "doc.html"
    path = export_html([], output_path=str(out))
    contents = Path(path).read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in contents
    assert "<html" in contents
    assert "</html>" in contents
    assert "<head>" in contents
    assert "<body>" in contents


def test_export_html_returns_string_path(tmp_path: Path) -> None:
    """Pi parity: response shape is ``{path: str}`` (single string)."""

    out = tmp_path / "doc.html"
    path = export_html([], output_path=str(out))
    assert isinstance(path, str)
    assert Path(path).exists()


# === §I — Pi fixture immutability ============================================


def test_pi_sha_pinned_to_phase_4_10_baseline() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_fixture_command_arithmetic_matches_implementation() -> None:
    """Fixture ``after_sprint_6h_3`` describes 24 / 5 / 29."""

    fixture = _load_fixture()
    arithmetic = fixture["command_count_arithmetic"]
    assert (
        arithmetic["after_sprint_6h_3"]
        == "24 supported / 5 deferred / 29 total"
    )
    assert set(arithmetic["supported_added"]) == _NEW_COMMANDS


def test_fixture_handler_line_numbers_match_pi() -> None:
    """Pi parity (P-286 W6): fixture line numbers ``553-556`` /
    ``558-561`` pinned (W5 audit corrected the W1-draft ``475-478`` /
    ``480-483`` to the actual SHA-734e08e line ranges).
    """

    fixture = _load_fixture()
    handlers = fixture["pi_handlers"]
    assert handlers["get_session_stats"]["lines"] == "553-556"
    assert handlers["export_html"]["lines"] == "558-561"
    assert (
        handlers["get_session_stats"]["agent_session_lines"]
        == "2901-2945"
    )
