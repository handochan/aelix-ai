"""Issue #65 (ADR-0188) — the extension **discover-catalog** (advisory, air-gap).

This module is the *pure* half of #65's discover feature: it owns the catalog
document format, fetching a catalog over the air-gap-native transports
(local path / ``file://`` / self-hosted ``https`` / git shallow-clone), the
lenient forward-compatible parse, and the on-disk merged **cache** sidecar the
CLI and the TUI both read. It imports NOTHING from
:mod:`aelix_coding_agent.cli.extension_install` (which orchestrates pip and the
consent/verify gate and calls into here) so the two stay a clean pure/effectful
split and this half unit-tests with no network and no pip.

Design (ADR-0188, owner-confirmed 2026-07-05):

* A catalog is a self-contained JSON DOCUMENT
  ``{schemaVersion, name?, updated?, extensions:[{name, source, …}]}`` reachable
  by a URL / path the org already controls — so it works on a CLOSED intranet
  (epic #7), including the hardest "no server at all" case (a ``catalog.json`` on
  a shared drive / ``file://`` / a git repo). Registered like an
  ``extension_sources`` entry (``kind="catalog"``); many catalogs merge.
* The catalog is strictly **ADVISORY**: it only chooses WHAT to install. Each
  entry's ``source`` is a ``path | git+url[@sha] | pypi`` spec handed to the
  existing installer — through :func:`resolve_entry_target`, which accepts a
  fixed list of forms, places a relative path beside a local catalog file (handed
  on as a :class:`ResolvedPath`, its ``[extras]`` a separate value) and refuses the
  rest (#131, ADR-0255) — so the source-level ``y/N`` consent prompt +
  ``verify_and_pin`` (#64) remain the sole trust boundary. An entry's optional
  ``sha256`` is **display-only** and MUST NEVER seed the #64 pin store (seeding
  an unauthenticated network hash would manufacture a false green "integrity
  verified" over attacker bytes) — this module therefore imports nothing from,
  and never writes, :mod:`extension_pins`.
* Remote ``http(s)`` fetch REQUIRES TLS (plain ``http://`` is MITM-rewritable and
  is refused); ``file://`` and git ``ssh``/``file`` transports are unconditional
  for the air-gap. A byte / entry-count cap guards a hostile or accidentally
  huge (mis-registered public-index-scale) document.

Writes are SYNC + atomic (``os.replace``), mirroring
:mod:`aelix_coding_agent.cli.extension_pins`. ``--refresh`` is the ONLY writer;
the TUI getter reads the cache synchronously (no network in the render closure).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlsplit

from aelix_ai.utils._child_output import decode_child_output
from aelix_ai.utils._process_tree import run_contained

if TYPE_CHECKING:
    from packaging.requirements import Requirement

__all__ = [
    "ACCEPTED_SOURCE_FORMS",
    "CATALOG_CACHE_FILENAME",
    "DEFAULT_CATALOG_ENV",
    "DEFAULT_CATALOG_FILENAME",
    "DEFAULT_CATALOG_URL",
    "INDEX_ARTIFACT_SUFFIXES",
    "MAX_CATALOG_BYTES",
    "MAX_CATALOG_ENTRIES",
    "MAX_METADATA_BYTES",
    "SCHEMA_VERSION",
    "SIDECAR_SUFFIX",
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "CatalogSpec",
    "DocumentVerifier",
    "GitRunner",
    "IndexedArtifact",
    "Opener",
    "anchor_catalog_location",
    "build_index_catalog",
    "cache_file_path",
    "cached_copy",
    "fetch_catalog",
    "load_cached_catalog",
    "location_matches_selector",
    "now_iso",
    "parse_catalog",
    "read_artifact",
    "resolve_default_catalog_url",
    "resolve_entry",
    "resolve_entry_source",
    "resolve_entry_target",
    "save_catalogs",
    "scan_artifacts",
    "search_entries",
    "select_catalogs",
    "source_looks_like_path",
    "split_path_extras",
]

CATALOG_CACHE_FILENAME = "extension_catalog_cache.json"
#: The catalog document filename read at the root of a git-repo catalog source.
DEFAULT_CATALOG_FILENAME = "catalog.json"
#: Bumped only on an incompatible on-disk change to the CACHE. Read leniently.
SCHEMA_VERSION = 1
#: Reject a catalog document larger than this — guards a hostile / mis-registered
#: public-``/simple/``-scale document from OOMing the parser. Bounded read.
MAX_CATALOG_BYTES = 2 * 1024 * 1024
#: Reject a catalog carrying more than this many entries (same guard).
MAX_CATALOG_ENTRIES = 5000
#: Wall-clock cap on a git-catalog shallow clone so a hung/slow remote cannot
#: stall ``discover --refresh`` indefinitely (the https path is bounded by its own
#: socket ``timeout``). Honored by :func:`_default_git_runner`.
GIT_CLONE_TIMEOUT = 60.0

#: Env var that OVERRIDES / repoints the built-in default catalog URL for one run.
DEFAULT_CATALOG_ENV = "AELIX_DEFAULT_CATALOG"
#: The built-in default catalog URL — the official aelix marketplace catalog on
#: GitHub Pages. ADVISORY (chooses only WHAT to browse; every install still gates on
#: consent + verify_and_pin). Signature enforcement is PROGRESSIVE (guard ⑤,
#: ADR-0192 §amendment): while ``FIRST_PARTY_KEYS`` is empty this catalog is admitted
#: best-effort over TLS (a present-but-INVALID trusted signature still refuses); once
#: the maintainer provisions the first-party catalog key it auto-upgrades to
#: fail-closed. This is the lowest-priority fallback of the ``AELIX_DEFAULT_CATALOG``
#: override chain, NOT a frozen literal — an enterprise repoints it via the env var and
#: an empty value keeps the default absent (guard ②). Resolved by
#: :func:`resolve_default_catalog_url`.
DEFAULT_CATALOG_URL = "https://handochan.github.io/aelix-marketplace/catalog.json"
#: Suffix of the detached-signature sidecar fetched beside a catalog document
#: (``<location>.aelixsig``) and handed to an injected :data:`DocumentVerifier`.
SIDECAR_SUFFIX = ".aelixsig"

#: C0 controls + DEL + C1 controls — stripped from untrusted catalog DISPLAY
#: strings (a catalog is unauthenticated network/file data; a raw ``\n`` would
#: forge extra rows in the ANSI-rendered TUI frame and an SGR escape would paint a
#: fake "verified" badge). A control char in a functional ``source`` spec instead
#: SKIPS the entry (it can never be a valid path/git/pypi spec).
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean_display(value: str | None) -> str | None:
    """Collapse control/escape chars in an untrusted display string to spaces.

    Returns ``None`` when the result is empty so an all-control field drops rather
    than rendering a blank row. Used on every catalog-supplied field that reaches a
    terminal (name/description/version/homepage/catalog label).
    """

    if value is None:
        return None
    cleaned = _CONTROL_RE.sub(" ", value).strip()
    return cleaned or None


def _clean_error(text: str) -> str:
    """Strip control/escape chars from a catalog ``error`` before it becomes a
    display field (ADR-0192).

    Unlike :func:`_clean_display` this never collapses to ``None`` (an error row must
    always render). A FETCHED ``.aelixsig`` ``keyId`` reaches this one channel via a
    signature-verification refusal → :class:`CatalogError` → :attr:`Catalog.error`,
    which both the CLI and the TUI render RAW; scrubbing here keeps ``Catalog.error``
    injection-safe by construction so neither render site can emit raw ANSI/control
    bytes from attacker-controlled sidecar content.
    """

    return _CONTROL_RE.sub(" ", text).strip()

#: An injectable HTTP(S) fetcher (``(url, timeout) -> bytes``) — the default uses
#: ``urllib.request``; tests inject a stub so no network is touched.
Opener = Callable[[str, float], bytes]
#: An injectable git-clone runner (argv → CompletedProcess) — default subprocess.
GitRunner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]
#: An injected catalog-document verifier — ``(document_bytes, sidecar_bytes|None,
#: location) -> None``. Called AFTER the raw bytes are fetched and BEFORE the parse;
#: it verifies the document against its ``.aelixsig`` sidecar and RAISES to reject a
#: catalog (surfaced as :class:`CatalogError`, so the source degrades to an error
#: row). ``None`` (the default) skips verification. It is INJECTED — never imported —
#: so this pure module stays decoupled from the signing / pin code (AST-purity,
#: ADR-0188 §4a); the concrete verifier lives in ``extension_install``. Its return
#: value is ignored; only a raise gates admission.
DocumentVerifier = Callable[[bytes, "bytes | None", str], object]


def resolve_default_catalog_url() -> str | None:
    """The built-in default catalog URL, or :data:`None` when disabled.

    Priority: the ``AELIX_DEFAULT_CATALOG`` env var overrides :data:`DEFAULT_CATALOG_URL`
    — an enterprise repoints the default, or kills it for this run with an empty
    value. A blank / whitespace result → :data:`None`.

    :data:`DEFAULT_CATALOG_URL` is a LIVE https location (the official marketplace
    catalog on GitHub Pages), not an empty placeholder, so with no env override
    this returns that URL and the built-in default is ACTIVE — ``discover`` fetches
    it unless the user opted out (``source remove <default>`` tombstone) or is
    ``--offline``. The catalog itself may legitimately be empty
    (``{"extensions": []}``); that is a successful fetch, not a dormant mechanism.

    The returned string is RAW; the caller (``extension_install``) normalizes it via
    ``_normalize_catalog_spec`` before use.
    """

    raw = os.environ.get(DEFAULT_CATALOG_ENV)
    if raw is None:
        raw = DEFAULT_CATALOG_URL
    raw = raw.strip()
    return raw or None


class CatalogError(Exception):
    """A catalog could not be fetched or parsed.

    The CLI turns this into a per-source warning and skips that catalog rather
    than aborting the whole ``discover`` — one bad/unreachable catalog must never
    hide the others (mirrors the lenient spirit of :func:`load_cached_catalog`).
    """


# =====================================================================
# === Data model =======================================================
# =====================================================================


@dataclass(frozen=True)
class CatalogEntry:
    """One advertised extension in a catalog.

    ``source`` is the ONLY field the installer consumes — a ``path``, a
    ``git+url[@40-hexsha]``, or a ``pypi-name[==version]`` spec. It reaches the
    installer through :func:`resolve_entry_target`, never raw: only a fixed list of
    forms is accepted, a ``./`` or ``../`` path resolves against the catalog FILE's
    directory (never the process cwd), and every other form is refused, never
    rewritten (#131, ADR-0255). ``sha256`` is
    DISPLAY-ONLY (ADR-0188): it is never written to the #64 pin store.
    ``catalog_name`` records which catalog the entry came from (for grouped
    display + ambiguous-name disambiguation) and ``catalog_location`` WHERE that
    catalog lives — the base a relative ``source`` resolves against. It is not
    part of the entry's JSON (the catalog block already carries ``location``) and
    not part of its equality. ``extra`` preserves unknown keys verbatim for
    forward compatibility.
    """

    name: str
    source: str
    description: str | None = None
    version: str | None = None
    versions: tuple[str, ...] = ()
    sha256: str | None = None
    homepage: str | None = None
    catalog_name: str | None = None
    extra: dict[str, object] = field(default_factory=dict)
    catalog_location: str | None = field(default=None, compare=False, repr=False)

    #: The keys :meth:`from_json` maps into named fields (everything else → extra).
    _KNOWN = frozenset(
        {"name", "source", "description", "version", "versions", "sha256", "homepage"}
    )

    def display_version(self) -> str | None:
        """The version to show — the explicit ``version`` else the first of ``versions``."""

        if self.version:
            return self.version
        return self.versions[0] if self.versions else None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"name": self.name, "source": self.source}
        if self.description is not None:
            out["description"] = self.description
        if self.version is not None:
            out["version"] = self.version
        if self.versions:
            out["versions"] = list(self.versions)
        if self.sha256 is not None:
            out["sha256"] = self.sha256
        if self.homepage is not None:
            out["homepage"] = self.homepage
        out.update(self.extra)
        return out

    @classmethod
    def from_json(
        cls,
        raw: dict[str, object],
        *,
        catalog_name: str | None,
        catalog_location: str | None = None,
    ) -> CatalogEntry | None:
        """Parse one entry; return :data:`None` (skip) when name-or-source is missing.

        A single malformed entry is skipped, never fatal — the rest of the
        catalog still loads.
        """

        def _s(key: str) -> str | None:
            v = raw.get(key)
            return v if isinstance(v, str) and v.strip() else None

        source = _s("source")
        name = _s("name")
        # ``source`` is functional (fed to the installer) — a control char makes it
        # an invalid path/git/pypi spec, so skip the entry rather than sanitize it.
        if not name or not source or _CONTROL_RE.search(source):
            return None
        raw_versions = raw.get("versions")
        versions: tuple[str, ...] = ()
        if isinstance(raw_versions, list):
            versions = tuple(
                c for v in raw_versions
                if isinstance(v, str) and (c := _clean_display(v)) is not None
            )
        extra = {k: v for k, v in raw.items() if k not in cls._KNOWN}
        # Sanitize every DISPLAY field (name/description/version/homepage) — a
        # catalog is untrusted; a raw newline/SGR escape must not reach the frame.
        # ``name`` is also the resolve key, so cleaning it keeps browse == resolve.
        clean_name = _clean_display(name)
        if clean_name is None:
            return None
        return cls(
            name=clean_name,
            source=source,
            description=_clean_display(_s("description")),
            version=_clean_display(_s("version")),
            versions=versions,
            sha256=_s("sha256"),
            homepage=_clean_display(_s("homepage")),
            catalog_name=_clean_display(catalog_name),
            extra=extra,
            catalog_location=catalog_location,
        )


@dataclass(frozen=True)
class Catalog:
    """A fetched (or cached) catalog document from one registered location.

    ``error`` is set (with ``entries=()``) when a fetch/parse failed but the
    location is still recorded in the cache, so the TUI can show an honest
    "⚠ failed to fetch" row rather than silently dropping the source.

    ``registered_as`` is the spec as REGISTERED when ``discover --refresh``
    anchored a relative one (``catalog.json`` → ``/abs/catalog.json``, ADR-0255):
    ``location`` is then the absolute file that was read, and
    ``discover install --catalog catalog.json`` still selects the catalog by the
    spec the user registered (:func:`resolve_entry`). ``None`` otherwise.
    """

    location: str
    name: str | None = None
    updated: str | None = None
    entries: tuple[CatalogEntry, ...] = ()
    fetched_at: str | None = None
    error: str | None = None
    registered_as: str | None = None

    def label(self) -> str:
        """A human display label — the document ``name`` else the raw location."""

        return self.name or self.location

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {"location": self.location}
        if self.name is not None:
            out["name"] = self.name
        if self.updated is not None:
            out["updated"] = self.updated
        if self.fetched_at is not None:
            out["fetchedAt"] = self.fetched_at
        if self.error is not None:
            out["error"] = self.error
        if self.registered_as is not None:
            out["registeredAs"] = self.registered_as
        out["extensions"] = [e.to_json() for e in self.entries]
        return out

    @classmethod
    def from_json(cls, raw: dict[str, object]) -> Catalog | None:
        """Parse a cached catalog block; :data:`None` when it carries no location."""

        def _s(key: str) -> str | None:
            v = raw.get(key)
            return v if isinstance(v, str) else None

        location = _s("location")
        if not location or not location.strip():
            return None
        name = _clean_display(_s("name"))
        label = name or location
        raw_entries = raw.get("extensions")
        entries: list[CatalogEntry] = []
        if isinstance(raw_entries, list):
            for item in raw_entries:
                if isinstance(item, dict):
                    entry = CatalogEntry.from_json(
                        item, catalog_name=label, catalog_location=location
                    )
                    if entry is not None:
                        entries.append(entry)
        return cls(
            location=location,
            name=name,
            updated=_s("updated"),
            entries=tuple(entries),
            fetched_at=_s("fetchedAt"),
            error=(_clean_error(cached_error) if (cached_error := _s("error")) is not None else None),
            registered_as=_s("registeredAs"),
        )


def now_iso() -> str:
    """UTC ISO-8601 timestamp (seconds precision), matching extension_pins."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


# =====================================================================
# === Parse ============================================================
# =====================================================================


def parse_catalog(
    data: bytes | str,
    *,
    location: str,
    fetched_at: str | None = None,
) -> Catalog:
    """Parse a catalog document → :class:`Catalog`. LENIENT + capped.

    Unknown top-level / per-entry keys are ignored (``schemaVersion`` gates only
    breaking changes); an entry missing name-or-source is skipped; a document
    exceeding :data:`MAX_CATALOG_BYTES` or :data:`MAX_CATALOG_ENTRIES` is refused
    with :class:`CatalogError`. Raises :class:`CatalogError` on non-JSON, a
    non-object root, or a missing ``extensions`` array.
    """

    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError(
            f"catalog {location!r} exceeds the {MAX_CATALOG_BYTES} byte cap "
            f"({len(data)} bytes) — refusing to parse"
        )
    try:
        raw = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CatalogError(f"catalog {location!r} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogError(f"catalog {location!r} root is not a JSON object")
    raw_entries = raw.get("extensions")
    if not isinstance(raw_entries, list):
        raise CatalogError(f"catalog {location!r} has no 'extensions' array")
    if len(raw_entries) > MAX_CATALOG_ENTRIES:
        raise CatalogError(
            f"catalog {location!r} has {len(raw_entries)} entries "
            f"(cap {MAX_CATALOG_ENTRIES}) — refusing to parse"
        )
    doc_name = raw.get("name")
    name = _clean_display(doc_name) if isinstance(doc_name, str) else None
    label = name or location
    entries: list[CatalogEntry] = []
    for item in raw_entries:
        if isinstance(item, dict):
            entry = CatalogEntry.from_json(item, catalog_name=label, catalog_location=location)
            if entry is not None:
                entries.append(entry)
    return Catalog(
        location=location,
        name=name,
        updated=raw.get("updated") if isinstance(raw.get("updated"), str) else None,
        entries=tuple(entries),
        fetched_at=fetched_at or now_iso(),
    )


# =====================================================================
# === Fetch (path / file:// / https / git) =============================
# =====================================================================


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse any redirect whose target is not HTTPS (a TLS-downgrade attack).

    Without this, a compromised/rogue ``https://`` catalog host could 302 the
    fetch to ``http://`` and serve the name→spec document over plaintext — an
    invariant-#3 bypass through the back door.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def, override]
        if not newurl.lower().startswith("https://"):
            raise CatalogError(f"catalog fetch refused an insecure redirect to {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_opener(url: str, timeout: float) -> bytes:
    """Fetch ``url`` over HTTPS, bounded to :data:`MAX_CATALOG_BYTES` + 1 bytes.

    Cross-scheme redirects to plaintext ``http`` are refused, and the FINAL URL is
    re-asserted to be ``https://`` after the request settles — so a redirect chain
    can never downgrade the catalog fetch off TLS.
    """

    opener = urllib.request.build_opener(_HttpsOnlyRedirect)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 — https enforced by caller
    with opener.open(req, timeout=timeout) as resp:  # noqa: S310 — scheme checked in fetch_catalog + redirect handler
        final = str(getattr(resp, "url", None) or resp.geturl() or url)
        if not final.lower().startswith("https://"):
            raise CatalogError(f"catalog fetch ended on a non-HTTPS URL: {final!r}")
        return resp.read(MAX_CATALOG_BYTES + 1)


def _default_git_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    # A contained run (#221): its own session, a process group / job the timeout
    # ends WHOLE, and a close() that is a release.
    #
    # The group is not belt-and-braces. Measured on main dff6b62: a `git clone`
    # whose `git` is SIGKILLed root-only leaves `git remote-http` — blocked in
    # libcurl on a hung server — alive 3 s later at `ppid 1` and still in OUR
    # process group; the ssh transport helper and its own child likewise. So the
    # old shape (subprocess.run(timeout=), which kills the root and nothing
    # else) left the pipeline behind on every timed-out refresh.
    #
    # The session costs the clone its terminal, deliberately (spec §A.4.1): the
    # alternative that keeps one, `process_group=0`, was measured under a real
    # pty to leave git, ssh, sshd-session and sshd-auth all STOPPED (`ps stat T`,
    # SIGTTOU out of ssh's tcsetattr) with NO prompt ever printed, for the full
    # 60 s. With no terminal every tty read instead fails at once with the tool's
    # own message — `fatal: could not read Username for '…'` (rc 128, measured
    # 0.08 s on darwin against a local 401 remote; the tail after that colon is
    # the platform's strerror(ENXIO) — `Device not configured` here, `No such
    # device or address` on Linux — so it is truncated rather than quoted, #221
    # review DOC-2), `Host key verification failed.` (rc 128, 0.62 s) — which
    # _git_clone_bytes already surfaces. A catalog clone is therefore
    # NON-INTERACTIVE ON YOUR TERMINAL, which is narrower than "non-interactive"
    # and is what docs/guides/private-catalog.md now says (#221 review
    # DOC-3/SITE-3, both measured): an ASKPASS program needs no terminal, still
    # runs — VS Code exports GIT_ASKPASS unconditionally, and git under this very
    # spawn was measured calling it TWICE — and a dialog nobody answers still
    # costs the full 60 s before the clone fails. On win32 there is no session at
    # all (the containment is CREATE_NEW_PROCESS_GROUP plus a job), the clone
    # keeps our console, and a prompt there is still possible. That last
    # sentence is now on every surface that states the property — the guide and
    # its bundled copy, both CHANGELOG bullets, ADR-0238, the decisions README
    # row and the timeout CatalogError below — because the post-merge review
    # found it stated unconditionally on all five (adversary-1).
    #
    # GIT_CLONE_TIMEOUT bounds the root, as it did.
    return run_contained(argv, timeout=GIT_CLONE_TIMEOUT)


def _read_local(path: Path, location: str) -> bytes:
    if not path.is_file():
        raise CatalogError(f"catalog file not found: {location}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CatalogError(f"cannot stat catalog {location!r}: {exc}") from exc
    if size > MAX_CATALOG_BYTES:
        raise CatalogError(
            f"catalog {location!r} exceeds the {MAX_CATALOG_BYTES} byte cap ({size} bytes)"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"cannot read catalog {location!r}: {exc}") from exc


def _read_sidecar_local(path: Path) -> bytes | None:
    """Best-effort read of a detached-signature sidecar → its bytes, else ``None``.

    A missing sidecar (the catalog is unsigned), a SYMLINK (a malicious repo must
    not exfiltrate an arbitrary host file into the verifier), an oversized blob, or
    any read error all degrade to ``None`` — the verifier then treats the catalog as
    unsigned. The envelope is tiny, so it shares the document byte cap as a DoS guard.
    """

    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_CATALOG_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _git_clone_bytes(
    location: str, *, git_runner: GitRunner
) -> tuple[bytes, bytes | None]:
    """Shallow-clone a git catalog source and read its root ``catalog.json``.

    Returns ``(document_bytes, sidecar_bytes|None)`` — the sibling root
    ``catalog.json.aelixsig`` is read from the SAME clone when present (best-effort,
    ``None`` when absent) so an injected verifier sees the detached signature over
    the same transport. The clone is discarded immediately; only the catalog
    document (and its sidecar) survives. A
    ``git+`` prefix is stripped for the actual clone URL. Clones are expected to
    target trusted intranet remotes (the source is admin-registered), but the
    transport is still guarded: a plaintext ``http`` clone URL is REFUSED (TLS,
    same as the direct https path), a missing/failing ``git`` binary or a hung
    remote degrades to a :class:`CatalogError` (never an escaping ``OSError`` /
    ``TimeoutExpired`` that would abort the whole ``discover --refresh``), and a
    ``catalog.json`` that is a symlink or resolves outside the clone dir is
    refused (a malicious repo must not exfiltrate an arbitrary host file).
    """

    clone_url = location[len("git+") :] if location.startswith("git+") else location
    if clone_url.lower().startswith("http://"):
        raise CatalogError(
            f"refusing to clone catalog git source over plain HTTP (TLS required): {location}"
        )
    dest = tempfile.mkdtemp(prefix="aelix-catalog-")
    try:
        try:
            result = git_runner(["git", "clone", "--depth", "1", clone_url, dest])
        except subprocess.TimeoutExpired as exc:
            # BEFORE the broad branch below, because a timeout has ONE likely
            # cause worth naming since #221 gave the clone a session of its own:
            # the clone is non-interactive ON YOUR TERMINAL, so a remote that
            # would have prompted for a password, a passphrase or a host key
            # cannot, and the wait is whatever else the transport was doing. The
            # generic ``{exc}`` rendering of a TimeoutExpired says only "timed
            # out after 60.0 seconds", which is the symptom, not the cause. (It
            # is a LIKELY cause, not a guarantee: _default_git_runner's comment
            # above records the two ways a prompt still reaches the user — an
            # askpass program, and win32's console.)
            #
            # THE MESSAGE ITSELF NAMES BOTH OF THOSE and does NOT say "a catalog
            # clone is non-interactive", which the post-merge review found false
            # on win32 (adversary-1): there is no session there, the clone keeps
            # our console, and git reads CONIN$ rather than the stdin we set to
            # NUL, so an unanswered console prompt burns the whole
            # GIT_CLONE_TIMEOUT and lands in exactly this branch. The wording is
            # true on both legs WITHOUT branching on sys.platform, deliberately:
            # a platform branch here would give the windows leg a different
            # string to assert and let the two expectations drift apart.
            #
            # AND THE GUESS IS CHECKABLE, because since #221 the timeout
            # attaches whatever the transport printed before the deadline
            # (bytes on BOTH platforms, never None — strictly more than main
            # had on win32). A clone that timed out for an unrelated reason — a
            # genuinely slow remote — is otherwise told to configure a
            # credential helper it may already have, and shown no evidence at
            # all; the non-zero-exit branch below already surfaces the same
            # tail under the same 200-char cap (#221 review SITE-5).
            # ``ragged_tail=True`` (#239 cross-review): a ``TimeoutExpired``
            # from ``run_contained`` carries "everything the reader has read so
            # far", which ends at an arbitrary byte — unlike the non-zero-exit
            # branch below, whose ``stderr`` is a completed stream. Without the
            # claim a severed trailing character is spelled by the console code
            # page instead of showing U+FFFD.
            partial = (
                decode_child_output(exc.stderr, ragged_tail=True).strip()[:200]
                if isinstance(exc.stderr, bytes)
                else ""
            )
            raise CatalogError(
                f"git clone failed for catalog {location!r}: no result within "
                f"{GIT_CLONE_TIMEOUT:g}s — the likeliest cause is a prompt nobody "
                "answered: an askpass dialog, or on Windows a prompt on the console this "
                "clone shares with Aelix (elsewhere the clone has no terminal to be asked "
                "on); configure a credential helper (https) or an ssh agent and a known "
                "host key (ssh)"
                + (f" — git said: {partial}" if partial else "")
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            # Missing git binary (FileNotFoundError), fork failure, or any other
            # SubprocessError (the timeout has its own branch above) — degrade
            # this ONE source, never crash the refresh.
            raise CatalogError(f"git clone failed for catalog {location!r}: {exc}") from exc
        if int(getattr(result, "returncode", 1)) != 0:
            stderr = getattr(result, "stderr", b"") or b""
            detail = decode_child_output(stderr).strip() if isinstance(stderr, bytes) else str(stderr)
            raise CatalogError(f"git clone failed for catalog {location!r}: {detail[:200]}")
        dest_real = Path(dest).resolve()
        catalog_path = dest_real / DEFAULT_CATALOG_FILENAME
        if catalog_path.is_symlink() or catalog_path.resolve().parent != dest_real:
            raise CatalogError(
                f"catalog git repo {location!r}: {DEFAULT_CATALOG_FILENAME} is a symlink / "
                "resolves outside the clone — refusing"
            )
        if not catalog_path.is_file():
            raise CatalogError(
                f"catalog git repo {location!r} has no {DEFAULT_CATALOG_FILENAME} at its root"
            )
        document = _read_local(catalog_path, location)
        # Sibling detached signature (best-effort) read from the SAME clone, before
        # it is discarded below — the verifier sees it over the same transport.
        sidecar = _read_sidecar_local(
            dest_real / (DEFAULT_CATALOG_FILENAME + SIDECAR_SUFFIX)
        )
        return document, sidecar
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def _file_url_to_path(location: str) -> Path:
    """Map a ``file://`` URL to a local path (handles ``file:///abs`` + host-less).

    A non-empty, non-``localhost`` host (``file://server/share/x``) is REFUSED
    rather than silently reinterpreted as a local path — the local read would
    target the wrong file (dropping the host), which is both surprising and a
    footgun (a UNC-style intranet path would resolve to a bogus local file).
    """

    parsed = urlparse(location)
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        raise CatalogError(
            f"file:// catalog with a remote host is not supported: {location} "
            "(use a local path, an https URL, or a git source)"
        )
    # url2pathname (NOT urllib.parse.unquote) — it percent-decodes like unquote on
    # POSIX but on Windows delegates to nturl2path, which strips the slash in front
    # of the drive letter. Plain unquote left `/C:/x`, which WindowsPath reads as a
    # drive-less relative path, so every file:// catalog fetch missed (issue #205).
    # nturl2path raises OSError on a second colon (`/C:/a:b`); that stays inside
    # the CatalogError contract so one bad stored spec degrades its own row.
    try:
        return Path(urllib.request.url2pathname(parsed.path))
    except OSError as exc:
        raise CatalogError(f"malformed file:// catalog URL: {location} ({exc})") from exc


def _fetch_sidecar_https(location: str, opener: Opener, timeout: float) -> bytes | None:
    """Best-effort fetch of ``<location>.aelixsig`` over the SAME https opener.

    A 404 / missing sidecar (unsigned catalog) or any transport error → ``None`` (the
    verifier then treats the catalog as unsigned). An oversized blob is discarded.
    """

    try:
        data = opener(location + SIDECAR_SUFFIX, timeout)
    except Exception:  # noqa: BLE001 — any fetch failure means "no sidecar present"
        return None
    return data if len(data) <= MAX_CATALOG_BYTES else None


def _run_document_verifier(
    verifier: DocumentVerifier | None,
    document: bytes,
    sidecar: bytes | None,
    location: str,
) -> None:
    """Run an injected verifier over the RAW fetched bytes; a raise → CatalogError.

    A ``None`` verifier is a no-op (verification disabled). Otherwise the verifier
    verifies ``document`` against its ``.aelixsig`` ``sidecar`` and RAISES to reject
    the catalog; the injected verifier is expected to raise :class:`CatalogError`
    (its adapter translates a signing refusal), but ANY exception is surfaced as
    :class:`CatalogError` so :func:`fetch_all` degrades THIS one catalog to an
    error-row (``entries=()``) — attacker bytes never reach the parse or the cache.
    """

    if verifier is None:
        return
    try:
        verifier(document, sidecar, location)
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001 — any verifier refusal rejects the catalog
        raise CatalogError(
            f"catalog {location!r} failed signature verification: {exc}"
        ) from exc


def _is_git_location(loc: str) -> bool:
    """True when a catalog LOCATION is fetched by a git clone (see :func:`fetch_catalog`)."""

    low = loc.lower()
    return (
        loc.startswith("git+")
        or low.startswith(("git://", "ssh://", "git@"))
        or low.endswith(".git")
    )


def fetch_catalog(
    location: str,
    *,
    opener: Opener = _default_opener,
    git_runner: GitRunner = _default_git_runner,
    timeout: float = 30.0,
    verifier: DocumentVerifier | None = None,
) -> Catalog:
    """Fetch + parse the catalog at ``location`` over an air-gap-native transport.

    Dispatch by shape: ``git+…`` → shallow clone + read root ``catalog.json``;
    ``https://`` → TLS fetch; ``http://`` → REFUSED (TLS required, ADR-0188);
    ``file://`` or a bare local path → read the file. Raises :class:`CatalogError`
    on any transport/parse failure (the CLI degrades that source to a warning).

    When a ``verifier`` is injected, the sibling ``<location>.aelixsig`` sidecar is
    fetched over the SAME transport (best-effort → ``None`` when absent) and the
    verifier runs over the RAW fetched bytes BEFORE the parse; a verifier raise is
    surfaced as :class:`CatalogError` (that one source degrades to an error-row, so
    no unverified entries reach the parse or the cache).
    """

    loc = location.strip()
    if not loc:
        raise CatalogError("empty catalog location")
    low = loc.lower()

    if _is_git_location(loc):
        # The sidecar rides along on the one clone, so it is read unconditionally.
        data, sidecar = _git_clone_bytes(loc, git_runner=git_runner)
        _run_document_verifier(verifier, data, sidecar, location)
        return parse_catalog(data, location=location)

    if low.startswith("http://"):
        raise CatalogError(
            f"refusing to fetch catalog over plain HTTP (TLS required): {location} "
            "— use https://, a file:// path, or a git source"
        )
    if low.startswith("https://"):
        try:
            data = opener(loc, timeout)
        except CatalogError:
            raise
        except Exception as exc:  # noqa: BLE001 — any urllib/network error → CatalogError
            raise CatalogError(f"failed to fetch catalog {location!r}: {exc}") from exc
        if len(data) > MAX_CATALOG_BYTES:
            raise CatalogError(
                f"catalog {location!r} exceeds the {MAX_CATALOG_BYTES} byte cap"
            )
        # Only spend the extra network round-trip for the sidecar when verifying.
        sidecar = _fetch_sidecar_https(loc, opener, timeout) if verifier is not None else None
        _run_document_verifier(verifier, data, sidecar, location)
        return parse_catalog(data, location=location)

    # file:// URL or a bare local path.
    path = _file_url_to_path(loc) if low.startswith("file://") else Path(loc).expanduser()
    data = _read_local(path, location)
    sidecar = (
        _read_sidecar_local(path.with_name(path.name + SIDECAR_SUFFIX))
        if verifier is not None
        else None
    )
    _run_document_verifier(verifier, data, sidecar, location)
    return parse_catalog(data, location=location)


def fetch_all(
    locations: Iterable[str],
    *,
    opener: Opener = _default_opener,
    git_runner: GitRunner = _default_git_runner,
    timeout: float = 30.0,
    verifier: DocumentVerifier | None = None,
) -> list[Catalog]:
    """Fetch every registered catalog location; a failure becomes an ``error`` row.

    Never raises for a single bad source — a failed fetch (or a verifier refusal)
    yields a :class:`Catalog` with ``error`` set and ``entries=()`` so ``--refresh``
    records the failure in the cache (the TUI shows it) instead of dropping the
    location. An injected ``verifier`` gates each catalog's raw bytes; a refusal
    degrades ONLY that catalog (its entries are never cached).
    """

    out: list[Catalog] = []
    for loc in locations:
        try:
            out.append(
                fetch_catalog(
                    loc, opener=opener, git_runner=git_runner, timeout=timeout, verifier=verifier
                )
            )
        except CatalogError as exc:
            out.append(
                Catalog(location=loc, entries=(), fetched_at=now_iso(), error=_clean_error(str(exc)))
            )
    return out


# =====================================================================
# === Cache sidecar (agent_dir/extension_catalog_cache.json) ===========
# =====================================================================


def cache_file_path(agent_dir: str | os.PathLike[str]) -> Path:
    """The merged-cache sidecar path (``<agent_dir>/extension_catalog_cache.json``)."""

    return Path(agent_dir) / CATALOG_CACHE_FILENAME


def save_catalogs(catalogs: list[Catalog], path: Path) -> None:
    """Atomically write the merged cache to ``path`` (same swap as save_pins)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "catalogs": [c.to_json() for c in catalogs],
    }
    body = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=".extension_catalog_cache.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def load_catalogs(path: Path) -> list[Catalog]:
    """Load the merged cache → ``list[Catalog]``; ``[]`` on missing/unreadable/bad.

    A corrupt cache degrades to empty rather than raising — a bad sidecar must
    never brick ``/extension`` or ``discover`` (re-run ``discover --refresh``).
    """

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    blocks = raw.get("catalogs")
    if not isinstance(blocks, list):
        return []
    out: list[Catalog] = []
    for block in blocks:
        if isinstance(block, dict):
            catalog = Catalog.from_json(block)
            if catalog is not None:
                out.append(catalog)
    return out


def load_cached_catalog(agent_dir: str | os.PathLike[str]) -> list[Catalog]:
    """Read the cached catalogs under ``agent_dir`` (the TUI getter — sync, safe)."""

    return load_catalogs(cache_file_path(agent_dir))


# =====================================================================
# === Search / resolve (pure) ==========================================
# =====================================================================


def search_entries(catalogs: Iterable[Catalog], query: str | None) -> list[CatalogEntry]:
    """All entries matching ``query`` (case-insensitive substring on name/description).

    ``None``/empty query returns every entry. Order: catalog registration order,
    then entry order within each catalog (stable).
    """

    needle = (query or "").strip().lower()
    out: list[CatalogEntry] = []
    for catalog in catalogs:
        for entry in catalog.entries:
            if not needle:
                out.append(entry)
                continue
            haystack = f"{entry.name}\n{entry.description or ''}".lower()
            if needle in haystack:
                out.append(entry)
    return out


def select_catalogs(catalogs: Iterable[Catalog], catalog: str | None) -> list[Catalog]:
    """The catalogs a ``--catalog`` selector names (all of them for none / blank).

    A catalog is selected by its label, its location or the spec it was
    registered as (``registered_as``), case-insensitively — or by the local
    catalog FILE a path selector names (a relative one read from the current
    directory): ``source add --catalog catalog.json`` stores the absolute path, so
    ``--catalog catalog.json`` typed beside that file selects it (#131 round 3; it
    used to match nothing and suggest ``--refresh``), and so does the symlinked
    spelling of an absolute path ``source add`` stored resolved (review round 4).
    It reads the CACHE: a registered catalog not fetched yet is not selected here
    (:func:`location_matches_selector` asks the registered sources).
    """

    cats = list(catalogs)
    if not catalog or not catalog.strip():
        return cats
    wanted = catalog.strip().lower()
    named_file = _selector_file(catalog)
    out: list[Catalog] = []
    for cat in cats:
        if wanted in {
            cat.label().lower(),
            cat.location.lower(),
            (cat.registered_as or "").strip().lower(),
        } or (named_file is not None and _local_file(cat.location) == named_file):
            out.append(cat)
    return out


def _selector_file(selector: str) -> str | None:
    """The physical local file a ``--catalog`` selector (or a registered spec)
    names: a relative path read from the current directory, an absolute or ``~``
    path, or a ``file://`` URL — resolved, so ``<dir>/link/catalog.json`` and the
    ``<dir>/real/catalog.json`` that ``source add`` stored for it (or ``/tmp`` and
    ``/private/tmp`` on macOS) compare equal (#131 review round 4). URLs, git specs
    and blanks → ``None``."""

    anchored = anchor_catalog_location(selector)
    return _local_file(anchored if anchored is not None else selector)


def location_matches_selector(location: str, selector: str) -> bool:
    """Does a REGISTERED catalog location (a settings spec, not a cached catalog)
    match a ``--catalog`` selector? By the spec itself, case-insensitively, or by
    the local catalog file both name. A label cannot match here: a catalog's name
    is known only once it has been fetched (#131 review round 4)."""

    wanted = selector.strip().lower()
    if not wanted:
        return False
    if location.strip().lower() == wanted:
        return True
    named_file = _selector_file(selector)
    return named_file is not None and _selector_file(location) == named_file


def cached_copy(catalogs: Iterable[Catalog], location: str) -> Catalog | None:
    """The cached catalog recorded for a REGISTERED ``location``: the one cached
    under that location, or under the spec it was registered as
    (``registered_as``), or — a local file — the cached copy of the same physical
    file; ``None`` when the cache holds none (not fetched since it was registered,
    or the cache predates ``registeredAs``). An error row counts: it is what the
    last refresh recorded (#131 review round 4)."""

    loc = location.strip()
    loc_file = _local_file(loc)
    for cat in catalogs:
        if loc in (cat.location, cat.registered_as) or (
            loc_file is not None and _local_file(cat.location) == loc_file
        ):
            return cat
    return None


def resolve_entry(
    catalogs: Iterable[Catalog],
    name: str,
    *,
    catalog: str | None = None,
) -> tuple[CatalogEntry | None, list[CatalogEntry]]:
    """Resolve an exact ``name`` to one entry across catalogs.

    Returns ``(resolved, candidates)``: ``candidates`` is every entry whose name
    matches ``name`` case-insensitively (optionally narrowed to the catalog whose
    label, location or ``registered_as`` matches ``catalog`` — the last so a
    relative registration ``discover --refresh`` anchored is still selected by the
    spec as registered, #131 — or, for a LOCAL catalog, whose file a path
    ``catalog`` names, a relative one from the current directory: ``source add
    --catalog catalog.json`` stores the absolute path, and ``--catalog
    catalog.json`` typed beside that file selects it, #131 round 3, as does the
    symlinked spelling of a stored absolute path, review round 4 — see
    :func:`select_catalogs`); ``resolved`` is the single candidate when
    there is EXACTLY one, else :data:`None`. The caller REFUSES an ambiguous
    resolution (``resolved is None and len(candidates) > 1``) with the candidate
    list — never a silent first-match (ADR-0188).
    """

    target = name.strip().lower()
    candidates: list[CatalogEntry] = []
    for cat in select_catalogs(catalogs, catalog):
        for entry in cat.entries:
            if entry.name.strip().lower() == target:
                candidates.append(entry)
    resolved = candidates[0] if len(candidates) == 1 else None
    return resolved, candidates


#: Archive suffixes pip installs as a FILE (pip's ``ARCHIVE_EXTENSIONS`` plus
#: ``.whl``; uv's ``looks_like_archive`` reads the same set). A BARE catalog
#: ``source`` ending in one (no separator) is a relative path — the
#: ``acme_notes-1.4.0-py3-none-any.whl`` an older ``extension index --relative``
#: emitted is exactly that shape (#131). One test row per suffix pins this tuple
#: (round-2 review: a three-suffix list passed; round-3 review: without the lzip /
#: lzma three, ``x.tar.lz`` from an https catalog went to uv as a cwd file).
_ARCHIVE_SUFFIXES = (
    ".whl",
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar",
    ".tar.bz2",
    ".tbz",
    ".tar.xz",
    ".txz",
    ".tlz",
    ".tar.lz",
    ".tar.lzma",
)
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
#: The prefixes that make a TYPED target path-shaped before anything else is asked
#: (:func:`source_looks_like_path`) — pi's ``parseSource`` reads ``./https://x`` as a
#: local path for the same reason (#131).
_PATH_PREFIXES = ("./", "../", ".\\", "..\\", "/", "\\", "~")
#: The only RELATIVE path spellings a catalog ``source`` may use (ADR-0255 (C)).
_RELATIVE_PREFIXES = ("./", "../", ".\\", "..\\")
#: pip's own ``strip_extras`` pattern (``pip._internal.req.constructors``): a
#: trailing ``[extra,...]`` on a path is installer syntax, not part of the file name
#: (pip then right-strips the path part — :func:`split_path_extras` does too).
_EXTRAS_RE = re.compile(r"^(.+)(\[[^\]]+\])$")
#: A PEP 508 direct reference, ``name[extras] @ <target>`` (``<target>`` up to
#: whitespace; whatever follows — a marker — rides along untouched).
_DIRECT_REF_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\s*(?:\[[^\]]*\])?)"
    r"\s*@\s*(?P<url>\S+)(?P<tail>.*)$",
    re.DOTALL,
)
#: A URL scheme at the start of a source (``https:``, ``file:``, ``git+ssh:`` …).
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
#: scp-style git, ``<user>@<host>:<path>`` with ANY user — ``git@github.com:o/r.git``,
#: ``deploy@git.corp:team/ext.git`` — no ``://``, no whitespace: what
#: ``classify_target`` routes as git and ``_normalize_git_spec`` rewrites to
#: ``git+ssh://<user>@<host>/<path>`` (:func:`is_scp_git`).
_SCP_RE = re.compile(
    r"^[A-Za-z0-9._~-]+@(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?):(?!//)\S+$"
)
#: What every shape refusal tells the catalog author (ADR-0255 §2).
ACCEPTED_SOURCE_FORMS = (
    "A catalog source must be one of: a package name, optionally with [extras] and a "
    "version specifier ('acme-notes==1.4.0'); an absolute URL, its scheme in "
    "lowercase — https://, http://, git+<scheme>://, git://, ssh://, scp-style "
    "user@host:path, file:/// or file://localhost/ (no %-escapes in a file URL's "
    "path) — or 'name @ <an https://, http://, git+ or file:/// URL>' (a git "
    "repository as 'name @ git+…'), passed on unchanged; an absolute path or a ~ "
    "path; or a path starting with ./ or ../ (optionally followed by [extras]), which "
    "resolves beside a local catalog file."
)


def is_scp_git(spec: str) -> bool:
    """True for scp-style git, ``<user>@<host>:<path>`` — any user, not only ``git``.

    Review round 7: round 6 matched only ``git@``, so ``alice@h.example:o/r.git``
    and ``deploy@git.corp:team/ext.git`` read as PEP 508 direct references
    (``alice @ h.example:o/r.git``) and went raw to the backend, which read the part
    after ``@`` as a path in the cwd; ``source add`` refused them. A host spelled
    like a URL scheme (``name@file:x``) is not one: that stays the ``name @ file:x``
    reference the resolver refuses.
    """

    m = _SCP_RE.match(spec.strip())
    return m is not None and m.group("host").lower() not in _KNOWN_SCHEMES


def direct_reference_url(spec: str) -> str | None:
    """The URL of a PEP 508 direct reference ``name[extras] @ <url> [; marker]``, or
    ``None`` — scp-style ``<user>@host:path`` is a git remote, not
    ``<user> @ host:path`` (:func:`is_scp_git`). The installer's git helpers use it
    so ``name @ git+https://…`` keeps its name and is never given a second ``git+``
    (#131 review round 5)."""

    s = spec.strip()
    if is_scp_git(s):
        return None
    m = _DIRECT_REF_RE.match(s)
    return m.group("url") if m is not None else None


def _starts_like_a_path(source: str) -> bool:
    """``./`` ``../`` ``/`` ``~`` (or the Windows ``\\`` / drive forms), ``.``, ``..``."""

    return (
        source in (".", "..") or source.startswith(_PATH_PREFIXES) or bool(_DRIVE_RE.match(source))
    )


def _is_url_spec(source: str) -> bool:
    """A URL / VCS spec: a scheme (``file:`` included), ``git+``, scp-style ``git@``,
    or a ``name @ <url>`` direct reference. Asked only AFTER :func:`_starts_like_a_path`.
    """

    low = source.lower()
    return (
        "://" in source
        or low.startswith(("git+", "git@", "file:"))
        or _DIRECT_REF_RE.match(source) is not None
    )


def split_path_extras(spec: str) -> tuple[str, str]:
    """``./x.whl[feature]`` → ``("./x.whl", "[feature]")``; no extras → ``(spec, "")``.

    The same split pip makes before it looks for the file (``strip_extras``), so
    the existence check runs on what pip will open and the extras ride along —
    including pip's right-strip of the path part: ``./x.whl [feature]`` is
    ``./x.whl`` for pip 26.2.1, never ``./x.whl `` (#131 review round 4; uv
    rejects the spaced spelling outright; for a catalog path aelix hands either
    backend ``name[feature] @ file:///<abs>/x.whl``, review round 5).
    """

    m = _EXTRAS_RE.match(spec)
    return (m.group(1).rstrip(), m.group(2)) if m else (spec, "")


def source_looks_like_path(source: str) -> bool:
    """True when a TYPED install target is shaped like a local file or directory.

    ``classify_target`` asks it (through ``_path_extras``) whether a target the user
    typed with pip's trailing ``[extras]`` — ``./x.whl[feature]`` — names a file, so
    ``foo[bar]`` beside a ``./foo`` stays a package as it does for pip. Decided in
    this order:

    1. it STARTS like a path — ``./`` ``../`` ``/`` ``~`` (or the Windows ``\\`` /
       drive forms), ``.`` or ``..`` — even when a ``://`` follows;
    2. otherwise a URL or VCS spec (a scheme — ``file:`` included —, ``git+``,
       ``git@``, ``name @ <url>``) is not a path, though it contains ``/``;
    3. otherwise it contains a path separator, or (extras aside) ends in an archive
       suffix pip installs as a file.

    A catalog entry's ``source`` is NOT decided here: :func:`resolve_entry_target`
    accepts a fixed list of forms and refuses the rest (#131, ADR-0255).
    """

    s = source.strip()
    if not s:
        return False
    if _starts_like_a_path(s):
        return True
    if _is_url_spec(s):
        return False
    if "/" in s or "\\" in s:
        return True
    return split_path_extras(s)[0].lower().endswith(_ARCHIVE_SUFFIXES)


def _catalog_base_dir(location: str | None) -> tuple[Path | None, str]:
    """The directory a relative entry ``source`` resolves against → ``(dir, why_not)``.

    Only a LOCAL catalog file has one: a bare absolute path or a ``file://`` URL →
    the PHYSICAL directory of that file (``Path.resolve()``, symlinks followed), the
    same directory ``extension index --relative`` measures from, so the two can
    never disagree about a symlinked catalog. When there is none, ``dir`` is
    ``None`` and ``why_not`` says why, in words that are true of that location:
    an ``https`` or git catalog is fetched, not read from a directory; a location
    that is itself relative names no fixed directory (``discover --refresh``
    records it absolute — see :func:`anchor_catalog_location`).
    """

    if not location or not location.strip():
        return None, "the catalog's location is unknown"
    loc = location.strip()
    low = loc.lower()
    if _is_git_location(loc) or low.startswith(("http://", "https://")):
        return None, f"the catalog is fetched from '{loc}', not read from a local directory"
    try:
        path = _file_url_to_path(loc) if low.startswith("file://") else Path(loc).expanduser()
    except (CatalogError, RuntimeError):
        return None, f"the catalog location '{loc}' does not name a local file"
    if not path.is_absolute():
        return None, (
            f"the catalog's cached location '{loc}' is itself a relative path, so it "
            "names no fixed directory — run 'aelix extension discover --refresh' to "
            "record where it is, or register the catalog by its absolute path"
        )
    try:
        return path.resolve().parent, ""
    except (OSError, RuntimeError, ValueError):
        return path.parent, ""


def _local_file(location: str | None) -> str | None:
    """A LOCAL catalog location (bare absolute path or ``file://`` URL) → its
    physical file, normalised for comparison; anything else → ``None``."""

    if not location:
        return None
    loc = location.strip()
    low = loc.lower()
    if _is_git_location(loc) or low.startswith(("http://", "https://")):
        return None
    try:
        path = _file_url_to_path(loc) if low.startswith("file://") else Path(loc).expanduser()
        if not path.is_absolute():
            return None
        return os.path.normcase(str(path.resolve()))
    except (CatalogError, OSError, RuntimeError, ValueError):
        return None


def anchor_catalog_location(location: str) -> str | None:
    """A bare RELATIVE catalog path → that path from the cwd, absolute; else ``None``.

    ``source add --catalog`` stores a path absolute, but a hand-edited settings file
    can still hold ``catalog.json``. ``discover --refresh`` reads such a location
    from the current directory anyway; anchoring it there ONCE, at refresh, and
    caching the absolute form gives its relative entries the directory of the file
    that was actually read (ADR-0255). URLs, git specs, ``~`` and absolute paths
    return ``None``. The join is not normalized, so ``../x`` keeps filesystem
    semantics through a symlinked cwd.
    """

    loc = location.strip()
    if not loc:
        return None
    low = loc.lower()
    if _is_git_location(loc) or "://" in loc or low.startswith(("http:", "https:", "file:")):
        return None
    try:
        path = Path(loc).expanduser()
    except RuntimeError:
        return None
    if path.is_absolute():
        return None
    return str(Path.cwd() / path)


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def _is_absolute_file_url(url: str) -> bool:
    """``file:///…`` or ``file://localhost/…`` — a file URL that names no cwd.

    Byte-exact since review round 7: the host is empty or ``localhost`` in
    lowercase (uv 0.11 read ``file://LOCALHOST/<abs>`` and ``file://LocalHost/…``
    as ``<cwd>/LOCALHOST/<abs>`` and installed a cwd decoy), and the path holds no
    ``%`` (uv decodes ``%23`` to ``#`` and cuts there, installing a sibling; one rule
    for every escape rather than a list of the dangerous ones). The scheme's own
    case is refused earlier (:func:`_non_lowercase_scheme`).
    """

    if not url.startswith(("file:///", "file://localhost/")):
        return False
    return "%" not in url.split("#", 1)[0]


def _has_host(url: str) -> bool:
    try:
        return bool(urlparse(url).netloc)
    except ValueError:
        return False


def _is_absolute_reference_url(url: str) -> bool:
    """A URL pip and uv fetch without consulting the cwd, as PEP 508 allows after
    ``name @``: ``https://host/…``, ``http://host/…``, ``git+<https|http|ssh|git>://host/…``,
    ``git+file:///…``, ``file:///…``, ``file://localhost/…``."""

    low = url.lower()
    if low.startswith("git+"):
        inner = url[4:]
        if inner.lower().startswith("file:"):
            return _is_absolute_file_url(inner)
        return inner.lower().startswith(("https://", "http://", "ssh://", "git://")) and _has_host(
            inner
        )
    if low.startswith("file:"):
        return _is_absolute_file_url(url)
    return low.startswith(("https://", "http://")) and _has_host(url)


def _is_absolute_source_url(source: str) -> bool:
    """A whole ``source`` that is an absolute URL: everything
    :func:`_is_absolute_reference_url` takes, plus the git transports
    ``classify_target`` routes as git on their own — ``git://host/…``,
    ``ssh://host/…`` and scp-style ``<user>@host:path`` (:func:`is_scp_git`)."""

    if _is_absolute_reference_url(source):
        return True
    low = source.lower()
    if low.startswith(("git://", "ssh://")):
        return _has_host(source)
    return is_scp_git(source)


def _is_bare_archive_name(body: str) -> bool:
    """A bare archive FILE name — any name :func:`scan_artifacts` lists, as an older
    ``index --relative`` wrote it: no separator, ending in an archive suffix.

    Spaces and any other file-name character are accepted (round-3 review: a
    character whitelist refused the ``team notes-1.0.tar.gz`` the old generator
    emitted and uv installs). A name that also reads as a URL (a scheme, ``C:``) or
    as a ``name @ …`` direct reference is decided by those rules instead, so the
    round-2 refusals (``file:x.whl``, ``name @ x.whl``) stand.
    """

    if "/" in body or "\\" in body:
        return False
    if _SCHEME_RE.match(body) or _DIRECT_REF_RE.match(body):
        return False
    return body.lower().endswith(_ARCHIVE_SUFFIXES)


def _is_path_form(source: str) -> bool:
    """ADR-0255 (C): an absolute path, a ``~`` path, a ``./`` ``../`` (or ``.\\``
    ``..\\``) relative path, or a bare archive file name — extras aside."""

    body = split_path_extras(source)[0]
    if body.startswith(_RELATIVE_PREFIXES) or body.startswith("~"):
        return True
    try:
        if Path(body).is_absolute():
            return True
    except (OSError, ValueError):  # pragma: no cover — a NUL in the string
        return False
    return _is_bare_archive_name(body)


def spelled_as_path(source: str) -> bool:
    """True when a catalog or install-record spec (:class:`CatalogSpec`) is SPELLED as
    a local path: ADR-0255's path form (C) — an absolute path, a ``~`` path, a ``./``
    ``../`` (``.\\`` ``..\\``) path or a bare archive file name, extras aside —,
    anything else that starts like one (``.``, ``..``, a rooted ``\\x``, a drive
    ``C:x``), and a string with a path separator (``/`` or ``\\``, on every platform)
    that is no URL, ``name @ <url>`` reference, git remote or PEP 508 requirement
    (``a/b``, ``local-ext/``, ``sub\\ext``).

    A string that parses as a PEP 508 requirement with no URL is a package, whatever
    ``/`` its marker holds (``x; platform_version == "…/RELEASE_ARM64"`` — review round
    2: the separator test refused it as a relative path). One with a version
    specifier, a marker or a URL is asked BEFORE the bare-archive test (review round 3,
    Codex: ``path-probe==1.0+vendor.whl``, a local version ending in ``.whl``, was
    refused as a relative path): ``x==1.0+v.whl`` and ``x.whl; python_version>"3"``
    are packages. A bare token — a name, optionally with ``[extras]`` — ending in an
    archive suffix stays a path spelling (``x.whl``, ``x.tar.gz``,
    ``pkg-1.0-py3-none-any.whl``, ``x.whl[feature]``: pip and uv open those as files,
    §2 (C) "extras aside"), and so stays refused as relative.

    The SHAPE is read from the string without its surrounding whitespace (review
    round 3); whether a path is absolute is never decided here — the installer's
    ``_names_no_cwd`` reads the exact string (``' /abs/x.whl'`` is spelled as a path
    and is relative as written). :class:`CatalogSpec` keeps a path spelling exactly
    as given and strips every other one, so a check and the installation read one
    string.

    Decided from the string alone, never from what exists in the current directory
    (#405): the installer's ``classify_target`` asks it instead of the cwd for a
    :class:`CatalogSpec`, so a cwd entry named like a recorded package no longer turns
    that package into a local path. A git remote (``git@h:o/r``, scp-style
    ``<user>@host:path`` with any user), a URL and a ``name @ <url>`` reference are not
    paths here, whatever ``/`` they hold. Whatever this calls a path must be absolute
    to be installed — the resolver refuses a relative one with no local catalog to
    place it beside, and so does the installer for a :class:`CatalogSpec`.
    """

    # The shape only (review round 3): the absolute-or-relative test that follows a
    # path spelling reads the exact string (round 2: a stripped absolute test passed
    # ``' /abs/x.whl'`` and the installer resolved ``<cwd>/' /abs/x.whl'``).
    s = source.strip()
    if not s:
        return False
    if _starts_like_a_path(s):
        return True
    if _is_qualified_requirement(s):
        return False
    if _is_path_form(s):
        return True
    if _is_url_spec(s) or is_scp_git(s):
        return False
    if _is_requirement_without_url(s):
        return False
    return "/" in s or "\\" in s


def _parsed_requirement(source: str) -> Requirement | None:
    """``packaging``'s :class:`~packaging.requirements.Requirement`, or ``None``."""

    from packaging.requirements import InvalidRequirement, Requirement

    try:
        return Requirement(source)
    except InvalidRequirement:
        return None


def _is_requirement_without_url(source: str) -> bool:
    """A PEP 508 requirement with no direct reference — markers and all."""

    req = _parsed_requirement(source)
    return req is not None and req.url is None


def _is_qualified_requirement(source: str) -> bool:
    """A PEP 508 requirement with a version specifier, a marker or a URL — more than a
    bare name (``[extras]`` alone does not count: ``x.whl[feature]`` is a file to pip
    and uv). Asked before :func:`spelled_as_path`'s bare-archive test (#405 review
    round 3)."""

    req = _parsed_requirement(source)
    return req is not None and (
        bool(req.specifier) or req.marker is not None or req.url is not None
    )


def is_qualified_package_requirement(source: str) -> bool:
    """A PEP 508 requirement with NO URL and a version specifier or a marker
    (``probe405==1.0+vendor.git``, ``x; python_version>"3"``) — a package, whatever its
    spelling ends in. The installer's ``classify_target`` asks it for a
    :class:`CatalogSpec` BEFORE the git-URL shapes (#405 review round 4, Codex: a local
    version ending in ``.git`` was taken for a git URL by the ``.git``-suffix test and
    refused when passed as the package it is). A bare name (``foo.git``) is not
    qualified and keeps the ``.git`` reading (``foo.git[x]`` never had it)."""

    req = _parsed_requirement(source.strip())
    return (
        req is not None
        and req.url is None
        and (bool(req.specifier) or req.marker is not None)
    )


def _is_plain_requirement(source: str) -> bool:
    """ADR-0255 (A): a PEP 508 requirement with NO direct reference and no marker —
    a name, optionally with ``[extras]`` and a version specifier."""

    from packaging.requirements import InvalidRequirement, Requirement

    try:
        req = Requirement(source)
    except InvalidRequirement:
        return False
    return req.url is None and req.marker is None


class CatalogSpec(str):
    """A source string the catalog resolver (or an install record) chose — a package
    requirement or an absolute URL — as :func:`resolve_entry_target` returns it.

    It is the string itself (a ``str`` subclass: every string use is unchanged) and
    it carries its ORIGIN: the installer runs a :class:`CatalogSpec` or a
    :class:`ResolvedPath` in aelix's installer directory, never the caller's cwd,
    whatever the caller passes (#392 review round 4 — ``install_extension(
    resolve_entry_target(entry))`` from the Python API ran in the caller's cwd,
    where a cloned repository's ``uv.toml`` chose a dependency). A plain ``str`` is a
    source the user typed, and its installer runs where it was typed. Any string
    operation (``strip``, slicing, ``+``) returns a plain ``str``: the origin is read
    where the install starts, from the object the resolver returned.

    Its KIND is read from its spelling alone, never from the cwd (#405): the
    installer's ``classify_target`` used to ask the process cwd whether the string
    existed there, so ``update`` of the package ``local-ext`` installed a cwd
    directory or symlink of that name. A package requirement is a package, an absolute
    path a path, a URL the URL it is; one spelled as a relative path is refused, and
    so is a ``kind`` a caller passes for it that its spelling does not give
    (``verify_and_pin``, ``build_pip_args`` — review round 2).

    Surrounding whitespace is normalised ONCE, here, where a source is wrapped (#405
    review round 3): a package requirement, URL or git remote is stripped, so every
    check and the installation read the same stripped string — a typed ``aelix
    extension install 'git+file:///repo '`` records the space, and ``update`` wraps
    that record here. A source SPELLED AS A PATH keeps its exact string: a file name
    may end in a space (``<dir>/trusted `` is a real directory an older record holds,
    §14), and a leading space makes it relative, which the installer refuses rather
    than resolve against the cwd."""

    __slots__ = ()

    def __new__(cls, value: object = "") -> CatalogSpec:
        text = str(value)
        stripped = text.strip()
        if stripped != text and not spelled_as_path(stripped):
            text = stripped
        return super().__new__(cls, text)


@dataclass(frozen=True)
class ResolvedPath:
    """A path source as the resolver placed it: the file or directory, and pip's
    ``[extras]`` — two values, split ONCE, here (#131 round 3).

    ``discover install`` hands this object to the installer, which installs,
    hashes, stages and pins ``path`` and puts ``extras`` back only on the argv. It
    used to get ``f"{path}{extras}"`` and split it again with its own rule, which
    preferred a sibling LITERALLY named ``x.whl[feature]`` (a symlink to another
    wheel) over the ``x.whl`` checked here — the Resolved line named one artifact
    and another was installed and pinned (round-3 review, P1). ``str()`` is
    ``path`` + ``extras`` as the user reads them (the Resolved and Install lines, the
    install record); the installer gets :meth:`installer_arg`, a ``file://`` URI
    (review round 5).
    """

    path: str
    extras: str = ""

    def __str__(self) -> str:
        return f"{self.path}{self.extras}"

    def installer_arg(self) -> str:
        """What pip and uv receive: the path as a percent-encoded absolute ``file://``
        URI (``Path.as_uri()``), never a bare path string (#131 review round 5).

        A path string is parsed AGAIN by the backend, differently from the
        filesystem: uv cuts at a ``#`` (a URL fragment) and strips an empty ``[]``, a
        ``[x]`` group and trailing whitespace; pip also strips trailing whitespace and
        a ``[x]`` group, and splits at ``;`` (a marker) — each opened ANOTHER path
        than the one resolved (uv 0.11.19, pip 26.2.1:
        ``.omc/probes/131-live/fix6/uri-matrix.txt``). The URI carries ``[`` ``]``,
        spaces, ``;``, ``%``, ``?``, a backslash and non-ASCII literally to both.
        Extras cannot ride on an unnamed URL (pip opens ``file:///x.whl[feature]``
        as a file of that name), so with extras the argument is PEP 508's
        ``name[extras] @ file:///…``, the name read from the artifact
        (:func:`local_project_name`). A ``#`` survives no spelling on uv — it decodes
        ``%23`` and cuts there too — so a path holding one is refused, here and by
        :func:`resolve_entry_target`.
        """

        if "#" in self.path:
            raise CatalogError(
                f"local path '{self.path}' contains '#', which uv reads as the start of "
                "a URL fragment in every spelling (a bare path, or %23 in a file:// "
                "URI) and would open a different path — rename it"
            )
        uri = Path(self.path).as_uri()
        if not self.extras:
            return uri
        name = local_project_name(Path(self.path))
        if name is None:
            raise CatalogError(
                f"local path '{self.path}' is asked for the extras {self.extras}, but "
                "aelix cannot read its project name (from a wheel or sdist file name, "
                "or a directory's pyproject.toml [project] name). aelix hands a local "
                "path to the installer as a file:// URI, and extras on a URI need the "
                "project's name ('name[extras] @ file:///…'), so it refuses rather "
                "than guess one — drop the extras, or give the project a "
                "[project] name"
            )
        return f"{name}{self.extras} @ {uri}"


def resolved_path_from_installer_arg(spec: str) -> ResolvedPath | None:
    """The :class:`ResolvedPath` an :meth:`ResolvedPath.installer_arg` string names —
    ``file:///…`` or ``name[extras] @ file:///…`` — or ``None`` for any other string.

    An install record keeps that string since review round 7, so ``extension
    update`` re-installs the path through the same URI hand-off ``discover install``
    used, never a bare path string the backend parses again (verify6 B2: a recorded
    ``<dir>/trusted[]`` or ``<dir>/trusted `` re-installed the stripped sibling
    ``trusted`` on update). The path is the URI's, percent-decoded
    (``url2pathname``); the extras are the ones written before ``@``.
    """

    s = spec.strip()
    extras = ""
    m = _DIRECT_REF_RE.match(s)
    if m is not None:
        if m.group("tail").strip():
            return None
        name = m.group("name")
        extras = name[name.index("[") :].strip() if "[" in name else ""
        s = m.group("url")
    if not s.startswith("file:///"):
        return None
    path = urllib.request.url2pathname(urlsplit(s).path)
    return ResolvedPath(path, extras)


#: A PEP 508 project name.
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def local_project_name(path: Path) -> str | None:
    """The project name a local artifact declares, for ``name[extras] @ file:///…``:
    a wheel's file name (PEP 427 ``{name}-{version}-…``), an sdist's
    (``{name}-{version}`` + an archive suffix) or a directory's ``pyproject.toml``
    ``[project] name``; ``None`` when there is none to read. pip and uv check it
    against the metadata they build, so a wrong one fails the install loudly."""

    import tomllib

    try:
        if path.is_dir():
            data = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
            project = data.get("project")
            declared = project.get("name") if isinstance(project, dict) else None
            name = declared.strip() if isinstance(declared, str) else None
        else:
            low = path.name.lower()
            if low.endswith(".whl"):
                name = path.name.split("-", 1)[0]
            else:
                suffix = next((s for s in _ARCHIVE_SUFFIXES if low.endswith(s)), None)
                stem = path.name[: -len(suffix)] if suffix else ""
                name = stem.rsplit("-", 1)[0] if "-" in stem else None
    except (OSError, ValueError):  # unreadable / undecodable / bad TOML
        return None
    return name if name and _PROJECT_NAME_RE.match(name) else None


def resolve_entry_source(entry: CatalogEntry) -> tuple[CatalogSpec, bool]:
    """:func:`resolve_entry_target` as ``(spec, is_path)`` — ``spec`` as shown (a path
    with its extras joined; the installer gets :meth:`ResolvedPath.installer_arg`) —
    for callers that only show it.

    ``spec`` is a :class:`CatalogSpec` either way, so it keeps its catalog ORIGIN:
    handed to ``install_extension`` it runs in aelix's installer directory, never the
    caller's cwd (#392 review round 5 — it was a plain ``str``, which the installer
    takes for a typed source and ran where the caller stood, where a cloned
    repository's ``uv.toml`` chose the package). A path comes back absolute, as the
    resolver placed it; to install one, :func:`resolve_entry_target` is still the
    call to make — its :class:`ResolvedPath` reaches the installer as a ``file://``
    URI, which no backend parses again (#131)."""

    target = resolve_entry_target(entry)
    spec = target if isinstance(target, CatalogSpec) else CatalogSpec(str(target))
    return spec, isinstance(target, ResolvedPath)


def resolve_entry_target(entry: CatalogEntry) -> CatalogSpec | ResolvedPath:
    """What an entry installs from — a :class:`CatalogSpec` (a spec string that
    carries its catalog origin, #392 review round 4), or a :class:`ResolvedPath`;
    :class:`CatalogError` refuses.

    #131 (ADR-0255). The installer reads anything relative from the PROCESS working
    directory: ``classify_target`` calls a TYPED target a path only if it exists
    there (a :class:`CatalogSpec` by its spelling alone since #405), and pip / uv
    open ``./x``, ``file:x``, ``x.whl`` there — uv also ``name @ ./x`` and
    ``name @ x`` (measured). Handing it an entry's raw
    ``source`` therefore installed whatever the cwd held. So a source is accepted
    in exactly these forms, and every other one is REFUSED, never rewritten:

    * (A) a package requirement without a direct reference — a name, optionally
      with ``[extras]`` and a version specifier → unchanged, to the index. Unless
      it has NO version specifier and a file or directory named like the package
      (its name, extras aside) sits beside a LOCAL catalog: the entry is then
      ambiguous (the local copy, or the package?) and is refused;
    * (B) an absolute URL — ``https://`` / ``http://`` / ``git+<scheme>://`` (and
      ``git://``, ``ssh://``, scp-style ``<user>@host:path`` with any user),
      ``file:///`` or ``file://localhost/`` (the host byte-exact, no ``%`` in the
      path) — or ``name @ <an https, http, git+ or absolute file URL>``, its scheme
      in lowercase → passed through UNCHANGED, fragments (``#sha256=``,
      ``#subdirectory=``, ``#egg=``) and extras exactly as written;
    * (C) a path — an absolute path, a ``~`` path, or a relative path that STARTS
      with ``./`` or ``../`` (``.\\`` ``..\\`` too), optionally followed by
      ``[extras]``; a bare archive file name (``x-1.0-py3-none-any.whl``, what an
      older ``index --relative`` wrote) is a relative path too. It must exist and
      is passed on ABSOLUTE and ``Path.resolve()``-d (``link/..`` as the OS opens it:
      link followed on POSIX, lexical on Windows), as a :class:`ResolvedPath` — the
      path and the extras as two values, never re-split downstream. A relative one resolves
      against the PHYSICAL directory of the LOCAL catalog file it came from —
      never the cwd — and is refused in an https or git catalog (or a cached one
      whose location is itself relative). An absolute or ``~`` path does not
      depend on the catalog's location, so it is taken from any catalog, ``~``
      being the installing user's home.

    Refused: ``name @ <relative path or bare word>`` (uv reads it from the cwd), a
    ``file:`` URL that is not ``file:///`` / ``file://localhost/`` (``file:x``,
    ``file:``, ``file:#subdirectory=x``, ``name @ file:x``), a source starting with
    ``-`` (the installer would read an option), a relative path not starting with
    ``./`` or ``../``, and anything else not listed. Every refusal names the entry
    and the catalog; a shape refusal also lists the accepted forms
    (:data:`ACCEPTED_SOURCE_FORMS`). Paths are quoted by hand, not with ``!r``,
    which doubles a Windows path's backslashes (#208). The returned type tells the
    caller which branch decided; a :class:`CatalogSpec` is never re-read as a path
    from the cwd (#405 — before it, ``discover install`` had to refuse a package
    spec that named a cwd entry, and ``update`` installed that entry). A
    :class:`CatalogSpec` is the source with its surrounding whitespace dropped — the
    string every check above read (#405 review round 2; since round 3
    :class:`CatalogSpec` itself strips a source that is not spelled as a path).

    Round 3 adds a refusal of a package name ending in ``.git`` (the installer routes
    it as a git URL, ``git+acme.git``, which no backend can fetch). Review round 5
    closes the class "resolved right, then re-parsed by the backend": a path reaches
    the installer as a ``file://`` URI (:meth:`ResolvedPath.installer_arg`), so the
    round-3 refusal of a resolved name ending in ``[…]`` is gone (the URI carries the
    brackets) and a resolved path holding ``#`` is refused instead (uv cuts there in
    every spelling); a URL scheme not written in lowercase is refused (uv takes
    ``FILE:`` for a relative path segment); and ``name @ <http(s) URL ending in .git>``
    is refused — the two backends disagree on it (uv clones it, pip downloads it as
    an archive and fails) — with the ``name @ git+…`` spelling to use instead.
    Review round 6 (the final round, owner decision): an scp-style git source with
    any user is git again (:func:`is_scp_git`); a ``file:`` URL whose host is not
    empty or ``localhost`` byte-exact (``file://LOCALHOST/…``), or whose path holds a
    ``%``-escape, is refused (uv read both as other paths); every refusal message says
    what is true of its spelling (:func:`_url_problem`).

    The guarantee (ADR-0255 §12): a TRUSTED catalog's source is never resolved
    against the cwd, nor handed over as a string the installer reads from there,
    and its installer runs in aelix's installer directory, where uv reads no project
    configuration (#392); a hostile catalog is outside it, so
    adversarial spellings are refused only where a simple rule does it.
    """

    source = entry.source
    raw = source.strip()
    location = entry.catalog_location
    where = entry.catalog_name or location or "?"
    who = f"catalog entry '{entry.name}' (catalog '{where}')"

    def refuse(why: str) -> CatalogError:
        return CatalogError(
            f"{who}: source '{source}' {why}. Refusing it. {ACCEPTED_SOURCE_FORMS}"
        )

    if not raw:
        raise refuse("is empty")
    if raw.startswith("-"):
        raise refuse("starts with '-', so the installer would read it as an option")
    if _is_path_form(raw):
        body, extras = split_path_extras(raw)
        placed = ResolvedPath(str(_place_path(body, raw, who, location)), extras)
        try:
            placed.installer_arg()  # the hand-off must exist before consent is asked
        except CatalogError as exc:
            raise CatalogError(f"{who}: source '{raw}': {exc}. Refusing it.") from exc
        return placed
    direct = None if is_scp_git(raw) else _DIRECT_REF_RE.match(raw)
    url = direct.group("url") if direct is not None else raw
    upper = _non_lowercase_scheme(url)
    if upper is not None:
        raise refuse(
            f"writes the URL scheme '{upper}' with upper-case letters — write it in "
            f"lowercase ('{upper.lower()}:'). aelix passes a URL on exactly as written, "
            "and uv does not read an upper-case scheme as one ('FILE:///x' is a path "
            "segment 'FILE:' under the current directory to it)"
        )
    if _is_absolute_source_url(raw):
        return CatalogSpec(raw)
    if direct is not None:
        if _is_absolute_reference_url(url):
            if url.lower().startswith(("https://", "http://")) and _url_path_is_git(url):
                name = direct.group("name").strip()
                raise refuse(
                    f"is a direct reference to '{url}', a git repository URL without "
                    "'git+' — uv clones it while pip downloads it as an archive and "
                    f"fails; write '{name} @ git+{url}'"
                )
            return CatalogSpec(raw)
        raise refuse(
            f"is a direct reference to '{url}', which {_url_problem(url, after_name=True)}"
        )
    if _SCHEME_RE.match(raw):
        raise refuse(_url_problem(raw))
    if _is_plain_requirement(raw):
        base, _ = _catalog_base_dir(location)
        # The NAME as written (extras aside) — only for a requirement with no version
        # specifier: ``local-ext[extra]`` beside a ``./local-ext`` is ambiguous too,
        # but ``local-ext==1.0`` says "package", whatever sits beside the catalog
        # (review round 5: a neighbouring file literally named ``local-ext==1.0``
        # refused it).
        named = _unversioned_requirement_name(raw)
        if base is not None and named is not None and _exists(base / named[0]):
            meant = f"./{named[0]}{named[1]}"
            raise CatalogError(
                f"{who}: source '{source}' reads as a package name, but "
                f"'{base / named[0]}' exists beside the catalog — write '{meant}' if "
                "the entry means that local copy. Refusing rather than guessing which "
                "one to install."
            )
        # Only a bare name: one with a version specifier (``x==1.0+vendor.git``) is a
        # package to the installer too (#405 review round 4).
        if raw.lower().endswith(".git") and not is_qualified_package_requirement(raw):
            raise refuse(
                "reads as a package name, but the installer takes a name ending in "
                "'.git' for a git URL (it would run 'git+" + raw + "', which no "
                "backend can fetch) — write the repository's absolute git URL"
            )
        return CatalogSpec(raw)
    raise refuse(
        "is neither a package name, an absolute URL nor a path in an accepted form "
        "(absolute, ~, or starting with ./ or ../)"
    )


#: The schemes a source may use (``git+`` any of them): an upper-case spelling of one
#: is refused, never passed on (review round 5).
_KNOWN_SCHEMES = ("file", "http", "https", "git", "ssh")


def _non_lowercase_scheme(url: str) -> str | None:
    """The scheme of ``url`` when it is one aelix knows (``file``, ``http(s)``,
    ``git``, ``ssh``, ``git+<any>``) written with an upper-case letter; else ``None``.
    A one-letter scheme is a Windows drive (``C:``), not a URL."""

    m = _SCHEME_RE.match(url)
    if m is None or len(m.group(0)) <= 2:
        return None
    scheme = m.group(0)[:-1]
    low = scheme.lower()
    if low != scheme and (low in _KNOWN_SCHEMES or low.startswith("git+")):
        return scheme
    return None


def _url_path_is_git(url: str) -> bool:
    """An http(s) URL whose PATH ends in ``.git`` (a trailing ``/`` or ``@<rev>``
    aside) — the test the installer's ``classify_target`` routes as git."""

    try:
        path = urlparse(url.lower()).path.rstrip("/")
    except ValueError:
        return False
    if path.endswith(".git"):
        return True
    head, sep, _rev = path.rpartition("@")
    return bool(sep) and head.rstrip("/").endswith(".git")


def _url_problem(url: str, *, after_name: bool = False) -> str:
    """Why a URL-shaped ``url`` (a whole source, or — ``after_name`` — the target of
    ``name @``) is not accepted, in words true of that spelling (review round 5:
    ``file:/abs`` was said to be read from the current directory; review round 7:
    ``ftp://h/x`` and ``name @ ssh://h/x`` were said to need a host they have)."""

    low = url.lower()
    if low.startswith("git+file:"):
        return _file_url_problem(url[len("git+") :], "git+file")
    if low.startswith("file:"):
        return _file_url_problem(url, "file")
    m = _SCHEME_RE.match(url)
    if m is None:
        return "is not an absolute URL — uv reads it from the current directory"
    scheme = m.group(0)[:-1].lower()
    whole_only = ("git", "ssh")
    known = ("https", "http") if after_name else ("https", "http", *whole_only)
    git_transport = scheme.startswith("git+") and scheme[len("git+") :] in (
        "https",
        "http",
        "ssh",
        "git",
    )
    if scheme in known or git_transport:
        return f"is not an absolute URL — '{scheme}:' needs '//' and a host after it"
    if after_name and scheme in whole_only:
        return (
            f"uses the URL scheme '{scheme}:', which a 'name @' reference may not use "
            f"— write 'name @ git+{scheme}://…', or the URL on its own"
        )
    return f"uses the URL scheme '{scheme}:', which a catalog source may not use"


def _file_url_problem(url: str, scheme: str) -> str:
    """:func:`_url_problem` for a ``file:`` URL (``scheme`` is ``file`` or
    ``git+file``, for the spelling to suggest).

    ``git+file:`` has its own words (#131 verify round 7): what uv does with a
    ``file:`` host or a ``%23`` is not what happens to a ``git+file:`` URL — git
    ignores the host (uv 0.11.19 and pip 26.2.1 both cloned ``<abs>`` for
    ``git+file://h<abs>`` and ``git+file://LOCALHOST<abs>``, never a directory under
    the cwd), and for ``git+file:///<dir>/t%23r`` it is pip, not uv, that cut the
    path and cloned the sibling ``t`` (``.omc/probes/131-live/verify7/
    gitfile-measure.txt``). The refusals stay; the reason given is the true one.
    """

    rest = url[len("file:") :]
    good = f"{scheme}:///<absolute path>"
    git = scheme == "git+file"
    if rest.startswith("//"):
        authority = rest[2:].split("#", 1)[0]
        host = authority.split("/", 1)[0].split("?", 1)[0]
        if host in ("", "localhost"):
            if "/" not in authority:
                return (
                    f"is a {scheme}://{host} URL with no absolute path after the host — "
                    f"write {good}"
                )
            if git:
                return (
                    "is a git+file: URL whose path holds a percent-escape ('%'), which "
                    "aelix does not accept: pip cuts the path at a '%23' and clones "
                    "another repository — write git+file:///<absolute path> without "
                    "escapes"
                )
            return (
                f"is a {scheme}: URL whose path holds a percent-escape ('%'), which "
                "aelix does not accept: the installer decodes it (uv reads '%23' as "
                "'#' and cuts the path there, opening another one) — name the path "
                "directly instead (an absolute path, or ./<path> beside the catalog)"
            )
        if git:
            return (
                f"is a git+file: URL naming the host '{host}', an unsupported spelling "
                "— aelix accepts a git+file: host only empty or as 'localhost' in "
                f"lowercase; write {good}"
            )
        if host.lower() == "localhost":
            return (
                f"is a {scheme}: URL naming the host '{host}' — aelix accepts the host "
                f"only empty or as 'localhost' in lowercase; write {good} (uv reads "
                "any other spelling of the host as a directory under the current "
                "directory)"
            )
        return (
            f"is a {scheme}: URL naming the host '{host}', which aelix does not accept "
            f"— write {good} (uv reads such a host as a directory under the current "
            "directory)"
        )
    if rest.startswith("/"):
        return (
            f"is a {scheme}: URL written with one slash ('{scheme}:/…'), a spelling "
            f"aelix does not accept — write it with three, {good}"
        )
    if scheme == "file":
        return (
            "is a relative file: URL — uv reads it from the current directory; write "
            "./<path> (beside the catalog) or file:///<absolute path>"
        )
    return f"is a relative {scheme}: URL, which aelix does not accept — write {good}"


def _unversioned_requirement_name(source: str) -> tuple[str, str] | None:
    """``(name, "[extras]")`` of a plain requirement with NO version specifier, as
    written; ``None`` for one with a specifier (or not a requirement)."""

    from packaging.requirements import InvalidRequirement, Requirement

    try:
        req = Requirement(source)
    except InvalidRequirement:
        return None
    if str(req.specifier):
        return None
    extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
    return req.name, extras


def _place_path(body: str, raw: str, who: str, location: str | None) -> Path:
    """Resolve one path-shaped ``body`` from a catalog entry; :class:`CatalogError` refuses."""

    try:
        expanded = Path(body).expanduser()
    except RuntimeError as exc:  # ``~user`` with no such user
        raise CatalogError(f"{who}: cannot expand source '{raw}': {exc}") from exc
    if expanded.is_absolute():
        candidate = expanded
    else:
        base, why_not = _catalog_base_dir(location)
        if base is None:
            raise CatalogError(
                f"{who}: relative source '{raw}' has nothing to resolve against — "
                f"{why_not}. Refusing rather than reading it from the current "
                "directory or treating it as a package name; the catalog should give "
                "an absolute path, an absolute URL or a package name."
            )
        # Joined, NOT normalized: ``link/..`` is what the OS opens (resolve()).
        candidate = base / expanded
    if not _exists(candidate):
        raise CatalogError(
            f"{who}: source path '{candidate}' does not exist (the catalog says "
            f"'{raw}'). Refusing rather than treating it as a package name."
        )
    try:
        return candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise CatalogError(f"{who}: cannot resolve source path '{candidate}': {exc}") from exc


# =====================================================================
# === Index (generate a catalog from a directory of artifacts) ========
# =====================================================================
#
# Issue #68. Until now a private operator hand-wrote catalog.json and kept it in
# sync with a directory of wheels by hand — which is the part of "run your own
# catalog" that actually costs, and the part that silently rots (a rebuilt wheel
# changes its sha256 and nothing tells you the catalog is now wrong).
#
# The emit side lives beside :func:`parse_catalog` on purpose: the two halves of
# one format drift the moment they live apart. Everything here is pure — it reads
# artifact bytes and returns a document; the CLI half owns writing it.


#: Distribution archive suffixes we index. Wheels first (the normal case).
INDEX_ARTIFACT_SUFFIXES = (".whl", ".tar.gz")
#: Cap on a single METADATA/PKG-INFO member read — an archive is untrusted input
#: and a decompression bomb must not OOM the indexer. Real metadata is ~1-10 KB.
MAX_METADATA_BYTES = 512 * 1024


@dataclass(frozen=True)
class IndexedArtifact:
    """One built distribution found by :func:`scan_artifacts`.

    ``path`` is the artifact on disk; ``sha256`` is the hash of its bytes —
    DISPLAY-ONLY once it reaches a catalog entry, exactly like every other
    catalog ``sha256`` (ADR-0188). Generating it here does not make it a
    trust anchor, and it must still never seed the #64 pin store.
    """

    path: Path
    name: str
    version: str | None
    summary: str | None
    homepage: str | None
    sha256: str


def _version_sort_key(version: str) -> tuple[object, ...]:
    """Order versions by their numeric runs, so 1.10 sorts above 1.9.

    Deliberately not ``packaging.version``: ``packaging`` is present in most
    environments but is NOT a declared dependency of this package, and an
    indexer that dies on an import is worse than one that orders an exotic
    pre-release tag by string. Numeric runs compare as ints, the text between
    them as text.
    """

    parts: tuple[object, ...] = ()
    for chunk in re.findall(r"\d+|\D+", version):
        parts += ((0, int(chunk)) if chunk.isdigit() else (1, chunk),)
    return parts


def _read_metadata_bytes(path: Path) -> bytes | None:
    """Read a built distribution's core metadata without unpacking it.

    Wheel → the ``*.dist-info/METADATA`` member; sdist → the top-level
    ``*/PKG-INFO``. Members are read, never extracted, so no archive can write
    outside the directory. Returns :data:`None` when the archive carries no
    recognisable metadata (a stray zip in the wheel directory).
    """

    if path.name.endswith(".whl"):
        import zipfile  # noqa: PLC0415 — only the index path pays this import

        with zipfile.ZipFile(path) as zf:
            names = [
                n
                for n in zf.namelist()
                if n.endswith(".dist-info/METADATA") and n.count("/") == 1
            ]
            if not names:
                return None
            with zf.open(sorted(names)[0]) as fh:
                return fh.read(MAX_METADATA_BYTES)

    import tarfile  # noqa: PLC0415 — only the index path pays this import

    with tarfile.open(path, "r:gz") as tf:
        members = [
            m
            for m in tf.getmembers()
            if m.isfile() and m.name.endswith("/PKG-INFO") and m.name.count("/") == 1
        ]
        if not members:
            return None
        fh = tf.extractfile(sorted(members, key=lambda m: m.name)[0])
        if fh is None:
            return None
        with fh:
            return fh.read(MAX_METADATA_BYTES)


def read_artifact(path: Path) -> IndexedArtifact | None:
    """Read one built distribution into an :class:`IndexedArtifact`.

    Returns :data:`None` when the file carries no usable core metadata, so a
    stray archive in the wheel directory is skipped rather than fatal — the same
    lenience :func:`parse_catalog` applies to a malformed entry.
    """

    import hashlib  # noqa: PLC0415 — only the index path pays this import
    from email import message_from_string  # noqa: PLC0415

    try:
        raw = _read_metadata_bytes(path)
        # Core metadata is UTF-8 (PEP 566) — so NOT ``decode_child_output``
        # (#239): this is a FILE member of an archive whose encoding a spec
        # fixes, not a child's output whose encoding a console decides. And it
        # is decoded BEFORE the parse deliberately. ``BytesParser`` hands back an ``email.header.Header`` — not a
        # ``str`` — for any header carrying a non-ASCII byte, and _clean_display's regex
        # then raises TypeError: a pack whose Summary held Korean text (or an em dash,
        # which aelix's own wheels carry) took the WHOLE ``index`` command down with a
        # traceback, skipping nothing. ``str(header)`` is not the fix — it re-encodes the
        # unknown-8bit payload and yields mojibake. The parse sits INSIDE the try for the
        # same reason the read does: this function's contract is that an unreadable
        # archive is skipped and counted, never fatal to the scan.
        meta = message_from_string(raw.decode("utf-8", "replace")) if raw else None
    except Exception:  # noqa: BLE001 — a corrupt archive skips, never aborts the scan
        return None
    if meta is None:
        return None

    name = _clean_display(meta.get("Name"))
    if not name:
        return None

    homepage = _clean_display(meta.get("Home-page"))
    if not homepage:
        # PEP 621 projects emit Project-URL instead of the legacy Home-page.
        for value in meta.get_all("Project-URL") or ():
            label, _, url = str(value).partition(",")
            if label.strip().lower() in ("homepage", "home", "repository", "source"):
                homepage = _clean_display(url)
                break

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)

    return IndexedArtifact(
        path=path,
        name=name,
        version=_clean_display(meta.get("Version")),
        summary=_clean_display(meta.get("Summary")),
        homepage=homepage,
        sha256=digest.hexdigest(),
    )


def scan_artifacts(directory: Path) -> list[IndexedArtifact]:
    """Read every indexable built distribution directly inside ``directory``.

    Not recursive: a wheel directory is flat, and recursing would silently sweep
    in a neighbouring build tree. Unreadable / metadata-less files are skipped.
    Sorted by (name, version) so the emitted document is deterministic.
    """

    found: list[IndexedArtifact] = []
    for child in sorted(directory.iterdir()):
        if not child.is_file() or not child.name.endswith(INDEX_ARTIFACT_SUFFIXES):
            continue
        artifact = read_artifact(child)
        if artifact is not None:
            found.append(artifact)
    return sorted(
        found, key=lambda a: (a.name.lower(), _version_sort_key(a.version or ""))
    )


def _relative_source(artifact: Path, base: Path) -> str:
    """``artifact`` as a ``./``- or ``../``-prefixed POSIX path measured from ``base``.

    Forward slashes on every platform (Windows accepts them, and the catalog may be
    read on another OS). ``os.path.relpath`` raises ``ValueError`` across Windows
    drives; the CLI turns that into a usage error.
    """

    rel = Path(os.path.relpath(artifact, base)).as_posix()
    return rel if rel.startswith("../") else f"./{rel}"


def build_index_catalog(
    artifacts: Iterable[IndexedArtifact],
    *,
    name: str | None = None,
    relative_to: Path | None = None,
    updated: str | None = None,
) -> dict[str, object]:
    """Build a catalog DOCUMENT (the ``parse_catalog`` shape) from artifacts.

    One entry per distribution NAME, not per file. Several versions of a pack in
    one directory would otherwise emit several same-named entries, and
    :func:`resolve_entry` REFUSES an ambiguous name rather than picking one — so
    a two-version directory would produce a catalog whose entries cannot be
    installed by name. Instead the newest version becomes the entry (its path,
    its ``sha256``) and every version found is listed in ``versions``, which is
    what that field is for.

    ``source`` is an ABSOLUTE artifact path by default — it installs from any
    catalog location, a served one included. ``relative_to`` opts into a
    ``./``-prefixed path measured from that directory, which must be the
    PHYSICAL directory the catalog FILE is written to (the CLI passes
    ``target.resolve().parent``, or the resolved scanned directory for ``--out -``,
    which writes no file): since #131 (ADR-0255)
    :func:`resolve_entry_target` resolves a relative source against the local
    catalog file's own physical directory (never the process cwd), so such a
    catalog travels with its wheelhouse, a symlinked catalog file included — and
    is refused, not read from the cwd, if it is ever served over https or git.
    The ``./`` prefix is what the resolver's allowlist requires of a relative
    path, even a same-directory one (a bare ``name.whl`` is still accepted for
    catalogs an older ``--relative`` wrote).
    """

    by_name: dict[str, list[IndexedArtifact]] = {}
    for artifact in artifacts:
        by_name.setdefault(artifact.name, []).append(artifact)

    entries: list[dict[str, object]] = []
    for dist_name in sorted(by_name, key=str.lower):
        versions = sorted(
            by_name[dist_name],
            key=lambda a: _version_sort_key(a.version or ""),
            reverse=True,
        )
        newest = versions[0]
        # Both sides resolved before comparing. `relative_to` fails outright on
        # a bare path measured against an absolute directory, so leaving either
        # side in whatever form the caller happened to hold turned a scan of a
        # relative directory into a ValueError.
        source = (
            _relative_source(newest.path.resolve(), Path(relative_to).resolve())
            if relative_to is not None
            else str(newest.path.resolve())
        )
        entry = CatalogEntry(
            name=dist_name,
            source=source,
            description=newest.summary,
            version=newest.version,
            versions=tuple(a.version for a in versions if a.version),
            sha256=newest.sha256,
            homepage=newest.homepage,
        )
        entries.append(entry.to_json())

    document: dict[str, object] = {"schemaVersion": SCHEMA_VERSION}
    if name:
        document["name"] = name
    document["updated"] = updated or now_iso()
    document["extensions"] = entries
    return document
