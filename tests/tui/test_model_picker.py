"""Unit tests for the /model picker helpers (Sprint 6h₂₆, ADR-0154, WP-7)."""

from __future__ import annotations

from typing import Any

from aelix_ai.streaming import Model
from aelix_coding_agent.tui.model_picker import (
    model_detail_lines,
    model_picker_labels,
    run_model_picker,
)


def test_labels_numbered_with_provider_tag() -> None:
    models = [
        Model(id="z-ai/glm-4.5-air:free", provider="openrouter"),
        Model(id="openai/gpt-oss-120b:free", provider="openrouter"),
    ]
    labels = model_picker_labels(models)
    assert labels == [
        "1. [openrouter] z-ai/glm-4.5-air:free",
        "2. [openrouter] openai/gpt-oss-120b:free",
    ]


def test_labels_mark_current_model_with_star() -> None:
    models = [Model(id="a", provider="p"), Model(id="b", provider="p")]
    labels = model_picker_labels(models, current_id="b", current_provider="p")
    assert labels[0] == "1. [p] a"
    assert labels[1] == "✱ 2. [p] b"


def test_labels_current_requires_provider_match() -> None:
    # Same id under two providers — only the (id, provider) pair matches.
    models = [Model(id="a", provider="p1"), Model(id="a", provider="p2")]
    labels = model_picker_labels(models, current_id="a", current_provider="p2")
    assert not labels[0].startswith("✱")
    assert labels[1].startswith("✱")


def test_labels_unique_so_index_recovery_is_lossless() -> None:
    # The "N." prefix guarantees uniqueness even for duplicate ids, so
    # labels.index(choice) round-trips to the right Model.
    models = [Model(id="dup", provider="p"), Model(id="dup", provider="p")]
    labels = model_picker_labels(models)
    assert labels[0] != labels[1]
    assert len(set(labels)) == 2


def test_detail_lines_text_only_and_formatting() -> None:
    model = Model(
        id="x",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        input=["text"],
        context_window=131072,
    )
    lines = model_detail_lines(model)
    assert lines[0].startswith("─")  # divider first
    assert "Modality:       text-only" in lines
    assert "Context Window: 131,072 tokens" in lines
    assert "Base URL:       https://openrouter.ai/api/v1" in lines
    assert "API Key:        OPENROUTER_API_KEY" in lines


def test_detail_lines_multimodal_and_defaults() -> None:
    model = Model(id="x", provider="nope", input=["text", "image"], context_window=0, base_url="")
    lines = model_detail_lines(model)
    assert "Modality:       text, image" in lines
    assert "Context Window: unknown" in lines
    assert "Base URL:       (provider default)" in lines
    # Unknown provider has no env mapping → em dash, never a fabricated var name.
    assert "API Key:        —" in lines


def test_detail_lines_empty_input_is_unknown_modality() -> None:
    lines = model_detail_lines(Model(id="x", provider="p", input=[]))
    assert "Modality:       unknown" in lines


# ── run_model_picker flow (W-review 6h₂₆ HIGH: the picker flow itself) ──────────


class _FakeRegistry:
    def __init__(self, models: list[Model], *, raise_exc: bool = False) -> None:
        self._models = models
        self._raise = raise_exc

    def get_available(self) -> list[Model]:
        if self._raise:
            raise RuntimeError("boom")
        return self._models


class _FakeHarness:
    def __init__(self, current: Model | None = None, *, fail_set: bool = False) -> None:
        self.current_model = current
        self.set_calls: list[object] = []
        self._fail = fail_set

    async def set_model(self, model: object) -> None:
        if self._fail:
            raise RuntimeError("switch failed")
        self.set_calls.append(model)
        self.current_model = model  # type: ignore[assignment]


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


async def _select_unreachable(*_a: Any, **_k: Any) -> str | None:
    raise AssertionError("select must not be called on this path")


