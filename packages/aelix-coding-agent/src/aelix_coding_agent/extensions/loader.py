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
- Other ``str`` → ``importlib.import_module`` (dotted module path, either
  bare — top-level ``setup`` — or ``module.path:callable``). Reachable from
  the CLI's ``-e`` since issue #114; see :func:`_is_module_ref`.
- Anything else is treated as a callable factory and invoked directly. Class
  instances with a ``__call__(self, aelix)`` (e.g. ``PolicyExtension()``) are
  valid factories per D.1.8.

Sprint 5a (Phase 3.1, ADR-0028 Accepted / ADR-0041): adds
:func:`discover_and_load_extensions` — a Pi-parity 3-tier directory scan
(project-local ``cwd/.aelix/extensions/``, global ``<agent_dir>/extensions/``
— ``~/.aelix/agent/extensions/`` in every shipped configuration, see the
``agent_dir`` note on that function — and explicit configured paths) PLUS an
Aelix-additive
``entry_points(group="aelix.extensions")`` pass. The directory scan is the
**primary** discovery channel (Pi parity); ``entry_points`` is layered on
LAST so installed packages cannot shadow project-local files (P-21
reversal of the original Draft ADR-0028).
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import importlib.util
import inspect
import logging
import os
import re
import sys
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aelix_agent_core.contracts import (
    AELIX_API_LEVEL,
    LICENSE_WHITELIST,
    McpServerContrib,
    PluginManifest,
    parse_manifest_toml,
)
from pydantic import ValidationError

