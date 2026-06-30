"""Issue #24 / #53 — the go-live toggle for the full factory-rebuild ``/reload``.

``_reload_rebuild_enabled()`` is DEFAULT-ON after the multi-lens adversarial
review: ``/reload`` routes through ``AgentSessionRuntime.reload()`` (the full
hot-reload that re-discovers on-disk extensions, no restart — the #53 moat)
UNLESS ``AELIX_RELOAD_REBUILD`` is set to a falsy kill-switch value, which falls
back to the cheap ``harness.reload_resources()`` refresh.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.tui.shell import _reload_rebuild_enabled


def test_reload_rebuild_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_RELOAD_REBUILD", raising=False)
    assert _reload_rebuild_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", " OFF "])
def test_reload_rebuild_kill_switch(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AELIX_RELOAD_REBUILD", value)
    assert _reload_rebuild_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "", "  ", "anything"])
def test_reload_rebuild_enabled_otherwise(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    # Default-ON: unset, empty, truthy, or any non-kill-switch value -> enabled.
    monkeypatch.setenv("AELIX_RELOAD_REBUILD", value)
    assert _reload_rebuild_enabled() is True
