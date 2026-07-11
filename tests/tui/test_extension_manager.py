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


def test_discover_lines_empty_state() -> None:
    # #65 (ADR-0188): no catalog registered → an honest pointer at the CLI.
    lines = build_discover_lines([])
    assert lines
    assert any("No catalog registered" in line for line in lines)
    assert any("aelix extension source add --catalog" in line for line in lines)


def test_sources_lines_empty_state() -> None:
    lines = build_sources_lines([])
    assert lines
    assert any("No extension sources registered." in line for line in lines)
    # Points the user at the CLI (the viewer is read-only).
    assert any("aelix extension source add" in line for line in lines)


def test_sources_lines_render_persisted() -> None:
    from aelix_ai.settings import ExtensionSourceObject

    lines = build_sources_lines(
        [
            ExtensionSourceObject(spec="https://idx/simple", kind="index"),
            ExtensionSourceObject(spec="git+https://h/r.git", kind="git", name="r"),
        ]
    )
    body = "\n".join(lines)
    assert "[index] https://idx/simple" in body
    assert "[git] git+https://h/r.git (installed as r)" in body


def test_sources_lines_getattr_guarded() -> None:
    # A duck-typed odd shape (missing fields) degrades to "?" rather than raising.
    class _Odd:
        kind = "path"

    lines = build_sources_lines([_Odd()])
    assert any("[path] ?" in line for line in lines)


def test_sources_lines_builtin_default_present_row() -> None:
    # Track D (guard ③, ADR-0192): the injected built-in default renders FIRST as
    # a marked "present" row, ahead of the stored sources.
    from aelix_ai.settings import ExtensionSourceObject

    lines = build_sources_lines(
        [ExtensionSourceObject(spec="https://idx/simple", kind="index")],
        default_catalog=("https://catalog.aelix.dev/index.json", False),
    )
    body = "\n".join(lines)
    assert "[catalog] https://catalog.aelix.dev/index.json  (built-in default — present)" in body
    assert "[index] https://idx/simple" in body
    # The built-in default row precedes the stored source.
    default_i = next(i for i, ln in enumerate(lines) if "built-in default" in ln)
    stored_i = next(i for i, ln in enumerate(lines) if "https://idx/simple" in ln)
    assert default_i < stored_i


def test_sources_lines_builtin_default_suppressed_row() -> None:
    # An opted-out (tombstoned) default renders the SAME row marked "suppressed".
    lines = build_sources_lines(
        [],
        default_catalog=("https://catalog.aelix.dev/index.json", True),
    )
    body = "\n".join(lines)
    assert "[catalog] https://catalog.aelix.dev/index.json  (built-in default — suppressed)" in body
    # Even with NO stored sources the built-in row shows (not the empty-state).
    assert "No extension sources registered." not in body
    assert body.startswith("Registered sources:")


def test_sources_lines_no_default_keeps_empty_state() -> None:
    # No stored sources AND no injected default (beta dormant) → the empty-state,
    # unchanged from before this feature.
    assert build_sources_lines([], default_catalog=None) == build_sources_lines([])
    assert any(
        "No extension sources registered." in ln
        for ln in build_sources_lines([], default_catalog=None)
    )


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

    async def fake_tabbed(
        title: str, tabs: list[Any], *, initial: int = 0, filter_tabs: set[int] | None = None
    ) -> None:
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


async def test_run_sources_tab_reads_getter_live() -> None:
    from aelix_ai.settings import ExtensionSourceObject

    captured: dict[str, Any] = {}

    async def fake_tabbed(
        title: str, tabs: list[Any], *, initial: int = 0, filter_tabs: set[int] | None = None
    ) -> None:
        captured["tabs"] = tabs

    live: list[Any] = []

    await run_extension_manager(
        extensions=[],
        mcp_manager=None,
        tabbed=fake_tabbed,
        commit=[].append,
        sources_getter=lambda: live,  # read live at render time
    )
    render = dict(captured["tabs"])["Sources"]
    assert any("No extension sources registered." in ln for ln in render())
    # Mutate AFTER open — the closure re-reads, so the new source appears.
    live.append(ExtensionSourceObject(spec="https://idx/simple", kind="index"))
    assert any("[index] https://idx/simple" in ln for ln in render())


async def test_run_sources_tab_renders_injected_default_catalog() -> None:
    # Track D (guard ③): the Sources render reads default_catalog_getter live and
    # renders the marked built-in row alongside the stored sources.
    captured: dict[str, Any] = {}

    async def fake_tabbed(
        title: str, tabs: list[Any], *, initial: int = 0, filter_tabs: set[int] | None = None
    ) -> None:
        captured["tabs"] = tabs

    default: tuple[str, bool] | None = ("https://catalog.aelix.dev/index.json", False)

    await run_extension_manager(
        extensions=[],
        mcp_manager=None,
        tabbed=fake_tabbed,
        commit=[].append,
        default_catalog_getter=lambda: default,
    )
    render = dict(captured["tabs"])["Sources"]
    assert any("(built-in default — present)" in ln for ln in render())
    # Read live: flip to suppressed AFTER open — the closure re-reads.
    default = ("https://catalog.aelix.dev/index.json", True)
    assert any("(built-in default — suppressed)" in ln for ln in render())


async def test_run_default_catalog_getter_raising_degrades() -> None:
    # A raising default_catalog_getter omits the built-in row but keeps the stored
    # sources — never crashes the tab render.
    from aelix_ai.settings import ExtensionSourceObject

    captured: dict[str, Any] = {}

    async def fake_tabbed(
        title: str, tabs: list[Any], *, initial: int = 0, filter_tabs: set[int] | None = None
    ) -> None:
        captured["tabs"] = tabs

    def _boom() -> Any:
        raise RuntimeError("default resolve failed")

    await run_extension_manager(
        extensions=[],
        mcp_manager=None,
        tabbed=fake_tabbed,
        commit=[].append,
        sources_getter=lambda: [ExtensionSourceObject(spec="https://idx/simple", kind="index")],
        default_catalog_getter=_boom,
    )
    render = dict(captured["tabs"])["Sources"]
    body = "\n".join(render())
    assert "built-in default" not in body  # the raising getter is swallowed
    assert "[index] https://idx/simple" in body  # stored sources still render


async def test_run_sources_getter_raising_degrades() -> None:
    captured: dict[str, Any] = {}

    async def fake_tabbed(
        title: str, tabs: list[Any], *, initial: int = 0, filter_tabs: set[int] | None = None
    ) -> None:
        captured["tabs"] = tabs

    def _boom() -> Any:
        raise RuntimeError("settings unavailable")

    await run_extension_manager(
        extensions=[],
        mcp_manager=None,
        tabbed=fake_tabbed,
        commit=[].append,
        sources_getter=_boom,
    )
    render = dict(captured["tabs"])["Sources"]
    # A raising getter degrades to the empty-state, never crashes the tab.
    assert any("No extension sources registered." in ln for ln in render())


async def test_run_surfaces_tabbed_exception() -> None:
    async def boom_tabbed(
        title: str, tabs: list[Any], *, initial: int = 0, filter_tabs: set[int] | None = None
    ) -> None:
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
