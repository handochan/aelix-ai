"""ADR-0135 (P0 #1) — provider-layer reasoning resolution.

Layer 2 (OpenAI-completions): ``build_params`` applies ``thinkingLevelMap``
in the deepseek + openrouter branches (pi ``openai-completions.ts:567-582``).

Layer 3 (Anthropic): the request builder now emits a ``thinking`` param —
adaptive ``effort`` for Opus 4.6+/Sonnet 4.6, ``budget_tokens`` for older
reasoning models — plus the interleaved-thinking beta header for the
budget-based path (pi ``anthropic.ts`` + ``simple-options.ts``, SHA 734e08e).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

from aelix_ai.messages import UserMessage
from aelix_ai.models import (
    clamp_thinking_level,
    get_model,
    get_supported_thinking_levels,
)
from aelix_ai.models_generated import MODELS
from aelix_ai.providers._anthropic_transforms import (
    _DEFAULT_THINKING_BUDGETS,
    _MIN_OUTPUT_TOKENS,
    _MIN_THINKING_BUDGET,
    INTERLEAVED_THINKING_BETA,
    adjust_max_tokens_for_thinking,
    clamp_reasoning,
    map_thinking_level_to_effort,
    resolve_anthropic_thinking,
    supports_adaptive_thinking,
)
from aelix_ai.providers._openai_compat import get_compat
from aelix_ai.providers.anthropic import (
    _effective_output_cap,
    _with_interleaved_beta,
    stream_anthropic,
)
from aelix_ai.providers.openai_completions import (
    OpenAICompletionsOptions,
    build_params,
    stream_simple_openai_completions,
)
from aelix_ai.streaming import Context, Model, SimpleStreamOptions


def _or_model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="openai-completions",
        id="gpt-4",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        input=["text"],
        max_tokens=1024,
        reasoning=True,
    )
    base.update(kwargs)
    return Model(**base)


# === Layer 2: OpenAI thinkingLevelMap application ==========================


def test_openrouter_thinking_level_map_applied() -> None:
    model = _or_model(thinking_level_map={"high": "high", "xhigh": "max"})
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="xhigh")
    params = build_params(model, Context(), opts, compat, "short")
    # xhigh → "max" via the model's thinkingLevelMap (pi:577-578).
    assert params["reasoning"] == {"effort": "max"}


def test_openrouter_no_map_passes_level_through() -> None:
    model = _or_model()  # no thinking_level_map
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["reasoning"] == {"effort": "high"}


def test_openrouter_off_null_omits_reasoning() -> None:
    model = _or_model(thinking_level_map={"off": None})
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    # Explicit ``off: null`` → omit ``reasoning`` entirely (pi:580).
    assert "reasoning" not in params


def test_openrouter_off_string_used() -> None:
    model = _or_model(thinking_level_map={"off": "disable"})
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["reasoning"] == {"effort": "disable"}


def test_openrouter_no_map_off_defaults_none() -> None:
    """Regression: pre-ADR-0135 behaviour preserved when no map present."""

    model = _or_model()
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["reasoning"] == {"effort": "none"}


def test_deepseek_thinking_level_map_applied() -> None:
    model = _or_model(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        thinking_level_map={"medium": "med-native"},
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="medium")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["thinking"] == {"type": "enabled"}
    assert params["reasoning_effort"] == "med-native"


def test_deepseek_off_disables_thinking() -> None:
    model = _or_model(
        provider="deepseek", base_url="https://api.deepseek.com/v1"
    )
    compat = get_compat(model)
    params = build_params(model, Context(), None, compat, "short")
    assert params["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in params


def test_together_thinking_level_map_applied() -> None:
    model = _or_model(
        provider="together",
        base_url="https://api.together.ai/v1",
        thinking_level_map={"high": "hi-native"},
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    assert params["reasoning"] == {"enabled": True}
    if compat.supports_reasoning_effort:
        # pi:589-590 only sets reasoning_effort when supports_reasoning_effort.
        assert params["reasoning_effort"] == "hi-native"


def test_default_openai_style_thinking_level_map_applied() -> None:
    # OpenAI-style fallback branch (no special thinking_format) maps too.
    model = _or_model(
        provider="openai",
        base_url="https://api.openai.com/v1",
        thinking_level_map={"high": "hi-native"},
    )
    compat = get_compat(model)
    opts = OpenAICompletionsOptions(reasoning_effort="high")
    params = build_params(model, Context(), opts, compat, "short")
    if compat.supports_reasoning_effort:
        assert params["reasoning_effort"] == "hi-native"


def test_default_off_string_emitted_when_no_effort() -> None:
    # pi:595-600 — with no requested effort, an explicit string off mapping is
    # emitted on the OpenAI-style path.
    model = _or_model(
        provider="openai",
        base_url="https://api.openai.com/v1",
        thinking_level_map={"off": "off-native"},
    )
    compat = get_compat(model)
    if not compat.supports_reasoning_effort:
        return
    params = build_params(model, Context(), None, compat, "short")
    assert params.get("reasoning_effort") == "off-native"


# === Layer 2: end-to-end clamp → map through stream_simple ================


class _EmptyAsyncIter:
    def __aiter__(self) -> _EmptyAsyncIter:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _RawResp:
    status_code = 200
    headers: dict[str, Any] = {}


class _RawWrap:
    def __init__(self) -> None:
        self.http_response = _RawResp()

    def parse(self) -> _EmptyAsyncIter:
        return _EmptyAsyncIter()


class _WithRaw:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    async def create(self, **kwargs: Any) -> _RawWrap:
        self._captured["params"] = kwargs
        return _RawWrap()


class _FakeOpenAI:
    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}
        self.chat = type(
            "_Chat", (), {"completions": type(
                "_Comp", (), {"with_raw_response": _WithRaw(self.captured)}
            )()}
        )()


async def test_stream_simple_clamps_then_maps() -> None:
    """End-to-end: stream_simple clamps xhigh→high (model lacks xhigh) BEFORE
    build_params applies the thinkingLevelMap (high→native)."""

    model = _or_model(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        thinking_level_map={"high": "hi-native"},  # xhigh absent → unsupported
    )
    fake = _FakeOpenAI()
    opts = SimpleStreamOptions(reasoning="xhigh", client=fake, api_key="k")
    async for _ in stream_simple_openai_completions(model, Context(), opts):
        pass
    # xhigh clamped to high, then high mapped to "hi-native".
    assert fake.captured["params"]["reasoning_effort"] == "hi-native"


# === Layer 3: Anthropic thinking helpers (unit) ============================


def _ant_model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="anthropic-messages",
        id="claude-3-7-sonnet",
        provider="anthropic",
    )
    base.update(kwargs)
    return Model(**base)


def test_clamp_reasoning_keeps_xhigh_as_its_own_tier() -> None:
    # #250: aelix diverges from pi ``clampReasoning`` (simple-options.ts:22-24,
    # which still folds "xhigh" onto "high"). "xhigh" is a budget row here, so
    # the clamp only rejects spellings the budget table cannot resolve.
    assert clamp_reasoning("xhigh") == "xhigh"
    assert clamp_reasoning("medium") == "medium"
    # Unknown spellings clamp to "medium" — the budget the ``.get`` fallback in
    # ``adjust_max_tokens_for_thinking`` already handed them (measured on
    # 0985fcf: ``adjust(64000, 64000, "ultra")`` -> budget 8192). "off" is the
    # unvalidated string that really arrives (ADR-0135 Context §3):
    # ``resolve_anthropic_thinking`` gates on ``if not reasoning`` and "off" is
    # truthy. Clamping unknowns to "high" would have doubled both to 16384.
    assert clamp_reasoning("ultra") == "medium"
    assert clamp_reasoning("off") == "medium"
    assert adjust_max_tokens_for_thinking(64000, 64000, "ultra")[1] == 8192
    assert adjust_max_tokens_for_thinking(64000, 64000, "off")[1] == 8192
    # A ``custom_budgets`` key of any spelling stays reachable (#250 §A.2b).
    assert clamp_reasoning("max", {"max": 1}) == "max"
    # Caller contract: ``budgets`` is the MERGED table, not the overrides. Hand
    # it the raw overrides and every standard level would demote to "medium".
    assert clamp_reasoning("high", {"max": 1}) == "medium"
    assert clamp_reasoning("high", {**_DEFAULT_THINKING_BUDGETS, "max": 1}) == "high"


def test_adjust_max_tokens_carves_budget_below_max() -> None:
    # Roomy model: budget stays at the medium default, max_tokens capped.
    max_tokens, budget = adjust_max_tokens_for_thinking(64000, 64000, "medium")
    assert max_tokens == 64000
    assert budget == 8192


def test_adjust_max_tokens_shrinks_budget_when_tight() -> None:
    # Tight model: budget exceeds max_tokens → shrink to leave 1024 output.
    max_tokens, budget = adjust_max_tokens_for_thinking(4096, 4096, "high")
    assert max_tokens == 4096
    assert budget == 4096 - 1024


def test_map_effort_prefers_thinking_level_map() -> None:
    model = _ant_model(thinking_level_map={"xhigh": "max"})
    assert map_thinking_level_to_effort(model, "xhigh") == "max"
    # Unmapped levels fall back to the coarse mapping (pi anthropic.ts:715-725).
    assert map_thinking_level_to_effort(model, "minimal") == "low"
    assert map_thinking_level_to_effort(model, "low") == "low"
    assert map_thinking_level_to_effort(model, "medium") == "medium"
    assert map_thinking_level_to_effort(model, "high") == "high"
    # Unknown / None default to "high".
    assert map_thinking_level_to_effort(model, None) == "high"
    assert map_thinking_level_to_effort(model, "ultra") == "high"


def test_adjust_max_tokens_xhigh_is_its_own_tier() -> None:
    # #250: xhigh is a budget row of its own — 32768, twice "high".
    _max, budget = adjust_max_tokens_for_thinking(64000, 64000, "xhigh")
    assert budget == 32768
    _hmax, high_budget = adjust_max_tokens_for_thinking(64000, 64000, "high")
    assert high_budget == 16384
    assert budget > high_budget


def test_xhigh_raises_request_max_tokens_when_room_allows() -> None:
    # The change's SECOND behaviour. ``adjust`` returns (max_tokens, budget) and
    # ``max_tokens = min(base + budget, clamp)``, so a caller that supplied its
    # own ``options.max_tokens`` gets a request 16384 tokens larger. This is
    # the only case here with ``base + budget < clamp``, i.e. the only one that
    # reads the ``min``. No in-tree caller reaches it today: the compaction
    # summarizer sets ``max_tokens`` but never ``reasoning`` (compaction.py:896,
    # :986), so it takes the ``thinking: disabled`` branch, and the harness sets
    # ``reasoning`` but drops ``max_tokens`` (core.py:4240). The arithmetic is
    # pinned here for the out-of-tree caller that supplies both.
    # Measured on 0985fcf, before the change: (48384, 16384).
    assert adjust_max_tokens_for_thinking(32000, 128000, "xhigh") == (64768, 32768)


def test_xhigh_budget_still_leaves_answer_room() -> None:
    # Budget above "high" (16384) but above the cap too → the carve shrinks it
    # to leave _MIN_OUTPUT_TOKENS for the visible answer. Anthropic rejects a
    # budget that does not fit under max_tokens. Before #250: (24000, 16384).
    assert adjust_max_tokens_for_thinking(24000, 24000, "xhigh") == (24000, 22976)


def test_unsat_ceiling_cap_collapses_the_answer_to_the_minimum() -> None:
    # ``_UNSAT_ABSOLUTE_OUTPUT_CEILING = 32000`` (providers/anthropic.py:207) is
    # the cap a row whose maxTokens >= contextWindow gets. At that cap an xhigh
    # request leaves exactly _MIN_OUTPUT_TOKENS of answer room where "high"
    # leaves 15616. 46 catalog rows are clamped to it, but none of them offers
    # ``xhigh``, and ``stream_anthropic`` clamps the level against the row
    # (test_xhigh_on_a_row_that_does_not_offer_it_… below), so reaching this
    # arithmetic needs a models.json override on a row that DOES offer xhigh
    # (all 20 of those cap at 16384/64000/128000). Pinned so a future change to
    # that constant is visible.
    assert adjust_max_tokens_for_thinking(32000, 32000, "xhigh") == (32000, 30976)
    assert adjust_max_tokens_for_thinking(32000, 32000, "high") == (32000, 16384)


def test_tight_cap_row_still_collapses() -> None:
    # The measured limit of the fix: on a 16384-cap row (vercel-ai-gateway
    # ``openai/gpt-5.2-chat`` / ``openai/gpt-5.3-chat``) the carve shrinks both
    # tiers to 15360, so xhigh is unchanged there. Equal is the floor now — it
    # can no longer come out BELOW high (test_budget_never_falls_as_the_tier_
    # rises), which is what the cap-just-above-16384 case used to do.
    assert adjust_max_tokens_for_thinking(16384, 16384, "xhigh") == (16384, 15360)
    assert adjust_max_tokens_for_thinking(16384, 16384, "high") == (16384, 15360)


def test_budget_never_falls_as_the_tier_rises() -> None:
    """#250 Codex cross-review, finding 2 — the class, not the one value.

    Measured on 73d167a: ``adjust(17000, 17000, "xhigh")`` returned 15976
    while ``"high"`` returned 16384, so asking for MORE reasoning got less.
    The cause was pi's after-the-fact ``if maxTokens <= thinkingBudget``
    shrink (simple-options.ts:26-50): it fires only once a tier's budget has
    swallowed the cap whole, and *which* tiers it has swallowed depends on the
    cap — so between ``high``'s boundary and ``xhigh``'s the two tiers cross.
    One assertion per cap would have pinned one crossing; this walks every
    cap from 1 to 40000 and asserts the property, which is what "monotonic"
    means.

    ``base = cap`` is the shipped shape (``default_max_tokens`` is the
    effective output cap when the caller supplies no ``max_tokens``); the
    second walk varies the base independently, because the budget must not
    depend on it either.
    """

    tiers = ["minimal", "low", "medium", "high", "xhigh"]
    assert [_DEFAULT_THINKING_BUDGETS[t] for t in tiers] == sorted(
        _DEFAULT_THINKING_BUDGETS[t] for t in tiers
    ), "the table itself must be ordered, or this test proves nothing"

    caps = list(range(1, 2200)) + list(range(2200, 40001, 7))
    for cap in caps:
        budgets = [adjust_max_tokens_for_thinking(cap, cap, t)[1] for t in tiers]
        assert budgets == sorted(budgets), (cap, budgets)
    for base in (1, 500, 1024, 4096, 17000, 64000):
        for cap in (2048, 16385, 17000, 17408, 32769, 33792, 64000):
            budgets = [
                adjust_max_tokens_for_thinking(base, cap, t)[1] for t in tiers
            ]
            assert budgets == sorted(budgets), (base, cap, budgets)

    # The exact inversion the review reported, now equal rather than inverted.
    assert adjust_max_tokens_for_thinking(17000, 17000, "xhigh")[1] == 15976
    assert adjust_max_tokens_for_thinking(17000, 17000, "high")[1] == 15976


def test_the_carve_never_takes_more_answer_room_than_it_must() -> None:
    """#250 Codex cross-review finding 2, re-scoped by the beta2 re-review.

    The carve must not shrink the visible answer below ``_MIN_OUTPUT_TOKENS``;
    pi's shrink did not deliver that in the window ``(B, B + 1024)``. Measured
    on 73d167a: a row overridden to ``maxTokens: 32769`` sent
    ``budget_tokens: 32768`` — **1** visible token — and ``maxTokens: 16400``
    left ``high`` 16 tokens of answer. Reachable through a ``maxTokens``
    override in ``models.json``, which validates only that the number is
    positive.

    The re-review caught the guarantee being stated *unqualified* while the
    code delivers it only when the caller's own base is at least 1024. The
    real room is ``min(base_max_tokens, model_max_tokens - budget)`` and only
    the right operand is the carve's business: ``adjust(1, 1025, "high")`` is
    ``(2, 1)``, one visible token, because one is what the caller asked for.
    Raising that to 1024 would return more answer than was requested, so the
    code is right and the sentence was wrong. First arm below is the original
    ``base == cap`` walk; the second pins the small-base arm, so the weaker
    and true invariant is what regresses if this changes.
    """

    assert adjust_max_tokens_for_thinking(32769, 32769, "xhigh") == (32769, 31745)
    assert adjust_max_tokens_for_thinking(16400, 16400, "high") == (16400, 15376)
    for cap in list(range(_MIN_OUTPUT_TOKENS + 1, 4000)) + list(
        range(4000, 40001, 11)
    ):
        for tier in _DEFAULT_THINKING_BUDGETS:
            max_tokens, budget = adjust_max_tokens_for_thinking(cap, cap, tier)
            assert max_tokens - budget >= _MIN_OUTPUT_TOKENS, (cap, tier)

    assert adjust_max_tokens_for_thinking(1, 1025, "high") == (2, 1)
    for base in (1, 100, 500, _MIN_OUTPUT_TOKENS - 1, _MIN_OUTPUT_TOKENS):
        for cap in list(range(_MIN_OUTPUT_TOKENS + 1, 4000, 7)) + list(
            range(4000, 40001, 101)
        ):
            for tier in _DEFAULT_THINKING_BUDGETS:
                max_tokens, budget = adjust_max_tokens_for_thinking(base, cap, tier)
                assert max_tokens - budget == min(base, cap - budget), (
                    base,
                    cap,
                    tier,
                )
                assert max_tokens - budget >= min(base, _MIN_OUTPUT_TOKENS), (
                    base,
                    cap,
                    tier,
                )


def test_a_long_prompt_alone_reaches_the_carve_hole_on_a_shipped_row() -> None:
    """Half of finding 2 needs no `models.json` override at all.

    The review scoped it to overrides. But ``_effective_output_cap``
    (anthropic.py) clamps a row whose ``maxTokens >= contextWindow`` to
    ``min(context_window - prompt - margin, 32000)`` — so on those rows the
    cap is a function of the PROMPT, and a long enough prompt walks it through
    every value, holes included. Measured here on the shipped `fireworks` row
    ``accounts/fireworks/models/deepseek-v3p1`` (window == cap == 163840, no
    override, ``high`` offered): a ~583k-character prompt puts the effective
    cap at ~17000, inside ``high``'s ``(16384, 17408)`` window, where pi's
    shrink left the visible answer ~626 tokens instead of 1024.

    The character count is searched rather than pinned because it belongs to
    the shared token estimate, not to this fix.

    The *inversion* half does still need an override: none of the unsat rows
    offers ``xhigh`` (asserted below), so no two tiers can cross on them.
    """

    rows = [
        model
        for provider_models in MODELS.values()
        for model in provider_models.values()
        if model.api == "anthropic-messages"
        and model.reasoning
        and not supports_adaptive_thinking(model)
        and (model.max_tokens or 0) > 0
        and (model.context_window or 0) > 0
        and (model.max_tokens or 0) >= (model.context_window or 0)
    ]
    assert rows, "no UNSAT budget-path row — the walk lost its subject"
    assert not [m for m in rows if "xhigh" in get_supported_thinking_levels(m)]

    model = next(
        m for m in rows if m.id == "accounts/fireworks/models/deepseek-v3p1"
    )

    def cap_for(chars: int) -> int:
        return _effective_output_cap(
            model, Context(messages=[UserMessage(content="x" * chars)])
        )

    lo, hi = 1, 3_000_000
    while lo < hi:  # smallest prompt whose cap drops to 17000 or below
        mid = (lo + hi) // 2
        if cap_for(mid) > 17000:
            lo = mid + 1
        else:
            hi = mid
    cap = cap_for(lo)
    assert _DEFAULT_THINKING_BUDGETS["high"] < cap <= 17000, cap

    max_tokens, budget = adjust_max_tokens_for_thinking(cap, cap, "high")
    assert max_tokens == cap
    assert max_tokens - budget == _MIN_OUTPUT_TOKENS
    # What pi's shrink would have sent at this cap: the full 16384, leaving
    # under 1024 tokens of answer.
    assert cap - _DEFAULT_THINKING_BUDGETS["high"] < _MIN_OUTPUT_TOKENS


def test_a_caller_max_tokens_caps_the_answer_not_the_payload() -> None:
    """#250 Codex cross-review, finding 1 — the contract, pinned as intended.

    ``SimpleStreamOptions(reasoning="xhigh", max_tokens=16384)`` on a
    128000-cap budget row builds ``max_tokens: 49152`` with
    ``budget_tokens: 32768``, i.e. a payload larger than the caller's number.
    That is pi's contract and not drift: ``adjustMaxTokensForThinking``
    (simple-options.ts:26-50) computes ``min(base + budget, model.maxTokens)``
    and its own comment on the parameter says an undefined base means "no
    explicit caller cap", so a defined one is a base to add the budget to.
    What the caller's number bounds is the VISIBLE answer, and that is the
    invariant asserted here — the review read the field as a payload cap
    because the docstring said so; the docstring was fixed, not the maths.

    #258 moved the subject. The numbers were measured on
    ``anthropic/claude-opus-5``, which now takes the *adaptive* path and never
    builds a budget; the row below is one of the 13 that still offer ``xhigh``
    on the budget path (counted 2026-09-09) and carries the same 128000 cap,
    so every number in this test is unchanged.
    """

    model = get_model("vercel-ai-gateway", "openai/gpt-5.2")
    assert model is not None and model.max_tokens == 128000
    assert model.api == "anthropic-messages" and not supports_adaptive_thinking(model)
    extra, max_tokens, _beta = resolve_anthropic_thinking(model, "xhigh", 16384)
    budget = extra["thinking"]["budget_tokens"]
    assert (max_tokens, budget) == (49152, 32768)
    assert max_tokens - budget == 16384  # the caller's cap, honoured

    for base in (256, 1024, 8192, 16384, 64000, 200000):
        for level in _DEFAULT_THINKING_BUDGETS:
            _e, mt, _b = resolve_anthropic_thinking(model, level, base)
            spent = _e["thinking"].get("budget_tokens", 0)
            assert mt - spent <= base, (base, level)


def test_no_output_cap_can_build_a_request_anthropic_rejects() -> None:
    """#250 Codex cross-review, finding 3.

    Anthropic's rule on the budget path: ``budget_tokens`` is at least 1024
    and strictly less than ``max_tokens`` — otherwise a 400, not a shorter
    answer. On 73d167a the ``budget_tokens: budget or 1024`` fallback broke
    both halves through a ``models.json`` ``maxTokens`` override: at 1024 it
    sent ``budget_tokens: 1024`` with ``max_tokens: 1024`` (equal), and at 512
    a budget larger than the whole request.

    The chosen repair is to disable thinking rather than clamp, because a cap
    below ``_MIN_THINKING_BUDGET + _MIN_OUTPUT_TOKENS`` has no budget that is
    both >= 1024 and leaves an answer. This walk is the "unconstructible"
    claim: every cap from 1 to 40000 either disables thinking or produces a
    pair the API accepts.

    #258 re-pointed the id: those measurements were taken while
    ``claude-opus-5`` still took the budget path, and it no longer does, so
    the walk runs on ``claude-opus-4-1`` — a row that does. The branch under
    test is the carve, which never read the id.
    """

    model = _ant_model(id="claude-opus-4-1", reasoning=True)
    caps = list(range(1, 2200)) + list(range(2200, 40001, 13))
    disabled = 0
    for cap in caps:
        for level in _DEFAULT_THINKING_BUDGETS:
            capped = replace(model, max_tokens=cap)
            extra, max_tokens, needs_beta = resolve_anthropic_thinking(
                capped, level, cap, max_tokens_ceiling=cap
            )
            thinking = extra["thinking"]
            if thinking["type"] == "disabled":
                assert "budget_tokens" not in thinking
                assert needs_beta is False
                assert max_tokens == cap
                disabled += 1
                continue
            budget = thinking["budget_tokens"]
            assert budget >= _MIN_THINKING_BUDGET, (cap, level)
            assert budget < max_tokens, (cap, level)
    assert disabled, "the disabled branch was never taken — the walk lost it"


def test_thinking_disables_itself_only_below_the_two_minimums() -> None:
    """The boundary of the branch above, pinned to the exact token.

    2048 = ``_MIN_THINKING_BUDGET`` (Anthropic's floor for ``budget_tokens``)
    + ``_MIN_OUTPUT_TOKENS`` (aelix's reserve for the visible answer). Below
    it there is no valid request; at it there is exactly one.

    On ``claude-opus-4-1`` for the same reason as the walk above (#258).
    """

    model = _ant_model(id="claude-opus-4-1", reasoning=True)
    for cap in (1, 512, 1024, 2047):
        capped = replace(model, max_tokens=cap)
        extra, max_tokens, needs_beta = resolve_anthropic_thinking(
            capped, "xhigh", cap, max_tokens_ceiling=cap
        )
        assert extra == {"thinking": {"type": "disabled"}}, cap
        assert (max_tokens, needs_beta) == (cap, False)
    capped = replace(model, max_tokens=2048)
    extra, max_tokens, needs_beta = resolve_anthropic_thinking(
        capped, "xhigh", 2048, max_tokens_ceiling=2048
    )
    assert extra["thinking"]["budget_tokens"] == 1024
    assert (max_tokens, needs_beta) == (2048, True)


def test_off_passed_as_a_string_turns_thinking_off() -> None:
    """#258 — the string ``"off"`` now means off on this adapter.

    It did not on 0985fcf: ``resolve_anthropic_thinking`` gated on ``if not
    reasoning`` and ``"off"`` is truthy (ADR-0135 Context §3), so it reached
    the budget path and ``clamp_reasoning`` bought it ``"medium"``'s 8192
    — the behaviour this test characterised while #259 was open.

    #258 could not leave it there. Moving the 5-family rows onto the adaptive
    path makes ``map_thinking_level_to_effort(model, "off")`` the thing that
    answers, and its coarse fallback returns ``"high"``: asking for no
    thinking would have bought the most. So the string is answered where it
    arrives, and the walk below covers both shapes it can be answered in.

    #259 stays open on its own terms: both Google adapters still map ``"off"``
    to ``"high"``, and that cross-adapter contract is not this issue's.
    """

    # Budget row: "off" is a disabled request, not a medium-sized budget.
    budget_row = _ant_model(id="claude-opus-4-1", reasoning=True, max_tokens=64000)
    extra, max_tokens, needs_beta = resolve_anthropic_thinking(
        budget_row, "off", 64000
    )
    assert extra == {"thinking": {"type": "disabled"}}
    assert (max_tokens, needs_beta) == (64000, False)

    # Adaptive row that CAN be turned off: same disabled request. Measured
    # 2026-09-09 — ``claude-opus-5`` answers with thinking disabled.
    opus5 = get_model("anthropic", "claude-opus-5")
    assert opus5 is not None
    assert resolve_anthropic_thinking(opus5, "off", 64000)[0] == {
        "thinking": {"type": "disabled"}
    }

    # Adaptive row that CANNOT: ``claude-fable-5`` declares ``"off": null``
    # and 400s on ``thinking.type.disabled`` (``req_011CerzNQTwZdGcBeTt9o3Ea``),
    # so "off" becomes the least thinking it offers. ``clamp_thinking_level``
    # — which ``stream_anthropic`` applies (#250) — moves "off" UP to
    # "minimal" on that row, and both spellings land on the same request, so
    # the clamp can no longer smuggle thinking back in through ``budget_tokens:
    # 1024``.
    fable = get_model("anthropic", "claude-fable-5")
    assert fable is not None
    assert (fable.thinking_level_map or {}).get("off", "absent") is None
    assert clamp_thinking_level(fable, "off") == "minimal"
    off_request = {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }
    assert resolve_anthropic_thinking(fable, "off", 64000)[0] == off_request
    assert resolve_anthropic_thinking(fable, "minimal", 64000)[0] != off_request
    assert resolve_anthropic_thinking(fable, None, 64000)[0] == off_request


def test_adjust_honours_a_custom_budget_key_outside_the_table() -> None:
    # §A.2b: the clamp reads the MERGED table, so a custom_budgets key that is
    # not one of the five default tiers still resolves. Mutation note — gate the
    # clamp on ``_DEFAULT_THINKING_BUDGETS`` alone and this returns 8192
    # ("max" -> "medium"), regressing behaviour that exists on 0985fcf.
    assert adjust_max_tokens_for_thinking(64000, 64000, "max", {"max": 40000})[1] == (
        40000
    )


def test_every_budget_path_xhigh_model_differs_from_high() -> None:
    """Catalog-driven: EVERY row that reaches the Anthropic *budget* path.

    Dispatch is keyed on ``model.api`` alone (api_registry.py), not on the
    provider name, so ``vercel-ai-gateway`` rows serving OpenAI ids over the
    Anthropic Messages API land here too. Asserts the arithmetic rule rather
    than a roster, so a catalog refresh that adds another tight-cap row does
    not fail with no bug present.

    Beta2 (#250): the walk covers every non-adaptive row, not only the ones
    that offer ``xhigh``. Filtering on the offering rows hid the regression the
    review found — the 32768 row reaching the rest through
    ``--thinking xhigh``, which on a row whose cap sits in (16384, 32768] cut
    the answer allowance from 15616 to 1024. The level is therefore taken
    through ``clamp_thinking_level``, the way ``stream_anthropic`` takes it.

    #258 shrank the subject: 282 budget rows, 13 of them offering ``xhigh``
    (counted 2026-09-09; 304 and 23 before, the difference being the 22 Claude
    rows that moved to the adaptive path). The counts are asserted as
    behaviour, not as numbers, so the next catalog refresh moves them without
    failing here.
    """

    def expected(cap: int, budget: int) -> int:
        # adjust(): the tier's budget, capped by the room the cap leaves once
        # _MIN_OUTPUT_TOKENS are reserved. (Before the Codex cross-review this
        # read ``budget if cap > budget else cap - _MIN_OUTPUT_TOKENS``, pi's
        # shrink; the two agree on every shipped cap — none lands in a
        # ``(B, B + 1024)`` window — and disagree only where the old rule left
        # the answer under the minimum.)
        return max(0, min(budget, cap - _MIN_OUTPUT_TOKENS))

    rows = [
        model
        for provider_models in MODELS.values()
        for model in provider_models.values()
        if model.api == "anthropic-messages"
        and model.reasoning
        and not supports_adaptive_thinking(model)
    ]
    # 282 rows at this commit, 13 of which offer xhigh — all
    # vercel-ai-gateway rows serving OpenAI ids over the Anthropic Messages
    # API, since #258 moved every Claude row of that generation to adaptive.
    assert rows, "no budget-path reasoning model — the walk lost its subject"
    offering = 0
    for model in rows:
        cap = model.max_tokens or 0
        effective = clamp_thinking_level(model, "xhigh")
        _hmax, high_budget = adjust_max_tokens_for_thinking(cap, cap, "high")
        _xmax, xhigh_budget = adjust_max_tokens_for_thinking(cap, cap, effective)
        assert high_budget == expected(cap, 16384), model.id
        if "xhigh" not in get_supported_thinking_levels(model):
            # 269 rows: the picker never offers xhigh, so an xhigh that arrives
            # anyway must land on high's budget — 0985fcf behaviour exactly.
            assert effective == "high", model.id
            assert xhigh_budget == high_budget, model.id
            continue
        offering += 1
        assert effective == "xhigh", model.id
        assert xhigh_budget == expected(cap, 32768), model.id
        if cap > 16384 + _MIN_OUTPUT_TOKENS:
            # 11 of the 13 offering rows (cap 128000).
            assert xhigh_budget > high_budget, model.id
        else:
            # ``openai/gpt-5.2-chat`` / ``openai/gpt-5.3-chat``, cap 16384:
            # both tiers collapse to 15360, so xhigh is unchanged there.
            assert xhigh_budget <= high_budget, model.id
        # Whatever the cap, the visible answer keeps at least the minimum.
        assert cap - xhigh_budget >= _MIN_OUTPUT_TOKENS, model.id
    assert offering, "no budget-path model offers xhigh — the walk lost its subject"


def test_resolve_thinking_non_reasoning_model_emits_nothing() -> None:
    model = _ant_model(reasoning=False, max_tokens=8192)
    extra, max_tokens, needs_beta = resolve_anthropic_thinking(model, "high", 8192)
    assert extra == {}
    assert max_tokens == 8192  # passthrough, unchanged
    # Deliberate narrower scope than pi: aelix sends the interleaved beta only
    # on the active budget-thinking path, so a non-reasoning model does NOT.
    assert needs_beta is False


def test_resolve_thinking_off_does_not_need_beta() -> None:
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=8192)
    extra, _max, needs_beta = resolve_anthropic_thinking(model, None, 8192)
    assert extra == {"thinking": {"type": "disabled"}}
    # Thinking off → not active → no interleaved beta (narrower than pi).
    assert needs_beta is False


def test_with_interleaved_beta_appends() -> None:
    assert _with_interleaved_beta(None, False) is None
    out = _with_interleaved_beta({"anthropic-beta": "oauth-2025-04-20"}, True)
    assert out is not None
    assert out["anthropic-beta"] == (
        f"oauth-2025-04-20,{INTERLEAVED_THINKING_BETA}"
    )


# === Layer 3: Anthropic request integration (mock client) ==================


@dataclass
class _MockFinalMessage:
    stop_reason: str = "end_turn"


@dataclass
class _MockResponse:
    status_code: int = 200
    headers: dict = field(default_factory=lambda: {"x-test": "1"})


class _MockStream:
    def __init__(self, response: Any = None) -> None:
        self.response = response

    async def __aenter__(self) -> _MockStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    async def get_final_message(self) -> _MockFinalMessage:
        return _MockFinalMessage()


class _MockMessages:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    def stream(self, **params: Any) -> _MockStream:
        self._captured["params"] = params
        return _MockStream(response=_MockResponse())


class _MockAnthropicClient:
    def __init__(self, captured: dict) -> None:
        self.messages = _MockMessages(captured)


async def _capture_anthropic_params(
    model: Model, reasoning: str | None
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    opts = SimpleStreamOptions(
        api_key="sk-test",
        client=_MockAnthropicClient(captured),
        reasoning=reasoning,
    )
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    return captured["params"]


async def test_anthropic_adaptive_uses_thinking_level_map() -> None:
    # thinking_level_map remaps the REQUESTED level → proves the map (not the
    # coarse fallback) drives output_config end-to-end through stream_anthropic.
    model = _ant_model(
        id="claude-opus-4-6",
        reasoning=True,
        max_tokens=64000,
        thinking_level_map={"high": "max"},
    )
    params = await _capture_anthropic_params(model, "high")
    assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert params["output_config"] == {"effort": "max"}


async def test_anthropic_adaptive_fallback_effort_without_map() -> None:
    model = _ant_model(id="claude-opus-4-7", reasoning=True, max_tokens=64000)
    params = await _capture_anthropic_params(model, "medium")
    # No map key for "medium" → coarse fallback "medium".
    assert params["output_config"] == {"effort": "medium"}


async def test_anthropic_budget_emits_budget_tokens() -> None:
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    params = await _capture_anthropic_params(model, "medium")
    assert params["thinking"]["type"] == "enabled"
    assert params["thinking"]["budget_tokens"] == 8192
    assert params["max_tokens"] == 64000


async def test_resolve_thinking_xhigh_differs_from_high_on_a_budget_model() -> None:
    # End-to-end through stream_anthropic: the table reaches the request object.
    # The row must OFFER xhigh (an explicit ``thinking_level_map`` key, per
    # ``get_supported_thinking_levels``) — since beta2 ``stream_anthropic``
    # clamps the level against the row first, like the other five adapters.
    model = _ant_model(
        id="claude-3-7-sonnet",
        reasoning=True,
        max_tokens=64000,
        thinking_level_map={"xhigh": "xhigh"},
    )
    high = await _capture_anthropic_params(model, "high")
    xhigh = await _capture_anthropic_params(model, "xhigh")
    assert high["thinking"]["budget_tokens"] == 16384
    assert xhigh["thinking"]["budget_tokens"] == 32768


async def test_xhigh_on_a_row_that_does_not_offer_it_sends_highs_budget() -> None:
    """#250 beta2: the 32768 row must not escape onto the other 252 rows.

    ``aelix --thinking xhigh --model anthropic/claude-opus-4-1`` is accepted —
    ``cli/args.py`` validates the spelling, not the model — and neither
    ``cli/entry.py`` nor ``harness/core.py`` clamps against the row, so an
    unoffered level reaches the adapter. ``claude-opus-4-1`` caps output at
    32000: without the adapter-side clamp the carve left the visible answer
    exactly ``_MIN_OUTPUT_TOKENS`` (measured on the pre-fix branch:
    ``budget_tokens`` 30976 / room 1024, against 16384 / 15616 on 0985fcf).
    28 catalog rows have a cap in that window and 46 more are clamped into it
    by ``_effective_output_cap``.
    """

    model = get_model("anthropic", "claude-opus-4-1")
    assert model is not None
    assert "xhigh" not in get_supported_thinking_levels(model)
    params = await _capture_anthropic_params(model, "xhigh")
    assert params["thinking"]["budget_tokens"] == 16384
    assert params["max_tokens"] - params["thinking"]["budget_tokens"] == 15616


async def test_adaptive_model_xhigh_unchanged() -> None:
    # Control for #250: the adaptive path was already right, so the budget row
    # must not leak into it — no budget_tokens, effort from thinkingLevelMap.
    model = _ant_model(
        id="claude-opus-4-6",
        reasoning=True,
        max_tokens=64000,
        thinking_level_map={"xhigh": "max"},
    )
    params = await _capture_anthropic_params(model, "xhigh")
    assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert params["output_config"] == {"effort": "max"}
    assert "budget_tokens" not in params["thinking"]


async def test_anthropic_off_disables_thinking() -> None:
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    params = await _capture_anthropic_params(model, None)
    assert params["thinking"] == {"type": "disabled"}


async def test_anthropic_non_reasoning_omits_thinking() -> None:
    model = _ant_model(id="claude-haiku", reasoning=False, max_tokens=8192)
    params = await _capture_anthropic_params(model, "high")
    assert "thinking" not in params


# === Layer 3: interleaved-thinking beta header =============================


async def test_anthropic_budget_sets_interleaved_beta(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    opts = SimpleStreamOptions(api_key="sk-test", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    headers = captured["default_headers"] or {}
    assert INTERLEAVED_THINKING_BETA in headers.get("anthropic-beta", "")


async def test_anthropic_adaptive_skips_interleaved_beta(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-opus-4-7", reasoning=True, max_tokens=64000)
    opts = SimpleStreamOptions(api_key="sk-test", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    headers = captured["default_headers"] or {}
    assert INTERLEAVED_THINKING_BETA not in headers.get("anthropic-beta", "")


async def test_anthropic_oauth_path_appends_interleaved_beta(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-3-7-sonnet", reasoning=True, max_tokens=64000)
    # OAuth token (sk-ant-oat…) routes through the oauth header branch.
    opts = SimpleStreamOptions(api_key="sk-ant-oat-budget", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    beta = (captured["default_headers"] or {}).get("anthropic-beta", "")
    assert "oauth-2025-04-20" in beta
    assert INTERLEAVED_THINKING_BETA in beta


async def test_anthropic_non_reasoning_omits_interleaved_beta(
    monkeypatch: Any,
) -> None:
    # Deliberate narrower scope than pi: non-reasoning models get no interleaved
    # beta (aelix gates it on active budget-thinking, preserving the
    # caller-anthropic-beta-wins contract — see ADR-0135).
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> _MockAnthropicClient:
        captured["default_headers"] = kwargs.get("default_headers")
        return _MockAnthropicClient({})

    monkeypatch.setattr(
        "aelix_ai.providers.anthropic.create_async_client", fake_create
    )
    model = _ant_model(id="claude-haiku", reasoning=False, max_tokens=8192)
    opts = SimpleStreamOptions(api_key="sk-test", reasoning="high")
    async for _ in stream_anthropic(model, Context(), opts):
        pass
    beta = (captured["default_headers"] or {}).get("anthropic-beta", "")
    assert INTERLEAVED_THINKING_BETA not in beta
