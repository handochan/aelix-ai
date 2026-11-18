"""``/mcp``: the on/off panel over the ``mcp.json`` a launch here reads (#624, ADR-0305 D16).

The shared selection panel (ADR-0279 D11, layout A) over
:class:`~aelix_coding_agent.core.mcp_selection.McpSelection`: a checkbox per
server, the status column with the state words and ``off · restart``, the "Save
to" control fixed on the one writable file, and an Enter that saves and applies
nothing. ``/mcp list`` keeps the read-only table (``tui/mcp_viewer.py``).

A module of its own, not code beside ``agents_panel_opener`` in ``tui/shell.py``:
an insertion there would move every citation of that file below it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aelix_coding_agent.core.mcp_selection import McpControl

EFFECT_NOTE = "the next aelix start applies it; /reload does not reconnect MCP servers"
"""What a save says about when it takes effect (ADR-0305 D15): MCP connects at launch."""


def mcp_panel_opener(
    ctx: Callable[[], Any],
    control: McpControl | None,
    width_cap: Callable[[], int | None],
) -> Callable[[], Awaitable[None]] | None:
    """``CommandContext.mcp_panel``: ``None`` when the launch built no MCP control."""

    if control is None:
        return None

    async def _open() -> None:
        from aelix_coding_agent.tui.selection_panel import run_selection_panel  # noqa: PLC0415

        await run_selection_panel(
            ctx(),
            control.selection,  # type: ignore[arg-type]
            control.kind,  # type: ignore[arg-type]
            title="MCP servers",
            noun=("server", "servers"),
            effect_note=EFFECT_NOTE,
            render_width_cap=width_cap,
            # A server an extension declares is a server: the count says so, as
            # ``/mcp list`` does (live check, verify r1).
            count_info=True,
        )

    return _open


__all__ = ["EFFECT_NOTE", "mcp_panel_opener"]
