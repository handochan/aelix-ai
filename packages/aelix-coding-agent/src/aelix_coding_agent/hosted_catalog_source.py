"""The hosted catalog source (#434, ADR-0264 §4.3 The hosted source).

Pi parity: ``packages/coding-agent/src/core/remote-catalog-provider.ts``
``withRemoteCatalog`` (the cache phase, then the network phase), with aelix's
ordering rule (a revision's lineage, not a timestamp) and one index plus one
content-addressed revision instead of one request per provider. The format and
the parser are aelix-ai's (:mod:`aelix_ai.hosted_catalog`), shared with the
publisher; this module only fetches, verifies, stores and decides.

- **Cache phase** (:func:`load_cached`, synchronous, offline included): select
  this build's entry from the stored index and install its stored revision when
  that revision's lineage holds this build's shipped digest.
- **Network phase** (:func:`refresh_hosted`): ``GET {base}/v1/index.json``,
  then each revision the index names that is not stored, the selected one
  first; persist the index and its revisions together; publish this build's
  revision, or remove the overlay when the index names none for it.

A published row never carries transport, and no credential is ever sent: a
wrong or forged file cannot send a request anywhere the shipped catalog does
not already send one. Requests are HTTPS only, with at most
:data:`MAX_REDIRECTS` redirects, each to HTTPS; a literal loopback address over
HTTP serves the tests and the dry run.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from aelix_ai import hosted_catalog as hc
from aelix_ai import models as catalog
from aelix_ai.models_store import (
    HOSTED_ENTRY,
    ModelsPublication,
    RefreshAborted,
    RefreshModelsContext,
    SourceOutcome,
    entry_stamp,
)
from aelix_ai.utils.terminal_text import safe_for_terminal

__all__ = [
    "CHECK_INTERVAL",
    "DEFAULT_BASE_URL",
    "ENV_CATALOG_URL",
    "FAILURE_INTERVAL",
    "HOSTED_ENTRY",
    "MAX_REDIRECTS",
    "MAX_REMOVED",
    "HostedUpdate",
    "adopt_entry",
    "catalog_base_url",
    "index_url",
    "load_cached",
    "refresh_hosted",
    "revision_url",
]

_LOG = logging.getLogger(__name__)

ENV_CATALOG_URL = "AELIX_MODEL_CATALOG_URL"
"""Where the hosted catalog lives. Read from the real environment only: a cwd
``.env`` may not set it, since it decides what the global store holds for
every directory (§4.3 Transport rules)."""

DEFAULT_BASE_URL = "https://handochan.github.io/aelix-catalog"
"""The catalog repository's GitHub Pages site (§7 Q1)."""

CHECK_INTERVAL = 4 * 60 * 60
"""An unforced check is skipped within this many seconds of ``checkedAt`` (pi's interval)."""

FAILURE_INTERVAL = 15 * 60
"""With no index stored, an unforced check is skipped this long after ``failedAt``."""

MAX_REDIRECTS = 3
REQUEST_TIMEOUT = 15.0
"""httpx's own bound per request. Every trigger bounds its whole wait at 15 s too."""

MAX_REMOVED = 20_000
"""The most ids ``removed`` keeps; the oldest go first (§4.4)."""

_MAX_ERROR_CHARS = 200
_WARNED_URLS: set[str] = set()
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


# ── the base URL ───────────────────────────────────────────────────────


def _is_loopback_http(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.username or parts.password:
        return False
    netloc = parts.netloc.rsplit("@", 1)[-1]
    host = netloc.rsplit(":", 1)[0] if not netloc.startswith("[") else netloc.split("]", 1)[0] + "]"
    return host in ("127.0.0.1", "[::1]")


def _is_https(url: str) -> bool:
    # No userinfo: httpx turns ``user:pass@`` into a Basic Authorization header
    # whatever ``auth=`` says, and no credential is ever sent (§4.3).
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
    )


