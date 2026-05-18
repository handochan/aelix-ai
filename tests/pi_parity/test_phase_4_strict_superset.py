"""Sprint 6a / Phase 4.1 §G closure pin (ADR-0046).

Pi parity invariant (ADR-0046): every Pi-verified surface in the Phase 4
scope (provider Protocol + 3 emit sites + 12 streaming variants +
deep-merge + "auth" error code) has a corresponding binding in Aelix,
**and DEFERRED_ALLOWLIST is empty** — Phase 2.1 → Phase 4.1 closure.

Closure date: **2026-05-17**; Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import json
from pathlib import Path

from aelix_agent_core.harness.core import AgentHarnessError
from aelix_agent_core.harness.hooks import (
    AfterProviderResponseHookEvent,
    BeforeProviderPayloadHookEvent,
    BeforeProviderRequestHookEvent,
    _apply_stream_options_patch,
)
from aelix_ai.providers.anthropic import (
    ANTHROPIC_API,
    ANTHROPIC_PROVIDER,
    BUILTIN_SOURCE_ID,
    register_all,
    stream_anthropic,
)
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantEndEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    ProviderResponse,
    SimpleStreamOptions,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_phase_2_1_module():
    spec = _importlib_util.spec_from_file_location(
        "_phase_2_1_superset",
        Path(__file__).parent / "test_phase_2_1_strict_superset.py",
    )
    assert spec is not None and spec.loader is not None
    mod = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# === §A — Provider Protocol + Anthropic adapter ===


def test_anthropic_provider_registered_api_id() -> None:
    """Pi parity: ``api == "anthropic-messages"``."""

    assert ANTHROPIC_API == "anthropic-messages"
    assert ANTHROPIC_PROVIDER.api == "anthropic-messages"
    assert callable(ANTHROPIC_PROVIDER.stream)
    assert callable(stream_anthropic)
    assert BUILTIN_SOURCE_ID == "aelix-ai.builtin"


def test_register_all_callable() -> None:
    """``register_all()`` is the documented adapter entry point."""

    assert callable(register_all)


# === §C — 12 AssistantMessageEvent variants ===


def test_12_streaming_event_variants_exist() -> None:
    classes = (
        AssistantStartEvent,
        TextStartEvent,
        TextDeltaEvent,
        TextEndEvent,
        ThinkingStartEvent,
        ThinkingDeltaEvent,
        ThinkingEndEvent,
        ToolCallStartEvent,
        ToolCallDeltaEvent,
        ToolCallEndEvent,
        AssistantDoneEvent,
        AssistantErrorEvent,
    )
    assert len(classes) == 12

    fixture = json.loads(
        (_FIXTURES / "pi_assistant_message_events_734e08e.json").read_text()
    )
    pi_types = {v["type"] for v in fixture["variants"]}
    aelix_types = {cls().type for cls in classes}  # type: ignore[call-arg]
    assert pi_types == aelix_types


def test_assistant_end_event_legacy_subclass_kept() -> None:
    """Back-compat alias retained for test mocks."""

    assert issubclass(AssistantEndEvent, AssistantDoneEvent)
    assert AssistantEndEvent().type == "end"


def test_toolcall_delta_spelling_pi_parity() -> None:
    """P-39d SILENT DRIFT FIX: ``toolcall_delta`` no underscore."""

    assert ToolCallDeltaEvent().type == "toolcall_delta"


# === §D — 3 emit sites active ===


def test_three_provider_events_have_emit_sites() -> None:
    """All 3 provider events register in the Phase 2.1 emit detector."""

    mod = _load_phase_2_1_module()
    emit_subs = mod._HARNESS_OWN_EMIT_SUBSTRINGS
    for name in (
        "before_provider_request",
        "before_provider_payload",
        "after_provider_response",
    ):
        assert name in emit_subs


def test_provider_event_classes_constructible() -> None:
    """Each provider event class can be instantiated for the emit site."""

    BeforeProviderRequestHookEvent(session_id="x", stream_options={})
    BeforeProviderPayloadHookEvent(payload={})
    AfterProviderResponseHookEvent(status=200, headers={})


# === §E — Deep-merge fix (P-41) ===


def test_apply_stream_options_patch_delete_on_none() -> None:
    """Pi parity: nested ``headers: None`` deletes the key."""

    base = {"headers": {"a": "1", "b": "2"}}
    result = _apply_stream_options_patch(base, {"headers": {"a": None}})
    assert result == {"headers": {"b": "2"}}


def test_apply_stream_options_patch_empty_collapses_to_none() -> None:
    """Pi parity (line 111): empty headers dict → key removed."""

    result = _apply_stream_options_patch(
        {"headers": {"a": "1"}}, {"headers": {"a": None}}
    )
    assert result == {}


# === §F — 10-code AgentHarnessError taxonomy ===


def test_agent_harness_error_has_10_codes() -> None:
    """All 10 Pi-parity + Aelix-additive codes are constructible."""

    for code in (
        "busy",
        "invalid_state",
        "invalid_argument",
        "session",
        "hook",
        "auth",
        "compaction",
        "branch_summary",
        "unknown",
        "aborted",
    ):
        err = AgentHarnessError(code, "test")  # type: ignore[arg-type]
        assert err.code == code


# === §H — DEFERRED_ALLOWLIST closure (ADR-0046) ===


def test_deferred_allowlist_is_empty() -> None:
    """Phase 4 closure: every Pi-verified event has an emit site in code."""

    mod = _load_phase_2_1_module()
    assert mod.DEFERRED_ALLOWLIST == {}, (
        f"Phase 4 closure violation — DEFERRED_ALLOWLIST should be empty, "
        f"got {mod.DEFERRED_ALLOWLIST!r}"
    )


def test_provider_response_dataclass_present() -> None:
    """The :class:`ProviderResponse` Pi-shape dataclass exists."""

    pr = ProviderResponse(status=200, headers={"a": "b"})
    assert pr.status == 200
    assert pr.headers == {"a": "b"}


def test_simple_stream_options_phase_4_fields_present() -> None:
    """SimpleStreamOptions exposes Sprint 6a extensions (ADR-0045)."""

    opts = SimpleStreamOptions(
        api_key="k",
        cache_retention="short",
        transport="sse",
        timeout_ms=10000,
        max_retries=2,
        max_retry_delay_ms=500,
        reasoning="medium",
    )
    assert opts.cache_retention == "short"
    assert opts.transport == "sse"
    assert opts.timeout_ms == 10000
    assert opts.max_retries == 2
    assert opts.max_retry_delay_ms == 500
    assert opts.reasoning == "medium"
