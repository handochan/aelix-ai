"""Pi parity: enables ``python -m aelix_coding_agent``.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-391). Delegates to
:func:`aelix_coding_agent.cli.entry.main_sync`.
"""

from __future__ import annotations

from aelix_coding_agent.cli.entry import main_sync

if __name__ == "__main__":
    main_sync()