async def test_run_model_picker_switches_to_selected_model() -> None:
    models = [Model(id="a", provider="p"), Model(id="b", provider="p", input=["text"])]
    harness = _FakeHarness()
    committed: list[object] = []
    refreshed: list[int] = []
    captured: dict[str, Any] = {}

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        captured["options"] = options
        if detail is not None:
            captured["detail1"] = detail(1)  # exercise the per-highlight callback
        return options[1]  # choose the 2nd row

    await run_model_picker(
        registry=_FakeRegistry(models),
        harness=harness,
        select=select,
        commit=committed.append,
        refresh_footer=lambda: refreshed.append(1),
    )
    assert harness.set_calls == [models[1]]
    assert any("model →" in _plain(c) for c in committed)
    assert refreshed == [1]
    # detail(1) returned the SECOND model's lines (index maps to models order).
    assert any("Modality" in line for line in captured["detail1"])


async def test_run_model_picker_no_registry_is_unavailable() -> None:
    committed: list[object] = []
    await run_model_picker(
        registry=None, harness=_FakeHarness(), select=_select_unreachable, commit=committed.append
    )
    assert any("unavailable" in _plain(c) for c in committed)


async def test_run_model_picker_empty_catalog_hints_auth() -> None:
    committed: list[object] = []
    await run_model_picker(
        registry=_FakeRegistry([]),
        harness=_FakeHarness(),
        select=_select_unreachable,
        commit=committed.append,
    )
    assert any("No models available" in _plain(c) for c in committed)


async def test_run_model_picker_cancel_does_not_switch() -> None:
    models = [Model(id="a", provider="p")]
    harness = _FakeHarness()
    committed: list[object] = []

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        return None  # user pressed Esc

    await run_model_picker(
        registry=_FakeRegistry(models), harness=harness, select=select, commit=committed.append
    )
    assert harness.set_calls == []


async def test_run_model_picker_list_failure_is_surfaced() -> None:
    committed: list[object] = []
    await run_model_picker(
        registry=_FakeRegistry([], raise_exc=True),
        harness=_FakeHarness(),
        select=_select_unreachable,
        commit=committed.append,
    )
    assert any("model list failed" in _plain(c) for c in committed)


async def test_run_model_picker_switch_failure_is_surfaced() -> None:
    models = [Model(id="a", provider="p")]
    harness = _FakeHarness(fail_set=True)
    committed: list[object] = []

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        return options[0]

    await run_model_picker(
        registry=_FakeRegistry(models), harness=harness, select=select, commit=committed.append
    )
    assert harness.set_calls == []
    assert any("model switch failed" in _plain(c) for c in committed)


async def test_run_model_picker_selecting_current_model_round_trips() -> None:
    # The ✱-marked current row must still recover the right Model via labels.index.
    models = [Model(id="a", provider="p"), Model(id="b", provider="p")]
    harness = _FakeHarness(current=models[1])  # 2nd is current → gets the ✱ marker
    committed: list[object] = []

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        assert options[1].startswith("✱ ")  # current model is marked
        return options[1]  # re-select the (marked) current row

    await run_model_picker(
        registry=_FakeRegistry(models), harness=harness, select=select, commit=committed.append
    )
    assert harness.set_calls == [models[1]]


# ── enabled_models enforcement (ADR-0162) ──────────────────────────────────────


class _FakeSettings:
    """SettingsManager double exposing only ``get_enabled_models`` (read live)."""

    def __init__(self, patterns: list[str] | None) -> None:
        self._patterns = patterns

    def get_enabled_models(self) -> list[str] | None:
        return None if self._patterns is None else list(self._patterns)


