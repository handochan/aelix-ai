"""Import-free resolution of an entry-point plugin's ``aelix-plugin.toml`` (issue #91).

The ``aelix.extensions`` entry-point tier used to resolve a plugin by calling
``ep.load()`` during *discovery* — importing the plugin's module before any
capability gate had seen a single byte of its manifest. That is the same
defect the manifest tiers closed in 878004b, one tier down: a pack that
declares ``[[contributes.hooks]]`` with ``shell_exec = false`` had its module
imported (and its module-level code run) and only then was refused.

This module replaces the import with **metadata-only** resolution. It never
imports the plugin, never touches ``sys.path``, and never consults the import
system: everything it decides, it decides from the installed distribution's
``RECORD`` plus the bytes of the manifest file itself.

Why there is no ``find_spec`` tier
----------------------------------
A "hardened ``find_spec`` fallback" was measured and is attacker-bypassable.
With a real ``pip install -e``, a *src-layout* editable writes a literal path
into the ``.pth`` (so a containment predicate is enforceable), but a
*flat-layout* editable writes an import hook
(``import __editable___flatpack_0_1_0_finder; ...``), which makes the
predicate unenforceable — and a flat-layout attacker pack sailed straight
through, inheriting ``shell_exec = true``. So the ladder stops at metadata.

Why degrading is safe
---------------------
``capabilities.*`` is consumed in exactly two places: the loader's declarative
gates and the manifest's own self-consistency validator. No manifest means
zero declarative contributions, which means no privilege is gained. Resolving
to "no manifest" therefore cannot *grant* anything; it can only cost the pack
its declarative features. That is why every doubtful outcome degrades (with a
visible reason) instead of refusing.

The one exception is ``api.min_level > AELIX_API_LEVEL``: a pack that says it
cannot run on this host is not "unproven", it is incompatible, so that case
raises :class:`EpApiLevelRefusal` rather than degrading.

Trust asymmetry — OPEN, deliberately not decided here
-----------------------------------------------------
The directory tiers are gated by Project Trust (``no_project_local`` is
threaded from ``project_trusted``). This tier is NOT: an installed
distribution is visible to every session in the environment regardless of
which directory the agent was started in, so it is the only tier whose
manifest-declared MCP servers can be reached from an untrusted directory.
ADR-0205 item ④ lists deciding this as a #91 prerequisite; #91 does not
decide it, and says so rather than implying a gate that is not here.

What #91 does NOT change: the population. An installed pack got there because
the user ran an install command, whereas a project-local pack arrives with a
cloned repository — which is precisely why Project Trust exists for the latter
and not the former. What #91 DOES change is that this tier carries manifests
at all, so the question is now live. Until it is settled, the mitigation is
that everything here is metadata-only (no import) and every declarative
family is gated on a capability the manifest must declare in writing.

Contract
--------
``resolve_entry_point_manifest(ep)`` returns an :class:`EpResolution` whose
``outcome`` is one of :class:`EpOutcome`. Only ``BOUND`` carries a manifest and
a ``pkg_dir``; every other outcome carries ``None`` for both plus a ``reason``
string that names the absolute path of whatever went wrong, so the loader can
surface an actionable error instead of installing a silently inert pack.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import Distribution, EntryPoint
from pathlib import Path, PurePosixPath

from aelix_agent_core.contracts import (
    AELIX_API_LEVEL,
    LICENSE_WHITELIST,
    PluginManifest,
    parse_manifest_toml,
)
from pydantic import ValidationError

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "aelix-plugin.toml"
"""The one filename this module will ever bind."""

EXTENSIONS_GROUP = "aelix.extensions"
"""Entry-point group carrying extension factories."""

MANIFESTS_GROUP = "aelix.manifests"
"""OPTIONAL entry-point group (issue #91 decision iv).

A pack may declare ``<ep-name> = <dotted.package.path>`` here to point at the
directory holding its ``aelix-plugin.toml``. It is the remedy for the two
layouts the RECORD ladder cannot bind on its own:

* a manifest shipped outside the entry module's package (otherwise
  :attr:`EpOutcome.MISPLACED`), and
