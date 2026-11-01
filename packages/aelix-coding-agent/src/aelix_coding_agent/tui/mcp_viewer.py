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

#512 (ADR-0282): the rows and their state words come from
``core.mcp_servers.runtime_statuses`` — the vocabulary ``/extension`` and
``aelix status`` print too — so a server ``mcp.json`` keeps off is listed as
``disabled``, after the others, with the file and key that turn it back on.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# Bound a single hung server's tool listing so the snapshot never blocks the REPL.
_LIST_TOOLS_TIMEOUT_SECONDS = 3.0


#: The style each state word renders in; the words themselves are
#: ``core.mcp_servers``'. A state missing here renders unstyled rather than raising.
_STATE_STYLES = {
    "connected": "green",
    "failed": "red",
    "disconnected": "red",
    "disabled": "yellow",
}


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
    manager is ``None`` (no servers / headless) or lists no server — a manager
    holding only disabled entries lists them. A
    per-server ``list_tools`` timeout/error shows a ``?`` tool count but still
    renders the row — never crashes the REPL.
    """

    from aelix_ai.utils.terminal_text import safe_for_terminal
    from rich.box import ROUNDED
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from aelix_coding_agent.core.mcp_servers import (
        display_endpoint,
        enable_hint,
        runtime_statuses,
    )

    statuses = runtime_statuses(manager)
    if not statuses:
        commit(Text("No MCP servers configured.", style="yellow"))
        return
    conns = getattr(manager, "connections", None) or {}

    def cell(value: str) -> Text:
        # A ``Text``, never a ``str``: Rich reads a ``str`` cell as MARKUP, so a
        # server named ``[bold]x`` rendered bold and one named ``close[/]``
        # raised MarkupError and blanked /mcp; and the name, endpoint and file
        # label are the config author's, so their escape codes are stripped
        # (#512 review 512-03, verify V2). The real values stay on the rows.
        return Text(safe_for_terminal(value, max_chars=300))

    table = Table.grid(padding=(0, 2))
    # Bounded with an ellipsis: one long name used to push every other column
    # off an 80-column screen, ``disabled`` included (#512 verify V5). The
    # endpoint is the column that gives way: it wraps after the transport word,
    # and a path or url longer than the column is cut with ``…``. A state word
    # and a count are never folded.
    table.add_column(style="bold cyan", no_wrap=True, max_width=28, overflow="ellipsis")
    table.add_column(style="white")  # transport + endpoint
    table.add_column(style="white", no_wrap=True)  # state
    table.add_column(style="white", justify="right", no_wrap=True)  # tool count
    for status in statuses:
        state = Text(status.state, style=_STATE_STYLES.get(status.state, ""))
        count = "-"
        conn = conns.get(status.name) if status.state == "connected" else None
        if conn is not None:
            try:
                tools = await asyncio.wait_for(
                    conn.list_tools(), timeout=_LIST_TOOLS_TIMEOUT_SECONDS
                )
                count = str(len(tools))
            except Exception:  # noqa: BLE001 — timeout/transport: show '?', keep row
                count = "?"
        # A url as scheme, host and port only: its query or path is where a
        # token rides (#512 verify V7, R7).
        endpoint = f"{status.transport} {display_endpoint(status.endpoint)}".strip()
        table.add_row(cell(status.name), cell(endpoint), state, count)
    disabled = sum(1 for status in statuses if status.state == "disabled")
    title = f"Manage MCP servers · {len(statuses)} servers"
    if disabled:
        title += f" · {disabled} disabled"
    hint = enable_hint(statuses)
    body: Any = table
    if hint:
        from rich.console import Group

        body = Group(
            table,
            Text(""),
            *(Text(safe_for_terminal(line, max_chars=300), style="dim") for line in hint),
        )
    commit(Panel(body, title=title, box=ROUNDED, border_style="cyan"))


__all__ = ["run_mcp_viewer"]