async def test_run_model_picker_scopes_to_enabled_allow_list() -> None:
    # 3-model catalog, allow-list of 2 → only the 2 enabled rows are offered;
    # the ✱ current marker still applies WITHIN the scoped subset.
    models = [
        Model(id="a", provider="p"),
        Model(id="b", provider="p"),
        Model(id="c", provider="p"),
    ]
    harness = _FakeHarness(current=models[1])  # "b" is current
    committed: list[object] = []
    seen: dict[str, Any] = {}

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        seen["options"] = options
        return options[0]  # pick the first offered (scoped) row

    await run_model_picker(
        registry=_FakeRegistry(models),
        harness=harness,
        select=select,
        commit=committed.append,
        settings_manager=_FakeSettings(["p/a", "p/b"]),
    )
    # Only the 2 enabled rows ("a", "b") are offered — "c" is filtered out.
    assert len(seen["options"]) == 2
    assert all("[p] c" not in o for o in seen["options"])
    # "b" (current) keeps its ✱ marker within the scoped subset.
    assert any(o.startswith("✱ ") and "[p] b" in o for o in seen["options"])
    # Selecting the first scoped row switched to "a".
    assert harness.set_calls == [models[0]]


async def test_run_model_picker_empty_match_degrades_to_all_with_warning() -> None:
    # A concrete allow-list matching ZERO available models must NOT lock the
    # picker out — it shows all rows and commits a warning line.
    models = [Model(id="a", provider="p"), Model(id="b", provider="p")]
    harness = _FakeHarness()
    committed: list[object] = []
    seen: dict[str, Any] = {}

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        seen["options"] = options
        return None  # Esc after seeing the full list

    await run_model_picker(
        registry=_FakeRegistry(models),
        harness=harness,
        select=select,
        commit=committed.append,
        settings_manager=_FakeSettings(["ghost/missing"]),
    )
    assert len(seen["options"]) == 2  # full list, no lockout
    assert any("matched no available models" in _plain(c) for c in committed)


async def test_run_model_picker_none_settings_shows_full_list() -> None:
    # settings_manager omitted/None → unchanged behaviour (full auth list).
    models = [Model(id="a", provider="p"), Model(id="b", provider="p")]
    harness = _FakeHarness()
    seen: dict[str, Any] = {}

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        seen["options"] = options
        return None

    await run_model_picker(
        registry=_FakeRegistry(models),
        harness=harness,
        select=select,
        commit=lambda _c: None,
        settings_manager=None,
    )
    assert len(seen["options"]) == 2


# ── /model persistence (pi parity: setModel → setDefaultModelAndProvider) ───────


class _PersistSettings(_FakeSettings):
    """Settings double that also records the default-model persist + flush."""

    def __init__(self, patterns: list[str] | None = None) -> None:
        super().__init__(patterns)
        self.persisted: list[tuple[str, str]] = []
        self.flushes = 0

    def set_default_model_and_provider(self, provider: str, model_id: str) -> None:
        self.persisted.append((provider, model_id))

    async def flush(self) -> None:
        self.flushes += 1


async def test_run_model_picker_persists_default_on_switch() -> None:
    # Selecting a model persists it as the default (provider, id) + flushes, so
    # the pick survives restart / /new — matching /settings → Default model.
    models = [Model(id="a", provider="p"), Model(id="b", provider="q")]
    harness = _FakeHarness()
    settings = _PersistSettings()

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        return options[1]  # choose "b" under provider "q"

    await run_model_picker(
        registry=_FakeRegistry(models),
        harness=harness,
        select=select,
        commit=lambda _c: None,
        settings_manager=settings,
    )
    assert harness.set_calls == [models[1]]
    assert settings.persisted == [("q", "b")]  # provider-qualified identity
    assert settings.flushes == 1


async def test_run_model_picker_cancel_does_not_persist() -> None:
    # Esc → no switch AND no persist (the picker returns before set_model).
    models = [Model(id="a", provider="p")]
    harness = _FakeHarness()
    settings = _PersistSettings()

    async def select(
        title: str, options: list[str], detail: Any = None
    ) -> str | None:
        return None

    await run_model_picker(
        registry=_FakeRegistry(models),
        harness=harness,
        select=select,
        commit=lambda _c: None,
        settings_manager=settings,
    )
    assert harness.set_calls == []
    assert settings.persisted == []
    assert settings.flushes == 0
