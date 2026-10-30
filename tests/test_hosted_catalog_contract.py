"""The hosted catalog's contract: what the publisher writes, the client parses.

#172, ADR-0264 §4.2 ("Keeping CI, ``main`` and the hosted file consistent") and
§8. aelix publishes its own catalog, without transport, from
``handochan/aelix-catalog``; ``scripts/publish_catalog.py`` builds each revision
with :mod:`aelix_ai.hosted_catalog`'s one serializer, and the client (#434)
reads it with that module's one parser. This file runs both on every pull
request, without network, so the two cannot drift apart in any commit:

- a revision of the catalog on disk, built with no additions, parses with the
  client's parser, reproduces every row, and its lineage ends in the catalog's
  own digest. In aelix-ai's CI that is the committed catalog; inside the publish
  job it is the applied one, which is the point of running it there;
- ``tests/fixtures/hosted_catalog/v1-sample.json``, a frozen schema-1 revision,
  parses forever with a pinned outcome, and so does its index;
- each publisher check fails once on purpose (§8): pin, additivity, no
  transport, client parse, inheritance, lineage head, additions cap, caps,
  providers, and a held repository;
- the lineage over a synthetic ``main`` reproduces r5's pairs
  (``.omc/probes/434-adr/r5/scripts/lineage_probe.py``): no older facts
  installed as newer, nothing withheld at or after a build's version, a dev
  edit in no lineage, a merge's branch commits outside it.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import re
import subprocess
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from aelix_ai import hosted_catalog as hc
from aelix_ai.catalog_inherit import transport_of
from aelix_ai.models import EXTENDED_THINKING_LEVELS
from packaging.version import Version

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _ROOT / "tests/fixtures/hosted_catalog"
_CATALOG_PATH = _ROOT / "packages/aelix-ai/src/aelix_ai/models_generated.json"


def _load_publisher() -> Any:
    """``scripts/publish_catalog.py``, loaded by path as ``test_catalog_overlay.py`` loads its sibling."""

    path = _ROOT / "scripts/publish_catalog.py"
    spec = importlib.util.spec_from_file_location("publish_catalog", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = _load_publisher()

SHIPPED: dict[str, dict[str, dict[str, Any]]] = json.loads(
    _CATALOG_PATH.read_text(encoding="utf-8")
)

_RECORD = {
    "modelsDevSha256": "a" * 64,
    "openRouterSha256": "b" * 64,
    "fetchedAt": "2026-10-21T01:17:00Z",
}
_COMMIT = "c" * 40

# openrouter/auto's shipped cost is OpenRouter's negative "price varies" sentinel
# (-1000000 per million), outside the parser's 0..10000. Every client keeps the
# shipped value for that one field; the publisher passes it because the value
# kept is the value written (ADR-0264's amendment of 2026-10-21).
_COST_OUTSIDE_BOUNDS = {("openrouter", "openrouter/auto")}

# The two shipped rows whose output cap exceeds their window (upstream's own
# data, pinned in test_catalog_overlay.py). The parser clamps a cap to the
# window (§4.3), so a client installing a revision sees these two clamped.
_OUTPUT_ABOVE_CONTEXT = {
    ("openrouter", "nex-agi/deepseek-v3.1-nex-n1"),
    ("openrouter", "openai/gpt-3.5-turbo-0613"),
}


def _clamped(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["maxTokens"] = min(out["maxTokens"], out["contextWindow"])
    return out


# ── the format ─────────────────────────────────────────────────────────


def test_canonical_bytes_are_sorted_compact_utf8_and_end_without_a_newline() -> None:
    raw = hc.canonical_bytes({"b": 1, "a": {"d": "é", "c": [2, 1]}})
    assert raw == '{"a":{"c":[2,1],"d":"é"},"b":1}'.encode()
    with pytest.raises(ValueError):
        hc.canonical_bytes({"x": float("nan")})


def test_the_digest_sees_facts_not_key_order_layout_or_line_endings() -> None:
    """Canonical, so a CRLF checkout or a re-ordered file is the same catalog (§4.2)."""

    digest = hc.catalog_digest(SHIPPED)
    assert len(digest) == 32 and all(c in "0123456789abcdef" for c in digest)
    reordered = {p: dict(reversed(list(rows.items()))) for p, rows in reversed(SHIPPED.items())}
    assert hc.catalog_digest(reordered) == digest
    crlf = json.dumps(SHIPPED, indent=2).replace("\n", "\r\n")
    assert hc.catalog_digest(json.loads(crlf)) == digest
    edited = copy.deepcopy(SHIPPED)
    provider = next(iter(edited))
    model_id = next(iter(edited[provider]))
    edited[provider][model_id]["contextWindow"] += 1
    assert hc.catalog_digest(edited) != digest


def test_a_revision_is_named_by_its_bytes_and_verified_against_the_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = hc.serialize_revision(hc.build_revision(SHIPPED, [hc.catalog_digest(SHIPPED)]))
    name = hc.revision_name(raw)
    assert name.startswith("sha256-") and len(name) == 7 + 64
    assert hc.verify_revision_bytes(name, raw)
    assert not hc.verify_revision_bytes(name, raw[:-1] + b" ")
    monkeypatch.setattr(hc, "MAX_REVISION_BYTES", len(raw) - 1)
    assert not hc.verify_revision_bytes(name, raw), "an oversized body is a failed fetch"


def test_nothing_time_dependent_is_hashed() -> None:
    """Built twice with different build metadata, one name (r5's finding; §4.2 Format)."""

    one = pc.build_publication(
        committed=SHIPPED,
        lineage=[hc.catalog_digest(SHIPPED)],
        upstream={},
        openrouter_listing={"data": []},
        upstream_record=_RECORD,
        source_commit=_COMMIT,
        published_at="2026-10-21T01:17:00Z",
    )
    two = pc.build_publication(
        committed=SHIPPED,
        lineage=[hc.catalog_digest(SHIPPED)],
        upstream={},
        openrouter_listing={"data": []},
        upstream_record={**_RECORD, "fetchedAt": "2026-10-22T05:17:00Z"},
        source_commit="d" * 40,
        published_at="2026-10-22T05:17:00Z",
    )
    assert one["name"] == two["name"]
    assert one["index"] != two["index"], "the metadata lives in the index"


# ── the contract over the shipped catalog ──────────────────────────────


def test_a_revision_of_this_catalog_parses_and_reproduces_every_row() -> None:
    """§4.2: built with no additions, the revision the client parser accepts reproduces every row.

    Whole rows, transport included: the effective catalog a client builds
    from the revision is this catalog, but for the two caps the parser clamps.
    """

    digest = hc.catalog_digest(SHIPPED)
    built = pc.build_publication(
        committed=SHIPPED,
        lineage=[digest],
        upstream={},
        openrouter_listing={"data": []},
        upstream_record=_RECORD,
        source_commit=_COMMIT,
    )
    assert built["state"] == "publish"
    parsed = hc.parse_revision(json.loads(built["raw"]), SHIPPED)
    assert parsed.dropped == [] and parsed.ignored_providers == () and parsed.added == {}
    assert {(n.provider, n.model_id, n.field) for n in parsed.kept} == {
        (p, m, "cost") for p, m in _COST_OUTSIDE_BOUNDS
    }
    assert hc.lineage_admits(parsed.lineage, digest) and parsed.lineage[-1] == digest
    clamped = set()
    for provider, rows in SHIPPED.items():
        assert list(parsed.rows[provider]) == list(rows), f"{provider}: order changed"
        for model_id, row in rows.items():
            effective = parsed.rows[provider][model_id]
            if effective != row:
                clamped.add((provider, model_id))
                assert effective == _clamped(row)
    assert clamped == _OUTPUT_ABOVE_CONTEXT
    assert parsed.changed == len(_OUTPUT_ABOVE_CONTEXT)


def test_the_next_index_replaces_its_own_floors_entry_and_keeps_the_others() -> None:
    """Review round 2, as pi's index keeps one entry per ``minimumPiVersion``: once
    MINIMUM_AELIX_VERSION is raised, the older floor's entry stays for the builds below it."""

    def entry(floor: str, name: str) -> dict[str, Any]:
        return {
            "minimumAelixVersion": floor,
            "revision": name,
            "publishedAt": "2026-10-21T01:17:00Z",
            "sourceCommit": _COMMIT,
            "upstream": _RECORD,
            "rows": 4,
            "newRows": 0,
        }

    older, current, fresh = "sha256-" + "1" * 64, "sha256-" + "2" * 64, "sha256-" + "3" * 64
    previous = {
        "schema": hc.INDEX_SCHEMA,
        "catalogs": [entry("0.0.9", older), entry(pc.MINIMUM_AELIX_VERSION, current)],
    }
    index = pc.next_index(previous, entry(pc.MINIMUM_AELIX_VERSION, fresh))
    floors = [(e["minimumAelixVersion"], e["revision"]) for e in index["catalogs"]]
    assert floors == [("0.0.9", older), (pc.MINIMUM_AELIX_VERSION, fresh)]
    parsed = hc.parse_index(index)
    for build, want in (("0.0.9", older), ("0.1.0a1", older), (pc.MINIMUM_AELIX_VERSION, fresh)):
        selected = hc.select_catalog(parsed, build, parse_version=Version)
        assert selected is not None and selected.revision == want, build


def test_the_index_the_publisher_writes_selects_its_revision_for_this_build() -> None:
    """The ``0.1.0b2`` floor (§4.2) admits every checkout until R bumps the version."""

    from importlib.metadata import version

    built = pc.build_publication(
        committed=SHIPPED,
        lineage=[hc.catalog_digest(SHIPPED)],
        upstream={},
        openrouter_listing={"data": []},
        upstream_record=_RECORD,
        source_commit=_COMMIT,
    )
    index = hc.parse_index(json.loads(hc.canonical_bytes(built["index"])))
    assert index.hold is None
    entry = hc.select_catalog(index, version("aelix-ai"), parse_version=Version)
    assert entry is not None and entry.revision == built["name"]
    assert entry.minimum_version == pc.MINIMUM_AELIX_VERSION == "0.1.0b2"
    assert hc.select_catalog(index, "0.1.0b1", parse_version=Version) is None


def test_every_shipped_row_fits_what_a_hosted_row_may_set() -> None:
    """§4.3's bounds sit below every shipped row (§3.2), or the publisher could never pass.

    MEASURED while writing the parser (2026-10-21): the ADR's map-value rule
    ``[a-z0-9_-]`` refused 8 shipped Google rows, whose wire values are
    Gemini's own ``HIGH``, ``LOW`` and ``MINIMAL``; the amendment widens it to
    both cases. ``openrouter/auto``'s negative cost is the one value outside a
    bound, and a client keeps it as shipped.
    """

    lower: dict[str, set[str]] = {}
    for provider, rows in SHIPPED.items():
        for model_id, row in rows.items():
            assert hc.valid_model_id(model_id), (provider, model_id)
            assert model_id.lower() not in lower.setdefault(provider, set()), (provider, model_id)
            lower[provider].add(model_id.lower())
            parsed = hc.parse_revision(
                {
                    "schema": hc.REVISION_SCHEMA,
                    "lineage": ["0" * 32],
                    "providers": {provider: [hc.revision_row(row)]},
                },
                {provider: {model_id: row}},
            )
            kept = {(n.field, n.reason) for n in parsed.kept}
            if (provider, model_id) in _COST_OUTSIDE_BOUNDS:
                assert {field for field, _ in kept} == {"cost"}
            else:
                assert not kept, (provider, model_id, kept)
    assert sum(len(rows) for rows in SHIPPED.values()) <= hc.MAX_ROWS
    assert max(len(rows) for rows in SHIPPED.values()) <= hc.MAX_PROVIDER_ROWS


def test_no_published_row_carries_transport() -> None:
    revision = hc.build_revision(SHIPPED, [hc.catalog_digest(SHIPPED)])
    for rows in revision["providers"].values():
        for row in rows:
            assert not set(row) & set(hc.TRANSPORT_FIELDS), row["id"]
            assert set(row) <= set(hc.FACT_FIELDS) | set(hc.LADDER_FIELDS)


# ── the frozen schema-1 samples ────────────────────────────────────────

# Copied verbatim from .omc/probes/b4-d-live/172/probes/make_sample.py, which
# wrote v1-sample.json against it. Frozen with the sample: never edit either.
SAMPLE_SHIPPED: dict[str, dict[str, dict[str, Any]]] = {
    "acme": {
        "acme-chat-1": {
            "id": "acme-chat-1",
            "name": "Acme Chat 1",
            "api": "openai-completions",
            "provider": "acme",
            "baseUrl": "https://api.acme.example/v1",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 1, "output": 2, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 128000,
            "maxTokens": 8192,
        },
        "acme-think-2": {
            "id": "acme-think-2",
            "name": "Acme Think 2",
            "api": "openai-completions",
            "provider": "acme",
            "baseUrl": "https://api.acme.example/v1",
            "reasoning": True,
            "input": ["text", "image"],
            "cost": {"input": 3, "output": 15, "cacheRead": 0.3, "cacheWrite": 3.75},
            "contextWindow": 200000,
            "maxTokens": 64000,
            "thinkingLevelMap": {"xhigh": "xhigh"},
            "compat": {"supportsReasoningEffort": True},
        },
    },
    "orbit": {
        "orbit/alpha-1": {
            "id": "orbit/alpha-1",
            "name": "Alpha 1",
            "api": "anthropic-messages",
            "provider": "orbit",
            "baseUrl": "https://orbit.example",
            "reasoning": True,
            "input": ["text"],
            "cost": {"input": 1, "output": 5, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 32000,
        },
        "orbit/beta-1": {
            "id": "orbit/beta-1",
            "name": "Beta 1",
            "api": "openai-responses",
            "provider": "orbit",
            "baseUrl": "https://orbit.example/v1",
            "reasoning": True,
            "input": ["text"],
            "cost": {"input": 2, "output": 8, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 400000,
            "maxTokens": 128000,
        },
    },
}
_SAMPLE_NAME = "sha256-4dae027564100c90cfdcdbe5fc2a247daadbcf878131006ddc716154672948cd"
_SAMPLE_DIGEST = "7d93b1ebc8d05e8d1578a092ab0a98e6"


def test_the_frozen_v1_sample_parses_forever_under_schema_1() -> None:
    """§4.2: a frozen sample "must parse forever under schema 1", with this outcome.

    It pins the digest algorithm (the sample's lineage must keep admitting
    ``SAMPLE_SHIPPED``), the content address, the canonical serializer, and
    every row rule a schema-1 client applies: transport fields ignored on a
    known id, an absent map removing the shipped one, an invalid field kept
    from the shipped row, a new id inheriting by family, a new id the ladder
    cannot answer dropped, a case variant of a shipped id dropped (and never
    the shipped id it shadows), an unknown provider and an unknown field
    ignored, a cap clamped to its window.
    """

    raw = (_FIXTURES / "v1-sample.json").read_bytes()
    assert hc.revision_name(raw) == _SAMPLE_NAME, "the frozen sample was edited"
    assert hc.serialize_revision(json.loads(raw)) == raw, "the serializer is no longer canonical"
    assert hc.catalog_digest(SAMPLE_SHIPPED) == _SAMPLE_DIGEST, "the digest algorithm changed"

    parsed = hc.parse_revision(json.loads(raw), SAMPLE_SHIPPED)
    assert hc.lineage_admits(parsed.lineage, _SAMPLE_DIGEST)
    assert (parsed.known, parsed.changed, parsed.regressions) == (4, 2, 1)
    assert parsed.ignored_providers == ("nowhere",)
    assert [(n.provider, n.model_id, n.field) for n in parsed.dropped] == [
        ("acme", "ACME-CHAT-1", "id"),
        ("orbit", "orbit/gamma-1", "transport"),
    ]
    assert [(n.provider, n.model_id, n.field) for n in parsed.kept] == [
        ("acme", "acme-think-2", "maxTokens")
    ]

    chat = parsed.rows["acme"]["acme-chat-1"]
    assert transport_of(chat) == transport_of(SAMPLE_SHIPPED["acme"]["acme-chat-1"])
    assert chat["provider"] == "acme" and "headers" not in chat and "futureField" not in chat
    assert (chat["name"], chat["contextWindow"], chat["maxTokens"]) == (
        "Acme Chat 1 (2026)",
        256000,
        16384,
    )
    assert chat["cost"] == {"input": 0.5, "output": 1.5, "cacheRead": 0.05, "cacheWrite": 0}

    think = parsed.rows["acme"]["acme-think-2"]
    assert "thinkingLevelMap" not in think, "an absent map must remove the shipped one"
    assert think["maxTokens"] == 64000, "an invalid field keeps the shipped value"

    new = parsed.added["acme"]["acme-think-3"]
    assert transport_of(new) == transport_of(SAMPLE_SHIPPED["acme"]["acme-think-2"])
    assert new["input"] == ["text", "image"]
    assert new["maxTokens"] == new["contextWindow"] == 1000000
    assert new["thinkingLevelMap"] == {"off": "none", "minimal": None, "high": "high", "max": "max"}
    alpha = parsed.added["orbit"]["orbit/alpha-2"]
    assert (alpha["api"], alpha["baseUrl"]) == ("anthropic-messages", "https://orbit.example")
    assert list(parsed.rows["orbit"]) == ["orbit/alpha-1", "orbit/beta-1", "orbit/alpha-2"]


def test_the_frozen_index_sample_parses_and_selects_by_version() -> None:
    index = hc.parse_index(json.loads((_FIXTURES / "v1-index-sample.json").read_bytes()))
    assert index.hold is None and len(index.catalogs) == 1
    assert index.catalogs[0].revision == _SAMPLE_NAME
    assert index.catalogs[0].metadata["newRows"] == 3
    for version, expected in (
        ("0.1.0b1", None),
        ("0.1.0b2", _SAMPLE_NAME),
        ("0.2.0", _SAMPLE_NAME),
    ):
        entry = hc.select_catalog(index, version, parse_version=Version)
        assert (entry.revision if entry else None) == expected, version


def test_an_index_picks_the_highest_floor_not_above_the_build_and_reads_its_hold() -> None:
    entries = [
        {"minimumAelixVersion": floor, "revision": "sha256-" + c * 64}
        for floor, c in (("0.1.0b2", "1"), ("0.1.0", "2"), ("0.3.0", "3"))
    ]
    index = hc.parse_index(
        {"schema": hc.INDEX_SCHEMA, "catalogs": entries, "hold": {"action": "rollback", "at": "t"}}
    )
    assert index.hold == hc.Hold("rollback", "t")
    chosen = hc.select_catalog(index, "0.2.1", parse_version=Version)
    assert chosen is not None and chosen.revision == "sha256-" + "2" * 64
    withdrawn = hc.parse_index({"schema": hc.INDEX_SCHEMA, "catalogs": []})
    assert hc.select_catalog(withdrawn, "0.2.1", parse_version=Version) is None
    for broken in (
        {"schema": "aelix-model-catalog-index/2", "catalogs": []},
        {"schema": hc.INDEX_SCHEMA},
        {"schema": hc.INDEX_SCHEMA, "catalogs": [entries[0], entries[0]]},
        {"schema": hc.INDEX_SCHEMA, "catalogs": [{**entries[0], "revision": "sha256-xyz"}]},
        {"schema": hc.INDEX_SCHEMA, "catalogs": [{**entries[0], "sourceCommit": "main"}]},
        {"schema": hc.INDEX_SCHEMA, "catalogs": [], "hold": {"action": "pause", "at": "t"}},
    ):
        with pytest.raises(hc.IndexInvalid):
            hc.parse_index(broken)


# ── what a hosted row may set (§4.3) ───────────────────────────────────


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-6",
        "us.anthropic.claude-opus-4-7",
        "@cf/meta/llama-4",
        "~openai/gpt-luna-latest",
        "openai/gpt-6:batch",
        "a" * 256,
    ],
)
def test_ids_that_fit_the_rule(model_id: str) -> None:
    assert hc.valid_model_id(model_id)


@pytest.mark.parametrize(
    "model_id",
    [
        "",
        "a" * 257,
        "-leading-dash",
        ".hidden",
        "a//b",
        "a/./b",
        "a/../b",
        "trailing/",
        "space id",
        "id?x=1",
        "id#frag",
        "ünicode",
        "a\\b",
        "%2e%2e",
        7,
        None,
    ],
)
def test_ids_that_could_reach_a_url_or_an_argv_wrongly_are_refused(model_id: Any) -> None:
    assert not hc.valid_model_id(model_id)


def _one(provider_rows: list[dict[str, Any]], shipped: dict[str, Any] | None = None) -> Any:
    return hc.parse_revision(
        {
            "schema": hc.REVISION_SCHEMA,
            "lineage": [_SAMPLE_DIGEST],
            "providers": {"acme": provider_rows},
        },
        shipped if shipped is not None else SAMPLE_SHIPPED,
    )


def _known(**overrides: Any) -> dict[str, Any]:
    row = hc.revision_row(SAMPLE_SHIPPED["acme"]["acme-think-2"], {"family": "acme-think"})
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reasoning", 1),
        ("reasoning", "true"),
        ("input", ["image"]),
        ("input", ["text", "audio"]),
        ("cost", {"input": True, "output": 0, "cacheRead": 0, "cacheWrite": 0}),
        ("cost", {"input": float("nan"), "output": 0, "cacheRead": 0, "cacheWrite": 0}),
        ("cost", {"input": -1, "output": 0, "cacheRead": 0, "cacheWrite": 0}),
        ("cost", {"input": 10001, "output": 0, "cacheRead": 0, "cacheWrite": 0}),
        ("cost", {"input": 1, "output": 1, "cacheRead": 0}),
        ("contextWindow", 1023),
        ("contextWindow", 100_000_001),
        ("contextWindow", 128000.5),
        ("contextWindow", True),
        ("maxTokens", 255),
        ("name", "x" * 201),
        ("name", "\x1b\x07"),
        ("thinkingLevelMap", {"ultra": "ultra"}),
        ("thinkingLevelMap", {"high": "HIGH; rm"}),
        ("thinkingLevelMap", {"high": "x" * 33}),
        ("thinkingLevelMap", ["high"]),
        ("family", "Claude Opus"),
        ("releaseDate", "2026-10-21T00:00:00Z"),
    ],
)
def test_a_field_that_fails_validation_keeps_the_shipped_value_on_a_known_row(
    field: str, value: Any
) -> None:
    parsed = _one([_known(**{field: value})])
    assert [(n.model_id, n.field) for n in parsed.kept] == [("acme-think-2", field)]
    effective = parsed.rows["acme"]["acme-think-2"]
    if field in ("family", "releaseDate"):
        return
    assert effective.get(field) == SAMPLE_SHIPPED["acme"]["acme-think-2"].get(field)


