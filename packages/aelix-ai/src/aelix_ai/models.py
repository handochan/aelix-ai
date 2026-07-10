"""Model-level helpers — Sprint 6f (ADR-0065 §C).

Pi parity: ``packages/ai/src/models.ts`` (SHA 734e08e, 92 LOC verbatim
port). Provider→model catalog accessor + cost calculator + thinking-
level clamp. Replaces the Sprint 6b stub :func:`clamp_thinking_level`
with the full Pi-parity 7-helper surface.

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

from typing import Literal

from aelix_ai.models_generated import MODELS
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


# Pi parity: ``models.ts:48`` — exact 6-element ordered list.
EXTENDED_THINKING_LEVELS: list[str] = [
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
]
"""Pi parity: ``models.ts:48`` — extended thinking level enum order."""


# Sprint 6b back-compat: legacy ``ThinkingLevel`` literal alias (5 values
# excluding ``"off"``) — kept so Sprint 6b callers keep type-checking.
ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]


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


def get_supported_thinking_levels(model: Model) -> list[str]:
    """Pi parity: ``models.ts:50-59`` ``getSupportedThinkingLevels``.

    Pi rules:

    - If ``model.thinkingLevelMap[level] === null`` the level is NOT
      supported.
    - If ``level === "xhigh"`` AND ``model.thinkingLevelMap[level]`` is
      ``undefined`` (Aelix: absent key), NOT supported.
    - Otherwise (level mapped to a non-null value, OR level is in
      EXTENDED_THINKING_LEVELS and absent and not ``xhigh``), supported.

    Pi parity short-circuit: non-reasoning models support ONLY ``"off"``.
    """

    if not model.reasoning:
        return ["off"]
    thinking_map = model.thinking_level_map or {}
    out: list[str] = []
    for level in EXTENDED_THINKING_LEVELS:
        # Pi parity: ``thinkingLevelMap[level] === null`` → NOT supported.
        # Pi parity: missing ``xhigh`` key → NOT supported.
        if level in thinking_map:
            if thinking_map[level] is None:
                continue
        else:
            # Key absent
            if level == "xhigh":
                continue
        out.append(level)
    return out


def clamp_thinking_level(
    model: Model, level: str | ThinkingLevel | None
) -> str | None:
    """Pi parity: ``models.ts:61-80`` ``clampThinkingLevel``.

    Sprint 6f W2 (ADR-0065): replaces the Sprint 6b stub. Algorithm
    matches Pi ``models.ts:61-80`` verbatim:

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


__all__ = [
    "EXTENDED_THINKING_LEVELS",
    "ThinkingLevel",
    "calculate_cost",
    "clamp_thinking_level",
    "get_model",
    "get_models",
    "get_providers",
    "get_supported_thinking_levels",
    "models_are_equal",
]
