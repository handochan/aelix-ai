"""Google ``genai`` client-wrapper tests — #15 (Workflow A, dormant).

Covers the ``api_version=""`` quirk for the Gemini Developer API, Vertex
client auth resolution (explicit API key vs ADC project/location), the
custom-base-url handling, and the double-await stream seam. The
``google.genai.Client`` constructor is monkeypatched to capture kwargs without
touching the network.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_ai.providers import _google_client


class _FakeClient:
    """Captures the kwargs ``genai.Client(...)`` was called with."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs


@pytest.fixture(autouse=True)
def _patch_genai(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.genai as genai

    monkeypatch.setattr(genai, "Client", _FakeClient)
    _FakeClient.last_kwargs = {}


# === Gemini Developer API client ===


def test_create_client_sets_api_version_empty_when_base_url_present() -> None:
    _google_client.create_client(
        api_key="k", base_url="https://generativelanguage.googleapis.com/v1beta"
    )
    http = _FakeClient.last_kwargs["http_options"]
    # The base URL already includes /v1beta — the SDK must NOT append a version.
    assert http["api_version"] == ""
    assert http["base_url"] == "https://generativelanguage.googleapis.com/v1beta"


def test_create_client_omits_http_options_without_base_url() -> None:
    _google_client.create_client(api_key="k")
    assert "http_options" not in _FakeClient.last_kwargs
    assert _FakeClient.last_kwargs["api_key"] == "k"


def test_create_client_forwards_headers_and_timeout() -> None:
    _google_client.create_client(
        api_key="k",
        base_url="https://x/v1beta",
        headers={"x-test": "1"},
        timeout_ms=5000,
    )
    http = _FakeClient.last_kwargs["http_options"]
    assert http["headers"] == {"x-test": "1"}
    assert http["timeout"] == 5000


def test_create_client_expands_base_url_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_HOST", "example.com")
    _google_client.create_client(api_key="k", base_url="https://{MY_HOST}/v1beta")
    http = _FakeClient.last_kwargs["http_options"]
    assert http["base_url"] == "https://example.com/v1beta"


# === Vertex client ===


def test_create_vertex_client_with_api_key() -> None:
    _google_client.create_vertex_client(
        api_key="vk", base_url="https://{location}-aiplatform.googleapis.com"
    )
    kw = _FakeClient.last_kwargs
    assert kw["vertexai"] is True
    assert kw["api_key"] == "vk"
    # The catalog base URL still carries {location} → no custom base_url.
    assert "base_url" not in kw["http_options"]
    # apiVersion defaults to v1 (not the "" Gemini quirk).
    assert kw["http_options"]["api_version"] == "v1"
    assert "project" not in kw


def test_create_vertex_client_with_adc_project_location() -> None:
    _google_client.create_vertex_client(
        project="my-proj",
        location="us-central1",
        base_url="https://{location}-aiplatform.googleapis.com",
    )
    kw = _FakeClient.last_kwargs
    assert kw["vertexai"] is True
    assert "api_key" not in kw
    assert kw["project"] == "my-proj"
    assert kw["location"] == "us-central1"
    assert kw["http_options"]["api_version"] == "v1"


def test_create_vertex_client_custom_base_url_with_version() -> None:
    # An explicit non-placeholder base URL that includes a version path clears
    # api_version and sets the COLLECTION resource scope (pi buildHttpOptions).
    _google_client.create_vertex_client(
        api_key="vk", base_url="https://custom.example.com/v1"
    )
    http = _FakeClient.last_kwargs["http_options"]
    assert http["base_url"] == "https://custom.example.com/v1"
    assert http["base_url_resource_scope"] == "COLLECTION"
    assert http["api_version"] == ""


def test_create_vertex_client_custom_base_url_without_version() -> None:
    _google_client.create_vertex_client(
        api_key="vk", base_url="https://custom.example.com"
    )
    http = _FakeClient.last_kwargs["http_options"]
    assert http["base_url"] == "https://custom.example.com"
    # No version segment in the path → keep the default v1.
    assert http["api_version"] == "v1"


# === double-await stream seam ===


class _FakeChunkIter:
    def __aiter__(self) -> _FakeChunkIter:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _FakeModels:
    def __init__(self, captured: dict[str, Any]) -> None:
        self._captured = captured

    async def generate_content_stream(self, **kwargs: Any) -> _FakeChunkIter:
        self._captured.update(kwargs)
        return _FakeChunkIter()


class _FakeAio:
    def __init__(self, captured: dict[str, Any]) -> None:
        self.models = _FakeModels(captured)


class _FakeStreamingClient:
    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}
        self.aio = _FakeAio(self.captured)


async def test_open_generate_content_stream_double_awaits() -> None:
    client = _FakeStreamingClient()
    params = {"model": "gemini-2.5-flash", "contents": [], "config": {"x": 1}}
    it = await _google_client.open_generate_content_stream(client, params)
    assert hasattr(it, "__anext__")
    assert client.captured["model"] == "gemini-2.5-flash"
    assert client.captured["config"] == {"x": 1}