def test_the_same_field_drops_a_new_row() -> None:
    parsed = _one([_known(id="acme-think-9", contextWindow=1023)])
    assert [(n.model_id, n.field) for n in parsed.dropped] == [("acme-think-9", "contextWindow")]
    assert "acme-think-9" not in parsed.rows["acme"]


def _from_json_text(provider_rows: list[dict[str, Any]]) -> Any:
    """:func:`_one` over the document as JSON text, as a revision arrives: a long integer
    literal comes back as a Python ``int``."""

    text = json.dumps(
        {
            "schema": hc.REVISION_SCHEMA,
            "lineage": [_SAMPLE_DIGEST],
            "providers": {"acme": provider_rows},
        }
    )
    return hc.parse_revision(json.loads(text), SAMPLE_SHIPPED)


@pytest.mark.parametrize("bad_id", [[], {}, ["acme-think-2"], {"id": "acme-think-2"}])
def test_a_row_whose_id_is_an_array_or_an_object_is_dropped_not_raised(bad_id: Any) -> None:
    """Review round 2: the new-id count tested ``id not in own`` before any id rule, so an
    unhashable id raised TypeError out of the parser instead of being dropped (§4.3)."""

    parsed = _from_json_text([_known(), _known(id=bad_id)])
    assert [(n.model_id, n.field, n.reason) for n in parsed.dropped] == [
        ("#1", "id", "not a valid id")
    ]
    assert parsed.known == 1 and parsed.added_count == 0