* a single-module pack (``soloext.py``), which has no package directory to
  hold a manifest at all.

OPTIONAL means optional: a pack that omits this group behaves exactly as it
would if the group did not exist. The declared path is subject to the SAME
fence and the SAME RECORD-ownership requirement as the ladder, and only the
*owning* distribution's own entry points are consulted, so this is not a
cross-dist hijack surface.
"""


class EpApiLevelRefusal(Exception):
    """The pack's manifest requires a newer host API level than we provide.

    The ONE place entry-point resolution refuses instead of degrading, and the
    single place issue #91 knowingly breaks a pack that loads today: before
    #91 the manifest was never read on this tier, so an over-new pack loaded
    anyway and misbehaved later.

    Raised out of :func:`resolve_entry_point_manifest`. The loader translates
    it into an ``ExtensionLoadError`` report at the wiring seam (contract
    review L12 corrected this line, which used to name
    ``ExtensionManifestError``). Observable behaviour matches the directory
    tier, whose ``ExtensionManifestError`` is likewise caught and reported as
    an ``ExtensionLoadError``. This module deliberately does not import the
    loader — the loader imports it, so naming either type here is
    documentation only.
    """


class EpOutcome(StrEnum):
    """What entry-point manifest resolution concluded.

    ``BOUND`` is the only outcome that grants declarative features. Everything
    else degrades the pack to manifest-less loading plus a visible error.
    """

    BOUND = "bound"
    """Proved: a RECORD-owned, fence-passing, parseable manifest sits on the
    entry module's own path. ``pkg_dir`` and ``manifest`` are populated."""

    ABSENT = "absent"
    """Proved otherwise: RECORD shows where this pack's files are and none of
    them is a manifest. The pack simply has no manifest. Not an error."""

    UNPROVEN = "unproven"
    """Cannot prove anything: no RECORD, or a RECORD that does not list the
    entry module's files at all (the editable-install shape — RECORD carries
    only the ``.pth`` and the ``dist-info``). Degrade, do not guess."""

    MISPLACED = "misplaced"
    """RECORD *does* list a manifest, but not on the entry module's path, so
    the ladder could not bind it. The reason NAMES the file — this is what
    turns a silently-inert install into an actionable error. Remedy: move the
    manifest, or declare :data:`MANIFESTS_GROUP`."""

    FENCED = "fenced"
    """A candidate manifest was refused by the containment fence: the RECORD
    entry was absolute, contained ``..``, resolved outside the distribution
    root, was not a file, or sat directly at the root rather than inside a
    package directory."""

    MALFORMED = "malformed"
    """A manifest bound but its bytes are unusable (TOML syntax error or
    schema violation). Reason carries the ABSOLUTE path. Degrades — except
    ``api.min_level``, which raises :class:`EpApiLevelRefusal`."""


@dataclass(frozen=True, repr=False)
class EpResolution:
    """Result of :func:`resolve_entry_point_manifest`.

    ``repr`` is REDACTED on purpose, for the same reason ``_ManifestEntry``'s
    is: this object flows into loader warnings and ``str(...)`` on the error
    path, and a default dataclass repr would expand the whole
    :class:`PluginManifest` — including ``contributes.mcp_servers[].env``,
    which holds plugin-supplied secrets. A load warning must never dump an API
    token to stderr or into a pasted bug report.
    """

    outcome: EpOutcome
    pkg_dir: Path | None
    manifest: PluginManifest | None
    reason: str

    @property
    def bound(self) -> bool:
        """True only for :attr:`EpOutcome.BOUND`."""
        return self.outcome is EpOutcome.BOUND

    def __repr__(self) -> str:
        plugin_id = self.manifest.plugin.id if self.manifest is not None else None
        pkg_dir = str(self.pkg_dir) if self.pkg_dir is not None else None
        return (
            f"EpResolution(outcome={self.outcome.value!r}, plugin={plugin_id!r}, "
            f"pkg_dir={pkg_dir!r}, reason={self.reason!r})"
        )


