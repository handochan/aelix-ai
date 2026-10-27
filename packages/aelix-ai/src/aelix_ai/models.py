"""Model-level helpers — Sprint 6f (ADR-0065 §C).

Pi parity: ``packages/ai/src/models.ts`` (SHA 734e08e, 92 LOC verbatim
port). Provider→model catalog accessor + cost calculator + thinking-
level clamp. Replaces the Sprint 6b stub :func:`clamp_thinking_level`
with the full Pi-parity 7-helper surface.

**How the anchors below read now (#433, ADR-0266).** The ``models.ts:NN``
line numbers are pi at ``734e08e`` (ADR-0034's pin, an interpretation anchor
since ADR-0235), where the file was 92 lines. At ``pi@fdce48eb4`` (2026-10-20)
it is 1,283 lines and the three thinking helpers this module ports sit at
``models.ts:1242-1274``: ``EXTENDED_THINKING_LEVELS`` (``:1242``, seven values
since ``pi@fbdd46389`` added ``max``), ``getSupportedThinkingLevels``
(``:1244-1253``, which gates ``xhigh`` and ``max`` alike on an explicit map
entry) and ``clampThinkingLevel`` (``:1255-1274``, unchanged: up first, then
down). The level scale and the two gates follow ``fdce48eb4``; every other
anchor in this file still reads at ``734e08e``.

Public surface (Pi parity):

- :func:`get_model` — Pi ``getModel`` (``models.ts:20-26``)
- :func:`get_providers` — Pi ``getProviders`` (``models.ts:28-30``)
- :func:`get_models` — Pi ``getModels`` (``models.ts:32-37``)
- :func:`calculate_cost` — Pi ``calculateCost`` (``models.ts:39-46``)
- :func:`get_supported_thinking_levels` — Pi
  ``getSupportedThinkingLevels`` (``models.ts:50-59``)
- :func:`clamp_thinking_level` — Pi ``clampThinkingLevel``
  (``models.ts:61-80``)
- :func:`models_are_equal` — Pi ``modelsAreEqual`` (``models.ts:86-92``)
- :data:`EXTENDED_THINKING_LEVELS` — Pi
  ``EXTENDED_THINKING_LEVELS`` (``models.ts:48``)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from aelix_ai.models_generated import MODELS, SHIPPED_CATALOG, model_from_row
from aelix_ai.streaming import Model, Usage, UsageCost

# Pi parity: ``models.ts:11-18`` — at module load Pi builds
# ``modelRegistry: Map<string, Map<string, Model>>`` from ``MODELS``.
# Aelix mirrors with a plain dict-of-dicts; insertion order is preserved
# (Pi parity: Map iteration order = insertion order).
_PROVIDER_MODELS: dict[str, dict[str, Model]] = {}
for _provider_name, _models_dict in MODELS.items():
    _PROVIDER_MODELS[_provider_name] = {}
    for _model_id, _model in _models_dict.items():
        _PROVIDER_MODELS[_provider_name][_model_id] = _model


# Pi parity: ``models.ts:1242`` at ``pi@fdce48eb4`` — exact 7-element ordered
# list (``models.ts:48`` at ``734e08e`` had the first six; ``pi@fbdd46389``
# appended ``max``). #433, ADR-0266.
EXTENDED_THINKING_LEVELS: list[str] = [
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]
"""Pi parity: ``models.ts:1242`` (``pi@fdce48eb4``) — extended thinking level enum order.

