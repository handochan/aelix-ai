"""The hosted model catalog's format: canonical bytes, digest, lineage and the row parser.

#172, ADR-0264 §4.2 (Format, the ordering rule) and §4.3 (what a hosted row may
set). aelix publishes its own corrected catalog, without transport, from a
scheduled job in ``handochan/aelix-catalog``; a client overlays a published
revision on its shipped catalog when the revision's lineage holds the shipped
catalog's digest. This module is the half of that contract both sides share:
``scripts/publish_catalog.py`` writes revisions with it and validates each one
through :func:`parse_revision` before anything is published, and the client
(#434) reads revisions with the same parser. ONE serializer and ONE parser, so
the publisher and the client cannot drift apart in any commit
(``tests/test_hosted_catalog_contract.py`` runs both on every pull request).

WHAT A REVISION CAN DO, AND WHAT IT CANNOT. A published row carries facts only
(:data:`FACT_FIELDS`) plus the ladder's two inputs (:data:`LADDER_FIELDS`). The
parser never reads ``api``, ``baseUrl``, ``headers``, ``compat`` or
``provider``: a known id keeps its shipped transport, and a new id inherits
transport from the shipped rows of its own provider through ADR-0232's ladder
(:mod:`aelix_ai.catalog_inherit`) or is dropped. So a wrong or forged revision
can never send a request, or a key, anywhere the shipped catalog does not
already send one, and it cannot add a provider.

THE ORDERING RULE IS LINEAGE, NOT TIME. :func:`catalog_digest` is the sha256 of
a catalog's canonical JSON, cut to 32 hex characters; a revision's ``lineage``
lists, oldest first, the digest of every version ``models_generated.json`` had
on ``main``'s first-parent history up to the commit it was built from. A build
installs a revision only when that lineage holds its own shipped digest
(:func:`lineage_admits`). Nothing time-dependent is inside a revision, so its
name (:func:`revision_name`, the sha256 of its bytes) changes only when its rows
or its lineage do.

This module imports nothing above ``aelix_ai`` and nothing that loads the
catalog at import (the ladder and ``utils.terminal_text`` are stdlib-only
below them): the thinking levels default lazily to
:data:`aelix_ai.models.EXTENDED_THINKING_LEVELS` inside :func:`parse_revision`,
so ``aelix_ai.models`` may import this module (#434 does) without a cycle.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.catalog_inherit import ladder_inputs, resolve_transport
from aelix_ai.utils.terminal_text import safe_for_terminal

__all__ = [
    "FACT_FIELDS",
    "INDEX_SCHEMA",
    "LADDER_FIELDS",
    "MAX_INDEX_BYTES",
    "MAX_LINEAGE",
    "MAX_NEW_IDS",
    "MAX_NEW_IDS_PER_PROVIDER",
    "MAX_PROVIDER_ROWS",
    "MAX_REVISION_BYTES",
    "MAX_ROWS",
    "REGRESSION_BOUND",
    "REVISION_SCHEMA",
    "TRANSPORT_FIELDS",
    "Hold",
    "HostedIndex",
    "IndexEntry",
    "IndexInvalid",
    "ParsedRevision",
    "RevisionRefused",
    "RowNote",
    "build_revision",
    "canonical_bytes",
    "catalog_digest",
    "lineage_admits",
    "parse_index",
    "parse_revision",
    "revision_name",
    "revision_row",
    "select_catalog",
    "serialize_revision",
    "valid_model_id",
    "verify_revision_bytes",
]

Row = dict[str, Any]
Catalog = Mapping[str, Mapping[str, Mapping[str, Any]]]

REVISION_SCHEMA = "aelix-model-catalog/1"
INDEX_SCHEMA = "aelix-model-catalog-index/1"

FACT_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "reasoning",
    "thinkingLevelMap",
    "input",
    "cost",
    "contextWindow",
    "maxTokens",
)
"""What a published row may say about a model (ADR-0264 §4.2 Format)."""

LADDER_FIELDS: tuple[str, ...] = ("family", "releaseDate")
"""The ladder's upstream inputs. Not facts: they steer inheritance only (§4.3)."""

