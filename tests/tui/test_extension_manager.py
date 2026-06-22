"""Unit tests for the /extension manager flow (Sprint WP-8, Feature 3)."""

from __future__ import annotations

from typing import Any

from aelix_coding_agent.tui.extension_manager import (
    build_discover_lines,
    build_installed_lines,
    build_sources_lines,
    run_extension_manager,
)


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


class _Plugin:
    def __init__(self, name: str | None = None, version: str | None = None) -> None:
        self.name = name
        self.version = version


class _Manifest:
    def __init__(self, plugin: _Plugin) -> None:
        self.plugin = plugin


class _Ext:
    """Duck-typed Extension: loader ``name`` + optional parsed manifest."""

    def __init__(self, name: str, manifest: _Manifest | None = None) -> None:
        self.name = name
        self.manifest = manifest


class _Conn:
    def __init__(self, name: str, transport: str, *, connected: bool) -> None:
        self.name = name
        self.transport = transport
        self.connected = connected


class _Manager:
    def __init__(self, conns: list[_Conn]) -> None:
        self.connections = {c.name: c for c in conns}


# === build_installed_lines ===


def test_installed_empty_state() -> None:
    lines = build_installed_lines([], [])
    assert lines == ["No plugins or MCP servers installed."]


def test_installed_empty_state_none_inputs() -> None:
    # None inputs (no extensions list / no mcp conns) degrade like empty.
    lines = build_installed_lines(None, None)
    assert lines == ["No plugins or MCP servers installed."]


def test_installed_extension_with_manifest_name_and_version() -> None:
    ext = _Ext("loader-name", _Manifest(_Plugin(name="Pretty Name", version="1.2.3")))
    lines = build_installed_lines([ext], [])
    body = "\n".join(lines)
    # Manifest identity name wins over the loader name; version is appended.
    assert "✓ Pretty Name 1.2.3" in body
    assert "loader-name" not in body


def test_installed_extension_falls_back_to_loader_name() -> None:
    # No manifest → fall back to Extension.name, omit the version (no placeholder).
    ext = _Ext("legacy-ext", manifest=None)
    lines = build_installed_lines([ext], [])
    # Falls back to the loader name; no version token appended.
    assert any(line.strip() == "✓ legacy-ext" for line in lines)


def test_installed_extension_missing_version_omits_version() -> None:
    ext = _Ext("x", _Manifest(_Plugin(name="HasName", version=None)))
    lines = build_installed_lines([ext], [])
    assert any(line.strip() == "✓ HasName" for line in lines)


def test_installed_extension_bare_object_degrades_to_question_mark() -> None:
    # An object with no name/manifest attributes degrades to '?'.
    bare: Any = object()
    lines = build_installed_lines([bare], [])
    assert any("✓ ?" in line for line in lines)


def test_installed_mcp_servers_render_state() -> None:
    manager = _Manager(
        [
            _Conn("alpha", "http", connected=True),
            _Conn("beta", "stdio", connected=False),
        ]
    )
    lines = build_installed_lines([], list(manager.connections.values()))
    body = "\n".join(lines)
    assert "MCP servers:" in body
    assert "alpha — http — connected" in body
    assert "beta — stdio — disconnected" in body


def test_installed_mcp_missing_fields_degrade() -> None:
    bare: Any = object()
    lines = build_installed_lines([], [bare])
    body = "\n".join(lines)
    # Missing name/transport degrade to '?'; missing connected → disconnected.
    assert "? — ? — disconnected" in body


def test_installed_both_extensions_and_mcp() -> None:
    ext = _Ext("e", _Manifest(_Plugin(name="Ext One", version="0.1.0")))
    conns = [_Conn("srv", "http", connected=True)]
    lines = build_installed_lines([ext], conns)
    body = "\n".join(lines)
    assert "Plugins:" in body
    assert "✓ Ext One 0.1.0" in body
    assert "MCP servers:" in body
    assert "srv — http — connected" in body
    # Read-only honesty note present.
    assert any("Read-only" in line for line in lines)


