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

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_dir(_per_test_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Not ``tmp_path_factory.mktemp``: that lists the whole basetemp per test (#459).
    agent_dir = _per_test_dir / "agent"
    agent_dir.mkdir(parents=True)
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(agent_dir))
