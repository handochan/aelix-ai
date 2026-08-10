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
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.providers._anthropic_transforms import resolve_anthropic_thinking
from aelix_ai.providers._token_estimate import (
    estimate_payload_tokens,
    estimate_text_tokens,
)
from aelix_ai.providers.anthropic import _effective_output_cap, stream_anthropic
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
    With the RAW catalog clamp, a clamped base of 260968 plus a 8192 budget
    would resolve to 262000 — exactly the window, undoing the clamp.
    """

    model = _model(**FIREWORKS_UNSAT)
    cap = _effective_output_cap(model, _context())

    _e1, unclamped, _b1 = resolve_anthropic_thinking(model, "medium", cap)
    assert unclamped >= model.context_window  # the trap, if the ceiling is skipped

    _e2, clamped, _b2 = resolve_anthropic_thinking(
        model, "medium", cap, max_tokens_ceiling=cap
    )
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
