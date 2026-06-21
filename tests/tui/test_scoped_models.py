"""ImplConsumers (ADR-0161) — unit tests for the /scoped-models flow.

Drives :func:`run_scoped_models` with a fake registry + an in-memory
:class:`SettingsManager` + a fake multiselect. Covers: all-checked →
``set_enabled_models(None)`` (canonical "all"); a subset → ``sorted(ids)``; the
read-back round-trip confirmation; Esc → no write; stale-id pruning when seeding
from a persisted list; and the degrade paths (no registry / no settings manager /
empty catalog) that commit a yellow message and never raise.
"""

from __future__ import annotations

from typing import Any

from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import Model
from aelix_coding_agent.tui.scoped_models import run_scoped_models, scoped_model_rows


class _FakeRegistry:
    def __init__(self, models: list[Model], *, raise_exc: bool = False) -> None:
        self._models = models
        self._raise = raise_exc

    def get_available(self) -> list[Model]:
        if self._raise:
            raise RuntimeError("boom")
        return self._models


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


_MODELS = [
    Model(id="a", provider="p"),
    Model(id="b", provider="p"),
    Model(id="c", provider="p"),
]


async def _select_unreachable(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("multiselect must not be called on this path")


def test_scoped_model_rows_uses_model_id_as_stable_key() -> None:
    rows = scoped_model_rows(_MODELS)
    assert [oid for oid, _, _ in rows] == ["a", "b", "c"]
    # The label is the clean ``[provider] id`` form.
    assert all("[p]" in label for _, label, _ in rows)
    assert all("provider: p" in desc for _, _, desc in rows)


def test_scoped_model_rows_have_no_numeric_picker_prefix() -> None:
    # The single-choice /model picker prefixes labels with a numeric "N." counter;
    # the checkbox /scoped-models list must NOT (no numeric selection here).
    rows = scoped_model_rows(_MODELS)
    labels = [label for _, label, _ in rows]
    assert labels == ["[p] a", "[p] b", "[p] c"]
    assert not any(label.lstrip().startswith(("1.", "2.", "3.")) for label in labels)


async def test_all_checked_writes_none_sentinel() -> None:
    sm = SettingsManager.in_memory({})
    committed: list[object] = []

    async def ms(title, options, *, selected, extra_toggles=None, preview=None):  # noqa: ANN001
        return ({"a", "b", "c"}, {})

    await run_scoped_models(
        registry=_FakeRegistry(_MODELS),
        settings_manager=sm,
        multiselect=ms,
        commit=committed.append,
    )
    assert sm.get_enabled_models() is None  # canonical "all enabled"
    assert any("all models enabled" in _plain(c) for c in committed)


async def test_subset_writes_sorted_ids() -> None:
    sm = SettingsManager.in_memory({})
    committed: list[object] = []

    async def ms(title, options, *, selected, extra_toggles=None, preview=None):  # noqa: ANN001
        return ({"c", "a"}, {})  # unsorted on purpose

    await run_scoped_models(
        registry=_FakeRegistry(_MODELS),
        settings_manager=sm,
        multiselect=ms,
        commit=committed.append,
    )
    assert sm.get_enabled_models() == ["a", "c"]  # sorted
    assert any("2 model" in _plain(c) for c in committed)
    # ENFORCED (ADR-0162): the confirmation now states the active effect — the
    # allow-list restricts /model. The old "enforcement pending" phrasing is gone.
    assert any("/model now restricted" in _plain(c) for c in committed)
    assert not any("enforcement pending" in _plain(c) for c in committed)


async def test_seed_is_not_scoped_disabled_model_stays_visible() -> None:
    # REGRESSION GUARD (ADR-0162): the /scoped-models picker seeds from the FULL
    # auth-filtered catalog (NOT scoped_available), so a model that is currently
    # DISABLED via the allow-list is still listed + re-checkable. Scoping the seed
    # would make a disabled model invisible and permanently un-re-enableable.
    sm = SettingsManager.in_memory({"enabledModels": ["a"]})  # only "a" enabled
    captured: dict[str, Any] = {}

    async def ms(title, options, *, selected, extra_toggles=None, preview=None):  # noqa: ANN001
        captured["option_ids"] = [oid for oid, _, _ in options]
        captured["selected"] = set(selected)
        return None  # Esc — just inspect the seed, no write

    await run_scoped_models(
        registry=_FakeRegistry(_MODELS),
        settings_manager=sm,
        multiselect=ms,
        commit=lambda c: None,
    )
    # ALL catalog ids are offered (the disabled "b"/"c" are still visible).
    assert captured["option_ids"] == ["a", "b", "c"]
    # Only the enabled model is pre-checked.
    assert captured["selected"] == {"a"}


async def test_seed_prunes_stale_ids() -> None:
    # A persisted enabled list with an id no longer in the catalog must NOT seed a
    # phantom checkbox — the seeded selection intersects the live catalog.
    sm = SettingsManager.in_memory({"enabledModels": ["a", "gone"]})
    captured: dict[str, Any] = {}

    async def ms(title, options, *, selected, extra_toggles=None, preview=None):  # noqa: ANN001
        captured["selected"] = set(selected)
        if preview is not None:
            captured["preview"] = preview(set(selected), {})
        return (set(selected), {})

    await run_scoped_models(
        registry=_FakeRegistry(_MODELS),
        settings_manager=sm,
        multiselect=ms,
        commit=lambda c: None,
    )
    assert captured["selected"] == {"a"}  # "gone" pruned


async def test_cancel_does_not_write() -> None:
    sm = SettingsManager.in_memory({"enabledModels": ["a"]})

    async def ms(title, options, *, selected, extra_toggles=None, preview=None):  # noqa: ANN001
        return None  # Esc

    await run_scoped_models(
        registry=_FakeRegistry(_MODELS),
        settings_manager=sm,
        multiselect=ms,
        commit=lambda c: None,
    )
    assert sm.get_enabled_models() == ["a"]  # unchanged


async def test_no_registry_degrades() -> None:
    committed: list[object] = []
    await run_scoped_models(
        registry=None,
        settings_manager=SettingsManager.in_memory({}),
        multiselect=_select_unreachable,
        commit=committed.append,
    )
    assert any("no model registry" in _plain(c).lower() for c in committed)


async def test_no_settings_manager_degrades() -> None:
    committed: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry(_MODELS),
        settings_manager=None,
        multiselect=_select_unreachable,
        commit=committed.append,
    )
    assert any("no settings manager" in _plain(c).lower() for c in committed)


async def test_empty_catalog_degrades_with_hint() -> None:
    committed: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry([]),
        settings_manager=SettingsManager.in_memory({}),
        multiselect=_select_unreachable,
        commit=committed.append,
    )
    assert any("No models available" in _plain(c) for c in committed)


async def test_list_failure_is_surfaced() -> None:
    committed: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry([], raise_exc=True),
        settings_manager=SettingsManager.in_memory({}),
        multiselect=_select_unreachable,
        commit=committed.append,
    )
    assert any("model list failed" in _plain(c) for c in committed)
