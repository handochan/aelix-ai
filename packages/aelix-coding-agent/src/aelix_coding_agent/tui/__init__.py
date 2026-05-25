"""Sprint 6h₁₀a (ADR-0104) — interactive TUI shell (Phase 5c-tui).

prompt-toolkit (input/editor) + Rich (output rendering) + a thin Aelix layer,
per ADR-0088. Public entry: :func:`run_tui`.
"""

from __future__ import annotations

from aelix_coding_agent.tui.shell import run_tui

__all__ = ["run_tui"]
