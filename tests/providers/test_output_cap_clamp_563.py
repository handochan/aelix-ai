"""#563 - every output cap an adapter sends fits what the context window still holds.

A request asks for ``prompt + max_tokens`` of the window. Endpoints that check that
sum (MiniMax's Anthropic-compatible API in pi #6061, the OpenAI-style endpoints
whose "you requested ... in the completion" errors ``utils/overflow.py`` reads)
reject a long conversation's request once ``prompt > window - max_tokens``, while
auto-compaction waits for ``prompt > window - 16384``. With a 32000-token cap that
is a 15616-token band in which every turn fails - measured on main ``27fa21c5``
against a local recorder that enforces the sum (``.omc/probes/b4-a-live/563/``):
turns 4-6 failed with "input length and `max_tokens` exceed context limit", and
after the compaction the error path finally ran, the kept 35101 tokens + 32000
still did not fit, so every later turn failed too.

pi clamps every request (``clampMaxTokensToContext``,
``packages/ai/src/api/simple-options.ts:18-22`` at ``fdce48eb4``, pi ``09f105957``):
``min(cap, context_window - estimateContextTokens(context) - 4096)``, at least 1,
and again after the thinking budget in its Anthropic adapter. These rows pin that
for every adapter that sends a cap, the estimate it reads, and the overflow
errors the sum check produces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from aelix_ai.messages import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.providers._google_shared import build_google_params
from aelix_ai.providers._openai_compat import get_compat
from aelix_ai.providers._token_estimate import (
    CONTEXT_SAFETY_TOKENS,
    ESTIMATED_IMAGE_TOKENS,
    clamp_max_tokens_to_context,
    estimate_context_text_tokens,
    estimate_context_tokens,
    estimate_message_tokens,
)
from aelix_ai.providers.anthropic import stream_anthropic
from aelix_ai.providers.openai_completions import build_params as completions_params
from aelix_ai.providers.openai_responses import build_params as responses_params
from aelix_ai.streaming import Context, Model, SimpleStreamOptions
from aelix_ai.utils.overflow import is_context_overflow

WINDOW = 60_000
CAP = 32_000

# --- helpers ---------------------------------------------------------------


def _anthropic_model(**kw: Any) -> Model:
    base: dict[str, Any] = dict(
        api="anthropic-messages",
        id="probe-model",
        provider="probe",
        base_url="http://127.0.0.1:1",
        input=["text"],
        context_window=WINDOW,
        max_tokens=CAP,
    )
    base.update(kw)
    return Model(**base)


def _answered(tokens: int, *, timestamp: float | None = None, stop: str = "end_turn") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text="ok")],
        stop_reason=stop,
        usage={"input": tokens - 5, "output": 5, "total_tokens": tokens},
        timestamp=timestamp,
    )


def _user(text: str, *, timestamp: float | None = None) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)], timestamp=timestamp)


def _long_conversation(usage_tokens: int, trailing_chars: int) -> Context:
    """A conversation whose last answer reported ``usage_tokens``, then a new prompt."""

    return Context(
        system_prompt="You are a probe.",
        messages=[
            _user("first"),
            _answered(usage_tokens),
            _user("x" * trailing_chars),
        ],
    )


@dataclass
class _FinalMessage:
    stop_reason: str = "end_turn"


@dataclass
class _Response:
    status_code: int = 200
    headers: dict = field(default_factory=dict)


class _Stream:
    def __init__(self) -> None:
        self.response = _Response()

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover

    async def get_final_message(self) -> _FinalMessage:
        return _FinalMessage()


class _Messages:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    def stream(self, **params: Any) -> _Stream:
        self._captured.update(params)
        return _Stream()


class _Client:
    def __init__(self, captured: dict) -> None:
        self.messages = _Messages(captured)


async def _anthropic_request(model: Model, ctx: Context, **opt_kwargs: Any) -> dict:
    captured: dict = {}
    opts = SimpleStreamOptions(api_key="sk-test-fake", client=_Client(captured), **opt_kwargs)
    async for _ in stream_anthropic(model, ctx, opts):
        pass
    return captured


def _room(ctx: Context, window: int = WINDOW) -> int:
    return window - estimate_context_tokens(ctx) - CONTEXT_SAFETY_TOKENS


# --- the estimate ----------------------------------------------------------


def test_estimate_anchors_on_the_last_answers_usage_plus_what_came_after() -> None:
    ctx = _long_conversation(35_102, 32_000)
    assert estimate_context_tokens(ctx) == 35_102 + estimate_context_text_tokens("x" * 32_000)


def test_estimate_without_usage_counts_system_prompt_tools_and_messages() -> None:
    @dataclass
    class _Tool:
        name: str = "bash"
        description: str = "Run a shell command."
        parameters: dict = field(default_factory=lambda: {"type": "object"})

    ctx = Context(system_prompt="s" * 400, messages=[_user("u" * 800)], tools=[_Tool()])
    assert estimate_context_tokens(ctx) > 100 + 200


@pytest.mark.parametrize("stop", ["error", "aborted"])
def test_a_failed_answer_does_not_anchor(stop: str) -> None:
    ctx = Context(messages=[_user("a"), _answered(10_000), _user("b"), _answered(50_000, stop=stop)])
    # The failed answer's usage would say 50000; the last good one says 10000.
    assert estimate_context_tokens(ctx) == 10_000 + estimate_message_tokens(
        ctx.messages[2]
    ) + estimate_message_tokens(ctx.messages[3])


def test_an_answer_that_reported_no_usage_does_not_anchor() -> None:
    zero = AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn", usage={})
    ctx = Context(messages=[_user("a"), _answered(10_000), zero])
    assert estimate_context_tokens(ctx) == 10_000 + estimate_message_tokens(zero)


def test_kept_answers_after_a_compaction_summary_do_not_anchor() -> None:
    """pi: a newer prefix message (the compaction summary) voids older usage.

    aelix stamps a turn's messages in seconds (``loop.py``) and a rebuilt
    compaction summary in milliseconds (``session/context.py``); the kept answer
    still carries the pre-compaction usage. Anchoring on it would cut the next cap
    to almost nothing (here to 1).
    """

    summary_ms = 1_761_000_000_000.0  # 2025-10, in milliseconds
    kept_s = 1_760_999_000.0  # earlier, in seconds
    ctx = Context(
        messages=[
            _user("summary of earlier turns", timestamp=summary_ms),
            _user("kept prompt", timestamp=kept_s),
            _answered(58_000, timestamp=kept_s + 1),
            _user("next prompt", timestamp=kept_s + 2),
        ]
    )
    estimate = estimate_context_tokens(ctx)
    assert estimate < 100, f"stale pre-compaction usage anchored: {estimate}"
    # And a fresh answer after the summary anchors again.
    fresh = _answered(30_000, timestamp=summary_ms / 1000.0 + 60)
    ctx.messages.extend([fresh, _user("after")])
    assert estimate_context_tokens(ctx) == 30_000 + estimate_message_tokens(ctx.messages[-1])


def test_messages_without_timestamps_still_anchor() -> None:
    ctx = _long_conversation(20_000, 400)
    assert estimate_context_tokens(ctx) == 20_000 + 115  # ceil(400 / 3.5), pi's divisor


def test_an_image_counts_flat_not_by_its_base64_length() -> None:
    image = ImageContent(mime_type="image/png", data="A" * 2_000_000)
    msg = ToolResultMessage(tool_call_id="t", content=[TextContent(text="shot"), image])
    assert estimate_message_tokens(msg) == ESTIMATED_IMAGE_TOKENS + estimate_context_text_tokens("shot")
    assert ESTIMATED_IMAGE_TOKENS == 1372  # pi's 4800 characters at 3.5 a token


def test_tool_call_arguments_are_counted() -> None:
    call = ToolCallContent(tool_call_id="t", tool_name="write", input={"content": "y" * 4000})
    msg = AssistantMessage(content=[call], stop_reason="toolUse")
    assert estimate_message_tokens(msg) >= 1000


def test_text_counts_at_pis_three_and_a_half_characters_a_token() -> None:
    """Review round 2: pi's ``estimate.ts`` divides by 3.5, not this module's 4.

    A non-ASCII character is a whole token, never less than pi's count.
    """

    assert estimate_context_text_tokens("x" * 7) == 2
    assert estimate_context_text_tokens("x" * 8) == 3
    assert estimate_context_text_tokens("x" * 150_000) == 42_858
    assert estimate_context_text_tokens("東京") == 2


def test_tools_without_an_anchor_count_their_json_at_pis_rate() -> None:
    """pi ``estimateToolsTokens``: ``JSON.stringify(tools)`` at 3.5 characters a token."""

    import json

    @dataclass
    class _Tool:
        name: str = "bash"
        description: str = "d" * 700
        parameters: dict = field(default_factory=lambda: {"type": "object", "properties": {}})

    tool = _Tool()
    pi_json = json.dumps(
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
        separators=(",", ":"),
    )
    ctx = Context(messages=[], tools=[tool])
    assert estimate_context_tokens(ctx) >= -(-len(pi_json) * 2 // 7)


# --- the clamp -------------------------------------------------------------


def test_clamp_is_min_of_the_cap_and_the_room() -> None:
    model = _anthropic_model()
    ctx = _long_conversation(35_102, 32_000)
    assert clamp_max_tokens_to_context(model, ctx, CAP) == _room(ctx)
    assert clamp_max_tokens_to_context(model, Context(messages=[_user("hi")]), CAP) == CAP


def test_clamp_keeps_pis_4096_tokens_of_margin() -> None:
    """A literal, so a smaller margin cannot pass by moving the constant."""

    model = _anthropic_model()
    ctx = Context(messages=[_user("a"), _answered(40_000)])
    assert clamp_max_tokens_to_context(model, ctx, CAP) == WINDOW - 40_000 - 4096 == 15_904


def test_clamp_reads_pis_estimate_where_four_characters_a_token_would_overflow() -> None:
    """Review round 2 (cross F4): the cap left room for pi's estimate, not a lower one.

    An answer reported 20000 tokens, then 150000 ASCII characters; window 90000,
    cap 32000. pi's own ``estimateContextTokens`` and clamp at ``fdce48eb4`` give an
    estimate of 62858 and a cap of 23046 (``.omc/probes/b4-a-live/r2``,
    ``pi_clamp.ts``). Counted at 4 characters a token the estimate was 57500 and the
    cap 28404: 62858 + 28404 = 91262 > 90000, a request over the window where pi's
    fits.
    """

    model = _anthropic_model(context_window=90_000)
    ctx = Context(messages=[_user("seed"), _answered(20_000), _user("x" * 150_000)])
    assert estimate_context_tokens(ctx) == 62_858
    cap = clamp_max_tokens_to_context(model, ctx, CAP)
    assert cap == 23_046
    assert 62_858 + cap <= 90_000 - CONTEXT_SAFETY_TOKENS


def test_clamp_never_goes_below_one() -> None:
    model = _anthropic_model()
    assert clamp_max_tokens_to_context(model, _long_conversation(70_000, 10), CAP) == 1


def test_clamp_with_no_known_window_keeps_the_cap() -> None:
    model = _anthropic_model(context_window=0)
    assert clamp_max_tokens_to_context(model, _long_conversation(900_000, 10), CAP) == CAP


# --- anthropic-messages ----------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_long_conversation_asks_only_for_what_fits() -> None:
    """The probe's turn 4: 35102 reported + a 32000-character prompt, cap 32000."""

    ctx = _long_conversation(35_102, 32_000)
    sent = (await _anthropic_request(_anthropic_model(), ctx))["max_tokens"]
    assert sent == _room(ctx)
    assert estimate_context_tokens(ctx) + sent <= WINDOW - CONTEXT_SAFETY_TOKENS


