"""Issue #24 — the dormant gate for the full factory-rebuild ``/reload``.

``_reload_rebuild_enabled()`` is default-OFF: production ``/reload`` keeps the
cheap ``reload_resources()`` refresh until ``AELIX_RELOAD_REBUILD`` is truthy, at
which point the TUI routes ``/reload`` through ``AgentSessionRuntime.reload()``
(the full hot-reload). This pins the toggle semantics so the prod flip is explicit.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.tui.shell import _reload_rebuild_enabled


def test_reload_rebuild_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AELIX_RELOAD_REBUILD", raising=False)
    assert _reload_rebuild_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on", " ON "])
def test_reload_rebuild_enabled_truthy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AELIX_RELOAD_REBUILD", value)
    assert _reload_rebuild_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_reload_rebuild_disabled_falsy(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AELIX_RELOAD_REBUILD", value)
    assert _reload_rebuild_enabled() is False
