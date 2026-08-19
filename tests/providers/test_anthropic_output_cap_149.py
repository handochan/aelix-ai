"""#149 — anthropic-messages must not request more output than the window holds.

``max_tokens`` is MANDATORY in the Messages API, so this adapter cannot use the
openai-completions remedy (#144), which *omits* a cap that does not fit. The
remedy here is a CLAMP, and its trigger was chosen from a live measurement
rather than by analogy — see ``anthropic._effective_output_cap``.

Measured against the real endpoint on 2026-08-10:

* ``max_tokens=1000000`` on claude-opus-4-7 → 400 "max_tokens: 1000000 > 128000,
  which is the maximum allowed number of output tokens".
* a 142514-token prompt with ``max_tokens=64000`` on claude-haiku-4-5 (window
  200000, sum 206514) → **ACCEPTED**. Anthropic does NOT enforce
  ``prompt + max_tokens <= context_window``.

That second result is why the clamp fires ONLY on a self-inconsistent catalog
row (``maxTokens >= contextWindow``). A blanket prompt-aware clamp would shrink
``max_tokens`` on long prompts for well-behaved models and truncate answers the
API would have produced — a regression bought for nothing.

FOLLOW-UP (2026-08-10, second pass). The window arithmetic ALONE does not fix the
blocker. An independent verifier ran the same live probe with a realistic gap —
``ctx == max == 200000`` on claude-opus-4-7 — and the clamped request STILL 400'd:

    SENT max_tokens=198962 → 400 "max_tokens: 198962 > 128000, which is the
    maximum allowed number of output tokens for claude-opus-4-7"
    (req_011CduShvbmQgGfWwHPuc58Z)

The first pass passed its own live proof only because its synthetic row's gap
(1000) was smaller than ``OUTPUT_CAP_MARGIN_TOKENS`` (1024) — by construction, not
because the rule worked. The lesson is in ``_UNSAT_ABSOLUTE_OUTPUT_CEILING``: an
UNSAT row has already proven its ``maxTokens`` wrong, so nothing may be derived
from it, and ``context_window`` never encoded the per-model output ceiling
(claude-opus-4-7's real ceiling is 128000 against a 200000 window — a number
nowhere in the row). The clamp therefore also applies a conservative absolute.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

import pytest
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.providers._anthropic_transforms import resolve_anthropic_thinking
from aelix_ai.providers._token_estimate import (
    estimate_payload_tokens,
    estimate_text_tokens,
)
from aelix_ai.providers.anthropic import (
    _UNSAT_ABSOLUTE_OUTPUT_CEILING,
    _effective_output_cap,
    stream_anthropic,
)
from aelix_ai.streaming import Context, Model, SimpleStreamOptions

# ── Catalog rows, verbatim from ``aelix_ai/models_generated.json`` ──────────

# DEFAULT_MODEL_PER_PROVIDER["fireworks"]; contextWindow == maxTokens == 262000.
# One of 36 UNSAT anthropic-messages rows out of 264.
FIREWORKS_UNSAT = dict(
    id="accounts/fireworks/models/kimi-k2p6",
    provider="fireworks",
    base_url="https://api.fireworks.ai/inference",
    context_window=262000,
    max_tokens=262000,
    reasoning=True,
)

# DEFAULT_MODEL_PER_PROVIDER["anthropic"]; generous and self-consistent.
OPUS_47 = dict(
    id="claude-opus-4-7",
    provider="anthropic",
    base_url="https://api.anthropic.com",
    context_window=1000000,
    max_tokens=128000,
    reasoning=True,
)

# Measured live, not read off the catalog: claude-opus-4-7 rejects 128001 with
# "max_tokens: 128001 > 128000, which is the maximum allowed number of output
# tokens". Against a 200000-token window that ceiling is 128000 — the number an
# UNSAT row shaped ``ctx == max == 200000`` does not contain.
REAL_OPUS_47_CEILING = 128000


def _model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(api="anthropic-messages", input=["text"])
    base.update(kwargs)
    return Model(**base)


def _context(text: str = "Reply with exactly: OK") -> Context:
    return Context(messages=[UserMessage(content=[TextContent(text=text)])])


# ── Mock SDK client (captures the params that would go on the wire) ─────────


@dataclass
class _MockFinalMessage:
    stop_reason: str = "end_turn"


@dataclass
class _MockResponse:
    status_code: int = 200
    headers: dict = field(default_factory=dict)


class _MockStream:
    def __init__(self) -> None:
        self.response = _MockResponse()

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
        self._captured.update(params)
        return _MockStream()


class _MockClient:
    def __init__(self, captured: dict) -> None:
        self.messages = _MockMessages(captured)


async def _capture(model: Model, ctx: Context, **opt_kwargs: Any) -> dict:
    """Drive the real adapter and return the params handed to the SDK."""

    captured: dict = {}
    opts = SimpleStreamOptions(
        api_key="sk-test", client=_MockClient(captured), **opt_kwargs
    )
    async for _ in stream_anthropic(model, ctx, opts):
        pass
    return captured


# ── The blocker: an UNSAT row must become satisfiable ──────────────────────


@pytest.mark.asyncio
async def test_unsat_row_is_clamped_to_something_satisfiable() -> None:
    """The fireworks first-run default must not ask for the whole window."""

    model = _model(**FIREWORKS_UNSAT)
    params = await _capture(model, _context())

    sent = params["max_tokens"]
    # Pre-fix this was 262000 — equal to the context window, leaving zero room
    # for the prompt, which is the identical shape of the Wave 1 OpenRouter 400.
    assert sent < model.context_window, (
        f"max_tokens {sent} must be strictly under the {model.context_window} "
        "context window"
    )
    prompt_estimate = estimate_payload_tokens(_context())
    assert sent + prompt_estimate <= model.context_window


@pytest.mark.asyncio
async def test_unsat_row_leaves_room_for_the_actual_prompt() -> None:
    """A big prompt shrinks the clamped cap further; it never goes negative."""

    big = "word " * 40_000  # ~50K tokens by the shared estimator
    model = _model(**FIREWORKS_UNSAT)
    params = await _capture(model, _context(big))

    sent = params["max_tokens"]
    assert sent + estimate_payload_tokens(_context(big)) <= model.context_window
    assert sent >= 4096  # the floor keeps the thinking math well-formed


@pytest.mark.asyncio
async def test_the_case_the_window_bound_alone_could_not_fix() -> None:
    """The verifier's failing probe, reproduced as a unit test.

    ``ctx == max == 200000`` on claude-opus-4-7. The gap between the window and
    the endpoint's REAL ceiling (128000) is 72000 — 70x the margin — so this
    cannot pass by construction the way a 1000-token gap did. Under the
    window-only rule the adapter sent 198962 and got a 400; the request now goes
    out under the measured ceiling.
    """

    model = _model(
        id="claude-opus-4-7", provider="anthropic",
        base_url="https://api.anthropic.com",
        context_window=200000, max_tokens=200000, reasoning=True,
    )
    params = await _capture(model, _context())

    window_only = model.context_window - estimate_payload_tokens(_context()) - 1024
    assert window_only > REAL_OPUS_47_CEILING, (
        "the synthetic row must actually exercise the gap the fix is about"
    )
    assert params["max_tokens"] <= REAL_OPUS_47_CEILING, (
        f"sent {params['max_tokens']}; live 400 at 198962 "
        "(req_011CduShvbmQgGfWwHPuc58Z)"
    )
    assert params["max_tokens"] == _UNSAT_ABSOLUTE_OUTPUT_CEILING


def test_an_unsat_row_never_exceeds_the_absolute_ceiling_at_any_window() -> None:
    """No window, however large, can talk the clamp past the ceiling.

    ``context_window`` is not evidence about the output ceiling — that is the
    whole finding. The catalog's biggest UNSAT rows (the xai/grok-4.20 family at
    ``ctx == max == 2000000``) would otherwise ask for 1998976 output tokens.
    """

    for window in (128000, 131072, 200000, 262000, 1000000, 2000000):
        model = _model(
            id="x", provider="p", base_url="https://h",
            context_window=window, max_tokens=window,
        )
        assert _effective_output_cap(model, _context()) <= (
            _UNSAT_ABSOLUTE_OUTPUT_CEILING
        ), f"window {window} escaped the ceiling"


def test_every_unsat_catalog_row_lands_under_the_ceiling() -> None:
    """All 55 UNSAT anthropic-messages rows, not just the blocker.

    Was 36 before #172. The 19 new ones are all ``vercel-ai-gateway``, whose
    every row speaks ``anthropic-messages``, and each arrived with upstream's
    own ``output == context`` — the shape ``xai/grok-4.5`` already shipped. The
    count assertion sits BEFORE the loop, so a stale number does not weaken
    this test, it silently stops running it.
    """

    catalog = json.loads((files("aelix_ai") / "models_generated.json").read_text())
    unsat = [
        (provider, model_id, row)
        for provider, rows in catalog.items()
        for model_id, row in rows.items()
        if row.get("api") == "anthropic-messages"
        and row.get("maxTokens")
        and row.get("contextWindow")
        and row["maxTokens"] >= row["contextWindow"]
    ]
    assert len(unsat) == 55, "the UNSAT population moved; re-read the evidence"
    for provider, model_id, row in unsat:
        model = _model(
            id=model_id, provider=provider, base_url="https://h",
            context_window=row["contextWindow"], max_tokens=row["maxTokens"],
        )
        cap = _effective_output_cap(model, _context())
        assert cap <= _UNSAT_ABSOLUTE_OUTPUT_CEILING, f"{provider}/{model_id}"
        assert cap < row["contextWindow"]


def test_the_window_bound_still_wins_when_it_is_the_smaller_one() -> None:
    """The ceiling is a second bound, not a replacement for the first."""

    model = _model(**FIREWORKS_UNSAT)
    # A prompt big enough that the window leaves less than the ceiling.
    big = "word " * 200_000
    cap = _effective_output_cap(model, _context(big))
    assert cap < _UNSAT_ABSOLUTE_OUTPUT_CEILING
    assert cap + estimate_payload_tokens(_context(big)) <= model.context_window


def test_the_absolute_ceiling_matches_the_evidence_it_was_chosen_from() -> None:
    """Pin the two measurements behind the number, not just the number.

    A SATISFIABLE row's ``maxTokens`` IS the endpoint's real ceiling — measured
    5/5 live by sending ``catalog maxTokens + 1`` (claude-opus-4-5-20251101 /
    claude-sonnet-4-5-20250929 / claude-haiku-4-5-20251001 → "64001 > 64000";
    claude-opus-4-7 / claude-sonnet-4-6 → "128001 > 128000"). So the satisfiable
    rows are the trustworthy evidence, and 32000 is (a) their lower quartile and
    (b) an actual first-party Anthropic ceiling.
    """

    catalog = json.loads((files("aelix_ai") / "models_generated.json").read_text())
    sat = sorted(
        row["maxTokens"]
        for rows in catalog.values()
        for row in rows.values()
        if row.get("api") == "anthropic-messages"
        and row.get("maxTokens")
        and (not row.get("contextWindow") or row["maxTokens"] < row["contextWindow"])
    )
    # 228 → 230 when two ``claude-opus-5`` rows were hand-added, → 309 when
    # #172 added 422. RE-DERIVED at each step rather than nudged, which is the
    # whole point of asserting the population size first: p25 is now ``sat[77]``
    # and it is STILL 32000. The margin also stopped being thin — ``sat[74:81]``
    # are all 32000, where before p25 sat two rows above a pair of 24000s.
    assert len(sat) == 309
    assert sat[len(sat) // 4] == _UNSAT_ABSOLUTE_OUTPUT_CEILING, (
        "32000 is the p25 of the trustworthy rows; if the catalog moved, "
        "re-derive the ceiling rather than nudging this assertion"
    )
    first_party_at_the_ceiling = {
        model_id
        for model_id, row in catalog["anthropic"].items()
        if row.get("maxTokens") == _UNSAT_ABSOLUTE_OUTPUT_CEILING
    }
    assert first_party_at_the_ceiling == {
        "claude-opus-4-0",
        "claude-opus-4-1",
        "claude-opus-4-1-20250805",
        "claude-opus-4-20250514",
    }
    # 32768 — the obvious power of two — is ABOVE that real ceiling, so a row
    # shaped like those models would still 400 under it.
    assert _UNSAT_ABSOLUTE_OUTPUT_CEILING < 32768
    # A LITERAL upper bound, deliberately not derived from anything above. The
    # two "never exceeds the ceiling" tests compare the cap against this same
    # constant, so they move with it and would pass at any value; the p25
    # assertion pins it but only while the catalog's own distribution holds. This
    # line is the one that fails if someone raises the number, and raising it is
    # the dangerous direction: every row this fires on is a gateway route whose
    # true output ceiling nobody here can measure, and the failure mode of "too
    # high" is a hard 400 while "too low" merely truncates a long answer.
    assert _UNSAT_ABSOLUTE_OUTPUT_CEILING <= 32000


def test_clamped_cap_floor_is_never_below_the_thinking_minimum() -> None:
    """A prompt that nearly fills the window still yields a valid request.

    Reaching the floor means the request is doomed anyway (the provider's own
    "prompt is too long" is the honest error); what matters is that we do not
    trade that error for a self-inflicted ``budget_tokens >= max_tokens`` 400.
    """

    model = _model(**FIREWORKS_UNSAT)
    # A prompt larger than the whole window.
    cap = _effective_output_cap(model, _context("x" * 2_000_000))
    assert cap == 4096
    _extra, max_tokens, _beta = resolve_anthropic_thinking(
        model, "medium", cap, max_tokens_ceiling=cap
    )
    budget = _extra["thinking"]["budget_tokens"]
    assert 0 < budget < max_tokens, "budget_tokens must stay under max_tokens"


# ── The thing that must NOT change ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_generous_model_is_unchanged() -> None:
    """claude-opus-4-7 keeps its full catalog cap — the measured non-rule."""

    model = _model(**OPUS_47)
    params = await _capture(model, _context())
    assert params["max_tokens"] == 128000


@pytest.mark.asyncio
async def test_generous_model_is_unchanged_even_on_a_long_prompt() -> None:
    """The regression guard for the measurement.

    prompt(~250K) + 128000 exceeds nothing Anthropic enforces, and the live
    haiku probe proved ``prompt + max_tokens > context_window`` is ACCEPTED. A
    prompt-aware clamp would cut this to ~750K and silently shorten answers.
    """

    big = "word " * 200_000
    model = _model(**OPUS_47)
    params = await _capture(model, _context(big))
    assert params["max_tokens"] == 128000


def test_satisfiable_rows_are_returned_verbatim() -> None:
    """Every self-consistent row is byte-identical at any prompt length."""

    for ctx_window, cap in ((200000, 64000), (1000000, 128000), (128000, 8192)):
        model = _model(
            id="x", provider="p", base_url="https://h", context_window=ctx_window,
            max_tokens=cap,
        )
        assert _effective_output_cap(model, _context()) == cap
        assert _effective_output_cap(model, _context("word " * 20_000)) == cap


def test_unknown_context_window_keeps_the_catalog_cap() -> None:
    """No window means no arithmetic is possible — do not invent a clamp."""

    model = _model(
        id="x", provider="p", base_url="https://h", context_window=0, max_tokens=8192
    )
    assert _effective_output_cap(model, _context()) == 8192


def test_no_max_tokens_keeps_the_4096_fallback() -> None:
    model = _model(
        id="x", provider="p", base_url="https://h", context_window=200000,
        max_tokens=0,
    )
    assert _effective_output_cap(model, _context()) == 4096


# ── opts.max_tokens still wins (P0 #6) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_options_max_tokens_still_wins_on_an_unsat_row() -> None:
    """The compaction summarizer's explicit cap is never second-guessed."""

    model = _model(**{**FIREWORKS_UNSAT, "reasoning": False})
    params = await _capture(model, _context(), max_tokens=13107)
    assert params["max_tokens"] == 13107


