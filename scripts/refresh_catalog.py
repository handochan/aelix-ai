"""Add models that exist upstream and not here, WITHOUT overwriting what a shipped row says.

#172. ``models_generated.json`` is a corrected fork of pi's catalog, not a
mirror of anything: 41 hand edits over six commits, pinned in
``tests/test_catalog_corrections_are_pinned.py``. MEASURED, and it is why this
script is additive-only: a faithful regeneration from pi reverts 19 of those 32
corrections and drops 376 of 847 models. So this never writes a key that
already exists. Reverting a correction is then not a thing it can do — not a
rule it follows, a shape it has.

WHAT UPSTREAM CANNOT TELL US. models.dev serves model metadata: cost, limits,
modalities, ``tool_call``. It carries NO transport — there is no ``api``, no
``baseUrl``, no ``headers``, no ``compat`` anywhere in ``api.json``. Those four
fields come from pi's per-provider model files, and they are what makes a row
actually run. A row with the wrong ``api`` is worse than an absent one: it
shows up in ``/model``, the user picks it, and it fails at request time with a
transport error nobody can read.

So transport is INHERITED from a model we already ship, never invented, and
where it cannot be inherited unambiguously the model is SKIPPED and reported.
The rule is ADR-0232's ladder, which lives in :mod:`aelix_ai.catalog_inherit`
since #172's publishing pipeline (ADR-0264 §4.8) so the client that reads a
published revision runs the same implementation; the names below are that
module's functions. :func:`audit_transport_rule` measures the rule against the
rows we already have — leave-one-out, so the row being predicted is never its
own evidence — and ``tests/test_catalog_overlay.py`` fails if the accuracy
drops.

THINKING MAPS FOR NEW ROWS (ADR-0264 §7 Q2, the owner's decision of
2026-10-16). A new reasoning row's ``thinkingLevelMap`` is derived as pi derives
one, in pi's order, and a row nothing covers gets none:
:func:`derive_thinking_level_map`. An existing row's map is never overwritten —
it is a key that exists — with one addition (#507, ADR-0264 §10, 2026-10-26): where its map
has no ``off`` and the same evidence says the model takes no ``none``,
``off: null`` is written into it (:func:`plan_off_fills`), because otherwise
off is sent as ``none`` and answered 400; and under a hidden ``off``, the levels
below the lowest one the judging API lists are written ``null`` too
(:func:`plan_floor_fills`, #397), so a fitted ``off`` lands on a level the
model takes.

Usage::

    python scripts/refresh_catalog.py --audit                 # score the rule
    python scripts/refresh_catalog.py --source api.json --openrouter-source models.json
    python scripts/refresh_catalog.py --fetch --apply         # the fold-back (RELEASING.md)
    python scripts/refresh_catalog.py --fetch --apply --off-only  # only the off and floor fills

``--source`` takes a models.dev ``api.json`` on disk and ``--openrouter-source``
OpenRouter's public ``/api/v1/models``; ``--fetch`` downloads both. ``--apply``
refuses to run without OpenRouter's list, because without it every new
OpenRouter reasoning row would land without the map rule 2 gives it.

WHERE THIS RUNS. By hand (the fold-back, ``RELEASING.md``), and on a schedule in
``handochan/aelix-catalog`` through ``scripts/publish_catalog.py build``, which
plans with this module over the committed catalog and publishes the result
without transport (ADR-0264 §4.2). aelix-ai's own CI runs it without network
(``tests/test_hosted_catalog_contract.py``). Nothing here runs at import.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from aelix_ai.catalog_inherit import (
    Transport,
    encode,
    family_keys,
    namespace,
    orphans,
    released,
    resolve_transport,
    settle,
    transport_of,
    wire,
)

UPSTREAM_URL = "https://models.dev/api.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"

UPSTREAM_IDS: dict[str, tuple[str, ...]] = {
    "together": ("togetherai",),
    "vercel-ai-gateway": ("vercel",),
    "zai-coding-cn": ("zhipuai", "zhipuai-coding-plan"),
    "zai": ("zai", "zai-coding-plan"),
}
"""Providers whose upstream id is not the one we call them, and why.

Every entry is a MEASURED id-set overlap against the rows we already ship, and
the ones NOT here matter more than the ones that are:

- ``together`` → ``togetherai``: 17 of 17 local ids, 100%. A rename.
- ``vercel-ai-gateway`` → ``vercel``: 134 of 154, 87%; next best 45%.
- ``zai-coding-cn`` → the two ``zhipuai`` providers. Our base URL is literally
  ``open.bigmodel.cn/api/coding/paas/v4``, which is zhipuai's coding plan.
- ``zai`` → itself PLUS ``zai-coding-plan``. Our base URL is the coding-plan
  endpoint, yet three of our six rows (``glm-4.5-air``, ``glm-5.1``,
  ``glm-5v-turbo``) exist upstream only under plain ``zai`` — so pi already
  treats the two as one catalog, and the union is what it has been doing.

