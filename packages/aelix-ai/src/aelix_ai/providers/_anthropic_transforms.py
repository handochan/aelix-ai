"""Anthropic message-shape helpers — Sprint 6a (ADR-0045 §B).

Ports the relevant portions of Pi ``providers/anthropic.ts`` +
``providers/transform-messages.ts`` so the adapter body in
``providers/anthropic.py`` stays readable.

The function shapes mirror Pi's helpers:

- :func:`transform_messages` — convert Aelix ``Message`` → Anthropic SDK
  ``MessageParam`` list (user / assistant / tool_result).
- :func:`build_params` — assemble the SDK ``messages.stream`` kwargs
  (system prompt, tools, max_tokens, …).
- :func:`map_stop_reason` — Anthropic ``stop_reason`` → Aelix
  ``StopReason`` (``end_turn``/``tool_use``/``max_tokens``/``error``).
"""

from __future__ import annotations

from typing import Any

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import Model

# Anthropic ``stop_reason`` → Aelix-shaped strings. Sprint 6b W6 (P-57):
# Aelix now uses Pi's ``"toolUse"`` spelling verbatim across every adapter.
# The agent loop only compares against ``"error" | "aborted"`` so the
# spelling change is invisible to terminal-detection.
_ANTHROPIC_STOP_REASON_MAP: dict[str | None, str] = {
    "end_turn": "end_turn",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "toolUse",
    "pause_turn": "stop",
    "refusal": "error",
}


def map_stop_reason(anthropic_reason: str | None) -> str:
    """Map Anthropic SDK ``stop_reason`` to Aelix ``AssistantMessage.stop_reason``.

    Unknown reasons fall through as the raw string so callers can
    inspect them; the agent loop's terminal-detection compares against
    ``("error", "aborted")`` so a benign unknown reason continues the loop.
    """

    return _ANTHROPIC_STOP_REASON_MAP.get(anthropic_reason, anthropic_reason or "stop")


def _content_blocks_to_anthropic(
    blocks: list[Any],
) -> list[dict[str, Any]]:
    """Convert Aelix content blocks → Anthropic SDK content blocks."""

    out: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextContent):
            out.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageContent):
            # Anthropic expects {type:image, source:{type, media_type, data}}.
            # Sprint 6b (P-61): prefer the new ``mime_type`` + ``data``
            # split fields; fall back to the legacy ``source`` data-URL /
            # base64 string when ``data`` is empty so pre-6b callers keep
            # working.
            mime = block.mime_type or "image/png"
            data = block.data if block.data else block.source
            out.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": data,
                    },
                }
            )
        elif isinstance(block, ToolCallContent):
            out.append(
                {
                    "type": "tool_use",
                    "id": block.tool_call_id,
                    "name": block.tool_name,
                    "input": block.input,
                }
            )
    return out


def transform_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert Aelix ``Message`` list → Anthropic SDK ``MessageParam`` list.

    Pi parity: ``providers/transform-messages.ts``. Tool results are
    grouped into the most recent ``user`` message (Anthropic requires
    tool_result blocks inside a user message) per Pi's behavior.
    """

    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            out.append(
                {
                    "role": "user",
                    "content": _content_blocks_to_anthropic(list(msg.content)),
                }
            )
        elif isinstance(msg, AssistantMessage):
            out.append(
                {
                    "role": "assistant",
                    "content": _content_blocks_to_anthropic(list(msg.content)),
                }
            )
        elif isinstance(msg, ToolResultMessage):
            # Anthropic expects tool_result blocks inside a user message.
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id,
                "content": _content_blocks_to_anthropic(list(msg.content)),
                "is_error": msg.is_error,
            }
            # Coalesce into preceding user message when possible (Pi parity).
            if out and out[-1]["role"] == "user" and isinstance(
                out[-1]["content"], list
            ):
                out[-1]["content"].append(tool_result_block)
            else:
                out.append(
                    {
                        "role": "user",
                        "content": [tool_result_block],
                    }
                )
    return out


def build_params(
    model: Model,
    system_prompt: str,
    messages: list[Message],
    tools: list[Any],
    *,
    max_tokens: int = 4096,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble ``messages.create`` kwargs for the Anthropic SDK.

    Pi parity: ``providers/anthropic.ts:489-505``-region ``buildParams``.

    ``extra`` lets callers inject ``cache_retention`` / ``thinking`` /
    other provider-specific top-level kwargs without forking this helper.
    """

    params: dict[str, Any] = {
        "model": model.id or model.name,
        "max_tokens": max_tokens,
        "messages": transform_messages(messages),
    }
    if system_prompt:
        params["system"] = system_prompt
    if tools:
        sdk_tools: list[dict[str, Any]] = []
        for tool in tools:
            if hasattr(tool, "to_anthropic_param"):
                sdk_tools.append(tool.to_anthropic_param())
            else:
                # Generic ``Tool``-shaped object — best effort.
                schema = getattr(tool, "input_schema", None) or getattr(
                    tool, "parameters", {}
                )
                sdk_tools.append(
                    {
                        "name": getattr(tool, "name", "unknown"),
                        "description": getattr(tool, "description", ""),
                        "input_schema": schema or {"type": "object", "properties": {}},
                    }
                )
        params["tools"] = sdk_tools
    if extra:
        params.update(extra)
    return params


