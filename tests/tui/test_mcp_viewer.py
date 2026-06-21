"""Unit tests for the /mcp viewer flow (Sprint 6h₂₇, ADR-0155, WP-7)."""

from __future__ import annotations

import asyncio
import io

from aelix_coding_agent.tui.mcp_viewer import run_mcp_viewer
from rich.console import Console


def _render(renderable: object) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=120, no_color=True).print(renderable)
    return buffer.getvalue()


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


class _Contrib:
    def __init__(self, url: str | None = None, command: str | None = None) -> None:
        self.url = url
        self.command = command


class _Conn:
    def __init__(
        self,
        name: str,
        transport: str,
        *,
        connected: bool,
        tools: int = 0,
        raise_list: bool = False,
        hang: bool = False,
        contrib: _Contrib | None = None,
    ) -> None:
        self.name = name
        self.transport = transport
        self.connected = connected
        self._tools = tools
        self._raise = raise_list
        self._hang = hang
        self._contrib = contrib

    async def list_tools(self) -> list[object]:
        if self._raise:
            raise RuntimeError("transport boom")
        if self._hang:
            await asyncio.sleep(60)  # exceeds the wait_for timeout
        return [object() for _ in range(self._tools)]


class _Manager:
    def __init__(self, conns: list[_Conn]) -> None:
        self.connections = {c.name: c for c in conns}


async def test_run_none_manager_degrades() -> None:
    committed: list[object] = []
    await run_mcp_viewer(manager=None, commit=committed.append)
    assert any("No MCP servers configured" in _plain(c) for c in committed)


async def test_run_empty_connections_degrades() -> None:
    committed: list[object] = []
    await run_mcp_viewer(manager=_Manager([]), commit=committed.append)
    assert any("No MCP servers configured" in _plain(c) for c in committed)


async def test_run_renders_servers_states_and_counts() -> None:
    manager = _Manager(
        [
            _Conn(
                "alpha",
                "http",
                connected=True,
                tools=2,
                contrib=_Contrib(url="https://x/api"),
            ),
            _Conn("beta", "stdio", connected=False),
        ]
    )
    committed: list[object] = []
    await run_mcp_viewer(manager=manager, commit=committed.append)
    assert len(committed) == 1
    out = _render(committed[0])
    assert "2 servers" in out
    assert "alpha" in out and "connected" in out
    assert "beta" in out and "disconnected" in out
    assert "https://x/api" in out
    assert "2" in out  # tool count for the connected server


async def test_run_list_tools_error_shows_question_mark() -> None:
    manager = _Manager([_Conn("alpha", "http", connected=True, raise_list=True)])
    committed: list[object] = []
    await run_mcp_viewer(manager=manager, commit=committed.append)
    out = _render(committed[0])
    # The row still renders; the count degrades to '?'.
    assert "alpha" in out
    assert "?" in out


async def test_run_list_tools_timeout_shows_question_mark() -> None:
    # Patch the timeout to a tiny value so the hung server trips wait_for fast.
    import aelix_coding_agent.tui.mcp_viewer as mod

    original = mod._LIST_TOOLS_TIMEOUT_SECONDS
    mod._LIST_TOOLS_TIMEOUT_SECONDS = 0.01
    try:
        manager = _Manager([_Conn("alpha", "http", connected=True, hang=True)])
        committed: list[object] = []
        await run_mcp_viewer(manager=manager, commit=committed.append)
    finally:
        mod._LIST_TOOLS_TIMEOUT_SECONDS = original
    out = _render(committed[0])
    assert "alpha" in out
    assert "?" in out
