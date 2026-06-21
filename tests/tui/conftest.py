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
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AELIX_CODING_AGENT_DIR", str(tmp_path / "agent"))
