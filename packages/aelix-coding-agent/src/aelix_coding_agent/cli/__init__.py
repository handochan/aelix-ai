"""Aelix coding-agent minimal CLI (Sprint 5b §B.2, ADR-0042).

Full TUI lives in Phase 5 (ADR-0033 successor); this REPL is the smallest
surface that exercises ``user_bash`` emit + ``/reload`` command interception.
"""

from aelix_coding_agent.cli.repl import (
    handle_user_bash,
    run_repl,
)

__all__ = ["handle_user_bash", "run_repl"]