NOT aliased, and this is the whole reason the table is written by hand rather
than solved by overlap: ``openai-codex`` overlaps ``opencode`` 93%, and
``azure-openai-responses`` overlaps ``openai`` 79%. Those numbers are high
because resellers carry the same model NAMES, not because they are the same
service. Overlap measures the catalog, not the provider, so a threshold would
have quietly pointed our ChatGPT-subscription transport at a reseller's list.
``ant-ling``, ``fireworks`` and ``kimi-coding`` have no counterpart worth the
name (0%, 30%, 33%) and stay frozen.
"""

CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "packages/aelix-ai/src/aelix_ai/models_generated.json"
)

Row = dict[str, Any]
Catalog = dict[str, dict[str, Row]]


def upstream_models_for(provider: str, upstream: dict[str, Any]) -> dict[str, Row]:
    """Every upstream row that describes ``provider``, under whatever id it uses.

    Merged left to right, first writer winning, so an entry listed first in
    :data:`UPSTREAM_IDS` is the authority when both describe the same model.
    """

    merged: dict[str, Row] = {}
    for upstream_id in UPSTREAM_IDS.get(provider, (provider,)):
        for model_id, row in ((upstream.get(upstream_id) or {}).get("models") or {}).items():
            merged.setdefault(model_id, row)
    return merged


# ── what we refuse to add ──────────────────────────────────────────────


def is_eligible(upstream_row: Row) -> str | None:
    """Reason to skip ``upstream_row``, or ``None`` if it may be added.

    ``tool_call`` is the one that matters. This is a coding agent: a model that
    cannot call a tool cannot edit a file, and offering it in ``/model`` sells
    the user a session that will fail on its first action. 185 of the 578
    upstream rows we lack are in that state — image models, embedders, TTS.

    The limits are a data-quality floor, not a policy: :class:`Model` requires
    ``contextWindow`` and ``maxTokens``, and a 0 there would render a context
    meter that divides by zero.
    """

    if not upstream_row.get("tool_call"):
        return "no tool_call"
    limit = upstream_row.get("limit") or {}
    if not limit.get("context"):
        return "no limit.context"
    if not limit.get("output"):
        return "no limit.output"
    return None


# ── transport, which upstream does not have ────────────────────────────
#
# ADR-0232's ladder, moved to ``aelix_ai.catalog_inherit`` (#172) so the
# generator and the client run one implementation. These are the same function
# objects under the names this script and its tests have always used;
# ``tests/test_catalog_overlay.py`` checks the identity.

_encode = encode
_family_keys = family_keys
_namespace = namespace
_released = released
_wire = wire
_orphans = orphans
_settle = settle


# ── the local conventions upstream disagrees with ──────────────────────


def _copilot_context(transport: Transport, local_models: dict[str, Row]) -> int | None:
    """What a Copilot row's context window is here, which is not what it is upstream.

    A GitHub Copilot seat caps the request, so the number is the seat's, not the
    model's. 759fae9 normalised every Copilot Claude row to the documented
    200000 against an inconsistent 144K/160K/1M mix; the ``openai-completions``
    rows have carried 128000 uniformly across every commit that touched them.
    Upstream serves the model's own numbers — 1000000 for claude-sonnet-5,
    1000000 for gemini-3.1-pro — and taking those would put the mix back and
    make the context meter wrong by 5x on the models people actually use.

    So a new Copilot row inherits the context of the rows that share its
    transport. Returns ``None`` if they disagree, and the caller skips.
    """

    peers = {
        row["contextWindow"]
        for row in local_models.values()
        if transport_of(row)[0] == transport[0]
    }
    if len(peers) == 1:
        return next(iter(peers))
    return max(peers) if peers else None


def build_row(
    provider: str,
    model_id: str,
    upstream_row: Row,
    transport: Transport,
    local_models: dict[str, Row],
) -> Row:
    """The catalog row for a model we do not have, in this file's own shape.

    Everything the JSON needs comes from one of two places and it is worth
    being able to see which: transport from a sibling we already ship, metadata
    from upstream. ``input`` is narrowed to the two values this catalog uses —
    ``["text", "image"]`` and ``["text"]``, every row, no third — so upstream's
    ``pdf``/``video``/``audio`` collapse rather than introducing a modality
    nothing downstream reads.
    """

    api, base_url, headers, compat = transport
    cost = upstream_row.get("cost") or {}
    limit = upstream_row.get("limit") or {}
    modalities = (upstream_row.get("modalities") or {}).get("input") or ["text"]

    row: Row = {
        "id": model_id,
        "name": upstream_row.get("name") or model_id,
        "api": api,
        "provider": provider,
        "baseUrl": base_url,
        "reasoning": bool(upstream_row.get("reasoning")),
        "input": ["text", "image"] if "image" in modalities else ["text"],
        "cost": {
            "input": cost.get("input", 0),
            "output": cost.get("output", 0),
            "cacheRead": cost.get("cache_read", 0),
            "cacheWrite": cost.get("cache_write", 0),
        },
        "contextWindow": int(limit["context"]),
        "maxTokens": int(limit["output"]),
    }
    # An output cap above the context window is not a number anything can act
    # on, and the harness divides by these. Upstream ships two such rows on its
    # own (``Inkling-Small`` at 1048576/524288); the Copilot normalisation below
    # can create a third by lowering the context under an untouched output.
    row["maxTokens"] = min(row["maxTokens"], row["contextWindow"])
    if headers is not None:
        row["headers"] = json.loads(headers)
    if compat is not None:
        row["compat"] = json.loads(compat)

    if provider == "github-copilot":
        # A seat is a subscription; usage is not billed per token. The catalog
        # has carried zero cost for every Copilot row since the provider was
        # added, and `/cost` would otherwise report money nobody spent.
        row["cost"] = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
        context = _copilot_context(transport, local_models)
        if context is None:
            raise ValueError(f"no Copilot context convention for {model_id}")
        row["contextWindow"] = context
        row["maxTokens"] = min(row["maxTokens"], context)
    return row


# ── thinking maps for new rows (ADR-0264 §7 Q2) ────────────────────────

PROVIDER_RULE_PROVIDERS: frozenset[str] = frozenset(
    {"together", "xiaomi", "xiaomi-token-plan-ams", "xiaomi-token-plan-cn", "xiaomi-token-plan-sgp"}
)
"""Rule 1: providers whose every new reasoning row offers only off and high.

