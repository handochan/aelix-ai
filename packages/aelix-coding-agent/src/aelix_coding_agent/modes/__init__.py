"""Pi parity: ``modes/index.ts`` — mode entry-point re-exports.

Sprint 6h₆ (Phase 5a-ii, ADR-0089). Re-exports the mode entry functions
consumed by :func:`aelix_coding_agent.cli.entry._async_main`:

- :func:`run_print_mode` — text / JSON one-shot print mode.
- :func:`run_rpc_mode` — headless JSONL command/response protocol.
- :func:`run_tui` — interactive TUI shell (Sprint 6h₁₀a, ADR-0104). Imported
  lazily by the entry point so the optional ``[tui]`` extra (prompt-toolkit +
  rich) is only required when interactive mode is actually launched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aelix_coding_agent.modes.print_mode import run_print_mode
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

if TYPE_CHECKING:
    from aelix_coding_agent.tui import run_tui

__all__ = ["run_print_mode", "run_rpc_mode", "run_tui"]


def __getattr__(name: str) -> Any:
    """Lazily resolve :func:`run_tui` (PEP 562).

    Keeps the optional ``[tui]`` extra (prompt-toolkit + rich) off the import
    path of the print/rpc/server consumers — ``from ...modes import
    run_print_mode`` must not require prompt-toolkit. ``run_tui`` is only
    imported when explicitly accessed (the interactive entry branch), so a
    headless install without ``[tui]`` raises a clean ``ImportError`` exactly
    at that point.
    """

    if name == "run_tui":
        from aelix_coding_agent.tui import run_tui

        return run_tui
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
