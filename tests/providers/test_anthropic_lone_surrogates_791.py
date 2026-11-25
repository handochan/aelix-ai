"""#791 — an Anthropic request whose text holds a lone surrogate is still sent.

Before #791 the Anthropic adapter passed every text through unchanged. The SDK
serializes a request body with ``ensure_ascii=False`` and encodes it as UTF-8,
so one lone surrogate (a code point in U+D800..U+DFFF without its partner) in a
tool result, a user or assistant text, the system prompt, a thinking block or a
tool call's arguments raised ``UnicodeEncodeError`` before the request left:
the turn ended with "surrogates not allowed" and nothing reached the transport
(every case below failed that way on 6ef1a4b6).

Pi (``packages/ai/src/api/anthropic-messages.ts`` at ``c11a73bfb``) calls
``sanitizeSurrogates`` on tool-result text, the system prompt, user and
assistant text and thinking, signed or not, and leaves a thinking block's
signature and a redacted block's data alone. The adapter now does the same, and
also sanitizes a tool call's arguments, which pi sends as they are.

Every case goes through ``stream_anthropic`` and the adapter's own client
factory; the SDK's ``__init__`` is wrapped only to hand it an
:class:`httpx.MockTransport`, whose handler records the request body and then
refuses the connection. A request that serialized ends in the SDK's
"Connection error."; one that did not never reaches the handler. No model is
called and no socket is opened.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.streaming import Context, Model, SimpleStreamOptions
from anthropic import AsyncAnthropic

HALF = "\ud83d"  # the high half of U+1F648, alone
EMOJI = "\U0001f648"  # one code point, four UTF-8 bytes
SPLIT = chr(0xD83D) + chr(0xDE48)  # the same character as two code points (two escapes)


class _Wire:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(request.content)
        raise httpx.ConnectError("refused by the test transport", request=request)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> _Wire:
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    rec = _Wire()
    real_init = AsyncAnthropic.__init__

    def _init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
        kwargs["max_retries"] = 0
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(AsyncAnthropic, "__init__", _init)
    return rec


def _model() -> Model:
    return Model(
        id="claude-sonnet-4-5",
        name="Claude Sonnet 4.5",
        api="anthropic-messages",
        provider="anthropic",
        base_url="http://127.0.0.1:9",
        reasoning=False,
        input=["text", "image"],
        context_window=200000,
        max_tokens=64000,
    )


def _context(
    *,
    system: str = "you help",
    user: str = "go",
    assistant: str = "sure",
    thinking: str | None = None,
    signature: str = "SIG-opaque",
    args: dict[str, Any] | None = None,
    tool_result: str = "ok",
) -> Context:
    """user -> assistant (thinking?, text, tool call) -> tool result, all same-model."""

    model = _model()
    content: list[Any] = []
    if thinking is not None:
        content.append(ThinkingContent(thinking=thinking, thinking_signature=signature))
    content.append(TextContent(text=assistant))
    content.append(
        ToolCallContent(tool_call_id="toolu_1", tool_name="read", input=args or {"path": "a"})
    )
    return Context(
        system_prompt=system,
        messages=[
            UserMessage(content=[TextContent(text=user)], timestamp=0),
            AssistantMessage(
                content=content,
                api=model.api,
                provider=model.provider,
                model=model.id,
                stop_reason="toolUse",
                timestamp=0,
            ),
            ToolResultMessage(
                tool_call_id="toolu_1",
                tool_name="read",
                content=[TextContent(text=tool_result)],
                timestamp=0,
            ),
        ],
    )


async def _send(wire: _Wire, context: Context) -> tuple[dict[str, Any], bytes]:
    """Run one turn; return the request body the transport saw, parsed and raw."""

    events = [
        event
        async for event in stream_anthropic(
            _model(), context, SimpleStreamOptions(api_key="sk-ant-test")
        )
    ]
    errors = [e.error_message or "" for e in events if getattr(e, "type", "") == "error"]
    assert len(wire.bodies) == 1, f"the request never reached the transport: {errors}"
    assert errors and "Connection error" in errors[-1], errors
    raw = wire.bodies[0]
    return json.loads(raw.decode("utf-8")), raw


def _assistant_block(body: dict[str, Any], block_type: str) -> dict[str, Any]:
    blocks = [b for b in body["messages"][1]["content"] if b["type"] == block_type]
    assert len(blocks) == 1, body["messages"][1]["content"]
    return blocks[0]


def _surrogates_in(value: Any) -> list[str]:
    """Every string anywhere in ``value`` (keys too) that holds a surrogate."""

    if isinstance(value, str):
        return [value] if any(0xD800 <= ord(c) <= 0xDFFF for c in value) else []
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _surrogates_in(k) + _surrogates_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in _surrogates_in(v)]
    return []


#: Each text site: how to put ``text`` there, and how to read it back off the wire.
_SITES: dict[str, tuple[Callable[[str], Context], Callable[[dict[str, Any]], str]]] = {
    "tool_result": (
        lambda t: _context(tool_result=t),
        lambda b: b["messages"][2]["content"][0]["content"][0]["text"],
    ),
    "user": (lambda t: _context(user=t), lambda b: b["messages"][0]["content"][0]["text"]),
    "assistant": (
        lambda t: _context(assistant=t),
        lambda b: _assistant_block(b, "text")["text"],
    ),
    "system": (lambda t: _context(system=t), lambda b: b["system"]),
    "signed_thinking": (
        lambda t: _context(thinking=t),
        lambda b: _assistant_block(b, "thinking")["thinking"],
    ),
    "unsigned_thinking": (
        # No signature: sent as a text block (pi's downgrade), which carries the thinking.
        lambda t: _context(thinking=t, signature=""),
        lambda b: b["messages"][1]["content"][0]["text"],
    ),
    "tool_args": (
        lambda t: _context(args={"path": t}),
        lambda b: _assistant_block(b, "tool_use")["input"]["path"],
    ),
}


@pytest.mark.parametrize("site", list(_SITES))
async def test_a_lone_half_is_dropped_and_the_request_reaches_the_transport(
    wire: _Wire, site: str
) -> None:
    """The issue's probe: ``before <lone half> after`` at each site."""

    build, read = _SITES[site]
    body, _raw = await _send(wire, build(f"before {HALF} after"))
    assert read(body) == "before  after"
    assert _surrogates_in(body) == []