_HUGE_INT = int("1" + "0" * 400)
"""A JSON integer literal Python reads as an ``int``; ``float()`` of it overflows."""


def test_a_huge_integer_cost_keeps_the_field_on_a_known_row_and_drops_a_new_one() -> None:
    """Review round 2: ``math.isfinite`` of a 400-digit int raised OverflowError (§4.3: cost is
    four finite numbers of at most $10,000 per million; a known row keeps, a new row drops)."""

    cost = {"input": _HUGE_INT, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    known = _from_json_text([_known(cost=cost)])
    assert [(n.model_id, n.field) for n in known.kept] == [("acme-think-2", "cost")]
    shipped_cost = SAMPLE_SHIPPED["acme"]["acme-think-2"]["cost"]
    assert known.rows["acme"]["acme-think-2"]["cost"] == shipped_cost
    new = _from_json_text([_known(id="acme-think-9", cost=cost)])
    assert [(n.model_id, n.field) for n in new.dropped] == [("acme-think-9", "cost")]
    assert "acme-think-9" not in new.rows["acme"]


def test_no_json_value_in_any_field_raises_anything_but_a_refusal() -> None:
    """§4.3: the parser refuses a revision whole or notes a row; it never raises otherwise.
    Each field of a known and of a new row takes each JSON shape that broke it or could."""

    shapes: list[Any] = [None, True, 0, -1, _HUGE_INT, -_HUGE_INT, 1e308, 3.5, "", "x" * 300]
    shapes += [[], {}, [[]], {"input": []}, ["text", {}], {"high": []}, {"high": {}}]
    shapes += [{key: _HUGE_INT for key in ("input", "output", "cacheRead", "cacheWrite")}]
    fields = [*hc.FACT_FIELDS, *hc.LADDER_FIELDS]
    assert "id" in fields
    for model_id in ("acme-think-2", "acme-think-9"):
        for name in fields:
            for value in shapes:
                for whole in (False, True):
                    row = {"id": model_id} if whole else _known(id=model_id)
                    row[name] = value
                    with contextlib.suppress(hc.RevisionRefused):
                        _from_json_text([row])


def test_a_new_row_missing_a_required_fact_is_dropped() -> None:
    row = _known(id="acme-think-9")
    del row["cost"]
    parsed = _one([row])
    assert [(n.model_id, n.field, n.reason) for n in parsed.dropped] == [
        ("acme-think-9", "cost", "missing")
    ]


def test_a_known_row_missing_a_required_fact_keeps_it() -> None:
    row = _known()
    del row["name"]
    parsed = _one([row])
    assert [(n.field, n.reason) for n in parsed.kept] == [("name", "missing")]
    assert parsed.rows["acme"]["acme-think-2"]["name"] == "Acme Think 2"


def test_a_present_map_replaces_the_shipped_one_and_wire_values_keep_their_case() -> None:
    parsed = _one([_known(thinkingLevelMap={"high": "HIGH", "off": None})])
    assert parsed.rows["acme"]["acme-think-2"]["thinkingLevelMap"] == {"high": "HIGH", "off": None}
    assert parsed.regressions == 0


def test_a_terminal_steering_name_is_stripped_not_refused() -> None:
    parsed = _one([_known(name="Acme\x1b[31m Think\x07 2")])
    assert parsed.rows["acme"]["acme-think-2"]["name"] == "Acme[31m Think 2"
    assert not parsed.kept


def test_case_variants_of_a_new_id_keep_the_first() -> None:
    parsed = _one([_known(id="Acme-New"), _known(id="acme-new")])
    assert list(parsed.added["acme"]) == ["Acme-New"]
    assert [(n.model_id, n.reason) for n in parsed.dropped] == [
        ("acme-new", "equals an earlier hosted id ignoring case")
    ]


def test_a_known_id_listed_twice_takes_the_first() -> None:
    parsed = _one([_known(name="First"), _known(name="Second")])
    assert parsed.rows["acme"]["acme-think-2"]["name"] == "First"
    assert [(n.model_id, n.reason) for n in parsed.dropped] == [("acme-think-2", "listed twice")]


def test_a_new_id_inherits_only_from_its_own_providers_shipped_rows() -> None:
    """The ladder never reads hosted or user rows (§4.3): a new row is no sibling for the next."""

    parsed = hc.parse_revision(
        {
            "schema": hc.REVISION_SCHEMA,
            "lineage": [_SAMPLE_DIGEST],
            "providers": {
                "orbit": [
                    {**_known(id="orbit/gamma-1", family="gamma")},
                    {**_known(id="orbit/gamma-2", family="gamma")},
                ]
            },
        },
        SAMPLE_SHIPPED,
    )
    assert parsed.added == {}
    assert {n.model_id for n in parsed.no_transport} == {"orbit/gamma-1", "orbit/gamma-2"}


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"schema": "aelix-model-catalog/2", "lineage": ["0" * 32], "providers": {}},
        {"schema": hc.REVISION_SCHEMA, "lineage": [], "providers": {}},
        {"schema": hc.REVISION_SCHEMA, "lineage": ["XYZ"], "providers": {}},
        {"schema": hc.REVISION_SCHEMA, "lineage": ["0" * 32] * 501, "providers": {}},
        {"schema": hc.REVISION_SCHEMA, "lineage": ["0" * 32], "providers": []},
        {"schema": hc.REVISION_SCHEMA, "lineage": ["0" * 32], "providers": {"acme": {}}},
    ],
)
def test_a_document_that_is_not_schema_1_is_refused_whole(document: Any) -> None:
    with pytest.raises(hc.RevisionRefused):
        hc.parse_revision(document, SAMPLE_SHIPPED)