from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    ExtensionFactory,
    PendingActivation,
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
        # Issue #21 descriptors (ADR-0184) — warn REGARDLESS of lazy/eager: the
        # [[contributes.descriptors]] slot is reserved/inert (a descriptor's
        # renderable payload is runtime data a static {kind,id} cannot carry),
        # so descriptors are emitted at runtime via the ui:list-modules probe
        # (ADR-0095). Emitted here — before the lazy ``continue`` below — so a
        # pure-``on_command`` plugin that also declares descriptors still sees
        # the guidance (the deferred branch would otherwise skip the warning).
        if isinstance(entry, _ManifestEntry) and entry.manifest.contributes.descriptors:
            logger.warning(
                "plugin %r declares [[contributes.descriptors]] which is "
                "reserved and inert; emit descriptors at runtime by "
                "subscribing to the ui:list-modules probe (ADR-0095/0184)",
                entry.manifest.plugin.id,
            )
        # Issue #21 — VS Code-style LAZY activation (ADR-0096 §Activation
        # policy, "lazy load is mandatory"): a manifest plugin whose ONLY
        # trigger is ``on_command`` is DEFERRED — no module import, no factory
        # run. An empty Extension shell (manifest attached) joins the loaded
        # set so the runner/banner/palette see it; the command-dispatch layer
        # activates it (import + factory + refresh_tools/refresh_hooks) when
        # one of its trigger commands fires. Any eager trigger
        # (on_startup_finished / on_session_start / on_tool_call — W1 treats
        # on_tool_call as eager) keeps today's load-time factory run.
        if isinstance(entry, _ManifestEntry) and _is_lazy_eligible(
            entry.manifest
        ):
            shell = Extension(
                name=entry.manifest.plugin.id, manifest=entry.manifest
            )
            runtime.pending_activations[entry.manifest.plugin.id] = (
                PendingActivation(extension=shell, entry=entry, cwd=cwd)
            )
            result.extensions.append(shell)
            continue
        try:
            factory, name, manifest = await _resolve_factory(entry, cwd=cwd)
        except Exception as exc:  # noqa: BLE001 — surface as load error
            # Issue #91 review: label a manifest entry by its plugin id (what
            # the sibling handler below already uses) rather than by the
            # carrier's repr. Both this string and ``error`` are printed to
            # stderr verbatim by cli/entry.py, so the label must stay short
            # and must never carry manifest payload.
            label = (
                entry.manifest.plugin.id
                if isinstance(entry, _ManifestEntry)
                else str(entry)
            )
            result.errors.append(
                ExtensionLoadError(path=label, error=str(exc))
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
        # Issue #21 themes (ADR-0184) — record the plugin directory so the
        # manifest theme adapter can resolve a ``ThemeContrib.path`` (a
        # plugin-root-relative file) against it. ``resolved_path`` was a
        # declared-but-unset Pi-parity field (api.py) until now.
        if isinstance(entry, _ManifestEntry):
            extension.resolved_path = str(entry.pkg_dir)
        # (descriptors inert-warning now emitted at the top of the loop, before
        # the lazy continue, so pure-on_command plugins are covered too.)
        result.extensions.append(extension)
    return result


def _is_lazy_eligible(manifest: PluginManifest) -> bool:
    """Issue #21 (W1): defer ONLY pure ``on_command`` plugins.

    Any eager trigger (``on_startup_finished`` / ``on_session_start`` /
    ``on_tool_call`` — W1 keeps ``on_tool_call`` eager because lazy tool
    stubs would need schema-carrying ToolContribs) forces the load-time
    factory run, preserving today's behavior for those plugins.
    """

    activation = manifest.activation
    return (
        bool(activation.on_command)
        and not (
            activation.on_startup_finished
            or activation.on_session_start
            or activation.on_tool_call
        )
        # Declared tools must be VISIBLE TO THE MODEL from startup — a
        # deferred plugin's tools would silently vanish until first command
        # use (adversarial-review MEDIUM) — so contributes.tools forces eager.
        and not manifest.contributes.tools
        # Declared subprocess hooks likewise: deferral would leave them inert
        # until first command AND move their shell_exec trust-gate error from
        # a visible load-time failure to a mid-session dispatch error.
        and not manifest.contributes.hooks
        # Declared TUI widgets (ADR-0182) paint at TUI start via the manifest
        # adapter (tui/ext_widgets.py), which only reads LOADED extensions —
        # deferral would leave them invisible until first command use with no
        # re-apply seam (the same silent-vanish class as contributes.tools).
        and not manifest.contributes.tui_widgets
        # Declared themes (ADR-0184) register at TUI start via the manifest
        # adapter (tui/ext_themes.py), which reads LOADED extensions' pkg_dir —
        # deferral would keep a plugin theme out of the /settings picker until
        # first command use (same silent-vanish class).
        and not manifest.contributes.themes
    )


async def activate_pending_extension(
    runtime: _ExtensionRuntime, name: str
) -> Extension | None:
    """Issue #21 — run a deferred manifest plugin's factory NOW (on_command).

    First code execution for the plugin: resolves ``[plugin.entry] python``
    (module import), runs the factory against the SAME shared runtime and the
    PRE-CREATED Extension shell (identity matters — the ExtensionRunner and
    the harness already hold that object), wires declarative subprocess
    hooks, then fires ``refresh_tools`` + ``refresh_hooks`` so the live
    harness registries see the late registrations. One-shot: the pending
    record is popped up front, so a FAILED activation does not retry on every
    keystroke — the error surfaces via the dispatch layer and the plugin
    stays inert until the next (re)build re-defers it.

    Returns the activated Extension, or ``None`` when nothing is pending
    under ``name``.
    """

    pending = runtime.pending_activations.pop(name, None)
    if pending is None:
        return None
    shell = pending.extension
    try:
        factory, _display, manifest = await _resolve_factory(
            pending.entry, cwd=pending.cwd
        )
        await _invoke_factory(
            factory,
            runtime,
            name=shell.name,
            manifest=manifest,
            extension=shell,
        )
    except BaseException:
        # ROLLBACK (adversarial-review HIGH): a factory that registered SOME
        # surfaces before raising must not leave a half-activated plugin —
        # its partial commands would execute on the next keystroke and its
        # partial hooks would arm on the next refresh. Clear every
        # registration surface on the SHARED shell (the runner/harness hold
        # this object), then re-run the refreshes to PURGE anything
        # register_tool's immediate refresh already pushed into the live
        # registries. The plugin ends fully inert (pending was popped —
        # one-shot); the error propagates to the dispatch layer.
        shell.handlers.clear()
        shell.handler_error_modes.clear()
        shell.tools.clear()
        shell.flags.clear()
        shell.cleanups.clear()
        shell.commands.clear()
        shell.shortcuts.clear()
        shell.message_renderers.clear()
        runtime.actions.refresh_tools()
        runtime.actions.refresh_hooks()
        raise
    # Late registrations reach the constructed harness through the two
    # refresh actions (no-ops when no harness is bound yet — tests/embedders).
    runtime.actions.refresh_tools()
    runtime.actions.refresh_hooks()
    if shell.shortcuts:
        # W1 limitation (adversarial-review MEDIUM): the TUI enumerates
        # shortcut KEYS once at chrome build, so keys from a lazily-activated
        # factory cannot bind until the next TUI start. Say so loudly.
        logger.warning(
            "plugin %r registered %d shortcut(s) during lazy activation; "
            "they will bind on the next TUI start (key bindings are built "
            "once at startup)",
            shell.name,
            len(shell.shortcuts),
        )
    return shell


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
    2. ``<agent_dir> / extensions /`` — user globals. **In every shipped
       configuration this is ``~/.aelix/agent/extensions/``**, not
       ``~/.aelix/extensions/``: both production call sites
       (``cli/entry.py:925`` and ``:1534``) pass
       ``agent_dir=Path(get_agent_dir())``, and ``get_agent_dir``
       (``cli/config.py:82-92``) returns ``$AELIX_CODING_AGENT_DIR`` or
       ``~/.aelix/agent``. The ``agent_dir=None`` default below falls back to
       ``~/.aelix/extensions``, but no shipped caller takes it — it exists for
       tests and direct library use. Naming the fallback as if it were the
       user-facing directory is how a "write your extension to
       ``~/.aelix/extensions``" instruction gets written and silently loads
       nothing.
    3. ``configured_paths`` — explicit entries provided by the caller (the
       CLI's ``-e``). A directory entry is expanded via
       :func:`_discover_in_dir`; an entry resolving to a directory with a
       ``pyproject.toml [tool.aelix] extensions = [...]`` manifest uses the
       declared list. A string that is NOT path-shaped is treated as a dotted
       module reference — ``pkg.mod`` (top-level ``setup``) or
       ``pkg.mod:factory`` — and imported rather than opened (issue #114; see
       :func:`_is_module_ref` for the classification, in which anything
       present at the corresponding path beats the module reading, and the
       ambiguity is warned about rather than resolved silently). The import
       deliberately excludes the project directory from ``sys.path`` — see
       :func:`_import_path_without_cwd`.
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

    all_entries, errors = _discover_entries(
        configured_paths,
        cwd=cwd,
        agent_dir=agent_dir,
        prepend=prepend,
        no_discovery=no_discovery,
        no_project_local=no_project_local,
    )
    result = await load_extensions(all_entries, cwd=cwd, flag_values=flag_values)
    # Splice discovery-time errors in front of loader-time errors so the
    # caller sees them in the order they happened.
    result.errors = errors + result.errors
    return result


# === Issue #114 — configured-entry classification (path vs dotted module) ===

_DOTTED_MODULE_RE = re.compile(
    r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z"
)
"""A bare importable module reference: ``pkg``, ``pkg.sub.mod``.

Anchored with ``\\Z``, not ``$``: ``$`` also matches immediately *before* a
trailing newline, so ``"justaname\\n"`` (a config- or file-sourced entry with
trailing whitespace) would classify as a module and get its newline spliced
into the middle of the error message. Not reachable from a shell argv, but
free to close.
"""

_MODULE_CALLABLE_RE = re.compile(
    r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r":[A-Za-z_][A-Za-z0-9_]*\Z"
)
"""The ``module.path:callable`` form, matching :func:`_factory_from_module`.

Deliberately stricter than ``PluginEntry.python``'s ``^[\\w.]+:\\w+$`` at the
head: a *leading* digit (``9x:setup``) is not an identifier, so it is left to
the path branch rather than being handed to ``import_module`` to fail on.
``\\Z`` rather than ``$`` for the same reason as :data:`_DOTTED_MODULE_RE`.
"""

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
"""A Windows drive-qualified path (``C:\\ext``, ``C:/ext``).

Checked BEFORE the colon test so a drive letter is never mistaken for the
``module:callable`` separator. Ordering matters and is asserted by
``test_windows_drive_letter_is_a_path_not_module_callable``: on POSIX
``os.sep`` is ``/`` and ``os.altsep`` is ``None``, so ``C:\\ext`` contains NO
separator this platform recognises and clause 3 does NOT fire for it — the
plan's "clause 3 excludes drive letters" assumption holds on Windows only.
This explicit drive test makes the exclusion platform-independent. It requires
a following separator, so a genuine one-letter module in colon form
(``c:setup``) still classifies as a module.
"""

_WINDOWS_DRIVE_RELATIVE_RE = re.compile(r"^[A-Za-z]:")
"""A bare drive qualifier (``C:ext`` — "``ext`` on drive C's current dir").

Applied **only when** ``os.name == "nt"``. It overlaps the legitimate
one-letter ``module:callable`` form (``c:setup``), so paying for it everywhere
would cost a real input on the platform where drive-relative paths cannot
occur. On Windows the path reading wins the overlap: drive-relative paths are
a native (if discouraged) spelling, whereas a one-letter module is vanishingly
rare and still reachable as ``c.mod:setup`` or via the library API. The repo
has open Windows-portability issues; this keeps the change from adding one.
"""

_OS_NAME = os.name
"""The platform, read through a module-level seam so tests can override it.

Patching ``os.name`` itself would work, but ``loader.os is os`` — it is the
stdlib module object, and ``pathlib`` dispatches ``Path()`` to ``WindowsPath``
off ``os.name``. A test that flipped it process-wide would make every
``Path`` in the interpreter a ``WindowsPath`` for the duration, including any
built by pytest's own failure reporting if an assertion inside the window
failed. This seam confines the override to this module.
"""


def _is_module_shaped(entry: str) -> bool:
    """Does ``entry`` have the *syntax* of a module reference, ignoring disk?

    :func:`_is_module_ref` answers "should this be IMPORTED", which folds in
    the filesystem. This answers only "does it look like a module", which is
    what the tier-3 ambiguity warning needs: an entry that is module-shaped
    yet claimed by the on-disk clause is exactly the case where the visited
    directory silently decides what a user-typed name means.
    """

    if not entry or entry.endswith(".py"):
        return False
    if _WINDOWS_DRIVE_RE.match(entry):
        return False
    if _OS_NAME == "nt" and _WINDOWS_DRIVE_RELATIVE_RE.match(entry):
        return False
    seps = {"\\", "/", os.sep}
    if os.altsep:
        seps.add(os.altsep)
    if any(sep in entry for sep in seps):
        return False
    if ":" in entry:
        return bool(_MODULE_CALLABLE_RE.match(entry))
    return bool(_DOTTED_MODULE_RE.match(entry))


def _is_module_ref(entry: str, *, cwd: Path) -> bool:
    """Issue #114 — is this configured ``-e`` string a dotted module, not a path?

    ``False`` means "treat as a filesystem path", which is what EVERY string
    got before this function existed. The predicate therefore only ever moves
    a string from path to module, and only when no path interpretation can
    work — so it can add working inputs but cannot break one that worked.

    Clause order (first match wins):

    1. ``.py`` suffix → path, always (mirrors :func:`_resolve_factory`).
    2. A Windows drive prefix (``C:\\…`` / ``C:/…``, plus bare ``C:…`` when
       running ON Windows) → path (see :data:`_WINDOWS_DRIVE_RE` and
       :data:`_WINDOWS_DRIVE_RELATIVE_RE`; must precede the colon clause).
    3. Contains a path separator — ``\\`` on any platform, plus ``os.sep`` /
       ``os.altsep`` — → path. A dotted module reference never contains one.
    4. Exists on disk (absolute, or relative to ``cwd``) → path. **An existing
       path always wins**, matching :func:`~aelix_coding_agent.cli.
       extension_install.classify_target` ("a local path WINS if it exists").
       Tested with ``os.path.lexists``, not ``Path.exists``: the latter
       follows symlinks and reports ``False`` for a dangling or looping one,
       which would hand a path the user demonstrably created to the module
       branch and replace an accurate filesystem diagnostic ("Symlink loop
       from …") with a misleading "no module named".
    5. Contains ``:`` → module only when it is a well-formed
       ``module.path:callable``; otherwise → path (clause 7).
    6. A well-formed dotted identifier → module.
    7. Anything else → path, preserving today's behaviour and today's error
       message for inputs we cannot confidently classify.

    **Clause 4 is not free, and it is not silent.** It hands the visited
    directory the power to decide whether ``-e acme`` means "import the
    user's installed extension" or "load this project's ``acme/``". That
    ambiguity predates #114 for the path half (the coercion always expanded
    an on-disk directory), but #114 *creates the spelling*, so the tier-3
    loop in :func:`_discover_entries` logs a warning naming both readings
    whenever clause 4 is the only reason a module-shaped string was called a
    path. The clause itself stays because dropping it would regress the
    plan's acceptance case — ``-e mypkg.ext`` with ``mypkg.ext/`` on disk
    loaded from disk before #114 and must keep doing so.

    **The one input whose ERROR changes: a dotless bare name.** ``justaname``
    has no separator, no ``.py``, and (clause 4 having already declined) does
    not exist on disk, so clause 6 reads it as a top-level module — which is
    the point, since ``-e my_installed_ext`` is exactly the input #114 is
    about. A user who meant a path now gets a "no module named" failure
    instead of "Extension file not found". No *working* input moves: a bare
    name that resolves on disk is claimed by clause 4, and a bare name that
    resolves to nothing failed before and fails now. Only the message moves,
    so :func:`_resolve_factory` names BOTH readings when a module reference
    turns out not to be importable. The alternative — importing first and
    falling back to the path reading on ``ModuleNotFoundError`` — was
    rejected: a module that raises ``ModuleNotFoundError`` from *inside* its
    own imports (a missing third-party dependency) would then be misreported
    as a missing file, which is strictly worse diagnostics than the case it
    fixes.
    """

    if not _is_module_shaped(entry):
        return False
    try:
        candidate = Path(entry)
        resolved = candidate if candidate.is_absolute() else (cwd / candidate)
        if os.path.lexists(resolved):
            return False
    except (OSError, ValueError):
        # Unrepresentable as a path (embedded NUL, name too long, …) — keep
        # the historical path branch so the historical error is produced.
        return False
    return True


def _discover_entries(
    configured_paths: list[str | Path | ExtensionFactory],
    *,
    cwd: Path,
    agent_dir: Path | None = None,
    prepend: list[ExtensionFactory] | None = None,
    no_discovery: bool = False,
    no_project_local: bool = False,
    include_entry_points: bool = True,
) -> tuple[
    list[str | Path | ExtensionFactory | _ManifestEntry],
    list[ExtensionLoadError],
]:
    """The 4-tier discovery pass of :func:`discover_and_load_extensions`.

    Pure metadata: walks the tiers, parses ``aelix-plugin.toml`` manifests
    into ``_ManifestEntry`` carriers, and NEVER imports or executes plugin
    code (factory invocation happens later in :func:`load_extensions`).
    Extracted (issue #21) so :func:`scan_extension_manifests` can reuse the
    identical discovery + trust gating without the execution half.

    Tier 3 entries are classified by :func:`_is_module_ref` (issue #114): a
    dotted module reference stays a ``str`` (so :func:`_resolve_factory`
    imports it), everything else is coerced to ``Path`` exactly as before.
    ``str`` therefore appears in the returned list; :func:`load_extensions`
    resolves it, and :func:`scan_extension_manifests` ignores it (a module
    reference carries no ``aelix-plugin.toml``).
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

        # 2. Global: <agent_dir>/extensions/ — i.e. ~/.aelix/agent/extensions/
        # for both production callers, which pass agent_dir=get_agent_dir().
        # The `agent_dir is None` branch is the library/test fallback and is
        # the ONLY way ~/.aelix/extensions is ever scanned; do not describe it
        # as the user's global extension directory.
        home_aelix = Path.home() / ".aelix" if agent_dir is None else agent_dir
        global_dir = (home_aelix / "extensions").resolve(strict=False)
        for discovered in _discover_in_dir(global_dir, errors=errors):
            _push_entry(discovered)

    # 3. Explicit configured paths.
    seen_modules: set[str] = set()
    for entry in configured_paths:
        # Callables/factories pass through (P-21 — explicit takes precedence
        # over entry_points but loses to local/global directories).
        if callable(entry) and not isinstance(entry, (str, Path)):
            all_entries.append(entry)
            continue
        # Issue #114: a dotted module reference ("pkg.mod", "pkg.mod:factory")
        # passes through AS A STRING so _resolve_factory reaches
        # _factory_from_module. Coercing it to Path here (as this loop did
        # unconditionally) made that branch unreachable from the CLI, so
        # `-e pkg.mod` failed with "Extension file not found: <cwd>/pkg.mod"
        # while load_extensions(["pkg.mod"]) loaded the very same string —
        # a CLI/library divergence, not merely a missing feature.
        # Deduped by the literal string, mirroring the Path.resolve() dedupe
        # below (before #114 a repeated module string deduped as a repeated
        # cwd-relative path; keep that).
        if isinstance(entry, str) and _is_module_ref(entry, cwd=cwd):
            if entry in seen_modules:
                continue
            seen_modules.add(entry)
            all_entries.append(entry)
            continue
        # Issue #114 fold-in: the entry LOOKS like a module but something on
        # disk claimed it (clause 4). That is deliberate — a path that exists
        # wins, so `-e mypkg.ext` with `mypkg.ext/` present keeps loading from
        # disk exactly as it did before #114 — but it means the visited
        # directory, not the user, decided what the name means, and the code
        # about to run is project-local and outside the `no_project_local`
        # trust gate. Do not let that happen silently.
        if isinstance(entry, str) and _is_module_shaped(entry):
            logger.warning(
                "extension entry %r looks like a module reference, but %s "
                "exists and takes precedence, so PROJECT-LOCAL code will be "
                "loaded instead of the installed module. Pass './%s' to "
                "confirm you meant the local path, or run from another "
                "directory to load the module.",
                entry,
                Path(entry) if Path(entry).is_absolute() else (cwd / entry),
                entry,
            )
            # NO ``continue`` — the warning is advisory and the entry falls
            # through to the path handling below, which is what "an existing
            # path always wins" means. Swallowing it here would regress the
            # plan's acceptance case `-e mypkg.ext that exists on disk`.
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
    # ``include_entry_points=False`` (the manifest SCAN): ``ep.load()`` below
    # IMPORTS the endpoint module — running it would violate the scan's
    # metadata-only contract (adversarial-review finding), and entry_points
    # yield factories, never manifests, so the scan loses nothing by skipping.
    if not no_discovery and include_entry_points:
        for ep_entry, ep_error in _discover_via_entry_points(seen_ep):
            if ep_error is not None:
                errors.append(ep_error)
                continue
            if ep_entry is not None:
                all_entries.append(ep_entry)

    return all_entries, errors


def scan_extension_manifests(
    configured_paths: list[str | Path | ExtensionFactory],
    *,
    cwd: Path,
    agent_dir: Path | None = None,
    no_discovery: bool = False,
    no_project_local: bool = False,
) -> list[PluginManifest]:
    """Issue #21 — metadata-ONLY manifest scan (no plugin code execution).

    Runs the SAME 4-tier discovery + Project-Trust gating as
    :func:`discover_and_load_extensions` but stops at manifest parsing:
    plugin modules are never imported and factories never run, so it is
    safe to call before trust-sensitive wiring. Used by the CLI to merge
    ``contributes.mcp_servers`` into the MCP connect list, which happens
    BEFORE the first harness build (where the full extension load runs).

    Entries without a manifest (bare ``.py`` extensions, inline factories)
    contribute nothing here — only ``aelix-plugin.toml`` carriers are
    surfaced. The entry_points tier is EXCLUDED entirely: resolving an
    endpoint (``ep.load()``) is a module import, which would break the
    metadata-only contract — and endpoints yield factories, never manifests.
    Discovery errors (e.g. a malformed manifest) are logged as warnings so a
    plugin whose declared MCP servers were silently skipped is diagnosable;
    the full loader re-reports them properly at load time.

    SECURITY — the returned manifests are UNGATED metadata. Do NOT feed
    ``manifest.contributes.mcp_servers`` straight to the MCP client: a stdio
    server is a subprocess spawn and an http/sse server is an outbound
    connection, both attacker-chosen. Route them through
    :func:`gate_manifest_mcp_contribs` first (issue #91).
    """

    entries, errors = _discover_entries(
        configured_paths,
        cwd=cwd,
        agent_dir=agent_dir,
        no_discovery=no_discovery,
        no_project_local=no_project_local,
        include_entry_points=False,
    )
    for err in errors:
        logger.warning("manifest scan: %s: %s", err.path, err.error)
    return [e.manifest for e in entries if isinstance(e, _ManifestEntry)]


_MCP_TRANSPORT_CAPABILITY: Mapping[str, str] = {
    # A stdio server is ``command`` + ``args`` exec'd as a child process with
    # the host's environment — the SAME primitive ``contributes.hooks`` is
    # gated on, so it takes the SAME flag.
    "stdio": "shell_exec",
    # http/sse spawn nothing; they dial a plugin-chosen URL. Different
    # primitive, different flag.
    "http": "net",
    "sse": "net",
}
"""Which ``[capabilities]`` flag each MCP transport requires (issue #91).

Why not ``mcp_serve`` / ``mcp_invoke``, the two flags whose NAMES look like a
match — the vocabulary is genuinely ambiguous here, so the reasoning is
recorded rather than assumed:

* ``mcp_serve`` is documented as "plugin runs as MCP server" (ADR-0096 §schema)
  / "exposes its own MCP server" (the ``examples/echo`` template). It is about
  the plugin BEING a server, and :class:`PluginManifest` proves it: declaring
  ``mcp_serve`` forces ``[plugin.entry] python``, because the host has to load
  the plugin's Python to run that server. ``[[contributes.mcp_servers]]`` is
  the opposite direction — pure config telling the host to connect OUT to
  someone else's server, no plugin code required. Gating on ``mcp_serve``
  would force every config-only pack to invent an entry module, i.e. to ship
  importable code the loader then imports: a security REGRESSION.
  ADR-0094's tier table does map ``mcp_serve`` to the same T4 row, which is
  the ambiguity; the schema's entry-point coupling breaks the tie.
* ``mcp_invoke`` is "plugin calls MCP servers the host has connected" — the
  plugin-API direction (ADR-0101 defers exactly that enforcement). It says
  nothing about who may cause a connection to exist.

So the gate follows the primitive actually exercised, not the noun in the
flag name — which also keeps ONE mental model with the hooks gate: the host
does not spawn a subprocess for a plugin that did not ask for ``shell_exec``.
"""


def gate_manifest_mcp_contribs(
    manifests: Iterable[PluginManifest],
) -> tuple[list[McpServerContrib], list[str], list[str]]:
    """Split manifest-declared MCP servers into allowed / notices / refusals.

    Issue #91. ``contributes.mcp_servers`` is consumed at CLI startup, before
    the first harness build — so :func:`_enforce_declarative_capability_gates`
    (a LOAD-time gate) structurally cannot reach it, and until this function
    existed the family had NO gate at all: a manifest with no ``[capabilities]``
    table and no ``[plugin.entry] python`` — the most auditable-looking pack
    it is possible to write — got ``command``/``args`` exec'd with
    ``{**os.environ, **env}`` on the next start, the only trace a benign MCP
    connect warning. Measured, not theorised.

    The refusal is returned rather than raised: one denied server must not
    abort the others, matching :meth:`McpClientManager.connect_all`'s
    one-bad-server-never-aborts-the-rest contract.

    BOTH outcomes are reported, and the symmetry is deliberate. A gate that
    only speaks when it refuses makes the ALLOW path invisible, and the allow
    path is where the damage is: capabilities are per-manifest, not per-family,
    so a pack the user installed for its ``tool_call`` hook — a use that
    genuinely justifies ``shell_exec`` — gets a stdio ``[[contributes.
    mcp_servers]]`` spawn in the same manifest for free, unannounced, every
    start. Nothing else in the product ever shows a user a capability flag
    (neither ``/extension``, nor the installer, nor the catalog), so this
    notice is the only place a granted spawn becomes observable. Notices are
    per-server prose in the same leak-safe shape as refusals.

    Messages name the plugin id, the server name and the transport ONLY.
    ``McpServerContrib.env`` holds plugin-supplied API tokens and is NEVER
    interpolated (same leak class as the ``_ManifestEntry`` repr fix). Both
    identifiers go through ``!r``: ``name`` is a free-form attacker-chosen
    string, and repr escapes newlines/ANSI so neither message can forge extra
    terminal lines.

    Returns:
        ``(allowed, notices, refusals)`` — ``allowed`` preserves the caller's
        manifest order (which encodes MCP name-collision precedence);
        ``notices`` is parallel to it, one line per allowed server.
    """

    allowed: list[McpServerContrib] = []
    notices: list[str] = []
    refusals: list[str] = []
    for manifest in manifests:
        caps = manifest.capabilities
        for contrib in manifest.contributes.mcp_servers:
            required = _MCP_TRANSPORT_CAPABILITY.get(contrib.transport)
            if required is None:  # pragma: no cover — Literal-constrained
                refusals.append(
                    f"plugin {manifest.plugin.id!r} declares MCP server "
                    f"{contrib.name!r} with unknown transport "
                    f"{contrib.transport!r}; not started"
                )
                continue
            if getattr(caps, required, False):
                allowed.append(contrib)
                notices.append(
                    f"plugin {manifest.plugin.id!r} starts MCP server "
                    f"{contrib.name!r} (transport={contrib.transport}, "
                    f"capabilities.{required}=true)"
                )
                continue
            detail = (
                "a stdio MCP server is a subprocess the host spawns"
                if contrib.transport == "stdio"
                else "an MCP server over "
                f"{contrib.transport} is an outbound network connection "
                "the host opens"
            )
            refusals.append(
                f"plugin {manifest.plugin.id!r} declares MCP server "
                f"{contrib.name!r} (transport={contrib.transport}) but "
                f"capabilities.{required} is false; {detail}, so it requires "
                f"{required}=true. Server NOT started."
            )
    return allowed, notices, refusals


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


@dataclass(frozen=True, repr=False)
class _ManifestEntry:
    """Internal carrier for manifest-discovered extensions (Sprint 6h₉b §B).

    A ``_ManifestEntry`` flows through ``load_extensions`` like a Path,
    but carries the parsed manifest + the plugin directory so the inner
    factory resolver can use ``[plugin.entry] python = "module:callable"``
    instead of falling back to the directory's ``setup`` convention.

    ``repr`` is REDACTED on purpose (issue #91 review): this object flows
    into ``str(entry)`` on the load-error path, and the default dataclass
    repr expands the whole ``PluginManifest`` — including
    ``contributes.mcp_servers[].env``, which holds plugin-supplied secrets
    (API tokens). A load warning must never dump a token to stderr or into
    a pasted bug report, so the repr carries the identity only.

    NOT exported.
    """

    manifest: PluginManifest
    pkg_dir: Path

    def __repr__(self) -> str:
        return (
            f"_ManifestEntry(plugin={self.manifest.plugin.id!r}, "
            f"pkg_dir={str(self.pkg_dir)!r})"
        )


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


def _enforce_declarative_capability_gates(manifest: PluginManifest) -> None:
    """Raise when a declared ``contributes.*`` family lacks its capability.

    Issue #91. Every declarative contribution whose payload is *executed*
    (plugin code in the user's terminal, a subprocess) is gated on an opt-in
    ``[capabilities]`` flag. The gates are collected HERE, in one function,
    so the class has exactly one shape and the NEXT ``contributes.*`` family
    cannot be added with its gate wired into the wrong phase — which is
    precisely the bug this function closes: ``tui_widgets``/``ui_tui_trusted``
    had been hoisted ahead of the entry-module import while
    ``hooks``/``shell_exec`` was left behind in :func:`_invoke_factory`, so a
    denied hooks plugin had its module imported and its ``setup()`` run before
    being refused.

    Called from TWO places on purpose (defense in depth):

    * :func:`_resolve_factory`'s ``_ManifestEntry`` branch — BEFORE
      :func:`_factory_from_module` imports anything, so a denied plugin
      executes NO code at all (data before code).
    * :func:`_invoke_factory` — before the factory runs, covering manifests
      that reach it by a route which skipped entry resolution (the hooks-only
      ``_noop_factory`` short-circuit does exactly that, and any future
      direct caller threading a manifest would too) and guarding the wiring
      step itself.

    SCOPE — what "executes NO code at all" does and does not cover
    (measured, issue #91 review; do not read the sentence above as absolute):

    * It holds on the MANIFEST discovery tiers, which is where a
      ``_ManifestEntry`` comes from.
    * It does NOT hold on the ``entry_points(group="aelix.extensions")``
      tier: :func:`_discover_via_entry_points` calls ``ep.load()`` during
      discovery — importing the plugin BEFORE any gate — and yields a bare
      factory, so ``manifest`` is ``None`` and this function never runs for
      it, even when the installed dist ships an ``aelix-plugin.toml``. That
      is a discovery gap, tracked separately; whoever closes it must read
      the manifest before ``ep.load()``, not after.
    * It does NOT cover ``contributes.mcp_servers``, and cannot: those are
      consumed in ``cli/entry.py`` BEFORE the first harness build, so no
      load-time gate is reachable in time. That family is gated at its own
      seam by :func:`gate_manifest_mcp_contribs`, which returns refusals
      instead of raising (one denied server must not kill the pack). The
      asymmetry is deliberate — recorded here so "the gates all live in this
      function" is not read as "mcp_servers has no gate".
    * It is a LOAD-TIME check on declared data. It is not a sandbox: once
      ``factory(api)`` runs, the plugin can reach its own (mutable,
      non-frozen) :class:`Capabilities` through ``api`` and self-grant.
      Post-``setup()`` manifest state is not a trust boundary.

    The message text and exception type are USER-FACING and asserted by
    tests: they must stay byte-identical. Widget gate is checked first,
    preserving the pre-existing precedence when a manifest trips both.
    """

    # Issue #21 tui_widgets (ADR-0182): a declared widget's ``factory`` is
    # plugin code executed in the user's terminal.
    if manifest.contributes.tui_widgets and not manifest.capabilities.ui_tui_trusted:
        raise ExtensionManifestError(
            f"plugin {manifest.plugin.id!r} declares [[contributes.tui_widgets]] "
            f"but capabilities.ui_tui_trusted is false; declarative TUI widgets "
            f"require ui_tui_trusted=true"
        )
    # Sprint 6h₉e (Tier 4b, ADR-0102): a declared hook spawns a subprocess.
    if manifest.contributes.hooks and not manifest.capabilities.shell_exec:
        raise ExtensionManifestError(
            f"plugin {manifest.plugin.id!r} declares [[contributes.hooks]] "
            f"but capabilities.shell_exec is false; subprocess hooks "
            f"require shell_exec=true"
        )


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
        # Declarative trust gates (tui_widgets/ui_tui_trusted — ADR-0182;
        # hooks/shell_exec — ADR-0102). Fire HERE, before
        # ``_factory_from_module`` imports the entry module, so a denied
        # plugin executes NO code at all (data before code — review MEDIUM:
        # the widget gate originally sat in _invoke_factory, after the import,
        # and issue #91 found the hooks gate still sitting there).
        _enforce_declarative_capability_gates(entry.manifest)
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
        # Issue #114 security fold-in: resolve the module WITHOUT the project
        # directory on ``sys.path``. See :func:`_import_path_without_cwd`.
        shadow = _cwd_shadow_candidate(entry, cwd)
        try:
            with _import_path_without_cwd(cwd):
                return _factory_from_module(entry), entry, None
        except ModuleNotFoundError as exc:
            # Issue #114: this branch became reachable from ``-e``, so a
            # dotless bare name that used to fail as "Extension file not
            # found" now fails as "no module named". Name BOTH readings
            # rather than silently swapping one dead end for another.
            if shadow is not None and _names_the_reference(entry, exc):
                raise _cwd_shadowed_module_error(
                    entry, exc, shadow, cwd
                ) from exc
            raise _unresolvable_module_ref_error(entry, exc, cwd=cwd) from exc
    raise TypeError(
        f"Unsupported extension entry type: {type(entry).__name__}"
    )


def _import_path_without_cwd(cwd: Path | None) -> AbstractContextManager[None]:
    """Issue #114 — take the project directory off ``sys.path`` for an import.

    ``-e <module>`` became reachable in #114, and ``importlib.import_module``
    resolves a name against the ambient ``sys.path``. Under ``python -m
    aelix_coding_agent`` — which is not exotic here: it is verbatim the
    command :mod:`aelix_agents.print_channel` uses to spawn EVERY subagent,
    with the child's cwd — ``sys.path[0]`` is the current directory. Without
    this guard, ``-e acme`` run inside a cloned repo that happens to contain
    ``acme.py`` imports the REPO's file and executes its top-level code, while
    the user's genuinely installed ``acme`` never loads. Nothing prevented it:
    :func:`_is_module_ref` clause 4 tests ``<cwd>/acme`` (no ``.py``) and so
    does not fire, tier-3 explicit entries are loaded outside the
    ``no_project_local`` trust gate, and ``--no-approve`` is not consulted.

    ``-e <module>`` means "the module this Aelix installation can import", so
    the project directory has no business in that lookup; a project-local
    extension has its own gated channel (``./.aelix/extensions/``) and its own
    spelling (``-e ./file.py``). Blocking the directory rather than inspecting
    the winning spec also closes the ``find_spec`` hole, in which resolving
    ``a.b`` imports ``a`` — executing a hostile ``a/__init__.py`` during the
    very check meant to detect it.

    Removed entries are re-inserted at their original indices rather than by
    assigning a saved list back, so a ``sys.path`` mutation made by the
    extension while it imports survives. ``sys.path`` is process-global and
    this is not thread-safe; extension loading is a sequential startup step.
    """

    blocked: set[str] = {""}
    with contextlib.suppress(OSError):  # cwd unlinked underneath us
        blocked.add(os.getcwd())
    if cwd is not None:
        blocked.add(str(cwd))
        with contextlib.suppress(OSError):  # unresolvable cwd
            blocked.add(str(cwd.resolve()))

    removed = [(i, p) for i, p in enumerate(sys.path) if p in blocked]

    @contextlib.contextmanager
    def _guard() -> Iterator[None]:
        if not removed:
            yield
            return
        for _, value in removed:
            sys.path.remove(value)
        try:
            yield
        finally:
            for index, value in removed:
                sys.path.insert(index, value)

    return _guard()


def _cwd_shadow_candidate(entry: str, cwd: Path | None) -> Path | None:
    """The project-local file ``entry`` WOULD have imported, if any.

    Purely a filesystem test — it imports nothing, which is the point: it runs
    only to explain a failure, and must not execute the very code
    :func:`_import_path_without_cwd` just refused to run.
    """

    if cwd is None:
        return None
    head = entry.partition(":")[0].split(".")[0]
    if not head:
        return None
    for candidate in (cwd / f"{head}.py", cwd / head / "__init__.py"):
        try:
            if candidate.is_file():
                return candidate
        except OSError:  # pragma: no cover — unreadable directory
            return None
    return None


def _names_the_reference(entry: str, exc: ModuleNotFoundError) -> bool:
    """Is ``exc`` about ``entry`` itself, not a dependency it tried to import?

    Shared by both rewrites below. A module whose own ``import`` of a missing
    third-party package fails raises ``ModuleNotFoundError`` too; blaming the
    user's spelling for that is strictly worse diagnostics than saying
    nothing.
    """

    head_parts = entry.partition(":")[0].split(".")
    prefixes = {".".join(head_parts[: i + 1]) for i in range(len(head_parts))}
    return exc.name in prefixes


def _cwd_shadowed_module_error(
    entry: str, exc: ModuleNotFoundError, shadow: Path, cwd: Path | None
) -> ModuleNotFoundError:
    """Explain a module reference that ONLY the project directory could satisfy.

    Silence here would be the worst outcome: the user asked for a module, a
    file with that name is sitting right there, and the only reason it did not
    load is a deliberate refusal. Say so, and name the spelling that does what
    they meant.
    """

    spelling = str(shadow)
    if cwd is not None:
        # shadow is always built from cwd, so this cannot miss in practice.
        with contextlib.suppress(ValueError):
            spelling = f"./{shadow.relative_to(cwd).as_posix()}"
    return ModuleNotFoundError(
        f"{exc}. {shadow} would have satisfied {entry!r}, but Aelix does not "
        f"import extensions from the project directory: '-e <module>' "
        f"resolves against the installed environment only, so that a "
        f"repository cannot decide what a module reference means. Pass "
        f"'-e {spelling}' to load that file on purpose, put it under "
        f"'./.aelix/extensions/' to go through the Project Trust gate, or "
        f"install the package to import it by name.",
        name=exc.name,
        path=exc.path,
    )


def _unresolvable_module_ref_error(
    entry: str, exc: ModuleNotFoundError, *, cwd: Path | None
) -> ModuleNotFoundError:
    """Issue #114 — restate a failed module import as the ambiguity it is.

    Returns ``exc`` UNCHANGED unless every clause of the replacement message
    is true of this call:

    * the thing Python could not find is the reference itself (``exc.name`` is
      ``entry`` or a dotted prefix of it). A module which imports a missing
      third-party package raises ``ModuleNotFoundError`` too, and rewriting
      *that* into "…and no file at…" would blame the user's spelling for a
      missing dependency.
    * ``entry`` really is the ambiguous case — module-shaped, and with nothing
      at the corresponding path. **This is re-checked here rather than assumed
      from the call site**, because the public :func:`load_extensions` reaches
      this ``str`` branch WITHOUT ever consulting :func:`_is_module_ref`:
      ``load_extensions(["d"])`` with a real ``cwd/d`` directory would
      otherwise be told "nothing exists at that path" about a directory that
      does, and ``load_extensions(["d/"])`` would be told it contains "no path
      separator" about a string that visibly does.

    Only the bare form is ambiguous — ``pkg.mod:factory`` cannot be a path the
    user meant, so it keeps the plain error.
    """

    if ":" in entry:
        return exc
    if not _names_the_reference(entry, exc):
        return exc
    if not _is_module_ref(entry, cwd=cwd or Path.cwd()):
        # Path-shaped, or something IS on disk at that name: the rationale
        # below would be false, so say nothing rather than say it wrongly.
        return exc
    attempted = Path(entry)
    if not attempted.is_absolute():
        attempted = (cwd or Path.cwd()) / attempted
    rebuilt = ModuleNotFoundError(
        f"{exc}; and no extension file at {attempted}. Aelix read "
        f"{entry!r} as a dotted module reference because it is not "
        f"path-shaped (no path separator, no '.py' suffix, and nothing "
        f"exists at that path). Pass './{entry}.py' or an absolute path "
        f"for a file, or make the module importable for a module.",
        name=exc.name,
        path=exc.path,
    )
    return rebuilt


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
    extension: Extension | None = None,
) -> Extension:
    """Sprint 6h₉b §C: propagate ``manifest`` to the loaded :class:`Extension`.

    Legacy callers (``load_extension_from_factory``, tests that bypass
    the discovery pipeline) pass ``manifest=None`` and the resulting
    ``Extension.manifest`` stays ``None``; manifest-discovered plugins
    get their parsed :class:`PluginManifest` attached so Sprint 6h₉c/d/
    e/f consumers can read declared capabilities / activation /
    contributes.

    ``extension`` (issue #21): a LAZY activation passes the pre-created
    shell so the factory populates the SAME object the runner/harness
    already hold; eager paths leave it ``None`` and a fresh Extension is
    built here as before.
    """

    if extension is None:
        extension = Extension(name=name, manifest=manifest)

    # Defensive second fence for the declarative trust gates (tui_widgets /
    # hooks). The PRIMARY gate lives in _resolve_factory, before the
    # entry-module import — data before code. This one covers manifests that
    # reach _invoke_factory WITHOUT passing that branch: today that is the
    # hooks-only ``_noop_factory`` short-circuit (no ``[plugin.entry]
    # python`` → _resolve_factory returns before its gate call), and it would
    # cover any future direct caller threading a manifest. Placed before
    # ``factory(api)`` so a denied plugin's setup() does not run either.
    if manifest is not None:
        _enforce_declarative_capability_gates(manifest)

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
        # THIRD fence, kept deliberately (issue #91) even though
        # _enforce_declarative_capability_gates already ran twice above: it is
        # the last statement before the wiring loop, so it is the only copy
        # that sees the manifest as it stands AFTER ``factory(api)`` — a
        # plugin declaring no hooks passes both earlier gates and can append a
        # hook contrib from setup(); this catches that.
        #
        # It is NOT a trust boundary, and this comment must not claim to be
        # one (measured, issue #91 review): ``Capabilities`` is a non-frozen
        # pydantic model reachable through the same ``api``, so a setup() that
        # ALSO flips ``shell_exec = True`` passes all three fences and wires
        # its hook. Stopping that needs a snapshot of the pre-setup() manifest
        # (or a frozen ``Contributes``), which is a separate change. What this
        # line buys is the naive half, for one attribute read on a path that
        # already spawns subprocesses.
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
