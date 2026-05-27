"""Sprint 6b / Phase 4.2 §H closure pin (ADR-0049).

Pi parity invariant: every Pi-verified surface in the Phase 4.2 scope
(OpenAI Completions adapter + 4 shared utilities + compat detection)
has a corresponding binding in Aelix, **and the deferred-adapter /
deferred-compat allowlists are explicit** — Sprint 6b → Sprint 6d
closure.

Closure date: **2026-05-18**. Pi SHA pinned by ADR-0034:
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aelix_ai.api_registry import (
    clear_providers,
    get_registered_providers,
)
from aelix_ai.providers._env_api_keys import ENV_API_KEYS
from aelix_ai.providers._openai_compat import (
    COMPAT_DEFERRED_ALLOWLIST,
    OpenAICompletionsCompat,
    detect_compat,
)
from aelix_ai.providers.anthropic import (
    ANTHROPIC_API,
)
from aelix_ai.providers.anthropic import (
    register_all as register_anthropic,
)
from aelix_ai.providers.openai_completions import (
    BUILTIN_SOURCE_ID,
    OPENAI_COMPLETIONS_API,
    OPENAI_COMPLETIONS_PROVIDER,
    OpenAICompletionsOptions,
    _map_stop_reason,
    convert_messages,
    convert_tools,
    stream_openai_completions,
    stream_simple_openai_completions,
)
from aelix_ai.providers.openai_completions import (
    register_all as register_openai,
)
from aelix_ai.streaming import Model

_FIXTURES = Path(__file__).parent / "fixtures"


# Pi parity (P-49): 9 KnownApi adapters total. Sprint 6a registered
# anthropic-messages; Sprint 6b registers openai-completions. The 7
# below remain deferred to subsequent sprints — each row carries the
# owning ADR for traceability.
PHASE_4_2_DEFERRED_APIS: dict[str, str] = {
    "openai-responses": "ADR-0049 §J — separate sprint",
    "openai-codex-responses": "ADR-0049 §J — separate sprint",
    "azure-openai-responses": "ADR-0049 §J — separate sprint",
    "mistral-conversations": "ADR-0049 §J — separate sprint",
    "google-generative-ai": "ADR-0049 §J — separate sprint",
    "google-vertex": "ADR-0049 §J — separate sprint",
    "bedrock-converse-stream": "ADR-0049 §J — separate sprint",
}


PI_TOTAL_KNOWN_APIS = 9


def _load_fixture() -> dict:
    return json.loads(
        (_FIXTURES / "pi_openai_completions_734e08e.json").read_text()
    )


# === §A — Adapter registration ===


def test_openai_completions_api_id_pi_parity() -> None:
    assert OPENAI_COMPLETIONS_API == "openai-completions"
    assert OPENAI_COMPLETIONS_PROVIDER.api == "openai-completions"
    assert callable(stream_openai_completions)
    assert callable(stream_simple_openai_completions)
    assert BUILTIN_SOURCE_ID == "aelix-ai.builtin"


def test_register_all_is_idempotent() -> None:
    clear_providers()
    try:
        register_openai()
        register_openai()  # idempotent re-registration.
        registry = get_registered_providers()
        assert "openai-completions" in registry
        assert registry["openai-completions"] is OPENAI_COMPLETIONS_PROVIDER
    finally:
        clear_providers()


def test_two_of_nine_apis_live() -> None:
    """Sprint 6a + 6b → 2 / 9 KnownApi adapters registered."""

    clear_providers()
    try:
        register_anthropic()
        register_openai()
        registry = get_registered_providers()
        assert ANTHROPIC_API in registry
        assert OPENAI_COMPLETIONS_API in registry
        assert len(registry) == 2
    finally:
        clear_providers()


# === §B — Deferred adapter allowlist (ADR-0049 §J) ===


def test_deferred_adapter_allowlist_size() -> None:
    """7 of 9 KnownApi adapters deferred to subsequent sprints."""

    assert len(PHASE_4_2_DEFERRED_APIS) == PI_TOTAL_KNOWN_APIS - 2


def test_deferred_adapter_allowlist_owns_each_api() -> None:
    """Every deferred adapter has an owning ADR reference."""

    for api, owner in PHASE_4_2_DEFERRED_APIS.items():
        assert "ADR-" in owner, (
            f"{api} deferred allowlist entry missing owning ADR"
        )


# === §C — Compat dataclass (17 fields) ===


def test_compat_field_set_matches_pi() -> None:
    fixture = _load_fixture()
    pi_compat_fields_snake = {
        # Pi camelCase → Python snake_case.
        "supportsStore": "supports_store",
        "supportsDeveloperRole": "supports_developer_role",
        "supportsReasoningEffort": "supports_reasoning_effort",
        "supportsUsageInStreaming": "supports_usage_in_streaming",
        "maxTokensField": "max_tokens_field",
        "requiresToolResultName": "requires_tool_result_name",
        "requiresAssistantAfterToolResult": "requires_assistant_after_tool_result",
        "requiresThinkingAsText": "requires_thinking_as_text",
        "requiresReasoningContentOnAssistantMessages": (
            "requires_reasoning_content_on_assistant_messages"
        ),
        "thinkingFormat": "thinking_format",
        "openRouterRouting": "open_router_routing",
        "vercelGatewayRouting": "vercel_gateway_routing",
        "zaiToolStream": "zai_tool_stream",
        "supportsStrictMode": "supports_strict_mode",
        "cacheControlFormat": "cache_control_format",
        "sendSessionAffinityHeaders": "send_session_affinity_headers",
        "supportsLongCacheRetention": "supports_long_cache_retention",
    }
    expected_pi_fields = set(fixture["compat_fields"])
    assert set(pi_compat_fields_snake.keys()) == expected_pi_fields
    aelix_fields = set(OpenAICompletionsCompat.__dataclass_fields__.keys())
    assert set(pi_compat_fields_snake.values()) <= aelix_fields
    assert len(OpenAICompletionsCompat.__dataclass_fields__) == 17


# === §D — Env-key table superset of Pi ===


def test_env_api_keys_superset_of_pi() -> None:
    fixture = _load_fixture()
    pi_map = {
        k: v
        for k, v in fixture["env_api_key_mapping"].items()
        if not k.startswith("_")
    }
    for provider, envs in pi_map.items():
        assert provider in ENV_API_KEYS
        assert ENV_API_KEYS[provider] == envs


# === §E — Compat deferred allowlist (Sprint 6d compat zoo) ===


def test_compat_deferred_allowlist_populated() -> None:
    """4+2 compat targets remain deferred to Sprint 6d (per ADR-0050 §J).

    Sprint 6b W6 (M-2): the ``qwen`` / ``qwen-chat-template`` thinking
    formats are reachable via ``model.compat`` overrides but have no
    auto-detection path — they sit in the allowlist alongside the four
    compat-zoo targets so a future detection-path PR cannot silently
    bypass parity.
    """

    expected = {
        "cloudflare-workers-ai",
        "cloudflare-ai-gateway",
        "github-copilot",
        "vercel-ai-gateway",
        "qwen",
        "qwen-chat-template",
    }
    assert set(COMPAT_DEFERRED_ALLOWLIST.keys()) == expected
    for owner in COMPAT_DEFERRED_ALLOWLIST.values():
        assert "ADR-" in owner


# === §F — Detection sanity for the 11 supported providers ===


def test_supported_providers_detect_without_crashing() -> None:
    """Every Sprint 6b-supported provider produces a usable compat."""

    providers = [
        ("openai", "https://api.openai.com/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("groq", "https://api.groq.com/openai/v1"),
        ("deepseek", "https://api.deepseek.com/v1"),
        ("xai", "https://api.x.ai/v1"),
        ("zai", "https://api.z.ai/v1"),
        ("together", "https://api.together.ai/v1"),
        ("moonshotai", "https://api.moonshot.cn/v1"),
        ("moonshotai-cn", "https://api.moonshot.cn/v1"),
        ("cerebras", "https://api.cerebras.ai/v1"),
        ("opencode", "https://opencode.ai/v1"),
    ]
    for provider, base_url in providers:
        m = Model(
            api="openai-completions",
            id="model",
            provider=provider,
            base_url=base_url,
        )
        compat = detect_compat(m)
        assert isinstance(compat, OpenAICompletionsCompat)


# === §G — Convert helpers exposed ===


def test_convert_helpers_callable() -> None:
    assert callable(convert_messages)
    assert callable(convert_tools)


def test_options_dataclass_extends_simple() -> None:
    """``OpenAICompletionsOptions`` is constructible with Pi-shape kwargs."""

    opts = OpenAICompletionsOptions(
        api_key="k", tool_choice="auto", reasoning_effort="medium"
    )
    assert opts.tool_choice == "auto"
    assert opts.reasoning_effort == "medium"


# === §H — Sprint 6b W6 behavior pins (P-57 / P-76 / M-2) ===


def _map_stop_reason_param_cases() -> list[tuple[str, str, str | None]]:
    """Project the fixture ``map_stop_reason_cases`` into parametrize args.

    Each row carries ``(reason_in, expected_stop_reason, expected_error_message_fragment)``.
    ``"_default"`` and ``"null"`` are special — ``null`` maps to ``None``
    on the Aelix side; ``_default`` is exercised via a separate sentinel.
    """

    fixture = _load_fixture()
    rows = fixture["map_stop_reason_cases"]
    cases: list[tuple[str, str, str | None]] = []
    for raw_in, expected in rows.items():
        if raw_in == "_default":
            continue
        if isinstance(expected, str):
            cases.append((raw_in, expected, None))
        else:
            cases.append(
                (raw_in, expected["stopReason"], expected["errorMessage"])
            )
    return cases


@pytest.mark.parametrize(
    ("reason_in", "expected_stop", "expected_error"),
    _map_stop_reason_param_cases(),
)
def test_map_stop_reason_matches_pi_fixture(
    reason_in: str, expected_stop: str, expected_error: str | None
) -> None:
    """Pi parity (P-57 / P-76): every fixture row of ``map_stop_reason_cases``.

    Catches the W6 BLOCKING P-57 drift mechanically — when an adapter
    edit returns ``"tool_use"`` instead of Pi's ``"toolUse"``, this
    parametrize row trips.
    """

    sr_in: str | None = None if reason_in == "null" else reason_in
    sr_out, em_out = _map_stop_reason(sr_in)
    assert sr_out == expected_stop, (
        f"map_stop_reason({reason_in!r}) returned {sr_out!r}, "
        f"expected {expected_stop!r}"
    )
    if expected_error is None:
        assert em_out is None
    else:
        assert em_out is not None and expected_error.lower() in em_out.lower()


def test_map_stop_reason_unknown_fallback() -> None:
    """Pi parity: ``_default`` row → ``error`` + ``Provider finish_reason: <reason>``."""

    sr, em = _map_stop_reason("mystery_finish_reason")
    assert sr == "error"
    assert em is not None and "mystery_finish_reason" in em


@pytest.mark.parametrize(
    ("provider", "base_url", "expected_thinking_format"),
    [
        ("openai", "https://api.openai.com/v1", "openai"),
        ("openrouter", "https://openrouter.ai/api/v1", "openrouter"),
        ("groq", "https://api.groq.com/openai/v1", "openai"),
        ("deepseek", "https://api.deepseek.com/v1", "deepseek"),
        ("xai", "https://api.x.ai/v1", "openai"),
        ("zai", "https://api.z.ai/v1", "zai"),
        ("together", "https://api.together.ai/v1", "together"),
        ("moonshotai", "https://api.moonshot.cn/v1", "openai"),
        ("moonshotai-cn", "https://api.moonshot.cn/v1", "openai"),
        ("cerebras", "https://api.cerebras.ai/v1", "openai"),
        ("opencode", "https://opencode.ai/v1", "openai"),
    ],
)
def test_detect_compat_thinking_format_matches_pi(
    provider: str, base_url: str, expected_thinking_format: str
) -> None:
    """Pi parity: ``detect_compat`` returns the right ``thinking_format`` per row."""

    m = Model(
        api="openai-completions",
        id="model",
        provider=provider,
        base_url=base_url,
    )
    compat = detect_compat(m)
    assert compat.thinking_format == expected_thinking_format


@pytest.mark.parametrize(
    ("provider", "base_url", "expected_max_tokens_field", "expected_non_standard"),
    [
        ("openai", "https://api.openai.com/v1", "max_completion_tokens", False),
        ("together", "https://api.together.ai/v1", "max_tokens", True),
        ("moonshotai", "https://api.moonshot.cn/v1", "max_tokens", True),
        ("cerebras", "https://api.cerebras.ai/v1", "max_completion_tokens", True),
        ("xai", "https://api.x.ai/v1", "max_completion_tokens", True),
        ("deepseek", "https://api.deepseek.com/v1", "max_completion_tokens", True),
        ("opencode", "https://opencode.ai/v1", "max_completion_tokens", True),
    ],
)
def test_detect_compat_max_tokens_and_non_standard(
    provider: str,
    base_url: str,
    expected_max_tokens_field: str,
    expected_non_standard: bool,
) -> None:
    """Pi parity: ``max_tokens_field`` + ``supports_store`` per provider."""

    m = Model(
        api="openai-completions",
        id="model",
        provider=provider,
        base_url=base_url,
    )
    compat = detect_compat(m)
    assert compat.max_tokens_field == expected_max_tokens_field
    # ``supports_store`` and ``supports_developer_role`` flip together on
    # non-standard providers (Pi parity).
    #
    # NOTE (ADR-0118): do NOT add ``"openrouter"`` to the parametrize list above.
    # OpenRouter is NOT ``is_non_standard``, but ``supports_developer_role`` is
    # deliberately forced False for it (it proxies to providers like Parasail
    # that reject the ``developer`` role) — so this generic "flips with
    # non_standard" assertion would fire a FALSE parity failure against correct
    # ADR-0118 behavior. The OpenRouter case is pinned in
    # ``tests/providers/test_openai_completions_openrouter.py``.
    assert compat.supports_store is (not expected_non_standard)
    assert compat.supports_developer_role is (not expected_non_standard)


def test_thinking_format_literal_exhaustively_covered() -> None:
    """Pi parity (M-2 / P-76): every ``thinking_format`` Literal value is reachable.

    Every value in :attr:`OpenAICompletionsCompat.thinking_format` must
    either be reachable from :func:`detect_compat` OR present in
    :data:`COMPAT_DEFERRED_ALLOWLIST`. Catches the M-2 drift mechanically
    — when Pi adds a new thinking format and Aelix forgets to register
    a detection path AND forgets the allowlist entry, this trip-wire
    fires.
    """

    import typing

    field = OpenAICompletionsCompat.__dataclass_fields__["thinking_format"]
    literal_args = set(typing.get_args(field.type))

    # The seed providers / URLs above exercise each non-default detection.
    seeds = [
        ("openai", "https://api.openai.com/v1"),
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("deepseek", "https://api.deepseek.com/v1"),
        ("zai", "https://api.z.ai/v1"),
        ("together", "https://api.together.ai/v1"),
    ]
    reachable = set()
    for provider, base_url in seeds:
        m = Model(
            api="openai-completions",
            id="m",
            provider=provider,
            base_url=base_url,
        )
        reachable.add(detect_compat(m).thinking_format)

    deferred = set(COMPAT_DEFERRED_ALLOWLIST.keys())
    missing = literal_args - reachable - deferred
    assert not missing, (
        f"thinking_format Literal values {missing} are neither reachable "
        f"via detect_compat seeds nor present in COMPAT_DEFERRED_ALLOWLIST "
        f"— add a detection path or document the deferral."
    )


def test_env_api_keys_superset_includes_minimax_rows() -> None:
    """Pi parity (P-79): W0 fixture rows for minimax / minimax-cn present."""

    fixture = _load_fixture()
    pi_map = fixture["env_api_key_mapping"]
    assert "minimax" in pi_map and pi_map["minimax"] == ["MINIMAX_API_KEY"]
    assert "minimax-cn" in pi_map and pi_map["minimax-cn"] == [
        "MINIMAX_CN_API_KEY"
    ]
    assert ENV_API_KEYS["minimax"] == ["MINIMAX_API_KEY"]
    assert ENV_API_KEYS["minimax-cn"] == ["MINIMAX_CN_API_KEY"]