pi's Xiaomi rule (``packages/ai/scripts/generate-models.ts:2487-2503`` at
``pi@fdce48eb4``: MiMo "documents all effort levels as identical") and
Together's default (``:215-219``, ``:578-587``). The MAP only: pi's Xiaomi
``compat`` is transport, which a published row never carries, and MEASURED
(ADR-0264 §7 Q2) aelix's 21 Xiaomi rows are ``anthropic-messages`` and send a
budget per level, so here the rule only hides levels.
"""

PROVIDER_RULE_MAP: dict[str, str | None] = {"minimal": None, "low": None, "medium": None}
"""Hides minimal, low and medium; off and high stay; xhigh and max need a map entry anyway."""

_EFFORT_LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")
"""pi's ``THINKING_LEVELS`` in ``models-dev-reasoning-options.ts:11``: the levels an effort list names."""

_DIRECT_EFFORT_APIS: frozenset[str] = frozenset(
    {"openai-responses", "azure-openai-responses", "openai-codex-responses"}
)


def effort_thinking_level_map(values: list[Any]) -> dict[str, str | None] | None:
    """pi's ``getEffortThinkingLevelMap`` (``models-dev-reasoning-options.ts:18-30``).

    A listed level maps to itself and an unlisted one is hidden (``None``);
    ``off`` maps to ``none`` when ``none`` is listed and is hidden otherwise.
    ``default`` and JSON ``null`` have no level and are ignored, and a list
    naming no level and no ``none`` gives no map. Then cut to this build's
    levels (§7 Q2: "A map names only the build's levels"); since #433 that
    keeps ``max``.
    """

    from aelix_ai.models import EXTENDED_THINKING_LEVELS

    supported = {value for value in values if isinstance(value, str)}
    if not any(level in supported for level in _EFFORT_LEVELS) and "none" not in supported:
        return None
    mapped: dict[str, str | None] = {"off": "none" if "none" in supported else None}
    for level in _EFFORT_LEVELS:
        mapped[level] = level if level in supported else None
    return {level: wire for level, wire in mapped.items() if level in EXTENDED_THINKING_LEVELS}


def _models_dev_effort_values(upstream_row: Row | None) -> list[Any]:
    values: list[Any] = []
    for option in (upstream_row or {}).get("reasoning_options") or []:
        if isinstance(option, dict) and option.get("type") == "effort":
            values.extend(option.get("values") or [])
    return values


def openrouter_thinking_level_map(reasoning: Any) -> dict[str, str | None] | None:
    """pi's ``getOpenRouterThinkingLevelMap`` (``openrouter-reasoning-options.ts:12-23``).

    OpenRouter's ``reasoning.supported_efforts`` uses models.dev's effort
    values, so both share :func:`effort_thinking_level_map`; ``off`` then
    follows ``mandatory`` rather than ``none``. A mandatory model with no
    effort list hides ``off`` and nothing else.
    """

    if not isinstance(reasoning, dict):
        return None
    mandatory = reasoning.get("mandatory") is True
    efforts = reasoning.get("supported_efforts")
    mapped = (
        effort_thinking_level_map(list(efforts)) if isinstance(efforts, list) and efforts else None
    )
    if mapped is None:
        return {"off": None} if mandatory else None
    mapped["off"] = None if mandatory else "none"
    return mapped


def sends_level_name(row: Row) -> bool:
    """pi's ``supportsDirectReasoningEffort`` (``generate-models.ts:545-561``), for aelix rows.

    True when the row's inherited transport sends the level's own name as the
    wire value: the three Responses APIs, and ``openai-completions`` whose
    compat (detected, then the row's own, as the adapter merges them) says
    ``thinkingFormat: openai`` with reasoning effort supported.
    ``anthropic-messages`` is False here: pi's branch fires only where a loader
    set ``forceAdaptiveThinking`` before the derivation (Fireworks,
    ``:1765-1770``), and this generator adds no Fireworks rows (§7 Q2 rule 3).
    """

    if row["api"] in _DIRECT_EFFORT_APIS:
        return True
    if row["api"] != "openai-completions":
        return False
    compat = _merged_compat(row)
    return compat.thinking_format == "openai" and bool(compat.supports_reasoning_effort)


def _merged_compat(row: Row) -> Any:
    """An ``openai-completions`` row's compat as the adapter merges it: detected, then the row's own."""

    from aelix_ai.providers._openai_compat import get_compat
    from aelix_ai.streaming import Model, ModelCost

    model = Model(
        id=row["id"],
        name=row.get("name") or row["id"],
        api=row["api"],
        provider=row["provider"],
        base_url=row["baseUrl"],
        reasoning=bool(row.get("reasoning")),
        input=list(row.get("input") or ["text"]),
        cost=ModelCost(input=0.0, output=0.0, cache_read=0.0, cache_write=0.0),
        context_window=int(row["contextWindow"]),
        max_tokens=int(row["maxTokens"]),
        headers=row.get("headers"),
        compat=row.get("compat"),
    )
    return get_compat(model)


def derive_thinking_level_map(
    provider: str,
    row: Row,
    upstream_row: Row | None,
    openrouter_row: Row | None,
) -> tuple[dict[str, str | None] | None, str]:
    """A new row's thinking map and where it came from, in pi's order (ADR-0264 §7 Q2).

    1. ``"provider rule"``: :data:`PROVIDER_RULE_PROVIDERS` hide every level but
       off and high.
    2. ``"openrouter"``: an OpenRouter row takes OpenRouter's public
       ``reasoning.supported_efforts`` (:func:`openrouter_thinking_level_map`).
    3. ``"models.dev"``: any other row whose inherited transport sends the
       level's own name (:func:`sends_level_name`) takes models.dev's
       ``reasoning_options`` effort values.
    4. ``"none"``: no map. The row offers off to high and the OpenAI-family
       adapters send each level's name, as pi publishes such rows.

    ``"not reasoning"`` for a row that does not reason. A row this script adds
    takes the whole map. For a row we already ship, the map is a key that
    exists; only its ``off`` is read, by :func:`plan_off_fills` (#507).
    """

    if not row.get("reasoning"):
        return None, "not reasoning"
    if provider in PROVIDER_RULE_PROVIDERS:
        return dict(PROVIDER_RULE_MAP), "provider rule"
    if provider == "openrouter":
        mapped = openrouter_thinking_level_map((openrouter_row or {}).get("reasoning"))
        return (mapped, "openrouter") if mapped else (None, "none")
    if sends_level_name(row):
        mapped = effort_thinking_level_map(_models_dev_effort_values(upstream_row))
        if mapped:
            return mapped, "models.dev"
    return None, "none"


