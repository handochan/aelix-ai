"""#679 — the TUI repaints the footer when a ``/reload`` installs its rebuilt harness.

``run_tui``'s rebind returned early on ``"reload"`` before anything repainted, so
the footer kept the torn-down harness's ``✱`` model and ``🧠`` level until the next
turn ended (measured in a pty, ``.omc/specs/663-followups-evidence/grids/p2_tui_*``).
Since #679 a reload keeps both, so the footer is usually right as it stands; the
repaint keeps it right whenever the rebuilt harness differs (a placeholder resolved
again, a level clamped to the model the build runs).
"""

from __future__ import annotations

from pathlib import Path

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

from tests.tui.test_footer_repaints_on_model_change_534 import (  # noqa: F401 — a fixture
    _completions_adapter,
    _registry_model,
    _tui,
)


async def test_a_reload_rebind_repaints_the_footer(tmp_path: Path) -> None:
    async with _tui(tmp_path) as bench:
        assert "✱ m1" in bench.footer() and "🧠 off" in bench.footer(), bench.footer()
        rebuilt = AgentHarness(
            AgentHarnessOptions(
                model=await _registry_model(tmp_path, "gwa", "m2"),
                system_prompt="B2",
                thinking_level="high",
            )
        )
        bench.runtime._harness = rebuilt  # type: ignore[assignment]
        assert bench.runtime.rebind_cb is not None

        await bench.runtime.rebind_cb(rebuilt, "reload")

        assert "✱ m2" in bench.footer(), bench.footer()
        assert "🧠 high" in bench.footer(), bench.footer()
