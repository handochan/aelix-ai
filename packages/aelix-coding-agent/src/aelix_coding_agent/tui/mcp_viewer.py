"""DI flow for the ``/mcp`` read-only status viewer (Sprint 6h₂₇, ADR-0155, WP-7).

The interactive flow lives in :func:`aelix_coding_agent.tui.shell._open_mcp_status`;
this module owns the formatting + per-server tool-count collection so it stays
unit-testable without standing up the prompt-toolkit app — like
:mod:`aelix_coding_agent.tui.model_picker`.

Aelix-additive (Tier 4a — Pi has no MCP client). Read-only POINT-IN-TIME
snapshot: it reports each declared server's transport, connected state, and tool
count. The tool count is the only async I/O (one :meth:`McpServerConnection.list_tools`
round-trip per CONNECTED server); ``connect_all`` already ran at startup so a
connected server answers fast, but a slow/hung server is bounded with
``asyncio.wait_for`` and the row still renders with a ``?`` count on
timeout/error. There is deliberately no live re-poll / reconnect button (that is
fine for v1 per the mockup). The manager is threaded from ``entry.py`` into
``run_tui`` (the harness does NOT expose it — same seam as ``model_registry``).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# Bound a single hung server's tool listing so the snapshot never blocks the REPL.
_LIST_TOOLS_TIMEOUT_SECONDS = 3.0


def _endpoint(conn: Any) -> str:
    """The server endpoint string (url or command), ``""`` when unknown.

    Reads the connection's private ``_contrib`` (url / command) — a minor
    private read with no public accessor; purely cosmetic, fully getattr-guarded
    so an odd connection shape never raises.
    """

    contrib = getattr(conn, "_contrib", None)
    if contrib is None:
        return ""
    return getattr(contrib, "url", None) or getattr(contrib, "command", None) or ""


async def run_mcp_viewer(
    *,
    manager: Any,
    commit: Callable[[object], None],
) -> None:
    """Render the ``/mcp`` server-status panel (Sprint 6h₂₇, ADR-0155, WP-7).

    Module-level + dependency-injected (duck-typed ``manager`` + ``commit``
    callable) so the formatting + async tool-count collection are unit-testable
    without the prompt-toolkit app. ``shell.py`` wires the live MCP manager +
    output-committer into it.

    Degrades to a committed "No MCP servers configured." message when the
    manager is ``None`` (no servers / headless) or has no connections. A
    per-server ``list_tools`` timeout/error shows a ``?`` tool count but still
    renders the row — never crashes the REPL.
    """

    from rich.box import ROUNDED
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    if manager is None:
        commit(Text("No MCP servers configured.", style="yellow"))
        return
    conns = list(getattr(manager, "connections", {}).values())
    if not conns:
        commit(Text("No MCP servers configured.", style="yellow"))
        return

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)  # server name
    table.add_column(style="white")  # transport + endpoint
    table.add_column(style="white")  # state
    table.add_column(style="white", justify="right")  # tool count
    for conn in conns:
        name = getattr(conn, "name", "?")
        transport = getattr(conn, "transport", "?")
        connected = bool(getattr(conn, "connected", False))
        state = (
            Text("connected", style="green")
            if connected
            else Text("disconnected", style="red")
        )
        endpoint = _endpoint(conn)
        count = "-"
        if connected:
            try:
                tools = await asyncio.wait_for(
                    conn.list_tools(), timeout=_LIST_TOOLS_TIMEOUT_SECONDS
                )
                count = str(len(tools))
            except Exception:  # noqa: BLE001 — timeout/transport: show '?', keep row
                count = "?"
        table.add_row(name, f"{transport} {endpoint}".strip(), state, count)
    title = f"Manage MCP servers · {len(conns)} servers"
    commit(Panel(table, title=title, box=ROUNDED, border_style="cyan"))


__all__ = ["run_mcp_viewer"]