def catalog_base_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The base URL the hosted source uses, or ``None`` when it is off.

    The built-in one unless :data:`ENV_CATALOG_URL` is set. An empty value
    turns the source off, as ``AELIX_DEFAULT_CATALOG=`` does for the
    marketplace. ``https:`` is taken; a literal loopback address over HTTP
    (``http://127.0.0.1[:port]``, ``http://[::1][:port]``) serves the tests and
    the dry run; any other value turns the source off with a warning.
    """

    env = os.environ if environ is None else environ
    if ENV_CATALOG_URL not in env:
        return DEFAULT_BASE_URL
    value = env[ENV_CATALOG_URL].strip()
    if not value:
        return None
    value = value.rstrip("/")
    if _is_https(value) or _is_loopback_http(value):
        return value
    if value not in _WARNED_URLS:  # once per value, not at every trigger
        _WARNED_URLS.add(value)
        _LOG.warning(
            "%s is neither an https:// URL nor a loopback http:// address; "
            "the hosted model catalog is off",
            ENV_CATALOG_URL,
        )
    return None


def index_url(base_url: str) -> str:
    return f"{base_url}/v1/index.json"


def revision_url(base_url: str, name: str) -> str:
    return f"{base_url}/v1/revisions/{name}.json"


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)


def _redirect_allowed(origin: str, target: str) -> bool:
    if _is_loopback_http(origin):
        return _same_origin(origin, target) and _is_loopback_http(target)
    return _is_https(target)


# ── what a stored entry means for this build ───────────────────────────


@dataclass(frozen=True)
class HostedUpdate:
    """What memory takes from the hosted source.

    ``action`` is ``install`` (``rows`` become the effective catalog),
    ``remove`` (back to the shipped catalog) or ``keep`` (the overlay this
    process has stays: a 404, a refused revision). ``removed`` is the hosted
    entry's ``removed`` ids per provider, applied whatever the action; ``None``
    leaves the ids the process has (an entry for another URL).
    """

    action: str
    revision: str | None = None
    rows: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None
    removed: Mapping[str, frozenset[str]] | None = field(default_factory=dict)
    parsed: hc.ParsedRevision | None = None
    note: str | None = None


def _bounded(text: str) -> str:
    return safe_for_terminal(text, max_chars=_MAX_ERROR_CHARS)


def _removed_of(entry: Mapping[str, Any] | None) -> dict[str, frozenset[str]]:
    removed = (entry or {}).get("removed")
    if not isinstance(removed, Mapping):
        return {}
    out: dict[str, frozenset[str]] = {}
    for provider, items in removed.items():
        if not isinstance(provider, str) or not isinstance(items, list):
            continue
        ids = frozenset(
            item[0]
            for item in items
            if isinstance(item, list) and item and hc.valid_model_id(item[0])
        )
        if ids:
            out[provider] = ids
    return out


def _stored_index(entry: Mapping[str, Any] | None) -> hc.HostedIndex | None:
    document = (entry or {}).get("index")
    if document is None:
        return None
    try:
        return hc.parse_index(document)
    except hc.IndexInvalid:
        return None


def _stored_revision(entry: Mapping[str, Any] | None, name: str) -> bytes | None:
    revisions = (entry or {}).get("revisions")
    if not isinstance(revisions, Mapping):
        return None
    text = revisions.get(name)
    if not isinstance(text, str):
        return None
    raw = text.encode("utf-8")
    return raw if hc.verify_revision_bytes(name, raw) else None


def _version_key(value: str) -> Any:
    from packaging.version import Version

    return Version(value)


def _select(index: hc.HostedIndex, version: str) -> hc.IndexEntry | None:
    try:
        return hc.select_catalog(index, version, parse_version=_version_key)
    except Exception:  # noqa: BLE001 - this build's own version does not parse
        return None


def _update_for(
    index: hc.HostedIndex | None,
    revision_bytes: Callable[[str], bytes | None],
    *,
    version: str,
    removed: Mapping[str, frozenset[str]],
) -> HostedUpdate:
    """The update a stored or fetched index makes for this build."""

    if index is None:
        return HostedUpdate("remove", removed=removed, note="no index stored")
    selected = _select(index, version)
    if selected is None:
        return HostedUpdate("remove", removed=removed, note="withdrawn")
    raw = revision_bytes(selected.revision)
    if raw is None:
        return HostedUpdate("keep", selected.revision, removed=removed, note="revision not stored")
    try:
        document = json.loads(raw)
        parsed = hc.parse_revision(document, catalog.shipped_catalog())
    except hc.RevisionRefused as exc:
        return HostedUpdate(
            "keep", selected.revision, removed=removed, note=_bounded(f"refused: {exc}")
        )
    except (ValueError, RecursionError) as exc:
        return HostedUpdate(
            "keep", selected.revision, removed=removed, note=_bounded(f"not JSON: {exc}")
        )
    if not hc.lineage_admits(parsed.lineage, catalog.shipped_digest()):
        return HostedUpdate("remove", selected.revision, removed=removed, note="not-applicable")
    return HostedUpdate(
        "install", selected.revision, rows=parsed.rows, removed=removed, parsed=parsed
    )


def update_from_entry(
    entry: Mapping[str, Any] | None, *, base_url: str | None, version: str
) -> HostedUpdate:
    """What a stored ``aelix-hosted`` entry means for this build.

    An entry from another URL is ignored, and so is everything when the source
    is off. A stored revision is re-verified against its name and re-parsed
    over this build's shipped catalog, so a hand-edited store adds nothing the
    network could not. The cache phase at start-up installs this; so does a
    run whose persist lost to another process's newer entry (§4.3 rule 2).
    """

    if base_url is None or entry is None or entry.get("url") != index_url(base_url):
        return HostedUpdate("remove", note="no entry for this URL")
    removed = _removed_of(entry)
    update = _update_for(
        _stored_index(entry),
        lambda name: _stored_revision(entry, name),
        version=version,
        removed=removed,
    )
    if update.action == "keep":
        # At start-up there is no overlay to keep: a stored revision that is
        # missing or refused means none until the index names another.
        return HostedUpdate("remove", update.revision, removed=removed, note=update.note)
    return update


def load_cached(store: Any, *, base_url: str | None, version: str) -> HostedUpdate:
    """The cache phase: what the stored entry installs before any network (§4.3)."""

    try:
        entry = store.read(HOSTED_ENTRY)
    except Exception as exc:  # noqa: BLE001 - a store that cannot be read is an empty one
        _LOG.warning("the models store could not be read: %s", exc)
        entry = None
    try:
        update = update_from_entry(entry, base_url=base_url, version=version)
    except Exception as exc:  # noqa: BLE001 - a hand-edited store never stops a launch
        _LOG.warning("the cached model catalog was not installed: %s", _bounded(str(exc)))
        return HostedUpdate("remove", note="unreadable entry")
    if update.note and update.note.startswith(("refused", "not JSON")):
        _LOG.warning("the cached model catalog was not installed: %s", update.note)
    return update


# ── the network phase ──────────────────────────────────────────────────


class _Failure(Exception):
    """A failed check; ``str()`` is the bounded reason the event reports."""


@dataclass(frozen=True)
class _Response:
    status: int
    headers: httpx.Headers
    body: bytes


async def _get(
    client: httpx.AsyncClient,
    url: str,
    *,
    cap: int,
    headers: Mapping[str, str],
    context: RefreshModelsContext,
) -> _Response:
    origin = url
    for _ in range(MAX_REDIRECTS + 1):
        context.signal.throw_if_aborted()
        async with client.stream("GET", url, headers=dict(headers)) as response:
            if response.status_code in _REDIRECT_CODES:
                location = response.headers.get("location")
                if not location:
                    raise _Failure(f"a {response.status_code} without a Location")
                target = urljoin(url, location)
                if not _redirect_allowed(origin, target):
                    raise _Failure("a redirect away from HTTPS was refused")
                url = target
                continue
            body = bytearray()
            if 200 <= response.status_code < 300:
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > cap:
                        raise _Failure(f"more than {cap} bytes")
                    context.signal.throw_if_aborted()
            return _Response(response.status_code, response.headers, bytes(body))
    raise _Failure(f"more than {MAX_REDIRECTS} redirects")


def _named_ids(raw: bytes) -> dict[str, set[str]]:
    """Each provider's ids a revision names (strings only), for ``removed``."""

    try:
        document = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    providers = document.get("providers") if isinstance(document, dict) else None
    if not isinstance(providers, dict):
        return {}
    out: dict[str, set[str]] = {}
    for provider, rows in providers.items():
        if isinstance(provider, str) and isinstance(rows, list):
            out[provider] = {
                row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
            }
    return out


def _next_removed(
    stored: Mapping[str, Any] | None,
    old_revisions: Mapping[str, bytes],
    new_revisions: Mapping[str, bytes],
    *,
    hold: bool,
    complete: bool,
    now: float,
) -> tuple[dict[str, list[list[Any]]], dict[str, list[str]]]:
    """``removed`` after persisting an index, and the candidates still pending (§4.4).

    An index carrying ``hold`` adds, per provider, each id a revision of the
    stored index named and none of the new one's names; one without adds none.
    An id the new index's revisions name leaves. At most :data:`MAX_REMOVED`
    stay, oldest dropped. ``complete`` is false when a revision the new index
    names could not be fetched: its ids are unknown, so the candidates wait in
    ``pendingRemoved`` (the old revisions are dropped with the old index) and a
    later check that has every revision decides them.
    """

    current = (stored or {}).get("removed")
    kept: dict[str, list[list[Any]]] = {}
    if isinstance(current, Mapping):
        for provider, items in current.items():
            if isinstance(provider, str) and isinstance(items, list):
                kept[provider] = [
                    [item[0], item[1] if len(item) > 1 and isinstance(item[1], (int, float)) else 0]
                    for item in items
                    if isinstance(item, list) and item and hc.valid_model_id(item[0])
                ]
    named_new: dict[str, set[str]] = {}
    for raw in new_revisions.values():
        for provider, ids in _named_ids(raw).items():
            named_new.setdefault(provider, set()).update(ids)
    candidates: dict[str, set[str]] = {}
    if hold:
        for raw in old_revisions.values():
            for provider, ids in _named_ids(raw).items():
                candidates.setdefault(provider, set()).update(ids)
        pending = (stored or {}).get("pendingRemoved")
        if isinstance(pending, Mapping):
            for provider, ids in pending.items():
                if isinstance(provider, str) and isinstance(ids, list):
                    candidates.setdefault(provider, set()).update(
                        i for i in ids if hc.valid_model_id(i)
                    )
        candidates = {
            provider: ids - named_new.get(provider, set())
            for provider, ids in candidates.items()
            if provider in catalog.shipped_catalog()
        }
        candidates = {provider: ids for provider, ids in candidates.items() if ids}
    still_pending: dict[str, list[str]] = {}
    if candidates and not complete:
        still_pending = {provider: sorted(ids) for provider, ids in candidates.items()}
    elif candidates:
        for provider, ids in candidates.items():
            present = {item[0] for item in kept.get(provider, [])}
            for model_id in sorted(ids - present):
                kept.setdefault(provider, []).append([model_id, now])
    for provider, ids in named_new.items():
        if provider in kept:
            kept[provider] = [item for item in kept[provider] if item[0] not in ids]
    flat = sorted(
        ((item[1], provider, item[0]) for provider, items in kept.items() for item in items),
        key=lambda t: (t[0], t[1], t[2]),
    )
    if len(flat) > MAX_REMOVED:
        flat = flat[len(flat) - MAX_REMOVED :]
    out: dict[str, list[list[Any]]] = {}
    for at, provider, model_id in flat:
        out.setdefault(provider, []).append([model_id, at])
    return out, still_pending


def _hosted_ids() -> set[tuple[str, str]]:
    """The effective catalog's ids this build does not ship: what an overlay added."""

    shipped = catalog.shipped_catalog()
    return {
        (provider, model.id)
        for provider in catalog.get_providers()
        for model in catalog.get_models(provider)
        if model.id not in shipped.get(provider, {})
    }


def _is_utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _fresh(value: Any, now: float) -> float | None:
    """A stored timestamp, or ``None`` when absent, malformed or in the future."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value <= now else None


def _outcome(
    update: HostedUpdate, *, previous: str | None, checked_at: float, removed: int = 0
) -> SourceOutcome:
    parsed = update.parsed
    if update.action == "install":
        added = parsed.added_count if parsed else 0
        changed = parsed.changed if parsed else 0
        dropped = len(parsed.no_transport) if parsed else 0
        outcome = "unchanged" if previous == update.revision else "published"
        return SourceOutcome(
            HOSTED_ENTRY,
            outcome,
            update.revision,
            added=added,
            changed=changed,
            removed=removed,
            dropped=dropped,
            checked_at=checked_at,
        )
    if update.note == "unavailable":
        return SourceOutcome(HOSTED_ENTRY, "unavailable", checked_at=checked_at)
    if update.action == "keep":
        return SourceOutcome(
            HOSTED_ENTRY, "failed", update.revision, error=update.note, checked_at=checked_at
        )
    if update.note == "not-applicable":
        return SourceOutcome(
            HOSTED_ENTRY, "not-applicable", update.revision, removed=removed, checked_at=checked_at
        )
    return SourceOutcome(HOSTED_ENTRY, "withdrawn", removed=removed, checked_at=checked_at)


def adopt_entry(
    entry: Mapping[str, Any] | None, *, base_url: str | None, version: str
) -> HostedUpdate:
    """What a RUNNING process takes from an entry another aelix stored (§4.3 rule 2, the skip).

    Unlike the cache phase at start-up (:func:`update_from_entry`), the process
    has an overlay to keep: an entry for another URL changes nothing, a 404's
    (``unavailable``) keeps the overlay, as pi's 404 does, and a revision that
    is missing or refused keeps it too.
    """

    if base_url is None or entry is None or entry.get("url") != index_url(base_url):
        return HostedUpdate("keep", note="another URL", removed=None)
    removed = _removed_of(entry)
    if entry.get("unavailable"):
        return HostedUpdate("keep", removed=removed, note="unavailable")
    return _update_for(
        _stored_index(entry),
        lambda name: _stored_revision(entry, name),
        version=version,
        removed=removed,
    )


async def refresh_hosted(
    context: RefreshModelsContext,
    *,
    version: str,
    clock: Callable[[], float] = time.time,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SourceOutcome:
    """One network-phase run of the hosted source (§4.3). Never raises but on cancellation.

    ``context.base_url`` ``None`` is ``not-applicable`` (the source is off), and
    ``allow_network`` false is ``skipped``. The returned outcome is what the
    ``model_catalog_refresh`` event reports.
    """

    base_url = context.base_url
    now = clock()
    if base_url is None:
        return SourceOutcome(HOSTED_ENTRY, "not-applicable")
    if not context.allow_network:
        return SourceOutcome(HOSTED_ENTRY, "skipped")
    url = index_url(base_url)
    raw_stored = context.stored
    stored = raw_stored if raw_stored is not None and raw_stored.get("url") == url else None
    base = entry_stamp(raw_stored)
    stored_index = _stored_index(stored)
    previous = catalog.installed_overlay()

    if not context.force:
        checked = _fresh((stored or {}).get("checkedAt"), now)
        failed = _fresh((stored or {}).get("failedAt"), now)
        if stored_index is not None and checked is not None and now - checked < CHECK_INTERVAL:
            # The skip stands for the stored entry (§4.3), which another aelix on
            # this agent dir may have written since this process read it: memory
            # takes it, nothing is persisted (a 404's keeps the overlay).
            try:
                context.publish(
                    ModelsPublication(
                        update=adopt_entry(stored, base_url=base_url, version=version)
                    )
                )
            except RefreshAborted:
                return SourceOutcome(HOSTED_ENTRY, "aborted", previous)
            return SourceOutcome(
                HOSTED_ENTRY, "skipped", catalog.installed_overlay(), checked_at=checked
            )
        if stored_index is None and failed is not None and now - failed < FAILURE_INTERVAL:
            return SourceOutcome(HOSTED_ENTRY, "skipped", previous, checked_at=checked)

    old_revisions: dict[str, bytes] = {}
    if stored_index is not None:
        for item in stored_index.catalogs:
            raw = _stored_revision(stored, item.revision)
            if raw is not None:
                old_revisions[item.revision] = raw

    # What memory took instead when the persist lost to another process's
    # newer entry (§4.3 rule 2): the outcome reports that, not this run's own.
    lost: dict[str, HostedUpdate] = {}
    counts = {"removed": 0}

    def on_lost(newer: Mapping[str, Any]) -> HostedUpdate:
        lost["update"] = adopt_entry(newer, base_url=base_url, version=version)
        return lost["update"]

    def published(update: HostedUpdate) -> SourceOutcome:
        taken = lost.get("update", update)
        return _outcome(taken, previous=previous, checked_at=now, removed=counts["removed"])

    def publish(entry: Mapping[str, Any], update: HostedUpdate | None) -> bool:
        context.signal.throw_if_aborted()
        before = _hosted_ids()
        ok = context.publish(
            ModelsPublication(persist=entry, base=base, update=update, on_lost=on_lost)
        )
        if ok:
            counts["removed"] = len(before - _hosted_ids())
        return ok

    def refused(entry: Mapping[str, Any], update: HostedUpdate | None) -> SourceOutcome | None:
        """Publish; the outcome when it did not (aborted, or a write that failed), else ``None``."""

        try:
            if not publish(entry, update):
                return SourceOutcome(HOSTED_ENTRY, "aborted", previous)
        except RefreshAborted:
            return SourceOutcome(HOSTED_ENTRY, "aborted", previous)
        except Exception as exc:  # noqa: BLE001 - a failed write publishes nothing in memory
            return SourceOutcome(
                HOSTED_ENTRY, "failed", previous, error=_bounded(str(exc)), checked_at=now
            )
        return None

    def failure(reason: str) -> SourceOutcome:
        entry: dict[str, Any] = dict(stored) if stored is not None else {"url": url}
        entry["failedAt"] = now
        if stored_index is not None:
            entry["checkedAt"] = now
        error = _bounded(reason)
        try:
            if not publish(entry, None):
                return SourceOutcome(HOSTED_ENTRY, "aborted", previous, error=error)
        except RefreshAborted:
            return SourceOutcome(HOSTED_ENTRY, "aborted", previous, error=error)
        except Exception as exc:  # noqa: BLE001 - the store's failure is still a failure
            error = _bounded(f"{reason}; {exc}")
        return SourceOutcome(HOSTED_ENTRY, "failed", previous, error=error, checked_at=now)

    from .cli.config import VERSION

    headers = {"User-Agent": f"aelix/{VERSION}", "Accept": "application/json"}
    validators = dict(headers)
    if stored_index is not None and len(old_revisions) == len(
        {item.revision for item in stored_index.catalogs}
    ):
        etag = stored.get("etag") if stored else None
        last_modified = stored.get("lastModified") if stored else None
        if isinstance(etag, str) and etag:
            validators["If-None-Match"] = etag
        elif isinstance(last_modified, str) and last_modified:
            validators["If-Modified-Since"] = last_modified

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
            auth=None,
            transport=transport,
        ) as client:
            try:
                response = await _get(
                    client, url, cap=hc.MAX_INDEX_BYTES, headers=validators, context=context
                )
            except _Failure as exc:
                return failure(f"index: {exc}")
            except httpx.HTTPError as exc:
                return failure(f"index: {type(exc).__name__}")

            validator_headers: dict[str, str] = {}
            if response.status == 304 and stored is not None and stored_index is not None:
                # Not modified: the stored index stands, and the revisions it
                # names that are not stored are fetched below as on a 200.
                index = stored_index
                index_document = stored["index"]
                for key in ("etag", "lastModified"):
                    if isinstance(stored.get(key), str):
                        validator_headers[key] = stored[key]
            elif response.status == 404:
                entry = {
                    "url": url,
                    "index": {"schema": hc.INDEX_SCHEMA, "catalogs": []},
                    "checkedAt": now,
                    "revisions": {},
                    # A 404, not a withdrawal: the process keeps its overlay (pi's
                    # 404), and the next start has none either way.
                    "unavailable": True,
                }
                for key in ("removed", "pendingRemoved"):
                    if stored is not None and key in stored:
                        entry[key] = stored[key]
                update = HostedUpdate("keep", removed=_removed_of(stored), note="unavailable")
                return refused(entry, update) or published(update)
            elif not 200 <= response.status < 300:
                return failure(f"index: HTTP {response.status}")
            else:
                try:
                    index_document = json.loads(response.body)
                    index = hc.parse_index(index_document)
                except (ValueError, hc.IndexInvalid) as exc:
                    return failure(f"index: {exc}")
                if response.headers.get("etag"):
                    validator_headers["etag"] = response.headers["etag"]
                if response.headers.get("last-modified"):
                    validator_headers["lastModified"] = response.headers["last-modified"]

            selected = _select(index, version)
            names = [item.revision for item in index.catalogs]
            if selected is not None:
                names.remove(selected.revision)
                names.insert(0, selected.revision)
            fetched: dict[str, bytes] = {}
            missing: list[str] = []
            for name in dict.fromkeys(names):
                have = old_revisions.get(name) or _stored_revision(stored, name)
                if have is not None:
                    fetched[name] = have
                    continue
                body: bytes | None = None
                why = ""
                try:
                    got = await _get(
                        client,
                        revision_url(base_url, name),
                        cap=hc.MAX_REVISION_BYTES,
                        headers=headers,
                        context=context,
                    )
                    if not 200 <= got.status < 300:
                        why = f"HTTP {got.status}"
                    elif not hc.verify_revision_bytes(name, got.body):
                        why = "does not match its name"
                    elif not _is_utf8(got.body):
                        why = "not UTF-8"
                    else:
                        body = got.body
                except _Failure as exc:
                    why = str(exc)
                except httpx.HTTPError as exc:
                    why = type(exc).__name__
                if body is not None:
                    fetched[name] = body
                elif selected is not None and name == selected.revision:
                    return failure(f"revision {name[:19]}: {why}")
                else:
                    missing.append(f"{name[:19]}: {why}")
    except RefreshAborted:
        return SourceOutcome(HOSTED_ENTRY, "aborted", previous)

    try:
        removed_ids, pending = _next_removed(
            stored,
            old_revisions,
            fetched,
            hold=index.hold is not None,
            complete=not missing,
            now=now,
        )
        entry = {
            "url": url,
            "index": index_document,
            "checkedAt": now,
            "revisions": {name: raw.decode("utf-8") for name, raw in fetched.items()},
            "removed": removed_ids,
            **validator_headers,
        }
        if pending:
            entry["pendingRemoved"] = pending
        update = _update_for(index, fetched.get, version=version, removed=_removed_of(entry))
    except Exception as exc:  # noqa: BLE001 - recorded like any failed check, with its backoff
        return failure(f"{type(exc).__name__}: {exc}")
    not_published = refused(entry, update)
    if not_published is not None:
        return not_published
    result = published(update)
    if missing and result.error is None and "update" not in lost:
        result = SourceOutcome(
            result.source,
            result.outcome,
            result.revision,
            added=result.added,
            changed=result.changed,
            removed=result.removed,
            dropped=result.dropped,
            error=_bounded("not stored: " + "; ".join(missing)),
            checked_at=result.checked_at,
        )
    return result