``max`` sits above ``xhigh`` (#433): a level of its own that a model offers
only when its ``thinkingLevelMap`` names it, exactly like ``xhigh``
(:func:`get_supported_thinking_levels`). 21 of the 24 rows that spelled their top tier
``"xhigh": "max"`` say ``"max": "max"`` (ADR-0266), so ``xhigh`` there clamps up to ``max`` and
sends what it sent; OpenRouter's 3 DeepSeek V4 rows name ``xhigh`` and no ``max``, as pi's do.
"""


# Sprint 6b back-compat: legacy ``ThinkingLevel`` literal alias (the levels
# excluding ``"off"``) — kept so Sprint 6b callers keep type-checking.
ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh", "max"]


def get_model(provider: str, model_id: str) -> Model | None:
    """Pi parity: ``models.ts:20-26`` ``getModel``."""

    return _PROVIDER_MODELS.get(provider, {}).get(model_id)


def get_providers() -> list[str]:
    """Pi parity: ``models.ts:28-30`` ``getProviders``."""

    return list(_PROVIDER_MODELS.keys())


def get_models(provider: str) -> list[Model]:
    """Pi parity: ``models.ts:32-37`` ``getModels``."""

    return list(_PROVIDER_MODELS.get(provider, {}).values())


def calculate_cost(model: Model, usage: Usage) -> UsageCost:
    """Pi parity: ``models.ts:39-46`` ``calculateCost``.

    Mutates :attr:`Usage.cost` in-place to match Pi behavior
    (Pi assigns to ``usage.cost.input`` / ``usage.cost.output`` /
    ``usage.cost.cacheRead`` / ``usage.cost.cacheWrite`` / ``usage.cost.total``
    and returns ``usage.cost``). Per-million divisor applied to every
    rate before multiplication by token counts.
    """

    # pi #5738 (models.ts:386-392): "Anthropic charges 2x base input for 1h cache
    # writes." Split the cache-write tokens into the 1h-TTL slice
    # (``cache_write_1h``, priced at 2× the model's base input rate) and the 5m
    # remainder (``short_write``, priced at the model's cache_write rate). When
    # ``cache_write_1h`` is 0 this reduces to the original 5m-only formula.
    long_write = usage.cache_write_1h or 0
    short_write = usage.cache_write - long_write
    usage.cost.input = (model.cost.input / 1_000_000) * usage.input
    usage.cost.output = (model.cost.output / 1_000_000) * usage.output
    usage.cost.cache_read = (model.cost.cache_read / 1_000_000) * usage.cache_read
    usage.cost.cache_write = (
        model.cost.cache_write * short_write + model.cost.input * 2 * long_write
    ) / 1_000_000
    usage.cost.total = (
        usage.cost.input
        + usage.cost.output
        + usage.cost.cache_read
        + usage.cost.cache_write
    )
    return usage.cost


#: The levels a model offers only when its ``thinkingLevelMap`` names them
#: (pi ``models.ts:1250`` at ``fdce48eb4``: ``level === "xhigh" || level ===
#: "max"``). Every other level is offered unless the map says ``null``.
_EXPLICIT_ONLY_LEVELS: frozenset[str] = frozenset({"xhigh", "max"})


def get_supported_thinking_levels(model: Model) -> list[str]:
    """Pi parity: ``models.ts:1244-1253`` ``getSupportedThinkingLevels`` (``pi@fdce48eb4``).

    Pi rules:

    - If ``model.thinkingLevelMap[level] === null`` the level is NOT
      supported.
    - If ``level`` is ``"xhigh"`` or ``"max"`` AND
      ``model.thinkingLevelMap[level]`` is ``undefined`` (Aelix: absent
      key), NOT supported — the two top levels exist only where a map
      names them (``max`` joined ``xhigh`` here in #433).
    - Otherwise (level mapped to a non-null value, OR level is in
      EXTENDED_THINKING_LEVELS, absent, and neither ``xhigh`` nor
      ``max``), supported.

    Pi parity short-circuit: non-reasoning models support ONLY ``"off"``.
    """

    if not model.reasoning:
        return ["off"]
    thinking_map = model.thinking_level_map or {}
    out: list[str] = []
    for level in EXTENDED_THINKING_LEVELS:
        # Pi parity: ``thinkingLevelMap[level] === null`` → NOT supported.
        # Pi parity: a missing ``xhigh`` or ``max`` key → NOT supported.
        if level in thinking_map:
            if thinking_map[level] is None:
                continue
        else:
            # Key absent
            if level in _EXPLICIT_ONLY_LEVELS:
                continue
        out.append(level)
    return out


def clamp_thinking_level(
    model: Model, level: str | ThinkingLevel | None
) -> str | None:
    """Pi parity: ``models.ts:61-80`` ``clampThinkingLevel`` (``:1255-1274`` at ``fdce48eb4``).

    Sprint 6f W2 (ADR-0065): replaces the Sprint 6b stub. Algorithm
    matches Pi ``models.ts:61-80`` verbatim, and ``pi@fdce48eb4`` has not
    changed it — ``max`` (#433) needed no edit here because the scan walks
    the list. What the scan now does with an old ``xhigh`` on a row that
    names only ``max`` (21 of the 24 rows ADR-0266 reclassified) is step 3: it
    finds ``max`` above it, so a saved ``xhigh`` keeps sending ``max``.

    1. If ``level`` is in :func:`get_supported_thinking_levels`, return
       it unchanged.
    2. If ``level`` is not in :data:`EXTENDED_THINKING_LEVELS`, return
       the first supported level (Pi falls back to ``available[0]``).
    3. Scan forward from ``EXTENDED_THINKING_LEVELS.index(level)`` for
       the next supported level.
    4. Scan backward from ``EXTENDED_THINKING_LEVELS.index(level) - 1``
       for the previous supported level.
    5. Final fallback: ``available[0]`` or ``"off"``.

    Sprint 6b back-compat: ``level=None`` returns ``None`` (Pi has no
    None path — Pi callers always pass a level string; the Aelix
    Sprint 6b stub returned ``None`` and existing callers depend on
    that signal).
    """

    if level is None:
        # Sprint 6b back-compat: caller-side ``None`` propagates through
        # so OpenAI-completions adapters can preserve "no reasoning
        # effort requested" semantics.
        return None

    available = get_supported_thinking_levels(model)
    if level in available:
        return level
    if level not in EXTENDED_THINKING_LEVELS:
        # Unknown spelling — Pi falls back to available[0] / off.
        return available[0] if available else "off"
    idx = EXTENDED_THINKING_LEVELS.index(level)
    # Pi: forward scan from idx to end.
    for i in range(idx, len(EXTENDED_THINKING_LEVELS)):
        cand = EXTENDED_THINKING_LEVELS[i]
        if cand in available:
            return cand
    # Pi: backward scan from idx-1 down to 0.
    for i in range(idx - 1, -1, -1):
        cand = EXTENDED_THINKING_LEVELS[i]
        if cand in available:
            return cand
    return available[0] if available else "off"


def models_are_equal(a: Model | None, b: Model | None) -> bool:
    """Pi parity: ``models.ts:86-92`` ``modelsAreEqual``.

    Compares ``id`` + ``provider``. ``None`` on either side → False.
    """

    if a is None or b is None:
        return False
    return a.id == b.id and a.provider == b.provider


# ── The effective built-in catalog (#434, ADR-0264 §4.3) ───────────────
#
# Declared here, at the end of the module, so the lines above keep their
# numbers. ``_PROVIDER_MODELS`` above is the EFFECTIVE catalog every reader of
# :func:`get_model`, :func:`get_models` and :func:`get_providers` sees: the
# shipped rows, or, once a hosted revision is installed, the rows that revision
# makes of them (a known id's published facts on its shipped transport, plus
# the new ids that inherited one). The shipped rows stay here, untouched.

_SHIPPED_MODELS: dict[str, dict[str, Model]] = _PROVIDER_MODELS
_installed_revision: str | None = None
_shipped_digest: str | None = None


def install_overlay(
    rows: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
    *,
    revision: str | None = None,
) -> None:
    """Make ``rows`` the effective built-in catalog, or the shipped one when ``None``.

    ``rows`` is :attr:`aelix_ai.hosted_catalog.ParsedRevision.rows`: every
    shipped provider and row in shipped order, a known id's hosted facts in
    place, then each new id. Only a validated revision may be installed; the
    parser is what keeps transport out of it. A provider this build does not
    ship is ignored, and a shipped row ``rows`` lacks stays as shipped, so an
    overlay can neither add a provider nor remove a row.

    The swap is one assignment: a reader sees the old map or the new one, never
    a mix. A row equal to its shipped row keeps the shipped :class:`Model`
    object. ``revision`` names what is installed (:func:`installed_overlay`).
    """

    global _PROVIDER_MODELS, _installed_revision
    if rows is None:
        _PROVIDER_MODELS = _SHIPPED_MODELS
        _installed_revision = None
        return
    effective: dict[str, dict[str, Model]] = {}
    for provider, shipped_models in _SHIPPED_MODELS.items():
        shipped_rows = SHIPPED_CATALOG.get(provider, {})
        published = rows.get(provider)
        if published is None:
            effective[provider] = dict(shipped_models)
            continue
        out: dict[str, Model] = {}
        for model_id, row in published.items():
            shipped_model = shipped_models.get(model_id)
            if shipped_model is not None and row == shipped_rows.get(model_id):
                out[model_id] = shipped_model
            else:
                out[model_id] = model_from_row(row)
        for model_id, shipped_model in shipped_models.items():
            out.setdefault(model_id, shipped_model)
        effective[provider] = out
    _PROVIDER_MODELS = effective
    _installed_revision = revision or "unnamed"


def installed_overlay() -> str | None:
    """The name of the installed hosted revision, or ``None`` on the shipped catalog."""

    return _installed_revision


def get_shipped_model(provider: str, model_id: str) -> Model | None:
    """:func:`get_model` over this build's shipped rows, whatever overlay is installed.

    Copilot's policy enablement reads these (§4.3): it writes to the user's
    GitHub account, as pi's reads its shipped constant there.
    """

    return _SHIPPED_MODELS.get(provider, {}).get(model_id)


def get_shipped_models(provider: str) -> list[Model]:
    """:func:`get_models` over this build's shipped rows, whatever overlay is installed."""

    return list(_SHIPPED_MODELS.get(provider, {}).values())


def shipped_catalog() -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    """This build's ``models_generated.json`` as parsed, transport included. Read-only."""

    return SHIPPED_CATALOG


def shipped_digest() -> str:
    """This build's catalog digest (:func:`aelix_ai.hosted_catalog.catalog_digest`), computed once.

    A hosted revision overlays this build only when its lineage holds this
    digest (§4.2's ordering rule).
    """

    global _shipped_digest
    if _shipped_digest is None:
        from aelix_ai.hosted_catalog import catalog_digest

        _shipped_digest = catalog_digest(SHIPPED_CATALOG)
    return _shipped_digest


__all__ = [
    "EXTENDED_THINKING_LEVELS",
    "ThinkingLevel",
    "calculate_cost",
    "clamp_thinking_level",
    "get_model",
    "get_models",
    "get_providers",
    "get_shipped_model",
    "get_shipped_models",
    "get_supported_thinking_levels",
    "install_overlay",
    "installed_overlay",
    "models_are_equal",
    "shipped_catalog",
    "shipped_digest",
]
