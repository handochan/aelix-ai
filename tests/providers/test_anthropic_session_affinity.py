"""ADR-0190 (#36 anthropic side) — ``x-session-affinity`` header injection.

Item 1 (MEDIUM review gap): the ``x-session-affinity`` header injection in
``stream_anthropic`` (the non-OAuth api-key ``else`` branch) is the only wired
consumer of :mod:`aelix_ai.providers._anthropic_compat`
(``get_compat(model).send_session_affinity_headers``). Every other adapter
stream test passes ``opts.client=<mock>``, which takes the
``client = opts.client`` branch and never runs the header-injecting
``else`` branch.

These tests drop ``opts.client`` and instead spy
:func:`aelix_ai.providers.anthropic.create_async_client` to capture the
``default_headers`` the adapter builds — mirroring the spy pattern in
``tests/oauth/test_anthropic_adapter_oauth_passthrough.py`` and
``tests/providers/test_adr0135_reasoning_wiring.py``.

Pi parity: ``anthropic.ts:862-863``. ADR-0190 divergence #1: the OAuth
branch never injects ``x-session-affinity``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.streaming import Context, Model, SimpleStreamOptions


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


def _fireworks_model() -> Model:
    return Model(
        api="anthropic-messages",
        id="claude-3",
        provider="fireworks",
        base_url="https://api.fireworks.ai/inference/v1",
    )


def _anthropic_model() -> Model:
    return Model(
        api="anthropic-messages",
        id="claude-3",
        provider="anthropic",
        base_url="https://api.anthropic.com",
    )


async def _capture_default_headers(
    model: Model, opts: SimpleStreamOptions
) -> dict[str, Any]:
    """Run ``stream_anthropic`` with ``create_async_client`` spied and return
    the ``default_headers`` kwarg the adapter built (``{}`` when ``None``)."""

    captured: dict[str, Any] = {}

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubClient()

    with patch(
        "aelix_ai.providers.anthropic.create_async_client", side_effect=_spy
    ):
        async for _ in stream_anthropic(model, Context(), opts):
            pass
    return captured.get("default_headers") or {}


async def test_fireworks_session_id_injects_affinity_header() -> None:
    """(1) fireworks + session_id → ``x-session-affinity`` == session_id."""

    opts = SimpleStreamOptions(api_key="sk-ant-classic", session_id="sess-123")
    headers = await _capture_default_headers(_fireworks_model(), opts)
    assert headers.get("x-session-affinity") == "sess-123"


async def test_plain_anthropic_omits_affinity_header() -> None:
    """(2) plain anthropic + session_id → header omitted (compat False)."""

    opts = SimpleStreamOptions(api_key="sk-ant-classic", session_id="sess-123")
    headers = await _capture_default_headers(_anthropic_model(), opts)
    assert "x-session-affinity" not in headers


async def test_oauth_token_never_injects_affinity_header() -> None:
    """(3) OAuth token on fireworks + session_id → header omitted.

    ADR-0190 divergence #1: the OAuth client-build branch never injects
    ``x-session-affinity`` (pi omits it there too).
    """

    opts = SimpleStreamOptions(api_key="sk-ant-oat-XYZ", session_id="sess-123")
    headers = await _capture_default_headers(_fireworks_model(), opts)
    assert "x-session-affinity" not in headers


async def test_none_session_id_omits_affinity_header() -> None:
    """(4) fireworks + session_id=None → header omitted."""

    opts = SimpleStreamOptions(api_key="sk-ant-classic", session_id=None)
    headers = await _capture_default_headers(_fireworks_model(), opts)
    assert "x-session-affinity" not in headers
