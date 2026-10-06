"""Which tool objects aelix built itself — the permission gate's trusted input (#188).

ADR-0253. :class:`~aelix_coding_agent.builtin.permission.PermissionExtension`
used to decide "is this call mutating?" by looking a BARE NAME up in a fixed
set (``_MUTATING = _BASH_TOOLS | _WRITE_TOOLS``) and treating everything else
as read-only. Every tool that does not come from aelix's own factories arrives
under some other name — MCP prefixes its server (``fs__write_file``), and an
extension or pack picks whatever it likes — so every one of them was silently
allowed in every posture and was not stopped by PLAN either. Measured on
``aab1f210``: ``fs__write_file`` at ``plan`` returned ``None`` (allowed) with no
prompt, on the interactive and the headless path alike.

THE GATE NOW TRUSTS ONLY WHAT IT CAN PROVE IT BUILT. A tool is read-only (or a
built-in bash/write tool) only when it is *the object* one of aelix's own
factories returned, never because of what it is called. That is why this is a
registry of OBJECT IDENTITIES and not of names: an extension tool named
``read`` or ``grep`` is not ``read`` or ``grep`` — it is whatever its author
wrote — and a name check would wave it through. An MCP server's
``readOnlyHint`` annotation is likewise never consulted: it is a claim made by
the thing being gated.

WHY IDENTITY SURVIVES THE TRIP TO THE GATE. The ``tool_call`` hook carries the
turn's :class:`~aelix_agent_core.types.AgentContext`, and the loop resolves the
tool it will execute from that same list (``loop.py``:
``tool_map = {t.name: t for t in context.tools}``). The gate repeats that
lookup, so the object it classifies is the object that runs.

A tool rebuilt with :func:`dataclasses.replace` is a NEW object and is not
marked — the ``agent`` tool re-stamps its description every turn and so marks
its replacement explicitly (:func:`aelix_agents.tool.with_description`).
Anything that forgets to do that fails CLOSED: the copy is treated as an
unknown, mutating tool and asks.

Code already running inside the process can mark its own tools by importing
this module. That is out of scope by construction (#127/#128): in-process code
can replace the gate itself.
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

A tool with no provenance is mutating: it asks in ``default``, is blocked in
``plan``, gets the headless verdict a built-in mutating tool gets, and is never
auto-allowed by ``auto-accept-edits`` / ``auto``.
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