TRANSPORT_FIELDS: tuple[str, ...] = ("api", "baseUrl", "headers", "compat", "provider")
"""Never published, never read. The publisher fails a revision that carries one."""

_REQUIRED_FACTS: tuple[str, ...] = (
    "name",
    "reasoning",
    "input",
    "cost",
    "contextWindow",
    "maxTokens",
)
_COST_KEYS: tuple[str, ...] = ("input", "output", "cacheRead", "cacheWrite")

# The bounds of §4.2 and §4.3, in one place so the publisher checks the numbers
# the client enforces. Every shipped row sits inside them
# (``tests/test_hosted_catalog_contract.py`` checks that, not this comment).
MAX_REVISION_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 64 * 1024
MAX_ROWS = 20_000
MAX_PROVIDER_ROWS = 5_000
MAX_NEW_IDS = 2_000
MAX_NEW_IDS_PER_PROVIDER = 500
REGRESSION_BOUND = 25
MAX_LINEAGE = 500

_MAX_ID_CHARS = 256
_MAX_NAME_CHARS = 200
_MAX_COST_PER_MILLION = 10_000.0
_MIN_CONTEXT_WINDOW = 1_024
_MAX_CONTEXT_WINDOW = 100_000_000
_MIN_MAX_TOKENS = 256