def openrouter_by_id(listing: dict[str, Any] | None) -> dict[str, Row] | None:
    """OpenRouter's ``/api/v1/models`` body as ``{id: row}``, or ``None`` without one."""

    if listing is None:
        return None
    data = listing.get("data")
    if not isinstance(data, list):
        raise ValueError("OpenRouter's model list has no data array")
    return {item["id"]: item for item in data if isinstance(item, dict) and "id" in item}


# ── off for the rows we already ship (#507) ────────────────────────────

OFF_EVIDENCE_IDS: dict[str, tuple[tuple[str, str], ...]] = {
    "azure-openai-responses": (("azure", ""), ("openai", "")),
    "cloudflare-ai-gateway": (("cloudflare-ai-gateway", "openai/"), ("openai", "")),
}
"""Where a shipped row's ``off`` is looked up when upstream has no row under our id: ``(provider, id prefix)``.

Evidence only. :func:`plan_catalog` adds nothing from these, so the reasoning
behind :data:`UPSTREAM_IDS` (overlap measures the catalog, not the provider)
is untouched: the question here is not which models a provider sells, but
whether a model we already sell takes ``reasoning.effort: "none"``, and that is
the model's. Only a Responses row reaches the lookup (:func:`sends_none_for_off`),
and on both providers that row is an OpenAI model:

- ``azure-openai-responses``: models.dev's ``azure`` lists ``o1``, ``o3``,
  ``o3-mini`` and ``o4-mini`` with ``low``, ``medium`` and ``high``, as its
  ``openai`` does (fetched 2026-10-26); ``o1-pro`` and ``o3-pro`` are only under
  ``openai``. pi copies these rows from its OpenAI rows and gives them no map
  (pi.dev, 2026-10-26), which sends ``none``: a divergence.
- ``cloudflare-ai-gateway``: models.dev keys the gateway by the upstream it
  fronts, so our ``o3`` is its ``openai/o3`` — the prefix pi strips
  (``generate-models.ts:1985-2002`` at ``pi@b84d5d1bc``), and where pi.dev's own
  ``o3`` takes ``off: null``. ``o1`` and ``o3-pro`` are only under ``openai``.
"""


def sends_none_for_off(row: Row) -> bool:
    """True when the adapter sends ``effort: "none"`` for off because the map has no ``off`` key.

    The Responses APIs (``model.thinkingLevelMap?.off ?? "none"``,
    ``openai_responses.py`` and ``openai_codex_responses.py``, both of which
    leave GitHub Copilot out) and the ``openrouter`` thinking format of Chat
    Completions (``openai_completions.py``, for any provider). This build has
    no ``azure-openai-responses`` adapter (ADR-0254), so its rows are filled for
    when it has one, as pi's wire would send them. An OpenAI-format Chat
    Completions row sends nothing for an absent ``off``, and every other wire
    switches reasoning off its own way, so none of them can fail as #507 did.
    """

    if row["api"] in _DIRECT_EFFORT_APIS:
        return row.get("provider") != "github-copilot"
    if row["api"] != "openai-completions":
        return False
    return _merged_compat(row).thinking_format == "openrouter"


def _sends_off_string(row: Row) -> bool:
    """True when an ``off`` string in the map goes on the wire: :func:`sends_none_for_off`'s wires,
    and OpenAI-format Chat Completions with effort support, which sends it as ``reasoning_effort``."""

    return sends_none_for_off(row) or (row["api"] == "openai-completions" and sends_level_name(row))


def off_evidence_row(provider: str, model_id: str, upstream: dict[str, Any]) -> Row | None:
    """The models.dev row whose ``reasoning_options`` say whether ``provider``/``model_id`` takes ``none``.

    Our own provider's row under :data:`UPSTREAM_IDS` when there is one, then
    the first of :data:`OFF_EVIDENCE_IDS` that lists effort values.
    """

    found = upstream_models_for(provider, upstream).get(model_id)
    if found is not None:
        return found
    for upstream_id, prefix in OFF_EVIDENCE_IDS.get(provider, ()):
        candidate = ((upstream.get(upstream_id) or {}).get("models") or {}).get(prefix + model_id)
        if _models_dev_effort_values(candidate):
            return candidate
    return None


