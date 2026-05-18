"""Sprint 6b (Phase 4.2, §G) — OpenAI completions unit tests.

Tests for the pure helpers in :mod:`aelix_ai.providers.openai_completions`:

- ``_map_stop_reason`` for the Pi finish_reason → Aelix StopReason matrix.
- ``build_params`` for compat-driven parameter shaping.
- ``convert_tools`` for the strict-mode toggle.
- ``convert_messages`` shape sanity.
"""

from __future__ import annotations

from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers._openai_compat import detect_compat, get_compat
from aelix_ai.providers.openai_completions import (
    OpenAICompletionsOptions,
    _map_stop_reason,
    build_params,
    convert_messages,
    convert_tools,
)
from aelix_ai.streaming import Context, Model


def _model(**kwargs) -> Model:
    base = dict(
        api="openai-completions",
        id="gpt-4",
        provider="openai",
        base_url="https://api.openai.com/v1",
        input=["text"],
        max_tokens=1024,
    )
    base.update(kwargs)
    return Model(**base)


# === _map_stop_reason ===


def test_map_stop_reason_none_returns_stop() -> None:
    assert _map_stop_reason(None) == ("stop", None)


def test_map_stop_reason_null_string_returns_stop() -> None:
    assert _map_stop_reason("null") == ("stop", None)


def test_map_stop_reason_stop_and_end() -> None:
    assert _map_stop_reason("stop") == ("stop", None)
    assert _map_stop_reason("end") == ("stop", None)


def test_map_stop_reason_length() -> None:
    assert _map_stop_reason("length") == ("length", None)


def test_map_stop_reason_tool_calls_maps_to_tool_use() -> None:
    """Pi parity (P-57): adapter returns Pi's ``"toolUse"`` spelling."""

    assert _map_stop_reason("tool_calls") == ("toolUse", None)
    assert _map_stop_reason("function_call") == ("toolUse", None)


def test_map_stop_reason_content_filter() -> None:
    sr, em = _map_stop_reason("content_filter")
    assert sr == "error"
    assert em is not None and "content_filter" in em


def test_map_stop_reason_network_error() -> None:
    sr, em = _map_stop_reason("network_error")
    assert sr == "error"
    assert em is not None and "network_error" in em


def test_map_stop_reason_unknown_propagated() -> None:
    sr, em = _map_stop_reason("mystery")
    assert sr == "error"
    assert em is not None and "mystery" in em


# === convert_tools ===


def test_convert_tools_strict_field_present_by_default() -> None:
    class _Tool:
        name = "echo"
        description = "Echo a payload"
        parameters = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        }

    compat = detect_compat(_model())
    out = convert_tools([_Tool()], compat)
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "echo"
    assert out[0]["function"]["strict"] is False


def test_convert_tools_strict_field_omitted_for_moonshot() -> None:
    class _Tool:
        name = "echo"
        description = ""
        parameters = {}

    compat = detect_compat(_model(provider="moonshotai"))
    out = convert_tools([_Tool()], compat)
    assert "strict" not in out[0]["function"]


# === build_params ===


def test_build_params_openai_baseline() -> None:
    compat = detect_compat(_model())
    params = build_params(
        _model(), Context(system_prompt="be brief"), None, compat, "short"
    )
    assert params["model"] == "gpt-4"
    assert params["stream"] is True
    assert params["max_completion_tokens"] == 1024
    assert params["store"] is False
    assert params["stream_options"] == {"include_usage": True}
    # System prompt becomes a system message.
    assert params["messages"][0] == {"role": "system", "content": "be brief"}