@pytest.mark.asyncio
async def test_options_max_tokens_still_wins_on_a_generous_row() -> None:
    model = _model(**{**OPUS_47, "reasoning": False})
    params = await _capture(model, _context(), max_tokens=13107)
    assert params["max_tokens"] == 13107


# ── Thinking-budget behaviour is pinned ────────────────────────────────────


def test_thinking_budget_unchanged_for_a_generous_model() -> None:
    """The clamp is a no-op here, so the budget math is bit-identical."""

    model = _model(**OPUS_47)
    cap = _effective_output_cap(model, _context())
    with_ceiling = resolve_anthropic_thinking(
        model, "medium", cap, max_tokens_ceiling=cap
    )
    without = resolve_anthropic_thinking(model, "medium", model.max_tokens or 0)
    assert with_ceiling == without


def test_thinking_ceiling_defaults_to_the_catalog_cap() -> None:
    """Omitting ``max_tokens_ceiling`` preserves pi parity for every caller."""

    model = _model(**FIREWORKS_UNSAT)
    _extra, max_tokens, _beta = resolve_anthropic_thinking(model, "medium", 1000)
    # simple-options.ts:26-50 — min(base + budget, model.maxTokens).
    assert max_tokens == 1000 + 8192


def test_clamped_ceiling_stops_the_budget_adding_itself_back_on() -> None:
    """The reason the ceiling is threaded through, not just the base.

    ``adjust_max_tokens_for_thinking`` computes ``min(base + budget, clamp)``.
    With the RAW catalog clamp (262000) the carved thinking budget adds itself
    straight back on top of an already-clamped base, so the request leaves under
    a cap the adapter never agreed to.
    """

    model = _model(**FIREWORKS_UNSAT)
    cap = _effective_output_cap(model, _context())

    _e1, unclamped, _b1 = resolve_anthropic_thinking(model, "medium", cap)
    assert unclamped > cap  # the trap, if the ceiling is skipped

    _e2, clamped, _b2 = resolve_anthropic_thinking(
        model, "medium", cap, max_tokens_ceiling=cap
    )
    assert clamped == cap
    assert clamped < model.context_window


