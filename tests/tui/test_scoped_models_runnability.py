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

COUNTERFACTUAL, on purpose: 11 of these fail against the pre-fix source, but
:func:`test_enabled_recoverable_model_survives_a_no_change_save` is not one of
them, and that is not a hole. The pre-fix picker filters NOTHING, so it displays
the cloudflare row, ticks it, and saves it — the drop does not exist there. The
implementation that drop-guard exists to reject is the NAIVE one the issue's "Fix"
section describes literally (``partition_runnable`` in front of
``scoped_model_rows``), and against that variant it fails with
``assert [] == ['cfgw/haiku']`` — the whole scoping erased, which
``scoped_available`` then degrades to "no scoping at all". Nine of them fail
against that variant, including both drop-guards and the all-ticked
canonicalisation.

ROUND 2 pins three more things, all of which the FIRST fix got wrong:

* the hiding must survive the user's first narrowing save. Seeding the checkbox
  set from the whole catalog handed every HIDDEN id straight back, because
  ``multiselect`` starts from ``set(selected)`` and only toggles RENDERED rows —
  so one Space + Enter from an absent settings.json persisted 138 ids, 70 of them
  unrunnable, and re-opening showed them again (now as an explicit list). The
  seeding tests therefore drive the REAL widget, not the double: a double that
  returns only what it rendered cannot see this bug.
* "all models enabled" must stay REACHABLE. Testing the collapse against the
  whole catalog made the ``None`` sentinel unreachable through a UI that cannot
  tick a hidden row, pinning the allow-list to today's ids forever.
* a row's verdict must be READABLE at 80 columns. ``multiselect`` clips a row
  instead of wrapping it, so the env var names moved off the label (70 of 139
  real rows overflowed) into one grouped line.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aelix_ai.api_registry import clear_providers, get_registered_providers
from aelix_ai.settings import SettingsManager
from aelix_ai.streaming import Model
from aelix_coding_agent.core import runnable_models as rm
from aelix_coding_agent.tui import scoped_models as sm_mod
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.context import AelixTUIContext
from aelix_coding_agent.tui.footer_data import AelixFooterData
from aelix_coding_agent.tui.scoped_models import run_scoped_models, scoped_model_rows
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.base import PipeInput
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

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

# The REAL catalog's widest row (measured), and the provider that shares its
# reason but needs FEWER variables. Copied verbatim from the bundled catalog so
# the width + grouping assertions are about a row a user really sees.
_WIDEST_REAL_ROW = Model(
    id="workers-ai/@cf/nvidia/nemotron-3-120b-a12b",
    provider="cloudflare-ai-gateway",
    api="openai-completions",
    base_url=(
        "https://gateway.ai.cloudflare.com/v1/{CLOUDFLARE_ACCOUNT_ID}"
        "/{CLOUDFLARE_GATEWAY_ID}/openai"
    ),
)
_GATEWAY_ROW = Model(
    id="claude-3-5-haiku",
    provider="cloudflare-ai-gateway",
    api="openai-completions",
    base_url=(
        "https://gateway.ai.cloudflare.com/v1/{CLOUDFLARE_ACCOUNT_ID}"
        "/{CLOUDFLARE_GATEWAY_ID}/openai"
    ),
)
_WORKERS_ROW = Model(
    id="@cf/meta/llama-4-scout-17b-16e-instruct",
    provider="cloudflare-workers-ai",
    api="openai-completions",
    base_url=(
        "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1"
    ),
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
    out: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE]),
        settings_manager=sm,
        multiselect=picker,
        commit=out.append,
    )
    assert picker.ids() == {"good/ok", "cfgw/haiku"}
    row = next(label for label in picker.labels() if label.startswith("[cfgw]"))
    assert row == "[cfgw] haiku  (needs setup)"
    # The variable name is named ONCE, in the grouped line under the list — the
    # row cannot carry it (see ``test_annotated_rows_fit_the_picker_frame``).
    line = next(t for t in map(_plain, out) if "cannot run right now" in t)
    assert "set CF_ACCOUNT_ID" in line
    assert "cfgw 1" in line
    # A runnable row is never annotated.
    assert "[good] ok" in picker.labels()