@pytest.mark.parametrize(
    ("constant", "rows", "new"),
    [
        ("MAX_ROWS", 3, False),
        ("MAX_PROVIDER_ROWS", 3, False),
        ("MAX_NEW_IDS", 3, True),
        ("MAX_NEW_IDS_PER_PROVIDER", 3, True),
    ],
)
def test_the_whole_revision_bounds_refuse(
    monkeypatch: pytest.MonkeyPatch, constant: str, rows: int, new: bool
) -> None:
    monkeypatch.setattr(hc, constant, rows - 1)
    listed = [_known(id=f"acme-think-{9 + n}" if new else "acme-think-2") for n in range(rows)]
    with pytest.raises(hc.RevisionRefused):
        _one(listed)
    monkeypatch.setattr(hc, constant, rows)
    _one(listed)


def test_the_regression_bound_refuses_more_than_25_rows_losing_maps_or_half_their_limits() -> None:
    """A forged revision that strips every map, sets every window to 1,024 or every output cap
    to 256 is refused whole. ``tokens`` keeps each window, so only the ``maxTokens`` half of
    the rule sees it (review round 2: ``window`` clamps the cap too, so it never did alone)."""

    mapped = [
        (p, m) for p, rows in SHIPPED.items() for m, r in rows.items() if r.get("thinkingLevelMap")
    ]
    assert len(mapped) > hc.REGRESSION_BOUND
    assert all(SHIPPED[p][m]["maxTokens"] > 2 * 256 for p, m in mapped)

    def revision(regressed: int, how: str) -> dict[str, Any]:
        providers: dict[str, list[dict[str, Any]]] = {}
        for n, (provider, model_id) in enumerate(mapped):
            row = hc.revision_row(SHIPPED[provider][model_id])
            if n < regressed:
                if how == "map":
                    del row["thinkingLevelMap"]
                elif how == "window":
                    row["contextWindow"] = 1024
                else:
                    row["maxTokens"] = 256
            providers.setdefault(provider, []).append(row)
        return {"schema": hc.REVISION_SCHEMA, "lineage": ["0" * 32], "providers": providers}

    for how in ("map", "window", "tokens"):
        assert hc.parse_revision(revision(hc.REGRESSION_BOUND, how), SHIPPED).regressions == 25
        with pytest.raises(hc.RevisionRefused, match="lose their thinking map"):
            hc.parse_revision(revision(hc.REGRESSION_BOUND + 1, how), SHIPPED)


