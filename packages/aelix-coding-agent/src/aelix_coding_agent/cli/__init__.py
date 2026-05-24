"""Aelix coding-agent minimal CLI (Sprint 5b §B.2, ADR-0042).

Full TUI lives in Phase 5c-tui (Sprint 6h₁₀b, see ADR-0100); this REPL is the smallest
surface that exercises ``user_bash`` emit + ``/reload`` command interception.
"""

from aelix_coding_agent.cli.repl import (
    handle_user_bash,
    run_repl,
)

__all__ = ["handle_user_bash", "run_repl"]