# === ADR-0135 (P0 #1): Anthropic extended-thinking resolution ===
#
# Pi parity (all anchors at SHA 734e08e): ``providers/anthropic.ts``
# (``streamSimpleAnthropic`` 728-767, ``mapThinkingLevelToEffort`` 708-726,
# ``supportsAdaptiveThinking`` 692-702, ``buildParams`` thinking block
# 939-968) + ``providers/simple-options.ts`` (``clampReasoning`` 22-24,
# ``adjustMaxTokensForThinking`` 26-50).

#: Pi ``INTERLEAVED_THINKING_BETA`` (anthropic.ts:165). Sent only for
#: budget-based (older) reasoning models — adaptive models (Opus 4.6+/
#: Sonnet 4.6) have interleaved thinking built-in, so pi skips the header.
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"

#: Pi default thinking budgets (simple-options.ts:32-37). ``xhigh`` is not a
#: budget tier — :func:`clamp_reasoning` collapses it onto ``high``.
_DEFAULT_THINKING_BUDGETS: dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
}
_MIN_OUTPUT_TOKENS = 1024


def supports_adaptive_thinking(model_id: str) -> bool:
    """Pi parity ``supportsAdaptiveThinking`` (anthropic.ts:692-702).

    Opus 4.6+/Sonnet 4.6 use *adaptive* thinking (Claude decides how much to
    think, steered by an ``effort`` level); older reasoning models use
    *budget-based* thinking (an explicit ``budget_tokens`` allowance).
    """

    mid = model_id or ""
    return any(
        marker in mid
        for marker in (
            "opus-4-6",
            "opus-4.6",
            "opus-4-7",
            "opus-4.7",
            "sonnet-4-6",
            "sonnet-4.6",
        )
    )


def map_thinking_level_to_effort(model: Model, level: str | None) -> str:
    """Pi parity ``mapThinkingLevelToEffort`` (anthropic.ts:708-726).

    Prefer the model's ``thinkingLevelMap`` native string (e.g. ``xhigh`` →
    ``"max"`` on Opus 4.6, ``"xhigh"`` on Opus 4.7); otherwise fall back to a
    coarse mapping (minimal/low → ``"low"``, medium → ``"medium"``, else
    ``"high"``).
    """

    thinking_map = model.thinking_level_map or {}
    mapped = thinking_map.get(level) if level else None
    if isinstance(mapped, str):
        return mapped
    if level in ("minimal", "low"):
        return "low"
    if level == "medium":
        return "medium"
    if level == "high":
        return "high"
    return "high"


def clamp_reasoning(level: str) -> str:
    """Pi parity ``clampReasoning`` (simple-options.ts:22-24)."""

    return "high" if level == "xhigh" else level


def adjust_max_tokens_for_thinking(
    base_max_tokens: int,
    model_max_tokens: int,
    reasoning_level: str,
    custom_budgets: dict[str, int] | None = None,
) -> tuple[int, int]:
    """Pi parity ``adjustMaxTokensForThinking`` (simple-options.ts:26-50).

    Returns ``(max_tokens, thinking_budget)``. The budget is carved out of
    ``max_tokens`` and shrunk to leave at least ``_MIN_OUTPUT_TOKENS`` of
    room for the visible answer.
    """

    budgets = {**_DEFAULT_THINKING_BUDGETS, **(custom_budgets or {})}
    level = clamp_reasoning(reasoning_level)
    thinking_budget = budgets.get(level, _DEFAULT_THINKING_BUDGETS["medium"])
    max_tokens = min(base_max_tokens + thinking_budget, model_max_tokens)
    if max_tokens <= thinking_budget:
        thinking_budget = max(0, max_tokens - _MIN_OUTPUT_TOKENS)
    return max_tokens, thinking_budget