@pytest.mark.asyncio
async def test_thinking_request_on_an_unsat_row_is_internally_consistent() -> None:
    model = _model(**FIREWORKS_UNSAT)
    params = await _capture(model, _context(), reasoning="medium")
    budget = params["thinking"]["budget_tokens"]
    assert budget < params["max_tokens"] < model.context_window


# ── The shared estimator ───────────────────────────────────────────────────


def test_estimator_charges_non_ascii_per_character_not_per_string() -> None:
    """The known LOW from #144, fixed here because this change reuses it.

    The original branched on ``str.isascii()`` for the WHOLE string, so one
    em-dash in a 40 KB ASCII file charged the entire file at 1 token/char.
    """

    ascii_only = "a" * 40_000
    with_one_em_dash = ascii_only + "—"

    assert estimate_text_tokens(ascii_only) == 10_000
    # 40000 ASCII chars (10000 tokens) + 1 non-ASCII char (1 token).
    assert estimate_text_tokens(with_one_em_dash) == 10_001
    # Pre-fix this was 40001 — a 4x over-estimate from a single character.


def test_estimator_keeps_the_fail_closed_bias_on_real_non_ascii() -> None:
    """CJK still charges 1 token/char; only the contagion was removed."""

    assert estimate_text_tokens("안녕하세요") == 5


def test_estimator_walks_dataclasses() -> None:
    """The anthropic adapter has no built dict payload at decision time."""

    ctx = _context("hello world")
    assert estimate_payload_tokens(ctx) >= estimate_text_tokens("hello world")
