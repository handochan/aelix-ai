"""Anthropic-messages compatibility shim — ADR-0190 (#36 anthropic side).

Pi parity: ``providers/anthropic.ts:167-178`` (``getAnthropicCompat`` +
``AnthropicMessagesCompat``). Mirrors the structure of the sibling
:mod:`aelix_ai.providers._openai_compat` (same frozen-dataclass +
:func:`detect_compat` + :func:`get_compat` camelCase-alias-merge shape).

Unlike the 17-field OpenAI compat, ``getAnthropicCompat`` returns exactly
**four** auto-detected fields, all defaulting to the ``api.anthropic.com``
baseline (every field ``True`` except ``send_session_affinity_headers``,
which is ``False``). :func:`detect_compat` flips them for ``fireworks``
(all off, affinity on) and ``cloudflare-ai-gateway`` anthropic models
(affinity on).

Wiring status (ADR-0190 §"Detected-but-deferred"):

- :attr:`~AnthropicMessagesCompat.send_session_affinity_headers` **is
  consumed** by the adapter: on the API-key client-build branch, when a
  ``session_id`` is present and this flag is set, an ``x-session-affinity``
  header is injected (mirrors ``anthropic.ts:862-863``).
- :attr:`~AnthropicMessagesCompat.supports_long_cache_retention` and
  :attr:`~AnthropicMessagesCompat.supports_cache_control_on_tools` are
  **detected-but-inert** — aelix's native Anthropic ``build_params`` emits
  no ``cache_control`` breakpoints yet, so there is nothing to gate. Wiring
  them requires porting pi's cache-control-breakpoint subsystem; deferred
  to a follow-up issue (ADR-0190).
- :attr:`~AnthropicMessagesCompat.supports_eager_tool_input_streaming` is
  **detected-but-not-wired** — consuming it means either per-tool
  ``eager_input_streaming`` or the ``fine-grained-tool-streaming`` beta,
  whose merge must not clobber the interleaved-thinking/OAuth betas;
  deferred with the cache work (ADR-0190).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aelix_ai.streaming import Model


@dataclass(frozen=True)
class AnthropicMessagesCompat:
    """Pi parity: ``AnthropicMessagesCompat`` (4 fields).

    Every field defaults to the ``api.anthropic.com`` baseline (i.e. what
    the default Anthropic Messages endpoint accepts). :func:`detect_compat`
    flips each field according to provider / baseUrl detection.
    """

    supports_eager_tool_input_streaming: bool = True
    supports_long_cache_retention: bool = True
    send_session_affinity_headers: bool = False
    supports_cache_control_on_tools: bool = True


def detect_compat(model: Model) -> AnthropicMessagesCompat:
    """Detect compat settings from ``provider`` and ``baseUrl``.

    Pi parity: ``getAnthropicCompat`` (``anthropic.ts:167-177``). Fireworks
    disables eager tool-input streaming, long cache retention, and
    cache-control on tools, and enables session-affinity headers.
    ``cloudflare-ai-gateway`` anthropic models (``"anthropic"`` in the
    baseUrl) also enable session-affinity headers.
    """

    provider = (getattr(model, "provider", "") or "").lower()
    base_url = getattr(model, "base_url", "") or ""

    is_fireworks = provider == "fireworks"
    is_cloudflare_ai_gateway_anthropic = (
        provider == "cloudflare-ai-gateway" and "anthropic" in base_url
    )

    return AnthropicMessagesCompat(
        supports_eager_tool_input_streaming=not is_fireworks,
        supports_long_cache_retention=not is_fireworks,
        send_session_affinity_headers=(
            is_fireworks or is_cloudflare_ai_gateway_anthropic
        ),
        supports_cache_control_on_tools=not is_fireworks,
    )


def get_compat(model: Model) -> AnthropicMessagesCompat:
    """Merge an explicit ``model.compat`` (if any) onto the detected baseline.

    Pi parity: ``getAnthropicCompat`` (``anthropic.ts:172-178``) — each
    field is ``model.compat?.<field> ?? <detected>``. Mirrors
    :func:`aelix_ai.providers._openai_compat.get_compat`.
    """

    detected = detect_compat(model)
    override = getattr(model, "compat", None)
    if override is None:
        return detected

    # ``override`` may be a dict (most likely) or a dataclass-style object
    # with snake_case attrs. Dict overrides may use camelCase Pi names
    # (``supportsEagerToolInputStreaming`` etc.) for byte-parity with the Pi
    # SDK shape; we accept both spellings.
    _CAMEL_ALIASES: dict[str, str] = {
        "supports_eager_tool_input_streaming": "supportsEagerToolInputStreaming",
        "supports_long_cache_retention": "supportsLongCacheRetention",
        "send_session_affinity_headers": "sendSessionAffinityHeaders",
        "supports_cache_control_on_tools": "supportsCacheControlOnTools",
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
        supports_eager_tool_input_streaming=_pick(
            "supports_eager_tool_input_streaming",
            detected.supports_eager_tool_input_streaming,
        ),
        supports_long_cache_retention=_pick(
            "supports_long_cache_retention",
            detected.supports_long_cache_retention,
        ),
        send_session_affinity_headers=_pick(
            "send_session_affinity_headers",
            detected.send_session_affinity_headers,
        ),
        supports_cache_control_on_tools=_pick(
            "supports_cache_control_on_tools",
            detected.supports_cache_control_on_tools,
        ),
    )


__all__ = [
    "AnthropicMessagesCompat",
    "detect_compat",
    "get_compat",
]
