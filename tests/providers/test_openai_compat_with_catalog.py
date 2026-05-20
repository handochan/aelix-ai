"""Sprint 6g₂ W6 P-210 regression — catalog ``Model.compat`` merge wiring.

The Sprint 6g₁ binding spec §J said the OpenAI adapter does NOT read
``model.compat`` yet (deferred to Sprint 6g₂). That text was stale —
:func:`aelix_ai.providers._openai_compat.get_compat` (shipped Sprint
6b) already merges ``getattr(model, "compat", None)`` onto the
detection baseline. Sprint 6g₁ wired the catalog (`zai` models now
ship Pi-canonical compat overrides), so the Sprint 6b override path
fires for the first time on real catalog entries.

ADR-0067 amendment text and this regression suite together close
P-210. The merge accepts camelCase Pi keys
(``supportsDeveloperRole`` / ``thinkingFormat`` / ``zaiToolStream``)
directly because :func:`get_compat` translates them to snake_case
dataclass fields.
"""

from __future__ import annotations

from aelix_ai.models_generated import MODELS
from aelix_ai.providers._openai_compat import detect_compat, get_compat


def test_openai_compat_merges_model_compat_for_zai_glm5v_turbo() -> None:
    """Pi catalog ships compat overrides on ``zai/glm-5v-turbo``.

    The Sprint 6g₁ catalog loader threads ``entry["compat"]`` onto
    :attr:`Model.compat`. :func:`get_compat` then merges those
    camelCase keys onto the URL/provider-detected baseline returned by
    :func:`detect_compat`.
    """

    model = MODELS["zai"]["glm-5v-turbo"]

    # Sanity: Pi catalog actually carries the compat dict.
    assert model.compat == {
        "supportsDeveloperRole": False,
        "thinkingFormat": "zai",
        "zaiToolStream": True,
    }

    compat = get_compat(model)

    # camelCase Pi keys translated to snake_case dataclass fields.
    assert compat.supports_developer_role is False
    assert compat.thinking_format == "zai"
    assert compat.zai_tool_stream is True


def test_openai_compat_catalog_merge_overrides_detect_baseline() -> None:
    """Catalog-supplied ``compat`` MUST win over :func:`detect_compat`.

    The zai provider's :func:`detect_compat` baseline already sets
    ``thinking_format="zai"`` via URL/provider sniffing, so the catalog
    override is redundant for ``thinking_format`` on zai models — but
    ``zaiToolStream`` is NOT set by :func:`detect_compat` (it stays
    :data:`False` baseline) and is ONLY reachable through the catalog
    merge path. This pin asserts the merge wiring is the load-bearing
    seam for ``zaiToolStream``.
    """

    model = MODELS["zai"]["glm-5v-turbo"]
    baseline = detect_compat(model)
    merged = get_compat(model)

    # Baseline never sets zai_tool_stream — only the catalog merge does.
    assert baseline.zai_tool_stream is False
    assert merged.zai_tool_stream is True


def test_openai_compat_catalog_merge_for_glm_4_5_air_partial_dict() -> None:
    """Partial compat dicts merge field-by-field; absent keys keep the baseline.

    ``zai/glm-4.5-air`` ships a 2-key compat dict (no ``zaiToolStream``).
    :func:`get_compat` MUST leave the unspecified field at its detected
    baseline rather than silently zeroing it.
    """

    model = MODELS["zai"]["glm-4.5-air"]

    # Pi catalog: 2-key dict (no ``zaiToolStream``).
    assert model.compat == {
        "supportsDeveloperRole": False,
        "thinkingFormat": "zai",
    }

    merged = get_compat(model)
    baseline = detect_compat(model)

    # Specified field overridden.
    assert merged.supports_developer_role is False
    assert merged.thinking_format == "zai"
    # Unspecified field keeps the baseline value.
    assert merged.zai_tool_stream == baseline.zai_tool_stream