async def test_reason_rides_the_label_not_the_dropped_description(real_adapters) -> None:
    """``multiselect`` destructures the 3rd tuple element as ``_desc`` and drops it.

    An annotation that lived only in the description would be invisible, so the
    "the user can see the verdict before ticking" requirement is pinned to the
    LABEL. Wiring a per-row detail panel through that shared widget was
    considered and rejected for #153 (``/statusline`` uses the same widget), so
    the label carries the verdict and the grouped line carries the reasons.
    """

    rows = scoped_model_rows([RECOVERABLE, DEAD_END], apis=real_adapters)
    labels = {oid: label for oid, label, _ in rows}
    assert labels["cfgw/haiku"] == "[cfgw] haiku  (needs setup)"
    assert labels["mistral/large"] == "[mistral] large  (unusable)"


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
    # Seeded from the ``None`` sentinel → the OFFERED rows only. Seeding the
    # hidden id here is what let one keystroke write it to disk (round 2 HIGH).
    assert picker.selected == {"good/ok", "cfgw/haiku"}
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


async def test_hidden_dead_end_is_not_written_when_user_narrows(real_adapters) -> None:
    """ROUND 2 HIGH — a narrowing save must not persist ids the user never saw.

    Under the ``None`` sentinel there is no persisted list to protect, so the
    seed is the OFFERED rows. The first cut seeded the whole catalog, and since
    ``multiselect`` returns everything it was seeded with, un-ticking ONE visible
    row wrote the hidden dead end into the user's allow-list — where the explicit
    carve-out then made it visible and pre-ticked again on the next open, undoing
    the entire fix in one keystroke.
    """

    sm = SettingsManager.in_memory({})
    picker = _Picker(returns={"good/ok"})  # user un-ticks the cloudflare row
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=lambda _c: None,
    )
    assert sm.get_enabled_models() == ["good/ok"]


async def test_explicit_seed_is_always_offered_so_nothing_can_be_dropped(
    real_adapters,
) -> None:
    """The anti-drop rule, stated as the invariant that makes it hold.

    Round 2 narrowed the seed; the guarantee survives because every seeded id is
    OFFERED — a recoverable row is shown by class, and a seeded dead end is shown
    by the carve-out. If a future visibility rule breaks that pairing, the
    carry-forward at the save is the second line of defence, and this assertion
    is what tells a reader which one is doing the work.
    """

    sm = SettingsManager.in_memory({"enabledModels": ["mistral/large", "cfgw/haiku"]})
    picker = _Picker()
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=lambda _c: None,
    )
    assert picker.selected <= picker.ids()
    assert picker.selected == {"mistral/large", "cfgw/haiku"}
    assert sm.get_enabled_models() == ["cfgw/haiku", "mistral/large"]


# === the canonicalisation must still mean "all", and stay REACHABLE ===========