def test_a_map_naming_a_level_this_build_lacks_is_a_failed_field() -> None:
    """An older build's parser rejects ``max`` on a known row: a failed field, kept.

    The publish summary's release check (``release_report``) lists only the rows
    it drops, and its list of kept fields parses with ``main``'s levels, so this
    case never reaches the summary (ADR-0264 §10 2026-10-30).
    """

    pre_433 = [level for level in EXTENDED_THINKING_LEVELS if level != "max"]
    parsed = hc.parse_revision(
        {
            "schema": hc.REVISION_SCHEMA,
            "lineage": ["0" * 32],
            "providers": {"acme": [_known(thinkingLevelMap={"max": "max"})]},
        },
        SAMPLE_SHIPPED,
        levels=pre_433,
    )
    assert [(n.field) for n in parsed.kept] == ["thinkingLevelMap"]
    assert parsed.kept[0].reason == "level 'max' is not one of this build's"


def test_a_note_quotes_a_published_map_key_cut_to_32_characters() -> None:
    """Review round 2: the reason quoted a published key whole, though RowNote said a note
    quotes nothing published; keys have no length bound. Now it is cut, and RowNote says so."""

    key = "PUBLISHED-TEXT-" + "A" * 5000
    parsed = _one([_known(thinkingLevelMap={key: "high"})])
    (note,) = parsed.kept
    assert note.reason == f"level {key[:32]!r}... is not one of this build's"
    assert len(note.reason) < 80


# ── the publisher's checks, each made to fail once (§8) ────────────────


def _applied_with_new_row(**overrides: Any) -> dict[str, dict[str, dict[str, Any]]]:
    applied = copy.deepcopy(SAMPLE_SHIPPED)
    row = copy.deepcopy(SAMPLE_SHIPPED["acme"]["acme-think-2"])
    row.update({"id": "acme-think-3", "name": "Acme Think 3"})
    row.update(overrides)
    applied["acme"]["acme-think-3"] = row
    return applied


def _raw(catalog: dict[str, Any], inputs: dict[str, Any] | None = None) -> bytes:
    inputs = inputs or {
        "acme": {"acme-think-2": {"family": "acme-think"}, "acme-think-3": {"family": "acme-think"}}
    }
    return hc.serialize_revision(
        hc.build_revision(catalog, [hc.catalog_digest(SAMPLE_SHIPPED)], inputs)
    )


def test_the_checks_pass_a_good_revision() -> None:
    """The positive control the failures below are measured against."""

    applied = _applied_with_new_row()
    raw = _raw(applied)
    revision = json.loads(raw)
    assert pc.check_additive(SAMPLE_SHIPPED, applied) == []
    assert pc.check_no_transport(revision) == []
    assert pc.check_client_parse(raw, SAMPLE_SHIPPED, applied) == []
    assert pc.check_caps(raw, revision) == []
    assert pc.check_providers(revision, SAMPLE_SHIPPED) == []
    assert pc.check_lineage_head(revision["lineage"], SAMPLE_SHIPPED) == []


def test_the_additivity_check_fails_on_a_changed_dropped_or_moved_row() -> None:
    changed = _applied_with_new_row()
    changed["acme"]["acme-chat-1"] = {**changed["acme"]["acme-chat-1"], "contextWindow": 1}
    assert any("changed" in p for p in pc.check_additive(SAMPLE_SHIPPED, changed))
    dropped = _applied_with_new_row()
    del dropped["acme"]["acme-chat-1"]
    assert any("dropped" in p for p in pc.check_additive(SAMPLE_SHIPPED, dropped))
    moved = _applied_with_new_row()
    moved["acme"] = dict(reversed(list(moved["acme"].items())))
    assert any("moved" in p for p in pc.check_additive(SAMPLE_SHIPPED, moved))
    added = {**_applied_with_new_row(), "newco": {}}
    assert any("providers" in p for p in pc.check_additive(SAMPLE_SHIPPED, added))


def test_the_no_transport_check_fails_on_a_row_that_carries_one() -> None:
    revision = json.loads(_raw(_applied_with_new_row()))
    revision["providers"]["acme"][0]["baseUrl"] = "https://evil.example"
    assert pc.check_no_transport(revision)


def test_the_client_parse_check_fails_on_a_row_the_client_drops() -> None:
    applied = _applied_with_new_row(contextWindow=100, maxTokens=100)
    problems = pc.check_client_parse(_raw(applied), SAMPLE_SHIPPED, applied)
    assert any("acme-think-3 dropped" in p for p in problems), problems


def test_the_client_parse_check_fails_when_a_kept_back_value_differs() -> None:
    """A known row's invalid field is kept from the shipped row; that passes only when they agree."""

    applied = copy.deepcopy(SAMPLE_SHIPPED)
    applied["acme"]["acme-chat-1"]["cost"] = {
        "input": -5,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
    }
    problems = pc.check_client_parse(_raw(applied), SAMPLE_SHIPPED, applied)
    assert any("facts differ" in p for p in problems), problems


def test_the_client_parse_check_fails_on_a_ladder_input_the_parser_rejects() -> None:
    """§4.2 step 3: "any other value the parser rejects fails the run, loudly" - known rows too."""

    applied = _applied_with_new_row()
    inputs = {
        "acme": {"acme-chat-1": {"family": "two words"}, "acme-think-3": {"family": "acme-think"}}
    }
    problems = pc.check_client_parse(_raw(applied, inputs), SAMPLE_SHIPPED, applied)
    assert any("acme-chat-1 family rejected" in p for p in problems), problems


def test_the_inheritance_check_fails_when_the_client_would_inherit_another_transport() -> None:
    applied = _applied_with_new_row(baseUrl="https://elsewhere.example/v1")
    problems = pc.check_client_parse(_raw(applied), SAMPLE_SHIPPED, applied)
    assert any(p.startswith("inheritance:") for p in problems), problems


def test_the_caps_check_fails_over_a_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _raw(_applied_with_new_row())
    monkeypatch.setattr(hc, "MAX_PROVIDER_ROWS", 2)
    assert pc.check_caps(raw, json.loads(raw))
    monkeypatch.setattr(hc, "MAX_REVISION_BYTES", 10)
    assert any("bytes" in p for p in pc.check_caps(raw, json.loads(raw)))


def test_the_provider_check_fails_on_a_missing_or_an_extra_provider() -> None:
    revision = json.loads(_raw(_applied_with_new_row()))
    missing = {**revision, "providers": {"acme": revision["providers"]["acme"]}}
    assert pc.check_providers(missing, SAMPLE_SHIPPED) == ["providers: orbit missing"]
    extra = {**revision, "providers": {**revision["providers"], "newco": []}}
    assert pc.check_providers(extra, SAMPLE_SHIPPED) == [
        "providers: newco is not a catalogued provider"
    ]


