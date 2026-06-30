"""Tests for the Google Gemini shared engine (pi parity, #15).

Pi parity: ``packages/ai/src/api/google-shared.ts`` plus the thinking /
usage helpers from ``packages/ai/src/api/google-generative-ai.ts``.

Fixture-driven and SDK-free: ``convert_messages`` / ``convert_tools`` emit
plain dicts in the Google wire shape, so the assertions inspect those
dicts directly. The byte-sensitive focus is the ``thoughtSignature``
triple-gate (same/cross provider+model, valid/invalid base64,
retain-last-non-empty), the thinking-family branching, the stop-reason
toolUse override, and the usage arithmetic incl. reasoning.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers._google_shared import (
    GoogleStreamState,
    convert_messages,
    convert_tools,
    get_disabled_thinking_config,
    get_google_budget,
    get_thinking_level,
    get_usage,
    is_gemini3_flash_model,
    is_gemini3_pro_model,
    is_gemma4_model,
    is_same_provider_and_model,
    is_thinking_part,
    is_valid_thought_signature,
    map_stop_reason,
    map_tool_choice,
    process_google_stream,
    requires_tool_call_id,
    resolve_thought_signature,
    retain_thought_signature,
    supports_multimodal_function_response,
)
from aelix_ai.streaming import Context, Model
from aelix_ai.tools import Tool

# === helpers ================================================================


def _model(**kw: Any) -> Model:
    base: dict[str, Any] = {
        "id": "gemini-2.5-flash",
        "name": "gemini-2.5-flash",
        "api": "google-generative-ai",
        "provider": "google",
        "input": ["text", "image"],
        "reasoning": True,
    }
    base.update(kw)
    return Model(**base)


# A valid base64 signature (len % 4 == 0, base64 alphabet).
_VALID_SIG = base64.b64encode(b"gemini-thought-token").decode()


# === thought-signature validity =============================================


def test_is_valid_thought_signature_accepts_proper_base64() -> None:
    assert is_valid_thought_signature(_VALID_SIG) is True


@pytest.mark.parametrize(
    "sig",
    [
        None,
        "",
        "abc",  # len 3, not multiple of 4
        "abcde",  # len 5
        "!!!!",  # multiple of 4 but illegal chars
        "ab=c",  # padding not at the end
        "abc\n",  # len 4 but trailing newline — JS ``$`` rejects, Python
        # ``$`` would wrongly accept (matches before \n); ``\Z`` rejects it.
    ],
)
def test_is_valid_thought_signature_rejects_bad(sig: str | None) -> None:
    assert is_valid_thought_signature(sig) is False


def test_resolve_thought_signature_triple_gate() -> None:
    # same provider+model AND valid -> kept verbatim
    assert resolve_thought_signature(True, _VALID_SIG) == _VALID_SIG
    # cross provider/model -> dropped even when valid
    assert resolve_thought_signature(False, _VALID_SIG) is None
    # same but invalid base64 -> dropped
    assert resolve_thought_signature(True, "abc") is None


def test_retain_thought_signature_keeps_last_non_empty() -> None:
    assert retain_thought_signature("old", "new") == "new"
    assert retain_thought_signature("old", None) == "old"
    assert retain_thought_signature("old", "") == "old"
    assert retain_thought_signature(None, "first") == "first"


def test_is_thinking_part_only_thought_true() -> None:
    assert is_thinking_part({"thought": True, "text": "x"}) is True
    assert is_thinking_part({"text": "x", "thoughtSignature": "sig"}) is False
    assert is_thinking_part({"thought": False}) is False
    assert is_thinking_part({}) is False


def test_is_same_provider_and_model() -> None:
    model = _model()
    same = AssistantMessage(
        content=[], provider="google", model="gemini-2.5-flash"
    )
    cross_model = AssistantMessage(
        content=[], provider="google", model="gemini-3-pro"
    )
    cross_provider = AssistantMessage(
        content=[], provider="openai", model="gemini-2.5-flash"
    )
    none_provenance = AssistantMessage(content=[])
    assert is_same_provider_and_model(same, model) is True
    assert is_same_provider_and_model(cross_model, model) is False
    assert is_same_provider_and_model(cross_provider, model) is False
    assert is_same_provider_and_model(none_provenance, model) is False


# === requires_tool_call_id ==================================================


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("claude-sonnet-4", True),
        ("gpt-oss-120b", True),
        ("gemini-2.5-flash", False),
        ("gemini-3-pro", False),
    ],
)
def test_requires_tool_call_id(model_id: str, expected: bool) -> None:
    assert requires_tool_call_id(model_id) is expected


# === convert_messages : user / assistant roles & parts ======================


def test_convert_messages_user_text_and_image() -> None:
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="hello"),
                    ImageContent(mime_type="image/png", data="BASE64DATA"),
                ]
            )
        ]
    )
    contents = convert_messages(_model(), ctx)
    assert contents == [
        {
            "role": "user",
            "parts": [
                {"text": "hello"},
                {"inlineData": {"mimeType": "image/png", "data": "BASE64DATA"}},
            ],
        }
    ]


def test_convert_messages_assistant_text_thinking_toolcall() -> None:
    model = _model()
    assistant = AssistantMessage(
        content=[
            ThinkingContent(thinking="pondering"),
            TextContent(text="the answer"),
            ToolCallContent(
                tool_call_id="call_1",
                tool_name="search",
                input={"q": "x"},
            ),
        ],
        api="google-generative-ai",
        provider="google",
        model="gemini-2.5-flash",
    )
    # Match the tool call so transform_messages does not synthesize an
    # orphan tool-result; assert on the model turn (contents[0]).
    ctx = Context(
        messages=[
            assistant,
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="search",
                content=[TextContent(text="done")],
            ),
        ]
    )
    contents = convert_messages(model, ctx)
    assert contents[0] == {
        "role": "model",
        "parts": [
            {"thought": True, "text": "pondering"},
            {"text": "the answer"},
            {"functionCall": {"name": "search", "args": {"q": "x"}}},
        ],
    }


def test_convert_messages_skips_empty_text_and_thinking_blocks() -> None:
    assistant = AssistantMessage(
        content=[
            TextContent(text="   "),
            ThinkingContent(thinking=""),
            TextContent(text="real"),
        ],
        provider="google",
        model="gemini-2.5-flash",
    )
    contents = convert_messages(_model(), Context(messages=[assistant]))
    assert contents == [{"role": "model", "parts": [{"text": "real"}]}]


def test_convert_messages_cross_model_thinking_becomes_plain_text() -> None:
    # Cross-model assistant: thinking block flattens to plain text (no
    # thought marker, no signature).
    assistant = AssistantMessage(
        content=[ThinkingContent(thinking="secret", thinking_signature=_VALID_SIG)],
        provider="openai",
        model="o3",
    )
    contents = convert_messages(_model(), Context(messages=[assistant]))
    assert contents == [{"role": "model", "parts": [{"text": "secret"}]}]


# === convert_messages : thoughtSignature triple-gate ========================


def test_thought_signature_replayed_same_provider_model() -> None:
    model = _model()
    assistant = AssistantMessage(
        content=[
            TextContent(text="t", text_signature=_VALID_SIG),
            ThinkingContent(thinking="th", thinking_signature=_VALID_SIG),
            ToolCallContent(
                tool_call_id="c1",
                tool_name="fn",
                input={},
                thought_signature=_VALID_SIG,
            ),
        ],
        api="google-generative-ai",
        provider="google",
        model="gemini-2.5-flash",
    )
    ctx = Context(
        messages=[
            assistant,
            ToolResultMessage(
                tool_call_id="c1", tool_name="fn", content=[TextContent(text="r")]
            ),
        ]
    )
    parts = convert_messages(model, ctx)[0]["parts"]
    # Signature re-attached verbatim to each part, never moved/merged.
    assert parts[0] == {"text": "t", "thoughtSignature": _VALID_SIG}
    assert parts[1] == {
        "thought": True,
        "text": "th",
        "thoughtSignature": _VALID_SIG,
    }
    assert parts[2] == {
        "functionCall": {"name": "fn", "args": {}},
        "thoughtSignature": _VALID_SIG,
    }


def test_thought_signature_dropped_cross_model() -> None:
    # Same provider, different model id -> signature gate fails.
    model = _model()
    assistant = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="c1",
                tool_name="fn",
                input={},
                thought_signature=_VALID_SIG,
            )
        ],
        provider="google",
        model="gemini-3-pro",
    )
    parts = convert_messages(model, Context(messages=[assistant]))[0]["parts"]
    assert parts[0] == {"functionCall": {"name": "fn", "args": {}}}
    assert "thoughtSignature" not in parts[0]


def test_thought_signature_dropped_invalid_base64() -> None:
    model = _model()
    # api/provider/model all match (same-model), so the block reaches the
    # signature gate — which drops it because the base64 is invalid.
    assistant = AssistantMessage(
        content=[TextContent(text="t", text_signature="not-base64!!")],
        api="google-generative-ai",
        provider="google",
        model="gemini-2.5-flash",
    )
    parts = convert_messages(model, Context(messages=[assistant]))[0]["parts"]
    assert parts[0] == {"text": "t"}


# === convert_messages : toolResult (functionResponse) =======================


def test_convert_messages_tool_result_success_and_error() -> None:
    model = _model()
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="search",
                content=[TextContent(text="ok")],
                is_error=False,
            ),
            ToolResultMessage(
                tool_call_id="c2",
                tool_name="search",
                content=[TextContent(text="boom")],
                is_error=True,
            ),
        ]
    )
    contents = convert_messages(model, ctx)
    # Both function responses merged into a single user turn.
    assert len(contents) == 1
    assert contents[0]["role"] == "user"
    parts = contents[0]["parts"]
    assert parts[0] == {
        "functionResponse": {"name": "search", "response": {"output": "ok"}}
    }
    assert parts[1] == {
        "functionResponse": {"name": "search", "response": {"error": "boom"}}
    }


def test_convert_messages_tool_result_includes_id_for_claude() -> None:
    # claude-* (proxied behind google-vertex) requires explicit tool-call
    # ids in function responses; a plain Gemini model omits them.
    model = _model(id="claude-sonnet-4", provider="google-vertex")
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="fn",
                content=[TextContent(text="r")],
            )
        ]
    )
    fr = convert_messages(model, ctx)[0]["parts"][0]["functionResponse"]
    assert fr["id"] == "call_1"
    assert fr["name"] == "fn"


def test_convert_messages_tool_result_omits_id_for_gemini() -> None:
    model = _model(id="gemini-2.5-flash")
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="call_1",
                tool_name="fn",
                content=[TextContent(text="r")],
            )
        ]
    )
    fr = convert_messages(model, ctx)[0]["parts"][0]["functionResponse"]
    assert "id" not in fr


def test_convert_messages_tool_result_image_gemini3_nested() -> None:
    # Gemini 3+ nests images inside functionResponse.parts.
    model = _model(id="gemini-3-pro")
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="fn",
                content=[
                    TextContent(text="see"),
                    ImageContent(mime_type="image/png", data="IMG"),
                ],
            )
        ]
    )
    contents = convert_messages(model, ctx)
    assert len(contents) == 1
    fr = contents[0]["parts"][0]["functionResponse"]
    assert fr["response"] == {"output": "see"}
    assert fr["parts"] == [
        {"inlineData": {"mimeType": "image/png", "data": "IMG"}}
    ]


def test_convert_messages_tool_result_image_legacy_separate_turn() -> None:
    # Gemini < 3: image goes in a separate user turn, not nested.
    model = _model(id="gemini-2.5-flash")
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="fn",
                content=[
                    TextContent(text="see"),
                    ImageContent(mime_type="image/png", data="IMG"),
                ],
            )
        ]
    )
    contents = convert_messages(model, ctx)
    assert len(contents) == 2
    fr = contents[0]["parts"][0]["functionResponse"]
    assert "parts" not in fr
    assert contents[1] == {
        "role": "user",
        "parts": [
            {"text": "Tool result image:"},
            {"inlineData": {"mimeType": "image/png", "data": "IMG"}},
        ],
    }


def test_convert_messages_tool_result_image_only_value() -> None:
    model = _model(id="gemini-3-pro")
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="fn",
                content=[ImageContent(mime_type="image/png", data="IMG")],
            )
        ]
    )
    fr = convert_messages(model, ctx)[0]["parts"][0]["functionResponse"]
    assert fr["response"] == {"output": "(see attached image)"}


# === convert_tools ==========================================================


def test_convert_tools_parameters_json_schema() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    tools = [Tool(name="search", description="d", parameters=schema)]
    out = convert_tools(tools)
    assert out == [
        {
            "functionDeclarations": [
                {
                    "name": "search",
                    "description": "d",
                    "parametersJsonSchema": schema,
                }
            ]
        }
    ]


def test_convert_tools_use_parameters_strips_meta() -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$defs": {"x": {"type": "string"}},
        "type": "object",
        "properties": {"a": {"$comment": "drop me", "type": "string"}},
    }
    tools = [Tool(name="t", description="", parameters=schema)]
    out = convert_tools(tools, use_parameters=True)
    decl = out[0]["functionDeclarations"][0]
    assert "parametersJsonSchema" not in decl
    assert decl["parameters"] == {
        "type": "object",
        "properties": {"a": {"type": "string"}},
    }


def test_convert_tools_empty_returns_none() -> None:
    assert convert_tools([]) is None


def test_map_tool_choice() -> None:
    assert map_tool_choice("auto") == "AUTO"
    assert map_tool_choice("none") == "NONE"
    assert map_tool_choice("any") == "ANY"
    assert map_tool_choice("garbage") == "AUTO"


# === map_stop_reason ========================================================


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("STOP", "stop"),
        ("MAX_TOKENS", "length"),
        ("SAFETY", "error"),
        ("RECITATION", "error"),
        ("OTHER", "error"),
        ("FINISH_REASON_UNSPECIFIED", "error"),
        (None, "error"),
    ],
)
def test_map_stop_reason_pure(reason: str | None, expected: str) -> None:
    assert map_stop_reason(reason) == expected


def test_map_stop_reason_tooluse_override() -> None:
    # toolUse override wins unconditionally when a function call is present.
    assert map_stop_reason("STOP", has_function_call=True) == "toolUse"
    assert map_stop_reason("MAX_TOKENS", has_function_call=True) == "toolUse"
    assert map_stop_reason(None, has_function_call=True) == "toolUse"


# === thinking family branching ==============================================


@pytest.mark.parametrize(
    ("model_id", "is_pro", "is_flash", "is_gemma"),
    [
        ("gemini-3-pro", True, False, False),
        ("gemini-3.1-pro", True, False, False),
        ("gemini-3-flash", False, True, False),
        ("gemini-flash-latest", False, True, False),
        ("gemini-flash-lite-latest", False, True, False),
        ("gemma-4", False, False, True),
        ("gemma4-it", False, False, True),
        ("gemini-2.5-flash", False, False, False),
    ],
)
def test_model_family_predicates(
    model_id: str, is_pro: bool, is_flash: bool, is_gemma: bool
) -> None:
    assert is_gemini3_pro_model(model_id) is is_pro
    assert is_gemini3_flash_model(model_id) is is_flash
    assert is_gemma4_model(model_id) is is_gemma


def test_get_thinking_level_gemini3_pro() -> None:
    assert get_thinking_level("minimal", "gemini-3-pro") == "LOW"
    assert get_thinking_level("low", "gemini-3-pro") == "LOW"
    assert get_thinking_level("medium", "gemini-3-pro") == "HIGH"
    assert get_thinking_level("high", "gemini-3-pro") == "HIGH"


def test_get_thinking_level_gemma4() -> None:
    assert get_thinking_level("minimal", "gemma-4") == "MINIMAL"
    assert get_thinking_level("low", "gemma-4") == "MINIMAL"
    assert get_thinking_level("medium", "gemma-4") == "HIGH"
    assert get_thinking_level("high", "gemma-4") == "HIGH"


def test_get_thinking_level_default_one_to_one() -> None:
    # A gemini-3-flash falls through to the 1:1 default branch.
    assert get_thinking_level("minimal", "gemini-3-flash") == "MINIMAL"
    assert get_thinking_level("low", "gemini-3-flash") == "LOW"
    assert get_thinking_level("medium", "gemini-3-flash") == "MEDIUM"
    assert get_thinking_level("high", "gemini-3-flash") == "HIGH"


def test_get_disabled_thinking_config() -> None:
    assert get_disabled_thinking_config("gemini-3-pro") == {"thinkingLevel": "LOW"}
    assert get_disabled_thinking_config("gemini-3-flash") == {
        "thinkingLevel": "MINIMAL"
    }
    assert get_disabled_thinking_config("gemma-4") == {"thinkingLevel": "MINIMAL"}
    assert get_disabled_thinking_config("gemini-2.5-flash") == {"thinkingBudget": 0}


def test_get_google_budget_tables() -> None:
    assert get_google_budget("gemini-2.5-pro", "minimal") == 128
    assert get_google_budget("gemini-2.5-pro", "high") == 32768
    # flash-lite check precedes flash (id contains both substrings).
    assert get_google_budget("gemini-2.5-flash-lite", "minimal") == 512
    assert get_google_budget("gemini-2.5-flash-lite", "high") == 24576
    assert get_google_budget("gemini-2.5-flash", "minimal") == 128
    assert get_google_budget("gemini-2.5-flash", "high") == 24576
    # unknown family -> dynamic budget -1.
    assert get_google_budget("gemini-3-pro", "high") == -1


def test_get_google_budget_custom_override() -> None:
    custom = {"high": 9999}
    assert get_google_budget("gemini-2.5-pro", "high", custom) == 9999
    # falls back to table when the effort is absent from the override.
    assert get_google_budget("gemini-2.5-pro", "low", custom) == 2048


# === supports_multimodal_function_response ==================================


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("gemini-3-pro", True),
        ("gemini-3.1-flash", True),
        ("gemini-2.5-flash", False),
        ("gemini-1.5-pro", False),
        ("claude-sonnet-4", True),  # non-Gemini -> default True
    ],
)
def test_supports_multimodal_function_response(
    model_id: str, expected: bool
) -> None:
    assert supports_multimodal_function_response(model_id) is expected


# === usage ==================================================================


def test_get_usage_camel_dict_with_reasoning() -> None:
    um = {
        "promptTokenCount": 100,
        "candidatesTokenCount": 40,
        "thoughtsTokenCount": 15,
        "cachedContentTokenCount": 30,
        "totalTokenCount": 185,
    }
    usage = get_usage(um)
    assert usage == {
        "input": 70,  # 100 - 30
        "output": 55,  # 40 + 15
        "input_tokens": 70,
        "output_tokens": 55,
        "cache_read": 30,
        "cache_write": 0,
        "reasoning": 15,
        "total_tokens": 185,
    }


def test_get_usage_snake_object() -> None:
    class _UM:
        prompt_token_count = 10
        candidates_token_count = 5
        thoughts_token_count = 0
        cached_content_token_count = 0
        total_token_count = 15

    usage = get_usage(_UM())
    assert usage["input"] == 10
    assert usage["output"] == 5
    assert usage["reasoning"] == 0
    assert usage["total_tokens"] == 15


def test_get_usage_negative_input_not_clamped() -> None:
    # pi parity: prompt - cached may go negative; do NOT clamp.
    um = {
        "promptTokenCount": 10,
        "cachedContentTokenCount": 30,
        "candidatesTokenCount": 5,
    }
    usage = get_usage(um)
    assert usage["input"] == -20
    assert usage["input_tokens"] == -20


def test_get_usage_none_returns_none() -> None:
    assert get_usage(None) is None


# === FIX 1: REAL SDK Part bytes thoughtSignature round-trip =================
# The google-genai Python SDK types ``Part.thought_signature`` (and
# ``Blob.data``) as ``Optional[bytes]`` and base64-DECODES the wire value, so
# a Part built from ``{'thoughtSignature': 'YWJjZA=='}`` yields ``b'abcd'``.
# These tests drive a *real* validated ``types.Part`` through the ingest seam
# (string fixtures elsewhere mask the bytes path) and assert the stored
# signature is normalized back to the ORIGINAL base64 STRING, then replayed
# through ``convert_messages`` so the SDK round-trips to the same bytes.


# Wire base64 string -> SDK decodes to b'abcd' -> we re-encode to this string.
_WIRE_SIG = "YWJjZA=="


async def _drive_stream(
    chunks: list[Any], model: Model
) -> tuple[GoogleStreamState, list[Any]]:
    async def _aiter() -> Any:
        for c in chunks:
            yield c

    state = GoogleStreamState()
    events = [ev async for ev in process_google_stream(_aiter(), state, model)]
    return state, events


def _assert_sdk_roundtrips_to_bytes(part_dict: dict[str, Any]) -> None:
    """The replayed dict must decode back to b'abcd' under the real SDK."""

    from google.genai import types

    part = types.Part.model_validate(part_dict)
    assert part.thought_signature == b"abcd"


async def test_ingest_real_part_text_signature_bytes_to_str() -> None:
    from google.genai import types

    part = types.Part.model_validate({"text": "hi", "thoughtSignature": _WIRE_SIG})
    # Precondition: the real SDK gives us BYTES, not str.
    assert part.thought_signature == b"abcd"

    model = _model()
    chunk = {"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]}
    state, _ = await _drive_stream([chunk], model)

    text_block = next(b for b in state.content if isinstance(b, TextContent))
    assert text_block.text == "hi"
    # Stored as the ORIGINAL base64 STRING (not bytes, not corrupt decode).
    assert text_block.text_signature == _WIRE_SIG
    assert isinstance(text_block.text_signature, str)

    # Replay: convert_messages re-attaches it and the SDK round-trips it back.
    assistant = AssistantMessage(
        content=[text_block],
        api="google-generative-ai",
        provider="google",
        model="gemini-2.5-flash",
    )
    parts = convert_messages(model, Context(messages=[assistant]))[0]["parts"]
    assert parts[0] == {"text": "hi", "thoughtSignature": _WIRE_SIG}
    _assert_sdk_roundtrips_to_bytes(parts[0])


async def test_ingest_real_part_thinking_signature_bytes_to_str() -> None:
    from google.genai import types

    part = types.Part.model_validate(
        {"text": "ponder", "thought": True, "thoughtSignature": _WIRE_SIG}
    )
    assert part.thought_signature == b"abcd"

    model = _model()
    chunk = {"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]}
    state, _ = await _drive_stream([chunk], model)

    th_block = next(b for b in state.content if isinstance(b, ThinkingContent))
    assert th_block.thinking == "ponder"
    assert th_block.thinking_signature == _WIRE_SIG
    assert isinstance(th_block.thinking_signature, str)

    assistant = AssistantMessage(
        content=[th_block],
        api="google-generative-ai",
        provider="google",
        model="gemini-2.5-flash",
    )
    parts = convert_messages(model, Context(messages=[assistant]))[0]["parts"]
    assert parts[0] == {
        "thought": True,
        "text": "ponder",
        "thoughtSignature": _WIRE_SIG,
    }
    _assert_sdk_roundtrips_to_bytes(parts[0])


async def test_ingest_real_part_tool_call_signature_bytes_to_str() -> None:
    from google.genai import types

    part = types.Part.model_validate(
        {
            "functionCall": {"name": "fn", "args": {"a": 1}},
            "thoughtSignature": _WIRE_SIG,
        }
    )
    assert part.thought_signature == b"abcd"

    model = _model()
    chunk = {"candidates": [{"content": {"parts": [part]}, "finishReason": "STOP"}]}
    state, _ = await _drive_stream([chunk], model)

    tc = next(b for b in state.content if isinstance(b, ToolCallContent))
    assert tc.tool_name == "fn"
    assert tc.input == {"a": 1}
    # Pre-fix this stored b'abcd' (bytes) -> next-turn regex crash.
    assert tc.thought_signature == _WIRE_SIG
    assert isinstance(tc.thought_signature, str)

    # Replay must NOT raise (is_valid_thought_signature on a str, not bytes).
    assistant = AssistantMessage(
        content=[tc],
        api="google-generative-ai",
        provider="google",
        model="gemini-2.5-flash",
    )
    parts = convert_messages(model, Context(messages=[assistant]))[0]["parts"]
    assert parts[0] == {
        "functionCall": {"name": "fn", "args": {"a": 1}},
        "thoughtSignature": _WIRE_SIG,
    }
    _assert_sdk_roundtrips_to_bytes(parts[0])


def test_send_inline_data_base64_str_roundtrips_under_sdk() -> None:
    # convert_messages emits inlineData.data as a base64 STR; the SDK's
    # Blob.data is TYPE_BYTES and base64-decodes a str dict value, so passing
    # the str (not pre-decoded bytes) is correct — it re-encodes to the same
    # wire value. Guards against a regression that pre-decodes to bytes.
    from google.genai import types

    model = _model()
    ctx = Context(
        messages=[
            UserMessage(
                content=[ImageContent(mime_type="image/png", data=_WIRE_SIG)]
            )
        ]
    )
    parts = convert_messages(model, ctx)[0]["parts"]
    assert parts[0] == {
        "inlineData": {"mimeType": "image/png", "data": _WIRE_SIG}
    }
    part = types.Part.model_validate(parts[0])
    assert part.inline_data.data == b"abcd"
