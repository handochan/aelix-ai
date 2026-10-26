"""#507: a shipped row whose model takes no ``reasoning.effort: "none"`` hides off.

A reasoning row whose ``thinkingLevelMap`` has no ``off`` key sends off as
``effort: "none"`` on the Responses APIs and on OpenRouter. MEASURED on
``0dee85d0`` (``.omc/probes/507/live-before/``): ``--thinking off`` on
OpenRouter's ``openai/gpt-5-mini``, ``openai/gpt-5-nano``, ``openai/gpt-oss-20b``,
``google/gemini-2.5-pro``, ``z-ai/glm-5.3-flash`` and ``openai/gpt-6-astra``
answered 400, "Reasoning is mandatory for this endpoint and cannot be
disabled." ``off: null`` omits the field.

The fill is derived, not typed: ``scripts/refresh_catalog.py``'s
``plan_off_fills`` reads the evidence a new row's map comes from (OpenRouter's
``reasoning.mandatory``, models.dev's effort list without ``none``) and
``fill_off`` writes ``off: null`` only where the map has no ``off`` (ADR-0264
§10, 2026-10-26). These tests hold three things:

- the rule, on synthetic rows;
- that the shipped catalog is exactly what the fold-back leaves behind, over the
  inputs it ran on (``tests/fixtures/hosted_catalog/off-507-inputs.json.gz``,
  models.dev and OpenRouter as fetched 2026-10-26, cut by
  ``.omc/probes/507/make_off_fixture.py`` to what the plan reads; the cut plans
  the same fills as the full bodies), so the next refresh proposes nothing and
  overwrites nothing;
- that a publication carries the fills to a client, and reports, without
  applying, a fill ``main`` has not committed yet.
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
from aelix_ai.models import get_model, get_supported_thinking_levels
from aelix_ai.providers import openai_completions, openai_responses
from aelix_ai.providers._openai_compat import get_compat
from aelix_ai.streaming import Context

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_PATH = _ROOT / "packages/aelix-ai/src/aelix_ai/models_generated.json"
_FIXTURE = _ROOT / "tests/fixtures/hosted_catalog/off-507-inputs.json.gz"


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
INPUTS: dict[str, Any] = json.loads(gzip.decompress(_FIXTURE.read_bytes()))
FILLS = [tuple(fill) for fill in INPUTS["fills"]]


def _openrouter() -> dict[str, Any]:
    return rc.openrouter_by_id(INPUTS["openRouter"])


def _serialize(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, indent=2, ensure_ascii=False)


def _unfilled(catalog: dict[str, Any]) -> dict[str, Any]:
    """``catalog`` with the fixture's fills taken back out: what the fold-back ran over."""

    stripped = copy.deepcopy(catalog)
    for provider, model_id, _source in FILLS:
        row = stripped[provider][model_id]
        assert row["thinkingLevelMap"]["off"] is None, (provider, model_id)
        del row["thinkingLevelMap"]["off"]
        if not row["thinkingLevelMap"]:
            del row["thinkingLevelMap"]
    return stripped


# ── the rule ───────────────────────────────────────────────────────────


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "m-1",
        "name": "M 1",
        "api": "openai-responses",
        "provider": "acme",
        "baseUrl": "https://api.example",
        "reasoning": True,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 200000,
        "maxTokens": 32000,
    }
    row.update(overrides)
    return row