def _degraded(outcome: EpOutcome, reason: str) -> EpResolution:
    """Build a non-binding resolution. No manifest, no ``pkg_dir``, ever."""
    return EpResolution(outcome=outcome, pkg_dir=None, manifest=None, reason=reason)


def _normalise(entry: str) -> str:
    """RECORD paths are ``/``-separated; be tolerant of a Windows-built RECORD."""
    return entry.replace("\\", "/")


def _fence(root: Path, rel: str) -> tuple[Path | None, str]:
    """Containment fence for one RECORD entry. Returns ``(path, refusal_reason)``.

    A fence is MANDATORY here and the reason is measured, not theoretical:
    ``dist.locate_file("")`` returns the SITE-PACKAGES ROOT (not the package
    directory), RECORD legitimately contains ``..`` entries (scripts, data
    files installed outside the purelib), and ``locate_file`` does NOT
    normalise them — they resolve outside the root with ``exists() is True``.
    Without this, a RECORD line is a read primitive over the whole
    environment.

    The last rule (``parent != root``) is not cosmetic either: ``pkg_dir`` is
    handed to ``ext_themes.py``, which fences contributed theme paths with
    ``target.is_relative_to(pkg_dir)``. A ``pkg_dir`` equal to site-packages
    would turn that fence into a read of any file under site-packages.

    ``root`` MUST already be resolved.
    """

    pure = PurePosixPath(rel)
    if pure.is_absolute():
        return None, f"RECORD entry {rel!r} is an absolute path"
    if ".." in pure.parts:
        return None, f"RECORD entry {rel!r} contains '..'"
    # Resolve BOTH sides: the entry may still escape via a symlinked directory.
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root):
        return None, f"RECORD entry {rel!r} resolves to {candidate}, outside the dist root {root}"
    if not candidate.is_file():
        # MEASURED on CPython 3.12.1: ``Distribution.files`` already drops
        # entries whose ``locate().exists()`` is False, so the "listed but
        # deleted" shape usually never gets here — but a DIRECTORY named
        # ``aelix-plugin.toml`` does, and the 3.11 leg has no such filter.
        return None, f"RECORD lists {rel!r} but {candidate} is not a file"
    if candidate.parent == root:
        return None, (
            f"{candidate} sits at the dist root; a manifest must live inside a "
            f"package directory (its parent becomes pkg_dir)"
        )
    return candidate, ""


def _module_of(ep: EntryPoint) -> str | None:
    """``ep.module`` without letting a malformed value escape as an exception."""
    try:
        module = ep.module
    except (AttributeError, ValueError):
        return None
    return module or None


def _record_entries(dist: Distribution) -> list[str] | None:
    """RECORD as a list of normalised strings, or ``None`` when unreadable."""
    try:
        files = dist.files
    except Exception:  # noqa: BLE001 — a broken RECORD must not abort discovery
        return None
    if not files:
        return None
    return [_normalise(str(f)) for f in files]


def _ladder(module: str, owned: dict[str, Path]) -> tuple[str, Path] | None:
    """Walk the entry module's path from most to least specific.

    Binds on the MODULE PATH, never on "the dist". One distribution can carry
    several packages' manifests — measured on ``aelix-ep-danger``, which ships
    two and declares attacker-chosen entry-point names, where a naive
    first-match bound ``aelix_ep_hook``'s manifest to ``aelix_ep_widget``'s
    entry point.
    """

    parts = module.split(".")
    for i in range(len(parts), 0, -1):
        rel = "/".join([*parts[:i], MANIFEST_FILENAME])
        hit = owned.get(rel)
        if hit is not None:
            return rel, hit
    return None


