"""Migrations NO-OP STUB (Pi parity surface; Aelix has no legacy data).

Sprint 6h₈ (Phase 5a-iv, ADR-0092, §C). Pi
``packages/coding-agent/src/migrations.ts`` (315 LOC) orchestrates 7
cleanup migrations all targeting **legacy data Aelix never had**:

1. ``oauth.json`` → ``auth.json`` (Pi pre-Sprint 6c shape).
2. ``settings.apiKeys`` → ``auth.json`` lift.
3. v0.30.0 session bug fix.
4. ``commands/`` → ``prompts/`` directory rename.
5. ``keybindings.json`` rename.
6. ``tools/`` → ``bin/`` relocation.
7. Extension deprecation cleanup.

Aelix :class:`aelix_ai.oauth.AuthStorage` shipped fresh in Sprint 6c
(ADR-0053). Aelix :class:`aelix_agent_core.session.JsonlSessionStorage`
shipped at header version 3 from day one (Sprint 4a / ADR-0022).
Keybindings + extension framework remain deferred (Phase 5b / Sprint
6i+ / ADR-0058). The seven Pi migrations therefore have nothing to
operate on; a no-op stub mirroring Pi's return shape is adequate.

The stub is shipped so the symbol exists for downstream wiring (Phase
5b TUI startup hook) without a future code-archaeology pass — Pi
``runMigrations(cwd)`` return shape is preserved verbatim so a future
caller can match without re-typing the dict.

Pi citation: ``packages/coding-agent/src/migrations.ts:1-315`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


async def run_migrations(cwd: str | Path) -> dict[str, list[Any]]:
    """Pi parity surface: ``runMigrations(cwd)``.

    Returns the Pi-shaped result dict
    ``{"migrated_auth_providers": [], "deprecation_warnings": []}`` so
    future Phase 5b TUI startup hooks can call into this module without
    re-typing the contract. Both lists are always empty in Aelix; see
    module docstring for the rationale.

    Parameters
    ----------
    cwd:
        Workspace cwd path. Accepted for Pi parity; unused in the
        no-op stub.
    """

    # Pi parity surface preserved; Aelix has no legacy data so both
    # lists are always empty (see module docstring).
    _ = cwd
    return {"migrated_auth_providers": [], "deprecation_warnings": []}


async def show_deprecation_warnings(warnings: list[str]) -> None:
    """Pi parity surface: ``showDeprecationWarnings(warnings)``.

    No-op since :func:`run_migrations` always returns an empty
    deprecation list. Kept so future callers that mirror Pi
    ``main.ts`` startup sequence do not break when Aelix decides to
    surface non-empty deprecations.
    """

    _ = warnings


__all__ = ["run_migrations", "show_deprecation_warnings"]
