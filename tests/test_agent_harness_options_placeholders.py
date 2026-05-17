"""Tests for F-6 placeholder fields on :class:`AgentHarnessOptions` (Section B).

These fields are inert in Phase 1.4 — they exist so Phase 2.1 can wire
behavior without breaking constructors written today. The tests verify the
defaults and that each field accepts a correctly-typed value (Section B.5).
"""

from __future__ import annotations

from aelix_agent_core.harness.core import AgentHarnessOptions
from aelix_ai.streaming import Model


def test_harness_options_default_placeholders_none() -> None:
    """All 7 new placeholder fields default to ``None``."""

    options = AgentHarnessOptions(model=Model())
    assert options.session is None
    assert options.env is None
    assert options.resources is None
    assert options.thinking_level is None
    assert options.active_tool_names is None
    assert options.get_api_key_and_headers is None
    assert options.stream_options is None


def test_harness_options_accepts_each_placeholder() -> None:
    """Constructing with each placeholder set explicitly does not raise."""

    sentinel_session = object()

    def fake_get_api_key_and_headers(_model: Model) -> None:
        return None

    options = AgentHarnessOptions(
        model=Model(),
        session=sentinel_session,
        env={"X": "1"},
        resources=[object()],
        thinking_level="medium",
        active_tool_names=["x"],
        get_api_key_and_headers=fake_get_api_key_and_headers,
        stream_options={"timeout": 30},
    )

    # Roundtrip — values are stored as-is.
    assert options.session is sentinel_session
    assert options.env == {"X": "1"}
    assert options.resources is not None and len(options.resources) == 1
    assert options.thinking_level == "medium"
    assert options.active_tool_names == ["x"]
    assert options.get_api_key_and_headers is fake_get_api_key_and_headers
    assert options.stream_options == {"timeout": 30}
