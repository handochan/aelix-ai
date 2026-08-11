"""#153 — ``/scoped-models`` must not offer, nor silently drop, unrunnable models.

``run_scoped_models`` built its checkbox list straight from ``get_available()``,
which is AUTH-only: with keys held for providers this build cannot run it offered
113 tickable rows, every one unrunnable, while ``/model`` hid all 113. Ticking one
narrows the persisted allow-list that then RESTRICTS ``/model``.

The naive fix — filter ``models`` before ``scoped_model_rows`` — is worse than the
bug, because the saved list is rebuilt from what the picker DISPLAYED: a model
that is merely unrunnable RIGHT NOW would never appear, never be ticked, and be
written OUT of the allow-list by a save the user thought changed nothing. These
tests pin both halves: the filtering AND the non-dropping.

The adapter set is the REAL one (``runtime_bootstrap.register_providers``) rather
than a hand-written api set, so a build that gains a Mistral adapter must make the
"mistral is a dead end" assertions fail rather than keep passing off a constant.

COUNTERFACTUAL, on purpose: 11 of these 13 fail against the pre-fix source, but
:func:`test_enabled_recoverable_model_survives_a_no_change_save` is not one of
them, and that is not a hole. The pre-fix picker filters NOTHING, so it displays
the cloudflare row, ticks it, and saves it — the drop does not exist there. The
implementation that drop-guard exists to reject is the NAIVE one the issue's "Fix"
section describes literally (``partition_runnable`` in front of
``scoped_model_rows``), and against that variant it fails with
``assert [] == ['cfgw/haiku']`` — the whole scoping erased, which
``scoped_available`` then degrades to "no scoping at all". Nine of the thirteen
fail against that variant, including both drop-guards and the all-ticked
canonicalisation.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_ai.api_registry import clear_providers, get_registered_providers
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import Model
from aelix_coding_agent.core import runnable_models as rm
from aelix_coding_agent.tui.scoped_models import run_scoped_models, scoped_model_rows

# Env vars that would make the "needs setup" rows runnable — cleared per test so
# the recoverable branch is genuinely reached.
_CONFIG_ENV = (
    "CF_ACCOUNT_ID",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_GATEWAY_ID",
    "GOOGLE_CLOUD_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch):
    """Register the REAL adapter set for one test, then put the registry back."""

    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
    snapshot = get_registered_providers()
    clear_providers()
    from aelix_coding_agent.cli.runtime_bootstrap import register_providers

    register_providers()
    try:
        yield rm.supported_apis()
    finally:
        clear_providers()
        from aelix_ai.api_registry import register_provider_object

        for provider in snapshot.values():
            register_provider_object(provider)


# ``openai-completions`` IS registered by this build; ``mistral`` never is.
RUNNABLE = Model(id="ok", provider="good", api="openai-completions", base_url="https://api.test/v1")
DEAD_END = Model(id="large", provider="mistral", api="mistral", base_url="https://api.mistral.ai/v1")
RECOVERABLE = Model(
    id="haiku",
    provider="cfgw",
    api="openai-completions",
    base_url="https://gw.test/{CF_ACCOUNT_ID}/v1",
)


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


class _FakeRegistry:
    def __init__(self, models: list[Model]) -> None:
        self._models = models

    def get_available(self) -> list[Model]:
        return list(self._models)


class _Picker:
    """A ``multiselect`` double that records what it was OFFERED, then confirms.

    ``returns`` decides what the user "chose": ``None`` means "confirm the seed
    unchanged" (the do-nothing save that used to erase a scoping).
    """

    def __init__(self, returns: set[str] | None = None) -> None:
        self.options: list[tuple[str, str, str]] = []
        self.selected: set[str] = set()
        self.preview_lines: list[str] = []
        self._returns = returns

    async def __call__(
        self,
        title: str,
        options: list[tuple[str, str, str]],
        *,
        selected: set[str],
        extra_toggles: Any = None,
        preview: Any = None,
    ) -> tuple[set[str], dict[str, bool]]:
        self.options = list(options)
        self.selected = set(selected)
        if preview is not None:
            self.preview_lines = list(preview(set(selected), {}))
        # A real ``multiselect`` returns its internal set, seeded from ``selected``
        # and toggled only by ids it RENDERED. This double is deliberately
        # stricter: it returns ONLY ids it was offered, so the carry-forward is
        # proved at ``run_scoped_models``' own boundary rather than borrowed from
        # the widget's internals (``multiselect`` is dependency-injected — a host
        # is free to return only what it displayed).
        offered = {oid for oid, _, _ in self.options}
        if self._returns is not None:
            return (set(self._returns) & offered, {})
        return (set(selected) & offered, {})

    def labels(self) -> list[str]:
        return [label for _, label, _ in self.options]

    def ids(self) -> set[str]:
        return {oid for oid, _, _ in self.options}


# === the visibility decision =================================================


async def test_dead_end_is_hidden_and_counted_by_reason(real_adapters) -> None:
    """``no-adapter`` cannot be configured away, so it is not a tickable choice."""

    sm = SettingsManager.in_memory({})
    picker = _Picker()
    out: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=out.append,
    )
    assert picker.ids() == {"good/ok"}
    assert "mistral/large" not in picker.ids()
    # The count line names the real reason and the provider behind it — no
    # hardcoded example (that is what ``blocked_summary`` is for).
    line = next(t for t in map(_plain, out) if "hidden" in t)
    assert "1 model(s) hidden" in line
    assert "no adapter in this build" in line
    assert "mistral 1" in line


async def test_recoverable_block_stays_tickable_and_says_why(real_adapters) -> None:
    """One env var away is exactly what a user opens ``/scoped-models`` to declare."""

    sm = SettingsManager.in_memory({})
    picker = _Picker()
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE]),
        settings_manager=sm,
        multiselect=picker,
        commit=lambda _c: None,
    )
    assert picker.ids() == {"good/ok", "cfgw/haiku"}
    row = next(label for label in picker.labels() if label.startswith("[cfgw]"))
    assert "needs setup" in row
    assert "CF_ACCOUNT_ID" in row
    # The bare ``[provider] id`` prefix survives, so the picker's substring filter
    # still finds the row by provider or model id.
    assert row.startswith("[cfgw] haiku")
    # A runnable row is never annotated.
    assert "[good] ok" in picker.labels()


async def test_reason_rides_the_label_not_the_dropped_description(real_adapters) -> None:
    """``multiselect`` destructures the 3rd tuple element as ``_desc`` and drops it.

    An annotation that lived only in the description would be invisible, so the
    "the user can see WHY before ticking" requirement is pinned to the LABEL.
    """

    rows = scoped_model_rows([RECOVERABLE, DEAD_END], apis=real_adapters)
    labels = {oid: label for oid, label, _ in rows}
    assert "needs setup: set CF_ACCOUNT_ID" in labels["cfgw/haiku"]
    assert "unusable: no adapter in this build" in labels["mistral/large"]


async def test_mixed_runnable_and_blocked(real_adapters) -> None:
    """All three classes at once: runnable offered, recoverable offered, dead end hidden."""

    sm = SettingsManager.in_memory({})
    picker = _Picker()
    out: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=out.append,
    )
    assert picker.ids() == {"good/ok", "cfgw/haiku"}
    # Seeded from the ``None`` sentinel → every id in the CATALOG is ticked,
    # including the hidden one (that is what keeps "all" meaning all).
    assert picker.selected == {"good/ok", "cfgw/haiku", "mistral/large"}
    joined = " | ".join(map(_plain, out))
    assert "1 model(s) hidden" in joined
    assert "1 model(s) shown cannot run right now" in joined


async def test_every_model_blocked_dead_end_writes_nothing(real_adapters) -> None:
    """Zero selectable rows must not silently fall through ``multiselect``'s early return."""

    sm = SettingsManager.in_memory({"enabledModels": ["good/ok"]})
    out: list[object] = []

    async def unreachable(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("multiselect must not be called with no options")

    await run_scoped_models(
        registry=_FakeRegistry([DEAD_END]),
        settings_manager=sm,
        multiselect=unreachable,
        commit=out.append,
    )
    assert any("No selectable models" in _plain(c) for c in out)
    assert any("Nothing was changed" in _plain(c) for c in out)
    # The pre-existing scoping is untouched.
    assert sm.get_enabled_models() == ["good/ok"]


# === the trap: nothing enabled may be silently dropped ========================


async def test_enabled_recoverable_model_survives_a_no_change_save(real_adapters) -> None:
    """THE regression this whole change exists to prevent.

    A user scoped a cloudflare-style model, later opens ``/scoped-models`` while
    its env var happens to be unset, and confirms without touching anything. A
    naive runnability filter would omit the row, so it would not be in ``chosen``,
    so the save would delete the scoping — and setting the env var afterwards
    would NOT bring it back.
    """

    sm = SettingsManager.in_memory({"enabledModels": ["cfgw/haiku"]})
    picker = _Picker()
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE]),
        settings_manager=sm,
        multiselect=picker,
        commit=lambda _c: None,
    )
    assert sm.get_enabled_models() == ["cfgw/haiku"]