async def test_all_ticked_still_collapses_to_none_despite_hidden_rows(
    real_adapters,
) -> None:
    """A first-run user who changes nothing writes no ``enabledModels`` key.

    The open-ended "all" sentinel must not be frozen into a concrete list that a
    later build (or a later env var) cannot grow.
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
    # The collapse branch persists NO allow-list, so the hidden rows are covered
    # by it — a "not in this selection" note here would contradict the line above.
    assert picker.preview_lines == ["All models enabled (no scoping)."]


async def test_all_models_stays_reachable_when_a_dead_end_is_hidden(
    real_adapters,
) -> None:
    """ROUND 2 MEDIUM — ticking everything the picker CAN offer means "all".

    Measured against the whole catalog, the sentinel was unreachable the moment
    one dead end was hidden: the user ticks every row the picker has and still
    falls short by the untickable ones, and ``multiselect`` has no enable-all
    affordance to escape with. The user is left pinned to a concrete id list.
    """

    sm = SettingsManager.in_memory({"enabledModels": ["good/ok"]})
    picker = _Picker(returns={"good/ok", "cfgw/haiku"})  # every VISIBLE row
    out: list[object] = []
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=out.append,
    )
    assert picker.ids() == {"good/ok", "cfgw/haiku"}  # the dead end is hidden
    assert sm.get_enabled_models() is None
    assert any("all models enabled" in _plain(c) for c in out)


async def test_narrowing_preview_counts_the_catalog_and_flags_the_hidden_rows(
    real_adapters,
) -> None:
    """The other preview branch: a real narrowing says what it leaves out."""

    sm = SettingsManager.in_memory({"enabledModels": ["good/ok"]})
    picker = _Picker()
    await run_scoped_models(
        registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
        settings_manager=sm,
        multiselect=picker,
        commit=lambda _c: None,
    )
    # Denominator = the whole catalog (3), not the 2 rows drawn today.
    assert picker.preview_lines[0] == "1 of 3 models enabled."
    assert picker.preview_lines[1] == (
        "1 unrunnable model(s) not shown, and not in this selection."
    )


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


def test_blocked_summary_groups_by_hint_not_only_by_reason(real_adapters) -> None:
    """ROUND 2 LOW — a provider must not be listed under variables it does not use.

    Both rows are blocked for ``config-missing``, but the gateway templates TWO
    variables and workers-ai templates one. Grouping on the reason alone unioned
    them, so the line told a ``cloudflare-workers-ai`` user to set
    ``CLOUDFLARE_GATEWAY_ID`` — never wrong (the union does fix the group) but
    over-broad, and it disagreed with the precise per-row hints. It matters more
    now that this line is the only place the names appear.
    """

    summary = rm.blocked_summary([_GATEWAY_ROW, _WORKERS_ROW], real_adapters)
    assert summary == (
        "set CLOUDFLARE_ACCOUNT_ID (cloudflare-workers-ai 1); "
        "set CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_GATEWAY_ID (cloudflare-ai-gateway 1)"
    )


# === the verdict has to be READABLE at 80 columns =============================


def _rendered_width(label: str) -> int:
    """Columns ``multiselect`` actually draws for an option row.

    The widget composes ``"▸ " | "  "`` + ``"[✓] "`` + the label, and sizes its
    frame from ``_visible_len(row) + 2``. It never wraps or truncates, so
    anything past the frame is clipped by the terminal.
    """

    from aelix_coding_agent.tui.context import _visible_len

    return _visible_len(f"[✓] {label}") + 2


def test_label_budget_is_pinned_to_the_widget_frame() -> None:
    """The budget is derived from the widget's own constant, not guessed."""

    from aelix_coding_agent.tui import context as ctx_mod

    assert sm_mod._ROW_CHROME == len("▸ ") + len("[✓] ")
    assert sm_mod._LABEL_BUDGET == ctx_mod._PICK_MAX_WIDTH - sm_mod._ROW_CHROME


def test_annotated_rows_fit_the_picker_frame(real_adapters) -> None:
    """ROUND 2 MEDIUM — the widest REAL catalog row, annotated, at 78 columns.

    With the env var names inline this row rendered 137 columns and was clipped
    at ``(needs setup: set`` on an 80-column terminal — the second variable name
    was never visible, and 70 of the catalog's 139 rows were over the cap.
    """

    from aelix_coding_agent.tui import context as ctx_mod

    rows = scoped_model_rows([_WIDEST_REAL_ROW, _WORKERS_ROW], apis=real_adapters)
    for _oid, label, _desc in rows:
        assert _rendered_width(label) <= ctx_mod._PICK_MAX_WIDTH, label
    widest = rows[0][1]
    assert widest.endswith("  (needs setup)")
    # Middle-ellipsised: the provider tag AND the distinguishing id tail survive.
    assert widest.startswith("[cloudflare-ai-gateway] ")
    assert "…" in widest
    assert "nemotron-3-120b-a12b" in widest
    # The row's KEY is never truncated, so the picker's substring filter (which
    # matches label OR id) still finds it by the full model id.
    assert rows[0][0] == (
        "cloudflare-ai-gateway/workers-ai/@cf/nvidia/nemotron-3-120b-a12b"
    )


def test_short_rows_are_left_alone(real_adapters) -> None:
    """Fitting must not touch a row that already fits (no gratuitous churn)."""

    rows = scoped_model_rows([RUNNABLE, RECOVERABLE], apis=real_adapters)
    assert [label for _, label, _ in rows] == [
        "[good] ok",
        "[cfgw] haiku  (needs setup)",
    ]


