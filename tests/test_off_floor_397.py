"""#397: on a model that hides ``off``, a fitted ``off`` lands on a level the model takes.

Where a row's map hides ``off``, every seam that fits a level to the model — a
launch with no level (the ladder's bottom rung), ``defaultThinkingLevel: off``,
a resumed or carried ``off``, RPC ``set_model`` — moves ``off`` up to the lowest
level the map offers (``clamp_thinking_level``, pi's ``clampThinkingLevel``).
A map that names nothing below ``high`` offers ``minimal``, and OpenAI's
o-series refuses it. MEASURED on ``2cbc126b`` (``.omc/probes/397/live/``):
OpenRouter's ``openai/o3`` with no ``--thinking`` answered 400 "Unsupported
value: 'minimal' is not supported with the 'o3-2025-04-16' model", and in RPC
mode ``get_state`` said ``minimal`` and the first prompt failed the same way.

``scripts/refresh_catalog.py``'s ``plan_floor_fills`` writes ``null`` for each
level below the lowest one the judging API's effort list names (models.dev
for a Responses row; models.dev ``openai`` for an OpenRouter ``openai/`` row
that lists no efforts), on rows that hide ``off``, where the map has no key.
OpenRouter's own lists are not read: with ``effort: "minimal"`` 61 of 64 live
rows #507 filled answered 200, the 32 whose list leaves ``minimal`` out among
them; only ``openai/o3``, ``o3-mini`` and ``o3-pro`` refused it
(``.omc/probes/397/minimal-2026-10-26.jsonl``).

The fixture ``tests/fixtures/hosted_catalog/floor-397-fills.json`` records the
95 levels (83 rows) the fold-back wrote over ``2cbc126b`` from
``off-507-inputs.json.gz``. An explicit ``--thinking off``, a profile's
``thinking: off`` and RPC ``set_thinking_level("off")`` are not fitted
(ADR-0239), so they still send no reasoning field; that is unchanged here.
"""

from __future__ import annotations

import copy
import gzip
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_ai import hosted_catalog as hc
from aelix_ai.models import clamp_thinking_level, get_model, get_supported_thinking_levels

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_PATH = _ROOT / "packages/aelix-ai/src/aelix_ai/models_generated.json"
_FIXTURES = _ROOT / "tests/fixtures/hosted_catalog"


def _load(name: str) -> Any:
    path = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rc = _load("refresh_catalog")
# publish_catalog imports refresh_catalog by name from scripts/ (its own sys.path line).
sys.path.insert(0, str(_ROOT / "scripts"))
pc = _load("publish_catalog")

CATALOG: dict[str, dict[str, dict[str, Any]]] = json.loads(
    _CATALOG_PATH.read_text(encoding="utf-8")
)
INPUTS: dict[str, Any] = json.loads(
    gzip.decompress((_FIXTURES / "off-507-inputs.json.gz").read_bytes())
)
EXPECTED: dict[str, Any] = json.loads(
    (_FIXTURES / "floor-397-fills.json").read_text(encoding="utf-8")
)
FLOOR = [tuple(fill) for fill in EXPECTED["floorFills"]]


def _openrouter() -> dict[str, Any]:
    return rc.openrouter_by_id(INPUTS["openRouter"])


# ── the rule ───────────────────────────────────────────────────────────


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "m-1",
        "name": "M 1",
        "api": "openai-responses",
        "provider": "acme",
        "baseUrl": "https://api.example",
        "reasoning": True,
        "thinkingLevelMap": {"off": None},
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 200000,
        "maxTokens": 32000,
    }
    row.update(overrides)
    return row


def _router(model_id: str, **overrides: Any) -> dict[str, Any]:
    return _row(
        id=model_id,
        provider="openrouter",
        api="openai-completions",
        baseUrl="https://openrouter.ai/api/v1",
        **overrides,
    )


def _dev(provider: str, model_id: str, *values: Any) -> dict[str, Any]:
    return {
        provider: {
            "models": {
                model_id: {"reasoning_options": [{"type": "effort", "values": list(values)}]}
            }
        }
    }