async def test_enabled_dead_end_stays_visible_and_survives(real_adapters) -> None:
    """An explicitly-enabled dead end is shown (so it can be UN-ticked) and kept.

    Hiding it would leave an entry in the persisted allow-list that the picker
    owning that list cannot remove.
    """

    sm = SettingsManager.in_memory({"enabledModels": ["mistral/large"]})
    picker = _Picker()
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=lambda _c: None,
    )
    assert "mistral/large" in picker.ids()
    assert "unusable" in next(
        label for label in picker.labels() if label.startswith("[mistral]")
    )
    assert sm.get_enabled_models() == ["mistral/large"]


async def test_hidden_dead_end_is_carried_forward_when_user_narrows(real_adapters) -> None:
    """Un-ticking a VISIBLE row must not also delete rows that were never shown.

    Seeded from the "all" sentinel, the hidden dead end was never offered, so it
    cannot have been un-ticked — it keeps the state it was seeded with.
    """

    sm = SettingsManager.in_memory({})
    picker = _Picker(returns={"good/ok"})  # user un-ticks the cloudflare row
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=lambda _c: None,
    )
    assert sm.get_enabled_models() == ["good/ok", "mistral/large"]


# === the canonicalisation must still mean "all" ===============================


async def test_all_ticked_still_collapses_to_none_despite_hidden_rows(
    real_adapters,
) -> None:
    """``all_ids`` is the WHOLE catalog, not the visible subset.

    If ``all_ids`` were rebuilt from the offered rows, a first-run user who
    changed nothing would freeze the open-ended "all" sentinel into a concrete
    list that a later build (or a later env var) could not grow.
    """

    sm = SettingsManager.in_memory({})
    picker = _Picker()
    out: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=out.append,
    )
    assert sm.get_enabled_models() is None
    assert any("all models enabled" in _plain(c) for c in out)
    # The preview counts the whole catalog and says so.
    assert picker.preview_lines[0] == "All models enabled (no scoping)."
    assert "1 unrunnable model(s) not shown" in picker.preview_lines[1]


