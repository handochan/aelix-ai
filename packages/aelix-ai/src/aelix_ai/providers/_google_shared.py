"""Shared engine for the Google Gemini providers — pi parity (#15).

Pi parity: ``packages/ai/src/api/google-shared.ts`` (message / tool
conversion, thought-signature handling, stop-reason mapping) plus the
thinking-family + usage helpers that pi keeps inline in
``packages/ai/src/api/google-generative-ai.ts``. Both the ``google``
(Gemini Developer API) and ``google-vertex`` (Vertex AI) adapters share
this engine, so the family-branching and signature machinery live here
once.

This module is **SDK-free**: ``convert_messages`` / ``convert_tools``
emit plain ``dict`` structures using the Google wire shape (camelCase
keys — ``functionCall`` / ``functionResponse`` / ``inlineData`` /
``thoughtSignature`` / ``parametersJsonSchema`` …). The official
``google-genai`` Python SDK accepts those dicts directly via its
pydantic field aliases, so the thin per-adapter ``create_client`` /
``stream`` (a later sprint) lazy-imports the SDK while this engine stays
import-safe even when the dep is missing.

The byte-sensitive piece is **thoughtSignature replay** — the Gemini
analog of OpenAI's encrypted reasoning. A stored signature is re-attached
to the *same* part verbatim only when it passes a triple gate (same
provider AND same model AND valid base64); signatures are never merged,
moved, or reordered across parts. See :func:`resolve_thought_signature`
and the assistant branch of :func:`convert_messages`.

Divergences from pi (intentional, v1):

- **Token-dict usage convention.** pi builds a ``Usage`` object and calls
  ``calculateCost``. Aelix follows the established adapter convention
  (see ``openai_completions._usage_to_dict``): :func:`get_usage` emits a
  plain ``dict`` of token counts (incl. ``reasoning``) and a higher layer
  resolves cost. pi arithmetic is preserved verbatim, including the
  possibly-negative ``input`` edge (``prompt - cached``) which is NOT
  clamped.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers._sanitize_unicode import sanitize_surrogates
from aelix_ai.providers._transform_messages import transform_messages
from aelix_ai.streaming import (
    AssistantMessageEvent,
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

if TYPE_CHECKING:
    from aelix_ai.streaming import Context, Model


# ``GoogleThinkingLevel`` — mirrors Google's ``ThinkingLevel`` enum values
# (pi ``google-shared.ts`` ``GoogleThinkingLevel``). Used by the thinking
# helpers below and echoed straight into ``thinkingConfig.thinkingLevel``.
GoogleThinkingLevel = str  # "MINIMAL" | "LOW" | "MEDIUM" | "HIGH" | ...

# Clamped effort the thinking helpers accept (pi ``ClampedThinkingLevel`` =
# ``Exclude<ThinkingLevel, "xhigh">``). The caller clamps before dispatch.
_CLAMPED_EFFORTS: frozenset[str] = frozenset({"minimal", "low", "medium", "high"})


# =============================================================================
# Thought-signature handling (THE LANDMINE — Gemini multi-turn replay)
# =============================================================================


def is_thinking_part(part: Any) -> bool:
    """Pi parity: ``isThinkingPart`` (google-shared.ts).

    ``thought: true`` is the **only** thinking marker. A ``thoughtSignature``
    can attach to ANY part (text / functionCall / thinking) and does NOT by
    itself imply the part is thinking content.
    """

    if isinstance(part, dict):
        return part.get("thought") is True
    return getattr(part, "thought", None) is True


def retain_thought_signature(
    existing: str | None, incoming: str | None
) -> str | None:
    """Pi parity: ``retainThoughtSignature`` (google-shared.ts).

    Retain the LAST non-empty signature for the current streamed block.
    Some backends only send ``thoughtSignature`` on the first delta of a
    part; later deltas omit it. This preserves the prior value rather than
    overwriting it with ``None`` — it never merges/moves signatures across
    distinct parts.
    """

    if isinstance(incoming, str) and len(incoming) > 0:
        return incoming
    return existing


# Thought signatures must be base64 for Google APIs (TYPE_BYTES).
# ``\Z`` (not ``$``) mirrors JS end-of-string: Python ``$`` also matches just
# before a trailing newline, so ``"abc\n"`` would wrongly validate with ``$``.
_BASE64_SIGNATURE_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={0,2}\Z")


def _normalize_signature(value: Any) -> str | None:
    """Normalize a raw thoughtSignature/inlineData TYPE_BYTES value to a str.

    The real ``google-genai`` Python SDK types ``Part.thought_signature`` as
    ``Optional[bytes]`` and base64-DECODES the wire value, so a Part built from
    ``{'thoughtSignature': 'YWJjZA=='}`` yields ``b'abcd'``. Aelix stores and
    replays signatures as the canonical base64 STRING throughout (the engine
    and ``is_valid_thought_signature`` assume ``str``), so re-encode bytes back
    to the original wire string here at the ingest seam:
    ``b64encode(b'abcd') -> 'YWJjZA=='`` (NOT ``bytes.decode()``, which would
    corrupt non-UTF-8 token bytes). Plain-dict fixtures (already ``str``) pass
    through unchanged.
    """

    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


def is_valid_thought_signature(signature: str | None) -> bool:
    """Pi parity: ``isValidThoughtSignature`` (google-shared.ts).

    Valid only when non-empty, length is a multiple of 4, and the string
    matches the base64 alphabet (with 0–2 trailing ``=`` padding chars).
    """

    if not signature:
        return False
    if len(signature) % 4 != 0:
        return False
    return bool(_BASE64_SIGNATURE_PATTERN.match(signature))


def resolve_thought_signature(
    is_same_provider_and_model: bool, signature: str | None
) -> str | None:
    """Pi parity: ``resolveThoughtSignature`` (google-shared.ts).

    Keep the signature ONLY when it came from the same provider/model AND is
    valid base64; otherwise drop it (an opaque token from a different model
    is meaningless to the target model).
    """

    if is_same_provider_and_model and is_valid_thought_signature(signature):
        return signature
    return None


def is_same_provider_and_model(
    message: AssistantMessage, model: Model
) -> bool:
    """Pi parity: the inline ``isSameProviderAndModel`` check.

    The Gemini signature-replay gate compares only ``provider`` + ``model``
    (NOT ``api`` — distinct from :func:`_transform_messages._is_same_model`'s
    triple check). A ``None`` provenance field (older adapter / fixture)
    fails the comparison against the non-``None`` target, matching pi's
    "treat unknown provenance as cross-model" behavior.
    """

    return message.provider == model.provider and message.model == model.id


def requires_tool_call_id(model_id: str) -> bool:
    """Pi parity: ``requiresToolCallId`` (google-shared.ts).

    Models proxied behind the Google APIs that need explicit tool-call ids
    in function calls/responses (Claude + gpt-oss via Cloud Code Assist).
    """

    return model_id.startswith("claude-") or model_id.startswith("gpt-oss-")


def _gemini_major_version(model_id: str) -> int | None:
    """Pi parity: ``getGeminiMajorVersion`` (google-shared.ts)."""

    match = re.match(r"^gemini(?:-live)?-(\d+)", model_id.lower())
    if not match:
        return None
    return int(match.group(1))


def supports_multimodal_function_response(model_id: str) -> bool:
    """Pi parity: ``supportsMultimodalFunctionResponse`` (google-shared.ts).

    Gemini 3+ supports images nested inside ``functionResponse.parts``;
    Gemini < 3 (and Claude/other models behind Cloud Code Assist, which
    return no Gemini version) need a separate user image turn. A non-Gemini
    id (no version match) returns ``True``.
    """

    major = _gemini_major_version(model_id)
    if major is not None:
        return major >= 3
    return True


# =============================================================================
# Message conversion
# =============================================================================


def convert_messages(model: Model, context: Context) -> list[dict[str, Any]]:
    """Aelix :class:`Context` → Google ``Content[]`` (as plain dicts).

    Pi parity: ``convertMessages`` (google-shared.ts). Runs
    :func:`transform_messages` first (cross-provider hygiene + tool-call id
    normalization), then maps each message to the Gemini wire shape:

    - ``user`` → ``{role: "user", parts: [text | inlineData]}``
    - ``assistant`` → ``{role: "model", parts: [text | thought:true text |
      functionCall]}`` with ``thoughtSignature`` re-attached per part when
      the triple gate passes.
    - ``toolResult`` → ``{role: "user", parts: [functionResponse]}`` with
      function responses merged into a single user turn (Cloud Code Assist
      requirement) and Gemini < 3 image fallback.

    Field remap from pi (aelix dataclass names): ``ToolCallContent`` uses
    ``tool_name`` / ``input`` / ``tool_call_id`` (pi ``name`` / ``arguments``
    / ``id``); ``ToolResultMessage`` uses ``tool_name`` / ``is_error`` /
    ``tool_call_id``; ``ImageContent`` uses ``mime_type`` / ``data``.
    """

    contents: list[dict[str, Any]] = []

    def _normalize_tool_call_id(
        tool_call_id: str, _target: Model, _source: AssistantMessage
    ) -> str:
        # Pi parity: inline ``normalizeToolCallId`` (google-shared.ts).
        if not requires_tool_call_id(model.id):
            return tool_call_id
        return re.sub(r"[^a-zA-Z0-9_-]", "_", tool_call_id)[:64]

    transformed = transform_messages(
        list(context.messages),
        model,
        normalize_tool_call_id=_normalize_tool_call_id,
    )

    includes_image = "image" in (model.input or [])
    model_multimodal_fn_response = supports_multimodal_function_response(model.id)

    for msg in transformed:
        if isinstance(msg, UserMessage):
            parts: list[dict[str, Any]] = []
            for item in msg.content:
                if isinstance(item, TextContent):
                    parts.append({"text": sanitize_surrogates(item.text)})
                elif isinstance(item, ImageContent):
                    parts.append(
                        {
                            "inlineData": {
                                "mimeType": item.mime_type,
                                "data": item.data,
                            }
                        }
                    )
            if not parts:
                continue
            contents.append({"role": "user", "parts": parts})

        elif isinstance(msg, AssistantMessage):
            same = is_same_provider_and_model(msg, model)
            a_parts: list[dict[str, Any]] = []

            for block in msg.content:
                if isinstance(block, TextContent):
                    # Skip empty text blocks (pi).
                    if not block.text or block.text.strip() == "":
                        continue
                    sig = resolve_thought_signature(same, block.text_signature)
                    part: dict[str, Any] = {"text": sanitize_surrogates(block.text)}
                    if sig:
                        part["thoughtSignature"] = sig
                    a_parts.append(part)

                elif isinstance(block, ThinkingContent):
                    # Skip empty thinking blocks (pi).
                    if not block.thinking or block.thinking.strip() == "":
                        continue
                    if same:
                        sig = resolve_thought_signature(
                            same, block.thinking_signature
                        )
                        t_part: dict[str, Any] = {
                            "thought": True,
                            "text": sanitize_surrogates(block.thinking),
                        }
                        if sig:
                            t_part["thoughtSignature"] = sig
                        a_parts.append(t_part)
                    else:
                        # Cross-model: convert to plain text (no thought
                        # marker, no signature) so the new model neither
                        # mimics the thinking nor sees an undecodable token.
                        a_parts.append(
                            {"text": sanitize_surrogates(block.thinking)}
                        )

                elif isinstance(block, ToolCallContent):
                    sig = resolve_thought_signature(
                        same, block.thought_signature
                    )
                    fc: dict[str, Any] = {
                        "name": block.tool_name,
                        "args": block.input if block.input is not None else {},
                    }
                    if requires_tool_call_id(model.id):
                        fc["id"] = block.tool_call_id
                    fc_part: dict[str, Any] = {"functionCall": fc}
                    if sig:
                        fc_part["thoughtSignature"] = sig
                    a_parts.append(fc_part)

            if not a_parts:
                continue
            contents.append({"role": "model", "parts": a_parts})

        elif isinstance(msg, ToolResultMessage):
            text_blocks = [c for c in msg.content if isinstance(c, TextContent)]
            text_result = "\n".join(c.text for c in text_blocks)
            image_blocks = (
                [c for c in msg.content if isinstance(c, ImageContent)]
                if includes_image
                else []
            )

            has_text = len(text_result) > 0
            has_images = len(image_blocks) > 0

            # "output" key for success, "error" key for errors (Gemini SDK).
            if has_text:
                response_value: str = sanitize_surrogates(text_result)
            elif has_images:
                response_value = "(see attached image)"
            else:
                response_value = ""

            image_parts: list[dict[str, Any]] = [
                {
                    "inlineData": {
                        "mimeType": img.mime_type,
                        "data": img.data,
                    }
                }
                for img in image_blocks
            ]

            include_id = requires_tool_call_id(model.id)
            function_response: dict[str, Any] = {
                "name": msg.tool_name,
                "response": (
                    {"error": response_value}
                    if msg.is_error
                    else {"output": response_value}
                ),
            }
            if has_images and model_multimodal_fn_response:
                function_response["parts"] = image_parts
            if include_id:
                function_response["id"] = msg.tool_call_id
            function_response_part = {"functionResponse": function_response}

            # Cloud Code Assist requires all function responses in a single
            # user turn — merge into the last user turn that already carries
            # function responses.
            last = contents[-1] if contents else None
            if (
                last is not None
                and last.get("role") == "user"
                and any("functionResponse" in p for p in last.get("parts", []))
            ):
                last["parts"].append(function_response_part)
            else:
                contents.append(
                    {"role": "user", "parts": [function_response_part]}
                )

            # Gemini < 3: images go in a separate user message.
            if has_images and not model_multimodal_fn_response:
                contents.append(
                    {
                        "role": "user",
                        "parts": [{"text": "Tool result image:"}, *image_parts],
                    }
                )

    return contents


# =============================================================================
# Tool conversion
# =============================================================================


_JSON_SCHEMA_META_DECLARATIONS: frozenset[str] = frozenset(
    {
        "$schema",
        "$id",
        "$anchor",
        "$dynamicAnchor",
        "$vocabulary",
        "$comment",
        "$defs",
        "definitions",  # pre-draft-2019-09 equivalent of $defs
    }
)


def _sanitize_for_openapi(schema: Any) -> Any:
    """Pi parity: ``sanitizeForOpenApi`` (google-shared.ts).

    Recursively strip JSON-Schema meta-declaration keys (``$schema`` etc.)
    so the legacy OpenAPI ``parameters`` field validates. Non-object /
    array / scalar values pass through unchanged.
    """

    if not isinstance(schema, dict):
        return schema
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _JSON_SCHEMA_META_DECLARATIONS:
            continue
        result[key] = _sanitize_for_openapi(value)
    return result


def convert_tools(
    tools: list[Any], use_parameters: bool = False
) -> list[dict[str, Any]] | None:
    """Aelix tools → Google function declarations (as plain dicts).

    Pi parity: ``convertTools`` (google-shared.ts). Defaults to
    ``parametersJsonSchema`` (full JSON Schema — ``anyOf`` / ``oneOf`` /
    ``const`` …). ``use_parameters=True`` emits the legacy OpenAPI 3.03
    ``parameters`` field (Cloud Code Assist + Claude), recursively stripped
    of JSON-Schema meta-declarations. Returns ``None`` for an empty tool
    list (pi returns ``undefined``).
    """

    if not tools:
        return None
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        params = getattr(tool, "parameters", None)
        decl: dict[str, Any] = {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
        }
        if use_parameters:
            decl["parameters"] = _sanitize_for_openapi(params)
        else:
            decl["parametersJsonSchema"] = params
        declarations.append(decl)
    return [{"functionDeclarations": declarations}]


def map_tool_choice(choice: str) -> str:
    """Pi parity: ``mapToolChoice`` (google-shared.ts).

    Map an Aelix tool-choice string to a Gemini ``FunctionCallingConfigMode``
    value (the ``google-genai`` SDK accepts the bare enum string). Unknown
    values fall back to ``AUTO``.
    """

    if choice == "none":
        return "NONE"
    if choice == "any":
        return "ANY"
    return "AUTO"


# =============================================================================
# Stop-reason mapping
# =============================================================================


def map_stop_reason(
    reason: str | None, *, has_function_call: bool = False
) -> str:
    """Pi parity: ``mapStopReason`` + the stream's toolUse override.

    pi maps the Gemini ``finishReason`` (``STOP`` → ``"stop"``,
    ``MAX_TOKENS`` → ``"length"``, everything else — ``SAFETY`` /
    ``RECITATION`` / ``OTHER`` / … — → ``"error"``) and then, in the stream
    loop, unconditionally overrides the stop reason to ``"toolUse"`` when the
    assistant produced any ``functionCall`` part. ``has_function_call``
    folds that override in (it wins over the raw mapping, matching pi's
    post-assignment override).
    """

    if has_function_call:
        return "toolUse"
    if reason == "STOP":
        return "stop"
    if reason == "MAX_TOKENS":
        return "length"
    return "error"


# =============================================================================
# Thinking family branching
# =============================================================================


def is_gemma4_model(model_id: str) -> bool:
    """Pi parity: ``isGemma4Model`` (google-generative-ai.ts)."""

    return re.search(r"gemma-?4", model_id.lower()) is not None


def is_gemini3_pro_model(model_id: str) -> bool:
    """Pi parity: ``isGemini3ProModel`` (google-generative-ai.ts)."""

    return re.search(r"gemini-3(?:\.\d+)?-pro", model_id.lower()) is not None


def is_gemini3_flash_model(model_id: str) -> bool:
    """Pi parity: ``isGemini3FlashModel`` (google-generative-ai.ts)."""

    lowered = model_id.lower()
    if re.search(r"gemini-3(?:\.\d+)?-flash", lowered) is not None:
        return True
    return lowered in ("gemini-flash-latest", "gemini-flash-lite-latest")


def get_disabled_thinking_config(model_id: str) -> dict[str, Any]:
    """Pi parity: ``getDisabledThinkingConfig`` (google-generative-ai.ts).

    Gemini 3 Pro cannot disable thinking and Flash/Flash-Lite cannot fully
    turn it off, so use the lowest supported ``thinkingLevel`` *without*
    ``includeThoughts`` (hidden thinking stays invisible). Gemini 2.x
    disables via ``thinkingBudget = 0``.
    """

    if is_gemini3_pro_model(model_id):
        return {"thinkingLevel": "LOW"}
    if is_gemini3_flash_model(model_id):
        return {"thinkingLevel": "MINIMAL"}
    if is_gemma4_model(model_id):
        return {"thinkingLevel": "MINIMAL"}
    return {"thinkingBudget": 0}


def get_thinking_level(effort: str, model_id: str) -> GoogleThinkingLevel:
    """Pi parity: ``getThinkingLevel`` (google-generative-ai.ts).

    Map a clamped effort to a Gemini 3 / Gemma 4 ``thinkingLevel``. Pro and
    Gemma 4 collapse ``minimal``/``low`` and ``medium``/``high`` into two
    levels; the default branch (other ``thinkingLevel`` models) is a 1:1
    map.
    """

    if is_gemini3_pro_model(model_id):
        if effort in ("minimal", "low"):
            return "LOW"
        return "HIGH"  # medium | high
    if is_gemma4_model(model_id):
        if effort in ("minimal", "low"):
            return "MINIMAL"
        return "HIGH"  # medium | high
    if effort == "minimal":
        return "MINIMAL"
    if effort == "low":
        return "LOW"
    if effort == "medium":
        return "MEDIUM"
    return "HIGH"  # high


# Pi parity: the per-family budget tables (google-generative-ai.ts
# ``getGoogleBudget``). Keys are the clamped efforts.
_BUDGET_2_5_PRO: dict[str, int] = {
    "minimal": 128,
    "low": 2048,
    "medium": 8192,
    "high": 32768,
}
_BUDGET_2_5_FLASH_LITE: dict[str, int] = {
    "minimal": 512,
    "low": 2048,
    "medium": 8192,
    "high": 24576,
}
_BUDGET_2_5_FLASH: dict[str, int] = {
    "minimal": 128,
    "low": 2048,
    "medium": 8192,
    "high": 24576,
}


def get_google_budget(
    model_id: str,
    effort: str,
    custom_budgets: dict[str, int] | None = None,
) -> int:
    """Pi parity: ``getGoogleBudget`` (google-generative-ai.ts).

    Resolve the Gemini 2.x integer ``thinkingBudget`` for a clamped effort.
    A caller-supplied ``custom_budgets`` override wins; otherwise pick the
    per-family table (``2.5-pro`` / ``2.5-flash-lite`` / ``2.5-flash``).
    Anything else returns ``-1`` (dynamic budget). The ``flash-lite`` check
    precedes ``flash`` because the id contains both substrings.
    """

    if custom_budgets is not None and custom_budgets.get(effort) is not None:
        return custom_budgets[effort]
    if "2.5-pro" in model_id:
        return _BUDGET_2_5_PRO[effort]
    if "2.5-flash-lite" in model_id:
        return _BUDGET_2_5_FLASH_LITE[effort]
    if "2.5-flash" in model_id:
        return _BUDGET_2_5_FLASH[effort]
    return -1


# =============================================================================
# Usage
# =============================================================================


def _read(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first present attr/key from ``names`` (snake then camel)."""

    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return default


