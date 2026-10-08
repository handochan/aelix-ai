"""Identity evidence for the maintenance host's PLAN tool gate (#418, #188).

PLAN allows only read-only tools built by Aelix and its bundled delegation tool.
A borrowed name or an MCP readOnlyHint is not evidence. The permission callback
resolves the same last matching tool object as the loop and checks this registry.

Weak references keep the registry bounded and protect against recycled ids.
Dataclass copies carry no mark; the delegation description updater explicitly
carries an existing mark without inventing one for a third-party tool.

This backport changes PLAN only. Other beta.2 postures keep their existing policy;
the full DEFAULT/approval policy is the separate implementation of #188 on main.
In-process extension code is trusted and can modify the gate itself (#127/#128).
"""

from __future__ import annotations

import weakref
from typing import Any, Literal, TypeVar

ToolProvenance = Literal["read_only", "bash", "write", "delegation"]
"""What the gate may assume about a tool aelix built.

- ``read_only`` — ``read`` / ``grep`` / ``find`` / ``ls`` and the bundled
  ``aelix_status``: silently allowed in every posture, PLAN included.
- ``bash`` — the built-in ``bash`` tool: bash-family rule keys, the AUTO
  posture's tree-sitter classifier.
- ``write`` — the built-in ``write`` / ``edit`` tools: write-family rule keys,
  the AUTO_ACCEPT / AUTO in-project auto-allow.
- ``delegation`` — the bundled ``agent`` tool. The permission gate keeps
  ADR-0197's treatment: it neither prompts nor blocks it; spawn consent lives
  in ``aelix_agents/consent.py`` and the child's posture is clamped.

This beta.2 maintenance backport uses provenance in PLAN only. A tool with no
provenance is blocked there; other beta.2 postures retain their existing policy.
The complete default-posture gate is the separate main implementation of #188.
"""

_T = TypeVar("_T")

_MARKS: dict[int, tuple[weakref.ReferenceType[Any], ToolProvenance]] = {}


def mark_builtin(tool: _T, provenance: ToolProvenance) -> _T:
    """Record that aelix built ``tool`` and what it is. Returns ``tool``.

    Keyed by ``id()`` with a weak reference beside it, because a tool is a
    frozen dataclass whose generated ``__hash__`` hashes its fields — and a
    ``parameters`` dict makes that raise. The weak reference both lets the tool
    be collected and guards against ``id()`` reuse: a lookup only answers when
    the referent is still the very object asked about.
    """

    key = id(tool)

    def _forget(ref: weakref.ReferenceType[Any], key: int = key) -> None:
        entry = _MARKS.get(key)
        if entry is not None and entry[0] is ref:
            _MARKS.pop(key, None)

    _MARKS[key] = (weakref.ref(tool, _forget), provenance)
    return tool


def builtin_provenance(tool: object) -> ToolProvenance | None:
    """``tool``'s provenance if aelix built this exact object, else ``None``."""

    entry = _MARKS.get(id(tool))
    if entry is None:
        return None
    ref, provenance = entry
    return provenance if ref() is tool else None


__all__ = ["ToolProvenance", "builtin_provenance", "mark_builtin"]
