"""Builtin-extension test isolation (#646 fix round 1).

The write gate lists the agent directory on every write decision it may
auto-allow: the links a load follows out of ``extensions`` / ``skills`` /
``agents`` and the ``skills:`` / ``extensions:`` paths of the agent profiles in
it (``builtin/permission.py`` ``_loader_resource_roots``). Through
:func:`aelix_coding_agent.cli.config.get_agent_dir` that is the developer's
own ``~/.aelix/agent`` unless something pins it, and the #497 guard
(``tests/session_root_guard.py``) fails a test that lists it.

This autouse fixture points the agent dir at a fresh per-test tmp dir, as
``tests/tui/conftest.py`` does for its directory. A test that sets the variable
or sandboxes the home itself still wins: its own ``monkeypatch`` runs later.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