@pytest.mark.asyncio
async def test_anthropic_short_conversation_keeps_the_catalog_cap() -> None:
    sent = (await _anthropic_request(_anthropic_model(), Context(messages=[_user("hi")])))[
        "max_tokens"
    ]
    assert sent == CAP


@pytest.mark.asyncio
async def test_anthropic_caller_cap_is_clamped_too() -> None:
    """A compaction's summary cap (floor(0.8 * 16384)) is asked for at a full window."""

    ctx = _long_conversation(50_000, 4_000)
    sent = (await _anthropic_request(_anthropic_model(), ctx, max_tokens=13_107))["max_tokens"]
    assert sent == _room(ctx) < 13_107


@pytest.mark.asyncio
async def test_anthropic_budget_thinking_cap_includes_the_budget_and_still_fits() -> None:
    """pi re-clamps after ``adjustMaxTokensForThinking`` (anthropic-messages.ts:1004)."""

    model = _anthropic_model(id="claude-sonnet-4-5", reasoning=True, context_window=200_000, max_tokens=64_000)
    ctx = _long_conversation(150_000, 4_000)
    params = await _anthropic_request(model, ctx, reasoning="high")
    room = _room(ctx, 200_000)
    assert params["max_tokens"] == room
    budget = params["thinking"]["budget_tokens"]
    assert 1024 <= budget < params["max_tokens"]
    assert params["max_tokens"] - budget >= 1024