_ID_RE = re.compile(r"[A-Za-z0-9@~][A-Za-z0-9.\-:/@~]*")
# Wire values are case-sensitive: the shipped Google rows send Gemini's own
# ``thinkingLevel`` enum, ``HIGH``, ``LOW`` and ``MINIMAL`` (8 rows, MEASURED
# 2026-10-21), so the ADR's ``[a-z0-9_-]`` would refuse a shipped map. The
# amendment of 2026-10-21 (ADR-0264 §4.3) widens it to both cases, no more.
_MAP_VALUE_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
_FAMILY_RE = re.compile(r"[a-z0-9._-]{1,64}")
_RELEASE_RE = re.compile(r"[0-9-]{1,10}")
_DIGEST_RE = re.compile(r"[0-9a-f]{32}")
_REVISION_NAME_RE = re.compile(r"sha256-[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


# ── canonical bytes, digest, name ──────────────────────────────────────


def canonical_bytes(value: Any) -> bytes:
    """``value`` as canonical JSON: keys sorted, no insignificant whitespace, UTF-8.

    No trailing newline either: the file's bytes are what its name hashes, and
    a newline is insignificant whitespace. ``allow_nan=False`` because NaN and
    the infinities are not JSON, and a serializer that emits them writes a file
    no strict parser reads.
    """

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def catalog_digest(catalog: Catalog) -> str:
    """A catalog's digest: the sha256 of its canonical JSON, cut to 32 hex characters.

    ``catalog`` is the parsed ``models_generated.json`` (provider, then id, then
    row), transport included. Being canonical, the digest does not change with
    a CRLF checkout or a key order, only with what the rows say (§4.2).
    """

    return hashlib.sha256(canonical_bytes(catalog)).hexdigest()[:32]


def revision_name(raw: bytes) -> str:
    """``sha256-<hex>`` of a revision file's bytes: its content address."""

    return "sha256-" + hashlib.sha256(raw).hexdigest()


def verify_revision_bytes(name: str, raw: bytes) -> bool:
    """True when ``raw`` is at most 16 MiB and hashes to ``name``.

    The client checks this before parsing (§4.3, step 3); a mismatch or an
    oversized body is a failed fetch, not a refused revision.
    """

    return len(raw) <= MAX_REVISION_BYTES and revision_name(raw) == name


def lineage_admits(lineage: Sequence[str], digest: str) -> bool:
    """True when a revision whose lineage is ``lineage`` may overlay a build at ``digest``.

    That is: the revision was built from a ``main`` commit whose history
    contains this build's catalog (§4.2, the ordering rule). A dev checkout
    with an unmerged catalog edit has a digest no lineage holds.
    """

    return digest in lineage


# ── writing a revision ─────────────────────────────────────────────────


def revision_row(row: Mapping[str, Any], inputs: Mapping[str, Any] | None = None) -> Row:
    """One published row: ``row``'s facts, plus the ladder's inputs from ``inputs``.

    ``row`` is a catalog row in ``models_generated.json``'s shape; its transport
    fields are not copied. ``inputs`` holds ``family`` and ``releaseDate`` when
    models.dev lists the id for that provider; ``family`` is lowercased here, as
    the ladder lowercases it (§4.2 step 3), and an empty value is left out.
    """

    out: Row = {name: row[name] for name in FACT_FIELDS if name in row}
    if inputs:
        family = inputs.get("family")
        if isinstance(family, str) and family:
            out["family"] = family.lower()
        release = inputs.get("releaseDate")
        if isinstance(release, str) and release:
            out["releaseDate"] = release
    return out


def build_revision(
    catalog: Catalog,
    lineage: Sequence[str],
    inputs: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> Row:
    """The revision document for ``catalog``: providers and rows sorted by id.

    ``inputs[provider][model_id]`` is that row's ladder inputs
    (:func:`revision_row`). The document is plain JSON data; write it with
    :func:`serialize_revision`, which is what the name hashes.
    """

    inputs = inputs or {}
    providers: dict[str, list[Row]] = {}
    for provider in sorted(catalog):
        rows = catalog[provider]
        provider_inputs = inputs.get(provider) or {}
        providers[provider] = [
            revision_row(rows[model_id], provider_inputs.get(model_id)) for model_id in sorted(rows)
        ]
    return {"schema": REVISION_SCHEMA, "lineage": list(lineage), "providers": providers}


def serialize_revision(revision: Mapping[str, Any]) -> bytes:
    """The bytes a revision is published as. Rows are re-sorted by id, so the order is canonical."""

    document = dict(revision)
    providers = document.get("providers")
    if isinstance(providers, Mapping):
        document["providers"] = {
            provider: sorted(rows, key=lambda row: str(row.get("id", "")))
            if isinstance(rows, list)
            else rows
            for provider, rows in providers.items()
        }
    return canonical_bytes(document)


# ── validating a row ───────────────────────────────────────────────────


def valid_model_id(value: Any) -> bool:
    """§4.3's id rule: 1-256 ASCII characters from letters, digits and ``. - : / @ ~``.

    It starts with a letter, a digit, ``@`` or ``~``, and has no empty, ``.``
    or ``..`` path segment. Ids reach URLs (Copilot's policy path) and a
    child's argv, so the rule is about what they can do there, and every
    shipped id fits it.
    """

    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_ID_CHARS:
        return False
    if not _ID_RE.fullmatch(value):
        return False
    return all(segment not in ("", ".", "..") for segment in value.split("/"))


class _Invalid(Exception):
    """A field failed validation; ``str()`` says why."""


def _name(value: Any) -> str:
    if not isinstance(value, str):
        raise _Invalid("not a string")
    cleaned = safe_for_terminal(value)
    if not cleaned.strip():
        raise _Invalid("empty")
    if len(cleaned) > _MAX_NAME_CHARS:
        raise _Invalid(f"over {_MAX_NAME_CHARS} characters")
    return cleaned


def _reasoning(value: Any) -> bool:
    if type(value) is not bool:
        raise _Invalid("not a JSON boolean")
    return value


_MAX_QUOTED_CHARS = 32
"""The most of a published map key a :class:`RowNote` reason quotes."""


def _quoted(text: str) -> str:
    """``text`` as a reason quotes it: cut to :data:`_MAX_QUOTED_CHARS`, then ``repr``-escaped."""

    cut = text[:_MAX_QUOTED_CHARS]
    return repr(cut) + ("..." if len(text) > _MAX_QUOTED_CHARS else "")


def _thinking_map(value: Any, levels: frozenset[str]) -> dict[str, str | None]:
    if not isinstance(value, dict):
        raise _Invalid("not an object")
    out: dict[str, str | None] = {}
    for level, wire in value.items():
        if level not in levels:
            raise _Invalid(f"level {_quoted(level)} is not one of this build's")
        if wire is not None and not (isinstance(wire, str) and _MAP_VALUE_RE.fullmatch(wire)):
            raise _Invalid(f"value for {level!r} is not null or [A-Za-z0-9_-]{{1,32}}")
        out[level] = wire
    return out


def _input(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _Invalid("not a list of strings")
    kinds = set(value)
    if "text" not in kinds or not kinds <= {"text", "image"}:
        raise _Invalid("not a subset of text and image that contains text")
    return ["text", "image"] if "image" in kinds else ["text"]


def _number(value: Any) -> float | int:
    # ``True`` is an ``int`` in Python; a JSON ``true`` must never price a model.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Invalid("not a number")
    # Only a float can be infinite or NaN. An int is never converted to one:
    # Python's json reads a 400-digit literal as an int, ``math.isfinite`` of
    # it raises OverflowError, and the caller's range check compares an int of
    # any size exactly.
    if isinstance(value, float) and not math.isfinite(value):
        raise _Invalid("not finite")
    return value


def _cost(value: Any) -> dict[str, float | int]:
    if not isinstance(value, dict):
        raise _Invalid("not an object")
    out: dict[str, float | int] = {}
    for key in _COST_KEYS:
        if key not in value:
            raise _Invalid(f"{key} missing")
        number = _number(value[key])
        if not 0 <= number <= _MAX_COST_PER_MILLION:
            raise _Invalid(f"{key} outside 0..{_MAX_COST_PER_MILLION:g} per million")
        out[key] = number
    return out


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid("not an integer")
    return value


def _context_window(value: Any) -> int:
    number = _integer(value)
    if not _MIN_CONTEXT_WINDOW <= number <= _MAX_CONTEXT_WINDOW:
        raise _Invalid(f"outside {_MIN_CONTEXT_WINDOW}..{_MAX_CONTEXT_WINDOW}")
    return number


def _max_tokens(value: Any) -> int:
    number = _integer(value)
    if number < _MIN_MAX_TOKENS:
        raise _Invalid(f"below {_MIN_MAX_TOKENS}")
    return number


def _family(value: Any) -> str:
    if not isinstance(value, str) or not _FAMILY_RE.fullmatch(value):
        raise _Invalid("not 1-64 characters of [a-z0-9._-]")
    return value


def _release_date(value: Any) -> str:
    if not isinstance(value, str) or not _RELEASE_RE.fullmatch(value):
        raise _Invalid("not 1-10 characters of [0-9-]")
    return value


_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "name": _name,
    "reasoning": _reasoning,
    "input": _input,
    "cost": _cost,
    "contextWindow": _context_window,
    "maxTokens": _max_tokens,
    "family": _family,
    "releaseDate": _release_date,
}


# ── parsing a revision ─────────────────────────────────────────────────


class RevisionRefused(ValueError):
    """The whole revision is refused: the build installs none of it (§4.3).

    Refused, not failed: the bytes arrived and verified, and what they say is
    outside what this build accepts from the network at all.
    """


@dataclass(frozen=True)
class RowNote:
    """One thing the parser did not take as published.

    ``model_id`` is the row's id when it had a usable one, else its position
    (``#3``). ``field`` is ``None`` when the whole row is concerned. Ids and
    reasons only, and a reason quotes a published value only bounded: the
    ladder's reason for no transport may name a family key, which passed the
    ``family`` rule (at most 64 characters of ``[a-z0-9._-]``) or is cut from a
    valid id, and a thinking map's key that is not one of this build's levels
    is cut to 32 characters and ``repr``-escaped, so the publish summary can
    name it (``'max'`` for a build before #433). So nothing a revision carries
    reaches a log line through a note unbounded.
    """

    provider: str
    model_id: str
    field: str | None
    reason: str


@dataclass
class ParsedRevision:
    """What a revision does to this build's catalog, and what it did not do.

    ``rows`` is the effective built-in catalog in ``models_generated.json``'s
    shape: every shipped provider in shipped order, every shipped row in
    shipped order with a known id's hosted facts in place and its shipped
    transport kept, then each new id with inherited transport, in the
    revision's order. A shipped row the revision does not list stays as
    shipped, and a revision can never remove one.
    """

    lineage: tuple[str, ...]
    rows: dict[str, dict[str, Row]]
    known: int = 0
    changed: int = 0
    added: dict[str, dict[str, Row]] = field(default_factory=dict)
    dropped: list[RowNote] = field(default_factory=list)
    kept: list[RowNote] = field(default_factory=list)
    ignored_providers: tuple[str, ...] = ()
    regressions: int = 0

    @property
    def added_count(self) -> int:
        return sum(len(rows) for rows in self.added.values())

    @property
    def no_transport(self) -> list[RowNote]:
        """The new ids dropped because the ladder had no answer."""

        return [note for note in self.dropped if note.field == "transport"]


def _refuse_shape(document: Any) -> tuple[tuple[str, ...], Mapping[str, Any]]:
    if not isinstance(document, dict):
        raise RevisionRefused("not a JSON object")
    if document.get("schema") != REVISION_SCHEMA:
        raise RevisionRefused("unknown schema")
    lineage = document.get("lineage")
    if (
        not isinstance(lineage, list)
        or not lineage
        or len(lineage) > MAX_LINEAGE
        or not all(isinstance(entry, str) and _DIGEST_RE.fullmatch(entry) for entry in lineage)
    ):
        raise RevisionRefused(f"lineage is not 1-{MAX_LINEAGE} digests of 32 hex characters")
    providers = document.get("providers")
    if not isinstance(providers, dict) or not all(
        isinstance(rows, list) for rows in providers.values()
    ):
        raise RevisionRefused("providers is not an object of row lists")
    return tuple(lineage), providers


def _validated(raw: Mapping[str, Any], names: Sequence[str]) -> tuple[Row, list[tuple[str, str]]]:
    """``raw``'s fields among ``names`` that validate, and ``(field, reason)`` for those that do not."""

    good: Row = {}
    bad: list[tuple[str, str]] = []
    for name in names:
        if name not in raw:
            continue
        try:
            good[name] = _VALIDATORS[name](raw[name])
        except _Invalid as exc:
            bad.append((name, str(exc)))
    return good, bad


def parse_revision(
    document: Any,
    shipped: Catalog,
    *,
    levels: Sequence[str] | None = None,
) -> ParsedRevision:
    """Parse a revision against this build's ``shipped`` catalog (§4.3).

    ``document`` is the decoded JSON (verify the bytes first with
    :func:`verify_revision_bytes`). ``shipped`` is this build's own catalog in
    ``models_generated.json``'s shape, transport included; it is the only
    source of transport and the only set of providers a revision may touch.
    ``levels`` is this build's thinking levels, defaulting to
    :data:`aelix_ai.models.EXTENDED_THINKING_LEVELS`.

    A known id takes the hosted row as ``main``'s complete facts for it: each
    required field replaces the shipped value, and one that is missing or fails
    validation keeps the shipped value and is noted in ``kept``. A present
    ``thinkingLevelMap`` replaces the shipped map and an absent one removes it.
    A new id needs every required field valid and the ladder's answer over the
    provider's shipped rows; otherwise it is dropped and noted. So is an id
    equal, ignoring case, to a shipped id of the provider (that is not exactly
    it) or to an earlier hosted id. Unknown fields are ignored, transport
    fields are never read, and a provider this build does not ship is ignored.

    Raises :class:`RevisionRefused` (the build installs none of it) when the
    document is not a known schema's object, is over :data:`MAX_ROWS` rows or
    :data:`MAX_PROVIDER_ROWS` for one provider, holds over :data:`MAX_NEW_IDS`
    ids new to this build or :data:`MAX_NEW_IDS_PER_PROVIDER` for one provider,
    or makes more than :data:`REGRESSION_BOUND` known rows lose their thinking
    map or fall below half their shipped ``contextWindow`` or ``maxTokens``.
    It raises nothing else: a row or a value of any JSON type or size that
    fails a rule is a dropped row or a kept field, noted.
    """

    if levels is None:
        from aelix_ai.models import EXTENDED_THINKING_LEVELS

        levels = EXTENDED_THINKING_LEVELS
    level_set = frozenset(levels)
    lineage, providers = _refuse_shape(document)

    total = sum(len(rows) for rows in providers.values())
    if total > MAX_ROWS:
        raise RevisionRefused(f"{total} rows, over {MAX_ROWS}")
    for provider, rows in providers.items():
        if len(rows) > MAX_PROVIDER_ROWS:
            raise RevisionRefused(f"{provider}: {len(rows)} rows, over {MAX_PROVIDER_ROWS}")

    new_total = 0
    for provider, rows in providers.items():
        if provider not in shipped:
            continue
        own = shipped[provider]
        # Only a string can be an id. An array or object id is dropped below
        # as not a valid id; testing it against ``own`` here would raise
        # TypeError (an unhashable key) instead.
        fresh = sum(
            1
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] not in own
        )
        if fresh > MAX_NEW_IDS_PER_PROVIDER:
            raise RevisionRefused(
                f"{provider}: {fresh} ids new to this build, over {MAX_NEW_IDS_PER_PROVIDER}"
            )
        new_total += fresh
    if new_total > MAX_NEW_IDS:
        raise RevisionRefused(f"{new_total} ids new to this build, over {MAX_NEW_IDS}")

    result = ParsedRevision(
        lineage=lineage,
        rows={
            provider: {mid: dict(row) for mid, row in rows.items()}
            for provider, rows in shipped.items()
        },
        ignored_providers=tuple(sorted(p for p in providers if p not in shipped)),
    )

    for provider, rows in providers.items():
        if provider not in shipped:
            continue
        own = shipped[provider]
        own_lower = {model_id.lower(): model_id for model_id in own}
        # The ladder's inputs for every row the revision lists, known ones
        # included: a shipped row's ``family`` and ``releaseDate`` are what
        # the ladder compares a new id against (§4.3).
        upstream: dict[str, Row] = {}
        for raw in rows:
            if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                inputs, _bad = _validated(raw, LADDER_FIELDS)
                upstream.setdefault(
                    raw["id"], ladder_inputs(inputs.get("family"), inputs.get("releaseDate"))
                )
        # A shipped id is matched exactly and always wins its spelling: a case
        # variant listed before it (``ACME-1`` sorts before ``acme-1``) is the
        # row dropped, never the shipped one. A new id is dropped when it
        # equals, ignoring case, a shipped id or an earlier new id.
        seen_known: set[str] = set()
        seen_new: set[str] = set()
        local_models: dict[str, Row] = {mid: dict(row) for mid, row in own.items()}
        for position, raw in enumerate(rows):
            if not isinstance(raw, dict) or not valid_model_id(raw.get("id")):
                result.dropped.append(RowNote(provider, f"#{position}", "id", "not a valid id"))
                continue
            model_id: str = raw["id"]
            lowered = model_id.lower()
            if model_id in own:
                if model_id in seen_known:
                    result.dropped.append(RowNote(provider, model_id, "id", "listed twice"))
                    continue
                seen_known.add(model_id)
                _apply_known(result, provider, model_id, raw, own[model_id], level_set)
                continue
            if lowered in own_lower:
                result.dropped.append(
                    RowNote(provider, model_id, "id", "equals a shipped id ignoring case")
                )
                continue
            if lowered in seen_new:
                result.dropped.append(
                    RowNote(provider, model_id, "id", "equals an earlier hosted id ignoring case")
                )
                continue
            seen_new.add(lowered)
            _add_new(result, provider, model_id, raw, local_models, upstream, level_set)

    if result.regressions > REGRESSION_BOUND:
        raise RevisionRefused(
            f"{result.regressions} known rows lose their thinking map or half their limits, "
            f"over {REGRESSION_BOUND}"
        )
    return result


def _apply_known(
    result: ParsedRevision,
    provider: str,
    model_id: str,
    raw: Mapping[str, Any],
    shipped_row: Mapping[str, Any],
    levels: frozenset[str],
) -> None:
    result.known += 1
    row: Row = dict(shipped_row)
    facts, bad = _validated(raw, _REQUIRED_FACTS)
    for name, reason in bad:
        result.kept.append(RowNote(provider, model_id, name, reason))
    for name in _REQUIRED_FACTS:
        if name in facts:
            row[name] = facts[name]
        elif name not in raw:
            result.kept.append(RowNote(provider, model_id, name, "missing"))
    if "thinkingLevelMap" in raw:
        try:
            row["thinkingLevelMap"] = _thinking_map(raw["thinkingLevelMap"], levels)
        except _Invalid as exc:
            result.kept.append(RowNote(provider, model_id, "thinkingLevelMap", str(exc)))
    else:
        row.pop("thinkingLevelMap", None)
    row["maxTokens"] = min(row["maxTokens"], row["contextWindow"])
    _ladder_notes(result, provider, model_id, raw)

    # The regression bound (§4.3): a row counts once, whichever way it regressed.
    lost_map = bool(shipped_row.get("thinkingLevelMap")) and not row.get("thinkingLevelMap")
    halved = (
        row["contextWindow"] * 2 < shipped_row["contextWindow"]
        or row["maxTokens"] * 2 < shipped_row["maxTokens"]
    )
    if lost_map or halved:
        result.regressions += 1
    if row != dict(shipped_row):
        result.changed += 1
    result.rows[provider][model_id] = row


def _ladder_notes(
    result: ParsedRevision, provider: str, model_id: str, raw: Mapping[str, Any]
) -> None:
    _good, bad = _validated(raw, LADDER_FIELDS)
    for name, reason in bad:
        result.kept.append(RowNote(provider, model_id, name, reason))


def _add_new(
    result: ParsedRevision,
    provider: str,
    model_id: str,
    raw: Mapping[str, Any],
    local_models: dict[str, Row],
    upstream: dict[str, Row],
    levels: frozenset[str],
) -> None:
    facts, bad = _validated(raw, (*_REQUIRED_FACTS, *LADDER_FIELDS))
    missing = [name for name in _REQUIRED_FACTS if name not in raw]
    if bad or missing:
        name, reason = bad[0] if bad else (missing[0], "missing")
        result.dropped.append(RowNote(provider, model_id, name, reason))
        return
    thinking: dict[str, str | None] | None = None
    if "thinkingLevelMap" in raw:
        try:
            thinking = _thinking_map(raw["thinkingLevelMap"], levels)
        except _Invalid as exc:
            result.dropped.append(RowNote(provider, model_id, "thinkingLevelMap", str(exc)))
            return
    transport, why = resolve_transport(model_id, upstream.get(model_id), local_models, upstream)
    if transport is None:
        result.dropped.append(RowNote(provider, model_id, "transport", why))
        return
    api, base_url, headers, compat = transport
    row: Row = {
        "id": model_id,
        "name": facts["name"],
        "api": api,
        "provider": provider,
        "baseUrl": base_url,
        "reasoning": facts["reasoning"],
        "input": facts["input"],
        "cost": facts["cost"],
        "contextWindow": facts["contextWindow"],
        "maxTokens": min(facts["maxTokens"], facts["contextWindow"]),
    }
    if thinking is not None:
        row["thinkingLevelMap"] = thinking
    if headers is not None:
        row["headers"] = json.loads(headers)
    if compat is not None:
        row["compat"] = json.loads(compat)
    result.added.setdefault(provider, {})[model_id] = row
    result.rows[provider][model_id] = row


# ── the index ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IndexEntry:
    """One catalog an index offers: the revision for builds at or above a minimum version.

    ``metadata`` holds the entry's build metadata (``publishedAt``,
    ``sourceCommit``, ``upstream``, ``rows``, ``newRows``) as published. It
    lives in the index only, outside the hashed revision (§4.2 Format).
    """

    minimum_version: str
    revision: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class Hold:
    """The ``hold`` a rollback or withdrawal index carries (§4.2, §4.4)."""

    action: str
    at: str


@dataclass(frozen=True)
class HostedIndex:
    """A parsed ``v1/index.json``. An empty ``catalogs`` is a withdrawal."""

    catalogs: tuple[IndexEntry, ...]
    hold: Hold | None


class IndexInvalid(ValueError):
    """``v1/index.json`` is not schema 1's shape."""


_ENTRY_METADATA: dict[str, Callable[[Any], bool]] = {
    "publishedAt": lambda v: isinstance(v, str) and 0 < len(v) <= 64,
    "sourceCommit": lambda v: isinstance(v, str) and bool(_COMMIT_RE.fullmatch(v)),
    "upstream": lambda v: (
        isinstance(v, dict)
        and all(
            isinstance(v.get(key), str) and bool(_SHA256_RE.fullmatch(v[key]))
            for key in ("modelsDevSha256", "openRouterSha256")
        )
        and isinstance(v.get("fetchedAt"), str)
    ),
    "rows": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    "newRows": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
}


def parse_index(document: Any) -> HostedIndex:
    """Parse a decoded ``v1/index.json`` (§4.2 Format). Raises :class:`IndexInvalid`.

    The protocol fields (``minimumAelixVersion``, ``revision``) are required;
    build metadata is checked for type where present and kept as published.
    One entry per minimum version, as pi's index has; ``hold`` appears only in
    an index a rollback or a withdrawal published.
    """

    if not isinstance(document, dict) or document.get("schema") != INDEX_SCHEMA:
        raise IndexInvalid("not an object of a known schema")
    catalogs = document.get("catalogs")
    if not isinstance(catalogs, list):
        raise IndexInvalid("catalogs is not a list")
    entries: list[IndexEntry] = []
    minimums: set[str] = set()
    for entry in catalogs:
        if not isinstance(entry, dict):
            raise IndexInvalid("a catalog entry is not an object")
        minimum = entry.get("minimumAelixVersion")
        revision = entry.get("revision")
        if not isinstance(minimum, str) or not 0 < len(minimum) <= 64:
            raise IndexInvalid("minimumAelixVersion is not a version string")
        if minimum in minimums:
            raise IndexInvalid(f"two entries for {minimum}")
        minimums.add(minimum)
        if not isinstance(revision, str) or not _REVISION_NAME_RE.fullmatch(revision):
            raise IndexInvalid("revision is not sha256-<64 hex>")
        for key, check in _ENTRY_METADATA.items():
            if key in entry and not check(entry[key]):
                raise IndexInvalid(f"{key} is malformed")
        metadata = {key: entry[key] for key in _ENTRY_METADATA if key in entry}
        entries.append(IndexEntry(minimum, revision, metadata))
    hold: Hold | None = None
    if "hold" in document:
        raw_hold = document["hold"]
        if (
            not isinstance(raw_hold, dict)
            or raw_hold.get("action") not in ("rollback", "withdraw")
            or not isinstance(raw_hold.get("at"), str)
        ):
            raise IndexInvalid("hold is malformed")
        hold = Hold(raw_hold["action"], raw_hold["at"])
    return HostedIndex(tuple(entries), hold)


def select_catalog(
    index: HostedIndex,
    version: str,
    *,
    parse_version: Callable[[str], Any],
) -> IndexEntry | None:
    """The entry with the highest ``minimumAelixVersion`` not above ``version``.

    pi's ``selectModelCatalog`` (``scripts/model-catalog-protocol.ts:127-158``).
    ``parse_version`` turns a version string into something ordered (the
    client passes ``packaging.version.Version``, a coding-agent dependency, so
    aelix-ai takes the predicate as an argument, §4.7). An entry whose minimum
    does not parse is skipped. ``None`` when nothing qualifies, which removes
    the overlay, as an empty ``catalogs`` does.
    """

    current = parse_version(version)
    chosen: IndexEntry | None = None
    chosen_minimum: Any = None
    for entry in index.catalogs:
        try:
            minimum = parse_version(entry.minimum_version)
        except Exception:
            # An unparseable minimum is an entry for no build.
            continue
        if minimum <= current and (chosen_minimum is None or minimum > chosen_minimum):
            chosen, chosen_minimum = entry, minimum
    return chosen