# === fail-open: unknown is not broken =========================================


async def test_no_registered_adapters_changes_nothing() -> None:
    """No adapters registered (headless/tests) → no annotation, no hiding.

    Same rule as ``partition_runnable``: an empty registered-api set means we
    cannot tell, so we must not over-filter. Deliberately NOT using the
    ``real_adapters`` fixture.
    """

    snapshot = get_registered_providers()
    clear_providers()
    try:
        assert rm.supported_apis() == set()
        sm = SettingsManager.in_memory({})
        picker = _Picker()
        out: list[object] = []
        await run_scoped_models(
            registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
            settings_manager=sm,
            multiselect=picker,
            commit=out.append,
        )
        assert picker.ids() == {"good/ok", "cfgw/haiku", "mistral/large"}
        assert picker.labels() == ["[good] ok", "[cfgw] haiku", "[mistral] large"]
        assert not any("hidden" in _plain(c) for c in out)
        assert not any("cannot run right now" in _plain(c) for c in out)
        assert sm.get_enabled_models() is None
    finally:
        from aelix_ai.api_registry import register_provider_object

        clear_providers()
        for provider in snapshot.values():
            register_provider_object(provider)


# === blocked_summary ==========================================================


def test_blocked_summary_groups_by_reason_then_provider(real_adapters) -> None:
    summary = rm.blocked_summary([DEAD_END, RECOVERABLE, RUNNABLE], real_adapters)
    # Sorted by reason id: config-missing before no-adapter.
    assert summary == "set CF_ACCOUNT_ID (cfgw 1); no adapter in this build (mistral 1)"


def test_blocked_summary_is_empty_without_adapters() -> None:
    assert rm.blocked_summary([DEAD_END, RECOVERABLE], set()) == ""


def test_blocked_summary_is_empty_when_nothing_is_blocked(real_adapters) -> None:
    assert rm.blocked_summary([RUNNABLE], real_adapters) == ""