def get_usage(usage_metadata: Any) -> dict[str, Any] | None:
    """Gemini ``usageMetadata`` → the Aelix usage dict.

    Pi parity: the ``output.usage`` assignment in google-generative-ai.ts.
    pi arithmetic is preserved verbatim:

    - ``input``  = ``promptTokenCount`` − ``cachedContentTokenCount``
      (NOT clamped — may go negative; pi parity).
    - ``output`` = ``candidatesTokenCount`` + ``thoughtsTokenCount``.
    - ``reasoning`` = ``thoughtsTokenCount`` (a subset of ``output``).
    - ``cache_read`` = ``cachedContentTokenCount``; ``cache_write`` = 0.
    - ``total_tokens`` = ``totalTokenCount``.

    Both the ``input``/``output`` (Usage-field) and ``input_tokens`` /
    ``output_tokens`` / ``total_tokens`` spellings are emitted so the
    context meter and ``/cost`` aggregator both read real numbers (the
    established adapter convention; see
    ``openai_completions._usage_to_dict``). Accepts both the SDK object
    (snake_case attrs) and a plain camelCase dict (fixtures). Returns
    ``None`` for a missing payload.
    """

    if usage_metadata is None:
        return None

    prompt = _read(
        usage_metadata, "prompt_token_count", "promptTokenCount", default=0
    ) or 0
    candidates = _read(
        usage_metadata,
        "candidates_token_count",
        "candidatesTokenCount",
        default=0,
    ) or 0
    thoughts = _read(
        usage_metadata,
        "thoughts_token_count",
        "thoughtsTokenCount",
        default=0,
    ) or 0
    cached = _read(
        usage_metadata,
        "cached_content_token_count",
        "cachedContentTokenCount",
        default=0,
    ) or 0
    total = _read(
        usage_metadata, "total_token_count", "totalTokenCount", default=0
    ) or 0

    non_cached_input = prompt - cached  # pi parity: NOT clamped.
    output = candidates + thoughts

    return {
        "input": non_cached_input,
        "output": output,
        "input_tokens": non_cached_input,
        "output_tokens": output,
        "cache_read": cached,
        "cache_write": 0,
        "reasoning": thoughts,
        "total_tokens": total,
    }


