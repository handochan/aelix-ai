"""#512 (ADR-0282) — ``/mcp`` and ``/extension`` list a disabled server.

Both read ``core.mcp_servers.runtime_statuses`` over a REAL ``McpClientManager``
here (no connection is opened: a manager whose only enabled server failed, or
that holds only disabled entries, needs no subprocess), so the words on screen
are the shared vocabulary's and the manager's own ``failed`` / ``disabled``.
"""

from __future__ import annotations

import io

from aelix_agent_core.contracts import McpServerContrib
from aelix_coding_agent.core.mcp_servers import DisabledMcpServer
from aelix_coding_agent.mcp import McpClientManager
from aelix_coding_agent.tui.extension_manager import build_installed_lines, run_extension_manager
from aelix_coding_agent.tui.mcp_viewer import run_mcp_viewer
from rich.console import Console


def _render(renderable: object, width: int = 120) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, no_color=True).print(renderable)
    return buffer.getvalue()


def _off(name: str = "quiet") -> DisabledMcpServer:
    return DisabledMcpServer(
        contrib=McpServerContrib(name=name, transport="stdio", command="quiet-srv"),
        config="~/.aelix/agent/mcp.json",
    )


def _manager(*, with_failed: bool = True) -> McpClientManager:
    contribs = (
        [McpServerContrib(name="broken", transport="stdio", command="broken-srv")]
        if with_failed
        else []
    )
    manager = McpClientManager(contribs, disabled=[_off()])
    if with_failed:
        manager._failed.add("broken")  # what connect_all records for a raise
    return manager


def test_the_manager_never_gives_a_disabled_name_a_connection() -> None:
    """Even when a contribution of the same name is handed to it — the manifest
    case, defended here as well as in ``entry.py``."""

    same = McpServerContrib(name="quiet", transport="stdio", command="manifest-srv")
    manager = McpClientManager([same], disabled=[_off("quiet")])
    assert manager.connections == {}
    assert [d.contrib.name for d in manager.disabled] == ["quiet"]


async def test_connect_all_records_failed_names() -> None:
    manager = McpClientManager(
        [McpServerContrib(name="nocmd", transport="stdio", command=None)]
    )
    errors = await manager.connect_all()
    assert len(errors) == 1
    assert manager.failed == frozenset({"nocmd"})


async def test_mcp_lists_the_disabled_server_and_how_to_turn_it_on() -> None:
    committed: list[object] = []
    await run_mcp_viewer(manager=_manager(), commit=committed.append)
    (panel,) = committed
    out = _render(panel)
    assert "2 servers · 1 disabled" in out
    rows = {ln.split()[1]: ln for ln in out.splitlines() if ln.startswith("│ ") and len(ln.split()) > 2}
    assert "failed" in rows["broken"]
    assert "disabled" in rows["quiet"] and "quiet-srv" in rows["quiet"]
    assert out.index("broken") < out.index("quiet"), "disabled servers come last"
    assert '"enabled": false in ~/.aelix/agent/mcp.json' in out
    assert "delete the key" in out


async def test_mcp_with_only_disabled_servers_does_not_say_none_configured() -> None:
    committed: list[object] = []
    await run_mcp_viewer(manager=_manager(with_failed=False), commit=committed.append)
    out = _render(committed[0])
    assert "No MCP servers configured" not in out
    assert "quiet" in out and "disabled" in out


async def test_mcp_panel_fits_80_columns() -> None:
    committed: list[object] = []
    await run_mcp_viewer(manager=_manager(), commit=committed.append)
    for line in _render(committed[0], width=80).splitlines():
        assert len(line) <= 80, line


def test_extension_installed_tab_says_disabled_and_how_to_turn_it_on() -> None:
    from aelix_coding_agent.core.mcp_servers import runtime_statuses

    lines = [str(line) for line in build_installed_lines([], runtime_statuses(_manager()))]
    assert "  broken — stdio — failed" in lines
    row = '  quiet — stdio — disabled ("enabled": false in ~/.aelix/agent/mcp.json)'
    assert row in lines
    assert len(row) <= 78, "the modal clips at the terminal edge"
    # The note rides the row: this modal bottom-truncates, so no extra rows.
    assert [ln for ln in lines if "enabled" in ln] == [row]
    assert "Read-only: extensions + MCP servers load at startup." in lines


