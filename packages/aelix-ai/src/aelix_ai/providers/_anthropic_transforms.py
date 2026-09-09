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

import re
from typing import Any

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.models import get_supported_thinking_levels
from aelix_ai.providers._transform_messages import (
    transform_messages as shared_transform_messages,
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


#: pi #5666 (anthropic-messages.ts:1227) — generic refusal text used when the
#: provider supplies a ``refusal`` stop reason without a ``stop_details``
#: explanation.
_REFUSAL_FALLBACK_MESSAGE = "The model refused to complete the request"


def map_stop_reason_with_details(
    anthropic_reason: str | None,
    stop_details: Any | None = None,
) -> tuple[str, str | None]:
    """Pi parity ``mapStopReason`` (anthropic-messages.ts:1213-1235).

    Returns ``(stop_reason, error_message)``. pi #5666: a ``refusal`` stop
    reason is mapped to ``"error"`` *and* the provider's ``stop_details``
    object carries the human-readable reason — preserve its ``explanation`` text
    into the surfaced error message so observers see *why* the model refused,
    instead of dropping it for a generic "unknown error". ``stop_details`` may be
    an SDK object (``.explanation``) or a plain dict; falls back to a generic
    refusal message when no explanation is present.
    """

    reason = _ANTHROPIC_STOP_REASON_MAP.get(
        anthropic_reason, anthropic_reason or "stop"
    )
    if anthropic_reason == "refusal":
        explanation: str | None = None
        if stop_details is not None:
            explanation = getattr(stop_details, "explanation", None)
            if explanation is None and isinstance(stop_details, dict):
                explanation = stop_details.get("explanation")
        return reason, (explanation or _REFUSAL_FALLBACK_MESSAGE)
    return reason, None


def map_stop_reason(anthropic_reason: str | None) -> str:
    """Map Anthropic SDK ``stop_reason`` to Aelix ``AssistantMessage.stop_reason``.

    Unknown reasons fall through as the raw string so callers can
    inspect them; the agent loop's terminal-detection compares against
    ``("error", "aborted")`` so a benign unknown reason continues the loop.

    Thin wrapper over :func:`map_stop_reason_with_details` that drops the
    refusal error text — kept for callers that only need the mapped reason.
    """

    return map_stop_reason_with_details(anthropic_reason)[0]


def supports_temperature(model: Model) -> bool:
    """Pi parity ``getAnthropicCompat().supportsTemperature`` (anthropic-messages.ts:178).

    Anthropic deprecated the ``temperature`` sampling param on Claude Opus 4.7+
    (catalog ``compat.supportsTemperature: false``); sending it there is rejected
    and conflicts with adaptive thinking. Mirrors pi's ``model.compat?.
    supportsTemperature ?? true`` — defaults to ``True`` when the model carries no
    compat flag.
    """

    compat = getattr(model, "compat", None) or {}
    value = compat.get("supportsTemperature")
    return True if value is None else bool(value)


def _content_blocks_to_anthropic(
    blocks: list[Any],
) -> list[dict[str, Any]]:
    """Convert Aelix content blocks → Anthropic SDK content blocks."""

    out: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, TextContent):
            out.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingContent):
            # ADR-0190: replay thinking blocks in pi's exact 4-way order
            # (anthropic.ts:1056-1080). Thinking blocks were captured first,
            # so they serialize ahead of text/tool blocks in natural order.
            if block.redacted:
                # Redacted: echo the opaque payload back as redacted_thinking.
                out.append(
                    {
                        "type": "redacted_thinking",
                        "data": block.thinking_signature,
                    }
                )
            elif not block.thinking.strip():
                # Empty thinking (e.g. a start-only block) — skip entirely.
                continue
            elif not (block.thinking_signature or "").strip():
                # Missing/empty signature (e.g. aborted stream): downgrade to
                # a plain text block so the API doesn't reject an unsigned
                # thinking block (and Claude doesn't mimic <thinking> tags).
                # UNCONDITIONAL (pi's inline "allowEmptySignature" behavior,
                # anthropic.ts:1069) — NOT compat-gated. Still reachable after
                # the shared transform, which KEEPS a same-model non-empty
                # unsigned thinking block (_transform_messages.py:185-188).
                out.append({"type": "text", "text": block.thinking})
            else:
                out.append(
                    {
                        "type": "thinking",
                        "thinking": block.thinking,
                        "signature": block.thinking_signature,
                    }
                )
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


