"""Pi parity: anonymous inline return shape from
``agent-session.ts:2870`` — ``Array<{entryId, text}>``.

Sprint 6h₄a (ADR-0075, P-295) names the Pi-anonymous shape as a frozen
dataclass for type clarity. The wire serializer in :mod:`rpc.rpc_mode`
emits Pi-camelCase keys (``entryId`` / ``text``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForkPointInfo:
    """One user-message fork point — Pi parity for inline `{entryId, text}`."""

    entry_id: str
    text: str


__all__ = ["ForkPointInfo"]
