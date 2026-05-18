"""OpenAI-completions compatibility shim — Sprint 6b (ADR-0047 §F).

Pi parity: ``providers/openai-completions.ts:1062-1153`` (``detectCompat``
+ ``getCompat`` + ``ResolvedOpenAICompletionsCompat``). The 17-field
compat dataclass captures every per-provider quirk the OpenAI adapter
needs at request-build time:

- max-tokens field name (``max_tokens`` vs ``max_completion_tokens``)
- thinking format (``openai``, ``openrouter``, ``deepseek``, …)
- whether the provider supports ``store`` / ``developer`` role /
  ``reasoning_effort`` / strict mode / long cache retention
- the OpenRouter routing dict (when ``baseUrl`` includes
  ``openrouter.ai``)
- the Vercel AI Gateway routing dict (when ``baseUrl`` includes
  ``ai-gateway.vercel.sh``)
- cache-control format hint (``"anthropic"`` for anthropic/* on
  OpenRouter)

URL substring matching mirrors Pi byte-for-byte (see W0 fixture
``pi_openai_completions_734e08e.json``).

Out of scope for Sprint 6b (deferred to Sprint 6d compat zoo per
ADR-0047 §F deferred list):

- ``cloudflare-workers-ai`` / ``cloudflare-ai-gateway`` (needs
  ``_cloudflare.py`` for base-URL rewriting + auth-header swap)
- ``github-copilot`` (needs dynamic vision-input headers)
- ``vercel-ai-gateway`` (the routing dict is detected but full URL
  rewriting + auth-header swap is deferred)
- ``qwen`` / ``qwen-chat-template`` (compat detection requires
  Aliyun-specific URL probing; the thinking_format Literal values are
  reachable via ``model.compat`` overrides but no auto-detection path
  ships in 6b — see ADR-0050 §J)

These deferred targets remain in :data:`COMPAT_DEFERRED_ALLOWLIST` per
ADR-0050 §J.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from aelix_ai.streaming import Model


# Sprint 6b ships full detection for these providers (per ADR-0047 §F):
COMPAT_SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "openai",
    "openrouter",
    "groq",
    "deepseek",
    "xai",
    "zai",
    "together",
    "moonshotai",
    "moonshotai-cn",
    "cerebras",
    "opencode",
)


# Sprint 6d compat-zoo follow-up (ADR-0050 §J). Captures both:
#
#  - provider ids that need richer URL probing / header rewriting (the
#    4 compat-zoo targets), and
#  - ``thinking_format`` Literal values that ship in the dataclass but
#    have no auto-detection path in Sprint 6b (``qwen`` family).
#
# The closure pin asserts every Literal value in
# :attr:`OpenAICompletionsCompat.thinking_format` is reachable from
# :func:`detect_compat` OR present in this allowlist so a future
# detection-path PR cannot silently bypass parity.
COMPAT_DEFERRED_ALLOWLIST: dict[str, str] = {
    "cloudflare-workers-ai": "ADR-0050 §J — Sprint 6d compat zoo",
    "cloudflare-ai-gateway": "ADR-0050 §J — Sprint 6d compat zoo",
    "github-copilot": "ADR-0050 §J — Sprint 6d compat zoo",
    "vercel-ai-gateway": "ADR-0050 §J — Sprint 6d compat zoo",
    "qwen": "ADR-0050 §J — qwen detection deferred",
    "qwen-chat-template": "ADR-0050 §J — qwen detection deferred",
}


@dataclass(frozen=True)
class OpenAICompletionsCompat:
    """Pi parity: ``ResolvedOpenAICompletionsCompat`` (17 fields).

    Every field defaults to the OpenAI baseline (i.e. what the default
    ``api.openai.com`` endpoint accepts). :func:`detect_compat` flips
    each field according to provider / baseUrl detection.
    """

    supports_store: bool = True
    supports_developer_role: bool = True
    supports_reasoning_effort: bool = True
    supports_usage_in_streaming: bool = True
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = (
        "max_completion_tokens"
    )
    requires_tool_result_name: bool = False
    requires_assistant_after_tool_result: bool = False
    requires_thinking_as_text: bool = False
    requires_reasoning_content_on_assistant_messages: bool = False
    thinking_format: Literal[
        "openai",
        "openrouter",
        "together",
        "deepseek",
        "zai",
        "qwen",
        "qwen-chat-template",
    ] = "openai"
    open_router_routing: dict[str, Any] = field(default_factory=dict)
    vercel_gateway_routing: dict[str, Any] = field(default_factory=dict)
    zai_tool_stream: bool = False
    supports_strict_mode: bool = True
    cache_control_format: Literal["anthropic"] | None = None
    send_session_affinity_headers: bool = False
    supports_long_cache_retention: bool = True


def detect_compat(model: Model) -> OpenAICompletionsCompat:
    """Detect compat settings from ``provider`` and ``baseUrl``.

    Pi parity: ``detectCompat`` (``openai-completions.ts:1062-1121``).
    Provider id takes precedence over URL detection; URL fallback uses
    the same substring matches Pi defines.
    """

    provider = (getattr(model, "provider", "") or "").lower()
    base_url = getattr(model, "base_url", "") or ""
    model_id = getattr(model, "id", "") or ""

    is_zai = provider == "zai" or "api.z.ai" in base_url
    is_together = (
        provider == "together"
        or "api.together.ai" in base_url
        or "api.together.xyz" in base_url
    )
    is_moonshot = (
        provider in ("moonshotai", "moonshotai-cn")
        or "api.moonshot." in base_url
    )
    is_cloudflare_workers_ai = (
        provider == "cloudflare-workers-ai"
        or "api.cloudflare.com" in base_url
    )
    is_cloudflare_ai_gateway = (
        provider == "cloudflare-ai-gateway"
        or "gateway.ai.cloudflare.com" in base_url
    )

    is_non_standard = (
        provider == "cerebras"
        or "cerebras.ai" in base_url
        or provider == "xai"
        or "api.x.ai" in base_url
        or is_together
        or "chutes.ai" in base_url
        or "deepseek.com" in base_url
        or is_zai
        or is_moonshot
        or provider == "opencode"
        or "opencode.ai" in base_url
        or is_cloudflare_workers_ai
        or is_cloudflare_ai_gateway
    )

    use_max_tokens = (
        "chutes.ai" in base_url
        or is_moonshot
        or is_cloudflare_ai_gateway
        or is_together
    )

    is_grok = provider == "xai" or "api.x.ai" in base_url
    is_deep_seek = provider == "deepseek" or "deepseek.com" in base_url
    is_openrouter = (
        provider == "openrouter" or "openrouter.ai" in base_url
    )

    cache_control_format: Literal["anthropic"] | None = None
    if provider == "openrouter" and model_id.startswith("anthropic/"):
        cache_control_format = "anthropic"

    if is_deep_seek:
        thinking_format: Literal[
            "openai",
            "openrouter",
            "together",
            "deepseek",
            "zai",
            "qwen",
            "qwen-chat-template",
        ] = "deepseek"
    elif is_zai:
        thinking_format = "zai"
    elif is_together:
        thinking_format = "together"
    elif is_openrouter:
        thinking_format = "openrouter"
    else:
        thinking_format = "openai"

    return OpenAICompletionsCompat(
        supports_store=not is_non_standard,
        supports_developer_role=not is_non_standard,
        supports_reasoning_effort=not (
            is_grok
            or is_zai
            or is_moonshot
            or is_together
            or is_cloudflare_ai_gateway
        ),
        supports_usage_in_streaming=True,
        max_tokens_field="max_tokens" if use_max_tokens else "max_completion_tokens",
        requires_tool_result_name=False,
        requires_assistant_after_tool_result=False,
        requires_thinking_as_text=False,
        requires_reasoning_content_on_assistant_messages=is_deep_seek,
        thinking_format=thinking_format,
        open_router_routing={},
        vercel_gateway_routing={},
        zai_tool_stream=False,
        supports_strict_mode=not (
            is_moonshot or is_together or is_cloudflare_ai_gateway
        ),
        cache_control_format=cache_control_format,
        send_session_affinity_headers=False,
        supports_long_cache_retention=not (
            is_together or is_cloudflare_workers_ai or is_cloudflare_ai_gateway
        ),
    )


def get_compat(model: Model) -> OpenAICompletionsCompat:
    """Merge an explicit ``model.compat`` (if any) onto the detected baseline.

    Pi parity: ``getCompat`` (``openai-completions.ts:1127-1153``).
    Aelix's :class:`Model` does not carry a ``compat`` field today; the
    helper still accepts an opportunistic ``model.compat`` attribute so
    a test fixture or future port can override detection without
    reworking this seam.
    """

    detected = detect_compat(model)
    override = getattr(model, "compat", None)
    if override is None:
        return detected

    # ``override`` may be a dict (most likely) or a dataclass-style
    # object with snake_case attrs. Dict overrides may use camelCase Pi
    # names (``openRouterRouting`` etc.) for byte-parity with the Pi
    # SDK shape; we accept both spellings.
    _CAMEL_ALIASES: dict[str, str] = {
        "supports_store": "supportsStore",
        "supports_developer_role": "supportsDeveloperRole",
        "supports_reasoning_effort": "supportsReasoningEffort",
        "supports_usage_in_streaming": "supportsUsageInStreaming",
        "max_tokens_field": "maxTokensField",
        "requires_tool_result_name": "requiresToolResultName",
        "requires_assistant_after_tool_result": "requiresAssistantAfterToolResult",
        "requires_thinking_as_text": "requiresThinkingAsText",
        "requires_reasoning_content_on_assistant_messages": (
            "requiresReasoningContentOnAssistantMessages"
        ),
        "thinking_format": "thinkingFormat",
        "open_router_routing": "openRouterRouting",
        "vercel_gateway_routing": "vercelGatewayRouting",
        "zai_tool_stream": "zaiToolStream",
        "supports_strict_mode": "supportsStrictMode",
        "cache_control_format": "cacheControlFormat",
        "send_session_affinity_headers": "sendSessionAffinityHeaders",
        "supports_long_cache_retention": "supportsLongCacheRetention",
    }

    def _pick(name: str, default: Any) -> Any:
        if isinstance(override, dict):
            if name in override:
                return override[name]
            camel = _CAMEL_ALIASES.get(name)
            if camel is not None and camel in override:
                return override[camel]
            return default
        return getattr(override, name, default)

    return replace(
        detected,
        supports_store=_pick("supports_store", detected.supports_store),
        supports_developer_role=_pick(
            "supports_developer_role", detected.supports_developer_role
        ),
        supports_reasoning_effort=_pick(
            "supports_reasoning_effort", detected.supports_reasoning_effort
        ),
        supports_usage_in_streaming=_pick(
            "supports_usage_in_streaming",
            detected.supports_usage_in_streaming,
        ),
        max_tokens_field=_pick(
            "max_tokens_field", detected.max_tokens_field
        ),
        requires_tool_result_name=_pick(
            "requires_tool_result_name", detected.requires_tool_result_name
        ),
        requires_assistant_after_tool_result=_pick(
            "requires_assistant_after_tool_result",
            detected.requires_assistant_after_tool_result,
        ),
        requires_thinking_as_text=_pick(
            "requires_thinking_as_text",
            detected.requires_thinking_as_text,
        ),
        requires_reasoning_content_on_assistant_messages=_pick(
            "requires_reasoning_content_on_assistant_messages",
            detected.requires_reasoning_content_on_assistant_messages,
        ),
        thinking_format=_pick(
            "thinking_format", detected.thinking_format
        ),
        open_router_routing=_pick(
            "open_router_routing", detected.open_router_routing
        )
        or {},
        vercel_gateway_routing=_pick(
            "vercel_gateway_routing", detected.vercel_gateway_routing
        )
        or {},
        zai_tool_stream=_pick(
            "zai_tool_stream", detected.zai_tool_stream
        ),
        supports_strict_mode=_pick(
            "supports_strict_mode", detected.supports_strict_mode
        ),
        cache_control_format=_pick(
            "cache_control_format", detected.cache_control_format
        ),
        send_session_affinity_headers=_pick(
            "send_session_affinity_headers",
            detected.send_session_affinity_headers,
        ),
        supports_long_cache_retention=_pick(
            "supports_long_cache_retention",
            detected.supports_long_cache_retention,
        ),
    )


__all__ = [
    "COMPAT_DEFERRED_ALLOWLIST",
    "COMPAT_SUPPORTED_PROVIDERS",
    "OpenAICompletionsCompat",
    "detect_compat",
    "get_compat",
]
