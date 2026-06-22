"""DI flow for the ``/extension`` read-only manager (Sprint WP-8, Feature 3).

Mirrors :func:`aelix_coding_agent.tui.mcp_viewer.run_mcp_viewer` and
:func:`aelix_coding_agent.tui.scoped_models.run_scoped_models`: the WHOLE flow is
module-level + dependency-injected (duck-typed ``extensions`` list +
``mcp_manager`` + the ``tabbed`` viewer callable + ``commit``) so the line
builders are unit-testable without standing up the prompt-toolkit app.
``shell.py`` wires the live discovered extension list + MCP manager +
:meth:`AelixTUIContext.tabbed` + output-committer into it.

Aelix-additive read-only VIEWER. Runtime enable/disable is NOT supported
(extensions load once at startup, MCP servers connect at startup) so the
"Installed" tab is a point-in-time inventory with an honest note. There is no
extension registry / marketplace, so "Discover" and "Sources" are honest static
text (no fabricated catalog).

Every failure path (no ``tabbed`` viewer, the viewer raising) surfaces a
committed message and returns — never crashes the REPL, mirroring the existing
``tui/`` handlers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Dim / reset escapes — mirror context.py:71/73 (and stats_dashboard.py) so the
# built-in section renders dim inside the framed tabbed modal (duplicated, not
# imported, to keep this a leaf consumer that never reaches into a shared file).
_DIM = "\x1b[2m"
_RST = "\x1b[0m"


# The Aelix-additive built-in safety extensions are PREPENDED into the loaded
# list (entry.py: ``prepend=[GuardrailExtension(), permission]``) and carry no
# manifest — they are NOT user-installed plugins. Render them under a separate
# dim "Built-in:" section so the user-facing "Plugins" list (and its empty-state)
# reflects only real installs.
_BUILTIN_SAFETY_NAMES = frozenset({"GuardrailExtension", "PermissionExtension"})


def _is_builtin_safety(ext: Any) -> bool:
    """True for an Aelix-additive built-in safety extension (Guardrail/Permission).

    Structural marker: a built-in is prepended with NO manifest and a loader name
    equal to its class name (``type(entry).__name__``). We gate on BOTH the
    well-known class name AND ``manifest is None`` so a user plugin that happened
    to be named similarly (and therefore carries a manifest) is never hidden.
    """

    if getattr(ext, "manifest", None) is not None:
        return False
    return str(getattr(ext, "name", "") or "") in _BUILTIN_SAFETY_NAMES


def _extension_name(ext: Any) -> str:
    """Best-effort display name for an extension.

    Prefers the parsed manifest identity name
    (``ext.manifest.plugin.name``), falling back to the loader-assigned
    :attr:`Extension.name`, then ``"?"``. Fully getattr-guarded so an odd
    extension shape never raises.
    """

    manifest = getattr(ext, "manifest", None)
    plugin = getattr(manifest, "plugin", None)
    manifest_name = getattr(plugin, "name", None)
    if manifest_name:
        return str(manifest_name)
    name = getattr(ext, "name", None)
    return str(name) if name else "?"


def _extension_version(ext: Any) -> str | None:
    """Best-effort version string from the manifest, ``None`` when absent.

    The version lives at ``ext.manifest.plugin.version`` (a
    :class:`~aelix_agent_core.contracts.manifest.PluginIdentity` field).
    Legacy (non-manifest) discovery paths leave ``manifest`` ``None`` →
    degrade to ``None`` so the row omits the version rather than printing a
    placeholder.
    """

    manifest = getattr(ext, "manifest", None)
    plugin = getattr(manifest, "plugin", None)
    version = getattr(plugin, "version", None)
    return str(version) if version else None


def build_installed_lines(extensions: Any, mcp_conns: Any) -> list[str]:
    """Render the "Installed" tab: loaded extensions, then MCP servers.

    ``extensions`` is the discovered ``list[Extension]`` (each with an optional
    parsed manifest carrying name/version); ``mcp_conns`` is an iterable of MCP
    connection objects (``McpClientManager.connections.values()`` — each with
    ``name`` / ``transport`` / ``connected``). Both are fully getattr-guarded so
    a missing field degrades to ``"?"`` / an omitted version rather than raising.

    The Aelix-additive built-in safety extensions (Guardrail / Permission) are
    rendered under a separate dim "Built-in:" section, NOT counted as user
    plugins — so the empty-state below is reachable on a clean install.

    Empty inventory (no USER plugins, no built-ins AND no MCP servers) →
    ``["No plugins or MCP servers installed."]``.
    """

    all_exts = list(extensions or [])
    builtins = [e for e in all_exts if _is_builtin_safety(e)]
    user_exts = [e for e in all_exts if not _is_builtin_safety(e)]
    conns = list(mcp_conns or [])
    if not user_exts and not builtins and not conns:
        return ["No plugins or MCP servers installed."]

    lines: list[str] = []
    if user_exts:
        lines.append("Plugins:")
        for ext in user_exts:
            name = _extension_name(ext)
            version = _extension_version(ext)
            suffix = f" {version}" if version else ""
            lines.append(f"  ✓ {name}{suffix}")
    elif builtins or conns:
        # No user plugins, but built-ins/MCP exist — say so explicitly rather
        # than letting the built-in section masquerade as the plugin list.
        lines.append("Plugins:")
        lines.append("  (no user plugins installed)")
    if conns:
        if lines:
            lines.append("")
        lines.append("MCP servers:")
        for conn in conns:
            name = getattr(conn, "name", None) or "?"
            transport = getattr(conn, "transport", None) or "?"
            state = "connected" if getattr(conn, "connected", False) else "disconnected"
            lines.append(f"  {name} — {transport} — {state}")
    if builtins:
        if lines:
            lines.append("")
        lines.append("Built-in (always on):")
        for ext in builtins:
            lines.append(f"  {_DIM}● {_extension_name(ext)}{_RST}")
    lines.append("")
    lines.append("Read-only: extensions + MCP servers load at startup.")
    return lines


def build_discover_lines() -> list[str]:
    """Render the "Discover" tab — honest static text (no marketplace).

    Aelix has no extension registry / marketplace, so there is nothing to
    fetch. The text says so plainly rather than fabricating a catalog.
    """

    return [
        "No registry configured.",
        "",
        "Aelix has no extension marketplace yet. Install plugins by adding an",
        "aelix-plugin.toml under a discovered plugin directory (project or",
        "global), then restart so they load at startup.",
    ]


def build_sources_lines() -> list[str]:
    """Render the "Sources" tab — honest static text (no remote sources).

    There is no configurable remote source list; extensions are discovered
    from local plugin directories at startup.
    """

    return [
        "No extension sources configured.",
        "",
        "Plugins are discovered locally (project + global plugin directories) at",
        "startup. There are no remote sources to add or manage.",
    ]


async def run_extension_manager(
    *,
    extensions: Any,
    mcp_manager: Any,
    tabbed: Callable[..., Awaitable[None]] | None,
    commit: Callable[[object], None],
) -> None:
    """Drive the ``/extension`` read-only tabbed viewer (Sprint WP-8, Feature 3).

    Module-level + dependency-injected (duck-typed ``extensions`` list +
    ``mcp_manager`` + the ``tabbed`` viewer + ``commit``) so the formatting is
    unit-testable without the prompt-toolkit app. ``shell.py`` wires the live
    discovered extensions + MCP manager + :meth:`AelixTUIContext.tabbed` into it.

    Degrades to a committed message when no ``tabbed`` viewer is available
    (headless / not wired) and surfaces any viewer exception as a red line —
    never crashes the REPL.
    """

    from rich.text import Text  # local import keeps this module import-light

    if tabbed is None:
        commit(Text("Extension manager unavailable (no tabbed viewer).", style="yellow"))
        return

    exts = list(extensions or [])

    # Re-read the live MCP connection set INSIDE the render closure so each tab
    # switch reflects a server that connected / dropped while the modal is open
    # (matching ``/mcp``). The extensions list is fixed at startup, so it is
    # captured once.
    def _installed() -> list[str]:
        conns = list(getattr(mcp_manager, "connections", {}).values())
        return build_installed_lines(exts, conns)

    try:
        await tabbed(
            "Extensions",
            [
                ("Installed", _installed),
                ("Discover", build_discover_lines),
                ("Sources", build_sources_lines),
            ],
        )
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ extension manager failed: {exc}", style="bold red"))


__all__ = [
    "build_discover_lines",
    "build_installed_lines",
    "build_sources_lines",
    "run_extension_manager",
]