def _declared_manifest(
    dist: Distribution,
    ep_name: str,
    owned: dict[str, Path],
) -> tuple[str, Path] | None:
    """Resolve the OPTIONAL :data:`MANIFESTS_GROUP` declaration for ``ep_name``.

    Only the owning distribution's own entry points are read, and the declared
    location must already be present in ``owned`` — i.e. it passed the fence
    AND is listed in this dist's RECORD. A pack therefore cannot point at
    another dist's manifest, and cannot point outside its own install.
    """

    try:
        eps = list(dist.entry_points)
    except Exception:  # noqa: BLE001 — malformed entry_points.txt is not fatal
        return None
    for candidate in eps:
        if candidate.group != MANIFESTS_GROUP or candidate.name != ep_name:
            continue
        raw = _normalise((candidate.value or "").strip())
        if not raw:
            continue
        if raw.endswith(MANIFEST_FILENAME):
            rel = raw
        elif "/" in raw:
            rel = f"{raw.rstrip('/')}/{MANIFEST_FILENAME}"
        else:
            rel = "/".join([*raw.split("."), MANIFEST_FILENAME])
        hit = owned.get(rel)
        if hit is not None:
            return rel, hit
        logger.warning(
            "entry point %r declares %s = %r but %r is not a fence-passing "
            "RECORD entry of %s; ignoring the declaration",
            ep_name,
            MANIFESTS_GROUP,
            candidate.value,
            rel,
            _dist_name(dist),
        )
    return None


def _dist_name(dist: Distribution) -> str:
    try:
        return dist.name or "<unnamed dist>"
    except Exception:  # noqa: BLE001
        return "<unnamed dist>"


