"""Sprint 6h₄b types — :class:`HarnessFactory`, :class:`RuntimeReplaceResult`,
:class:`AgentSessionRuntimeDiagnostic` (ADR-0077).

Pi parity: ``packages/agent/src/core/agent-session-runtime.ts:67-374``.
The Pi return shape for ``switchSession`` / ``newSession`` / ``fork`` /
``importFromJsonl`` is ``{cancelled: boolean, selectedText?: string}``;
the Aelix dataclass mirrors verbatim with snake_case Python fields
serializing to camelCase keys at the wire layer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.session.session import Session


HarnessFactory = Callable[["Session"], Awaitable["AgentHarness"]]
"""Aelix-additive: factory called by :class:`AgentSessionRuntime` to build
a NEW :class:`AgentHarness` bound to ``new_session`` (P-302/P-306).
Async so callers can ``await harness.bootstrap()`` inside the factory.

Pi parity rationale (P-302 — BINDING):
Pi reassigns ``this._session`` in-place at
``agent-session-runtime.ts:166-173``. Aelix CANNOT mirror that directly
because :class:`AgentHarness` captures ``_state.session_id`` at
``__init__`` (``harness/core.py:524``) and binds runtime actions / merges
tools / caches session_name during construction. The factory pattern
preserves all of these invariants by reconstructing the harness for each
new :class:`Session`.
"""


@dataclass(frozen=True)
class RuntimeReplaceResult:
    """Pi parity: shape of the value returned by ``switchSession`` /
    ``newSession`` / ``fork`` / ``importFromJsonl`` (Pi
    ``agent-session-runtime.ts:175-320`` return signatures).

    Wire-shape preserves Pi camelCase keys when serialized:
    ``{"cancelled": bool, "selectedText"?: str}``.
    """

    cancelled: bool
    selected_text: str | None = None


@dataclass(frozen=True)
class AgentSessionRuntimeDiagnostic:
    """Pi parity: ``AgentSessionRuntimeDiagnostic`` (Pi
    ``agent-session-runtime.ts`` diagnostics array element type).

    Minimal frozen wrapper carrying a code + human-readable message.
    Extended in Sprint 6h₄c+ as real diagnostics emerge from the four
    replace APIs.
    """

    code: str
    message: str


__all__ = [
    "AgentSessionRuntimeDiagnostic",
    "HarnessFactory",
    "RuntimeReplaceResult",
]
