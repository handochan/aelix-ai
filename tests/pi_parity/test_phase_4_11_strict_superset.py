"""Sprint 6h₄a · Phase 4.11 closure pin (ADR-0075 / ADR-0076).

Pi parity invariant: every Pi-verified surface in the Phase 4.11
scope (2 read-only session-navigation RPC commands wired through the
harness) has a corresponding binding in Aelix. After Sprint 6h₄a the
dispatcher matches Pi on **26 of 29** commands; the remaining 3
session-tree commands (``switch_session`` / ``fork`` / ``clone``)
defer to Sprint 6h₄b per ADR-0076.

Closure date: **2026-05-20**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

Roster: P-293 ~ P-298.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness._fork_point import ForkPointInfo
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import (
    DEFERRED_COMMANDS,
    SUPPORTED_COMMANDS,
    _fork_points_to_dict,
    _handle_get_fork_messages,
    _handle_get_last_assistant_text,
    build_dispatch_table,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandGetForkMessages,
    RpcCommandGetLastAssistantText,
    RpcSuccessResponse,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads(
        (_FIXTURES / "pi_session_navigation_734e08e.json").read_text()
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


# === §A — Counts: 26 supported / 3 deferred / 29 total =======================


def test_supported_count_is_26_after_sprint_6h4a() -> None:
    """Sprint 6h₃ left 24 supported; Sprint 6h₄a adds 2 → 26."""

    assert len(SUPPORTED_COMMANDS) == 26


def test_deferred_count_is_3_after_sprint_6h4a() -> None:
    """Sprint 6h₃ left 5 deferred; Sprint 6h₄a drops 2 → 3."""

    assert len(DEFERRED_COMMANDS) == 3


def test_supported_plus_deferred_is_29() -> None:
    """Pi parity invariant: union covers the full Pi command set."""

    assert SUPPORTED_COMMANDS.isdisjoint(DEFERRED_COMMANDS)
    assert len(SUPPORTED_COMMANDS) + len(DEFERRED_COMMANDS) == 29


# === §B — 2 new commands wired ===============================================


_NEW_COMMANDS = {"get_fork_messages", "get_last_assistant_text"}


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


# === §C — Carry-forward 3 deferred all map to ADR-0076 =======================


def test_remaining_three_deferred_are_session_tree() -> None:
    """Sprint 6h₄a leaves 3 session-tree commands for Sprint 6h₄b."""

    expected = {"switch_session", "fork", "clone"}
    assert set(DEFERRED_COMMANDS) == expected


def test_remaining_three_deferred_own_adr_0076() -> None:
    """Pi parity: each deferred owner cites ADR-0076 (Sprint 6h₄a)
    or ADR-0078 (Sprint 6h₄b foundation rebrand per spec §D.5).
    """

    for cmd, owner in DEFERRED_COMMANDS.items():
        assert "ADR-0076" in owner or "ADR-0078" in owner, (
            f"{cmd!r} owner string {owner!r} does not cite "
            f"ADR-0076 or ADR-0078"
        )


# === §D — ForkPointInfo dataclass shape (P-295) ==============================


def test_fork_point_info_has_two_fields() -> None:
    """Pi parity: ``ForkPointInfo`` mirrors Pi inline shape
    ``{entryId, text}`` — 2 snake_case Python fields.
    """

    fields = set(ForkPointInfo.__dataclass_fields__.keys())
    assert fields == {"entry_id", "text"}


def test_fork_point_info_is_frozen() -> None:
    """Pi parity: ``ForkPointInfo`` is a frozen dataclass."""

    assert ForkPointInfo.__dataclass_params__.frozen is True


def test_fork_point_info_basic_construction() -> None:
    """ForkPointInfo accepts ``entry_id`` + ``text`` and exposes them."""

    p = ForkPointInfo(entry_id="e1", text="hi")
    assert p.entry_id == "e1"
    assert p.text == "hi"


# === §E — Wire serializer camelCase (P-295) ==================================


def test_fork_points_serializer_emits_camel_case_keys() -> None:
    """Pi parity: wire keys are ``entryId`` (camelCase), not ``entry_id``."""

    points = [
        ForkPointInfo(entry_id="e1", text="first"),
        ForkPointInfo(entry_id="e2", text="second"),
    ]
    wire = _fork_points_to_dict(points)
    assert wire == [
        {"entryId": "e1", "text": "first"},
        {"entryId": "e2", "text": "second"},
    ]
    # Each record carries EXACTLY 2 keys — no snake_case leak.
    for record in wire:
        assert set(record.keys()) == {"entryId", "text"}


def test_fork_points_serializer_empty_list() -> None:
    """Empty input → empty wire array."""

    assert _fork_points_to_dict([]) == []


# === §F — P-298 lock: Pi key-omission for get_last_assistant_text ============


async def test_get_last_assistant_text_empty_data_when_none() -> None:
    """P-298 SYNTHESIS lock: empty harness → ``data == {}``.

    Pi ``JSON.stringify({text: undefined})`` drops the ``text`` key.
    The Aelix handler MUST emit an empty dict so the wire bytes match.
    """

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    try:
        cmd = RpcCommandGetLastAssistantText(id="r1")
        response = await _handle_get_last_assistant_text(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_last_assistant_text"
        assert response.data == {}
        assert isinstance(response.data, dict)
        assert "text" not in response.data
    finally:
        await h.dispose()


async def test_get_last_assistant_text_includes_text_when_present() -> None:
    """Pi parity: text present → ``data == {"text": ...}``."""

    h = AgentHarness(
        AgentHarnessOptions(
            stream_fn=_stream(),
            initial_messages=[
                AssistantMessage(
                    content=[TextContent(text="closure")],
                    stop_reason="end_turn",
                )
            ],
        )
    )
    try:
        cmd = RpcCommandGetLastAssistantText(id="r2")
        response = await _handle_get_last_assistant_text(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.data == {"text": "closure"}
    finally:
        await h.dispose()


# === §G — get_fork_messages RPC integration ==================================


async def test_rpc_get_fork_messages_empty_harness_returns_pi_shape() -> None:
    """End-to-end RPC handler: empty harness → ``{messages: []}``."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    try:
        cmd = RpcCommandGetForkMessages(id="r3")
        response = await _handle_get_fork_messages(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "get_fork_messages"
        assert response.data == {"messages": []}
    finally:
        await h.dispose()


# === §H — Pi fixture immutability + line-citation pins (P-293) ===============


def test_pi_sha_pinned_to_phase_4_11_baseline() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_fixture_command_arithmetic_matches_implementation() -> None:
    """Fixture ``after_sprint_6h_4_a`` describes 26 / 3 / 29."""

    fixture = _load_fixture()
    arithmetic = fixture["command_count_arithmetic"]
    assert (
        arithmetic["after_sprint_6h_4_a"]
        == "26 supported / 3 deferred / 29 total"
    )
    assert set(arithmetic["supported_added"]) == _NEW_COMMANDS
    assert set(arithmetic["still_deferred"]) == {
        "switch_session",
        "fork",
        "clone",
    }


def test_fixture_handler_line_numbers_match_pi_at_sha_734e08e() -> None:
    """P-293 — fixture pins the W0-VERIFIED line citations (`591-594` /
    `596-599`) which supersede ADR-0074's `563-566` / `568-571`
    estimates. ADR-0075 records the supersession.
    """

    fixture = _load_fixture()
    handlers = fixture["pi_handlers"]
    assert handlers["get_fork_messages"]["lines"] == "591-594"
    assert handlers["get_last_assistant_text"]["lines"] == "596-599"


def test_fixture_agent_session_line_citations_pinned() -> None:
    """P-293 — agent-session.ts line citations for the harness methods
    + the private text-extraction helper.
    """

    fixture = _load_fixture()
    assert (
        fixture["pi_handlers"]["get_fork_messages"]["agent_session_lines"]
        == "2870-2885"
    )
    assert (
        fixture["pi_handlers"]["get_last_assistant_text"]["agent_session_lines"]
        == "3059-3081"
    )
    assert (
        fixture["pi_helpers"]["_extractUserMessageText"]["lines"]
        == "2887-2896"
    )


def test_fixture_drift_record_documents_adr_0074_supersession() -> None:
    """P-293 — the fixture's ``_p_293_note`` documents the line drift
    discovery so future audits resolve the discrepancy via ADR-0075
    rather than re-investigating.
    """

    fixture = _load_fixture()
    note = fixture["_p_293_note"]
    # Cites both the superseded estimate and the verified ranges.
    assert "563-566" in note
    assert "568-571" in note
    assert "591-594" in note
    assert "596-599" in note
    assert "ADR-0075" in note
    assert "ADR-0074" in note


# === §I — Pi-parity comment trail: handlers reference verified lines =========


def test_handler_docstrings_cite_verified_pi_lines() -> None:
    """The 2 new RPC handlers must cite the W0-VERIFIED line ranges
    (``591-594`` / ``596-599``) in their docstrings so reviewers can
    cross-check against Pi at the pinned SHA.
    """

    assert _handle_get_fork_messages.__doc__ is not None
    assert "591-594" in _handle_get_fork_messages.__doc__
    assert _handle_get_last_assistant_text.__doc__ is not None
    assert "596-599" in _handle_get_last_assistant_text.__doc__


def test_harness_method_docstrings_cite_agent_session_lines() -> None:
    """The 2 harness methods + the private helper must cite their
    Pi line ranges in ``agent-session.ts``.
    """

    method = AgentHarness.get_user_messages_for_forking
    assert method.__doc__ is not None
    assert "2870-2885" in method.__doc__

    last = AgentHarness.get_last_assistant_text
    assert last.__doc__ is not None
    assert "3059-3081" in last.__doc__

    extract = AgentHarness._extract_user_message_text
    assert extract.__doc__ is not None
    assert "2887-2896" in extract.__doc__


# === §J — Dispatcher integration: deferred → supported transition pin =======


def test_get_fork_messages_dispatch_is_not_deferred_stub() -> None:
    table = build_dispatch_table()
    handler = table["get_fork_messages"]
    name = getattr(handler, "__qualname__", repr(handler))
    assert "_handle_get_fork_messages" in name or "handle_get_fork_messages" in name


def test_get_last_assistant_text_dispatch_is_not_deferred_stub() -> None:
    table = build_dispatch_table()
    handler = table["get_last_assistant_text"]
    name = getattr(handler, "__qualname__", repr(handler))
    assert (
        "_handle_get_last_assistant_text" in name
        or "handle_get_last_assistant_text" in name
    )


def test_fork_point_info_dataclass_field_types() -> None:
    """ForkPointInfo carries two string fields (Pi: ``string`` /
    ``string``). The dataclass field type annotations match.
    """

    fields = {f.name: f for f in dataclasses.fields(ForkPointInfo)}
    # Annotation strings (the dataclass stores them as strings due to
    # ``from __future__ import annotations``).
    assert fields["entry_id"].type == "str"
    assert fields["text"].type == "str"
