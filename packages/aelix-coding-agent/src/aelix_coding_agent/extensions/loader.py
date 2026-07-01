"""Extension loader — resolves a heterogeneous list of paths/factories.

A loader call produces:

- one :class:`Extension` per successful factory,
- a list of :class:`ExtensionLoadError` for failures (one bad extension does
  not stop the others — Pi parity, ``/tmp/pi-ext-loader.ts:437``),
- a single :class:`_ExtensionRuntime` instance shared by every spawned
  :class:`ExtensionAPI` (D.1.7).

Path resolution:

- ``str`` or ``Path`` ending in ``.py`` → loaded via
  ``importlib.util.spec_from_file_location``.
- Other ``str`` → ``importlib.import_module`` (dotted module path).
- Anything else is treated as a callable factory and invoked directly. Class
  instances with a ``__call__(self, aelix)`` (e.g. ``PolicyExtension()``) are
  valid factories per D.1.8.

Sprint 5a (Phase 3.1, ADR-0028 Accepted / ADR-0041): adds
:func:`discover_and_load_extensions` — a Pi-parity 3-tier directory scan
(project-local ``cwd/.aelix/extensions/``, global ``~/.aelix/extensions/``,
explicit configured paths) PLUS an Aelix-additive
``entry_points(group="aelix.extensions")`` pass. The directory scan is the
**primary** discovery channel (Pi parity); ``entry_points`` is layered on
LAST so installed packages cannot shadow project-local files (P-21
reversal of the original Draft ADR-0028).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import logging
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aelix_agent_core.contracts import (
    AELIX_API_LEVEL,
    LICENSE_WHITELIST,
    PluginManifest,
    parse_manifest_toml,
)
from pydantic import ValidationError

from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionFactory,
    _ExtensionRuntime,
)

logger = logging.getLogger(__name__)


@dataclass
class ExtensionLoadError:
    """Captures a per-extension failure without aborting the whole load."""

    path: str
    error: str


@dataclass
class LoadExtensionsResult:
    """Aggregate result returned by :func:`load_extensions`.

    The shared ``runtime`` is what :class:`~aelix_agent_core.harness.core.AgentHarness`
    later binds via :meth:`_ExtensionRuntime.bind_core`.

    ``runtime`` defaults to a fresh :class:`_ExtensionRuntime` if not provided;
    in normal use, :func:`load_extensions` always supplies one.
    """

    extensions: list[Extension] = field(default_factory=list)
    errors: list[ExtensionLoadError] = field(default_factory=list)
    runtime: _ExtensionRuntime = field(default_factory=_ExtensionRuntime)


async def load_extensions(
    paths: Sequence[str | Path | ExtensionFactory | _ManifestEntry],
    *,
    cwd: Path | None = None,
    flag_values: Mapping[str, bool | str] | None = None,
) -> LoadExtensionsResult:
    """Load extensions from module paths, file paths, or inline factories.

    Each entry produces one :class:`Extension`. Failures are collected as
    :class:`ExtensionLoadError` so that one bad extension does not abort
    the rest of the wave.

    Sprint 6h₉b: the ``paths`` sequence may also contain internal
    ``_ManifestEntry`` carriers produced by
    :func:`discover_and_load_extensions`. The carrier type is NOT
    exported; external callers continue to pass the original
    ``str | Path | ExtensionFactory`` union — :class:`Sequence` keeps
    the parameter list covariant so a narrower list still type-checks.
    """

    # Issue #24-FU: pre-seed flag_values BEFORE the extension setup loop so a
    # re-run ``setup()`` reads the user's restored value (register_flag's
    # ``name not in flag_values`` guard then skips the default). Mirrors pi
    # ``_buildRuntime`` seeding ``runtime.flagValues`` before the runner is built.
    runtime = _ExtensionRuntime(flag_values=flag_values)
    result = LoadExtensionsResult(runtime=runtime)
    for entry in paths:
        try:
            factory, name, manifest = await _resolve_factory(entry, cwd=cwd)
        except Exception as exc:  # noqa: BLE001 — surface as load error
            result.errors.append(
                ExtensionLoadError(path=str(entry), error=str(exc))
            )
            continue
        try:
            extension = await _invoke_factory(
                factory, runtime, name=name, manifest=manifest
            )
        except Exception as exc:  # noqa: BLE001 — surface as load error
            result.errors.append(
                ExtensionLoadError(path=name, error=str(exc))
            )
            continue
        result.extensions.append(extension)
    return result


async def load_extension_from_factory(
    factory: ExtensionFactory,
    *,
    name: str = "<inline>",
    runtime: _ExtensionRuntime | None = None,
) -> Extension:
    """Invoke a factory directly and return the populated :class:`Extension`."""

    rt = runtime or _ExtensionRuntime()
    return await _invoke_factory(factory, rt, name=name)


# === Sprint 5a (Phase 3.1) — discover_and_load_extensions ===


async def discover_and_load_extensions(
    configured_paths: list[str | Path | ExtensionFactory],
    *,
    cwd: Path,
    agent_dir: Path | None = None,
    prepend: list[ExtensionFactory] | None = None,
    no_discovery: bool = False,
    no_project_local: bool = False,
    flag_values: Mapping[str, bool | str] | None = None,
) -> LoadExtensionsResult:
    """Pi-parity 3-tier discovery + Aelix-additive entry_points pass.

    Pi source: ``packages/coding-agent/src/core/extensions/loader.ts``
    ``discoverAndLoadExtensions()`` at SHA ``734e08e`` (lines 575-621).

    **P-21 reversal (ADR-0028 Accepted)**: directory scan is the PRIMARY
    discovery channel (Pi parity). The original Draft ADR treated
    ``entry_points`` as primary; the corrected reality is that Pi has no
    ``entry_points`` analogue and Aelix layers ``entry_points`` on as an
    additive convenience, loaded LAST so installed packages cannot shadow
    project-local files.

    Discovery order (highest priority first):

    1. ``cwd / .aelix / extensions /`` — project-local files / packages.
    2. ``~/.aelix/extensions/`` (or ``agent_dir`` override) — user globals.
    3. ``configured_paths`` — explicit entries provided by the caller. A
       directory entry is expanded via :func:`_discover_in_dir`; an entry
       resolving to a directory with a ``pyproject.toml [tool.aelix]
       extensions = [...]`` manifest uses the declared list.
    4. ``entry_points(group="aelix.extensions")`` — Aelix-additive. Each
       endpoint is resolved by ``.load()`` and treated as an inline
       factory (or a callable class instance per D.1.8).

    Deduplication: by ``Path.resolve()`` for filesystem paths; entry-point
    factories are deduplicated by their ``ep.value`` string so two endpoint
    declarations pointing at the same factory module:object load once.

    Error containment: per-entry try/except inside each tier — a single
    bad endpoint never aborts the wave. Errors append to
    :attr:`LoadExtensionsResult.errors`.

    ``no_project_local`` (Sprint P0 #10 Project Trust): when ``True``, skip
    ONLY tier 1 (the auto-discovered ``cwd/.aelix/extensions/`` project-local
    directory) while still loading the global tier 2, the explicit
    ``configured_paths`` (tier 3, i.e. ``-e``), and entry_points (tier 4).
    This is a FINER gate than ``no_discovery`` (which disables tiers 1, 2, AND
    4): the trust gate must suppress untrusted project-local code WITHOUT
    breaking user-chosen global/explicit/installed extensions.
    """

    all_entries: list[str | Path | ExtensionFactory | _ManifestEntry] = []
    seen_paths: set[Path] = set()
    seen_ep: set[str] = set()
    errors: list[ExtensionLoadError] = []

    # Aelix-additive built-ins (``prepend``) register FIRST; load-order
    # precedence then follows Pi (resource-loader.ts) so discovered/user
    # extensions can never shadow Guardrail/Permission.
    if prepend:
        all_entries.extend(prepend)

    def _push_entry(entry: Path | _ManifestEntry) -> None:
        # Sprint 6h₉b §B: dedupe by ``pkg_dir.resolve()`` for manifest
        # carriers (one manifest = one extension); legacy ``Path``
        # entries keep their pre-existing resolve dedupe.
        if isinstance(entry, _ManifestEntry):
            try:
                resolved = entry.pkg_dir.resolve()
            except OSError:
                resolved = entry.pkg_dir
            if resolved in seen_paths:
                return
            seen_paths.add(resolved)
            all_entries.append(entry)
            return
        try:
            resolved = entry.resolve()
        except OSError:
            resolved = entry
        if resolved in seen_paths:
            return
        seen_paths.add(resolved)
        all_entries.append(entry)

    # 1+2: directory auto-discovery (skipped under ``no_discovery`` — Pi
    # ``noExtensions`` keeps only explicit ``configured_paths``).
    if not no_discovery:
        # 1. Project-local: cwd/.aelix/extensions/ — gated by the Project
        # Trust gate via ``no_project_local`` (Sprint P0 #10). When an
        # untrusted directory resolves to ``project_trusted=False`` the caller
        # passes ``no_project_local=True`` so this tier's arbitrary .py is
        # NEVER exec_module'd, while the global/explicit/entry_point tiers
        # below still load (they are user-chosen, not project-local).
        if not no_project_local:
            local_dir = (cwd / ".aelix" / "extensions").resolve(strict=False)
            for discovered in _discover_in_dir(local_dir, errors=errors):
                _push_entry(discovered)

        # 2. Global: ~/.aelix/extensions/  (or override via agent_dir)
        home_aelix = Path.home() / ".aelix" if agent_dir is None else agent_dir
        global_dir = (home_aelix / "extensions").resolve(strict=False)
        for discovered in _discover_in_dir(global_dir, errors=errors):
            _push_entry(discovered)

    # 3. Explicit configured paths.
    for entry in configured_paths:
        # Callables/factories pass through (P-21 — explicit takes precedence
        # over entry_points but loses to local/global directories).
        if callable(entry) and not isinstance(entry, (str, Path)):
            all_entries.append(entry)
            continue
        # String / Path: expand directories via _discover_in_dir; pass files
        # through unchanged.
        try:
            p = Path(entry) if isinstance(entry, str) else entry
            resolved_path = p if p.is_absolute() else (cwd / p)
            if resolved_path.is_dir():
                expanded = _discover_in_dir(resolved_path, errors=errors)
                if expanded:
                    for discovered in expanded:
                        _push_entry(discovered)
                    continue
                # Directory exists but has no extension-shaped entries; fall
                # through to treat as a raw path (the inner loader will then
                # report a more useful "no setup()" style error).
            _push_entry(resolved_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                ExtensionLoadError(path=str(entry), error=str(exc))
            )

    # 4. Aelix-additive: entry_points loaded LAST (skipped under no_discovery).
    if not no_discovery:
        for ep_entry, ep_error in _discover_via_entry_points(seen_ep):
            if ep_error is not None:
                errors.append(ep_error)
                continue
            if ep_entry is not None:
                all_entries.append(ep_entry)

    result = await load_extensions(all_entries, cwd=cwd, flag_values=flag_values)
    # Splice discovery-time errors in front of loader-time errors so the
    # caller sees them in the order they happened.
    result.errors = errors + result.errors
    return result


def _discover_in_dir(
    dir_path: Path,
    *,
    errors: list[ExtensionLoadError] | None = None,
) -> list[Path | _ManifestEntry]:
    """Pi-parity ``discoverExtensionsInDir`` (``loader.ts:481-518``).

    For each entry in ``dir_path`` (non-recursive beyond one level):

    - ``*.py`` file → add directly.
    - Subdirectory: check ``aelix-plugin.toml`` (Sprint 6h₉b §B — NEW
      preferred) → use ``_ManifestEntry`` carrier. Else check
      ``pyproject.toml [tool.aelix] extensions=[...]`` (Aelix port of
      Pi's ``package.json "pi.extensions"``) → use declared paths. Else
      look for ``__init__.py`` (Aelix port of Pi's
      ``index.ts/index.js``) → treat the package as a single extension
      via its module path. Else skip.

    Sprint 6h₉b: ``errors`` is an optional sink for
    :class:`ExtensionManifestError` failures so the wave continues when
    one plugin's manifest fails to parse (per-plugin try/except, Pi
    parity ``loader.ts:437``).
    """

    if not dir_path.exists() or not dir_path.is_dir():
        return []
    discovered: list[Path | _ManifestEntry] = []
    try:
        children = sorted(dir_path.iterdir(), key=lambda c: c.name)
    except OSError:
        return []
    for child in children:
        try:
            if child.is_file() and child.suffix == ".py":
                discovered.append(child)
                continue
            if child.is_dir():
                try:
                    declared = _resolve_extension_entries(child)
                except ExtensionManifestError as exc:
                    # Per-plugin containment: one bad manifest never
                    # aborts the wave (Pi parity ``loader.ts:437``).
                    if errors is not None:
                        errors.append(
                            ExtensionLoadError(path=str(child), error=str(exc))
                        )
                    continue
                if declared is not None:
                    discovered.extend(declared)
                # else: skip — no manifest, no __init__.py.
        except OSError:
            continue
    return discovered


class ExtensionManifestError(Exception):
    """Sprint 6h₉b — raised on manifest parse / validation failure.

    Caught by the per-plugin try/except in ``discover_and_load_extensions``
    (via ``load_extensions``) and surfaced as an :class:`ExtensionLoadError`
    with a clear message. Pi-additive — Pi has no manifest concept.
    """


@dataclass(frozen=True)
class _ManifestEntry:
    """Internal carrier for manifest-discovered extensions (Sprint 6h₉b §B).

    A ``_ManifestEntry`` flows through ``load_extensions`` like a Path,
    but carries the parsed manifest + the plugin directory so the inner
    factory resolver can use ``[plugin.entry] python = "module:callable"``
    instead of falling back to the directory's ``setup`` convention.

    NOT exported.
    """

    manifest: PluginManifest
    pkg_dir: Path


def _load_manifest_from_dir(pkg_dir: Path) -> PluginManifest | None:
    """Load ``aelix-plugin.toml`` from ``pkg_dir`` if present (Sprint 6h₉b §B).

    Returns:
        Parsed ``PluginManifest`` on success.
        ``None`` if no ``aelix-plugin.toml`` exists in ``pkg_dir``.

    Raises:
        ExtensionManifestError: on parse / validation failure (TOML
        syntax error, Pydantic validation error, API_LEVEL too low).

    Pi-additive — Pi has no manifest concept.
    """

    manifest_path = pkg_dir / "aelix-plugin.toml"
    if not manifest_path.exists():
        return None

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtensionManifestError(
            f"Cannot read {manifest_path}: {exc}"
        ) from exc

    try:
        manifest = parse_manifest_toml(text)
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ExtensionManifestError(
            f"Invalid manifest {manifest_path}: {exc}"
        ) from exc

    # API_LEVEL gate (ADR-0096 §"API_LEVEL policy").
    if manifest.api.min_level > AELIX_API_LEVEL:
        raise ExtensionManifestError(
            f"Plugin {manifest.plugin.id!r} requires API_LEVEL "
            f">= {manifest.api.min_level}, host has {AELIX_API_LEVEL}"
        )
    if manifest.api.level > AELIX_API_LEVEL:
        # Forward-compat best-effort: log warning, accept anyway.
        logger.warning(
            "Plugin %r built for API_LEVEL %d, host has %d "
            "(loading anyway; behavior at undefined surfaces is best-effort)",
            manifest.plugin.id,
            manifest.api.level,
            AELIX_API_LEVEL,
        )

    # License whitelist (Phase 5b warn-only per ADR-0096 §"SPDX license whitelist v1").
    if manifest.plugin.license not in LICENSE_WHITELIST:
        logger.warning(
            "Plugin %r declares license %r outside the Sprint 6h₉a v1 "
            "whitelist; loading anyway (Phase 5b warn-only policy — "
            "Phase 6 will gate strict via --strict-licenses)",
            manifest.plugin.id,
            manifest.plugin.license,
        )

    return manifest


def _resolve_extension_entries(
    pkg_dir: Path,
) -> list[Path | _ManifestEntry] | None:
    """Sprint 6h₉b augmented resolver — Pi-parity ``resolveExtensionEntries``.

    Pi source: ``loader.ts:496-526`` (corrected from ``:454-479`` in
    Sprint 6h₉b fold-in §B — W5 critic verified the function signature
    is at line 496 at SHA ``734e08e``).

    Priority order (first match wins):

    1. ``aelix-plugin.toml`` — NEW preferred (Sprint 6h₉b §B). Parse via
       Pydantic and return ``[_ManifestEntry(manifest, pkg_dir)]``.
    2. ``pyproject.toml [tool.aelix] extensions = [...]`` — legacy
       package-internal entry list (Aelix mirror of Pi's
       ``package.json "pi.extensions"`` field; unchanged from Sprint 5a).
    3. ``__init__.py`` — single-file fallback (Aelix mirror of Pi's
       ``index.ts/index.js``; unchanged from Sprint 5a).

    Returns ``None`` if no manifest / legacy form is present (signal:
    skip this subdirectory).

    Failure semantics (Sprint 6h₉b fold-in §A — W4 MINOR-2):
        If ``aelix-plugin.toml`` exists but fails to parse / validate,
        :class:`ExtensionManifestError` is raised and the directory is
        treated as **unloadable** — there is NO fall-through to Tier 2
        (``pyproject.toml [tool.aelix]``) or Tier 3 (``__init__.py``).
        A broken manifest is a hard fail; rename the file (or fix the
        contents) to disable manifest-driven discovery for the
        directory.

    Raises:
        ExtensionManifestError: when ``aelix-plugin.toml`` exists but
        fails to parse / validate. Bubbles up to the per-plugin
        try/except in :func:`_discover_in_dir` / :func:`load_extensions`.
    """

    # Tier 1: aelix-plugin.toml (Sprint 6h₉b §B — NEW preferred).
    manifest = _load_manifest_from_dir(pkg_dir)
    if manifest is not None:
        return [_ManifestEntry(manifest=manifest, pkg_dir=pkg_dir)]

    # Tier 2: pyproject.toml [tool.aelix] extensions (Sprint 5a legacy).
    pyproject = pkg_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        declared = (
            data.get("tool", {}).get("aelix", {}).get("extensions")
        )
        if isinstance(declared, list) and declared:
            entries: list[Path | _ManifestEntry] = []
            for raw in declared:
                if not isinstance(raw, str):
                    continue
                resolved = (pkg_dir / raw).resolve(strict=False)
                if resolved.exists():
                    entries.append(resolved)
            if entries:
                return entries

    # Tier 3: __init__.py (Sprint 5a legacy).
    init_py = pkg_dir / "__init__.py"
    if init_py.exists():
        return [init_py]
    return None


def _discover_via_entry_points(
    seen_ep: set[str],
) -> list[tuple[ExtensionFactory | None, ExtensionLoadError | None]]:
    """Aelix-additive entry-point discovery (loaded LAST per P-21).

    Iterates ``entry_points(group="aelix.extensions")`` and returns
    ``(factory, None)`` for each successful load or ``(None, error)`` per
    failure. Per-endpoint try/except so one broken installed package never
    blocks the wave.
    """

    out: list[tuple[ExtensionFactory | None, ExtensionLoadError | None]] = []
    try:
        eps = importlib.metadata.entry_points(group="aelix.extensions")
    except Exception as exc:  # noqa: BLE001 — surface but never abort
        out.append(
            (None, ExtensionLoadError(path="entry_points:aelix.extensions", error=str(exc)))
        )
        return out
    for ep in eps:
        key = f"{ep.name}={ep.value}"
        if key in seen_ep:
            continue
        seen_ep.add(key)
        try:
            factory = ep.load()
        except Exception as exc:  # noqa: BLE001
            out.append(
                (None, ExtensionLoadError(path=f"entry_point:{ep.name}", error=str(exc)))
            )
            continue
        # Pi parity: an endpoint can resolve either to a bare ``setup`` callable
        # or to a class instance / class with __call__(self, aelix). Wrap class
        # objects (uninstantiated) so the inner loader handles them uniformly.
        if isinstance(factory, type):
            try:
                factory = factory()
            except Exception as exc:  # noqa: BLE001
                out.append(
                    (None, ExtensionLoadError(path=f"entry_point:{ep.name}", error=str(exc)))
                )
                continue
        if not callable(factory):
            out.append(
                (
                    None,
                    ExtensionLoadError(
                        path=f"entry_point:{ep.name}",
                        error=(
                            f"entry point {ep.name!r} resolved to "
                            f"non-callable {type(factory).__name__}; "
                            "expected a factory function or class."
                        ),
                    ),
                )
            )
            continue
        out.append((factory, None))
    return out


# === Internal helpers ===


def _noop_factory(api: ExtensionAPI) -> None:
    """No-op factory for hooks-only plugins (Sprint 6h₉e / ADR-0102).

    A manifest declaring ``[[contributes.hooks]]`` but no ``[plugin.entry]
    python`` has no Python surface to load. This factory does nothing —
    :func:`_invoke_factory` still constructs the :class:`Extension` (with
    the manifest attached) and wires the declared subprocess hooks via
    ``api.on(...)`` after the factory runs. A named def (not a lambda) keeps
    the ``__qualname__``-based display-name contract intact.
    """

    return None


async def _resolve_factory(
    entry: str | Path | ExtensionFactory | _ManifestEntry,
    *,
    cwd: Path | None,
) -> tuple[ExtensionFactory, str, PluginManifest | None]:
    """Return ``(factory, display_name, manifest)`` for a single loader entry.

    Sprint 6h₉b §C: the return tuple now carries an optional manifest so
    :func:`_invoke_factory` can attach it to the loaded :class:`Extension`.
    Legacy entry types (callable / Path / str) carry ``manifest=None``;
    only the :class:`_ManifestEntry` branch threads a real manifest
    through.
    """

    # Sprint 6h₉b §C — manifest-discovered plugin: resolve
    # ``[plugin.entry] python = "module:callable"`` via
    # :func:`_factory_from_module` (colon-form supported below).
    if isinstance(entry, _ManifestEntry):
        py_entry = entry.manifest.entry.python
        if py_entry is None:
            if entry.manifest.contributes.hooks:
                # Hooks-only plugin (Tier 4b, Sprint 6h₉e / ADR-0102): no
                # Python factory to load; return a no-op factory so
                # :func:`_invoke_factory` still builds the Extension (with
                # manifest attached) and wires the subprocess hooks.
                return _noop_factory, entry.manifest.plugin.id, entry.manifest
            raise ValueError(
                f"Manifest for plugin {entry.manifest.plugin.id!r} "
                f"in {entry.pkg_dir} has no [plugin.entry] python; "
                f"cannot load (Sprint 6h₉b requires python entry when "
                f"any of capabilities.ui_tui_trusted / .ui_descriptor / "
                f".mcp_serve is True — see Sprint 6h₉a fold-in §A)"
            )
        factory = _factory_from_module(py_entry)
        return factory, entry.manifest.plugin.id, entry.manifest

    # Check callable first; the isinstance guard is defensive because Path objects
    # are not callable, but the order matters: str/Path checks must come after so
    # that a callable class instance (e.g. PolicyExtension()) is handled here.
    if callable(entry) and not isinstance(entry, (str, Path)):
        # Inline factory — class instance or function.
        display = getattr(entry, "__qualname__", None) or type(entry).__name__
        return entry, display, None
    if isinstance(entry, Path):
        return _factory_from_file(entry, cwd=cwd), str(entry), None
    if isinstance(entry, str):
        if entry.endswith(".py"):
            return _factory_from_file(Path(entry), cwd=cwd), entry, None
        return _factory_from_module(entry), entry, None
    raise TypeError(
        f"Unsupported extension entry type: {type(entry).__name__}"
    )


def _factory_from_module(module_path: str) -> ExtensionFactory:
    """Import a module and return its factory callable.

    Sprint 6h₉b §C: now accepts ``"module:callable"`` colon-separated
    form (used by ``aelix-plugin.toml`` ``[plugin.entry] python``).
    Legacy bare-module form ``"module.path"`` still resolves to top-level
    ``setup`` for backward compat.

    Pre-filter note (Sprint 6h₉b fold-in §A — W4 MINOR-3): when called
    from the manifest-driven path, ``module_path`` has already been
    constrained by ``PluginEntry.python``'s Pydantic pattern
    ``^[\\w.]+:\\w+$`` (see :mod:`aelix_agent_core.contracts.manifest`),
    so the empty-module / empty-callable ``ValueError`` below is
    unreachable from manifests — it remains as defense-in-depth for
    direct test / programmatic callers that bypass the manifest layer.

    Raises:
        ValueError: when colon-form has empty module or empty callable
        (only reachable from direct callers — manifest paths are
        pre-filtered by Pydantic).
        AttributeError: when the specified callable does not exist on
        the imported module.
    """

    if ":" in module_path:
        module_name, _, callable_name = module_path.partition(":")
        if not module_name or not callable_name:
            raise ValueError(
                f"Invalid module:callable form {module_path!r}; "
                "expected 'module.path:callable_name'"
            )
        module = importlib.import_module(module_name)
        factory = getattr(module, callable_name, None)
        if factory is None or not callable(factory):
            raise AttributeError(
                f"Module {module_name!r} has no top-level callable "
                f"{callable_name!r} (manifest [plugin.entry] python)."
            )
        return factory

    # Legacy form: bare module, look for top-level ``setup``.
    module = importlib.import_module(module_path)
    factory = getattr(module, "setup", None)
    if factory is None or not callable(factory):
        raise AttributeError(
            f"Module {module_path!r} has no top-level callable 'setup'."
        )
    return factory


def _factory_from_file(path: Path, *, cwd: Path | None) -> ExtensionFactory:
    resolved = path if path.is_absolute() else (cwd or Path.cwd()) / path
    if not resolved.exists():
        raise FileNotFoundError(f"Extension file not found: {resolved}")
    spec = importlib.util.spec_from_file_location(
        f"_aelix_ext_{resolved.stem}",
        resolved,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load extension file: {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "setup", None)
    if factory is None or not callable(factory):
        raise AttributeError(
            f"Extension file {resolved} has no top-level callable 'setup'."
        )
    return factory


async def _invoke_factory(
    factory: ExtensionFactory,
    runtime: _ExtensionRuntime,
    *,
    name: str,
    manifest: PluginManifest | None = None,
) -> Extension:
    """Sprint 6h₉b §C: propagate ``manifest`` to the loaded :class:`Extension`.

    Legacy callers (``load_extension_from_factory``, tests that bypass
    the discovery pipeline) pass ``manifest=None`` and the resulting
    ``Extension.manifest`` stays ``None``; manifest-discovered plugins
    get their parsed :class:`PluginManifest` attached so Sprint 6h₉c/d/
    e/f consumers can read declared capabilities / activation /
    contributes.
    """

    extension = Extension(name=name, manifest=manifest)
    api = ExtensionAPI(extension, runtime)
    result: Any = factory(api)
    if inspect.iscoroutine(result):
        await result

    # Sprint 6h₉e (Tier 4b, ADR-0102) — wire declared subprocess hooks.
    # Function-local imports avoid a module-level cycle: ``subprocess_hooks``
    # imports :class:`ExtensionManifestError` from this module, so this module
    # may not import it at module scope (§6.3).
    if manifest is not None and manifest.contributes.hooks:
        from typing import cast

        from aelix_agent_core.harness.hooks import HookEventName

        from aelix_coding_agent.extensions.subprocess_hooks import (
            make_subprocess_handler,
            validate_subprocess_hook_event,
        )

        # Trust gate (v1 declarative): capabilities.shell_exec MUST be true.
        if not manifest.capabilities.shell_exec:
            raise ExtensionManifestError(
                f"plugin {manifest.plugin.id!r} declares [[contributes.hooks]] "
                f"but capabilities.shell_exec is false; subprocess hooks "
                f"require shell_exec=true"
            )
        for contrib in manifest.contributes.hooks:
            validate_subprocess_hook_event(contrib.event)
            # NOTE (spec §6.2 deviation): ``ExtensionAPI.on`` does NOT accept a
            # ``source`` kwarg (only ``HookBus.on`` does — ADR-0019 v3). The
            # handler is already attributed to this plugin via the bound
            # ``Extension`` (``Extension.name`` == plugin id), so source
            # attribution is preserved when the harness later wires these
            # handlers into its ``HookBus``. ``error_mode="continue"`` keeps a
            # subprocess hook crash from aborting the harness (fail-open).
            api.on(
                # ``contrib.event`` is a validated ``str``; the 35 typed
                # overloads narrow ``HookEventName`` so we cast to keep pyright
                # at the 8-error baseline (prefer cast over type: ignore).
                cast(HookEventName, contrib.event),
                make_subprocess_handler(contrib),
                error_mode="continue",
            )
    return extension


__all__ = [
    "ExtensionLoadError",
    "LoadExtensionsResult",
    "discover_and_load_extensions",
    "load_extension_from_factory",
    "load_extensions",
]
