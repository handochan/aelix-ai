"""Sprint 6b W6 regression tests (ADR-0050 §H).

Each test pins a W4 / W5 finding that the W6 commit fixed; if a future
refactor reintroduces the drift, the matching test trips.

Coverage:

- **P-57** — ``_map_stop_reason`` returns Pi's ``"toolUse"`` spelling
  (covered exhaustively by the closure pin's parametrized
  ``test_map_stop_reason_matches_pi_fixture`` — kept here as a quick
  unit smoke).
- **P-58 / P-67** — :class:`ThinkingContent` dataclass round-trip; the
  OpenAI adapter populates ``thinking_signature`` from the captured
  reasoning field name.
- **P-59 / M-1** — OpenRouter ``provider`` routing flows when an
  :class:`OpenAICompletionsCompat` dataclass (NOT a dict override) sets
  ``open_router_routing`` directly.
- **P-60 / C-1** — ``_open_stream`` calls ``with_raw_response.create``
  with **keyword** arguments (not positional) so the real SDK validates
  them.
- **P-61** — :class:`ImageContent` reads ``mime_type`` + ``data`` over
  legacy ``source``.
- **P-63** — :func:`convert_tools` does NOT fall back to Anthropic's
  ``input_schema`` field.
- **P-68** — :class:`AssistantMessage` carries the ``api`` / ``provider``
  / ``model`` provenance trio when the OpenAI adapter mints a Done
  event.
- **P-75** — :class:`ToolResultMessage` carries ``tool_name`` and the
  ``_transform_messages._flush_synthetic`` orphan path propagates it.
- **M-6** — :func:`_normalize_tool_call_id` clamps to 40 chars for every
  provider (no ``provider == "openai"`` gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix_ai.providers._openai_compat import (
    OpenAICompletionsCompat,
    get_compat,
)
from aelix_ai.providers._transform_messages import transform_messages
from aelix_ai.providers.openai_completions import (
    _map_stop_reason,
    _normalize_tool_call_id,
    build_params,
    convert_tools,
    stream_openai_completions,
)
from aelix_ai.streaming import (
    AssistantDoneEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

# === P-57 smoke (closure pin owns the exhaustive matrix) ===


def test_p57_map_stop_reason_tool_use_uses_pi_camelcase() -> None:
    assert _map_stop_reason("tool_calls") == ("toolUse", None)
    assert _map_stop_reason("function_call") == ("toolUse", None)


# === P-58 / P-67 — ThinkingContent dataclass ===


def test_p58_thinking_content_dataclass_round_trips() -> None:
    tc = ThinkingContent(
        thinking="ponder", thinking_signature="reasoning_content", redacted=False
    )
    assert tc.thinking == "ponder"
    assert tc.thinking_signature == "reasoning_content"
    assert tc.redacted is False
    assert tc.type == "thinking"


# === P-59 / M-1 — OpenRouter routing via dataclass compat override ===


def test_p59_openrouter_routing_dict_via_dataclass_compat() -> None:
    """Pi parity (M-1): ``open_router_routing`` on the detected dataclass works.

    A caller that constructs an :class:`OpenAICompletionsCompat`
    directly (no dict override) and sets ``open_router_routing`` MUST
    see ``params["provider"]`` injected. Today the production
    ``detect_compat`` always returns an empty dict; this test pins the
    behavior so a future detection-path PR that populates
    ``open_router_routing`` keeps working.
    """

    model = Model(
        api="openai-completions",
        id="openai/gpt-4",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    # Synthesize a compat dataclass with non-empty routing.
    compat = OpenAICompletionsCompat(
        thinking_format="openrouter",
        open_router_routing={"order": ["fireworks"]},
    )
    params = build_params(model, Context(), None, compat, "short")
    assert params["provider"] == {"order": ["fireworks"]}


# === P-60 / C-1 — _open_stream uses keyword args + with_raw_response ===


@dataclass
class _Delta:
    content: str | None = None


@dataclass
class _Choice:
    delta: _Delta
    finish_reason: str | None = None


@dataclass
class _Chunk:
    id: str = "chatcmpl-1"
    model: str = "gpt-4"
    choices: list = field(default_factory=list)
    usage: Any = None


class _AsyncIter:
    def __init__(self, chunks: list, response: Any) -> None:
        self._chunks = chunks
        self.response = response

    def __aiter__(self) -> _AsyncIter:
        self._i = 0
        return self

    async def __anext__(self) -> _Chunk:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


@dataclass
class _HttpResponse:
    status_code: int = 200
    headers: dict = field(default_factory=lambda: {"x-pi": "ok"})


class _RawWrapper:
    def __init__(self, iterator: _AsyncIter, response: _HttpResponse) -> None:
        self._iterator = iterator
        self.http_response = response

    def parse(self) -> _AsyncIter:
        return self._iterator


class _WithRawResponse:
    def __init__(self, iterator: _AsyncIter, response: _HttpResponse) -> None:
        self._iterator = iterator
        self._response = response
        self.captured_positional: tuple = ()
        self.captured_kwargs: dict = {}

    async def create(self, *args: Any, **kwargs: Any) -> _RawWrapper:
        self.captured_positional = args
        self.captured_kwargs = kwargs
        return _RawWrapper(self._iterator, self._response)


class _Completions:
    def __init__(self, iterator: _AsyncIter, response: _HttpResponse) -> None:
        self.with_raw_response = _WithRawResponse(iterator, response)


class _Chat:
    def __init__(self, iterator: _AsyncIter, response: _HttpResponse) -> None:
        self.completions = _Completions(iterator, response)


class _FakeAsyncOpenAI:
    def __init__(self) -> None:
        chunks = [
            _Chunk(choices=[_Choice(delta=_Delta(content="hi"))]),
            _Chunk(choices=[_Choice(delta=_Delta(), finish_reason="stop")]),
        ]
        response = _HttpResponse()
        self.chat = _Chat(_AsyncIter(chunks, response), response)


async def test_p60_open_stream_forwards_params_as_kwargs() -> None:
    """Pi parity (C-1): adapter calls ``create(**params, **request_options)``."""

    client = _FakeAsyncOpenAI()
    model = Model(
        api="openai-completions",
        id="gpt-4",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )
    events: list = []
    async for ev in stream_openai_completions(
        model, Context(), SimpleStreamOptions(api_key="k", client=client)
    ):
        events.append(ev)

    wrr = client.chat.completions.with_raw_response
    # NO positional arguments; all params passed as kwargs.
    assert wrr.captured_positional == ()
    assert "model" in wrr.captured_kwargs
    assert "messages" in wrr.captured_kwargs
    assert wrr.captured_kwargs.get("stream") is True


# === P-61 — ImageContent mime_type + data preferred over source ===


def test_p61_image_content_prefers_mime_type_and_data() -> None:
    """Pi parity (P-61): ``mime_type`` + ``data`` win over legacy ``source``."""

    from aelix_ai.messages import UserMessage
    from aelix_ai.providers.openai_completions import convert_messages

    model = Model(
        api="openai-completions",
        id="gpt-4o",
        provider="openai",
        base_url="https://api.openai.com/v1",
        input=["text", "image"],
    )
    compat = get_compat(model)
    img = ImageContent(
        source="legacy-data-not-used",
        mime_type="image/jpeg",
        data="REAL-BASE64-PAYLOAD",
    )
    out = convert_messages(
        model, Context(messages=[UserMessage(content=[img])]), compat
    )
    # The user message's content is a list with one image_url part.
    parts = out[0]["content"]
    assert isinstance(parts, list)
    image_part = next(p for p in parts if p.get("type") == "image_url")
    url = image_part["image_url"]["url"]
    assert url == "data:image/jpeg;base64,REAL-BASE64-PAYLOAD"


def test_p61_image_content_falls_back_to_source_when_data_empty() -> None:
    """Sprint 6a back-compat seam — when ``data`` is empty, read ``source``."""

    from aelix_ai.messages import UserMessage
    from aelix_ai.providers.openai_completions import convert_messages

    model = Model(
        api="openai-completions",
        id="gpt-4o",
        provider="openai",
        base_url="https://api.openai.com/v1",
        input=["text", "image"],
    )
    compat = get_compat(model)
    img = ImageContent(source="legacy-base64-payload")
    out = convert_messages(
        model, Context(messages=[UserMessage(content=[img])]), compat
    )
    parts = out[0]["content"]
    image_part = next(p for p in parts if p.get("type") == "image_url")
    assert "legacy-base64-payload" in image_part["image_url"]["url"]


# === P-63 — convert_tools input_schema leak drop ===


def test_p63_convert_tools_ignores_input_schema_anthropic_leak() -> None:
    """Pi parity (P-63): ``convert_tools`` does NOT read ``input_schema``."""

    class _AnthropicShapedTool:
        name = "echo"
        description = "Echo"
        # NO ``parameters`` field — only Anthropic-shape ``input_schema``.
        input_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}

    compat = get_compat(
        Model(
            api="openai-completions",
            id="gpt-4",
            provider="openai",
            base_url="https://api.openai.com/v1",
        )
    )
    out = convert_tools([_AnthropicShapedTool()], compat)
    # Adapter now surfaces a clean empty-object schema; Anthropic leak
    # ignored.
    assert out[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {},
    }


# === P-68 — AssistantMessage provenance trio populated on Done ===


async def test_p68_assistant_message_provenance_populated() -> None:
    """Pi parity (P-68): ``api`` / ``provider`` / ``model`` populated on Done."""

    client = _FakeAsyncOpenAI()
    model = Model(
        api="openai-completions",
        id="gpt-4-turbo",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )
    done: AssistantDoneEvent | None = None
    async for ev in stream_openai_completions(
        model, Context(), SimpleStreamOptions(api_key="k", client=client)
    ):
        if isinstance(ev, AssistantDoneEvent):
            done = ev

    assert done is not None
    assert done.message.api == "openai-completions"
    assert done.message.provider == "openai"
    assert done.message.model == "gpt-4-turbo"


# === P-75 — ToolResultMessage.tool_name propagated by orphan synthesis ===


def test_p75_orphan_tool_result_carries_tool_name() -> None:
    """Pi parity (P-75): synthetic orphan tool result keeps ``tool_name``."""

    model = Model(
        api="openai-completions",
        id="gpt-4",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )
    messages = [
        AssistantMessage(
            content=[
                ToolCallContent(
                    tool_call_id="call_1",
                    tool_name="echo",
                )
            ]
        ),
        # Note: no matching ToolResultMessage → orphan synthesis fires.
    ]
    transformed = transform_messages(messages, model)
    # Final synthetic result should be appended.
    synth = next(
        m for m in transformed if isinstance(m, ToolResultMessage)
    )
    assert synth.tool_call_id == "call_1"
    assert synth.tool_name == "echo"
    assert synth.is_error is True
    assert synth.content[0] == TextContent(text="No result provided")


# === M-6 — _normalize_tool_call_id clamps for every provider ===


@pytest.mark.parametrize(
    "provider",
    ["openai", "openrouter", "deepseek", "groq", "xai", "together"],
)
def test_m6_normalize_tool_call_id_clamps_all_providers(provider: str) -> None:
    """Pi parity (M-6): 40-char clamp applies regardless of provider id."""

    long_id = "x" * 80
    model = Model(
        api="openai-completions",
        id="model",
        provider=provider,
        base_url="",
    )
    out = _normalize_tool_call_id(
        long_id, model, AssistantMessage(content=[])
    )
    assert len(out) == 40