def test_the_lineage_head_check_fails_when_the_lineage_ends_elsewhere() -> None:
    assert pc.check_lineage_head(["0" * 32], SAMPLE_SHIPPED)
    assert pc.check_lineage_head([], SAMPLE_SHIPPED)


def test_the_additions_cap_counts_ids_new_since_the_previous_publication() -> None:
    revision = json.loads(_raw(_applied_with_new_row()))
    previous = {p: set(rows) for p, rows in SAMPLE_SHIPPED.items()}
    fresh = pc.new_ids(revision, previous)
    assert fresh == {"acme": ["acme-think-3"]}
    assert pc.check_additions_cap(fresh, 1) == []
    assert pc.check_additions_cap(fresh, 0)


def _models_dev(*model_ids: str) -> dict[str, Any]:
    row = {
        "name": "Acme Think",
        "tool_call": True,
        "reasoning": True,
        "family": "acme-think",
        "release_date": "2026-10-01",
        "modalities": {"input": ["text"]},
        "cost": {"input": 1, "output": 2},
        "limit": {"context": 200000, "output": 32000},
    }
    return {"acme": {"models": {mid: {**row, "name": mid} for mid in model_ids}}}


def test_a_build_over_the_cap_publishes_nothing_and_says_why() -> None:
    upstream = _models_dev("acme-think-7", "acme-think-8", "acme-think-9")
    kwargs: dict[str, Any] = {
        "committed": SAMPLE_SHIPPED,
        "lineage": [_SAMPLE_DIGEST],
        "upstream": upstream,
        "openrouter_listing": {"data": []},
        "upstream_record": _RECORD,
        "source_commit": _COMMIT,
    }
    with pytest.raises(pc.BuildFailed) as failed:
        pc.build_publication(**kwargs, max_additions=2)
    assert any(f.startswith("additions-cap:") for f in failed.value.failures)
    assert "FAILED - nothing is published" in failed.value.summary
    built = pc.build_publication(**kwargs, max_additions=3)
    assert built["state"] == "publish"
    assert sorted(built["applied"]["acme"])[-3:] == ["acme-think-7", "acme-think-8", "acme-think-9"]


def test_a_slot_with_nothing_new_is_unchanged_unless_the_index_still_carries_a_hold() -> None:
    kwargs: dict[str, Any] = {
        "committed": SAMPLE_SHIPPED,
        "lineage": [_SAMPLE_DIGEST],
        "upstream": {},
        "openrouter_listing": {"data": []},
        "upstream_record": _RECORD,
        "source_commit": _COMMIT,
    }
    first = pc.build_publication(**kwargs)
    assert first["state"] == "publish"
    assert pc.build_publication(**kwargs, previous_index=first["index"])["state"] == "unchanged"
    held = {**first["index"], "hold": {"action": "rollback", "at": "t"}}
    again = pc.build_publication(**kwargs, previous_index=held)
    assert again["state"] == "publish" and "hold" not in again["index"]


# ── each check on the publish path (review round 2) ────────────────────
#
# The rows above call each check function. These run build_publication, the path the
# CLI takes, with an input that trips exactly one check: dropping that check's
# ``failures +=`` line from build_publication turns its row red.


def _build(upstream: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "committed": SAMPLE_SHIPPED,
        "lineage": [_SAMPLE_DIGEST],
        "upstream": upstream if upstream is not None else _models_dev("acme-think-7"),
        "openrouter_listing": {"data": []},
        "upstream_record": _RECORD,
        "source_commit": _COMMIT,
    }
    kwargs.update(overrides)
    return pc.build_publication(**kwargs)