def test_the_levels_below_the_lowest_listed_one_are_hidden() -> None:
    local = {"acme": {"m-1": _row()}}
    assert rc.plan_floor_fills(local, _dev("acme", "m-1", "low", "medium", "high"), None) == (
        [("acme", "m-1", "minimal", "models.dev")],
        [],
    )
    fills, _ = rc.plan_floor_fills(local, _dev("acme", "m-1", "high"), None)
    assert [level for _p, _m, level, _s in fills] == ["minimal", "low", "medium"]
    # A list that starts at minimal leaves nothing to hide; no list says nothing.
    assert rc.plan_floor_fills(local, _dev("acme", "m-1", "minimal", "high"), None) == ([], [])
    assert rc.plan_floor_fills(local, _dev("acme", "m-1", "none", "default"), None) == ([], [])
    assert rc.plan_floor_fills(local, {}, None) == ([], [])


@pytest.mark.parametrize(
    "row",
    [
        _row(thinkingLevelMap={"off": "none"}),
        _row(thinkingLevelMap={"xhigh": "xhigh"}),
        _row(thinkingLevelMap=None),
        _row(reasoning=False),
        _row(api="anthropic-messages"),
        _row(api="google-generative-ai"),
    ],
    ids=["off-sent", "off-absent", "null-map", "not-reasoning", "anthropic", "google"],
)
def test_only_a_row_that_hides_off_on_a_wire_that_sends_the_name_gets_a_floor(
    row: dict[str, Any],
) -> None:
    """Elsewhere ``off`` is the floor, or the adapter maps the level itself."""

    provider = row["provider"]
    local = {provider: {"m-1": row}}
    assert rc.plan_floor_fills(local, _dev(provider, "m-1", "low", "high"), None) == ([], [])


def test_copilot_rows_get_a_floor_because_a_fitted_off_reaches_them_as_a_name() -> None:
    """Copilot's adapters skip only the off branch; ``minimal`` goes out by name."""

    local = {"github-copilot": {"m-1": _row(provider="github-copilot")}}
    assert rc.plan_floor_fills(local, _dev("github-copilot", "m-1", "low", "high"), None) == (
        [("github-copilot", "m-1", "minimal", "models.dev")],
        [],
    )


def test_a_responses_row_with_no_list_reads_the_same_id_under_openai_azure_opencode() -> None:
    """models.dev's ``openai`` lists nothing for the codex rows; ``azure`` and ``opencode`` do."""

    local = {"openai": {"gpt-x-codex": _row(id="gpt-x-codex", provider="openai")}}
    upstream = {
        "openai": {"models": {"gpt-x-codex": {"tool_call": True}}},
        **_dev("azure", "gpt-x-codex", "low", "high"),
    }
    assert rc.plan_floor_fills(local, upstream, None) == (
        [("openai", "gpt-x-codex", "minimal", "models.dev azure")],
        [],
    )
    upstream = {"openai": {"models": {}}, **_dev("opencode", "gpt-x-codex", "medium", "high")}
    fills, _ = rc.plan_floor_fills(local, upstream, None)
    assert [(level, source) for _p, _m, level, source in fills] == [
        ("minimal", "models.dev opencode"),
        ("low", "models.dev opencode"),
    ]


def test_a_floor_at_a_level_the_map_does_not_name_is_a_conflict() -> None:
    """``xhigh`` and ``max`` exist only where a map names them: hiding the rest would leave none."""

    local = {"acme": {"m-1": _row()}}
    assert rc.plan_floor_fills(local, _dev("acme", "m-1", "max"), None) == (
        [],
        [("acme", "m-1", "max", "models.dev's floor, which the map does not name")],
    )
    named = {"acme": {"m-1": _row(thinkingLevelMap={"off": None, "max": "max"})}}
    fills, conflicts = rc.plan_floor_fills(named, _dev("acme", "m-1", "max"), None)
    assert [level for _p, _m, level, _s in fills] == ["minimal", "low", "medium", "high", "xhigh"]
    assert conflicts == []


