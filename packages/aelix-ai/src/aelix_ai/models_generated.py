"""Pi parity: ``packages/ai/src/models.generated.ts`` (SHA 734e08e).

Sprint 6g₁ (ADR-0067, P-197/P-203) ships the FULL 32-provider catalog
(~942 models) loaded from ``models_generated.json`` at module import.
Replaces the Sprint 6f₁ 13-model seed.

The :data:`MODELS` dict shape contract (``dict[provider, dict[model_id,
Model]]``) is binding and downstream code MUST NOT break it.

Costs are per-million-tokens (matches Pi ``ai/src/types.ts::Model.cost``).
Provider/model insertion order follows the JSON catalog (Python 3.7+
guarantees dict order preservation), which mirrors Pi
``models.generated.ts`` key order for stable ``cycle_model`` rotation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aelix_ai.streaming import Model, ModelCost

_CATALOG_JSON_PATH: Path = Path(__file__).parent / "models_generated.json"
"""Pi catalog data file (auto-generated from ``models.generated.ts``)."""


def _load_catalog() -> dict[str, dict[str, Model]]:
    """Load the Pi catalog at module import.

    Pi parity: ``models.generated.ts:1-16386``. Pi camelCase JSON keys
    are translated to snake_case Aelix :class:`Model` fields. Order of
    providers and models follows JSON insertion order.

    Sprint 6g₂ W6 P-209 MAJOR fix: Pi-required fields (``id``, ``name``,
    ``api``, ``provider``, ``baseUrl``, ``reasoning``, ``input``,
    ``contextWindow``, ``maxTokens``) raise :exc:`KeyError` when missing
    so a corrupted catalog fails fast at import time instead of silently
    falling back to ``""`` / ``0`` / ``False``. Genuinely optional Pi
    fields (``thinkingLevelMap``, ``headers``, ``compat``, ``cost``)
    keep ``.get(...)`` because they may be absent in valid catalog
    entries (``cost`` itself is required but each of its 4 sub-keys
    defaults to ``0.0`` per Pi's ``ModelCost`` shape).
    """

    with _CATALOG_JSON_PATH.open(encoding="utf-8") as f:
        raw: dict[str, dict[str, dict[str, Any]]] = json.load(f)

    catalog: dict[str, dict[str, Model]] = {}
    for provider_name, models_dict in raw.items():
        catalog[provider_name] = {}
        for model_id, entry in models_dict.items():
            cost_dict = entry["cost"]
            cost = ModelCost(
                input=float(cost_dict.get("input", 0.0)),
                output=float(cost_dict.get("output", 0.0)),
                cache_read=float(cost_dict.get("cacheRead", 0.0)),
                cache_write=float(cost_dict.get("cacheWrite", 0.0)),
            )
            catalog[provider_name][model_id] = Model(
                id=entry["id"],
                name=entry["name"],
                api=entry["api"],
                provider=entry["provider"],
                base_url=entry["baseUrl"],
                reasoning=bool(entry["reasoning"]),
                input=list(entry["input"]),
                cost=cost,
                context_window=int(entry["contextWindow"]),
                max_tokens=int(entry["maxTokens"]),
                thinking_level_map=entry.get("thinkingLevelMap"),
                headers=entry.get("headers"),
                compat=entry.get("compat"),
            )
    return catalog


MODELS: dict[str, dict[str, Model]] = _load_catalog()
"""Pi parity: ``MODELS`` constant. Loaded at import from JSON catalog."""


__all__ = ["MODELS"]