def resolve_anthropic_thinking(
    model: Model,
    reasoning: str | None,
    default_max_tokens: int,
) -> tuple[dict[str, Any], int, bool]:
    """Pi parity: ``streamSimpleAnthropic`` + ``buildParams`` thinking block.

    Given the per-turn thinking level (``reasoning``), return
    ``(extra_params, max_tokens, needs_interleaved_beta)`` where
    ``extra_params`` carries the ``thinking`` request object (plus
    ``output_config`` for adaptive models) to merge into the Anthropic call.

    Behaviour (pi-faithful):
      * non-reasoning model → ``{}`` (never send a thinking param);
      * reasoning model, no level → ``{"thinking": {"type": "disabled"}}``;
      * adaptive model → ``thinking.type = "adaptive"`` + ``output_config``;
      * older reasoning model → ``thinking.type = "enabled"`` with a
        ``budget_tokens`` carved from ``max_tokens``.

    ``needs_interleaved_beta`` is True ONLY on the active budget-thinking path
    (non-adaptive reasoning model with a level set). **Deliberate narrower scope
    than pi:** pi sends the beta for EVERY non-adaptive model — even
    non-reasoning / thinking "off" — via ``(interleavedThinking ?? true) &&
    !supportsAdaptiveThinking`` (anthropic.ts:479, 784), and lets a caller's
    ``anthropic-beta`` *replace* it via ``mergeHeaders`` ordering. Aelix instead
    preserves its established "caller ``anthropic-beta`` wins" setdefault
    contract (tests/oauth) and gates the beta on active thinking — the only case
    where interleaved thinking is functional. Full pi parity (universal beta +
    mergeHeaders replace semantics) is an OAuth-header-architecture change
    tracked as a follow-up, out of ADR-0135's reasoning scope.
    """

    model_id = model.id or model.name or ""

    if not getattr(model, "reasoning", False):
        return {}, default_max_tokens, False

    if not reasoning:
        return {"thinking": {"type": "disabled"}}, default_max_tokens, False

    # Pi defaults thinking display to "summarized" so newer models match the
    # API default older Claude 4 models already use (anthropic.ts:943-945).
    display = "summarized"

    if supports_adaptive_thinking(model_id):
        extra: dict[str, Any] = {
            "thinking": {"type": "adaptive", "display": display}
        }
        effort = map_thinking_level_to_effort(model, reasoning)
        if effort:
            extra["output_config"] = {"effort": effort}
        return extra, default_max_tokens, False  # adaptive: beta built-in

    # Budget-based (older) reasoning models. Pi uses ``base.maxTokens``
    # (= options.maxTokens ?? model.maxTokens) as the budget base and
    # ``model.maxTokens`` as the hard clamp (simple-options.ts:26-50). P0 #6
    # plumbs ``options.maxTokens`` through ``default_max_tokens`` at the call
    # site (anthropic.py), so the base honors a caller override (e.g. the
    # compaction summarizer cap); the clamp stays the model cap.
    base_max = default_max_tokens
    model_clamp = model.max_tokens or default_max_tokens
    max_tokens, budget = adjust_max_tokens_for_thinking(
        base_max, model_clamp, reasoning
    )
    extra = {
        "thinking": {
            "type": "enabled",
            "budget_tokens": budget or 1024,
            "display": display,
        }
    }
    return extra, max_tokens, True  # budget thinking active → interleaved beta


def is_oauth_token(api_key: str | None) -> bool:
    """Pi parity ``providers/anthropic.ts:769`` — detect Anthropic OAuth bearer.

    Anthropic OAuth tokens (issued via the claude.ai login flow) all
    start with ``sk-ant-oat``. The SDK accepts them as API keys but the
    full OAuth flow (token refresh, etc.) is owned by Sprint 6c — Sprint
    6a rejects OAuth tokens with ``AgentHarnessError("auth", ...)``.
    """

    return bool(api_key and api_key.startswith("sk-ant-oat"))


__all__ = [
    "build_params",
    "is_oauth_token",
    "map_stop_reason",
    "transform_messages",
]