@pytest.mark.asyncio
async def test_anthropic_budget_thinking_turns_off_when_the_room_cannot_hold_a_budget() -> None:
    model = _anthropic_model(id="claude-sonnet-4-5", reasoning=True, context_window=200_000, max_tokens=64_000)
    ctx = _long_conversation(194_500, 400)  # room ~1300 tokens
    params = await _anthropic_request(model, ctx, reasoning="high")
    room = _room(ctx, 200_000)
    assert 0 < room < 2048
    assert params["max_tokens"] == room
    assert params["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_anthropic_adaptive_thinking_cap_fits() -> None:
    model = _anthropic_model(
        id="claude-opus-4-7", reasoning=True, context_window=200_000, max_tokens=128_000
    )
    ctx = _long_conversation(120_000, 4_000)
    params = await _anthropic_request(model, ctx, reasoning="high")
    assert params["thinking"]["type"] == "adaptive"
    assert params["max_tokens"] == _room(ctx, 200_000)


@pytest.mark.asyncio
async def test_anthropic_unsat_row_keeps_its_absolute_ceiling() -> None:
    """#149's protection survives: the window clamp alone cannot stand in for it."""

    model = _anthropic_model(context_window=262_000, max_tokens=262_000)
    sent = (await _anthropic_request(model, Context(messages=[_user("hi")])))["max_tokens"]
    assert sent == 32_000


@pytest.mark.asyncio
async def test_anthropic_every_turn_of_a_growing_conversation_fits() -> None:
    """The probe as a unit: an endpoint enforcing prompt + max_tokens <= window.

    The recorder counted 8039 tokens per turn; on ``27fa21c5`` turns 4-6 asked
    for 32000 on top of 35102, 43123 and 51145 and were rejected.
    """

    model = _anthropic_model()
    messages: list[Any] = []
    reported = 2_947  # the system prompt and tools the recorder counted
    for turn in range(1, 7):
        messages.append(_user("w" * 32_000))
        ctx = Context(system_prompt="s", messages=list(messages))
        sent = (await _anthropic_request(model, ctx))["max_tokens"]
        reported += 8_039
        assert reported + sent <= WINDOW, f"turn {turn}: {reported} + {sent} > {WINDOW}"
        messages.append(_answered(reported + 5))


# --- openai-completions ----------------------------------------------------


def _completions_model(**kw: Any) -> Model:
    base: dict[str, Any] = dict(
        api="openai-completions",
        id="probe-model",
        provider="probe",
        base_url="http://127.0.0.1:1/v1",
        input=["text"],
        context_window=WINDOW,
        max_tokens=CAP,
    )
    base.update(kw)
    return Model(**base)


def _completions_cap(params: dict[str, Any]) -> int | None:
    return params.get("max_tokens", params.get("max_completion_tokens"))


def test_completions_caller_cap_is_clamped() -> None:
    model = _completions_model()
    ctx = _long_conversation(50_000, 4_000)
    params = completions_params(
        model, ctx, SimpleStreamOptions(max_tokens=13_107), get_compat(model), "short"
    )
    assert _completions_cap(params) == _room(ctx) < 13_107


def test_completions_caller_cap_that_fits_is_sent_unchanged() -> None:
    model = _completions_model()
    params = completions_params(
        model, Context(messages=[_user("hi")]), SimpleStreamOptions(max_tokens=13_107),
        get_compat(model), "short",
    )
    assert _completions_cap(params) == 13_107


def test_completions_model_default_is_still_omitted_when_it_does_not_fit() -> None:
    """ADR-0114 / #144's omission is kept for the model's own cap (a divergence).

    The conversation's reported usage (35000) decides, not only its characters:
    the text here estimates to about 8000 tokens, which on ``27fa21c5`` let the
    32000 cap go out on top of a 43000-token request.
    """

    model = _completions_model()
    params = completions_params(model, _long_conversation(35_000, 32_000), None, get_compat(model), "short")
    assert _completions_cap(params) is None


# --- openai-responses ------------------------------------------------------


def _responses_model(**kw: Any) -> Model:
    base: dict[str, Any] = dict(
        api="openai-responses",
        id="gpt-5",
        provider="openai",
        base_url="https://api.openai.com/v1",
        context_window=WINDOW,
        max_tokens=CAP,
    )
    base.update(kw)
    return Model(**base)


def test_responses_caller_cap_is_clamped() -> None:
    ctx = _long_conversation(50_000, 4_000)
    params = responses_params(_responses_model(), ctx, SimpleStreamOptions(max_tokens=13_107))
    assert params["max_output_tokens"] == _room(ctx)


def test_responses_clamped_cap_is_never_below_sixteen() -> None:
    """OpenAI Responses rejects ``max_output_tokens`` below 16 (pi #6265)."""

    ctx = _long_conversation(59_000, 40)
    params = responses_params(_responses_model(), ctx, SimpleStreamOptions(max_tokens=13_107))
    assert params["max_output_tokens"] == 16


def test_responses_sends_no_cap_when_the_caller_gives_none() -> None:
    params = responses_params(_responses_model(), _long_conversation(50_000, 4_000), None)
    assert "max_output_tokens" not in params


# --- google ----------------------------------------------------------------


def test_google_caller_cap_is_clamped() -> None:
    model = Model(
        api="google-generative-ai", id="gemini-2.5-flash", provider="google",
        base_url="https://generativelanguage.googleapis.com", context_window=WINDOW,
        max_tokens=CAP,
    )
    ctx = _long_conversation(50_000, 4_000)
    params = build_google_params(model, ctx, max_tokens=13_107)
    assert params["config"]["maxOutputTokens"] == _room(ctx)


# --- the sum check's errors reach overflow recovery ------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Anthropic Messages API, as the probe's recorder sends it and the SDK wraps it.
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'input length and `max_tokens` exceed context limit: 35102 + 32000 > "
        "60000, decrease input length or `max_tokens` and try again'}}",
        # Hugging Face TGI.
        "Input validation error: `inputs` tokens + `max_new_tokens` must be <= 4097. "
        "Given: 4000 `inputs` tokens and 200 `max_new_tokens`",
        # pi's patterns at fdce48eb4 that aelix lacked.
        '400 {"code":"1261","message":"Prompt too long"}',
        '400 {"code":"1261","message":"Prompt exceeds max length"}',
        "400 Prompt has 256468 tokens, but the configured context size is 256000 tokens",
        "Prompt has 5,958,968 tokens, but the configured context size is 256,000 tokens",
        "Range of input length should be [1, 98304]",
    ],
)
def test_sum_and_newer_overflow_errors_are_context_overflow(text: str) -> None:
    message = AssistantMessage(content=[], stop_reason="error", error_message=text)
    assert is_context_overflow(message) is True
