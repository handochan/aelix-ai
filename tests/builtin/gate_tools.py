"""aelix's own tool OBJECTS, for tests that drive the permission gate directly.

ADR-0253 (#188): the gate decides by the provenance of the tool the loop will
execute, which it resolves from the event's ``AgentContext`` — never by name. A
hand-built :class:`ToolCallHookEvent` with no context is therefore an UNKNOWN
tool to the gate (mutating), whatever it is called. A test that means "the
built-in ``bash``" has to say so by carrying the built-in object, and this is
where those objects come from: the real factories, which mark what they build.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.harness.hooks import ToolCallHookEvent
from aelix_agent_core.types import AgentContext
from aelix_coding_agent.tools import create_all_tools

BUILTIN_TOOLS = create_all_tools("/proj")
"""``{name: tool}`` for the seven built-ins, each marked by its own factory."""

BUILTIN_CONTEXT = AgentContext(tools=list(BUILTIN_TOOLS.values()))
"""A turn context holding exactly the seven built-ins."""


def builtin_event(
    tool_name: str, args: dict[str, Any], *, tool_call_id: str = "t1"
) -> ToolCallHookEvent:
    """A ``tool_call`` event for the BUILT-IN tool of that name."""

    assert tool_name in BUILTIN_TOOLS, f"{tool_name!r} is not a built-in tool"
    return ToolCallHookEvent(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args=args,
        context=BUILTIN_CONTEXT,
    )


__all__ = ["BUILTIN_CONTEXT", "BUILTIN_TOOLS", "builtin_event"]
