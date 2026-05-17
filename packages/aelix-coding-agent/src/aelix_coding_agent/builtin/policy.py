"""Built-in PolicyExtension — allowlist/denylist gate on the ``tool_call`` hook.

Per ADR-0004, policy is a built-in *extension* (not a core gate). It subscribes
to the ``tool_call`` hook and returns :class:`ToolCallResult(block=True, ...)`
when a tool is denied. Deny wins over allow; ``allow_tools=None`` means "all
tools are allowed" (subject to the deny list).

Phase 1.2 ships a silent block (no interactive confirm) because the harness
has no UI surface yet — see ADR-0015 for the future ``ExtensionContext.ui``
discussion. Block reasons surface in the synthesized tool-result message via
the existing ``agent/loop.py:333`` path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aelix_agent_core.harness.hooks import ToolCallHookEvent, ToolCallResult

from aelix_coding_agent.extensions.api import ExtensionAPI, ExtensionContext


@dataclass
class PolicyExtension:
    """Allowlist/denylist policy registered as a built-in extension.

    Instances are valid :class:`~aelix_coding_agent.extensions.api.ExtensionFactory`
    callables — ``__call__(self, aelix)`` registers the ``tool_call``
    handler. Pass instances directly to ``load_extensions([PolicyExtension()])``
    per D.1.8.
    """

    allow_tools: frozenset[str] | None = None
    deny_tools: frozenset[str] = field(default_factory=frozenset)

    def __call__(self, aelix: ExtensionAPI) -> None:
        """Setup: register ``_on_tool_call`` as the ``tool_call`` handler."""

        aelix.on("tool_call", self._on_tool_call)

    def _on_tool_call(
        self,
        event: ToolCallHookEvent,
        _ctx: ExtensionContext,
    ) -> ToolCallResult | None:
        # Deny wins over allow.
        if event.tool_name in self.deny_tools:
            return ToolCallResult(
                block=True,
                reason=(
                    f"[blocked] tool {event.tool_name!r} is denied by policy."
                ),
            )
        if self.allow_tools is not None and event.tool_name not in self.allow_tools:
            return ToolCallResult(
                block=True,
                reason=(
                    f"[blocked] tool {event.tool_name!r} is not in the allow list."
                ),
            )
        return None


__all__ = ["PolicyExtension"]
