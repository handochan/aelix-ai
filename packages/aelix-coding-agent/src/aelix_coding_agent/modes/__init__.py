"""Pi parity: ``modes/index.ts`` — mode entry-point re-exports.

Sprint 6h₆ (Phase 5a-ii, ADR-0089). Re-exports the two mode entry
functions consumed by :func:`aelix_coding_agent.cli.entry._async_main`:

- :func:`run_print_mode` — text / JSON one-shot print mode.
- :func:`run_rpc_mode` — headless JSONL command/response protocol.

The interactive entry is deferred to Phase 5b pending TUI library
selection (ADR-0088).
"""

from __future__ import annotations

from aelix_coding_agent.modes.print_mode import run_print_mode
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

__all__ = ["run_print_mode", "run_rpc_mode"]
