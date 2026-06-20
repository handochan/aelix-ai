"""ModelRegistry test guards.

P0 #4 (ADR-0140): :meth:`ModelRegistry.create` now defaults its
``models_json_path`` to ``<agent-dir>/models.json`` (Pi parity). To keep
the suite hermetic — so a developer's real ``~/.aelix/agent/models.json``
can never leak into ``create(...)`` tests — this autouse fixture points
the agent dir at a clean per-test temp dir.

Tests that need a models.json pass an explicit path (and so are isolated
regardless); this only covers the default-path factory call.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path_factory.mktemp("aelix_agent")
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