def redact_manifest_error(exc: Exception) -> str:
    """Render a manifest parse failure WITHOUT echoing the manifest body.

    Adversarial review F3, MEASURED. :class:`EpResolution.__repr__` is
    redacted precisely so ``contributes.mcp_servers[].env`` — plugin-supplied
    API tokens — cannot reach stderr, and then pydantic walked straight around
    that discipline: ``str(ValidationError)`` interpolates ``input_value=``,
    which for a MODEL-level validator is the ENTIRE parsed manifest dict.
    Verbatim, before this function existed::

        Value error, `entry.python` is required when capabilities.
        ui_tui_trusted, ... [type=value_error, input_value={'plugin':
        {'id': 'leakym...P91-NEVER-PRINT-ME'}}]}}, input_type=dict]

    pydantic elides the MIDDLE of a long value, so a long token leaks its tail
    and a short one leaks whole. That string was embedded verbatim in the
    ``ExtensionLoadError`` printed by ``cli/entry.py``.

    ``include_input=False`` drops the payload; the LOCATION and the MESSAGE —
    the only actionable parts — are kept, so the error does not get vaguer.
    ``include_url`` drops a docs link that is pure noise in a load error.

    Non-pydantic failures (``tomllib.TOMLDecodeError``, bare ``ValueError``)
    stringify to syntax/position information only and pass through unchanged.
    """

    if isinstance(exc, ValidationError):
        rendered = [
            f"{'.'.join(str(p) for p in err['loc']) or '<manifest>'}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        ]
        if rendered:
            return "; ".join(rendered)
    return str(exc)


def _read_manifest(path: Path) -> tuple[PluginManifest | None, str]:
    """Parse ``path``. Returns ``(manifest, "")`` or ``(None, reason)``.

    Raises:
        EpApiLevelRefusal: when ``api.min_level`` exceeds the host API level.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"

    try:
        manifest = parse_manifest_toml(text)
    except (tomllib.TOMLDecodeError, ValidationError, ValueError) as exc:
        # Body-free rendering — see :func:`redact_manifest_error`.
        return None, f"invalid manifest {path}: {redact_manifest_error(exc)}"

    # The three POST-PARSE checks below are the same three
    # ``loader._load_manifest_from_dir`` applies, in the same order and with
    # the same thresholds: ``api.min_level`` refuses, ``api.level`` warns,
    # a non-whitelisted license warns.
    #
    # The tiers are NOT otherwise identical, and the difference is owner
    # decision (i), not an oversight — contract review H3 caught an earlier
    # version of this comment claiming they were. MEASURED, same broken
    # manifest on both tiers, one run:
    #
    #     dirbad (directory tier) -> LOADED? False  (hard error, no fall-through)
    #     epbad  (endpoint tier)  -> LOADED? True   (degraded, setup() ran)
    #
    # A directory pack with an unparseable manifest is unloadable; an
    # entry-point pack with the same manifest DEGRADES to manifest-less plus a
    # visible error. Degrading is safe because a missing manifest grants
    # nothing (see the module docstring), and refusing here would break
    # installed packs that load today for a defect in a file no previous
    # release even read. Secondary difference: this function also catches a
    # bare ``ValueError``, which ``_load_manifest_from_dir`` does not — one
    # more way to degrade rather than crash, never a way to bind.
    if manifest.api.min_level > AELIX_API_LEVEL:
        # Contract review, break ledger (b): this is the ONE break that
        # silently kills a pack which loads today (before #91 the manifest was
        # never read on this tier, so an over-new pack loaded anyway and
        # misbehaved later). It therefore has to say what to DO, not just what
        # is wrong.
        raise EpApiLevelRefusal(
            f"Plugin {manifest.plugin.id!r} ({path}) requires API_LEVEL "
            f">= {manifest.api.min_level}, host has {AELIX_API_LEVEL}; "
            f"not loaded. Upgrade aelix, or install a build of this plugin "
            f"for API_LEVEL {AELIX_API_LEVEL}. If you author it and it does "
            f"in fact run on this host, lower [plugin.api] min_level."
        )
    if manifest.api.level > AELIX_API_LEVEL:
        logger.warning(
            "Plugin %r built for API_LEVEL %d, host has %d "
            "(loading anyway; behavior at undefined surfaces is best-effort)",
            manifest.plugin.id,
            manifest.api.level,
            AELIX_API_LEVEL,
        )
    if manifest.plugin.license not in LICENSE_WHITELIST:
        logger.warning(
            "Plugin %r declares license %r outside the v1 whitelist; loading anyway",
            manifest.plugin.id,
            manifest.plugin.license,
        )
    return manifest, ""


def _bind(rel: str, path: Path, how: str) -> EpResolution:
    """Parse a bound candidate into BOUND or MALFORMED.

    ``pkg_dir = manifest_path.resolve().parent`` — this is the ONE producer of
    ``pkg_dir`` in the entry-point tier. Nothing else may express it.
    """

    manifest, problem = _read_manifest(path)
    if manifest is None:
        return _degraded(EpOutcome.MALFORMED, problem)
    return EpResolution(
        outcome=EpOutcome.BOUND,
        pkg_dir=path.resolve().parent,
        manifest=manifest,
        reason=f"bound {rel!r} {how}",
    )


def resolve_entry_point_manifest(ep: EntryPoint) -> EpResolution:
    """Resolve the ``aelix-plugin.toml`` belonging to ``ep`` WITHOUT importing it.

    Bind on proof, degrade on doubt, refuse only on evidence of attack.

    The ladder, in order:

    1. Read the owning distribution's RECORD. No dist / no RECORD ⇒
       :attr:`EpOutcome.UNPROVEN`.
    2. Fence every RECORD entry named ``aelix-plugin.toml``.
    3. Walk ``ep.module``'s dotted path from most to least specific and bind
       the first fence-passing, RECORD-owned manifest on it.
    4. If nothing bound, consult the OPTIONAL :data:`MANIFESTS_GROUP`
       declaration of the SAME distribution.
    5. Otherwise classify the failure: MISPLACED (RECORD has a manifest the
       ladder could not bind — named in the reason) > FENCED (a candidate was
       refused by the fence) > UNPROVEN (RECORD does not show the entry
       module's files) > ABSENT (RECORD shows them and there is no manifest).

    Returns:
        An :class:`EpResolution`. Only ``BOUND`` carries ``manifest`` /
        ``pkg_dir``.

    Raises:
        EpApiLevelRefusal: the manifest bound and declares an
        ``api.min_level`` this host cannot satisfy.
    """

    ident = f"{ep.name}={ep.value}"

    dist = getattr(ep, "dist", None)
    if dist is None:
        return _degraded(
            EpOutcome.UNPROVEN,
            f"entry point {ident} has no owning distribution; "
            f"nothing proves where its files live",
        )

    module = _module_of(ep)
    if module is None:
        return _degraded(
            EpOutcome.UNPROVEN,
            f"entry point {ident} has no parseable module path",
        )

    dist_name = _dist_name(dist)
    records = _record_entries(dist)
    if records is None:
        return _degraded(
            EpOutcome.UNPROVEN,
            f"distribution {dist_name} (entry point {ident}) has no readable "
            f"RECORD; its installed layout cannot be proved",
        )

    try:
        root = Path(str(dist.locate_file(""))).resolve()
    except Exception as exc:  # noqa: BLE001
        return _degraded(
            EpOutcome.UNPROVEN,
            f"cannot locate the install root of {dist_name} "
            f"(entry point {ident}): {exc}",
        )

    owned: dict[str, Path] = {}
    fenced: list[str] = []
    plain: list[str] = []
    for rel in records:
        if PurePosixPath(rel).name != MANIFEST_FILENAME:
            plain.append(rel)
            continue
        path, refusal = _fence(root, rel)
        if path is None:
            fenced.append(refusal)
        else:
            owned[rel] = path

    hit = _ladder(module, owned)
    if hit is not None:
        return _bind(hit[0], hit[1], f"on the path of entry module {module!r}")

    declared = _declared_manifest(dist, ep.name, owned)
    if declared is not None:
        return _bind(declared[0], declared[1], f"via the {MANIFESTS_GROUP} entry point")

    fence_note = f" (also fenced: {'; '.join(fenced)})" if fenced else ""

    if owned:
        names = ", ".join(str(p) for p in sorted(owned.values()))
        return _degraded(
            EpOutcome.MISPLACED,
            f"distribution {dist_name} installs a manifest the entry module "
            f"{module!r} does not own: {names}. Move it into the entry module's "
            f"package, or declare [project.entry-points.\"{MANIFESTS_GROUP}\"] "
            f"{ep.name} = \"<package.path>\"{fence_note}",
        )

    if fenced:
        return _degraded(
            EpOutcome.FENCED,
            f"every manifest candidate of {dist_name} (entry point {ident}) was "
            f"refused by the containment fence: {'; '.join(fenced)}",
        )

    head = module.split(".")[0]
    shows_head = any(
        rel == f"{head}.py" or rel.startswith(f"{head}/")
        for rel in plain
        if not PurePosixPath(rel).is_absolute() and ".." not in PurePosixPath(rel).parts
    )
    if not shows_head:
        return _degraded(
            EpOutcome.UNPROVEN,
            f"RECORD of {dist_name} does not list any file of top-level module "
            f"{head!r} (entry point {ident}); the install is editable or "
            f"otherwise unprovable, so no manifest is claimed",
        )

    return _degraded(
        EpOutcome.ABSENT,
        f"distribution {dist_name} installs no {MANIFEST_FILENAME} "
        f"(entry point {ident})",
    )


__all__ = [
    "EXTENSIONS_GROUP",
    "MANIFESTS_GROUP",
    "MANIFEST_FILENAME",
    "EpApiLevelRefusal",
    "EpOutcome",
    "EpResolution",
    "redact_manifest_error",
    "resolve_entry_point_manifest",
]
