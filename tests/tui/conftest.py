"""TUI test isolation (Sprint 6h₃₁, ADR-0164).

``run_tui`` and the statusline / settings stores resolve their config dir via
:func:`aelix_coding_agent.cli.config.get_agent_dir`, which honours the
``AELIX_CODING_AGENT_DIR`` env var. Several ``run_tui`` smokes build the REAL
stores, so without isolation a ``/statusline``-saving test writes to the user's
``~/.aelix/agent/statusline.json`` and a LATER footer smoke READS it — a
non-hermetic suite. A persisted config that omits the ``steering`` segment then
hides the footer ⏵⏵ marker and breaks
``test_run_tui_mode_command_sets_and_reflects_footer`` (the failure depends on
real on-disk state / test order, not the code under test).

This autouse fixture points the agent dir at a fresh per-test tmp dir, so every
TUI test gets an isolated, DEFAULT config — no pollution of the user's real
config, and no dependence on it. Scoped to ``tests/tui`` (not global) so it does
not perturb the CLI config tests that assert ``get_agent_dir``'s real default.

A test that names ``tmp_path`` (as an argument, through a fixture or
``usefixtures``) finds the dir at ``tmp_path / "agent"``, and several build
paths under it. Any other test gets ``_per_test_dir / "agent"``
(``tests/conftest.py``), including one that only calls
``request.getfixturevalue("tmp_path")`` while it runs: asking for ``tmp_path``
for each of them made each one list the whole basetemp (#459).
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