# === the REAL widget, for the seeding claims ==================================


@asynccontextmanager
async def _live_ctx() -> AsyncGenerator[tuple[AelixTUIContext, AelixChrome, PipeInput]]:
    """The real ``multiselect``, driven by pipe input (``test_multiselect`` harness)."""

    with create_pipe_input() as pipe, create_app_session(
        input=pipe, output=DummyOutput()
    ):
        console = Console(file=io.StringIO(), force_terminal=True, width=80)
        chrome = AelixChrome(console=console)
        ctx = AelixTUIContext(chrome, AelixFooterData(cwd="."))
        task = asyncio.create_task(chrome.run())
        try:
            yield ctx, chrome, pipe
        finally:
            chrome.exit()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=3)


async def _wait_modal(chrome: AelixChrome, *, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if chrome.is_modal_open():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("modal not mounted")


async def test_real_widget_one_space_enter_writes_no_hidden_id(real_adapters) -> None:
    """ROUND 2 HIGH, against the REAL widget — the double cannot see this bug.

    ``multiselect`` seeds its internal set from ``selected`` and ``_toggle``
    only ever flips a RENDERED row, so every seeded id comes back in ``chosen``
    whether or not it was ever drawn. A double that returns only what it
    rendered hides exactly that. From an absent settings.json, ONE Space + Enter
    must not persist a model the picker refused to show.
    """

    sm = SettingsManager.in_memory({})
    async with _live_ctx() as (ctx, chrome, pipe):
        run = asyncio.ensure_future(
            run_scoped_models(
                registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
                settings_manager=sm,
                multiselect=ctx.multiselect,
                commit=lambda _c: None,
            )
        )
        await _wait_modal(chrome)
        pipe.send_text(" ")  # un-tick the highlighted row ([good] ok)
        pipe.send_text("\r")  # confirm
        await asyncio.wait_for(run, timeout=5)
    saved = sm.get_enabled_models()
    assert saved == ["cfgw/haiku"]
    assert saved is not None and "mistral/large" not in saved


async def test_real_widget_all_visible_ticked_reaches_the_all_sentinel(
    real_adapters,
) -> None:
    """ROUND 2 MEDIUM, against the REAL widget — the escape hatch really works."""

    sm = SettingsManager.in_memory({"enabledModels": ["good/ok"]})
    async with _live_ctx() as (ctx, chrome, pipe):
        run = asyncio.ensure_future(
            run_scoped_models(
                registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
                settings_manager=sm,
                multiselect=ctx.multiselect,
                commit=lambda _c: None,
            )
        )
        await _wait_modal(chrome)
        pipe.send_text("\x1b[B")  # down → the second (and last) visible row
        pipe.send_text(" ")  # tick it: every VISIBLE row is now ticked
        pipe.send_text("\r")
        await asyncio.wait_for(run, timeout=5)
    assert sm.get_enabled_models() is None


async def test_real_widget_explicit_scoping_survives_a_no_change_save(
    real_adapters,
) -> None:
    """The anti-drop guarantee, re-proved through the widget that returns the seed."""

    sm = SettingsManager.in_memory({"enabledModels": ["cfgw/haiku"]})
    async with _live_ctx() as (ctx, chrome, pipe):
        run = asyncio.ensure_future(
            run_scoped_models(
                registry=_FakeRegistry([RUNNABLE, RECOVERABLE, DEAD_END]),
                settings_manager=sm,
                multiselect=ctx.multiselect,
                commit=lambda _c: None,
            )
        )
        await _wait_modal(chrome)
        pipe.send_text("\r")  # confirm, touching nothing
        await asyncio.wait_for(run, timeout=5)
    assert sm.get_enabled_models() == ["cfgw/haiku"]


def test_blocked_summary_is_empty_without_adapters() -> None:
    assert rm.blocked_summary([DEAD_END, RECOVERABLE], set()) == ""


def test_blocked_summary_is_empty_when_nothing_is_blocked(real_adapters) -> None:
    assert rm.blocked_summary([RUNNABLE], real_adapters) == ""