# =============================================================================
# Param assembly (shared by both Google adapters)
# =============================================================================


@dataclass(frozen=True)
class GoogleThinking:
    """Pi parity: ``GoogleOptions.thinking`` (google-generative-ai.ts:40-44).

    ``enabled`` toggles ``includeThoughts``. Exactly one of ``level`` (Gemini 3
    / Gemma 4 ``thinkingLevel``) or ``budget_tokens`` (Gemini 2.x
    ``thinkingBudget``; ``-1`` dynamic, ``0`` disable) is used; ``level`` wins
    when both are present (pi's ``if level !== undefined … else if budget``).
    """

    enabled: bool = False
    budget_tokens: int | None = None
    level: str | None = None


def build_google_params(
    model: Model,
    context: Context,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    tool_choice: str | None = None,
    thinking: GoogleThinking | None = None,
    disabled_thinking_config: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble ``generateContentStream`` params (pi ``buildParams``).

    Pi parity: ``buildParams`` (google-generative-ai.ts:343-401 /
    google-vertex.ts:442-499). The two pi adapters build an identical config
    given the same options; the only per-adapter difference (the
    thinking-family selection that populates ``thinking``) happens upstream in
    each adapter's ``streamSimple`` and the per-adapter
    ``disabled_thinking_config`` override. Returns ``{model, contents, config}``
    with ``config`` a camelCase ``dict`` the ``google-genai`` SDK accepts via
    its pydantic field aliases (``maxOutputTokens`` / ``systemInstruction`` /
    ``toolConfig`` / ``thinkingConfig`` …).

    ``temperature`` / ``maxOutputTokens`` are spread flat onto ``config`` (pi
    spreads its ``generationConfig`` into the top-level config). ``toolConfig``
    is set only when both tools AND ``tool_choice`` are present (pi otherwise
    assigns ``undefined`` — i.e. omits it). The thinking block mirrors pi: when
    ``thinking.enabled`` and the model reasons, emit ``includeThoughts`` plus
    either ``thinkingLevel`` or ``thinkingBudget``; when the model reasons but
    thinking is explicitly disabled, emit the disabled-thinking config. The
    Gemini adapter uses the shared :func:`get_disabled_thinking_config` (which
    has a Gemma 4 branch); the Vertex adapter passes its own
    ``disabled_thinking_config`` (no Gemma branch — pi ``google-vertex.ts``
    ``getDisabledThinkingConfig`` lets Gemma fall through to
    ``thinkingBudget: 0``).
    """

    contents = convert_messages(model, context)

    config: dict[str, Any] = {}
    if temperature is not None:
        config["temperature"] = temperature
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens
    if context.system_prompt:
        config["systemInstruction"] = sanitize_surrogates(context.system_prompt)

    tools = list(context.tools or [])
    if tools:
        config["tools"] = convert_tools(tools)
        if tool_choice:
            config["toolConfig"] = {
                "functionCallingConfig": {"mode": map_tool_choice(tool_choice)}
            }

    if thinking is not None and thinking.enabled and model.reasoning:
        thinking_config: dict[str, Any] = {"includeThoughts": True}
        if thinking.level is not None:
            thinking_config["thinkingLevel"] = thinking.level
        elif thinking.budget_tokens is not None:
            thinking_config["thinkingBudget"] = thinking.budget_tokens
        config["thinkingConfig"] = thinking_config
    elif model.reasoning and thinking is not None and not thinking.enabled:
        disabled_fn = disabled_thinking_config or get_disabled_thinking_config
        config["thinkingConfig"] = disabled_fn(model.id)

    return {"model": model.id, "contents": contents, "config": config}


# =============================================================================
# Streaming engine (shared by both Google adapters)
# =============================================================================


@dataclass
class GoogleStreamState:
    """Mutable accumulator the stream engine fills (pi's ``output`` object).

    The thin adapter creates one, drives :func:`process_google_stream` to
    exhaustion, then reads the finalized fields to emit the terminal ``done``
    event. Mirrors the OpenAI Responses adapter's ``ResponsesStreamState``.
    """

    content: list[Any] = field(default_factory=list)
    response_id: str | None = None
    usage: dict[str, Any] | None = None
    stop_reason: str = "stop"


# Module-level counter for synthesizing unique tool-call ids (pi
# ``toolCallCounter``, google-generative-ai.ts:48).
_tool_call_counter = 0


def _finish_reason_str(reason: Any) -> str | None:
    """Coerce a SDK ``FinishReason`` enum / plain string to its wire string."""

    if reason is None:
        return None
    value = getattr(reason, "value", None)
    if value is not None:
        return str(value)
    return str(reason)


async def process_google_stream(
    chunk_iter: AsyncIterator[Any], state: GoogleStreamState, model: Model
) -> AsyncIterator[AssistantMessageEvent]:
    """Translate a Gemini chunk stream into Aelix message events.

    Pi parity: the ``for await (const chunk of googleStream)`` loop shared
    verbatim by ``google-generative-ai.ts`` (90-255) and ``google-vertex.ts``
    (108-272). SDK-free: reads duck-typed chunk objects (real SDK
    ``GenerateContentResponse`` or fixture dicts) via :func:`_read`, so the
    same engine serves both adapters and the fake-client tests.

    Block management mirrors pi exactly: a single open text/thinking block is
    extended in place (rebuilt as a frozen :class:`TextContent` /
    :class:`ThinkingContent` on each delta, since Aelix blocks are immutable),
    switching blocks closes the prior one; a ``functionCall`` part closes any
    open block then emits a self-contained ``toolcall_start`` / ``delta`` /
    ``end`` trio. ``thoughtSignature`` is retained (last non-empty) onto the
    growing block and stamped verbatim onto the tool call.
    """

    global _tool_call_counter

    def _snapshot() -> AssistantMessage:
        return AssistantMessage(
            content=list(state.content),
            response_id=state.response_id,
            api=model.api,
            provider=model.provider,
            model=model.id,
        )

    # Local mirror of pi's ``currentBlock`` — kind + accumulators.
    cur_kind: str | None = None  # "text" | "thinking" | None
    cur_index = -1
    cur_text = ""
    cur_sig: str | None = None

    def _close_current() -> AssistantMessageEvent | None:
        nonlocal cur_kind, cur_index, cur_text, cur_sig
        if cur_kind is None:
            return None
        if cur_kind == "text":
            ev: AssistantMessageEvent = TextEndEvent(
                content_index=cur_index, content=cur_text, partial=_snapshot()
            )
        else:
            ev = ThinkingEndEvent(
                content_index=cur_index, content=cur_text, partial=_snapshot()
            )
        cur_kind = None
        cur_index = -1
        cur_text = ""
        cur_sig = None
        return ev

    async for chunk in chunk_iter:
        # responseId ||= chunk.responseId (keep first non-empty).
        if not state.response_id:
            rid = _read(chunk, "response_id", "responseId")
            if rid:
                state.response_id = rid

        candidates = _read(chunk, "candidates", default=None)
        candidate = candidates[0] if candidates else None

        if candidate is not None:
            content = _read(candidate, "content", default=None)
            parts = _read(content, "parts", default=None) if content else None
            for part in parts or []:
                text = (
                    part.get("text")
                    if isinstance(part, dict)
                    else getattr(part, "text", None)
                )
                # Normalize the SDK's bytes thoughtSignature (TYPE_BYTES,
                # base64-decoded by pydantic) back to the canonical base64
                # str BEFORE retain/store, so the str-assuming engine and
                # ``is_valid_thought_signature`` never see raw bytes.
                part_sig = _normalize_signature(
                    _read(part, "thought_signature", "thoughtSignature")
                )

                if text is not None:
                    is_thinking = is_thinking_part(part)
                    want = "thinking" if is_thinking else "text"
                    if cur_kind != want:
                        closed = _close_current()
                        if closed is not None:
                            yield closed
                        state.content.append(
                            ThinkingContent(thinking="")
                            if is_thinking
                            else TextContent(text="")
                        )
                        cur_index = len(state.content) - 1
                        cur_kind = want
                        cur_text = ""
                        cur_sig = None
                        if is_thinking:
                            yield ThinkingStartEvent(
                                content_index=cur_index, partial=_snapshot()
                            )
                        else:
                            yield TextStartEvent(
                                content_index=cur_index, partial=_snapshot()
                            )

                    cur_text += text
                    cur_sig = retain_thought_signature(cur_sig, part_sig)
                    if cur_kind == "thinking":
                        state.content[cur_index] = ThinkingContent(
                            thinking=cur_text,
                            thinking_signature=cur_sig or "",
                        )
                        yield ThinkingDeltaEvent(
                            delta=text,
                            content_index=cur_index,
                            partial=_snapshot(),
                        )
                    else:
                        state.content[cur_index] = TextContent(
                            text=cur_text, text_signature=cur_sig or ""
                        )
                        yield TextDeltaEvent(
                            delta=text,
                            content_index=cur_index,
                            partial=_snapshot(),
                        )

                fn_call = _read(part, "function_call", "functionCall")
                if fn_call:
                    closed = _close_current()
                    if closed is not None:
                        yield closed

                    fn_name = _read(fn_call, "name", default="") or ""
                    fn_args = _read(fn_call, "args", default=None)
                    if fn_args is None:
                        fn_args = {}
                    provided_id = _read(fn_call, "id", default=None)
                    used = {
                        b.tool_call_id
                        for b in state.content
                        if isinstance(b, ToolCallContent)
                    }
                    needs_new = not provided_id or provided_id in used
                    if needs_new:
                        _tool_call_counter += 1
                        # pi synthesizes ``${name}_${Date.now()}_${++counter}``
                        # (google-generative-ai.ts). Aelix intentionally drops
                        # the wall-clock segment: the monotonic module counter
                        # already guarantees per-process uniqueness, and the
                        # in-turn collision check above (``provided_id in
                        # used``) covers re-used model ids — so the time
                        # component adds only non-determinism (hostile to
                        # fixtures/tests) without changing correctness.
                        tool_call_id = f"{fn_name}_{_tool_call_counter}"
                    else:
                        tool_call_id = provided_id

                    tool_call = ToolCallContent(
                        tool_call_id=tool_call_id,
                        tool_name=fn_name,
                        input=fn_args,
                        thought_signature=part_sig or "",
                    )
                    state.content.append(tool_call)
                    idx = len(state.content) - 1
                    yield ToolCallStartEvent(
                        content_index=idx, partial=_snapshot()
                    )
                    yield ToolCallDeltaEvent(
                        delta=json.dumps(fn_args, separators=(",", ":")),
                        content_index=idx,
                        partial=_snapshot(),
                    )
                    yield ToolCallEndEvent(
                        content_index=idx,
                        tool_call=tool_call,
                        partial=_snapshot(),
                    )

        finish_reason = _finish_reason_str(
            _read(candidate, "finish_reason", "finishReason")
            if candidate is not None
            else None
        )
        if finish_reason:
            has_fn = any(
                isinstance(b, ToolCallContent) for b in state.content
            )
            state.stop_reason = map_stop_reason(
                finish_reason, has_function_call=has_fn
            )

        usage_metadata = _read(chunk, "usage_metadata", "usageMetadata")
        if usage_metadata is not None:
            usage = get_usage(usage_metadata)
            if usage is not None:
                state.usage = usage

    closed = _close_current()
    if closed is not None:
        yield closed


__all__ = [
    "GoogleStreamState",
    "GoogleThinking",
    "GoogleThinkingLevel",
    "build_google_params",
    "convert_messages",
    "convert_tools",
    "get_disabled_thinking_config",
    "get_google_budget",
    "get_thinking_level",
    "get_usage",
    "is_gemini3_flash_model",
    "is_gemini3_pro_model",
    "is_gemma4_model",
    "is_same_provider_and_model",
    "is_thinking_part",
    "is_valid_thought_signature",
    "map_stop_reason",
    "map_tool_choice",
    "process_google_stream",
    "requires_tool_call_id",
    "resolve_thought_signature",
    "retain_thought_signature",
    "supports_multimodal_function_response",
]