def _router(**overrides: Any) -> dict[str, Any]:
    return _row(
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


def _plan(
    local: dict[str, Any], upstream: dict[str, Any], openrouter: dict[str, Any] | None = None
) -> Any:
    return rc.plan_off_fills(local, upstream, openrouter)


def test_a_mandatory_openrouter_row_hides_off_and_an_optional_one_does_not() -> None:
    local = {
        "openrouter": {"a/m": _router(id="a/m"), "b/m": _router(id="b/m"), "c/m": _router(id="c/m")}
    }
    listing = {
        "a/m": {"reasoning": {"mandatory": True}},
        "b/m": {"reasoning": {"mandatory": False, "supported_efforts": ["low", "high"]}},
    }
    # c/m is not on OpenRouter's list: nothing to read, nothing written.
    assert _plan(local, {}, listing) == ([("openrouter", "a/m", "openrouter")], [])
    assert _plan(local, {}, None) == ([], [])


def test_a_responses_row_hides_off_only_when_upstream_lists_effort_without_none() -> None:
    local = {"acme": {"m-1": _row()}}
    assert _plan(local, _dev("acme", "m-1", "low", "high")) == ([("acme", "m-1", "models.dev")], [])
    assert _plan(local, _dev("acme", "m-1", "none", "low")) == ([], [])
    # ``default`` and JSON null name no level: no list, no claim about none.
    assert _plan(local, _dev("acme", "m-1", "default", None)) == ([], [])
    assert _plan(local, {}) == ([], [])


@pytest.mark.parametrize(
    "row",
    [
        _row(reasoning=False),
        _row(api="anthropic-messages"),
        _row(provider="github-copilot"),
        _row(
            api="openai-completions",
            compat={"thinkingFormat": "openai", "supportsReasoningEffort": True},
        ),
    ],
    ids=["not-reasoning", "anthropic-messages", "github-copilot", "openai-format-completions"],
)
def test_a_row_whose_wire_never_sends_none_for_off_is_left_alone(row: dict[str, Any]) -> None:
    """Only the wires that send ``none`` for an absent ``off`` can fail as #507 did."""

    provider = row["provider"]
    local = {provider: {"m-1": row}}
    assert _plan(local, _dev(provider, "m-1", "low", "high")) == ([], [])


def test_the_provider_rule_says_nothing_about_off() -> None:
    """Rule 1's map hides levels and leaves ``off`` (ADR-0264 §7 Q2), so it fills nothing."""

    local = {"together": {"m-1": _row(provider="together")}}
    assert _plan(local, _dev("together", "m-1", "low", "high")) == ([], [])


def test_an_off_that_exists_is_never_overwritten_and_a_contradicted_string_is_reported() -> None:
    hidden = _row(thinkingLevelMap={"off": None})
    sent = _row(id="m-2", thinkingLevelMap={"off": "none", "xhigh": "xhigh"})
    local = {"acme": {"m-1": hidden, "m-2": sent}}
    upstream = {
        "acme": {
            "models": {
                "m-1": {"reasoning_options": [{"type": "effort", "values": ["low"]}]},
                "m-2": {"reasoning_options": [{"type": "effort", "values": ["low"]}]},
            }
        }
    }
    assert _plan(local, upstream) == ([], [("acme", "m-2", "models.dev")])
    with pytest.raises(AssertionError, match="would overwrite"):
        rc.fill_off(local, [("acme", "m-2", "models.dev")])


def test_an_off_string_sent_as_reasoning_effort_is_a_conflict_but_never_a_fill() -> None:
    """OpenAI-format Chat Completions sends an ``off`` string as ``reasoning_effort``.

    So a contradicted string there is reported; an absent ``off`` sends nothing
    there, so it is not filled.
    """

    compat = {"thinkingFormat": "openai", "supportsReasoningEffort": True}
    local = {
        "acme": {
            "m-1": _row(api="openai-completions", compat=compat, thinkingLevelMap={"off": "none"}),
            "m-2": _row(id="m-2", api="openai-completions", compat=compat),
        }
    }
    upstream = {
        "acme": {
            "models": {
                "m-1": {"reasoning_options": [{"type": "effort", "values": ["low"]}]},
                "m-2": {"reasoning_options": [{"type": "effort", "values": ["low"]}]},
            }
        }
    }
    assert _plan(local, upstream) == ([], [("acme", "m-1", "models.dev")])


def test_copilot_is_left_out_only_where_the_adapter_leaves_it_out() -> None:
    """Both Responses adapters skip Copilot's off branch; Chat Completions' openrouter format does not."""

    assert not rc.sends_none_for_off(_row(provider="github-copilot"))
    assert rc.sends_none_for_off(
        _row(
            provider="github-copilot",
            api="openai-completions",
            compat={"thinkingFormat": "openrouter"},
        )
    )


def test_an_explicit_null_map_is_replaced_where_it_stands() -> None:
    """``"thinkingLevelMap": null`` is no ``off``: the fill replaces it, and the next plan is quiet."""

    row = _row(thinkingLevelMap=None)
    keys = list(row)
    local = {"acme": {"m-1": row}}
    upstream = _dev("acme", "m-1", "low", "high")
    fills, _ = _plan(local, upstream)
    assert fills == [("acme", "m-1", "models.dev")]
    filled = rc.fill_off(local, fills)
    assert filled["acme"]["m-1"]["thinkingLevelMap"] == {"off": None}
    assert list(filled["acme"]["m-1"]) == keys
    assert _plan(filled, upstream) == ([], [])


def test_azure_reads_its_own_upstream_first_and_openais_second() -> None:
    local = {
        "azure-openai-responses": {
            "o3": _row(id="o3", provider="azure-openai-responses", api="azure-openai-responses"),
            "o3-pro": _row(
                id="o3-pro", provider="azure-openai-responses", api="azure-openai-responses"
            ),
        }
    }
    upstream = {
        "azure": {"models": {"o3": {"reasoning_options": [{"type": "effort", "values": ["low"]}]}}},
        "openai": {
            "models": {
                "o3": {"reasoning_options": [{"type": "effort", "values": ["none", "low"]}]},
                "o3-pro": {"reasoning_options": [{"type": "effort", "values": ["low"]}]},
            }
        },
    }
    fills, _ = _plan(local, upstream)
    assert [(p, m) for p, m, _ in fills] == [
        ("azure-openai-responses", "o3"),
        ("azure-openai-responses", "o3-pro"),
    ]
    # Azure's own entry wins over OpenAI's when it lists effort values.
    upstream["azure"]["models"]["o3"]["reasoning_options"][0]["values"] = ["none", "low"]
    fills, _ = _plan(local, upstream)
    assert [(p, m) for p, m, _ in fills] == [("azure-openai-responses", "o3-pro")]


def test_cloudflare_reads_the_gateways_openai_prefixed_id() -> None:
    """models.dev keys the gateway by the upstream it fronts; pi strips the prefix."""

    local = {"cloudflare-ai-gateway": {"o3": _row(id="o3", provider="cloudflare-ai-gateway")}}
    fills, _ = _plan(local, _dev("cloudflare-ai-gateway", "openai/o3", "low", "high"))
    assert fills == [("cloudflare-ai-gateway", "o3", "models.dev")]
    fills, _ = _plan(local, _dev("cloudflare-ai-gateway", "openai/o3", "none", "high"))
    assert fills == []


def test_fill_off_puts_off_first_and_a_new_map_after_reasoning() -> None:
    mapless = _row()
    mapped = _row(id="m-2", thinkingLevelMap={"xhigh": "xhigh"})
    local = {"acme": {"m-1": mapless, "m-2": mapped}}
    before = copy.deepcopy(local)
    filled = rc.fill_off(local, [("acme", "m-1", "models.dev"), ("acme", "m-2", "models.dev")])
    assert local == before, "fill_off mutated the catalog it was reading"
    keys = list(filled["acme"]["m-1"])
    assert keys[keys.index("reasoning") + 1] == "thinkingLevelMap"
    assert filled["acme"]["m-1"]["thinkingLevelMap"] == {"off": None}
    assert list(filled["acme"]["m-2"]["thinkingLevelMap"].items()) == [
        ("off", None),
        ("xhigh", "xhigh"),
    ]
    assert list(filled["acme"]) == ["m-1", "m-2"]


# ── the shipped catalog is the fold-back's output ──────────────────────


def test_the_shipped_catalog_is_what_the_fill_leaves_behind() -> None:
    """A refresh over the fold-back's inputs proposes no fill and finds no conflict.

    So nothing here is waiting on a hand edit, and the next ``--apply`` has
    nothing of #507's to write.
    """

    assert _plan(CATALOG, INPUTS["modelsDev"], _openrouter()) == ([], [])


def test_the_fold_back_wrote_exactly_the_planned_fills(tmp_path: Path) -> None:
    """Take the fills back out and the plan proposes them again; writing them gives the file back.

    A round trip, byte for byte through the script's own ``write_catalog``:
    every ``off: null`` the fixture records sits where ``fill_off`` puts it, and
    nothing else in the file differs from what the plan leaves. The fixture's
    fills were planned over ``0dee85d0``'s catalog (``about.plannedOver``;
    ``make_off_fixture.py`` checked the cut against the full bodies), which is
    the provenance; this test holds the committed file to that record. The one
    conflict the run reported, ``openai/gpt-6-astra``'s copied
    ``"off": "none"``, is the hand correction RELEASING.md asks for, pinned in
    ``test_catalog_corrections_are_pinned.py``.
    """

    assert len(FILLS) == 91
    assert INPUTS["about"]["plannedOver"] == "0dee85d0178163795071a77a4e7aed8a8ede8430"
    assert [tuple(c) for c in INPUTS["conflicts"]] == [("openai", "gpt-6-astra", "models.dev")]
    unfilled = _unfilled(CATALOG)
    fills, conflicts = _plan(unfilled, INPUTS["modelsDev"], _openrouter())
    assert fills == FILLS
    assert conflicts == []
    written = tmp_path / "models_generated.json"
    rc.write_catalog(rc.fill_off(unfilled, fills), written)
    assert written.read_bytes() == _CATALOG_PATH.read_bytes()


def test_the_rows_the_issue_measured_now_omit_reasoning_for_off() -> None:
    """The live 400s of #507, as the adapters build the request from the shipped rows."""

    for model_id in (
        "openai/gpt-5-mini",
        "openai/gpt-5-nano",
        "openai/gpt-oss-20b",
        "google/gemini-2.5-pro",
        "z-ai/glm-5.3-flash",
        "openai/gpt-6-astra",
        "openai/o4-mini",
    ):
        model = get_model("openrouter", model_id)
        assert model is not None, model_id
        assert "off" not in get_supported_thinking_levels(model), model_id
        params = openai_completions.build_params(
            model, Context(messages=[]), None, get_compat(model), "short"
        )
        assert "reasoning" not in params, model_id
    for provider, model_id in (
        ("openai", "o3"),
        ("openai", "gpt-6-astra"),
        ("azure-openai-responses", "o4-mini"),
        ("cloudflare-ai-gateway", "o3"),
    ):
        model = get_model(provider, model_id)
        assert model is not None, (provider, model_id)
        assert "off" not in get_supported_thinking_levels(model), (provider, model_id)
        params = openai_responses.build_params(model, Context(messages=[]), None)
        assert "reasoning" not in params, (provider, model_id)


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
        published_at="2026-10-26T12:50:00Z",
    )