def _trip_additive(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The applied catalog moves a committed row's transport (no fact, so no other check sees it)."""

    real = pc.rc.merge

    def merge(committed: Any, additions: Any) -> Any:
        applied = copy.deepcopy(real(committed, additions))
        applied["acme"]["acme-chat-1"]["baseUrl"] = "https://elsewhere.example/v1"
        return applied

    monkeypatch.setattr(pc.rc, "merge", merge)
    return {}


def _trip_no_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One published row carries ``baseUrl`` (the client's parser never reads it)."""

    real = hc.revision_row

    def revision_row(row: Any, inputs: Any = None) -> Any:
        out = real(row, inputs)
        if out["id"] == "acme-chat-1":
            out["baseUrl"] = "https://evil.example/v1"
        return out

    monkeypatch.setattr(hc, "revision_row", revision_row)
    return {}


def _trip_client_parse(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """models.dev gives the new row a release date the parser rejects, so a client drops it."""

    upstream = _models_dev("acme-think-7")
    upstream["acme"]["models"]["acme-think-7"]["release_date"] = "2026-10-01T00:00:00Z"
    return {"upstream": upstream}


def _trip_caps(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(hc, "MAX_REVISION_BYTES", 10)  # the parser never reads this cap
    return {}


def _trip_providers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The revision leaves a catalogued provider out (a client then keeps its shipped rows)."""

    real = hc.build_revision

    def build_revision(*args: Any, **kwargs: Any) -> Any:
        revision = real(*args, **kwargs)
        del revision["providers"]["orbit"]
        return revision

    monkeypatch.setattr(hc, "build_revision", build_revision)
    return {}


def _trip_lineage_head(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return {"lineage": [_SAMPLE_DIGEST, "0" * 32]}


@pytest.mark.parametrize(
    ("trip", "check"),
    [
        (_trip_additive, "additivity"),
        (_trip_no_transport, "no-transport"),
        (_trip_client_parse, "client-parse"),
        (_trip_caps, "caps"),
        (_trip_providers, "providers"),
        (_trip_lineage_head, "lineage-head"),
    ],
)
def test_each_check_fails_the_build_on_the_publish_path(
    monkeypatch: pytest.MonkeyPatch, trip: Any, check: str
) -> None:
    assert _build()["state"] == "publish", "the positive control"
    overrides = trip(monkeypatch)
    with pytest.raises(pc.BuildFailed) as failed:
        _build(**overrides)
    assert {f.split(":", 1)[0] for f in failed.value.failures} == {check}, failed.value.failures
    assert "FAILED - nothing is published" in failed.value.summary


@pytest.mark.parametrize(
    ("field", "upstream_field", "value"),
    [("family", "family", "Acme Think"), ("releaseDate", "release_date", "2026-10-01T00:00Z")],
)
def test_a_new_rows_ladder_input_the_parser_rejects_drops_it_and_fails_the_run(
    field: str, upstream_field: str, value: str
) -> None:
    """ADR-0264 §10 item 2: a NEW row's ``family`` or ``releaseDate`` that fails validation is
    dropped by the client (§4.3: "so does a field that fails validation"), so the run fails
    loudly. A known row's is a kept field that fails it too (the check rows above)."""

    parsed = _one([_known(id="acme-think-9", **{field: value.lower()})])
    assert [(n.model_id, n.field) for n in parsed.dropped] == [("acme-think-9", field)]
    assert "acme-think-9" not in parsed.rows["acme"]
    upstream = _models_dev("acme-think-7")
    upstream["acme"]["models"]["acme-think-7"][upstream_field] = value
    with pytest.raises(pc.BuildFailed) as failed:
        _build(upstream)
    assert any(f"acme-think-7 dropped ({field}:" in f for f in failed.value.failures), (
        failed.value.failures
    )


def test_the_publisher_lowercases_family_where_it_reads_it_and_where_it_writes_it() -> None:
    """models.dev spells some families with capitals (``Hy`` on ``openrouter/tencent/hy3``,
    a shipped id); the parser takes ``[a-z0-9._-]`` only, and the ladder lowercases."""

    row = SAMPLE_SHIPPED["acme"]["acme-chat-1"]
    assert hc.revision_row(row, {"family": "Hy"})["family"] == "hy"
    upstream = _models_dev("acme-chat-1")
    upstream["acme"]["models"]["acme-chat-1"]["family"] = "Hy"
    inputs = pc.ladder_inputs_for(SAMPLE_SHIPPED, upstream)
    assert inputs["acme"]["acme-chat-1"]["family"] == "hy"
    upstream = _models_dev("acme-think-7")
    upstream["acme"]["models"]["acme-think-7"]["family"] = "Acme-Think"
    built = _build(upstream)
    assert built["state"] == "publish"
    published = {row["id"]: row for row in json.loads(built["raw"])["providers"]["acme"]}
    assert published["acme-think-7"]["family"] == "acme-think"


def test_the_pinned_tests_failing_on_the_applied_checkout_fail_the_build(tmp_path: Path) -> None:
    """§4.2 step 3: the pin test runs against what is published, so a failure stops it."""

    (tmp_path / "tests").mkdir()
    for name in pc.KEEP_PUBLISHED_TESTS:
        body = "def test_ok():\n    assert True\n"
        if name.endswith("corrections_are_pinned.py"):
            body = "def test_a_pinned_correction():\n    assert 272000 == 400000\n"
        (tmp_path / name).write_text(body, encoding="utf-8")
    problems = pc.run_pinned_tests(tmp_path)
    assert problems and "pin, overlay or contract test failed" in problems[0]
    (tmp_path / pc.KEEP_PUBLISHED_TESTS[0]).write_text(
        "def test_ok():\n    pass\n", encoding="utf-8"
    )
    assert pc.run_pinned_tests(tmp_path) == []


# ── the lineage over history (r5's pairs) ──────────────────────────────


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit_catalog(repo: Path, catalog: dict[str, Any], message: str) -> str:
    path = repo / pc.CATALOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _version(n: int) -> dict[str, Any]:
    catalog = copy.deepcopy(SAMPLE_SHIPPED)
    catalog["acme"]["acme-chat-1"]["contextWindow"] = 128000 + n
    return catalog


@pytest.fixture
def history(tmp_path: Path) -> tuple[Path, list[str], list[str]]:
    """A ``main`` of five catalog versions, one of them a merge whose branch held an extra one."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    shas = [_commit_catalog(repo, _version(0), "v0")]
    (repo / "README").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a commit that does not touch the catalog")
    shas.append(_commit_catalog(repo, _version(1), "v1"))
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit_catalog(repo, _version(90), "branch-only intermediate")
    _commit_catalog(repo, _version(2), "branch result")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    shas.append(_git(repo, "rev-parse", "HEAD"))
    shas.append(_commit_catalog(repo, _version(3), "v3"))
    same = json.dumps(_version(3))  # the same catalog, laid out differently
    (repo / pc.CATALOG_REL).write_text(same, encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "reformat only")
    shas.append(_commit_catalog(repo, _version(4), "v4"))
    digests = [hc.catalog_digest(_version(n)) for n in (0, 1, 2, 3, 4)]
    return repo, shas, digests


def test_the_lineage_is_the_first_parent_history_of_distinct_versions(
    history: tuple[Path, list[str], list[str]],
) -> None:
    repo, shas, digests = history
    assert pc.catalog_lineage(repo, "main") == digests
    assert hc.catalog_digest(_version(90)) not in pc.catalog_lineage(repo, "main"), (
        "a merged branch's intermediate catalog never reached main"
    )
    assert pc.catalog_lineage(repo, shas[2]) == digests[:3]


def test_r5s_pairs_no_older_facts_as_newer_and_nothing_withheld(
    history: tuple[Path, list[str], list[str]],
) -> None:
    """ADR-0264 §4.2, MEASURED in r5 over 11 versions; here over 5 with a merge and a no-op commit."""

    repo, shas, digests = history
    lineages = [pc.catalog_lineage(repo, sha) for sha in shas]
    for k, build in enumerate(digests):
        for m, lineage in enumerate(lineages):
            installs = hc.lineage_admits(lineage, build)
            assert installs == (m >= k), (k, m)
    dev = _version(4)
    dev["acme"]["acme-chat-1"]["maxTokens"] += 1
    assert not any(hc.lineage_admits(lineage, hc.catalog_digest(dev)) for lineage in lineages)


def test_a_catalog_revert_repeats_a_digest_as_the_adr_records(tmp_path: Path) -> None:
    """(A, B, A): a build cut at the second A admits the B-era revision until the next slot."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _commit_catalog(repo, _version(0), "A")
    b_era = _commit_catalog(repo, _version(1), "B")
    _commit_catalog(repo, _version(0), "A again")
    a, b = hc.catalog_digest(_version(0)), hc.catalog_digest(_version(1))
    assert pc.catalog_lineage(repo, "main") == [a, b, a]
    assert hc.lineage_admits(pc.catalog_lineage(repo, b_era), a)


def test_a_shallow_clone_is_refused(
    history: tuple[Path, list[str], list[str]], tmp_path: Path
) -> None:
    repo, _shas, _digests = history
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(pc.BuildFailed, match="shallow"):
        pc.catalog_lineage(shallow, "HEAD")


def _real_history() -> bool:
    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), "rev-parse", "--is-shallow-repository"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return False
    return out.returncode == 0 and out.stdout.strip() == "false"


def _lineage_by_the_rule(repo: Path, ref: str) -> list[str]:
    """``ref``'s lineage, checked against ADR-0264 §4.2's rule and nothing more.

    1 to 500 digests of 32 hex characters, oldest first, the last one the catalog
    committed at ``ref``; consecutive commits with one digest are one version (§10). A
    digest may repeat: a catalog revert (A, B, A) is in the ADR, so a check that wants
    every digest distinct fails ``main`` and every publication after one revert.
    """

    lineage = pc.catalog_lineage(repo, ref)
    assert 1 <= len(lineage) <= hc.MAX_LINEAGE
    assert all(re.fullmatch("[0-9a-f]{32}", digest) for digest in lineage), lineage
    assert all(older != newer for older, newer in pairwise(lineage)), lineage
    assert lineage[-1] == hc.catalog_digest(pc.catalog_at(repo, ref))
    return lineage


def test_the_lineage_rule_holds_over_a_reverted_catalog(tmp_path: Path) -> None:
    """Review round 2: the real-history test below wanted every digest distinct, so one
    ``git revert`` of a catalog commit on ``main`` would have failed CI and, through
    ``KEEP_PUBLISHED_TESTS``, every publication from then on. Its rule holds here."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _commit_catalog(repo, _version(0), "A")
    _commit_catalog(repo, _version(1), "B")
    _git(repo, "revert", "--no-edit", "HEAD")
    a, b = hc.catalog_digest(_version(0)), hc.catalog_digest(_version(1))
    assert _lineage_by_the_rule(repo, "main") == [a, b, a]


@pytest.mark.skipif(not _real_history(), reason="needs this checkout's full git history")
def test_this_checkouts_lineage_ends_in_its_committed_catalog() -> None:
    """The lineage rule on the real ``main`` (12 versions at ``27fa21c5``): it ends in the
    committed catalog's digest. Repeats are allowed, as after a revert."""

    assert len(_lineage_by_the_rule(_ROOT, "HEAD")) >= 12


# ── the CLI end to end, offline ────────────────────────────────────────


def _cli_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "aelix-ai"
    repo.mkdir()
    _git(repo, "init", "-q", "--initial-branch=main")
    _commit_catalog(repo, SAMPLE_SHIPPED, "catalog")
    return repo


def _cli_inputs(tmp_path: Path) -> list[str]:
    dev = tmp_path / "api.json"
    dev.write_text(json.dumps(_models_dev("acme-think-7")), encoding="utf-8")
    listing = tmp_path / "openrouter.json"
    listing.write_text(json.dumps({"data": []}), encoding="utf-8")
    return [
        "--models-dev",
        str(dev),
        "--openrouter",
        str(listing),
        "--fetched-at",
        "2026-10-21T01:17:00Z",
    ]


def test_the_cli_writes_the_revision_and_index_and_leaves_the_checkout_as_it_was(
    tmp_path: Path,
) -> None:
    repo = _cli_repo(tmp_path)
    before = (repo / pc.CATALOG_REL).read_bytes()
    out = tmp_path / "out"
    status = pc.main(
        ["build", "--repo", str(repo), "--out", str(out), "--no-tests", *_cli_inputs(tmp_path)]
    )
    assert status == 0
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["state"] == "publish"
    revision = out / "v1" / "revisions" / f"{result['revision']}.json"
    assert hc.verify_revision_bytes(result["revision"], revision.read_bytes())
    index = hc.parse_index(json.loads((out / "v1" / "index.json").read_bytes()))
    assert index.catalogs[0].revision == result["revision"]
    assert index.catalogs[0].metadata["newRows"] == 1
    assert (repo / pc.CATALOG_REL).read_bytes() == before
    assert sorted(p.name for p in out.rglob("*") if p.is_file()) == sorted(
        ["result.json", "summary.md", "index.json", revision.name]
    )

    # The next slot, with that publication in the catalog repository: unchanged.
    catalog_repo = tmp_path / "aelix-catalog"
    (catalog_repo / "v1" / "revisions").mkdir(parents=True)
    (catalog_repo / "v1" / "index.json").write_bytes((out / "v1" / "index.json").read_bytes())
    (catalog_repo / "v1" / "revisions" / revision.name).write_bytes(revision.read_bytes())
    again = tmp_path / "again"
    assert (
        pc.main(
            [
                "build",
                "--repo",
                str(repo),
                "--out",
                str(again),
                "--no-tests",
                "--catalog-repo",
                str(catalog_repo),
                *_cli_inputs(tmp_path),
            ]
        )
        == 0
    )
    assert json.loads((again / "result.json").read_text(encoding="utf-8"))["state"] == "unchanged"
    assert not (again / "v1").exists()


def _cli_inputs_listing(tmp_path: Path, tag: str, *model_ids: str) -> list[str]:
    """Upstream files on disk for one slot whose models.dev lists ``model_ids``."""

    dev = tmp_path / f"api-{tag}.json"
    dev.write_text(json.dumps(_models_dev(*model_ids)), encoding="utf-8")
    listing = tmp_path / f"openrouter-{tag}.json"
    listing.write_text(json.dumps({"data": []}), encoding="utf-8")
    return [
        "--models-dev",
        str(dev),
        "--openrouter",
        str(listing),
        "--fetched-at",
        "2026-10-21T01:17:00Z",
    ]


def test_the_additions_cap_counts_from_the_revision_the_catalog_repository_publishes(
    tmp_path: Path,
) -> None:
    """Review round 2: cmd_build passes the published revision's ids as the cap's baseline
    (§4.2 step 3: ids new since the previous publication, or ``main``'s catalog for the
    first). Counted against ``main`` instead, every slot after the first re-counts the
    ids already published and fails the cap for good."""

    repo = _cli_repo(tmp_path)
    first = tmp_path / "first"
    build = ["build", "--repo", str(repo), "--no-tests"]
    two = _cli_inputs_listing(tmp_path, "two", "acme-think-7", "acme-think-8")
    assert pc.main([*build, "--out", str(first), "--max-additions", "2", *two]) == 0
    published = json.loads((first / "result.json").read_text(encoding="utf-8"))["revision"]
    catalog_repo = tmp_path / "aelix-catalog"
    (catalog_repo / "v1" / "revisions").mkdir(parents=True)
    (catalog_repo / "v1" / "index.json").write_bytes((first / "v1" / "index.json").read_bytes())
    name = f"{published}.json"
    (catalog_repo / "v1" / "revisions" / name).write_bytes(
        (first / "v1" / "revisions" / name).read_bytes()
    )

    three = _cli_inputs_listing(tmp_path, "three", "acme-think-7", "acme-think-8", "acme-think-9")
    nxt = tmp_path / "next"
    cap_one = ["--max-additions", "1", *three]
    assert pc.main([*build, "--out", str(nxt), "--catalog-repo", str(catalog_repo), *cap_one]) == 0
    result = json.loads((nxt / "result.json").read_text(encoding="utf-8"))
    assert result["state"] == "publish", result
    index = hc.parse_index(json.loads((nxt / "v1" / "index.json").read_bytes()))
    assert index.catalogs[0].metadata["newRows"] == 1
    # The same slot with no publication to count from counts from main's catalog: three.
    alone = tmp_path / "alone"
    assert pc.main([*build, "--out", str(alone), *cap_one]) == 1
    failures = json.loads((alone / "result.json").read_text(encoding="utf-8"))["failures"]
    assert [f.split(":", 1)[0] for f in failures] == ["additions-cap"], failures
    assert "3 ids are new" in failures[0]


@pytest.mark.parametrize("given", ["neither", "models.dev only", "OpenRouter only"])
def test_a_build_without_its_upstream_inputs_is_bad_usage_exit_2_writing_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], given: str
) -> None:
    """Review round 2: the docstring said 2 on bad usage, and this exited 1 with no result.json."""

    repo = _cli_repo(tmp_path)
    inputs = _cli_inputs(tmp_path)
    extra = {"neither": [], "models.dev only": inputs[:2], "OpenRouter only": inputs[2:4]}[given]
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as exited:
        pc.main(["build", "--repo", str(repo), "--out", str(out), *extra])
    assert exited.value.code == 2
    assert "build needs --fetch, or both --models-dev and --openrouter" in capsys.readouterr().err
    assert not out.exists()


def test_a_held_catalog_repository_builds_nothing(tmp_path: Path) -> None:
    repo = _cli_repo(tmp_path)
    catalog_repo = tmp_path / "aelix-catalog"
    (catalog_repo / "control").mkdir(parents=True)
    (catalog_repo / "control" / "hold.json").write_text(
        json.dumps({"action": "withdraw", "reason": "bad revision"}), encoding="utf-8"
    )
    out = tmp_path / "out"
    assert (
        pc.main(
            [
                "build",
                "--repo",
                str(repo),
                "--out",
                str(out),
                "--no-tests",
                "--catalog-repo",
                str(catalog_repo),
                *_cli_inputs(tmp_path),
            ]
        )
        == 0
    )
    assert json.loads((out / "result.json").read_text(encoding="utf-8"))["state"] == "held"
    assert "bad revision" in (out / "summary.md").read_text(encoding="utf-8")
    assert not (out / "v1").exists()


def test_a_failed_check_writes_no_revision_and_restores_the_checkout(tmp_path: Path) -> None:
    repo = _cli_repo(tmp_path)
    (repo / "tests").mkdir()
    for name in pc.KEEP_PUBLISHED_TESTS:
        (repo / name).write_text("def test_pinned():\n    assert False\n", encoding="utf-8")
    before = (repo / pc.CATALOG_REL).read_bytes()
    out = tmp_path / "out"
    assert pc.main(["build", "--repo", str(repo), "--out", str(out), *_cli_inputs(tmp_path)]) == 1
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["state"] == "failed" and result["failures"][0].startswith("tests:")
    assert not (out / "v1").exists()
    assert (repo / pc.CATALOG_REL).read_bytes() == before


def test_a_dirty_catalog_is_not_published(tmp_path: Path) -> None:
    """The plan runs over the COMMITTED catalog; an uncommitted edit is in no lineage."""

    repo = _cli_repo(tmp_path)
    dirty = copy.deepcopy(SAMPLE_SHIPPED)
    dirty["acme"]["acme-chat-1"]["contextWindow"] = 1
    (repo / pc.CATALOG_REL).write_text(json.dumps(dirty), encoding="utf-8")
    out = tmp_path / "out"
    assert (
        pc.main(
            ["build", "--repo", str(repo), "--out", str(out), "--no-tests", *_cli_inputs(tmp_path)]
        )
        == 1
    )
    assert "not the source commit's" in (out / "summary.md").read_text(encoding="utf-8")