def test_openrouter_lists_are_not_read_and_openai_ids_without_one_read_models_dev() -> None:
    local = {
        "openrouter": {
            "openai/o3": _router("openai/o3"),
            "openai/gpt-5-mini": _router("openai/gpt-5-mini"),
            "google/g": _router("google/g"),
        }
    }
    upstream = {
        "openai": {
            "models": {
                "o3": {"reasoning_options": [{"type": "effort", "values": ["low", "high"]}]},
                "gpt-5-mini": {
                    "reasoning_options": [{"type": "effort", "values": ["low", "high"]}]
                },
            }
        },
        "google": {
            "models": {"g": {"reasoning_options": [{"type": "effort", "values": ["high"]}]}}
        },
    }
    listing = {
        "openai/o3": {"reasoning": {"mandatory": True}},
        "openai/gpt-5-mini": {
            "reasoning": {"mandatory": True, "supported_efforts": ["low", "high"]}
        },
        "google/g": {"reasoning": {"mandatory": True}},
    }
    assert rc.plan_floor_fills(local, upstream, listing) == (
        [("openrouter", "openai/o3", "minimal", "models.dev openai")],
        [],
    )


def test_a_named_level_under_the_floor_is_a_conflict_and_never_overwritten() -> None:
    local = {"acme": {"m-1": _row(thinkingLevelMap={"off": None, "minimal": "minimal"})}}
    assert rc.plan_floor_fills(local, _dev("acme", "m-1", "low"), None) == (
        [],
        [("acme", "m-1", "minimal", "named under the floor models.dev lists")],
    )
    with pytest.raises(AssertionError, match="would overwrite"):
        rc.fill_floor(local, [("acme", "m-1", "minimal", "models.dev")])


def test_fill_floor_puts_the_levels_after_off_and_keeps_the_rest() -> None:
    local = {"acme": {"m-1": _row(thinkingLevelMap={"off": None, "xhigh": "xhigh"})}}
    before = copy.deepcopy(local)
    filled = rc.fill_floor(
        local, [("acme", "m-1", "low", "models.dev"), ("acme", "m-1", "minimal", "models.dev")]
    )
    assert local == before, "fill_floor mutated the catalog it was reading"
    assert list(filled["acme"]["m-1"]["thinkingLevelMap"].items()) == [
        ("off", None),
        ("minimal", None),
        ("low", None),
        ("xhigh", "xhigh"),
    ]
    assert list(filled["acme"]["m-1"]) == list(local["acme"]["m-1"])


def test_fill_floor_raises_a_floor_in_order_wherever_off_stands() -> None:
    local = {
        "acme": {"m-1": _row(thinkingLevelMap={"xhigh": "xhigh", "off": None, "minimal": None})}
    }
    filled = rc.fill_floor(
        local, [("acme", "m-1", "low", "models.dev"), ("acme", "m-1", "medium", "models.dev")]
    )
    assert list(filled["acme"]["m-1"]["thinkingLevelMap"]) == [
        "xhigh",
        "off",
        "minimal",
        "low",
        "medium",
    ]


def test_a_row_whose_off_is_hidden_in_this_run_gets_its_floor_in_the_same_run() -> None:
    """``plan_catalog`` plans the floor over the shipped rows after their off fills."""

    local = {"acme": {"m-1": _row(thinkingLevelMap=None)}}
    planned = rc.plan_catalog(local, _dev("acme", "m-1", "low", "high"), {})
    assert planned.off_fills == [("acme", "m-1", "models.dev")]
    assert planned.floor_fills == [("acme", "m-1", "minimal", "models.dev")]


# ── the shipped catalog is the fold-back's output ──────────────────────


def test_the_shipped_catalog_is_what_the_floor_leaves_behind() -> None:
    assert rc.plan_floor_fills(CATALOG, INPUTS["modelsDev"], _openrouter()) == ([], [])


def test_the_fold_back_wrote_exactly_the_planned_floor(tmp_path: Path) -> None:
    """Take the floor back out and the plan proposes it again; writing it gives the file back.

    A round trip through the script's own ``write_catalog``, against the record
    ``floor-397-fills.json`` keeps of the plan over ``2cbc126b``.
    """

    assert len(FLOOR) == 95 and len({(p, m) for p, m, _l, _s in FLOOR}) == 83
    assert EXPECTED["floorConflicts"] == []
    unfilled = copy.deepcopy(CATALOG)
    for provider, model_id, level, _source in FLOOR:
        assert unfilled[provider][model_id]["thinkingLevelMap"].pop(level) is None
    fills, conflicts = rc.plan_floor_fills(unfilled, INPUTS["modelsDev"], _openrouter())
    assert fills == FLOOR
    assert conflicts == []
    written = tmp_path / "models_generated.json"
    rc.write_catalog(rc.fill_floor(unfilled, fills), written)
    assert written.read_bytes() == _CATALOG_PATH.read_bytes()


