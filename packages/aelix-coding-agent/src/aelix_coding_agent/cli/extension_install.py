"""``aelix extension`` — install + a pi-parity marketplace core.

Issue #19 (ADR-0185) shipped ``extension install <path|git|pypi>``: a
closed-network-native installer where all three source kinds resolve to a single
``pip install`` into the RUNNING interpreter's environment
(``sys.executable -m pip``), so the plugin's module becomes importable AND its
``entry_points(group="aelix.extensions")`` registration is discovered by the
loader's Tier-4 pass — no bespoke registry, no ``sys.path`` machinery. pip's own
``--index-url`` / ``git+file`` / ``ssh`` carry the self-hosted / air-gapped
requirement, and pip itself is the install ledger.

Issue #32-A (ADR-0186) adds the pi-parity **marketplace core** on top:

* ``extension source add|list|remove`` — a persisted list of install *sources*
  (a pip index URL / a git repo / a local path), stored on the
  ``SettingsManager`` (``extension_sources``, GLOBAL scope). ``source add`` is
  REGISTER-ONLY (add ≠ install — the owner-decided 2-step model).
* ``extension install <target>`` — #19's installer ENHANCED: a bare package
  NAME resolves against the registered **index** sources (their URLs join pip's
  ``--index-url`` / ``--extra-index-url``); git / path / url targets install
  directly. On success the install is RECORDED (so ``update`` can reinstall it).
* ``extension list`` — the installed inventory, read straight from
  ``entry_points(group="aelix.extensions")`` (the pip ledger — no separate
  record).
* ``extension update [<name>]`` — reinstall a recorded source with
  ``--upgrade`` (git → ``git+url``; path → the path; pypi → the package name +
  index sources). No name = every recorded installable.
* ``extension remove <name>`` — ``pip uninstall`` the distribution providing
  ``<name>`` (entry-point → distribution via ``EntryPoint.dist``), then drop any
  matching recorded source.

pip runs the package's build/setup code, so consent is **source-level** (shown +
y/N, deny-by-default; ``--yes`` for headless) on every install/update — a
manifest *capability* gate is impossible here because the manifest lives inside
the not-yet-built package.

pi parity: this is the Python-ecosystem swap of pi's package model
(``package-manager.ts`` + ``settings.packages``) — pip replaces npm,
``entry_points`` replaces the ``PiManifest`` package root, ``--index-url``
replaces ``.npmrc``, and ``extension_sources`` mirrors pi's ``packages``
``PackageSource[]`` (a DISTINCT field — pi's is an npm-package-with-sub-resources
model; an aelix source only records WHERE to install FROM). Signing / a
discover-catalog are OUT of scope for A (separate follow-up ADRs).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from aelix_ai.settings import ExtensionSourceObject, SettingsManager

_USAGE = (
    "usage: aelix extension <command>\n"
    "  install <path | git-url | package[==version]>  [--yes] [--index-url URL] "
    "[--offline]\n"
    "  source add <path | git-url | index-url>        [--yes]\n"
    "  source list\n"
    "  source remove <path | git-url | index-url>\n"
    "  list\n"
    "  update [<name>]                                [--yes] [--offline]\n"
    "  remove <name>                                  [--yes]"
)

# Exit codes: 0 = success; the pip returncode (usually 1) = pip ran and failed;
# 2 = did NOT run pip (usage error, guard refusal, user abort, missing pip). The
# 3-way split lets a script tell "pip failed" from "never ran" (ADR-0185).
_EXIT_DIDNT_RUN = 2

#: The entry-point group the loader's Tier-4 pass discovers (loader.py:750).
ENTRY_POINT_GROUP = "aelix.extensions"

TargetKind = Literal["path", "git", "pypi"]
#: The kinds a registered *source* can take. ``index`` = a pip index URL used
#: only to RESOLVE a bare-name install (never installed directly); ``git`` /
#: ``path`` = a directly-installable extension; ``pypi`` = an install RECORD of
#: a bare-name install (spec = the package, kept so ``update`` can reinstall it).
SourceKind = Literal["index", "git", "path", "pypi"]

# A subprocess runner injectable for tests (default = the real pip call).
PipRunner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]

__all__ = [
    "ENTRY_POINT_GROUP",
    "InstalledExtension",
    "PipRunner",
    "SourceKind",
    "TargetKind",
    "build_pip_args",
    "classify_source",
    "classify_target",
    "install_extension",
    "list_installed_extensions",
    "run_extension_command",
    "run_extension_command_async",
]


def _env_truthy(name: str) -> bool:
    # Strict 1/true/yes/on (case-insensitive) — so ``PI_OFFLINE=0`` reads as OFF,
    # not "any non-empty" (review NIT). Mirrors the canonical env-flag idiom.
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def classify_target(target: str) -> TargetKind:
    """Classify an install target as a local path, a git URL, or a pypi spec.

    A local path WINS if it exists on disk (so ``./my-ext`` beats any URL
    heuristic); otherwise git-URL shapes (``git+…`` / ``ssh://`` / ``git@`` /
    ``.git`` / an http(s) URL containing ``.git``) classify as git; everything
    else is a pypi package spec (``name`` / ``name==1.2`` / ``name[extra]``).
    """

    if target.strip() and Path(target).expanduser().exists():
        return "path"
    low = target.lower()
    if (
        target.startswith("git+")
        or low.startswith(("git://", "ssh://", "git@"))
        or low.endswith(".git")
        or (low.startswith(("http://", "https://")) and ".git" in low)
    ):
        return "git"
    return "pypi"


def classify_source(target: str) -> SourceKind | None:
    """Classify a ``source add`` target as ``path`` / ``git`` / ``index``.

    Reuses :func:`classify_target`'s path + git heuristics, then maps a *plain*
    http(s) URL (one :func:`classify_target` would call ``pypi`` because it has
    no ``.git``) to ``index`` — a pip package index. Returns :data:`None` for a
    bare token / empty string: a *source* must be a path, a git URL, or an index
    URL — a bare package name is an install TARGET, not a source to register.
    """

    if not target.strip():
        return None
    kind = classify_target(target)
    if kind in ("path", "git"):
        return kind
    # kind == "pypi": only a real http(s) URL is a valid index source.
    if target.strip().lower().startswith(("http://", "https://")):
        return "index"
    return None


def _normalize_git_spec(target: str) -> str:
    """Return a pip-installable ``git+…`` VCS spec for a git target.

    pip's VCS grammar requires a ``git+<transport>://`` scheme, so the scp
    shorthand ``git@host:path`` (which has no ``://``) is rewritten to
    ``git+ssh://git@host/path`` (review LOW: a bare ``git+`` prefix on the
    scp form produces a spec pip rejects at requirement-parse time). Forms
    that already carry a scheme pass through with just the ``git+`` prefix.
    """

    if target.startswith("git+"):
        return target
    # scp shorthand: ``[user@]host:path`` with NO ``://`` scheme.
    if "://" not in target and "@" in target and ":" in target:
        userhost, _, path = target.partition(":")
        return f"git+ssh://{userhost}/{path}"
    return f"git+{target}"


def _install_spec(target: str, kind: TargetKind) -> str:
    """The exact pip-install argument for a target (also its recorded spec)."""

    if kind == "path":
        return str(Path(target).expanduser().resolve())
    if kind == "git":
        return _normalize_git_spec(target)
    return target


def _bare_package_name(target: str) -> str:
    """Strip version specifiers / extras from a pypi target → the base name.

    ``pkg==1.2`` / ``pkg>=1`` / ``pkg[extra]`` / ``pkg ; marker`` → ``pkg``.
    Used so a recorded pypi install stores an upgradeable NAME (``update`` runs
    ``pip install --upgrade <name>`` — a pinned ``==`` spec would never move).
    """

    name = target.strip()
    for sep in ("[", ";", " "):
        name = name.split(sep, 1)[0]
    for op in ("===", "==", ">=", "<=", "~=", "!=", ">", "<", "="):
        name = name.split(op, 1)[0]
    return name.strip()


def build_pip_args(
    target: str,
    kind: TargetKind,
    *,
    index_url: str | None = None,
    extra_index_urls: Iterable[str] | None = None,
    upgrade: bool = False,
) -> list[str]:
    """Build the ``python -m pip install …`` argv for a classified target.

    ``index_url`` / ``extra_index_urls`` apply to a ``pypi`` target only (a
    bare-name install resolved against registered index sources — the first
    index becomes ``--index-url``, the rest ``--extra-index-url``). ``upgrade``
    adds ``--upgrade`` (used by ``extension update``).
    """

    base = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        base.append("--upgrade")
    if kind == "path":
        return [*base, str(Path(target).expanduser().resolve())]
    if kind == "git":
        return [*base, _normalize_git_spec(target)]
    # pypi
    args = [*base, target]
    if index_url:
        args += ["--index-url", index_url]
    for extra in extra_index_urls or ():
        args += ["--extra-index-url", extra]
    return args


def _is_offline(explicit: bool) -> bool:
    # Mirror the CLI's --offline / PI_OFFLINE contract (entry.py) + an aelix
    # alias. Strict truthiness so PI_OFFLINE=0 reads as OFF (review NIT).
    return explicit or _env_truthy("PI_OFFLINE") or _env_truthy("AELIX_OFFLINE")


def _pip_available(runner: PipRunner | None) -> bool:
    """True when the DEFAULT runner can invoke pip on this interpreter.

    A missing pip makes ``python -m pip`` exit nonzero WITHOUT raising, so a
    returncode check would mislabel it as an install failure (review LOW).
    Skipped when a custom runner is injected (tests / an alt package manager).
    uv-managed venvs often ship without pip — hence the actionable hint at the
    call site.
    """

    return runner is not None or importlib.util.find_spec("pip") is not None


def _pip_missing_message() -> str:
    return (
        f"Error: pip is not available on this interpreter ({sys.executable}). "
        "Install it (e.g. `python -m ensurepip --upgrade`, or `uv pip install "
        "pip` in a uv project) and retry."
    )


def install_extension(
    target: str,
    *,
    yes: bool = False,
    index_url: str | None = None,
    extra_index_urls: Iterable[str] | None = None,
    offline: bool = False,
    upgrade: bool = False,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Install one extension via ``pip``; return a process-style exit code.

    ``0`` on success, ``1`` on user-abort / pip failure, ``2`` on a usage/guard
    error. ``runner`` and ``input_fn`` are injectable for tests.
    """

    if not target.strip():
        print("Error: install target is empty.", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    kind = classify_target(target)
    if _is_offline(offline) and kind == "pypi" and not index_url:
        print(
            "Error: offline mode — a pypi install needs --index-url pointing at a "
            "self-hosted / local index (or use a path or git+file:// source).",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    if not _pip_available(runner):
        print(_pip_missing_message(), file=sys.stderr)
        return _EXIT_DIDNT_RUN

    pip_args = build_pip_args(
        target,
        kind,
        index_url=index_url,
        extra_index_urls=extra_index_urls,
        upgrade=upgrade,
    )

    # Consent — pip runs the package's build/setup code (arbitrary at install
    # time), so the manifest capability gate cannot protect this path; the
    # source-level y/N IS the trust boundary. Deny-by-default (headless without
    # --yes, or a closed stdin, aborts).
    verb = "Upgrade" if upgrade else "Install"
    print(f"{verb} extension from {kind}: {target}")
    print(f"  → {' '.join(pip_args)}")
    print(
        "  pip will run the package's build/setup code. Only install sources you trust."
    )
    if not yes:
        try:
            reply = input_fn("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.")
            return _EXIT_DIDNT_RUN  # distinct from pip's own failure code

    run = runner if runner is not None else _default_runner
    result = run(pip_args)
    code = int(getattr(result, "returncode", 1))
    if code == 0:
        print(
            "Installed. Restart aelix (or /reload in the TUI) so the loader "
            "discovers it via entry_points."
        )
    else:
        print(f"pip install failed (exit {code}).", file=sys.stderr)
    return code


def _default_runner(pip_args: list[str]) -> subprocess.CompletedProcess[bytes]:
    # Inherit stdio so the user sees pip's live progress; never shell=True.
    return subprocess.run(pip_args, check=False)  # noqa: S603 — argv list, no shell


# =====================================================================
# === Installed-extension inventory (the pip ledger) ===================
# =====================================================================


@dataclass(frozen=True)
class InstalledExtension:
    """One discovered ``aelix.extensions`` entry point + its distribution."""

    ep_name: str
    dist_name: str | None
    version: str | None


def list_installed_extensions() -> list[InstalledExtension]:
    """Read the installed inventory from ``entry_points(group=...)``.

    This IS the ledger (ADR-0185): every ``aelix extension install`` lands a pip
    distribution whose ``aelix.extensions`` entry point the loader discovers.
    Fully getattr-guarded — a distribution missing ``.dist`` (older metadata)
    degrades to ``dist_name=None`` rather than raising.
    """

    out: list[InstalledExtension] = []
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 — never let a bad env abort the listing
        return out
    for ep in eps:
        dist = getattr(ep, "dist", None)
        dist_name = getattr(dist, "name", None) if dist is not None else None
        version = getattr(dist, "version", None) if dist is not None else None
        out.append(
            InstalledExtension(
                ep_name=ep.name,
                dist_name=str(dist_name) if dist_name else None,
                version=str(version) if version else None,
            )
        )
    return out


def _canon(name: str) -> str:
    """Loose distribution-name canonicalization (case + ``_``/``-`` fold)."""

    return name.strip().lower().replace("_", "-")


def _find_distribution_for_name(name: str) -> str | None:
    """Map an extension ``<name>`` (entry-point OR distribution) to its dist.

    Matches, in order: exact/loose entry-point name, then exact/loose
    distribution name. Returns the distribution to hand to ``pip uninstall``, or
    :data:`None` when nothing installed provides ``<name>``.
    """

    target = _canon(name)
    installed = list_installed_extensions()
    for ext in installed:
        if _canon(ext.ep_name) == target:
            return ext.dist_name or ext.ep_name
    for ext in installed:
        if ext.dist_name and _canon(ext.dist_name) == target:
            return ext.dist_name
    return None


def _installed_dist_names() -> set[str]:
    """Snapshot the set of distribution names currently exposing an extension."""

    return {e.dist_name for e in list_installed_extensions() if e.dist_name}


# =====================================================================
# === Source-list persistence (SettingsManager-backed) ================
# =====================================================================


def _load_settings() -> SettingsManager:
    """Build the real settings manager the CLI reads/writes sources through.

    Mirrors the ``--list-models`` construction (entry.py): the SAME
    ``agent_dir`` as auth.json / mcp.json so ``settings.json`` is not split off.
    """

    from aelix_ai.settings import SettingsManager

    from .config import get_agent_dir

    return SettingsManager.create(
        cwd=str(Path.cwd()), agent_dir=Path(get_agent_dir())
    )


def _source_identity(spec: str, kind: str) -> str:
    """A comparable identity for dedupe — paths resolved, git normalized.

    A path is resolved to its absolute form and a git spec is run through
    :func:`_normalize_git_spec` so the SAME repo dedupes whether it arrived via
    ``source add <raw-url>`` (stored normalized) or an install-record (which
    normalizes through ``_install_spec``); without this the two forms
    (``https://x.git`` vs ``git+https://x.git``) would be treated as distinct and
    the repo would appear twice. An ``index`` spec is compared verbatim.
    """

    stripped = spec.strip()
    if kind == "path":
        try:
            return str(Path(stripped).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            return stripped
    if kind == "git":
        return _normalize_git_spec(stripped)
    return stripped


def _index_urls(sources: list[ExtensionSourceObject]) -> list[str]:
    """The registered pip index URLs, in registration order."""

    return [s.spec for s in sources if s.kind == "index" and s.spec]


def _upsert_source(
    sources: list[ExtensionSourceObject],
    spec: str,
    kind: str,
    *,
    name: str | None = None,
) -> tuple[list[ExtensionSourceObject], bool]:
    """Return ``(new_list, changed)`` after adding/refreshing one source.

    Dedupe is by ``(kind, identity)`` (a path resolved to absolute). An existing
    entry is refreshed only when the new call carries a ``name`` the stored one
    lacks (so recording an install can back-fill the dist name onto a source
    that was ``source add``-registered first). Otherwise a duplicate is a no-op.
    """

    from aelix_ai.settings import ExtensionSourceObject

    identity = _source_identity(spec, kind)
    out = list(sources)
    for i, existing in enumerate(out):
        if existing.kind == kind and _source_identity(existing.spec, existing.kind) == identity:
            if name and not existing.name:
                out[i] = ExtensionSourceObject(
                    spec=existing.spec, kind=existing.kind, name=name
                )
                return out, True
            return out, False
    out.append(ExtensionSourceObject(spec=spec, kind=kind, name=name))
    return out, True


def _remove_source(
    sources: list[ExtensionSourceObject], target: str
) -> tuple[list[ExtensionSourceObject], int]:
    """Return ``(new_list, removed_count)`` dropping sources matching ``target``.

    ``target`` matches a source by spec-identity OR by recorded ``name`` — so
    ``source remove <spec>`` and a post-uninstall cleanup by name both work
    through one path. The target is compared against a set of candidate
    identities (verbatim, path-resolved, git-normalized) so removal succeeds
    whether the user typed the raw or the normalized (``git+…`` / absolute) form.
    """

    stripped = target.strip()
    candidates = {stripped, _source_identity(stripped, "path"),
                  _source_identity(stripped, "git")}
    canon = _canon(stripped)
    kept: list[ExtensionSourceObject] = []
    removed = 0
    for s in sources:
        matches = (
            _source_identity(s.spec, s.kind) in candidates
            or (s.name is not None and _canon(s.name) == canon)
        )
        if matches:
            removed += 1
        else:
            kept.append(s)
    return kept, removed


# =====================================================================
# === Subcommand handlers (async — settings writes need a loop) ========
# =====================================================================


async def _persist(settings: SettingsManager, sources: list[ExtensionSourceObject]) -> None:
    """Write the source list and await the (async) flush so it hits disk."""

    settings.set_extension_sources(sources)
    await settings.flush()


async def _cmd_source(
    rest: list[str],
    *,
    settings: SettingsManager,
) -> int:
    """``extension source add|list|remove`` — manage the registered sources.

    No ``input_fn`` — ``source add`` is register-only (add ≠ install), so no
    subcommand here runs pip or prompts for consent.
    """

    if not rest:
        print(f"Error: source requires a subcommand.\n{_USAGE}", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    action, args = rest[0], rest[1:]

    if action == "list":
        sources = settings.get_extension_sources()
        if not sources:
            print("No extension sources registered.")
            return 0
        print("Extension sources:")
        for s in sources:
            suffix = f" (installed as {s.name})" if s.name else ""
            print(f"  [{s.kind}] {s.spec}{suffix}")
        return 0

    if action == "add":
        # Only a positional target (+ ignore a stray --yes for symmetry).
        positional = [a for a in args if not a.startswith("-")]
        if len(positional) != 1:
            print(
                f"Error: source add requires exactly one <path|git-url|index-url>.\n{_USAGE}",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN
        target = positional[0]
        kind = classify_source(target)
        if kind is None:
            print(
                f"Error: {target!r} is not a valid source. A source must be a local "
                "path, a git URL, or an http(s) index URL (a bare package name is an "
                "install target, not a source — use `aelix extension install`).",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN
        # Register-only (owner-decided 2-step: add ≠ install). Store the
        # NORMALIZED spec (path→absolute, git→git+scheme) so dedupe + display +
        # later install/update all agree with what an install-record writes.
        if kind == "path":
            spec = str(Path(target).expanduser().resolve())
        elif kind == "git":
            spec = _normalize_git_spec(target.strip())
        else:  # index
            spec = target.strip()
        sources = settings.get_extension_sources()
        new_sources, changed = _upsert_source(sources, spec, kind)
        if not changed:
            print(f"Source already registered: [{kind}] {spec}")
            return 0
        await _persist(settings, new_sources)
        print(f"Registered source: [{kind}] {spec}")
        if kind == "index":
            print("  Bare-name installs will resolve against this index.")
        else:
            print(f"  Install it with: aelix extension install {spec}")
        return 0

    if action == "remove":
        positional = [a for a in args if not a.startswith("-")]
        if len(positional) != 1:
            print(
                f"Error: source remove requires exactly one target.\n{_USAGE}",
                file=sys.stderr,
            )
            return _EXIT_DIDNT_RUN
        sources = settings.get_extension_sources()
        new_sources, removed = _remove_source(sources, positional[0])
        if removed == 0:
            print(f"No registered source matched {positional[0]!r}.", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        await _persist(settings, new_sources)
        print(f"Removed {removed} source(s).")
        return 0

    print(
        f"Error: unknown source subcommand {action!r} (add | list | remove).\n{_USAGE}",
        file=sys.stderr,
    )
    return _EXIT_DIDNT_RUN


def _cmd_list() -> int:
    """``extension list`` — the installed inventory (entry-point ledger)."""

    installed = list_installed_extensions()
    if not installed:
        print("No extensions installed (via entry_points).")
        return 0
    print("Installed extensions:")
    for ext in installed:
        dist = f" [{ext.dist_name}]" if ext.dist_name else ""
        version = f" {ext.version}" if ext.version else ""
        print(f"  {ext.ep_name}{version}{dist}")
    return 0


async def _cmd_install(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension install <target>`` — #19 install + resolve + record."""

    parsed = _parse_install_flags(args)
    if isinstance(parsed, int):
        return parsed
    target, yes, offline, index_url = parsed

    kind = classify_target(target)
    # Bare-name resolution: with no explicit --index-url, fold the registered
    # index sources into pip's index set (first → --index-url, rest → extra).
    extra_index_urls: list[str] = []
    if kind == "pypi" and index_url is None:
        registered = _index_urls(settings.get_extension_sources())
        if registered:
            index_url = registered[0]
            extra_index_urls = registered[1:]

    before = _installed_dist_names()
    code = install_extension(
        target,
        yes=yes,
        index_url=index_url,
        extra_index_urls=extra_index_urls,
        offline=offline,
        input_fn=input_fn,
        runner=runner,
    )
    if code == 0:
        await _record_install(settings, target, kind, before)
    return code


async def _record_install(
    settings: SettingsManager,
    target: str,
    kind: TargetKind,
    before: set[str],
) -> None:
    """Record a successful install so ``update`` can reinstall it (best-effort).

    Detects the newly-added distribution name by diffing the entry-point ledger
    before/after (``importlib.invalidate_caches`` first — a same-process install
    otherwise stays invisible to ``importlib.metadata``). Any failure here is
    swallowed: a missed record only degrades ``update``, never the install.
    """

    try:
        importlib.invalidate_caches()
        new_names = sorted(_installed_dist_names() - before)
        detected = new_names[0] if new_names else None
        if kind == "pypi":
            spec = _bare_package_name(target)
            name = detected or spec or None
            record_kind: SourceKind = "pypi"
        else:
            spec = _install_spec(target, kind)
            name = detected
            record_kind = kind  # "git" | "path"
        if not spec:
            return
        sources = settings.get_extension_sources()
        new_sources, changed = _upsert_source(sources, spec, record_kind, name=name)
        if changed:
            await _persist(settings, new_sources)
    except Exception as exc:  # noqa: BLE001 — recording is best-effort
        print(f"Warning: could not record install source: {exc}", file=sys.stderr)


async def _cmd_update(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension update [<name>]`` — reinstall recorded source(s) --upgrade."""

    name_filter: str | None = None
    yes = False
    offline = False
    for a in args:
        if a in ("-y", "--yes"):
            yes = True
        elif a == "--offline":
            offline = True
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif name_filter is None:
            name_filter = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN

    sources = settings.get_extension_sources()
    index_urls = _index_urls(sources)
    # Installable records only — an ``index`` source is a resolution hint, not a
    # thing to upgrade on its own.
    installable = [s for s in sources if s.kind in ("git", "path", "pypi")]

    if name_filter is not None:
        matched = [
            s
            for s in installable
            if (s.name is not None and _canon(s.name) == _canon(name_filter))
            or _source_identity(s.spec, s.kind) == name_filter.strip()
        ]
        if not matched:
            # Not recorded — treat <name> as a pypi package and upgrade it
            # against the registered index sources (covers a name install that
            # was never recorded, e.g. installed before this feature).
            return _upgrade_pypi_name(
                name_filter,
                index_urls,
                yes=yes,
                offline=offline,
                input_fn=input_fn,
                runner=runner,
            )
        targets = matched
    else:
        targets = installable
        if not targets:
            print("No recorded extension sources to update.")
            return 0

    worst = 0
    for s in targets:
        code = _upgrade_source(
            s, index_urls, yes=yes, offline=offline, input_fn=input_fn, runner=runner
        )
        worst = worst or code  # first nonzero wins (report the earliest failure)
    return worst


def _upgrade_source(
    source: ExtensionSourceObject,
    index_urls: list[str],
    *,
    yes: bool,
    offline: bool,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """Reinstall one recorded source with ``--upgrade``."""

    if source.kind == "pypi":
        target = source.name or _bare_package_name(source.spec)
        return _upgrade_pypi_name(
            target, index_urls, yes=yes, offline=offline, input_fn=input_fn, runner=runner
        )
    # git / path: the spec is directly installable.
    return install_extension(
        source.spec,
        yes=yes,
        offline=offline,
        upgrade=True,
        input_fn=input_fn,
        runner=runner,
    )


def _upgrade_pypi_name(
    name: str,
    index_urls: list[str],
    *,
    yes: bool,
    offline: bool,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``pip install --upgrade <name>`` against the registered index sources."""

    index_url = index_urls[0] if index_urls else None
    extra = index_urls[1:] if index_urls else []
    return install_extension(
        name,
        yes=yes,
        index_url=index_url,
        extra_index_urls=extra,
        offline=offline,
        upgrade=True,
        input_fn=input_fn,
        runner=runner,
    )


async def _cmd_remove(
    args: list[str],
    *,
    settings: SettingsManager,
    input_fn: Callable[[str], str],
    runner: PipRunner | None,
) -> int:
    """``extension remove <name>`` — pip uninstall + drop the recorded source."""

    name: str | None = None
    yes = False
    for a in args:
        if a in ("-y", "--yes"):
            yes = True
        elif a.startswith("-"):
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        elif name is None:
            name = a
        else:
            print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
    if name is None or not name.strip():
        print(f"Error: remove requires an extension <name>.\n{_USAGE}", file=sys.stderr)
        return _EXIT_DIDNT_RUN

    dist = _find_distribution_for_name(name)
    if dist is None:
        print(
            f"Error: no installed extension provides {name!r} "
            f"(via entry_points group '{ENTRY_POINT_GROUP}').",
            file=sys.stderr,
        )
        return _EXIT_DIDNT_RUN

    if not _pip_available(runner):
        print(_pip_missing_message(), file=sys.stderr)
        return _EXIT_DIDNT_RUN

    pip_args = [sys.executable, "-m", "pip", "uninstall", "-y", dist]
    print(f"Remove extension: {name} → uninstall distribution {dist}")
    print(f"  → {' '.join(pip_args)}")
    if not yes:
        try:
            reply = input_fn("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.")
            return _EXIT_DIDNT_RUN

    run = runner if runner is not None else _default_runner
    result = run(pip_args)
    code = int(getattr(result, "returncode", 1))
    if code != 0:
        print(f"pip uninstall failed (exit {code}).", file=sys.stderr)
        return code
    # Drop any recorded source for this name/dist (best-effort — a missed
    # cleanup only leaves a stale Sources row, never fails the removal).
    try:
        sources = settings.get_extension_sources()
        new_sources, removed = _remove_source(sources, name)
        if removed == 0 and dist != name:
            new_sources, removed = _remove_source(new_sources, dist)
        if removed:
            await _persist(settings, new_sources)
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not update source list: {exc}", file=sys.stderr)
    print("Removed. Restart aelix (or /reload in the TUI) so the loader drops it.")
    return 0


# =====================================================================
# === Flag parsing for `install` (shared by sync + async entries) ======
# =====================================================================


def _parse_install_flags(
    rest: list[str],
) -> tuple[str, bool, bool, str | None] | int:
    """Parse ``install`` args → ``(target, yes, offline, index_url)`` or a code."""

    target: str | None = None
    yes = False
    offline = False
    index_url: str | None = None
    only_positional = False  # set once a bare ``--`` is seen
    i = 0
    while i < len(rest):
        a = rest[i]
        if only_positional or not a.startswith("-"):
            if target is None:
                target = a
            else:
                print(f"Error: unexpected argument {a!r}.\n{_USAGE}", file=sys.stderr)
                return _EXIT_DIDNT_RUN
        elif a == "--":
            only_positional = True
        elif a in ("-y", "--yes"):
            yes = True
        elif a == "--offline":
            offline = True
        elif a == "--index-url":
            i += 1
            if i >= len(rest) or not rest[i]:
                print("Error: --index-url requires a URL.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            index_url = rest[i]
        elif a.startswith("--index-url="):
            value = a.split("=", 1)[1]
            if not value:
                print("Error: --index-url requires a URL.", file=sys.stderr)
                return _EXIT_DIDNT_RUN
            index_url = value
        elif a in ("-h", "--help"):
            print(_USAGE)
            return 0
        else:
            print(f"Error: unknown flag {a!r}.\n{_USAGE}", file=sys.stderr)
            return _EXIT_DIDNT_RUN
        i += 1

    if target is None or not target.strip():
        print(f"Error: install requires a non-empty target.\n{_USAGE}", file=sys.stderr)
        return _EXIT_DIDNT_RUN
    return target, yes, offline, index_url


# =====================================================================
# === Public dispatch ==================================================
# =====================================================================


async def run_extension_command_async(
    args: list[str],
    *,
    settings: SettingsManager | None = None,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Dispatch ``aelix extension <subcommand> …`` (the async implementation).

    ``settings`` is the source-list store; :data:`None` builds the real
    ``SettingsManager`` (production). Tests inject ``SettingsManager.in_memory()``
    to stay off disk. Must run inside an event loop — the settings write path is
    async and each mutating handler ``await``s ``settings.flush()``.
    """

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if args else _EXIT_DIDNT_RUN
    sub, rest = args[0], args[1:]

    # ``list`` needs no settings — answer it before any settings construction.
    if sub == "list":
        return _cmd_list()

    if settings is None:
        settings = _load_settings()
        for err in settings.drain_errors():
            print(f"Warning: settings ({err.scope}): {err.error}", file=sys.stderr)

    if sub == "install":
        return await _cmd_install(
            rest, settings=settings, input_fn=input_fn, runner=runner
        )
    if sub == "source":
        return await _cmd_source(rest, settings=settings)
    if sub == "update":
        return await _cmd_update(
            rest, settings=settings, input_fn=input_fn, runner=runner
        )
    if sub == "remove":
        return await _cmd_remove(
            rest, settings=settings, input_fn=input_fn, runner=runner
        )

    print(
        f"Error: unknown extension subcommand {sub!r} "
        "(install | source | list | update | remove).\n"
        f"{_USAGE}",
        file=sys.stderr,
    )
    return _EXIT_DIDNT_RUN


def run_extension_command(
    args: list[str],
    *,
    settings: SettingsManager | None = None,
    input_fn: Callable[[str], str] = input,
    runner: PipRunner | None = None,
) -> int:
    """Synchronous entry for ``aelix extension …`` (wraps the async dispatch).

    Provided for direct/non-async callers and tests. The live CLI dispatches via
    :func:`run_extension_command_async` directly (it is already inside the
    ``_async_main`` event loop — a nested :func:`asyncio.run` would raise).
    """

    return asyncio.run(
        run_extension_command_async(
            args, settings=settings, input_fn=input_fn, runner=runner
        )
    )
