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
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionFactory,
    _ExtensionRuntime,
)


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
    paths: list[str | Path | ExtensionFactory],
    *,
    cwd: Path | None = None,
) -> LoadExtensionsResult:
    """Load extensions from module paths, file paths, or inline factories.

    Each entry produces one :class:`Extension`. Failures are collected as
    :class:`ExtensionLoadError` so that one bad extension does not abort
    the rest of the wave.
    """

    runtime = _ExtensionRuntime()
    result = LoadExtensionsResult(runtime=runtime)
    for entry in paths:
        try:
            factory, name = await _resolve_factory(entry, cwd=cwd)
        except Exception as exc:  # noqa: BLE001 — surface as load error
            result.errors.append(
                ExtensionLoadError(path=str(entry), error=str(exc))
            )
            continue
        try:
            extension = await _invoke_factory(factory, runtime, name=name)
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
    """

    all_entries: list[str | Path | ExtensionFactory] = []
    seen_paths: set[Path] = set()
    seen_ep: set[str] = set()
    errors: list[ExtensionLoadError] = []

    def _push_path(p: Path) -> None:
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved in seen_paths:
            return
        seen_paths.add(resolved)
        all_entries.append(p)

    # 1. Project-local: cwd/.aelix/extensions/
    local_dir = (cwd / ".aelix" / "extensions").resolve(strict=False)
    for discovered in _discover_in_dir(local_dir):
        _push_path(discovered)

    # 2. Global: ~/.aelix/extensions/  (or override via agent_dir)
    home_aelix = Path.home() / ".aelix" if agent_dir is None else agent_dir
    global_dir = (home_aelix / "extensions").resolve(strict=False)
    for discovered in _discover_in_dir(global_dir):
        _push_path(discovered)

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
            resolved = p if p.is_absolute() else (cwd / p)
            if resolved.is_dir():
                expanded = _discover_in_dir(resolved)
                if expanded:
                    for discovered in expanded:
                        _push_path(discovered)
                    continue
                # Directory exists but has no extension-shaped entries; fall
                # through to treat as a raw path (the inner loader will then
                # report a more useful "no setup()" style error).
            _push_path(resolved)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                ExtensionLoadError(path=str(entry), error=str(exc))
            )

    # 4. Aelix-additive: entry_points loaded LAST.
    for ep_entry, ep_error in _discover_via_entry_points(seen_ep):
        if ep_error is not None:
            errors.append(ep_error)
            continue
        if ep_entry is not None:
            all_entries.append(ep_entry)

    result = await load_extensions(all_entries, cwd=cwd)
    # Splice discovery-time errors in front of loader-time errors so the
    # caller sees them in the order they happened.
    result.errors = errors + result.errors
    return result


def _discover_in_dir(dir_path: Path) -> list[Path]:
    """Pi-parity ``discoverExtensionsInDir`` (``loader.ts:481-518``).

    For each entry in ``dir_path`` (non-recursive beyond one level):

    - ``*.py`` file → add directly.
    - Subdirectory: check ``pyproject.toml [tool.aelix] extensions=[...]``
      (Aelix port of Pi's ``package.json "pi.extensions"``) → use declared
      paths.  Else look for ``__init__.py`` (Aelix port of Pi's
      ``index.ts/index.js``) → treat the package as a single extension via
      its module path.  Else skip.
    """

    if not dir_path.exists() or not dir_path.is_dir():
        return []
    discovered: list[Path] = []
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
                declared = _resolve_extension_entries(child)
                if declared is not None:
                    discovered.extend(declared)
                # else: skip — no manifest, no __init__.py.
        except OSError:
            continue
    return discovered


def _resolve_extension_entries(pkg_dir: Path) -> list[Path] | None:
    """Pi-parity ``resolveExtensionEntries`` (``loader.ts:454-479``).

    Checks for:

    1. ``pyproject.toml`` with ``[tool.aelix] extensions = [...]`` array
       (Aelix mirror of Pi's ``package.json "pi.extensions"`` field).
    2. ``__init__.py`` (Aelix mirror of Pi's ``index.ts/index.js``).

    Returns ``None`` if neither is present (signal: skip this subdirectory).
    """

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
            entries: list[Path] = []
            for raw in declared:
                if not isinstance(raw, str):
                    continue
                resolved = (pkg_dir / raw).resolve(strict=False)
                if resolved.exists():
                    entries.append(resolved)
            if entries:
                return entries
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


async def _resolve_factory(
    entry: str | Path | ExtensionFactory,
    *,
    cwd: Path | None,
) -> tuple[ExtensionFactory, str]:
    """Return ``(factory, display_name)`` for a single loader entry."""

    # Check callable first; the isinstance guard is defensive because Path objects
    # are not callable, but the order matters: str/Path checks must come after so
    # that a callable class instance (e.g. PolicyExtension()) is handled here.
    if callable(entry) and not isinstance(entry, (str, Path)):
        # Inline factory — class instance or function.
        display = getattr(entry, "__qualname__", None) or type(entry).__name__
        return entry, display
    if isinstance(entry, Path):
        return _factory_from_file(entry, cwd=cwd), str(entry)
    if isinstance(entry, str):
        if entry.endswith(".py"):
            return _factory_from_file(Path(entry), cwd=cwd), entry
        return _factory_from_module(entry), entry
    raise TypeError(
        f"Unsupported extension entry type: {type(entry).__name__}"
    )


def _factory_from_module(module_path: str) -> ExtensionFactory:
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
) -> Extension:
    extension = Extension(name=name)
    api = ExtensionAPI(extension, runtime)
    result: Any = factory(api)
    if inspect.iscoroutine(result):
        await result
    return extension


__all__ = [
    "ExtensionLoadError",
    "LoadExtensionsResult",
    "discover_and_load_extensions",
    "load_extension_from_factory",
    "load_extensions",
]
