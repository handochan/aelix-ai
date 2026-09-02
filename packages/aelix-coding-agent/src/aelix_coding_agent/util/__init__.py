"""Aelix Coding Agent utility helpers.

Sprint 6h₇a (Phase 5a-iii-α, ADR-0090) — namespace package marker for
:mod:`aelix_coding_agent.util` ports.

Modules:
  - :mod:`.fuzzy` — Pi parity port of ``tui/src/fuzzy.ts`` (stdlib-only
    fuzzy match / filter; consumed by :mod:`aelix_coding_agent.cli.list_models`).
  - :mod:`.stdio` — std-stream encoding hardening for non-UTF-8 consoles and
    pipes (N-3, issue #110 P7); called from the console-script entry points.
"""
