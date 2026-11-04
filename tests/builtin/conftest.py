"""Builtin-extension test isolation (#646 fix round 1).

The write gate lists the agent directory on every write decision it may
auto-allow: the links a load follows out of ``extensions`` / ``skills`` /
``agents`` and the ``skills:`` / ``extensions:`` paths of the agent profiles in
it (``builtin/permission.py`` ``_loader_resource_roots``). Through
:func:`aelix_coding_agent.cli.config.get_agent_dir` that is the developer's
own ``~/.aelix/agent`` unless something pins it, and the #497 guard
(``tests/session_root_guard.py``) fails a test that lists it.

This autouse fixture points the agent dir at a fresh per-test tmp dir, as
``tests/tui/conftest.py`` does for its directory, and in the same place: under
``tmp_path`` for a test that names it (as an argument, through a fixture or
``usefixtures``), else under ``_per_test_dir`` (#459).
A test that sets the variable or sandboxes the home itself still wins: its own
``monkeypatch`` runs later.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_dir(request, _per_test_dir, monkeypatch):
    if "tmp_path" in request.fixturenames:
        base = request.getfixturevalue("tmp_path")
    else:
        base = _per_test_dir
        base.mkdir(parents=True, exist_ok=True)  # the parent exists, as tmp_path did
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(base / "agent"))