@pytest.mark.parametrize(
    ("provider", "model_id", "floor"),
    [
        ("openrouter", "openai/o3", "low"),
        ("openrouter", "openai/o3-mini", "low"),
        ("openrouter", "openai/o3-pro", "low"),
        ("openai", "o3", "low"),
        ("openai", "o4-mini", "low"),
        ("openai", "gpt-6-astra", "low"),
        ("openai", "gpt-5.5-pro", "medium"),
        ("openai", "gpt-5-pro", "high"),
        ("cloudflare-ai-gateway", "o3", "low"),
        ("cloudflare-ai-gateway", "gpt-5.2-codex", "low"),
        ("opencode", "gpt-5.1-codex", "low"),
        ("openai", "gpt-5.1-codex", "low"),
        ("github-copilot", "gpt-5.4", "low"),
        ("github-copilot", "gpt-5.2", "low"),
    ],
)
def test_a_fitted_off_lands_on_the_lowest_level_the_model_takes(
    provider: str, model_id: str, floor: str
) -> None:
    model = get_model(provider, model_id)
    assert model is not None
    assert clamp_thinking_level(model, "off") == floor
    assert get_supported_thinking_levels(model)[0] == floor


@pytest.mark.parametrize(
    "model_id",
    ["openai/gpt-5-mini", "openai/gpt-5-nano", "openai/gpt-oss-20b", "google/gemini-2.5-pro"],
)
def test_an_openrouter_row_that_takes_minimal_keeps_it(model_id: str) -> None:
    """MEASURED: these answered ``effort: "minimal"`` with a 200; their floor stays ``minimal``."""

    model = get_model("openrouter", model_id)
    assert model is not None
    assert clamp_thinking_level(model, "off") == "minimal"


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("openai", "gpt-5.1-chat-latest"),
        ("openai", "gpt-5.2-chat-latest"),
        ("azure-openai-responses", "gpt-5.1-chat-latest"),
        ("azure-openai-responses", "gpt-5.2-chat-latest"),
    ],
)
def test_a_row_no_source_the_rule_reads_lists_still_fits_off_to_minimal(
    provider: str, model_id: str
) -> None:
    """The known gap (ADR-0264 §10): no source the rule reads lists these, so nothing is hidden."""

    model = get_model(provider, model_id)
    assert model is not None
    assert clamp_thinking_level(model, "off") == "minimal"


# ── what a publication does with them ──────────────────────────────────


def _build(committed: dict[str, Any]) -> dict[str, Any]:
    return pc.build_publication(
        committed=committed,
        lineage=[hc.catalog_digest(committed)],
        upstream=INPUTS["modelsDev"],
        openrouter_listing=INPUTS["openRouter"],
        upstream_record={
            "modelsDevSha256": INPUTS["about"]["modelsDevSha256"],
            "openRouterSha256": INPUTS["about"]["openRouterSha256"],
            "fetchedAt": INPUTS["about"]["fetchedAt"],
        },
        source_commit="c" * 40,
        published_at="2026-10-26T15:30:00Z",
    )


def test_a_publication_carries_the_floor_and_reports_one_main_lacks() -> None:
    built = _build(CATALOG)
    parsed = hc.parse_revision(json.loads(built["raw"]), CATALOG)
    assert parsed.rows["openrouter"]["openai/o3"]["thinkingLevelMap"] == {
        "off": None,
        "minimal": None,
    }

    committed = copy.deepcopy(CATALOG)
    del committed["openrouter"]["openai/o3"]["thinkingLevelMap"]["minimal"]
    built = _build(committed)
    assert "- openrouter/openai/o3: minimal: null, from models.dev openai" in built["summary"]
    published = {row["id"]: row for row in json.loads(built["raw"])["providers"]["openrouter"]}
    assert published["openai/o3"]["thinkingLevelMap"] == {"off": None}
