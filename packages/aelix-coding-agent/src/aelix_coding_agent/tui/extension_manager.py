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
"Installed" tab is a point-in-time inventory with an honest note. The "Sources"
tab renders the LIVE persisted ``extension_sources`` list (#32-A, ADR-0186) —
managed from the CLI (``aelix extension source add|list|remove``); the viewer
stays read-only (no in-TUI mutation). "Discover" remains honest static text —
there is no discover-catalog yet (a deferred follow-up), so no fabricated
listing.

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


def _catalog_label(cat: Any) -> str:
    """Best-effort display label for a cached ``Catalog`` (getattr-guarded)."""

    label_fn = getattr(cat, "label", None)
    if callable(label_fn):
        try:
            label = label_fn()
        except Exception:  # noqa: BLE001 — degrade, never raise in a render path
            label = None
        if label:
            return str(label)
    name = getattr(cat, "name", None)
    if name:
        return str(name)
    location = getattr(cat, "location", None)
    return str(location) if location else "?"


def _entry_version(entry: Any) -> str | None:
    """Best-effort display version for a ``CatalogEntry`` (getattr-guarded)."""

    dv = getattr(entry, "display_version", None)
    if callable(dv):
        try:
            version = dv()
        except Exception:  # noqa: BLE001 — degrade, never raise in a render path
            return None
        return str(version) if version else None
    return None


def build_discover_lines(catalogs: Any) -> list[str]:
    """Render the "Discover" tab from the cached catalogs (Issue #65, ADR-0188).

    ``catalogs`` is the cached ``list[Catalog]`` (each with ``label()`` /
    ``entries`` / optional ``error``) read live by ``run_extension_manager`` via
    the injected ``catalog_getter`` — the TUI reads the on-disk cache only (no
    network I/O in this render path; ``discover --refresh`` is the sole fetcher).
    Fully getattr-guarded (like :func:`build_sources_lines`) so an odd shape
    degrades rather than raising.

    Entries group under each catalog label, rendered
    ``  <name>  <version?>  — <description?>``; a fetch failure surfaces a
    ``⚠ <error>`` row for that catalog. Empty (no catalog registered) → an honest
    pointer at the CLI ``source add --catalog`` command. The catalog is ADVISORY
    (search/install go through ``aelix extension discover …``); the tab itself is
    read-only.
    """

    cats = list(catalogs or [])
    if not cats:
        return [
            "No catalog registered — register one with:",
            "  aelix extension source add --catalog <url|file|git>",
            "",
            "A catalog is advisory: it only lists installable extensions. Search",
            "and install via `aelix extension discover …`.",
        ]

    lines: list[str] = []
    for cat in cats:
        lines.append(f"{_catalog_label(cat)}:")
        error = getattr(cat, "error", None)
        entries = list(getattr(cat, "entries", ()) or [])
        if error:
            lines.append(f"  ⚠ {error}")
        elif not entries:
            lines.append("  (no extensions listed)")
        for entry in entries:
            name = str(getattr(entry, "name", None) or "?")
            version = _entry_version(entry)
            description = getattr(entry, "description", None)
            row = f"  {name}"
            if version:
                row += f"  {version}"
            if description:
                row += f"  — {description}"
            lines.append(row)
        lines.append("")

    lines.append("Read-only: search/install via `aelix extension discover …`.")
    return lines


def build_sources_lines(
    sources: Any,
    default_catalog: tuple[str, bool] | None = None,
) -> list[str]:
    """Render the "Sources" tab from the persisted ``extension_sources`` list.

    ``sources`` is an iterable of ``ExtensionSourceObject``-shaped items (#32-A,
    ADR-0186) — each with ``spec`` / ``kind`` / optional ``name``. Fully
    getattr-guarded so an odd shape degrades to ``"?"`` rather than raising.

    ``default_catalog`` (Track D, guard ③ / ADR-0192) is the injected built-in
    default catalog value — an ``(url, suppressed)`` pair, or :data:`None` when the
    default is dormant/disabled (the beta placeholder is empty → ``None`` → no row).
    When present it renders FIRST as a marked ``[catalog] <url>  (built-in default —
    present|suppressed)`` row so an opt-out is visible in the viewer. The value is
    INJECTED (mirroring the CLI ``source list``) so this leaf never imports
    ``extension_install`` / the merge+tombstone machinery.

    Empty list AND no built-in default → an honest empty-state that points at the
    CLI (the TUI viewer is read-only; sources are managed via ``aelix extension
    source …``).
    """

    items = list(sources or [])

    # Guard ③ (ADR-0192): the built-in default catalog is its own marked row
    # (present / suppressed). Dormant in beta (empty placeholder URL → injected
    # None → no row); an env repoint surfaces it here.
    default_row: str | None = None
    if default_catalog is not None:
        url, suppressed = default_catalog
        state = "suppressed" if suppressed else "present"
        default_row = f"  [catalog] {url}  (built-in default — {state})"

    if not items and default_row is None:
        return [
            "No extension sources registered.",
            "",
            "Register install sources from the CLI:",
            "  aelix extension source add <path | git-url | index-url>",
            "  aelix extension source list / remove",
            "",
            "An index source resolves bare-name installs; a git / path source is",
            "itself an installable extension.",
        ]

    lines: list[str] = ["Registered sources:"]
    if default_row is not None:
        lines.append(default_row)
    for src in items:
        kind = str(getattr(src, "kind", None) or "?")
        spec = str(getattr(src, "spec", None) or "?")
        name = getattr(src, "name", None)
        suffix = f" (installed as {name})" if name else ""
        lines.append(f"  [{kind}] {spec}{suffix}")
    lines.append("")
    lines.append("Read-only: manage sources via `aelix extension source …`.")
    return lines


async def run_extension_manager(
    *,
    extensions: Any,
    mcp_manager: Any,
    tabbed: Callable[..., Awaitable[None]] | None,
    commit: Callable[[object], None],
    sources_getter: Callable[[], Any] | None = None,
    catalog_getter: Callable[[], Any] | None = None,
    default_catalog_getter: Callable[[], tuple[str, bool] | None] | None = None,
) -> None:
    """Drive the ``/extension`` read-only tabbed viewer (Sprint WP-8, Feature 3).

    Module-level + dependency-injected (duck-typed ``extensions`` list +
    ``mcp_manager`` + the ``tabbed`` viewer + ``commit`` + a
    ``sources_getter``) so the formatting is unit-testable without the
    prompt-toolkit app. ``shell.py`` wires the live discovered extensions + MCP
    manager + :meth:`AelixTUIContext.tabbed` + a ``SettingsManager``-backed
    source reader into it.

    ``sources_getter`` (#32-A) is read INSIDE the Sources render closure so each
    open reflects the current persisted list (a source added from the CLI while
    the TUI runs shows up on the next ``/extension``). :data:`None` (or a getter
    that raises) degrades to the empty-state — never crashes.

    ``catalog_getter`` (Issue #65, ADR-0188) is read INSIDE the Discover render
    closure — it returns the cached ``list[Catalog]`` (``load_cached_catalog``, a
    SYNC on-disk read, NO network) so the filterable Discover tab reflects the
    last ``discover --refresh``. :data:`None` (or a getter that raises) degrades
    to the empty-state.

    ``default_catalog_getter`` (Track D, guard ③ / ADR-0192) is read INSIDE the
    Sources render closure — it returns the injected built-in default catalog
    ``(url, suppressed)`` pair (or :data:`None` when dormant/disabled) so the marked
    built-in row reflects an env repoint / a CLI opt-out live on the next open.
    :data:`None` (or a getter that raises) simply omits the built-in row — the
    stored sources still render.

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

    def _default_catalog() -> tuple[str, bool] | None:
        # Guard ③ (ADR-0192): read the injected built-in default value live so an
        # env repoint / a CLI opt-out during the session shows on the next open. A
        # getter that raises simply omits the built-in row (stored sources unaffected).
        if default_catalog_getter is None:
            return None
        try:
            return default_catalog_getter()
        except Exception:  # noqa: BLE001 — never crash the tab render
            return None

    def _sources() -> list[str]:
        # Read the persisted list live at render time so a CLI-added source
        # appears on the next open. A getter that raises degrades to empty. The
        # built-in default row (guard ③) is injected alongside so it renders even
        # when the stored list is empty or its getter degraded.
        default_catalog = _default_catalog()
        if sources_getter is None:
            return build_sources_lines([], default_catalog=default_catalog)
        try:
            return build_sources_lines(sources_getter(), default_catalog=default_catalog)
        except Exception:  # noqa: BLE001 — never crash the tab render
            return build_sources_lines([], default_catalog=default_catalog)

    def _discover() -> list[str]:
        # Read the cached catalogs live at render time (SYNC disk read — no
        # network in the render closure; ADR-0188). A getter that raises degrades
        # to the empty-state. Re-invoked on every keypress by the filterable tab.
        if catalog_getter is None:
            return build_discover_lines([])
        try:
            return build_discover_lines(catalog_getter())
        except Exception:  # noqa: BLE001 — never crash the tab render
            return build_discover_lines([])

    # The Discover tab is the ONLY filterable tab — its cached catalog can be
    # long, so type-to-filter narrows it in place (Installed / Sources stay
    # read-only). Compute its index so the filter set never drifts from the order.
    tabs: list[tuple[str, Callable[[], list[str]]]] = [
        ("Installed", _installed),
        ("Discover", _discover),
        ("Sources", _sources),
    ]
    discover_idx = next((i for i, (name, _) in enumerate(tabs) if name == "Discover"), 1)

    try:
        await tabbed("Extensions", tabs, filter_tabs={discover_idx})
    except Exception as exc:  # noqa: BLE001 — surface, never crash the REPL
        commit(Text(f"✖ extension manager failed: {exc}", style="bold red"))


__all__ = [
    "build_discover_lines",
    "build_installed_lines",
    "build_sources_lines",
    "run_extension_manager",
]