@pytest.mark.parametrize("site", list(_SITES))
async def test_an_emoji_and_a_split_pair_arrive_as_their_utf8_bytes(wire: _Wire, site: str) -> None:
    """A real emoji is untouched; its two halves as two code points become it again.

    Pi's regex keeps a high half followed by its low half, because in
    JavaScript that pair IS the character. Kept as two code points it could not
    be encoded at all, so it is joined.
    """

    build, read = _SITES[site]
    body, raw = await _send(wire, build(f"{EMOJI} a {HALF} b {SPLIT}"))
    assert read(body) == f"{EMOJI} a  b {EMOJI}"
    # Both arrive as the character's own four UTF-8 bytes, with no escape.
    assert raw.count(EMOJI.encode("utf-8")) == 2
    assert b"\\ud8" not in raw.lower()


async def test_a_signed_thinking_block_keeps_its_signature_and_stays_signed(
    wire: _Wire,
) -> None:
    """As pi does: the text is sanitized, the signature is sent as it is.

    A split pair comes back as the exact character the provider streamed, so
    the text matches what was signed; only a lone half changes it.
    """

    body, _raw = await _send(
        wire, _context(thinking=f"think {SPLIT} {HALF}hard", signature="EqQBCkYIAxgCKkB+/=")
    )
    assert _assistant_block(body, "thinking") == {
        "type": "thinking",
        "thinking": f"think {EMOJI} hard",
        "signature": "EqQBCkYIAxgCKkB+/=",
    }


async def test_tool_call_arguments_are_sanitized_in_keys_and_nested_values(
    wire: _Wire,
) -> None:
    """A divergence from pi, which sends ``arguments`` as they are.

    The adapter's stream parser (``json.loads``) turns a lone ``\\u`` escape
    in a model's arguments into a lone surrogate, and the call is replayed
    with every later request. In JavaScript ``JSON.stringify`` escapes it and
    the request is still built; here the body cannot be, so every later turn
    of the session would fail.
    """

    args = {
        f"note{HALF}": f"x{HALF}",
        "edits": [{"old": f"a{SPLIT}", "new": [HALF, 3, None, True, 1.5]}],
        "count": 2,
    }
    body, _raw = await _send(wire, _context(args=args))
    assert _assistant_block(body, "tool_use")["input"] == {
        "note": "x",
        "edits": [{"old": f"a{EMOJI}", "new": ["", 3, None, True, 1.5]}],
        "count": 2,
    }