def plan_off_fills(
    local: Catalog, upstream: dict[str, Any], openrouter: dict[str, Row] | None
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """``(fills, conflicts)``: shipped rows whose ``off`` upstream says cannot be ``none``.

    #507. A row whose map has no ``off`` key sends ``effort: "none"`` for off
    (:func:`sends_none_for_off`), and a model that takes no ``none`` answers
    400 — MEASURED on OpenRouter's mandatory-reasoning endpoints, "Reasoning is
    mandatory for this endpoint and cannot be disabled", and on OpenAI's own
    API (#506). ``off: null`` omits the field instead, and the request runs at
    the model's default. The evidence is the one a new row's map comes from,
    in the same order (:func:`derive_thinking_level_map`): OpenRouter's
    ``reasoning.mandatory`` for an OpenRouter row, models.dev's effort list
    without ``none`` for a row whose wire sends the level's name. Of that map
    only ``off`` is taken. The other levels a shipped row offers are
    ``main``'s curation (ADR-0264 §7 Q2), not this issue's.

    A fill is ``off`` written into a map that has no ``off`` — a key that does
    not exist, so the additive contract holds. A row whose ``off`` string goes
    on the wire (:func:`_sends_off_string`) and upstream contradicts is a
    conflict: reported for a hand correction, never overwritten.

    Only ``off`` is filled here. A level the harness clamps — a launch with no
    level, a resumed off session — then lands on the lowest level the map
    offers, and the levels under it a model refuses are :func:`plan_floor_fills`'s
    (#397): without them OpenRouter's ``openai/o3`` with no ``--thinking``
    answered 400 "Unsupported value: 'minimal'" (MEASURED, ADR-0264 §10).
    """

    fills: list[tuple[str, str, str]] = []
    conflicts: list[tuple[str, str, str]] = []
    for provider, local_models in local.items():
        for model_id, row in local_models.items():
            if not row.get("reasoning") or not _sends_off_string(row):
                continue
            mapped, source = derive_thinking_level_map(
                provider,
                row,
                off_evidence_row(provider, model_id, upstream),
                (openrouter or {}).get(model_id),
            )
            if mapped is None or "off" not in mapped or mapped["off"] is not None:
                continue
            current = row.get("thinkingLevelMap") or {}
            if "off" not in current:
                if sends_none_for_off(row):
                    fills.append((provider, model_id, source))
            elif current["off"] is not None:
                conflicts.append((provider, model_id, source))
    return fills, conflicts


def fill_off(local: Catalog, fills: list[tuple[str, str, str]]) -> Catalog:
    """``local`` with ``off: null`` written into each fill's map — never over an ``off`` that exists.

    ``off`` goes first in the map, where pi's maps carry it; a row with no map
    gets one right after ``reasoning``, where the rows that came from pi carry
    theirs, and a ``"thinkingLevelMap": null`` is replaced where it stands. Row
    and provider order are untouched (``cycle_model``, :func:`merge`).
    """

    filled: Catalog = {provider: dict(rows) for provider, rows in local.items()}
    for provider, model_id, _source in fills:
        row = filled[provider][model_id]
        current = row.get("thinkingLevelMap")
        assert "off" not in (current or {}), f"would overwrite {provider}/{model_id}'s off"
        mapped = {"off": None, **(current or {})}
        if "thinkingLevelMap" in row:
            filled[provider][model_id] = {**row, "thinkingLevelMap": mapped}
            continue
        rebuilt: Row = {}
        for key, value in row.items():
            rebuilt[key] = value
            if key == "reasoning":
                rebuilt["thinkingLevelMap"] = mapped
        rebuilt.setdefault("thinkingLevelMap", mapped)
        filled[provider][model_id] = rebuilt
    return filled


# ── the floor under a hidden off (#397) ────────────────────────────────

OPENROUTER_PASSTHROUGH: dict[str, str] = {"openai/": "openai"}
"""OpenRouter id prefixes whose ``reasoning.effort`` reaches the vendor's API as sent: ``{prefix: models.dev provider}``.

Evidence only, for an OpenRouter row whose public list gives no
``supported_efforts``. MEASURED 2026-10-26 (``.omc/probes/397/``): with
``effort: "minimal"`` OpenRouter's ``openai/o3``, ``o3-mini`` and ``o3-pro``
answered 400 with OpenAI's own body, "Unsupported value: 'minimal' is not
supported with the 'o3-2025-04-16' model", while the 61 other live rows #507
filled answered 200 — the 42 that list efforts among them, including the 32
whose list leaves ``minimal`` out. So OpenRouter's own lists are not read for
the floor: OpenRouter accepts a level they leave out.
"""

FLOOR_FALLBACK_IDS: tuple[str, ...] = ("openai", "azure", "opencode")
"""Where a Responses row's floor is looked up when its own evidence lists nothing: the same id.

Evidence only, and only for the floor. A Responses row is an OpenAI model, and
OpenAI refuses an effort a model does not take (MEASURED on ``openai/o3``
through OpenRouter; pi #1334 on ``gpt-5.3-codex``, its error naming ``gpt-5.2``). models.dev's ``openai`` has
no row for ``gpt-5-codex``, ``gpt-5.1-codex``,
``gpt-5.1-codex-max``, ``gpt-5.1-codex-mini`` or ``gpt-5.2-codex``; its ``azure``
and ``opencode`` list them from ``low`` (fetched 2026-10-26), and pi.dev's
``opencode`` maps hide ``minimal`` on them. #507's off lookups stay as they were
(:data:`OFF_EVIDENCE_IDS`).
"""


def floor_evidence(
    provider: str,
    model_id: str,
    row: Row,
    upstream: dict[str, Any],
    openrouter: dict[str, Row] | None,
) -> tuple[list[Any], str] | None:
    """The effort values of the API that will judge ``row``'s level, and where they come from.

    A Responses row's API reads the level's name, so its models.dev row speaks
    for it (:func:`off_evidence_row`), else the same id under
    :data:`FLOOR_FALLBACK_IDS`. GitHub Copilot is in: its adapters skip only the
    off branch (#507), and a fitted ``off`` reaches them as a level's name. An
    OpenRouter row with no effort list of its own speaks through
    :data:`OPENROUTER_PASSTHROUGH`. ``None`` for every other row: its wire maps
    the level itself (Anthropic, Google) or does not refuse it (OpenRouter with a
    list, MEASURED).
    """

    if row["api"] in _DIRECT_EFFORT_APIS:
        values = _models_dev_effort_values(off_evidence_row(provider, model_id, upstream))
        if values:
            return values, "models.dev"
        for upstream_id in FLOOR_FALLBACK_IDS:
            candidate = ((upstream.get(upstream_id) or {}).get("models") or {}).get(model_id)
            if _models_dev_effort_values(candidate):
                return _models_dev_effort_values(candidate), f"models.dev {upstream_id}"
        return None
    if provider != "openrouter":
        return None
    listed = (((openrouter or {}).get(model_id) or {}).get("reasoning") or {}).get(
        "supported_efforts"
    )
    if listed:
        return None
    for prefix, upstream_id in OPENROUTER_PASSTHROUGH.items():
        if model_id.startswith(prefix):
            vendor = ((upstream.get(upstream_id) or {}).get("models") or {}).get(
                model_id[len(prefix) :]
            )
            return _models_dev_effort_values(vendor), f"models.dev {upstream_id}"
    return None


def plan_floor_fills(
    local: Catalog, upstream: dict[str, Any], openrouter: dict[str, Row] | None
) -> tuple[list[tuple[str, str, str, str]], list[tuple[str, str, str, str]]]:
    """``(fills, conflicts)``: the levels under a hidden off a model refuses, and what needs a hand.

    A fill is ``(provider, id, level, source)``, a conflict ``(provider, id, level, why)``.

    #397. Where a row hides ``off``, a level the harness fits to the model — a
    launch with no level, a resumed or carried ``off`` — moves up to the lowest
    level the map offers (``clamp_thinking_level``, pi's ``clampThinkingLevel``),
    and a map that names nothing below ``high`` offers ``minimal``. MEASURED on
    ``2cbc126b``: OpenRouter's ``openai/o3`` with no ``--thinking`` answered 400
    "Unsupported value: 'minimal'". So each level below the lowest one the
    evidence (:func:`floor_evidence`) lists is written ``null`` where the map
    has no key for it, and the fitted ``off`` lands on a level the model takes,
    as pi's derived maps hide them (pi.dev's ``openai`` rows ``o3`` and
    ``gpt-5.5-pro``, 2026-10-26; its OpenRouter ``openai/o3`` hides only ``off``,
    so the OpenRouter rows go past pi). Only rows that hide ``off``: elsewhere
    ``off`` is the floor. Only the levels below the floor: pi's maps hide every
    level a list leaves out, which here stays ``main``'s curation (§7 Q2).

    A key that does not exist, so the generator stays additive. Reported for a
    hand correction, never written: a level below the floor that the map names
    with a string, and a floor at ``xhigh`` or ``max`` the map does not name,
    which would leave the model no level at all (those two exist only where a
    map names them).
    """

    fills: list[tuple[str, str, str, str]] = []
    conflicts: list[tuple[str, str, str, str]] = []
    for provider, local_models in local.items():
        for model_id, row in local_models.items():
            current = row.get("thinkingLevelMap") or {}
            if not row.get("reasoning") or "off" not in current or current["off"] is not None:
                continue
            found = floor_evidence(provider, model_id, row, upstream, openrouter)
            if found is None:
                continue
            values, source = found
            listed = [level for level in _EFFORT_LEVELS if level in values]
            if not listed:
                continue
            if listed[0] in ("xhigh", "max") and current.get(listed[0]) is None:
                conflicts.append(
                    (
                        provider,
                        model_id,
                        listed[0],
                        f"{source}'s floor, which the map does not name",
                    )
                )
                continue
            for level in _EFFORT_LEVELS[: _EFFORT_LEVELS.index(listed[0])]:
                if level not in current:
                    fills.append((provider, model_id, level, source))
                elif current[level] is not None:
                    conflicts.append(
                        (provider, model_id, level, f"named under the floor {source} lists")
                    )
    return fills, conflicts


def fill_floor(local: Catalog, fills: list[tuple[str, str, str, str]]) -> Catalog:
    """``local`` with each fill's level written ``null`` — never over a level that exists.

    The floor — the new levels, with any hidden level already below the
    highest of them — goes right after ``off``, wherever ``off`` stands, in the
    order pi's maps carry them; the rest of the map keeps its order, and so do
    the rows and providers.
    """

    hidden: dict[tuple[str, str], list[str]] = {}
    for provider, model_id, level, _source in fills:
        hidden.setdefault((provider, model_id), []).append(level)
    filled: Catalog = {provider: dict(rows) for provider, rows in local.items()}
    for (provider, model_id), levels in hidden.items():
        row = filled[provider][model_id]
        current = row["thinkingLevelMap"]
        assert current.get("off", "") is None, f"{provider}/{model_id} does not hide off"
        for level in levels:
            assert level not in current, f"would overwrite {provider}/{model_id}'s {level}"
        top = max(_EFFORT_LEVELS.index(level) for level in levels)
        floor = [
            level
            for level in _EFFORT_LEVELS[: top + 1]
            if level in levels or (level in current and current[level] is None)
        ]
        mapped: dict[str, Any] = {}
        for key, value in current.items():
            if key in floor:
                continue
            mapped[key] = value
            if key == "off":
                mapped.update({level: None for level in floor})
        filled[provider][model_id] = {**row, "thinkingLevelMap": mapped}
    return filled


# ── planning ───────────────────────────────────────────────────────────


class CatalogPlan:
    """What a refresh would add, what it skipped and why, each new row's map source, and the off fills.

    ``floor_fills`` (#397) are planned over the shipped rows after their off
    fills, so a row #507's rule hides ``off`` on in this run gets its floor in
    the same run; a row this run ADDS gets its floor at the next refresh, once
    it is shipped.

    A plain class, not a dataclass: the tests load this file with
    ``spec_from_file_location`` without registering it in ``sys.modules``, and
    ``dataclasses`` looks its module up there.
    """

    def __init__(self) -> None:
        self.additions: Catalog = {}
        self.skips: list[tuple[str, str, str]] = []
        self.map_sources: dict[tuple[str, str], str] = {}
        self.off_fills: list[tuple[str, str, str]] = []
        self.off_conflicts: list[tuple[str, str, str]] = []
        self.floor_fills: list[tuple[str, str, str, str]] = []
        self.floor_conflicts: list[tuple[str, str, str, str]] = []


def plan_catalog(
    local: Catalog,
    upstream: dict[str, Any],
    openrouter: dict[str, Row] | None = None,
) -> CatalogPlan:
    """The additions for every provider we already ship, each new reasoning row's map derived.

    ``openrouter`` is OpenRouter's list by id (:func:`openrouter_by_id`); without
    it rule 2 has nothing to read and new OpenRouter rows get no map, which is
    why ``--apply`` refuses to run without it. The off fills and conflicts of
    the rows ``local`` already has are :func:`plan_off_fills`'s; nothing here
    applies them.

    Providers we ship that upstream does not have — ``openai-codex``,
    ``azure-openai-responses``, ``fireworks``, ``together``,
    ``vercel-ai-gateway``, ``ant-ling``, ``kimi-coding``, ``zai-coding-cn`` —
    get no addition and none is reported: upstream has nothing to say about
    them (``azure-openai-responses``'s off fills read other providers' rows,
    :data:`OFF_EVIDENCE_IDS`), and a provider we do NOT ship is not a gap this
    script is allowed to decide to fill. Adding one means adding auth, an env
    var, and a resolver default.
    """

    result = CatalogPlan()
    for provider, local_models in local.items():
        upstream_models = upstream_models_for(provider, upstream)
        for model_id, upstream_row in upstream_models.items():
            if model_id in local_models:
                continue
            reason = is_eligible(upstream_row)
            if reason is not None:
                result.skips.append((provider, model_id, reason))
                continue
            transport, why = resolve_transport(
                model_id, upstream_row, local_models, upstream_models
            )
            if transport is None:
                result.skips.append((provider, model_id, why))
                continue
            try:
                row = build_row(provider, model_id, upstream_row, transport, local_models)
            except ValueError as exc:
                result.skips.append((provider, model_id, str(exc)))
                continue
            mapped, source = derive_thinking_level_map(
                provider, row, upstream_row, (openrouter or {}).get(model_id)
            )
            if mapped is not None:
                row["thinkingLevelMap"] = mapped
            result.map_sources[(provider, model_id)] = source
            result.additions.setdefault(provider, {})[model_id] = row
    result.off_fills, result.off_conflicts = plan_off_fills(local, upstream, openrouter)
    result.floor_fills, result.floor_conflicts = plan_floor_fills(
        fill_off(local, result.off_fills), upstream, openrouter
    )
    return result


def plan(
    local: Catalog, upstream: dict[str, Any], openrouter: dict[str, Row] | None = None
) -> tuple[Catalog, list[tuple[str, str, str]]]:
    """``(additions, skips)``: :func:`plan_catalog` without the map sources."""

    planned = plan_catalog(local, upstream, openrouter)
    return planned.additions, planned.skips


def merge(local: Catalog, additions: Catalog) -> Catalog:
    """``local`` with ``additions`` appended per provider — never overwriting.

    New rows land at the end of their provider, which is where every hand
    addition has landed. Order is load-bearing: ``models_generated.py`` relies
    on insertion order for ``cycle_model`` rotation, so appending keeps every
    existing model's position in that rotation.
    """

    merged: Catalog = {provider: dict(rows) for provider, rows in local.items()}
    for provider, rows in additions.items():
        for model_id, row in rows.items():
            assert model_id not in merged[provider], f"would overwrite {model_id}"
            merged[provider][model_id] = row
    return merged


# ── the rule, scored against the rows we already have ──────────────────


def _evidence_for(
    model_id: str,
    index: int,
    order: list[str],
    local_models: dict[str, Row],
    upstream_models: dict[str, Row],
    *,
    forward: bool,
) -> dict[str, Row]:
    """The rows the audit lets itself use when predicting ``model_id``.

    Always minus ``model_id``. Under ``forward``, also minus everything
    released after it — by upstream date where both rows have one, otherwise by
    catalog position, which puts hand-added rows last.
    """

    mine = _released(model_id, upstream_models)
    evidence: dict[str, Row] = {}
    for position, (candidate, row) in enumerate(local_models.items()):
        if candidate == model_id:
            continue
        if forward:
            stamp = _released(candidate, upstream_models)
            newer = stamp > mine if (stamp and mine) else position > index
            if newer:
                continue
        evidence[candidate] = row
    return evidence


def audit_transport_rule(
    local: Catalog, upstream: dict[str, Any], *, forward: bool = True
) -> tuple[int, int, list[tuple[str, str, str, str]]]:
    """Hide a row we ship and ask the rule to predict it. ``(correct, answered, misses)``.

    This is the whole reason to trust the additions. Without it, "inherit from a
    sibling" is a plausible sentence; with it, it is a number that a change to
    the ladder moves, and ``tests/test_catalog_overlay.py`` fails when it moves
    the wrong way.

    ``forward`` hides the row AND everything released after it, which is the
    question actually being asked: a model being added is new, so the evidence
    available to it is its predecessors. Plain leave-one-out instead lets a row
    be predicted from its own successors, and MEASURED that inverts the
    ``compat`` result — it asks the rule to guess that ``claude-3-haiku`` does
    NOT need the ``forceAdaptiveThinking`` that every recent Claude carries, and
    scores 89.1% by punishing it for saying the newest thing. The wire is
    unaffected either way: 5 misses in 981 under both.

    A row the rule declines is not an answer — declining is a supported
    outcome, and the caller skips the model — so ``answered`` is the
    denominator and ``misses`` names only what it got confidently wrong.
    """

    correct = answered = 0
    misses: list[tuple[str, str, str, str]] = []
    fields = ("api", "baseUrl", "headers", "compat")
    for provider, local_models in local.items():
        upstream_models = upstream_models_for(provider, upstream)
        order = list(local_models)
        for index, (model_id, row) in enumerate(local_models.items()):
            held_out = _evidence_for(
                model_id, index, order, local_models, upstream_models, forward=forward
            )
            predicted, why = resolve_transport(
                model_id, upstream_models.get(model_id), held_out, upstream_models
            )
            if predicted is None:
                continue
            answered += 1
            actual = transport_of(row)
            if predicted == actual:
                correct += 1
                continue
            # Name the field that disagreed. Reporting only ``api`` here would
            # have called ten ``compat`` differences "wrong transport" and made
            # a rule that is right about the wire look broken.
            wrong = [
                field
                for field, guess, truth in zip(fields, predicted, actual, strict=True)
                if guess != truth
            ]
            misses.append((provider, model_id, "+".join(wrong), why))
    return correct, answered, misses


# ── cli ────────────────────────────────────────────────────────────────


def fetch_json(url: str) -> tuple[bytes, Any]:
    """GET ``url`` (no credential) and return ``(raw bytes, decoded JSON)``."""

    request = urllib.request.Request(url, headers={"User-Agent": "aelix-catalog-refresh"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read()
    return raw, json.loads(raw.decode("utf-8"))


def _load_upstream(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Row] | None]:
    if args.fetch:
        _raw, upstream = fetch_json(UPSTREAM_URL)
        _raw, listing = fetch_json(OPENROUTER_URL)
        return upstream, openrouter_by_id(listing)
    if not args.source:
        raise SystemExit("--source PATH or --fetch is required")
    upstream = json.loads(Path(args.source).read_text(encoding="utf-8"))
    listing = (
        json.loads(Path(args.openrouter_source).read_text(encoding="utf-8"))
        if args.openrouter_source
        else None
    )
    return upstream, openrouter_by_id(listing)


def write_catalog(catalog: Catalog, path: Path = CATALOG_PATH) -> None:
    """Write the catalog as every refresh has: two-space indent, UTF-8, no trailing newline."""

    path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="path to a models.dev api.json")
    parser.add_argument(
        "--openrouter-source", help="path to OpenRouter's public /api/v1/models body"
    )
    parser.add_argument(
        "--fetch", action="store_true", help=f"download {UPSTREAM_URL} and {OPENROUTER_URL}"
    )
    parser.add_argument("--apply", action="store_true", help="write the catalog")
    parser.add_argument(
        "--off-only",
        action="store_true",
        help="with --apply, write the off and floor fills of the rows we ship and no addition (#507, #397)",
    )
    parser.add_argument("--audit", action="store_true", help="score the transport rule")
    parser.add_argument("--show-skips", action="store_true")
    parser.add_argument(
        "--strict", action="store_true", help="audit without the release-date cutoff"
    )
    args = parser.parse_args(argv)
    if args.off_only and not args.apply:
        parser.error("--off-only modifies --apply")

    local: Catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    upstream, openrouter = _load_upstream(args)

    if args.audit:
        correct, answered, misses = audit_transport_rule(local, upstream, forward=not args.strict)
        pct = 100.0 * correct / answered if answered else 0.0
        mode = "leave-one-out" if args.strict else "forward"
        print(f"transport rule: {correct}/{answered} = {pct:.1f}% ({mode})")
        fatal = [m for m in misses if "api" in m[2] or "baseUrl" in m[2]]
        print(f"  of {len(misses)} misses, {len(fatal)} would not reach the provider")
        for provider, model_id, wrong, why in misses:
            mark = "FATAL" if ("api" in wrong or "baseUrl" in wrong) else "quirk"
            print(f"  {mark} {provider}/{model_id}: {wrong} disagreed — {why}")
        return 0

    if args.apply and openrouter is None:
        raise SystemExit(
            "--apply needs OpenRouter's list (--fetch, or --openrouter-source PATH): "
            "without it every new OpenRouter reasoning row would land without its map"
        )

    planned = plan_catalog(local, upstream, openrouter)
    additions, skips = planned.additions, planned.skips
    total = sum(len(rows) for rows in additions.values())
    for provider, rows in sorted(additions.items(), key=lambda kv: -len(kv[1])):
        print(f"  + {provider:26} {len(rows):>4}  {', '.join(sorted(rows))[:96]}")
    unwritten = " (not written: --off-only)" if args.off_only else ""
    print(f"{total} model(s) to add, {len(skips)} skipped{unwritten}")
    sources: dict[str, int] = {}
    for source in planned.map_sources.values():
        sources[source] = sources.get(source, 0) + 1
    print(
        "thinking maps of the new rows: "
        + ", ".join(f"{k} {v}" for k, v in sorted(sources.items()))
    )
    if args.show_skips:
        for provider, model_id, reason in sorted(skips):
            print(f"  - {provider}/{model_id}: {reason}")
    print(f"{len(planned.off_fills)} shipped row(s) to take off: null (#507)")
    for provider, model_id, source in planned.off_fills:
        print(f"  ~ {provider}/{model_id}: {source}")
    for provider, model_id, source in planned.off_conflicts:
        print(f"  ! {provider}/{model_id}: an off string {source} says it cannot take; fix by hand")
    print(f"{len(planned.floor_fills)} level(s) to hide under a hidden off (#397)")
    for provider, model_id, level, source in planned.floor_fills:
        print(f"  ~ {provider}/{model_id}: {level}: null ({source})")
    for provider, model_id, level, why in planned.floor_conflicts:
        print(f"  ! {provider}/{model_id}: {level}, {why}; fix by hand")

    if args.apply:
        filled = fill_floor(fill_off(local, planned.off_fills), planned.floor_fills)
        write_catalog(filled if args.off_only else merge(filled, additions), CATALOG_PATH)
        print(f"wrote {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
