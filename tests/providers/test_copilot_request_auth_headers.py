"""GitHub Copilot request-path auth + dynamic headers (Copilot model-use fix).

Root cause (audit workflow wf_e58ec19a): the Anthropic adapter had NO
``github-copilot`` client-construction branch, so the 6 Copilot Claude models
(``api="anthropic-messages"``) sent the Copilot bearer token as ``x-api-key``
instead of ``Authorization: Bearer``. The GitHub Copilot proxy authenticates
via Bearer, so selecting any Copilot Claude model failed on first use (401).
Separately, the per-request dynamic copilot headers (``X-Initiator`` /
``Openai-Intent`` / ``Copilot-Vision-Request``) were applied only by the OpenAI
**Responses** adapter, never on the anthropic or completions copilot paths.

These tests spy ``create_async_client`` in each adapter module (dropping
``opts.client`` so the header-building branch actually runs) and assert the
outgoing auth + copilot headers. Pi parity: ``providers/anthropic.ts``
github-copilot createClient branch (``authToken`` → ``Authorization: Bearer``,
``isOAuthToken:false`` so no oauth/claude-code betas) +
``buildCopilotDynamicHeaders`` applied uniformly across all three adapters.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    UserMessage,
)
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.providers.openai_completions import stream_openai_completions
from aelix_ai.streaming import Context, Model, SimpleStreamOptions

_COPILOT_STATIC_HEADERS: dict[str, str] = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}
# A realistic Copilot bearer token — semicolon-delimited, NOT ``sk-ant-oat``,
# so ``is_oauth_token`` correctly refuses it and the fix must key off
# ``model.provider`` instead.
_COPILOT_TOKEN = "tid=abc123;exp=9999999999;proxy-ep=proxy.individual.githubcopilot.com"


# ---------------------------------------------------------------- models


def _copilot_claude_model() -> Model:
    return Model(
        api="anthropic-messages",
        id="claude-opus-4.5",
        provider="github-copilot",
        base_url="https://api.individual.githubcopilot.com",
        headers=dict(_COPILOT_STATIC_HEADERS),
    )


def _plain_anthropic_model() -> Model:
    return Model(
        api="anthropic-messages",
        id="claude-opus-4.5",
        provider="anthropic",
        base_url="https://api.anthropic.com",
    )


def _copilot_completions_model() -> Model:
    return Model(
        api="openai-completions",
        id="gpt-4o",
        provider="github-copilot",
        base_url="https://api.individual.githubcopilot.com",
        headers=dict(_COPILOT_STATIC_HEADERS),
    )


def _plain_completions_model() -> Model:
    return Model(
        api="openai-completions",
        id="gpt-4o",
        provider="openai",
        base_url="",
    )


def _text_ctx() -> Context:
    return Context(messages=[UserMessage(content=[TextContent(text="hi")])])


def _image_ctx() -> Context:
    return Context(
        messages=[
            UserMessage(content=[ImageContent(mime_type="image/png", data="AAAA")])
        ]
    )


def _agent_ctx() -> Context:
    """Multi-turn history whose LAST message is an assistant turn — the common
    post-tool-call shape that must emit ``X-Initiator: agent``."""

    return Context(
        messages=[
            UserMessage(content=[TextContent(text="hi")]),
            AssistantMessage(content=[TextContent(text="hello")]),
        ]
    )


# ------------------------------------------------------ anthropic spy harness


class _StubStream:
    """Empty scripted stream that completes cleanly (stop_reason end_turn)."""

    response = None

    def __aiter__(self) -> Any:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def get_final_message(self) -> Any:
        class _M:
            stop_reason = "end_turn"

        return _M()


class _StubManager:
    async def __aenter__(self) -> Any:
        return _StubStream()

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _StubMessages:
    def stream(self, **_kwargs: Any) -> Any:
        return _StubManager()


class _StubClient:
    messages = _StubMessages()


async def _capture_anthropic(
    model: Model, opts: SimpleStreamOptions, context: Context
) -> dict[str, Any]:
    """Run ``stream_anthropic`` with ``create_async_client`` spied; return the
    full captured kwargs (``default_headers`` + ``api_key`` + …)."""

    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubClient()

    with patch(
        "aelix_ai.providers.anthropic.create_async_client", side_effect=_spy
    ):
        async for _ in stream_anthropic(model, context, opts):
            pass
    return captured


# ----------------------------------------------------- completions spy harness


class _StopCapture(Exception):
    """Sentinel raised from the spy to short-circuit after capturing kwargs.

    ``stream_openai_completions`` wraps any exception into an
    ``AssistantErrorEvent`` (never re-raises), so the async-for completes
    cleanly once the client-build kwargs are captured.
    """


async def _capture_completions(
    model: Model, opts: SimpleStreamOptions, context: Context
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise _StopCapture

    with patch(
        "aelix_ai.providers.openai_completions.create_async_client",
        side_effect=_spy,
    ):
        async for _ in stream_openai_completions(model, context, opts):
            pass
    return captured


# =============================================================== anthropic


async def test_copilot_claude_uses_authorization_bearer_not_x_api_key() -> None:
    """THE fix: Copilot Claude auths via Authorization: Bearer, api_key blank."""

    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    captured = await _capture_anthropic(_copilot_claude_model(), opts, _text_ctx())
    headers = captured.get("default_headers") or {}

    assert headers.get("Authorization") == f"Bearer {_COPILOT_TOKEN}"
    # Blank api_key → the Anthropic SDK never emits an x-api-key auth header.
    assert captured.get("api_key") == ""
    # Request still goes to the Copilot proxy, not api.anthropic.com.
    assert captured.get("base_url") == "https://api.individual.githubcopilot.com"


async def test_copilot_claude_preserves_static_headers_no_oauth_beta() -> None:
    """Static COPILOT_HEADERS ride along; no oauth-2025 identity beta (isOAuthToken:false)."""

    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    headers = (
        await _capture_anthropic(_copilot_claude_model(), opts, _text_ctx())
    ).get("default_headers") or {}

    assert headers.get("Editor-Version") == "vscode/1.107.0"
    assert headers.get("Copilot-Integration-Id") == "vscode-chat"
    assert "oauth-2025-04-20" not in (headers.get("anthropic-beta") or "")


async def test_copilot_claude_dynamic_headers_present() -> None:
    """X-Initiator + Openai-Intent stamped; no vision header without an image."""

    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    headers = (
        await _capture_anthropic(_copilot_claude_model(), opts, _text_ctx())
    ).get("default_headers") or {}

    assert headers.get("X-Initiator") == "user"
    assert headers.get("Openai-Intent") == "conversation-edits"
    assert "Copilot-Vision-Request" not in headers


async def test_copilot_claude_vision_header_on_image() -> None:
    """Copilot-Vision-Request == 'true' when the turn carries an image."""

    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    headers = (
        await _capture_anthropic(_copilot_claude_model(), opts, _image_ctx())
    ).get("default_headers") or {}

    assert headers.get("Copilot-Vision-Request") == "true"


async def test_copilot_claude_initiator_agent_on_assistant_last() -> None:
    """Multi-turn (assistant-last) history → X-Initiator: agent, not user."""

    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    headers = (
        await _capture_anthropic(_copilot_claude_model(), opts, _agent_ctx())
    ).get("default_headers") or {}

    assert headers.get("X-Initiator") == "agent"


async def test_plain_anthropic_keeps_x_api_key_branch() -> None:
    """Regression guard: a non-copilot anthropic model must NOT take the copilot
    branch — it keeps its api_key on the client (SDK → x-api-key) and emits no
    Authorization / copilot dynamic headers."""

    opts = SimpleStreamOptions(api_key="sk-ant-classic")
    captured = await _capture_anthropic(_plain_anthropic_model(), opts, _text_ctx())
    headers = captured.get("default_headers") or {}

    assert captured.get("api_key") == "sk-ant-classic"
    assert "Authorization" not in headers
    assert "X-Initiator" not in headers


# ============================================================= completions


async def test_copilot_completions_dynamic_headers_present() -> None:
    """openai-completions copilot models also get X-Initiator + Openai-Intent."""

    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    headers = (
        await _capture_completions(
            _copilot_completions_model(), opts, _text_ctx()
        )
    ).get("default_headers") or {}

    assert headers.get("X-Initiator") == "user"
    assert headers.get("Openai-Intent") == "conversation-edits"
    # Static copilot headers still present via model.headers.
    assert headers.get("Copilot-Integration-Id") == "vscode-chat"


async def test_copilot_completions_vision_header_on_image() -> None:
    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    headers = (
        await _capture_completions(
            _copilot_completions_model(), opts, _image_ctx()
        )
    ).get("default_headers") or {}

    assert headers.get("Copilot-Vision-Request") == "true"


async def test_copilot_completions_initiator_agent_on_assistant_last() -> None:
    """Multi-turn (assistant-last) history → X-Initiator: agent on completions too."""

    opts = SimpleStreamOptions(api_key=_COPILOT_TOKEN)
    headers = (
        await _capture_completions(
            _copilot_completions_model(), opts, _agent_ctx()
        )
    ).get("default_headers") or {}

    assert headers.get("X-Initiator") == "agent"


async def test_plain_completions_omits_copilot_dynamic_headers() -> None:
    """Regression guard: a non-copilot openai-completions model gets no copilot headers."""

    opts = SimpleStreamOptions(api_key="sk-openai")
    headers = (
        await _capture_completions(_plain_completions_model(), opts, _text_ctx())
    ).get("default_headers") or {}

    assert "X-Initiator" not in headers
    assert "Openai-Intent" not in headers
