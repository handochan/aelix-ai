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
  entry's ``source`` is a ``path | git+url[@sha] | pypi`` spec handed UNCHANGED
  to the existing installer, so the source-level ``y/N`` consent prompt +
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
from urllib.parse import unquote, urlparse

__all__ = [
    "CATALOG_CACHE_FILENAME",
    "DEFAULT_CATALOG_FILENAME",
    "MAX_CATALOG_BYTES",
    "MAX_CATALOG_ENTRIES",
    "SCHEMA_VERSION",
    "Catalog",
    "CatalogEntry",
    "CatalogError",
    "GitRunner",
    "Opener",
    "cache_file_path",
    "fetch_catalog",
    "load_cached_catalog",
    "now_iso",
    "parse_catalog",
    "resolve_entry",
    "save_catalogs",
    "search_entries",
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

#: An injectable HTTP(S) fetcher (``(url, timeout) -> bytes``) — the default uses
#: ``urllib.request``; tests inject a stub so no network is touched.
Opener = Callable[[str, float], bytes]
#: An injectable git-clone runner (argv → CompletedProcess) — default subprocess.
GitRunner = Callable[[list[str]], "subprocess.CompletedProcess[bytes]"]


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
    ``git+url[@40-hexsha]``, or a ``pypi-name[==version]`` spec that
    ``classify_target`` already routes. ``sha256`` is DISPLAY-ONLY (ADR-0188): it
    is never written to the #64 pin store. ``catalog_name`` records which catalog
    the entry came from (for grouped display + ambiguous-name disambiguation).
    ``extra`` preserves unknown keys verbatim for forward compatibility.
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
    def from_json(cls, raw: dict[str, object], *, catalog_name: str | None) -> CatalogEntry | None:
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
        )


@dataclass(frozen=True)
class Catalog:
    """A fetched (or cached) catalog document from one registered location.

    ``error`` is set (with ``entries=()``) when a fetch/parse failed but the
    location is still recorded in the cache, so the TUI can show an honest
    "⚠ failed to fetch" row rather than silently dropping the source.
    """

    location: str
    name: str | None = None
    updated: str | None = None
    entries: tuple[CatalogEntry, ...] = ()
    fetched_at: str | None = None
    error: str | None = None

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
                    entry = CatalogEntry.from_json(item, catalog_name=label)
                    if entry is not None:
                        entries.append(entry)
        return cls(
            location=location,
            name=name,
            updated=_s("updated"),
            entries=tuple(entries),
            fetched_at=_s("fetchedAt"),
            error=_s("error"),
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
            entry = CatalogEntry.from_json(item, catalog_name=label)
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
    # A wall-clock timeout so a hung remote surfaces as TimeoutExpired (translated
    # to a CatalogError in _git_clone_bytes) instead of blocking discover forever.
    return subprocess.run(  # noqa: S603 — argv list, no shell
        argv, capture_output=True, check=False, timeout=GIT_CLONE_TIMEOUT
    )


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


def _git_clone_bytes(location: str, *, git_runner: GitRunner) -> bytes:
    """Shallow-clone a git catalog source and read its root ``catalog.json``.

    The clone is discarded immediately; only the catalog document survives. A
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
        except (OSError, subprocess.SubprocessError) as exc:
            # Missing git binary (FileNotFoundError), fork failure, or a clone
            # TimeoutExpired — degrade this ONE source, never crash the refresh.
            raise CatalogError(f"git clone failed for catalog {location!r}: {exc}") from exc
        if int(getattr(result, "returncode", 1)) != 0:
            stderr = getattr(result, "stderr", b"") or b""
            detail = stderr.decode("utf-8", "replace").strip() if isinstance(stderr, bytes) else str(stderr)
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
        return _read_local(catalog_path, location)
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
    return Path(unquote(parsed.path))


def fetch_catalog(
    location: str,
    *,
    opener: Opener = _default_opener,
    git_runner: GitRunner = _default_git_runner,
    timeout: float = 30.0,
) -> Catalog:
    """Fetch + parse the catalog at ``location`` over an air-gap-native transport.

    Dispatch by shape: ``git+…`` → shallow clone + read root ``catalog.json``;
    ``https://`` → TLS fetch; ``http://`` → REFUSED (TLS required, ADR-0188);
    ``file://`` or a bare local path → read the file. Raises :class:`CatalogError`
    on any transport/parse failure (the CLI degrades that source to a warning).
    """

    loc = location.strip()
    if not loc:
        raise CatalogError("empty catalog location")
    low = loc.lower()

    if loc.startswith("git+") or low.startswith(("git://", "ssh://", "git@")) or low.endswith(".git"):
        data = _git_clone_bytes(loc, git_runner=git_runner)
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
        return parse_catalog(data, location=location)

    # file:// URL or a bare local path.
    path = _file_url_to_path(loc) if low.startswith("file://") else Path(loc).expanduser()
    data = _read_local(path, location)
    return parse_catalog(data, location=location)


def fetch_all(
    locations: Iterable[str],
    *,
    opener: Opener = _default_opener,
    git_runner: GitRunner = _default_git_runner,
    timeout: float = 30.0,
) -> list[Catalog]:
    """Fetch every registered catalog location; a failure becomes an ``error`` row.

    Never raises for a single bad source — a failed fetch yields a
    :class:`Catalog` with ``error`` set and ``entries=()`` so ``--refresh`` records
    the failure in the cache (the TUI shows it) instead of dropping the location.
    """

    out: list[Catalog] = []
    for loc in locations:
        try:
            out.append(fetch_catalog(loc, opener=opener, git_runner=git_runner, timeout=timeout))
        except CatalogError as exc:
            out.append(Catalog(location=loc, entries=(), fetched_at=now_iso(), error=str(exc)))
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


def resolve_entry(
    catalogs: Iterable[Catalog],
    name: str,
    *,
    catalog: str | None = None,
) -> tuple[CatalogEntry | None, list[CatalogEntry]]:
    """Resolve an exact ``name`` to one entry across catalogs.

    Returns ``(resolved, candidates)``: ``candidates`` is every entry whose name
    matches ``name`` case-insensitively (optionally narrowed to the catalog whose
    label/location matches ``catalog``); ``resolved`` is the single candidate when
    there is EXACTLY one, else :data:`None`. The caller REFUSES an ambiguous
    resolution (``resolved is None and len(candidates) > 1``) with the candidate
    list — never a silent first-match (ADR-0188).
    """

    target = name.strip().lower()
    cat_filter = catalog.strip().lower() if catalog and catalog.strip() else None
    candidates: list[CatalogEntry] = []
    for cat in catalogs:
        if cat_filter is not None and cat.label().lower() != cat_filter and cat.location.lower() != cat_filter:
            continue
        for entry in cat.entries:
            if entry.name.strip().lower() == target:
                candidates.append(entry)
    resolved = candidates[0] if len(candidates) == 1 else None
    return resolved, candidates
