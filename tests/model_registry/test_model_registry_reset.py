"""Sprint 6h₇c §B (Phase 5a-iii-γ, ADR-0093) — ``ModelRegistry.reset`` tests.

Pi parity: ``model-registry.ts::reset`` naming alias (P-446). Aelix
already shipped :meth:`refresh` in Sprint 6f₁ (ADR-0065); the new
:meth:`reset` is a Pi-parity alias that delegates to :meth:`refresh`.
The :meth:`AgentHarness.reload` chain (`agent-session.ts:2389`) calls
``modelRegistry.reset()`` so the alias keeps the Pi-source citations
clean.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from aelix_ai.oauth import AuthStorage
from aelix_coding_agent.model_registry import ModelRegistry


async def _ready_storage(tmp_path: Path) -> AuthStorage:
    s = AuthStorage(path=tmp_path / "auth.json")
    await s.load()
    return s


async def test_reset_delegates_to_refresh(tmp_path: Path) -> None:
    """``reset()`` invokes the same loader as ``refresh()``."""

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)

    with patch.object(r, "refresh") as mock_refresh:
        r.reset()

    mock_refresh.assert_called_once_with()


async def test_reset_reloads_models(tmp_path: Path) -> None:
    """``reset()`` re-runs ``_load_models()`` end-to-end (no mocks)."""

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)

    before = r.get_all()
    assert before, "seed catalog must be non-empty"

    # Sentinel: forcibly clear the cached list, then reset.
    r._models = []
    assert r.get_all() == []

    r.reset()

    after = r.get_all()
    assert after, "reset() must re-populate via _load_models()"
    # Same models reload deterministically from the seed catalog.
    assert [m.id for m in after] == [m.id for m in before]


async def test_reset_clears_previous_load_error(tmp_path: Path) -> None:
    """``reset()`` clears stale ``_load_error`` (Pi parity per ``_load_models``).

    The Sprint 6f W6 P-175 invariant — `_load_error` reset at the top of
    every ``_load_models`` call — flows through the new alias.
    """

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)

    r._load_error = "stale failure"
    assert r.get_error() == "stale failure"

    r.reset()

    assert r.get_error() is None


async def test_reset_does_not_raise_on_empty_registry(tmp_path: Path) -> None:
    """``reset()`` on a fresh registry does not raise."""

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)
    # MUST NOT raise.
    r.reset()


async def test_refresh_and_reset_have_identical_effect(tmp_path: Path) -> None:
    """Pi parity: ``reset`` is a semantic alias of ``refresh``."""

    s = await _ready_storage(tmp_path)
    r = ModelRegistry.in_memory(s)

    r._models = []
    r.refresh()
    after_refresh = [(m.provider, m.id) for m in r.get_all()]

    r._models = []
    r.reset()
    after_reset = [(m.provider, m.id) for m in r.get_all()]

    assert after_refresh == after_reset