def _normalize_anthropic_tool_call_id(
    tool_call_id: str,
    model: Model,
    assistant: AssistantMessage,
) -> str:
    """Pi parity ``normalizeToolCallId`` (anthropic.ts:990-991).

    Coerce a tool-call id to Anthropic's allowed charset (``[a-zA-Z0-9_-]``)
    and 64-char cap. pi applies this unconditionally; native ``toolu_…`` ids
    are no-ops, so only cross-model ids (rewritten on the not-same-model path
    by the shared transform) actually change.
    """

    return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64]


def transform_messages(
    messages: list[Message], model: Model
) -> list[dict[str, Any]]:
    """Convert Aelix ``Message`` list → Anthropic SDK ``MessageParam`` list.

    Pi parity: ``providers/transform-messages.ts``. Tool results are
    grouped into the most recent ``user`` message (Anthropic requires
    tool_result blocks inside a user message) per Pi's behavior.

    ADR-0190: route through the shared cross-provider transform FIRST
    (same-model thinking preservation, cross-model drop/convert, orphan
    tool-call synthesis, errored/aborted turn drop, and tool-call-id
    normalization via :func:`_normalize_anthropic_tool_call_id`), then run
    the local per-shape map + tool_result coalescing over its **output** —
    which may have inserted synthetic :class:`ToolResultMessage`\\ s and
    dropped errored turns. Mirrors ``openai_completions.py:339`` /
    ``_google_shared.py:259``.
    """

    normalized = shared_transform_messages(
        messages,
        model,
        normalize_tool_call_id=_normalize_anthropic_tool_call_id,
    )

    out: list[dict[str, Any]] = []
    for msg in normalized:
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
    temperature: float | None = None,
    thinking_enabled: bool = False,
) -> dict[str, Any]:
    """Assemble ``messages.create`` kwargs for the Anthropic SDK.

    Pi parity: ``providers/anthropic.ts:489-505``-region ``buildParams``.

    ``extra`` lets callers inject ``cache_retention`` / ``thinking`` /
    other provider-specific top-level kwargs without forking this helper.

    ``temperature`` is forwarded only when supplied, ``thinking_enabled`` is
    False, and the model supports it (pi #5251, anthropic-messages.ts:943-945):
    temperature is incompatible with extended thinking and unsupported on
    Claude Opus 4.7+.
    """

    params: dict[str, Any] = {
        "model": model.id or model.name,
        "max_tokens": max_tokens,
        "messages": transform_messages(messages, model),
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
    # pi #5251 (anthropic-messages.ts:943-945): temperature is incompatible with
    # extended thinking and unsupported on Claude Opus 4.7+ — send it only when a
    # value is supplied, thinking is off, and the model's compat allows it.
    if (
        temperature is not None
        and not thinking_enabled
        and supports_temperature(model)
    ):
        params["temperature"] = temperature
    if extra:
        params.update(extra)
    return params


# === ADR-0135 (P0 #1): Anthropic extended-thinking resolution ===
#
# Pi parity (all anchors at SHA 734e08e): ``providers/anthropic.ts``
# (``streamSimpleAnthropic`` 728-767, ``mapThinkingLevelToEffort`` 708-726,
# ``supportsAdaptiveThinking`` 692-702, ``buildParams`` thinking block
# 939-968) + ``providers/simple-options.ts`` (``clampReasoning`` 22-24,
# ``adjustMaxTokensForThinking`` 26-50). Five of those diverge as of #258:
# ``clampReasoning`` (see :func:`clamp_reasoning`), the budget table
# (:data:`_DEFAULT_THINKING_BUDGETS`), the carve plus its ``|| 1024``
# fallback (see :func:`adjust_max_tokens_for_thinking` and the disabled
# branch of :func:`resolve_anthropic_thinking`), the adaptive membership test
# (:func:`supports_adaptive_thinking`, which reads the catalog before its
# marker regex) and the handling of a literal ``"off"``
# (:func:`resolve_anthropic_thinking`). ADR-0235: pi is a verified reference,
# not a target — divergence needs a reason, not an ADR.

#: Pi ``INTERLEAVED_THINKING_BETA`` (anthropic.ts:165). Sent only for
#: budget-based reasoning models — adaptive models (Opus 4.6 and newer, see
#: :func:`supports_adaptive_thinking`) have interleaved thinking built-in, so
#: pi skips the header.
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"

#: Pi default thinking budgets (simple-options.ts:32-37) plus a fifth row.
#: **Divergence (#250, ADR-0135 amendment):** pi has four rows and folds
#: ``xhigh`` onto ``high``; aelix offers ``xhigh`` in the picker, so on
#: 0985fcf both tiers sent ``budget_tokens: 16384`` while the statusline
#: claimed ``xhigh`` (measured on ``anthropic/claude-opus-5``). 32768 is
#: twice ``high`` and the value ``_BUDGET_2_5_PRO`` uses for its top tier;
#: 18 of the 20 catalog rows on this path cap at 64000/128000, so it fits.
_DEFAULT_THINKING_BUDGETS: dict[str, int] = {
    "minimal": 1024,
    "low": 2048,
    "medium": 8192,
    "high": 16384,
    "xhigh": 32768,
}
_MIN_OUTPUT_TOKENS = 1024
#: Anthropic's own floor for ``thinking.budget_tokens`` on the budget path:
#: the value must be **at least 1024** and **strictly less than**
#: ``max_tokens``; anything else is a 400, not a smaller answer. **MEASURED**
#: live against ``api.anthropic.com`` on 2026-09-09 (#250 beta2 re-review),
#: on ``claude-haiku-4-5`` because it is a first-party row that still takes
#: this path — ``budget_tokens: 512`` with ``max_tokens: 4096`` returns
#: *"thinking.enabled.budget_tokens: Input should be greater than or equal to
#: 1024"* (``req_011CerQpihKtfUSfimNLoaPZ``) and ``budget_tokens: 2048`` with
#: ``max_tokens: 2048`` returns *"``max_tokens`` must be greater than
#: ``thinking.budget_tokens``"* (``req_011CerQpkFasPMAjVBdaPMDK``), while
#: ``budget_tokens: 1024`` under ``max_tokens: 2048`` answers with a
#: ``thinking`` block (``req_011CerQpmN4cr9DZAtFwqYzn``). Both halves of the
#: rule are therefore observed, not read. Numerically equal to
#: :data:`_MIN_OUTPUT_TOKENS` today, kept separate because it is a different
#: fact — one is the API's rule, the other is aelix's reserve for the visible
#: answer.
_MIN_THINKING_BUDGET = 1024


#: The Claude generations whose ``thinking`` param must be ``adaptive`` — the
#: FALLBACK for rows the catalog has not flagged, see
#: :func:`supports_adaptive_thinking`. Matches both id spellings the catalog
#: uses (``claude-opus-4-8`` first-party, ``claude-opus-4.8`` on
#: ``github-copilot`` / ``vercel-ai-gateway``) and any suffix
#: (``anthropic/claude-opus-5-fast``). ``haiku`` is deliberately absent:
#: ``claude-haiku-4-5`` is a budget row and is the row the ``budget_tokens``
#: floor was measured on.
_ADAPTIVE_FAMILY_RE = re.compile(r"claude-(?:opus|sonnet|fable)-(?:4[-.][678]|5)")

#: The Claude generation that cannot express "thinking off" AT ALL — the
#: FALLBACK for rows whose ``thinkingLevelMap`` does not declare it, see
#: :func:`supports_thinking_off`.
_ALWAYS_THINKING_FAMILY_RE = re.compile(r"claude-fable-\d")


def supports_adaptive_thinking(model: Model) -> bool:
    """Does this row take ``thinking.type = "adaptive"`` instead of a budget?

    Adaptive models let Claude decide how much to think, steered by
    ``output_config.effort``; older reasoning models take an explicit
    ``budget_tokens`` allowance. Sending the wrong one is a 400, not a
    degraded answer.

    **The catalog decides when it has an opinion** (#258). ``compat.
    forceAdaptiveThinking`` is the field upstream ships for exactly this
    question; a row that carries it is believed in both directions, so a
    catalog that sets it — to ``true`` on a row no fallback would catch, or to
    ``false`` on one it would — moves that row with no code change. 12 of the
    rows this release ships carry it: the six first-party
    ``anthropic`` ids (``claude-opus-4-7``, ``claude-opus-4-8``,
    ``claude-opus-5``, ``claude-sonnet-5``, ``claude-fable-5``,
    ``claude-fable-5-1``) plus ``cloudflare-ai-gateway`` and ``opencode``
    mirrors (counted 2026-09-09).

    **Why a family fallback survives.** Pi's ``supportsAdaptiveThinking``
    (anthropic.ts:692-702) is a literal marker list of ``opus-4-6`` /
    ``opus-4-7`` / ``sonnet-4-6``, and dropping it in favour of the flag alone
    would have pushed rows the catalog does NOT flag back onto the budget
    path — measured against the catalog at this commit, 25 of the 37 adaptive
    ``anthropic-messages`` rows reach this branch only through the regex, and
    two of them are ``claude-opus-4.7`` (``github-copilot``,
    ``vercel-ai-gateway``), the very generation whose first-party twin is the
    #258 control. The mirrors are the same models behind a proxy, so the
    fallback covers the generations Anthropic documents as adaptive
    (4.6 / 4.7 / 4.8 and the 5 family) rather than only the two the old marker
    list knew. It is a fallback and not the rule: a flagged row never reaches
    it.

    **What was measured** (2026-09-09, ``api.anthropic.com``, the request
    Aelix builds, at ``high`` and at ``xhigh``). Every budget-path level on
    ``claude-opus-5`` (``req_011CerQpNc69m6CAsK93KroH``,
    ``req_011CerQpLuQC5P5Xaw7MzyVQ``), ``claude-fable-5``
    (``req_011CerQpRVUJivivpJ9SmDHp``, ``req_011CerQpQ3uQqfCeUsKaPg9M``),
    ``claude-opus-4-8`` (``req_011CerQpUWYMoCu4L9t8WS7w``,
    ``req_011CerQpSw3MHtbeDFKJYEpi``) and ``claude-sonnet-5``
    (``req_011CerQpXRAezGeumCSuhkSk``, ``req_011CerQpVws2QN9E2uMrD6yC``)
    returned ``400 invalid_request_error``: *"``thinking.type.enabled`` is not
    supported for this model. Use ``thinking.type.adaptive`` and
    ``output_config.effort`` to control thinking behavior."* ``claude-opus-4-7``
    — carried by the old marker list — answered through the adaptive branch in
    the same run (``req_011CerQpYrVRK4GK5pf5sJ8x``,
    ``req_011CerQpdX2jsWYUfDcBa1x6``), so the branch was sound and only its
    membership test was wrong. The same 400 lands on 0985fcf: pre-existing,
    not a #250 regression.

    Divergence from pi (ADR-0235: pi is a verified reference, not a target):
    pi reads neither the flag nor anything but its three markers.
    """

    compat = getattr(model, "compat", None) or {}
    flag = compat.get("forceAdaptiveThinking")
    if flag is not None:
        return bool(flag)
    return bool(_ADAPTIVE_FAMILY_RE.search(model.id or model.name or ""))


def supports_thinking_off(model: Model) -> bool:
    """Can this row be asked for NO thinking at all?

    ``thinking: {"type": "disabled"}`` is how every other Claude row spells
    "off", and on most of them it works — measured 2026-09-09 on
    ``claude-opus-5``, ``claude-opus-4-8`` and ``claude-sonnet-5``, which all
    answered with thinking disabled even though their thinking-ON request was
    a 400. On ``claude-fable-5`` and ``claude-fable-5-1`` it is itself a 400:
    *"``thinking.type.disabled`` is not supported for this model. Use
    ``thinking.type.adaptive`` and ``output_config.effort`` to control
    thinking behavior."* (``req_011CerzNQTwZdGcBeTt9o3Ea``) — those two rows
    reject BOTH shapes, which is why beta.2 shipped ``claude-fable-5-1``
    unusable at every level, thinking on or off.

    Two signals, in order:

    * the row's own ``thinkingLevelMap`` — a declared ``"off": null`` means
      the level is not supported (:func:`aelix_ai.models.
      get_supported_thinking_levels`, pi ``models.ts:50-59``). Three shipped
      rows declare it: ``anthropic/claude-fable-5``,
      ``anthropic/claude-fable-5-1`` and ``anthropic/claude-sonnet-5``
      (counted 2026-09-09). ``claude-sonnet-5`` is the one the wire and the
      catalog disagree about — ``disabled`` *is* accepted there — and the
      catalog wins on purpose: the picker (``get_supported_thinking_levels``)
      and the clamp (``clamp_thinking_level``) already act on that
      declaration, so the request is the third place that now agrees with it,
      and what it sends is a shape the row accepts either way.
    * the ``fable`` family, for the six mirror rows whose ``thinkingLevelMap``
      carries no ``off`` key at all — ``claude-fable-5`` and its ``5.1``/
      ``5-1`` spelling on ``github-copilot``, ``opencode`` and
      ``vercel-ai-gateway``. An ABSENT key means supported (pi
      ``models.ts:50-59``), so the map alone would send those six the
      ``disabled`` request the model behind the proxy rejects.
    """

    if _ALWAYS_THINKING_FAMILY_RE.search(model.id or model.name or ""):
        return False
    return "off" in get_supported_thinking_levels(model)


def lowest_thinking_level(model: Model) -> str:
    """The least thinking this row offers, ``"off"`` excluded.

    Used to answer "thinking off" on a row that cannot be turned off. Reads
    the row's own supported set, so a ``thinkingLevelMap`` that declares
    ``"minimal": null`` moves the floor up instead of building a level the row
    rejects. ``"minimal"`` when the row declares nothing at all.
    """

    levels = [
        level for level in get_supported_thinking_levels(model) if level != "off"
    ]
    return levels[0] if levels else "minimal"


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


def clamp_reasoning(level: str, budgets: dict[str, int] | None = None) -> str:
    """Clamp a thinking level onto a key of the budget table.

    Divergence from pi ``clampReasoning`` (simple-options.ts:22-24), which
    returns ``"high"`` for ``"xhigh"``: #250 gives ``xhigh`` its own budget
    row, so the only job left is rejecting spellings the table cannot
    resolve. Unvalidated levels do arrive — ``set_thinking_level``
    (harness/core.py) assigns without validation and
    :class:`SimpleStreamOptions.reasoning` is a public ``str | None``. Until
    #258 the one that really showed up was ``"off"``, because
    :func:`resolve_anthropic_thinking` gated on ``if not reasoning`` and
    ``"off"`` is truthy (ADR-0135 Context §3); that function now answers the
    string itself, so what reaches this clamp is a genuine misspelling.

    Unknown spellings clamp to ``"medium"``, not ``"high"``: that is the
    budget they already got from the ``.get`` fallback in
    :func:`adjust_max_tokens_for_thinking` (measured on 0985fcf,
    ``adjust(64000, 64000, "off")`` → budget 8192), so nothing moves.

    ``budgets`` must be the **merged** table (defaults + ``custom_budgets``),
    not the overrides alone — pass only the overrides and every standard
    level demotes to ``"medium"``. The sole caller merges before calling.
    """

    table = _DEFAULT_THINKING_BUDGETS if budgets is None else budgets
    return level if level in table else "medium"


def adjust_max_tokens_for_thinking(
    base_max_tokens: int,
    model_max_tokens: int,
    reasoning_level: str,
    custom_budgets: dict[str, int] | None = None,
) -> tuple[int, int]:
    """Port of pi ``adjustMaxTokensForThinking`` (simple-options.ts:26-50).

    Returns ``(max_tokens, thinking_budget)``.

    ``base_max_tokens`` is the allowance for the **visible** answer, not the
    payload cap: the budget is added on top of it and the sum clamped to
    ``model_max_tokens``, so a caller that supplies its own ``max_tokens``
    gets a request whose ``max_tokens`` is larger than the number it passed.
    That is pi's contract, not an accident — its own comment on the parameter
    reads "Undefined means no explicit caller cap. Use the model cap and fit
    thinking inside it." What the caller's number bounds is the answer:
    ``max_tokens - budget <= base_max_tokens`` always.

    **What the carve reserves, stated exactly.** The budget is capped at
    ``model_max_tokens - _MIN_OUTPUT_TOKENS``, so the visible answer works out
    to ``min(base_max_tokens, model_max_tokens - budget)`` and the right
    operand is never below ``_MIN_OUTPUT_TOKENS``. That is a promise about the
    *carve*, not a floor on the answer: a caller passing a ``base_max_tokens``
    under 1024 gets exactly that many visible tokens — the number it asked for
    — and raising it to 1024 would hand back more answer than was requested.
    So ``max_tokens - budget >= min(base_max_tokens, _MIN_OUTPUT_TOKENS)`` is
    the invariant **whenever a budget is actually carved**, and
    ``test_the_carve_never_takes_more_answer_room_than_it_must`` walks both
    arms of that ``min``. It does NOT hold when the model's own cap leaves no
    room to carve at all: ``adjust(2000, 500, "high")`` returns ``(500, 0)``,
    where 500 is below ``min(2000, 1024)``. That is not a violated promise but
    a disabled one — ``budget == 0`` means no thinking was requested of the
    provider, so there is no carve to reserve answer room against, and the
    answer is simply ``min(base_max_tokens, model_max_tokens)``.

    Two divergences from pi, both from the #250 Codex cross-review:

    * the table has a fifth ``xhigh`` row (:data:`_DEFAULT_THINKING_BUDGETS`);
    * the budget is capped at ``model_max_tokens - _MIN_OUTPUT_TOKENS`` up
      front instead of pi's after-the-fact ``if maxTokens <= thinkingBudget``
      shrink. Pi's guard only fires once the budget has swallowed the cap
      *whole*, which leaves two holes measured on 73d167a: with
      ``model_max_tokens`` in ``(B, B + 1024)`` for a tier's budget ``B`` the
      visible answer got less than ``_MIN_OUTPUT_TOKENS`` (at 32769 with
      ``xhigh``, exactly **1** token), and because the hole moves with ``B``
      the tiers could invert — ``adjust(17000, 17000, "xhigh")`` returned
      15976 against ``"high"``'s 16384, i.e. asking for MORE reasoning got
      less. Capping instead of shrinking makes the budget
      ``min(B, cap - 1024)``, which is monotonic in ``B`` by construction, so
      no cap can invert two tiers again.

    No shipped row's catalog ``maxTokens`` is inside a hole (measured — the
    closest, 8192 / 16384 / 32768, sit on the boundary), so the **inversion**
    needs a ``models.json`` override: it also needs the row to offer ``xhigh``,
    and none of the 46 rows whose cap is computed rather than declared does.
    The **lost answer room** needs no override at all. On those 46 rows
    ``_effective_output_cap`` (anthropic.py) returns
    ``min(context_window - prompt - margin, 32000)``, a function of the prompt,
    so a long enough prompt walks the cap through every value: measured on the
    shipped ``accounts/fireworks/models/deepseek-v3p1`` row, a ~583k-character
    prompt puts the cap at ~17000, where pi's shrink left ``high`` ~626 visible
    tokens against the ``_MIN_OUTPUT_TOKENS`` the cap above reserves.
    """

    budgets = {**_DEFAULT_THINKING_BUDGETS, **(custom_budgets or {})}
    level = clamp_reasoning(reasoning_level, budgets)
    # The ``.get`` fallback stays even though the clamp now guarantees the key:
    # keeping the guarantee here means a future "restore pi parity" edit inside
    # :func:`clamp_reasoning` yields a wrong number, not an unhandled KeyError.
    thinking_budget = budgets.get(level, _DEFAULT_THINKING_BUDGETS["medium"])
    thinking_budget = max(
        0, min(thinking_budget, model_max_tokens - _MIN_OUTPUT_TOKENS)
    )
    max_tokens = min(base_max_tokens + thinking_budget, model_max_tokens)
    return max_tokens, thinking_budget


def resolve_anthropic_thinking(
    model: Model,
    reasoning: str | None,
    default_max_tokens: int,
    *,
    max_tokens_ceiling: int | None = None,
) -> tuple[dict[str, Any], int, bool]:
    """Pi parity: ``streamSimpleAnthropic`` + ``buildParams`` thinking block.

    Given the per-turn thinking level (``reasoning``), return
    ``(extra_params, max_tokens, needs_interleaved_beta)`` where
    ``extra_params`` carries the ``thinking`` request object (plus
    ``output_config`` for adaptive models) to merge into the Anthropic call.

    Behaviour:
      * non-reasoning model → ``{}`` (never send a thinking param);
      * no level, or the level ``"off"`` → ``{"thinking": {"type":
        "disabled"}}`` on any row that can express it
        (:func:`supports_thinking_off`);
      * no level, or ``"off"``, on a row that CANNOT be turned off →
        ``thinking.type = "adaptive"`` at the row's lowest effort and NO
        ``display`` (#258, see below);
      * adaptive model with a level → ``thinking.type = "adaptive"`` +
        ``output_config.effort``;
      * older reasoning model → ``thinking.type = "enabled"`` with a
        ``budget_tokens`` carved from ``max_tokens``;
      * older reasoning model whose output cap cannot hold a budget the API
        accepts → ``{"thinking": {"type": "disabled"}}`` (#250 review). A
        divergence: pi's ``|| 1024`` fallback (anthropic.ts, the
        ``budget_tokens`` line of its thinking block, read at
        ``pi@032c01c1e`` — not this ADR's pin) builds the rejected request
        instead.

    **"Off" on a row that is always thinking** (#258). ``claude-fable-5`` and
    ``claude-fable-5-1`` reject ``thinking.type.disabled`` with a 400
    (``req_011CerzNQTwZdGcBeTt9o3Ea``, measured 2026-09-09), so "off" cannot
    be expressed there the way it is on every other row. The honest mapping is
    the least thinking the row does offer: ``thinking.type = "adaptive"`` with
    ``output_config.effort`` from :func:`lowest_thinking_level` — ``"low"`` on
    those rows — and ``display`` deliberately omitted, so the API default
    (``omitted``) keeps the reasoning the user asked not to have out of the
    transcript. A request that answers beats a request that is honest about
    an option the model does not have.

    **Divergence from pi, and from this adapter before #258: the literal
    string ``"off"``.** Pi gates on ``if (!reasoning)`` alone, so ``"off"`` —
    truthy — reaches the budget path and buys ``"medium"``'s budget: measured
    on 0985fcf, ``SimpleStreamOptions(reasoning="off")`` sent
    ``budget_tokens: 8192``. Aelix now treats the string as the level it
    names. Without this, #258's own fix would have made the defect worse
    rather than merely visible: on a row that just moved onto the adaptive
    path, ``map_thinking_level_to_effort(model, "off")`` falls through its
    coarse mapping to ``"high"``, i.e. asking for no thinking would have
    bought the most. The harness collapses ``"off"`` to ``None`` before any
    adapter sees it, so this is an embedder-visible path
    (``SimpleStreamOptions.reasoning`` is a public ``str | None``). The same
    string still maps to ``"high"`` in both Google adapters, which is why the
    cross-adapter contract stays open as #259 — this fixes one adapter
    because #258 forced it to, not the contract.

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

    if not getattr(model, "reasoning", False):
        return {}, default_max_tokens, False

    adaptive = supports_adaptive_thinking(model)

    if not reasoning or reasoning == "off":
        if supports_thinking_off(model):
            return {"thinking": {"type": "disabled"}}, default_max_tokens, False
        if adaptive:
            # The row cannot stop thinking — ask for as little as it offers.
            off_extra: dict[str, Any] = {"thinking": {"type": "adaptive"}}
            floor = map_thinking_level_to_effort(model, lowest_thinking_level(model))
            if floor:
                off_extra["output_config"] = {"effort": floor}
            return off_extra, default_max_tokens, False
        # A budget row that declares ``"off": null`` — no shipped row does
        # (counted 2026-09-09: the three that declare it are all adaptive), so
        # this is only reachable through a ``models.json`` thinkingLevelMap
        # override. Unmeasured, so it keeps the shape every budget row accepts.
        return {"thinking": {"type": "disabled"}}, default_max_tokens, False

    # Pi defaults thinking display to "summarized" so newer models match the
    # API default older Claude 4 models already use (anthropic.ts:943-945).
    display = "summarized"

    if adaptive:
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
    # #149: ``max_tokens_ceiling`` lets the caller substitute a CLAMPED hard
    # clamp for the raw catalog ``model.max_tokens``. It matters because this
    # line computes ``min(base + budget, clamp)`` — with the raw catalog value a
    # row whose ``maxTokens >= contextWindow`` would let the carved thinking
    # budget add itself back on top of an already-clamped base and re-cross the
    # window, undoing the clamp. Defaults to ``None`` so every existing caller
    # (and pi parity, simple-options.ts:26-50) is unchanged.
    model_clamp = (
        max_tokens_ceiling
        if max_tokens_ceiling is not None and max_tokens_ceiling > 0
        else (model.max_tokens or default_max_tokens)
    )
    max_tokens, budget = adjust_max_tokens_for_thinking(
        base_max, model_clamp, reasoning
    )
    if budget < _MIN_THINKING_BUDGET or budget >= max_tokens:
        # #250 Codex cross-review: this branch replaces ``budget_tokens:
        # budget or 1024``, which could only ever send a request Anthropic
        # rejects. Measured on 73d167a with ``maxTokens: 1024`` on
        # ``anthropic/claude-opus-5``: the carve returned 0 and the ``or``
        # sent ``budget_tokens: 1024`` alongside ``max_tokens: 1024`` — equal,
        # where the API requires the budget to be strictly smaller; at 512 it
        # sent a budget larger than the whole request. Both are reachable from
        # a ``maxTokens`` override in ``models.json``, which validates only
        # that the number is positive.
        #
        # Of the two honest repairs — clamp the budget under ``max_tokens``,
        # or turn thinking off — this takes the second. A cap under
        # ``_MIN_THINKING_BUDGET + _MIN_OUTPUT_TOKENS`` (2048) cannot hold a
        # budget the API accepts AND leave an answer worth returning, so the
        # only clamp available would be a budget below Anthropic's 1024 floor:
        # still a 400, just a different one. ``thinking: disabled`` is a shape
        # this function already emits (the "no level" branch above), so the
        # turn answers instead of failing, and the level the user asked for is
        # honoured as far as the row's own output cap allows.
        return (
            {"thinking": {"type": "disabled"}},
            min(base_max, model_clamp),
            False,
        )
    extra = {
        "thinking": {
            "type": "enabled",
            "budget_tokens": budget,
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
    "map_stop_reason_with_details",
    "supports_temperature",
    "transform_messages",
]
