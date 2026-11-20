"""Pi parity: enables ``python -m aelix_coding_agent``.

Sprint 6h₆ (Phase 5a-i, ADR-0089, P-391). Delegates to
:func:`aelix_coding_agent.cli.entry.main_sync`.
"""

from __future__ import annotations

from aelix_coding_agent.cli.entry import main_sync
from aelix_coding_agent.util.child_pythonpath import restore_child_pythonpath

if __name__ == "__main__":
    # Both delegation channels come here after startup imports and before CLI setup.
    restore_child_pythonpath()
    main_sync()
