"""Sprint 6d / Phase 4.4 §K closure pin (ADR-0058).

Pi parity invariant: every Pi-verified surface in the Phase 4.4 scope
(RPC mode + JSONL protocol + RpcClient) has a corresponding binding in
Aelix, **and the deferred-command allowlist is explicit** — Sprint 6d →
Sprint 6e/6f closure.

Closure date: **2026-05-19**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import json
from pathlib import Path

from aelix_coding_agent.rpc import (
    DEFERRED_COMMANDS,
    RPC_COMMAND_TYPES,
    RPC_EXTENSION_UI_REQUEST_METHODS,
    SUPPORTED_COMMANDS,
    JsonlLineReader,
    RpcClient,
    RpcExtensionUIRequestConfirm,
    RpcExtensionUIRequestEditor,
    RpcExtensionUIRequestInput,
    RpcExtensionUIRequestNotify,
    RpcExtensionUIRequestSelect,
    RpcExtensionUIRequestSetEditorText,
    RpcExtensionUIRequestSetStatus,
    RpcExtensionUIRequestSetTitle,
    RpcExtensionUIRequestSetWidget,
    RpcExtensionUIResponseCancelled,
    RpcExtensionUIResponseConfirmed,
    RpcExtensionUIResponseValue,
    RpcSessionState,
    build_dispatch_table,
    serialize_json_line,
)
from aelix_coding_agent.rpc.rpc_types import RpcErrorResponse

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    return json.loads((_FIXTURES / "pi_rpc_mode_734e08e.json").read_text())


# === §A — RpcCommand variant count (Pi rpc-types.ts:19-69) =====================


def test_rpc_command_count_matches_pi_fixture() -> None:
    """Pi RpcCommand variant count is enumerated in the W0 fixture.

    The fixture's ``rpc_command_types`` array is the authoritative wire
    surface; ``RPC_COMMAND_TYPES`` MUST equal it as a set.
    """

    fixture = _load_fixture()
    pi_types = set(fixture["rpc_command_types"])
    assert pi_types == RPC_COMMAND_TYPES


def test_supported_plus_deferred_covers_pi() -> None:
    """Pi parity: supported + deferred = 29 total Pi RpcCommand variants.

    Sprint 6f W2 (ADR-0065): counts moved to 12 supported + 17 deferred.
    Sprint 6h₁ (ADR-0069) bumps to 13 supported + 16 deferred = 29.
    Sprint 6h₂ (ADR-0071) wires 9 more: 22 supported + 7 deferred = 29.
    Sprint 6h₃ (ADR-0073) wires 2 session-inspection commands:
    24 supported + 5 deferred = 29.
    Sprint 6h₄a (ADR-0075 / ADR-0076) wires 2 session-navigation
    commands: 26 supported + 3 deferred = 29.
    Sprint 6d originally shipped 9 supported + 20 deferred.

    The spec preamble cites "28" as a counting error; the fixture's
    ``rpc_command_types`` list is the authoritative count and we honor it.
    """

    assert SUPPORTED_COMMANDS.isdisjoint(set(DEFERRED_COMMANDS.keys()))
    assert SUPPORTED_COMMANDS | set(DEFERRED_COMMANDS.keys()) == RPC_COMMAND_TYPES
    # W4 M2 / P-121 + Sprint 6f W2 + Sprint 6h₁ + Sprint 6h₂ + 6h₃ +
    # 6h₄a + 6h₄c — explicit count assertion so a future PR that adds
    # a command without updating both sets trips immediately.
    # Sprint 6h₄c (ADR-0079 / P-323~P-331) PHASE 4 CLOSURE: wires the 3
    # last DEFERRED session-tree commands (switch_session / fork /
    # clone) on top of the 6h₄b runtime foundation. Counts move to
    # 29 supported / 0 deferred / 29 total.
    assert len(RPC_COMMAND_TYPES) == 29
    assert len(SUPPORTED_COMMANDS) == 29
    assert len(DEFERRED_COMMANDS) == 0


def test_supported_commands_match_p107_table() -> None:
    """Pi parity (P-107 + Sprint 6f W2 P-168/P-169 + Sprint 6h₁ P-219 +
    Sprint 6h₂ P-245~P-253): commands the existing Aelix harness can
    satisfy.

    Sprint 6f W2 (ADR-0065) adds set_model / cycle_model /
    get_available_models on top of the Sprint 6d 9-command set.
    Sprint 6h₁ (ADR-0069) adds get_commands. Sprint 6h₂ (ADR-0071)
    adds 9 more (steer / follow_up / cycle_thinking_level / mode
    setters / auto-mode flags / abort_retry / abort_bash).
    """

    expected = {
        "prompt",
        "abort",
        "new_session",
        "get_state",
        "get_messages",
        "compact",
        "bash",
        "set_thinking_level",
        "set_session_name",
        # Sprint 6f W2 (ADR-0065).
        "set_model",
        "cycle_model",
        "get_available_models",
        # Sprint 6h₁ (ADR-0069 / P-219).
        "get_commands",
        # Sprint 6h₂ (ADR-0071 / P-245~P-253) — 9 new wired commands.
        "steer",
        "follow_up",
        "cycle_thinking_level",
        "set_steering_mode",
        "set_follow_up_mode",
        "set_auto_compaction",
        "set_auto_retry",
        "abort_retry",
        "abort_bash",
        # Sprint 6h₃ (ADR-0073 / P-268~P-274) — 2 session-inspection commands.
        "get_session_stats",
        "export_html",
        # Sprint 6h₄a (ADR-0075 / P-293~P-298) — 2 session-navigation commands.
        "get_fork_messages",
        "get_last_assistant_text",
        # Sprint 6h₄c (ADR-0079 / P-323~P-331) — 3 session-tree commands
        # (PHASE 4 CLOSURE).
        "switch_session",
        "fork",
        "clone",
    }
    assert expected == SUPPORTED_COMMANDS


def test_deferred_commands_cover_remaining_pi_set() -> None:
    """Every Pi variant not in SUPPORTED is in DEFERRED with an ADR owner.

    Sprint 6h₂ (ADR-0072) restated the 7 carry-forward ADR owners from
    ADR-0058 → ADR-0072.
    Sprint 6h₃ (ADR-0074) restates the remaining 5 session-tree owners
    ADR-0072 → ADR-0074.
    Sprint 6h₄a (ADR-0076) restates the remaining 3 session-tree
    owners ADR-0074 → ADR-0076.
    Sprint 6h₄b (ADR-0078) rebrands the same 3 session-tree owners
    ADR-0076 → ADR-0078 per spec §D.5 (foundation lands; 6h₄c wires
    the handlers). Accept any of the five prefixes so closure-pin
    runs across the transitions stay deterministic.
    """

    remaining = RPC_COMMAND_TYPES - SUPPORTED_COMMANDS
    assert set(DEFERRED_COMMANDS.keys()) == remaining
    for owner in DEFERRED_COMMANDS.values():
        assert (
            "ADR-0058" in owner
            or "ADR-0072" in owner
            or "ADR-0074" in owner
            or "ADR-0076" in owner
            or "ADR-0078" in owner
        )


# === §B — Dispatch table closure ==============================================


async def test_every_deferred_route_returns_error_response() -> None:
    """Every deferred command in the dispatch table emits ``success: false``."""

    table = build_dispatch_table()

    class _C:
        id = "x"

    for cmd_type in DEFERRED_COMMANDS:
        response = await table[cmd_type](None, _C())
        assert isinstance(response, RpcErrorResponse)
        assert response.command == cmd_type


# === §C — JSONL framing constants (Pi parity, P-106) ===========================


def test_jsonl_framing_is_lf_only() -> None:
    """P-127 — ``serialize_json_line`` uses ``\\n`` only — never U+2028 /
    U+2029. Round-trip a payload that literally contains both Unicode
    line separators inside its string value; parsing the serialized line
    after stripping the trailing LF must reproduce the original payload
    verbatim.
    """

    payload = {
        "text": (
            # U+2028 LINE SEPARATOR + U+2029 PARAGRAPH SEPARATOR — both
            # legal inside JSON strings; framing must NOT split on them.
            "line one line two line three"
        )
    }
    line = serialize_json_line(payload)
    assert line.endswith("\n")
    # Exactly ONE LF — the framing one. U+2028 / U+2029 must not be
    # treated as separators by the serializer.
    assert line.count("\n") == 1
    # Round-trip: strip the framing LF and re-parse → original dict.
    assert json.loads(line.rstrip("\n")) == payload
    # The raw Unicode separators survive the serialization step (no
    # escaping into `` `` since ``ensure_ascii=False``).
    assert " " in line
    assert " " in line


def test_jsonl_reader_strips_cr_for_crlf_tolerance() -> None:
    """Pi: ``line.endsWith("\\r") ? line.slice(0, -1) : line``."""

    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed("hello\r\n")
    assert received == ["hello"]


def test_jsonl_reader_emits_tail_on_end() -> None:
    """Pi: trailing buffer on ``onEnd`` is emitted as a final line."""

    received: list[str] = []
    reader = JsonlLineReader(received.append)
    reader.feed("partial")
    reader.end()
    assert received == ["partial"]


# === §D — RpcSessionState 13-field shape (Pi rpc-types.ts:90-103) =============
# Sprint 6h₂ (P-264) extended the wire shape 12 → 13 by adding
# ``auto_retry_enabled``, symmetric with ``auto_compaction_enabled``.


def test_rpc_session_state_has_pi_13_fields() -> None:
    """Pi shape: 13 named fields, camelCase on the wire (Sprint 6h₂
    P-264 added ``auto_retry_enabled``)."""

    fixture = _load_fixture()
    pi_fields = set(fixture["rpc_session_state_shape"].keys())
    aelix_fields = set(RpcSessionState.__dataclass_fields__.keys())
    # Pi fields are camelCase / snake-mixed in the fixture; normalise.
    # Fixture uses snake_case keys, so we compare directly.
    assert aelix_fields == pi_fields


def test_rpc_session_state_to_json_uses_camel_case_wire_shape() -> None:
    wire = RpcSessionState().to_json()
    # All keys are either single-word or camelCase (no underscores).
    for key in wire:
        assert "_" not in key, f"Key {key!r} should be camelCase on the wire"


# === §E — RpcExtensionUIRequest 9-method shape (Pi rpc-types.ts:213-247) ======


def test_rpc_extension_ui_request_methods_match_pi() -> None:
    fixture = _load_fixture()
    pi_methods = set(fixture["rpc_extension_ui_request_methods"])
    assert pi_methods == RPC_EXTENSION_UI_REQUEST_METHODS


def test_rpc_extension_ui_request_dataclasses_cover_9_methods() -> None:
    """9 dataclasses, one per Pi method (TYPES only — Sprint 6d ships shape)."""

    classes = [
        RpcExtensionUIRequestSelect,
        RpcExtensionUIRequestConfirm,
        RpcExtensionUIRequestInput,
        RpcExtensionUIRequestEditor,
        RpcExtensionUIRequestNotify,
        RpcExtensionUIRequestSetStatus,
        RpcExtensionUIRequestSetWidget,
        RpcExtensionUIRequestSetTitle,
        RpcExtensionUIRequestSetEditorText,
    ]
    assert len(classes) == 9
    # Every dataclass declares the ``method`` Literal discriminator that
    # matches one Pi method name.
    methods_in_dataclasses = {
        cls.__dataclass_fields__["method"].default for cls in classes
    }
    assert methods_in_dataclasses == RPC_EXTENSION_UI_REQUEST_METHODS


def test_rpc_extension_ui_response_three_shapes() -> None:
    """Pi rpc-types.ts:253-256 — 3 response shapes (value / confirmed / cancelled)."""

    # All three carry the ``extension_ui_response`` type discriminator.
    for cls in (
        RpcExtensionUIResponseValue,
        RpcExtensionUIResponseConfirmed,
        RpcExtensionUIResponseCancelled,
    ):
        default = cls.__dataclass_fields__["type"].default
        assert default == "extension_ui_response"


# === §F — RpcClient default constants (Pi rpc-client.ts) ======================


def test_rpc_client_default_constants_match_pi() -> None:
    """Pi rpc-client.ts:79 (100ms), :107 (1s), :262 (60s), :332 (30s)."""

    assert RpcClient.DEFAULT_SEND_TIMEOUT_MS == 30_000
    assert RpcClient.DEFAULT_WAIT_FOR_IDLE_MS == 60_000
    assert RpcClient.STARTUP_GRACE_MS == 100
    assert RpcClient.SHUTDOWN_SIGTERM_TIMEOUT_MS == 1_000


# === §G — Pi fixture immutability ============================================


def test_pi_sha_pinned_to_phase_4_4_baseline() -> None:
    fixture = _load_fixture()
    assert fixture["pi_sha"] == "734e08edf82ff315bc3d96472a6ebfa69a1d8016"


def test_fixture_loc_counts_present() -> None:
    fixture = _load_fixture()
    locs = fixture["pi_file_loc"]
    assert locs["jsonl.ts"] == 58
    assert locs["rpc-types.ts"] == 262
    assert locs["rpc-mode.ts"] == 492
    assert locs["rpc-client.ts"] == 343


def test_fixture_rpc_command_count_matches_implementation() -> None:
    """W4 M2 / P-121 — fixture ``rpc_command_count`` matches Pi reality
    (29 variants). A future PR that adds a variant must increment the
    fixture in the same change.
    """

    fixture = _load_fixture()
    assert fixture["rpc_command_count"] == len(RPC_COMMAND_TYPES) == 29


# === §H — Per-variant RpcCommand field-set assertion (P-128) ==================
#
# Each Pi ``RpcCommand`` variant has a known field roster
# (``rpc-types.ts:19-69``). A drift in any dataclass — e.g. dropping a
# required argument or renaming one — is caught mechanically.

PI_COMMAND_FIELDS: dict[str, frozenset[str]] = {
    "prompt": frozenset({"type", "message", "images", "streaming_behavior", "id"}),
    "steer": frozenset({"type", "message", "images", "id"}),
    "follow_up": frozenset({"type", "message", "images", "id"}),
    "abort": frozenset({"type", "id"}),
    "new_session": frozenset({"type", "parent_session", "id"}),
    "get_state": frozenset({"type", "id"}),
    "set_model": frozenset({"type", "provider", "model_id", "id"}),
    "cycle_model": frozenset({"type", "id"}),
    "get_available_models": frozenset({"type", "id"}),
    "set_thinking_level": frozenset({"type", "level", "id"}),
    "cycle_thinking_level": frozenset({"type", "id"}),
    "set_steering_mode": frozenset({"type", "mode", "id"}),
    "set_follow_up_mode": frozenset({"type", "mode", "id"}),
    "compact": frozenset({"type", "custom_instructions", "id"}),
    "set_auto_compaction": frozenset({"type", "enabled", "id"}),
    "set_auto_retry": frozenset({"type", "enabled", "id"}),
    "abort_retry": frozenset({"type", "id"}),
    "bash": frozenset({"type", "command", "id"}),
    "abort_bash": frozenset({"type", "id"}),
    "get_session_stats": frozenset({"type", "id"}),
    "export_html": frozenset({"type", "output_path", "id"}),
    "switch_session": frozenset({"type", "session_path", "id"}),
    "fork": frozenset({"type", "entry_id", "id"}),
    "clone": frozenset({"type", "id"}),
    "get_fork_messages": frozenset({"type", "id"}),
    "get_last_assistant_text": frozenset({"type", "id"}),
    "set_session_name": frozenset({"type", "name", "id"}),
    "get_messages": frozenset({"type", "id"}),
    "get_commands": frozenset({"type", "id"}),
}


def test_pi_command_fields_table_is_exhaustive() -> None:
    """P-128 — the field-set table covers every Pi variant exactly."""

    assert set(PI_COMMAND_FIELDS.keys()) == set(RPC_COMMAND_TYPES)


def test_each_rpc_command_dataclass_matches_pi_field_set() -> None:
    """P-128 — every ``RpcCommand`` variant's ``__dataclass_fields__`` matches
    the pinned Pi roster. Drift trips per-command, not in aggregate.
    """

    from aelix_coding_agent.rpc.rpc_types import _RPC_COMMAND_REGISTRY

    for cmd_type, expected in PI_COMMAND_FIELDS.items():
        cls = _RPC_COMMAND_REGISTRY[cmd_type]
        actual = frozenset(cls.__dataclass_fields__.keys())
        assert actual == expected, (
            f"{cls.__name__} field drift: expected {sorted(expected)}, "
            f"got {sorted(actual)}"
        )


# === §I — session_file resolution regression (W4 M5) =========================


async def test_get_state_session_file_resolves_real_jsonl_path(
    tmp_path,
) -> None:
    """W4 M5 — closure-pin regression for the storage-path attribute drift.

    Build a ``JsonlSessionStorage``-backed ``Session``, attach it to a
    harness, invoke ``_handle_get_state``, and assert the returned
    ``data["sessionFile"]`` is the actual ``.jsonl`` path. The previous
    implementation read a nonexistent ``_path`` attribute and always
    returned ``None``.
    """

    from collections.abc import AsyncIterator

    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem
    from aelix_agent_core.session.session import Session
    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import (
        AssistantEndEvent,
        AssistantMessageEvent,
        AssistantStartEvent,
        Context,
        Model,
        SimpleStreamOptions,
    )
    from aelix_coding_agent.rpc.rpc_mode import _handle_get_state
    from aelix_coding_agent.rpc.rpc_types import (
        RpcCommandGetState,
        RpcSuccessResponse,
    )

    async def _stream(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    fs = LocalFileSystem()
    file_path = str(tmp_path / "closure.jsonl")
    storage = await JsonlSessionStorage.create(
        fs, file_path, cwd=str(tmp_path), session_id="closure-pin"
    )
    session = Session(storage)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream,
            session=session,
        )
    )
    response = await _handle_get_state(
        harness, RpcCommandGetState(id="r")
    )
    assert isinstance(response, RpcSuccessResponse)
    assert isinstance(response.data, dict)
    assert response.data["sessionFile"] == file_path
    await harness.dispose()
