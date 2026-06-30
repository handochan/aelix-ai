"""Vertex AI thin-adapter tests — #15 (Workflow A, dormant).

Covers Vertex auth resolution (explicit ``GOOGLE_CLOUD_API_KEY`` vs ADC
project/location, placeholder/marker rejection), the Vertex-specific
thinking-family selection (no Gemma 4 branch; no flash-lite budget table —
a documented divergence from the Gemini adapter), ``build_params`` reuse of
the shared config builder, an end-to-end engine drive on a fake ``genai``
client, and the dormancy guard.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_ai.api_registry import get_registered_providers
from aelix_ai.providers import _google_shared
from aelix_ai.providers._google_shared import GoogleThinking
from aelix_ai.providers.google_vertex import (
    GOOGLE_VERTEX_API,
    GoogleVertexOptions,
    _resolve_location,
    _resolve_project,
    _resolve_vertex_api_key,
    _vertex_disabled_thinking_config,
    _vertex_google_budget,
    build_params,
    stream_google_vertex,
    stream_simple_google_vertex,
)
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    Context,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
)


def _model(**kw: Any) -> Model:
    base: dict[str, Any] = {
        "id": "gemini-2.5-flash",
        "name": "gemini-2.5-flash",
        "api": GOOGLE_VERTEX_API,
        "provider": "google-vertex",
        "base_url": "https://{location}-aiplatform.googleapis.com",
        "input": ["text"],
        "reasoning": True,
    }
    base.update(kw)
    return Model(**base)


# === Vertex auth resolution ===


def test_resolve_vertex_api_key_passes_real_key() -> None:
    assert _resolve_vertex_api_key("  real-key  ") == "real-key"


def test_resolve_vertex_api_key_rejects_marker_and_placeholder() -> None:
    assert _resolve_vertex_api_key("gcp-vertex-credentials") is None
    assert _resolve_vertex_api_key("<your-key-here>") is None
    assert _resolve_vertex_api_key("") is None
    assert _resolve_vertex_api_key(None) is None


def test_resolve_project_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-a")
    assert _resolve_project(GoogleVertexOptions()) == "proj-a"


def test_resolve_project_option_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-env")
    assert _resolve_project(GoogleVertexOptions(project="proj-opt")) == "proj-opt"


def test_resolve_project_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    with pytest.raises(RuntimeError, match="requires a project"):
        _resolve_project(GoogleVertexOptions())


def test_resolve_location_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    with pytest.raises(RuntimeError, match="requires a location"):
        _resolve_location(GoogleVertexOptions())


# === Vertex thinking-family divergence from the Gemini adapter ===


def test_vertex_flash_lite_budget_diverges_from_shared() -> None:
    # pi's Vertex getGoogleBudget has NO flash-lite table — a flash-lite id
    # falls through to the 2.5-flash table (minimal=128). The shared
    # (Gemini-flavored) helper keeps a flash-lite table (minimal=512). This
    # asserts the intentional Vertex-specific divergence.
    assert _vertex_google_budget("gemini-2.5-flash-lite", "minimal") == 128
    assert (
        _google_shared.get_google_budget("gemini-2.5-flash-lite", "minimal") == 512
    )


def test_vertex_disabled_thinking_config_has_no_gemma_branch() -> None:
    # pi's Vertex getDisabledThinkingConfig (google-vertex.ts:512-526) has NO
    # Gemma 4 branch — Gemma falls through to thinkingBudget:0 like Gemini 2.x.
    # The shared (Gemini-flavored) helper routes Gemma 4 to thinkingLevel:
    # MINIMAL. This asserts the intentional Vertex-specific divergence.
    assert _vertex_disabled_thinking_config("gemma-4") == {"thinkingBudget": 0}
    assert _google_shared.get_disabled_thinking_config("gemma-4") == {
        "thinkingLevel": "MINIMAL"
    }
    # Gemini 3 Pro/Flash still can't fully disable → lowest thinkingLevel.
    assert _vertex_disabled_thinking_config("gemini-3-pro") == {
        "thinkingLevel": "LOW"
    }
    assert _vertex_disabled_thinking_config("gemini-3-flash") == {
        "thinkingLevel": "MINIMAL"
    }
    # Gemini 2.x disables via budget 0.
    assert _vertex_disabled_thinking_config("gemini-2.5-flash") == {
        "thinkingBudget": 0
    }


def test_build_params_disabled_thinking_gemma_uses_vertex_config() -> None:
    # Latent today (no Gemma id in the Vertex catalog) but correctness: a Gemma
    # model with thinking disabled must emit thinkingBudget:0 (Vertex), NOT the
    # shared thinkingLevel:MINIMAL.
    opts = GoogleVertexOptions(thinking=GoogleThinking(enabled=False))
    config = build_params(_model(id="gemma-4"), Context(), opts)["config"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}


def test_build_params_reuses_shared_config_builder() -> None:
    opts = GoogleVertexOptions(
        thinking=GoogleThinking(enabled=True, level="HIGH"), temperature=0.1
    )
    config = build_params(_model(), Context(), opts)["config"]
    assert config["temperature"] == 0.1
    assert config["thinkingConfig"] == {"includeThoughts": True, "thinkingLevel": "HIGH"}


async def test_stream_simple_flash_lite_uses_vertex_budget() -> None:
    captured: dict[str, Any] = {}

    async def _payload(params: dict[str, Any], _m: Model) -> None:
        captured.update(params)

    client = _FakeGenAIClient(_text_chunks())
    opts = GoogleVertexOptions(
        client=client, reasoning="minimal", on_payload=_payload
    )
    await _collect(
        stream_simple_google_vertex(
            _model(id="gemini-2.5-flash-lite"), Context(), opts
        )
    )
    # Vertex flash-lite minimal → 128 (NOT the shared 512).
    assert captured["config"]["thinkingConfig"]["thinkingBudget"] == 128


# === Vertex does NOT raise on missing key (ADC path) ===


def test_stream_simple_does_not_raise_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_API_KEY", raising=False)
    # Vertex can authenticate via ADC, so stream_simple must NOT raise here.
    it = stream_simple_google_vertex(_model(), Context(), SimpleStreamOptions())
    assert hasattr(it, "__anext__")


# === Fake genai streaming client ===


class _FakeChunkIter:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> _FakeChunkIter:
        self._i = 0
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        if isinstance(c, Exception):
            raise c
        return c


class _FakeModels:
    def __init__(self, chunks: list[Any], captured: dict[str, Any]) -> None:
        self._chunks = chunks
        self._captured = captured

    async def generate_content_stream(self, **kwargs: Any) -> _FakeChunkIter:
        self._captured.update(kwargs)
        return _FakeChunkIter(self._chunks)


class _FakeAio:
    def __init__(self, chunks: list[Any], captured: dict[str, Any]) -> None:
        self.models = _FakeModels(chunks, captured)


class _FakeGenAIClient:
    def __init__(self, chunks: list[Any]) -> None:
        self.captured: dict[str, Any] = {}
        self.aio = _FakeAio(chunks, self.captured)


async def _collect(it: AsyncIterator[Any]) -> list[Any]:
    return [ev async for ev in it]


def _text_chunks() -> list[dict[str, Any]]:
    return [
        {
            "responseId": "resp_v1",
            "candidates": [{"content": {"parts": [{"text": "Hi"}]}}],
        },
        {
            "candidates": [
                {"content": {"parts": [{"text": "!"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 4,
                "candidatesTokenCount": 2,
                "totalTokenCount": 6,
            },
        },
    ]


async def test_adapter_drives_engine_end_to_end_with_injected_client() -> None:
    client = _FakeGenAIClient(_text_chunks())
    out = await _collect(
        stream_google_vertex(
            _model(), Context(), GoogleVertexOptions(client=client)
        )
    )
    deltas = [ev.delta for ev in out if isinstance(ev, TextDeltaEvent)]
    assert deltas == ["Hi", "!"]
    done = out[-1]
    assert isinstance(done, AssistantDoneEvent)
    assert done.reason == "stop"
    assert done.message.response_id == "resp_v1"
    assert done.message.api == GOOGLE_VERTEX_API


async def test_adapter_missing_project_surfaces_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No api key + no project/location → resolution error becomes an error
    # event (pi catches it in the stream try/catch), not a raise.
    monkeypatch.delenv("GOOGLE_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    out = await _collect(
        stream_google_vertex(_model(), Context(), GoogleVertexOptions())
    )
    assert isinstance(out[-1], AssistantErrorEvent)
    assert "project" in (out[-1].error_message or "")


# === dormancy guard ===


def test_importing_adapter_does_not_register_it() -> None:
    assert GOOGLE_VERTEX_API not in get_registered_providers()
