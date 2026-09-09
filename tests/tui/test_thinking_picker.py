"""Unit tests for the /thinking picker helpers (Sprint 6h₂₇, ADR-0155, WP-7)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from aelix_coding_agent.tui.thinking_picker import (
    run_thinking_picker,
    thinking_level_display,
    thinking_picker_labels,
)


def _plain(renderable: object) -> str:
    return getattr(renderable, "plain", str(renderable))


# === thinking_picker_labels (PURE) =========================================


def test_labels_numbered_and_mark_current() -> None:
    labels = thinking_picker_labels(["off", "low", "high"], "low")
    assert labels == ["1. off", "✱ 2. low", "3. high"]
    # The "N." prefix keeps labels unique → lossless index recovery.
    assert len(set(labels)) == 3


def test_labels_no_current_match_marks_nothing() -> None:
    labels = thinking_picker_labels(["off", "high"], "medium")
    assert not any(label.startswith("✱") for label in labels)


# === run_thinking_picker flow (DI) =========================================


class _Model:
    def __init__(
        self, *, reasoning: bool = True, thinking_level_map: dict | None = None
    ) -> None:
        self.reasoning = reasoning
        # get_supported_thinking_levels iterates EXTENDED_THINKING_LEVELS and
        # consults this map; {} → all of off/minimal/low/medium/high (xhigh is
        # excluded when its key is absent — Pi parity).
        self.thinking_level_map = thinking_level_map if thinking_level_map is not None else {}


class _State:
    def __init__(self, thinking_level: str | None) -> None:
        self.thinking_level = thinking_level


class _Harness:
    def __init__(
        self,
        *,
        model: _Model | None,
        level: str | None = None,
        fail_set: bool = False,
    ) -> None:
        self.current_model = model
        self._state = _State(level)
        self.set_calls: list[str] = []
        self._fail = fail_set

    async def set_thinking_level(self, level: str) -> None:
        if self._fail:
            raise RuntimeError("switch failed")
        self.set_calls.append(level)
        self._state.thinking_level = level


async def _select_unreachable(*_a: Any, **_k: Any) -> str | None:
    raise AssertionError("select must not be called on this path")


async def test_run_switches_to_selected_level() -> None:
    harness = _Harness(model=_Model(), level="low")
    committed: list[object] = []
    captured: dict[str, Any] = {}

    async def select(title: str, options: list[str]) -> str | None:
        captured["options"] = options
        # choose the "high" row (last in the off/low/medium/high set)
        return next(o for o in options if o.endswith("high"))

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert harness.set_calls == ["high"]
    assert any("thinking →" in _plain(c) for c in committed)
    # the current level (low) is marked in the offered options.
    assert any(o.startswith("✱") and o.endswith("low") for o in captured["options"])


async def test_run_cancel_does_not_switch() -> None:
    harness = _Harness(model=_Model(), level="low")
    committed: list[object] = []

    async def select(title: str, options: list[str]) -> str | None:
        return None  # user pressed Esc

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert harness.set_calls == []
    assert committed == []  # no error commit on a clean cancel


async def test_run_non_reasoning_model_degrades() -> None:
    harness = _Harness(model=_Model(reasoning=False))
    committed: list[object] = []
    await run_thinking_picker(
        harness=harness, select=_select_unreachable, commit=committed.append
    )
    assert harness.set_calls == []
    assert any("no thinking levels" in _plain(c) for c in committed)


async def test_run_no_current_model_unavailable() -> None:
    harness = _Harness(model=None)
    committed: list[object] = []
    await run_thinking_picker(
        harness=harness, select=_select_unreachable, commit=committed.append
    )
    assert harness.set_calls == []
    assert any("unavailable" in _plain(c) for c in committed)


async def test_run_switch_failure_surfaced() -> None:
    harness = _Harness(model=_Model(), level="low", fail_set=True)
    committed: list[object] = []

    async def select(title: str, options: list[str]) -> str | None:
        return options[0]

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert harness.set_calls == []
    assert any("thinking switch failed" in _plain(c) for c in committed)


# === thinking_level_display (PURE, #251) ===================================
#
# The contract these pin: the parenthesised token is the tier the model actually
# RECEIVES for that level, and no parenthesis means the model calls the level by
# the same name. So the display resolves the CLAMP first and only then the
# catalog's native rename — a bare level name was the lie #251 reported.


def test_display_appends_the_native_tier_when_the_model_renames_it() -> None:
    # C1 — the headline: 24 catalog rows map xhigh → "max" and the screen said
    # "xhigh". claude-opus-4-6 is the shape.
    assert thinking_level_display(_Model(thinking_level_map={"xhigh": "max"}), "xhigh") == (
        "xhigh (max)"
    )


def test_display_ignores_a_case_only_rename() -> None:
    # C2 — Google's catalog spells its enum "HIGH"/"LOW"/"MINIMAL"; that is the
    # same tier, so a suffix would be noise on 16 catalog rows.
    assert thinking_level_display(_Model(thinking_level_map={"high": "HIGH"}), "high") == "high"


def test_display_resolves_a_clamp_down_to_the_level_the_request_carries() -> None:
    # C3 — the /model defect: AgentHarness.set_model leaves _state.thinking_level
    # alone, so "xhigh" survives a switch to a model that stops at "high" and the
    # adapters clamp it. gpt-5.1's shape (no xhigh key → xhigh unsupported).
    model = _Model(thinking_level_map={"off": "none"})
    assert thinking_level_display(model, "xhigh") == "xhigh (high)"


def test_display_resolves_a_clamp_up_too() -> None:
    # C3b — clamp_thinking_level scans FORWARD first (models.py:179-182), so the
    # parenthesis can name a tier ABOVE the chosen level. deepseek-v4-pro's shape:
    # minimal/low/medium are unsupported, so "low" is served by "high".
    model = _Model(
        thinking_level_map={
            "minimal": None,
            "low": None,
            "medium": None,
            "high": "high",
            "xhigh": "max",
        }
    )
    assert thinking_level_display(model, "low") == "low (high)"


def test_display_leaves_off_bare_even_when_the_catalog_renames_it() -> None:
    # C4 — harness/core.py:4331-4336 folds off/unset to ``reasoning=None`` before
    # the adapters see it, so "off" never reaches one as a tier and there is no
    # true value to put in the parenthesis.
    assert thinking_level_display(_Model(thinking_level_map={"off": "none"}), "off") == "off"


def test_display_says_off_on_a_non_reasoning_model() -> None:
    # C4b — the recorded decision (#251): get_supported_thinking_levels
    # short-circuits a non-reasoning model to ["off"], so every level clamps to
    # "off" there. That is TRUE for 356 of the 369 non-reasoning catalog rows
    # (the adapters send no thinking field at all); a bare "xhigh" is the same
    # lie #251 reported. The 13 Gemini exceptions are a separate adapter defect.
    model = _Model(reasoning=False, thinking_level_map={"xhigh": "max"})
    assert thinking_level_display(model, "xhigh") == "xhigh (off)"


def test_display_degrades_on_a_missing_or_partial_model() -> None:
    # C5 — defensive paths + the strip contract. A whitespace-only catalog value
    # must not render "high ( )".
    assert thinking_level_display(None, "high") == "high"
    # ``thinking_level_map=None`` is the real Model default; _Model coerces it to
    # {}, so a bare namespace is the only way to exercise the ``or {}`` guard.
    bare = SimpleNamespace(reasoning=True, thinking_level_map=None)
    assert thinking_level_display(bare, "high") == "high"
    assert thinking_level_display(_Model(thinking_level_map={"low": "sub"}), "high") == "high"
    # A ``None`` VALUE is not a missing rename — it means the level is not
    # supported (models.py:130-132), so the clamp answers and the suffix is real.
    assert thinking_level_display(_Model(thinking_level_map={"high": None}), "high") == (
        "high (medium)"
    )
    assert thinking_level_display(_Model(thinking_level_map={"high": ""}), "high") == "high"
    assert thinking_level_display(_Model(thinking_level_map={"high": "  "}), "high") == "high"
    assert thinking_level_display(_Model(), "") == ""


def test_display_ignores_a_non_string_catalog_value() -> None:
    # C6 — an int budget is not a tier NAME and the adapters disagree about it:
    # _anthropic_transforms.py:517-535 guards on ``isinstance(mapped, str)`` and
    # sends a coarse "high", while openai_completions._native_effort forwards the
    # number. The catalog carries zero int values today, so nothing is lost.
    # (The line range was 374-393 until #258; it had already stopped covering
    # the guard, which the drift gate cannot see because it compares the text
    # at the cited lines, not the claim about them.)
    assert thinking_level_display(_Model(thinking_level_map={"high": 32000}), "high") == "high"


def test_labels_carry_the_tier_when_a_model_is_passed() -> None:
    # C7 — marker, number and suffix compose.
    model = _Model(thinking_level_map={"xhigh": "max"})
    labels = thinking_picker_labels(["off", "high", "xhigh"], "xhigh", model=model)
    assert labels == ["1. off", "2. high", "✱ 3. xhigh (max)"]


def test_labels_without_a_model_are_unchanged() -> None:
    # C8 — the keyword default keeps the pure helper's old contract, which is
    # what the two label tests above still assert.
    assert thinking_picker_labels(["off", "high", "xhigh"], "high") == [
        "1. off",
        "✱ 2. high",
        "3. xhigh",
    ]


def test_labels_stay_unique_and_index_recovery_stays_lossless() -> None:
    # C9 — ADR-0155's index round-trip over the worst catalog shape: glm-5.2 maps
    # three different levels onto the SAME native "high". The suffix cannot make
    # two rows collide (every label leads with its own distinct level name, so
    # these stay unique with the "N." prefix stripped too — verified by mutation);
    # what this pins is that the round-trip still recovers the pure level name
    # from a label the suffix has widened.
    model = _Model(
        thinking_level_map={"low": "high", "medium": "high", "high": "high", "xhigh": "max"}
    )
    levels = ["off", "minimal", "low", "medium", "high", "xhigh"]
    labels = thinking_picker_labels(levels, "high", model=model)
    assert labels == [
        "1. off",
        "2. minimal",
        "3. low (high)",
        "4. medium (high)",
        "✱ 5. high",
        "6. xhigh (max)",
    ]
    assert len(set(labels)) == len(labels)
    assert levels[labels.index("4. medium (high)")] == "medium"
    # The comment above, made into an assertion: strip the "N. " prefix the ADR
    # credits with uniqueness and the rows are STILL distinct, because every label
    # leads with its own level name. The suffix cannot collide two rows, so the
    # prefix is here for the index round-trip, not to rescue this shape.
    bare = [label.removeprefix("✱ ").split(". ", 1)[1] for label in labels]
    assert bare == ["off", "minimal", "low (high)", "medium (high)", "high", "xhigh (max)"]
    assert len(set(bare)) == len(bare)


def test_display_without_a_model_returns_the_bare_level() -> None:
    # The docstring promises callers may pass ``model=None`` (a partial harness
    # has no ``current_model``). There is no ``model is None`` guard any more —
    # the clamp raises into the ``except`` and the getattr fall-through returns
    # the level — so this pins the promise against the path that actually serves
    # it. Deleting that guard was verified not to change any of these answers.
    assert thinking_level_display(None, "high") == "high"
    assert thinking_level_display(None, "xhigh") == "xhigh"
    assert thinking_level_display(None, "off") == "off"
    assert thinking_level_display(None, "") == ""
    # A model that answers nothing degrades the same way.
    assert thinking_level_display(SimpleNamespace(), "high") == "high"


def test_widest_tier_suffix_over_the_vendored_catalog_is_ten_columns() -> None:
    # The CHANGELOG hands the manual 80-column check a width budget, and the
    # orchestrator judges the statusline clipping against it, so the number has to
    # be measured rather than generalised from ``xhigh (max)`` (which is only 6).
    # Widest today: "high (default)" on groq/qwen/qwen3-32b, a row you can pick
    # straight out of /thinking. If vendoring adds a longer native tier name this
    # fails and the CHANGELOG sentence gets corrected with it.
    from aelix_ai.models import get_models, get_providers

    levels = ["off", "minimal", "low", "medium", "high", "xhigh"]
    widest = max(
        (
            (len(thinking_level_display(model, level)) - len(level), provider, model.id, level)
            for provider in get_providers()
            for model in get_models(provider)
            for level in levels
        ),
    )
    assert widest[0] == 10
    assert widest[1:3] == ("groq", "qwen/qwen3-32b")


def test_off_understates_the_non_reasoning_google_rows() -> None:
    # The docstring and the guide both say the parenthesis names the tier the
    # REQUEST carries. Both Google adapters break that: they clamp, then revive a
    # clamped "off" into "high", so those requests DO carry thinking while the
    # screen reads "high (off)". This states the divergence rather than hiding it
    # (#256); when #256 is fixed this test flips to ``enabled is False``.
    from aelix_ai.models import MODELS
    from aelix_ai.providers.google_generative_ai import _thinking_for_simple
    from aelix_ai.streaming import SimpleStreamOptions

    model = MODELS["google"]["gemini-2.0-flash"]
    assert model.reasoning is False
    assert thinking_level_display(model, "high") == "high (off)"

    thinking = _thinking_for_simple(model, SimpleStreamOptions(reasoning="high"))
    assert thinking.enabled is True  # NOT what "(off)" says — #256

    # The count the CHANGELOG, the docstring and the guide all quote.
    non_reasoning = [
        m
        for provider in ("google", "google-vertex")
        for m in MODELS[provider].values()
        if not m.reasoning
    ]
    assert len(non_reasoning) == 13


def test_display_matches_the_vendored_catalog_for_opus_4_6() -> None:
    # C12 — the real catalog row behind the issue. If vendoring drops the
    # xhigh → max mapping this says so instead of the feature quietly no-op'ing.
    from aelix_ai.models import MODELS

    model = MODELS["anthropic"]["claude-opus-4-6"]
    assert thinking_level_display(model, "xhigh") == "xhigh (max)"
    assert thinking_level_display(model, "high") == "high"


# === run_thinking_picker carries the tier (#251) ===========================


async def test_run_offers_and_confirms_the_native_tier() -> None:
    # C10 + C11 — the display string must NOT leak into the setter, and the
    # confirmation line says the same tier the footer will.
    harness = _Harness(model=_Model(thinking_level_map={"xhigh": "max"}), level="high")
    committed: list[object] = []
    captured: dict[str, Any] = {}

    async def select(title: str, options: list[str]) -> str | None:
        captured["options"] = options
        return next(o for o in options if o.endswith("xhigh (max)"))

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert captured["options"][-1] == "6. xhigh (max)"
    assert harness.set_calls == ["xhigh"]  # the LEVEL, never the label
    assert [_plain(c) for c in committed] == ["thinking → xhigh (max)"]


async def test_run_confirms_without_a_suffix_when_the_names_agree() -> None:
    # C11, other half — the suffix is not decoration; a model that calls the
    # level by its own name still gets one bare word.
    harness = _Harness(model=_Model(), level="low")
    committed: list[object] = []

    async def select(title: str, options: list[str]) -> str | None:
        return next(o for o in options if o.endswith("high"))

    await run_thinking_picker(harness=harness, select=select, commit=committed.append)
    assert harness.set_calls == ["high"]
    assert [_plain(c) for c in committed] == ["thinking → high"]


async def test_run_marks_the_clamped_row_not_the_stored_level() -> None:
    # C11b — the caller clamps ``current`` (NOT thinking_picker_labels, so the
    # pure helper's contract and test_labels_no_current_match_marks_nothing both
    # survive). Without it, opening the picker on gpt-5.1 while the state still
    # says "xhigh" marks NO row while the footer reads "🧠 xhigh (high)".
    harness = _Harness(model=_Model(thinking_level_map={"off": "none"}), level="xhigh")
    captured: dict[str, Any] = {}

    async def select(title: str, options: list[str]) -> str | None:
        captured["options"] = options
        return None

    await run_thinking_picker(harness=harness, select=select, commit=lambda r: None)
    assert captured["options"] == ["1. off", "2. minimal", "3. low", "4. medium", "✱ 5. high"]
