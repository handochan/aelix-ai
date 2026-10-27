"""Pi parity: ``packages/ai/src/models.generated.ts`` (SHA 734e08e).

Sprint 6g₁ (ADR-0067, P-197/P-203) shipped the FULL 32-provider catalog
loaded from ``models_generated.json`` at module import, replacing the
Sprint 6f₁ 13-model seed. It is now 35 providers and 1427 models: #15 added
three providers, and #172 (ADR-0232) added 422 models from upstream through
``scripts/refresh_catalog.py``, which appends and never rewrites.

The :data:`MODELS` dict shape contract (``dict[provider, dict[model_id,
Model]]``) is binding and downstream code MUST NOT break it.

Costs are per-million-tokens (matches Pi ``ai/src/types.ts::Model.cost``).
Provider/model insertion order follows the JSON catalog (Python 3.7+
guarantees dict order preservation), which mirrors Pi
``models.generated.ts`` key order for stable ``cycle_model`` rotation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aelix_ai.streaming import Model, ModelCost

_CATALOG_JSON_PATH: Path = Path(__file__).parent / "models_generated.json"
"""Pi catalog data file (auto-generated from ``models.generated.ts``)."""


def model_from_row(entry: Mapping[str, Any]) -> Model:
    """One catalog row in ``models_generated.json``'s shape as a :class:`Model`.

    Pi camelCase JSON keys are translated to snake_case Aelix :class:`Model`
    fields. Sprint 6g₂ W6 P-209 MAJOR fix: Pi-required fields (``id``,
    ``name``, ``api``, ``provider``, ``baseUrl``, ``reasoning``, ``input``,
    ``contextWindow``, ``maxTokens``) raise :exc:`KeyError` when missing so a
    corrupted catalog fails fast at import time instead of silently falling
    back to ``""`` / ``0`` / ``False``. Genuinely optional Pi fields
    (``thinkingLevelMap``, ``headers``, ``compat``, ``cost``) keep
    ``.get(...)`` because they may be absent in valid catalog entries
    (``cost`` itself is required but each of its 4 sub-keys defaults to
    ``0.0`` per Pi's ``ModelCost`` shape).

    #434: :func:`aelix_ai.models.install_overlay` builds a hosted revision's
    rows with it too, so a published row becomes a :class:`Model` exactly as a
    shipped one does.
    """

    cost_dict = entry["cost"]
    cost = ModelCost(
        input=float(cost_dict.get("input", 0.0)),
        output=float(cost_dict.get("output", 0.0)),
        cache_read=float(cost_dict.get("cacheRead", 0.0)),
        cache_write=float(cost_dict.get("cacheWrite", 0.0)),
    )
    return Model(
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


def _read_catalog_json() -> dict[str, dict[str, dict[str, Any]]]:
    with _CATALOG_JSON_PATH.open(encoding="utf-8") as f:
        raw: dict[str, dict[str, dict[str, Any]]] = json.load(f)
    return raw


def _models_of(raw: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, Model]]:
    return {
        provider_name: {model_id: model_from_row(entry) for model_id, entry in models_dict.items()}
        for provider_name, models_dict in raw.items()
    }


def _load_catalog() -> dict[str, dict[str, Model]]:
    """Load the Pi catalog at module import.

    Pi parity: ``models.generated.ts:1-16386``. Order of providers and models
    follows JSON insertion order; each row goes through :func:`model_from_row`,
    which fails fast on a missing required field.
    """

    return _models_of(_read_catalog_json())


SHIPPED_CATALOG: dict[str, dict[str, dict[str, Any]]] = _read_catalog_json()
"""``models_generated.json`` as parsed, transport included (#434, ADR-0264 §4.3).

The hosted catalog's parser reads this build's shipped rows in this shape, and
its digest (:func:`aelix_ai.hosted_catalog.catalog_digest`) is computed over it,
as the publisher computes it over the committed file. Treat it as read-only.
"""


MODELS: dict[str, dict[str, Model]] = _models_of(SHIPPED_CATALOG)
"""Pi parity: ``MODELS`` constant. Loaded at import from JSON catalog."""


__all__ = ["MODELS", "SHIPPED_CATALOG", "model_from_row"]