def test_build_params_openrouter_reasoning_effort() -> None:
    model = _model(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        reasoning=True,
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["reasoning"] == {"effort": "high"}


def test_build_params_openrouter_reasoning_off_when_no_effort() -> None:
    model = _model(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        reasoning=True,
    )
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["reasoning"] == {"effort": "none"}


def test_build_params_deepseek_thinking_shape() -> None:
    model = _model(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        reasoning=True,
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="medium")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["thinking"] == {"type": "enabled"}
    assert params["reasoning_effort"] == "medium"


def test_build_params_together_max_tokens_field() -> None:
    model = _model(
        provider="together",
        base_url="https://api.together.ai/v1",
        max_tokens=2048,
    )
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["max_tokens"] == 2048
    assert "max_completion_tokens" not in params


def test_build_params_anthropic_cache_control_injected() -> None:
    model = _model(
        id="anthropic/claude-3-5-sonnet",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )
    compat = get_compat(model)
    params = build_params(
        model,
        Context(
            system_prompt="be brief",
            messages=[UserMessage(content=[TextContent(text="hi")])],
        ),
        None,
        compat,
        "short",
    )
    # System message text content carries cache_control.
    system_message = params["messages"][0]
    content = system_message.get("content")
    if isinstance(content, list):
        assert any("cache_control" in part for part in content if isinstance(part, dict))


def test_build_params_tool_choice_forwarded() -> None:
    model = _model()
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(tool_choice="auto")
    params = build_params(model, Context(), opts, compat, "short")
    assert params.get("tool_choice") == "auto"


def test_build_params_zai_enable_thinking_toggle() -> None:
    model = _model(
        provider="zai",
        base_url="https://api.z.ai/v1",
        reasoning=True,
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    assert params.get("enable_thinking") is True


def test_build_params_tool_history_seeds_tools_field() -> None:
    """When messages contain tool calls but tools is empty, params['tools'] = []."""

    model = _model()
    compat = get_compat(model)
    messages = [
        AssistantMessage(
            content=[ToolCallContent(tool_call_id="t1", tool_name="x")]
        ),
        ToolResultMessage(
            tool_call_id="t1", content=[TextContent(text="ok")]
        ),
    ]
    params = build_params(
        model, Context(messages=messages), None, compat, "short"
    )
    assert params.get("tools") == []


# === convert_messages ===


def test_convert_messages_single_user_message() -> None:
    model = _model()
    compat = get_compat(model)
    out = convert_messages(
        model,
        Context(
            messages=[UserMessage(content=[TextContent(text="hello")])],
        ),
        compat,
    )
    assert out == [{"role": "user", "content": "hello"}]


def test_convert_messages_assistant_with_tool_calls() -> None:
    model = _model()
    compat = get_compat(model)
    messages = [
        AssistantMessage(
            content=[
                TextContent(text="reasoning"),
                ToolCallContent(
                    tool_call_id="t1", tool_name="echo", input={"x": 1}
                ),
            ]
        ),
        ToolResultMessage(
            tool_call_id="t1",
            content=[TextContent(text="ok")],
        ),
    ]
    out = convert_messages(
        model, Context(messages=messages), compat
    )
    assistant = next(m for m in out if m.get("role") == "assistant")
    assert assistant["content"] == "reasoning"
    assert len(assistant["tool_calls"]) == 1
    assert assistant["tool_calls"][0]["function"]["name"] == "echo"
    tool = next(m for m in out if m.get("role") == "tool")
    assert tool["tool_call_id"] == "t1"
    assert tool["content"] == "ok"


def test_convert_messages_developer_role_for_reasoning_model() -> None:
    model = _model(provider="openai", reasoning=True)
    compat = get_compat(model)
    out = convert_messages(
        model, Context(system_prompt="thoughtful"), compat
    )
    assert out[0]["role"] == "developer"
    assert out[0]["content"] == "thoughtful"


def test_convert_messages_skips_empty_assistant_turn() -> None:
    """Pi parity: assistant message with no content + no tool_calls dropped."""

    model = _model()
    compat = get_compat(model)
    messages = [AssistantMessage(content=[])]
    out = convert_messages(model, Context(messages=messages), compat)
    assert out == []


def test_convert_messages_sanitizes_surrogates() -> None:
    model = _model()
    compat = get_compat(model)
    bad = "hi \ud800 there"
    out = convert_messages(
        model,
        Context(
            messages=[UserMessage(content=[TextContent(text=bad)])],
        ),
        compat,
    )
    assert "\ud800" not in out[0]["content"]
