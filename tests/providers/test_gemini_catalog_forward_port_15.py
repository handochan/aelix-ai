"""Gemini catalog forward-port (#15 Step 4) — presence + metadata closure.

Faithful forward-port of pi's current ``packages/ai/src/providers/google.models.ts``
and ``google-vertex.models.ts`` for the ``google`` (Gemini Developer API) and
``google-vertex`` providers.

Reconciliation performed against pi (see ``PI_SYNC_SHA`` below):

- Added the stable ``gemini-3.5-flash`` to both ``google`` and ``google-vertex``
  (pi #5761 hardcodes it; aelix lacked it).
- Fixed ``google/gemini-3-pro-preview`` drift: contextWindow 1000000 -> 1048576
  and maxTokens 64000 -> 65536 to match pi.
- Mapped the ``latest`` aliases (pi #5761): ``gemini-flash-latest`` and
  ``gemini-flash-lite-latest`` now carry the ``{"off": null}`` thinkingLevelMap
  and the current-model pricing.
- Forward-ported the ``google-vertex`` rows pi gained: ``gemini-3.1-flash-lite``,
  ``gemini-flash-latest``, ``gemini-flash-lite-latest``.
- Removed the shut-down Vertex preview rows pi removed (#5761):
  ``gemini-3-pro-preview`` and ``gemini-2.5-flash-lite-preview-09-2025``.

pi #6057 ("reasoning tokens") only added the ``Usage.reasoning`` field
(``thoughtsTokenCount`` for Google / Vertex); it changed no catalog metadata,
so no model rows are touched for it here.

Pi sync anchor: every value asserted below was re-fetched verbatim from pi
``packages/ai/src/providers/google.models.ts`` /
``packages/ai/src/providers/google-vertex.models.ts`` at the pinned commit. A
future transcription error fails this test rather than silently matching a
copy-pasted value.
"""

from __future__ import annotations

from aelix_ai.models_generated import MODELS
from aelix_ai.streaming import Model

# pi main HEAD synced against (== task-pinned SHA). Update only when re-syncing
# the gemini catalog against a newer pi, re-verifying every value below.
PI_SYNC_SHA = "3d6acb37b93d2ceedfcc170b2d212c34fedbf193"

_OFF_ONLY = {"off": None}


def test_pi_sync_sha_is_pinned() -> None:
    """The gemini catalog was synced against this exact pi commit."""

    assert PI_SYNC_SHA == "3d6acb37b93d2ceedfcc170b2d212c34fedbf193"
    assert len(PI_SYNC_SHA) == 40


# ── google: gemini-3.5-flash added ─────────────────────────────────


def test_google_gemini_3_5_flash_added() -> None:
    g = MODELS["google"]
    assert "gemini-3.5-flash" in g
    m = g["gemini-3.5-flash"]
    assert isinstance(m, Model)
    assert m.name == "Gemini 3.5 Flash"
    assert m.api == "google-generative-ai"
    assert m.provider == "google"
    assert m.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert m.reasoning is True
    assert m.thinking_level_map == _OFF_ONLY
    assert m.input == ["text", "image"]
    assert (m.cost.input, m.cost.output, m.cost.cache_read, m.cost.cache_write) == (
        1.5,
        9,
        0.15,
        0,
    )
    assert m.context_window == 1048576
    assert m.max_tokens == 65536


# ── google: gemini-3-pro-preview drift fixed ───────────────────────


def test_google_gemini_3_pro_preview_drift_fixed() -> None:
    m = MODELS["google"]["gemini-3-pro-preview"]
    # Drifted values were 1000000 / 64000; pi uses the powers-of-two pair.
    assert m.context_window == 1048576
    assert m.max_tokens == 65536
    # Untouched fields remain pi-faithful.
    assert m.thinking_level_map == {
        "off": None,
        "minimal": None,
        "low": "LOW",
        "medium": None,
        "high": "HIGH",
    }
    assert (m.cost.input, m.cost.output, m.cost.cache_read) == (2, 12, 0.2)


# ── google: latest aliases mapped to current models (#5761) ────────


def test_google_flash_latest_mapped_to_current_model() -> None:
    m = MODELS["google"]["gemini-flash-latest"]
    assert m.thinking_level_map == _OFF_ONLY
    assert (m.cost.input, m.cost.output, m.cost.cache_read, m.cost.cache_write) == (
        1.5,
        9,
        0.15,
        0,
    )
    assert m.context_window == 1048576
    assert m.max_tokens == 65536


def test_google_flash_lite_latest_mapped_to_current_model() -> None:
    m = MODELS["google"]["gemini-flash-lite-latest"]
    assert m.thinking_level_map == _OFF_ONLY
    assert (m.cost.input, m.cost.output, m.cost.cache_read, m.cost.cache_write) == (
        0.25,
        1.5,
        0.025,
        0,
    )
    assert m.context_window == 1048576
    assert m.max_tokens == 65536


# ── google-vertex: gemini-3.5-flash added ──────────────────────────


def test_vertex_gemini_3_5_flash_added() -> None:
    v = MODELS["google-vertex"]
    assert "gemini-3.5-flash" in v
    m = v["gemini-3.5-flash"]
    assert isinstance(m, Model)
    assert m.name == "Gemini 3.5 Flash (Vertex)"
    assert m.api == "google-vertex"
    assert m.provider == "google-vertex"
    assert m.base_url == "https://{location}-aiplatform.googleapis.com"
    assert m.reasoning is True
    assert m.thinking_level_map == _OFF_ONLY
    assert (m.cost.input, m.cost.output, m.cost.cache_read, m.cost.cache_write) == (
        1.5,
        9,
        0.15,
        0,
    )
    assert m.context_window == 1048576
    assert m.max_tokens == 65536


# ── google-vertex: forward-ported new rows ─────────────────────────


def test_vertex_forward_ported_rows_present() -> None:
    v = MODELS["google-vertex"]
    for mid in (
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
    ):
        assert mid in v, mid
        assert isinstance(v[mid], Model)
        assert v[mid].provider == "google-vertex"


def test_vertex_gemini_3_1_flash_lite_metadata() -> None:
    m = MODELS["google-vertex"]["gemini-3.1-flash-lite"]
    assert m.name == "Gemini 3.1 Flash Lite (Vertex)"
    assert m.thinking_level_map == _OFF_ONLY
    assert (m.cost.input, m.cost.output, m.cost.cache_read) == (0.25, 1.5, 0.025)
    assert m.context_window == 1048576
    assert m.max_tokens == 65536


def test_vertex_latest_aliases_metadata() -> None:
    v = MODELS["google-vertex"]
    fl = v["gemini-flash-latest"]
    assert fl.thinking_level_map == _OFF_ONLY
    assert (fl.cost.input, fl.cost.output, fl.cost.cache_read) == (1.5, 9, 0.15)
    fll = v["gemini-flash-lite-latest"]
    assert fll.thinking_level_map == _OFF_ONLY
    assert (fll.cost.input, fll.cost.output, fll.cost.cache_read) == (0.25, 1.5, 0.025)


# ── google-vertex: shut-down preview rows removed (#5761) ──────────


def test_vertex_shutdown_preview_rows_removed() -> None:
    v = MODELS["google-vertex"]
    assert "gemini-3-pro-preview" not in v
    assert "gemini-2.5-flash-lite-preview-09-2025" not in v


def test_google_provider_still_has_gemini_3_pro_preview() -> None:
    """Removal was Vertex-only; the ``google`` row is retained (pi keeps it)."""

    assert "gemini-3-pro-preview" in MODELS["google"]