def test_a_publication_carries_the_fills_to_the_client() -> None:
    built = _build(CATALOG)
    assert built["state"] == "publish"
    parsed = hc.parse_revision(json.loads(built["raw"]), CATALOG)
    assert parsed.dropped == []
    for provider, model_id, _source in FILLS:
        row = parsed.rows[provider][model_id]
        assert row["thinkingLevelMap"]["off"] is None, (provider, model_id)
    assert (
        "## Shipped rows' off, and the levels under it, upstream says a model refuses (#507, #397; not published)\n\n- none"
        in built["summary"]
    )


def test_a_publication_reports_a_fill_main_has_not_committed_and_does_not_apply_it() -> None:
    """A shipped row publishes as ``main`` has it (ADR-0264 §4.3); the fold-back commits the fill."""

    committed = copy.deepcopy(CATALOG)
    del committed["openrouter"]["openai/gpt-5-nano"]["thinkingLevelMap"]
    built = _build(committed)
    assert built["state"] == "publish"
    assert "- openrouter/openai/gpt-5-nano: off: null, from openrouter" in built["summary"]
    published = {row["id"]: row for row in json.loads(built["raw"])["providers"]["openrouter"]}
    assert "thinkingLevelMap" not in published["openai/gpt-5-nano"]


# ── the cli ────────────────────────────────────────────────────────────