def test_builtin_safety_extensions_not_listed_as_user_plugins() -> None:
    # Guardrail/Permission are PREPENDED built-ins with no manifest — they must
    # not masquerade as user plugins, and with only built-ins present the user
    # plugin list reports it has none (empty-state reachable for a clean install).
    guardrail = _Ext("GuardrailExtension", manifest=None)
    permission = _Ext("PermissionExtension", manifest=None)
    lines = build_installed_lines([guardrail, permission], [])
    body = "\n".join(lines)
    assert "(no user plugins installed)" in body
    # Built-ins are surfaced under their own section, NOT with a ✓ plugin marker.
    assert "Built-in (always on):" in body
    assert "GuardrailExtension" in body
    assert "PermissionExtension" in body
    assert "✓ GuardrailExtension" not in body
    assert "✓ PermissionExtension" not in body


def test_user_plugin_alongside_builtins_separated() -> None:
    guardrail = _Ext("GuardrailExtension", manifest=None)
    user = _Ext("u", _Manifest(_Plugin(name="My Plugin", version="2.0.0")))
    lines = build_installed_lines([guardrail, user], [])
    body = "\n".join(lines)
    assert "✓ My Plugin 2.0.0" in body  # the real plugin
    assert "Built-in (always on):" in body
    assert "(no user plugins installed)" not in body  # there IS a user plugin


def test_builtin_named_user_plugin_with_manifest_is_not_hidden() -> None:
    # A user plugin that happens to share a built-in's class name but carries a
    # manifest must still be listed as a plugin (the manifest disambiguates).
    impostor = _Ext(
        "GuardrailExtension",
        _Manifest(_Plugin(name="GuardrailExtension", version="9.9.9")),
    )
    lines = build_installed_lines([impostor], [])
    body = "\n".join(lines)
    assert "✓ GuardrailExtension 9.9.9" in body
    assert "Built-in (always on):" not in body


# === build_discover_lines / build_sources_lines ===


def test_discover_lines_honest_static() -> None:
    lines = build_discover_lines()
    assert lines
    assert any("No registry configured." in line for line in lines)


def test_sources_lines_honest_static() -> None:
    lines = build_sources_lines()
    assert lines
    assert any("No extension sources configured." in line for line in lines)


# === run_extension_manager ===


async def test_run_no_tabbed_viewer_degrades() -> None:
    committed: list[object] = []
    await run_extension_manager(
        extensions=[],
        mcp_manager=None,
        tabbed=None,
        commit=committed.append,
    )
    assert any("Extension manager unavailable" in _plain(c) for c in committed)


async def test_run_invokes_tabbed_with_three_tabs() -> None:
    captured: dict[str, Any] = {}

    async def fake_tabbed(title: str, tabs: list[Any]) -> None:
        captured["title"] = title
        captured["tabs"] = tabs

    ext = _Ext("e", _Manifest(_Plugin(name="Ext", version="1.0.0")))
    manager = _Manager([_Conn("srv", "http", connected=True)])
    committed: list[object] = []
    await run_extension_manager(
        extensions=[ext],
        mcp_manager=manager,
        tabbed=fake_tabbed,
        commit=committed.append,
    )
    assert captured["title"] == "Extensions"
    names = [name for name, _ in captured["tabs"]]
    assert names == ["Installed", "Discover", "Sources"]
    # The Installed render closure reflects the live inventory.
    installed = dict(captured["tabs"])["Installed"]()
    body = "\n".join(installed)
    assert "✓ Ext 1.0.0" in body
    assert "srv — http — connected" in body
    # No degrade message committed on the happy path.
    assert committed == []


async def test_run_surfaces_tabbed_exception() -> None:
    async def boom_tabbed(title: str, tabs: list[Any]) -> None:
        raise RuntimeError("viewer boom")

    committed: list[object] = []
    await run_extension_manager(
        extensions=[],
        mcp_manager=None,
        tabbed=boom_tabbed,
        commit=committed.append,
    )
    assert any("extension manager failed" in _plain(c) for c in committed)
    assert any("viewer boom" in _plain(c) for c in committed)