async def test_the_live_extension_manager_reads_the_shared_statuses() -> None:
    captured: dict[str, object] = {}

    async def _tabbed(title: str, tabs, **_kwargs):
        captured["installed"] = [str(line) for line in dict(tabs)["Installed"]()]

    committed: list[object] = []
    await run_extension_manager(
        extensions=[], mcp_manager=_manager(), tabbed=_tabbed, commit=committed.append
    )
    assert committed == []
    installed = captured["installed"]
    assert (
        '  quiet — stdio — disabled ("enabled": false in ~/.aelix/agent/mcp.json)' in installed  # type: ignore[operator]
    )


# --- review round 1 (2026-11-01) --------------------------------------------------


def _ansi(renderable: object, width: int) -> str:
    """What the terminal receives: escape codes and all, colour on."""

    buffer = io.StringIO()
    Console(file=buffer, width=width, force_terminal=True, color_system="standard").print(
        renderable
    )
    return buffer.getvalue()


async def _panel(manager: object) -> object:
    committed: list[object] = []
    await run_mcp_viewer(manager=manager, commit=committed.append)
    (panel,) = committed
    return panel


async def test_mcp_prints_names_and_files_as_text_not_markup_or_escapes() -> None:
    """#512 review 512-03 / verify V2. A ``str`` cell is Rich MARKUP: ``[bold]mk``
    rendered as a bold ``mk``, ``close[/]`` raised MarkupError and blanked /mcp,
    and an ESC in a name or in the file label reached the terminal (``ESC [2J``
    erased the transcript). A LF in the label started a row of its own."""

    hostile = [
        "ctl\x1b[31mRED\x07\x08z",
        "[bold]mk[/bold]",
        "close[/]",
    ]
    manager = McpClientManager(
        [],
        disabled=[
            DisabledMcpServer(
                contrib=McpServerContrib(name=name, transport="stdio", command="srv\x1b[2J"),
                config="evil\x1b[2J\nline.json",
            )
            for name in hostile
        ],
    )
    panel = await _panel(manager)
    out = _ansi(panel, 120)
    assert "\x1b[2J" not in out and "\x1b[31m" not in out and "\x07" not in out
    plain = _render(panel)
    assert "[bold]mk[/bold]" in plain, "markup is shown, not interpreted"
    assert "close[/]" in plain
    assert "ctl[31mREDz" in plain, "the escape is flattened to inert text"
    (hint,) = [ln for ln in plain.splitlines() if "Turned off by" in ln]
    assert "evil[2Jline.json" in hint, "the label stays on its own row"


async def test_one_long_name_does_not_push_the_state_off_80_columns() -> None:
    """#512 verify V5: the name column was ``no_wrap`` and unbounded, so a
    74-character name left room for nothing else on any row."""

    long = "sequential-thinking-" * 4
    manager = McpClientManager(
        [],
        disabled=[
            DisabledMcpServer(
                contrib=McpServerContrib(name=n, transport="stdio", command="/usr/bin/srv"),
                config="~/.aelix/agent/mcp.json",
            )
            for n in (long, "short")
        ],
    )
    out = _render(await _panel(manager), width=80)
    rows = [ln for ln in out.splitlines() if "stdio" in ln]
    assert len(rows) == 2, out
    for row in rows:
        assert len(row) <= 80, row
        assert "disabled" in row, row
        assert row.rstrip("│ ").endswith("-"), f"the tool count is on the row too: {row}"
    assert "…" in rows[0], "the long name is cut, and says so"


async def test_mcp_does_not_print_a_urls_query_or_path() -> None:
    """#512 verify V7: the token rode the query string onto the screen; R7: a
    key in the path (``/mcp/<key>/sse``) still did."""

    manager = McpClientManager(
        [],
        disabled=[
            DisabledMcpServer(
                contrib=McpServerContrib(
                    name="remote", transport="http", url="http://127.0.0.1:49360/mcp?token=TOKSECRET"
                ),
                config="~/.aelix/agent/mcp.json",
            ),
            DisabledMcpServer(
                contrib=McpServerContrib(
                    name="zap", transport="sse", url="https://hooks.example.test/mcp/PATHKEY512/sse"
                ),
                config="~/.aelix/agent/mcp.json",
            ),
        ],
    )
    out = _render(await _panel(manager))
    assert "TOKSECRET" not in out and "token=" not in out
    assert "PATHKEY512" not in out
    assert "http://127.0.0.1:49360/…" in out
    assert "sse https://hooks.example.test/…" in out