def test_off_only_writes_the_fills_and_no_addition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / "models_generated.json"
    catalog.write_text(
        _serialize({"acme": {"m-1": _row()}, "openrouter": {"a/m": _router(id="a/m")}}),
        encoding="utf-8",
    )
    upstream = {
        "acme": {
            "models": {
                "m-1": {"reasoning_options": [{"type": "effort", "values": ["low"]}]},
                "m-9": {"tool_call": True, "limit": {"context": 1000, "output": 100}},
            }
        }
    }
    source = tmp_path / "api.json"
    source.write_text(json.dumps(upstream), encoding="utf-8")
    listing = tmp_path / "openrouter.json"
    listing.write_text(
        json.dumps({"data": [{"id": "a/m", "reasoning": {"mandatory": True}}]}), encoding="utf-8"
    )
    monkeypatch.setattr(rc, "CATALOG_PATH", catalog)

    # Refused before anything is read: the source named here does not exist.
    with pytest.raises(SystemExit):
        rc.main(["--source", str(tmp_path / "absent.json"), "--off-only"])
    rc.main(["--source", str(source), "--openrouter-source", str(listing), "--apply", "--off-only"])
    written = json.loads(catalog.read_text(encoding="utf-8"))
    assert set(written["acme"]) == {"m-1"}
    # #397 hides the level under the hidden off that the effort list leaves out.
    assert written["acme"]["m-1"]["thinkingLevelMap"] == {"off": None, "minimal": None}
    assert written["openrouter"]["a/m"]["thinkingLevelMap"] == {"off": None}
