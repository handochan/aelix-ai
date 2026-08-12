"""The output cap must never exceed what the context window can hold.

Regression cover for the Wave 1 first-turn 400: a brand-new user onboarding with
an OpenRouter key lands on ``DEFAULT_MODEL_PER_PROVIDER["openrouter"]`` =
``moonshotai/kimi-k2.6`` (``contextWindow=262144`` / ``maxTokens=262142``) and
their very first message 400s because the request asks for 262142 output tokens
on a 262144 window.

The catalog carries two distinct shapes that both have to be handled:

- headroom 2   (``kimi-k2.6``)    — a meaningless cap; no prompt of any size fits.
- headroom 8192 (``minimax-m2``)  — a REAL cap that is merely generous; a short
  first turn fits and it only starts failing once the conversation grows.

A static threshold cannot serve both without leaving a long tail: 596 catalog
rows have ``maxTokens`` large enough to 400 once the prompt passes
``contextWindow - maxTokens``. So the cap is gated on the actual prompt size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.providers._openai_compat import get_compat
from aelix_ai.providers.openai_completions import build_params
from aelix_ai.streaming import Context, Model, SimpleStreamOptions


@dataclass
class _FakeTool:
    name: str = "bash"
    description: str = "Run a shell command."
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


def _model(**kwargs: Any) -> Model:
    base: dict[str, Any] = dict(
        api="openai-completions",
        id="moonshotai/kimi-k2.6",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        input=["text"],
    )
    base.update(kwargs)
    return Model(**base)


def _context(text: str = "Reply with exactly: ONBOARD-OK") -> Context:
    return Context(messages=[UserMessage(content=[TextContent(text=text)])])


def _emitted_cap(params: dict[str, Any]) -> int | None:
    # ``param_name``, not ``field``: this module imports ``dataclasses.field``
    # and uses it at :35, so a loop variable of that name shadows it (ruff F402,
    # and `ruff check .` is the CI lint gate).
    for param_name in ("max_tokens", "max_completion_tokens"):
        if param_name in params:
            return int(params[param_name])
    return None


# === The two catalog shapes ===============================================


def test_headroom_2_cap_dropped_on_first_turn() -> None:
    """kimi-k2.6: ctx=262144 max=262142. No prompt fits — always omit."""

    model = _model(context_window=262144, max_tokens=262142)
    params = build_params(model, _context(), None, get_compat(model), "short")
    assert _emitted_cap(params) is None


def test_headroom_8192_cap_kept_while_the_prompt_still_fits() -> None:
    """minimax-m2: ctx=204800 max=196608. A short first turn genuinely fits."""

    model = _model(
        id="minimax/minimax-m2", context_window=204800, max_tokens=196608
    )
    params = build_params(model, _context(), None, get_compat(model), "short")
    assert _emitted_cap(params) == 196608


def test_headroom_8192_cap_dropped_once_the_conversation_outgrows_it() -> None:
    """The same model must stop sending the cap before the request 400s.

    This is the case a "meaningless cap only" rule would miss: minimax would
    fail *later* instead of immediately, which looks intermittent.
    """

    model = _model(
        id="minimax/minimax-m2", context_window=204800, max_tokens=196608
    )
    # ~9k tokens of ASCII prompt — past the 8192 headroom.
    params = build_params(
        model, _context("x" * 36_000), None, get_compat(model), "short"
    )
    assert _emitted_cap(params) is None


# === The pre-existing ADR-0114 guard must keep working ====================


def test_max_tokens_equal_to_context_window_still_dropped() -> None:
    model = _model(context_window=262144, max_tokens=262144)
    params = build_params(model, _context(), None, get_compat(model), "short")
    assert _emitted_cap(params) is None


def test_max_tokens_above_context_window_still_dropped() -> None:
    model = _model(context_window=262144, max_tokens=300000)
    params = build_params(model, _context(), None, get_compat(model), "short")
    assert _emitted_cap(params) is None


# === Normal models are untouched ==========================================


def test_generous_context_model_keeps_its_cap() -> None:
    """claude-3-7-sonnet shape: ctx=200000 max=64000 — a real, usable cap."""

    model = _model(
        id="anthropic/claude-3.7-sonnet", context_window=200000, max_tokens=64000
    )
    params = build_params(model, _context(), None, get_compat(model), "short")
    assert _emitted_cap(params) == 64000


def test_unknown_context_window_keeps_the_cap() -> None:
    """``context_window == 0`` means unknown — no arithmetic is possible."""

    model = _model(context_window=0, max_tokens=4096)
    params = build_params(model, _context(), None, get_compat(model), "short")
    assert _emitted_cap(params) == 4096


# === A caller-supplied cap still wins =====================================


def test_options_max_tokens_wins_over_the_guard() -> None:
    """P0 #6: the compaction summarizer's explicit small cap must survive.

    It is intentionally small and is never the full window, so it bypasses the
    context-window arithmetic entirely — even on the headroom-2 model.
    """

    model = _model(context_window=262144, max_tokens=262142)
    opts = SimpleStreamOptions(max_tokens=13107)  # floor(0.8 * 16384)
    params = build_params(model, _context(), opts, get_compat(model), "short")
    assert _emitted_cap(params) == 13107


def test_options_max_tokens_wins_even_when_the_prompt_is_large() -> None:
    model = _model(context_window=262144, max_tokens=262142)
    opts = SimpleStreamOptions(max_tokens=8192)
    params = build_params(
        model, _context("x" * 100_000), opts, get_compat(model), "short"
    )
    assert _emitted_cap(params) == 8192


# === Tool schemas count toward the prompt =================================


def test_tool_schemas_are_counted_against_the_window() -> None:
    """The live 400 counted "1276 of tool input" — tools must not be ignored."""

    # ctx leaves exactly 4096 of headroom; a fat tool schema must consume it.
    model = _model(context_window=200_000, max_tokens=195_904)
    fat_tool = _FakeTool(description="d" * 60_000)
    ctx = Context(
        messages=[UserMessage(content=[TextContent(text="hi")])],
        tools=[fat_tool],
    )
    params = build_params(model, ctx, None, get_compat(model), "short")
    assert _emitted_cap(params) is None


def test_non_ascii_prompts_are_charged_densely() -> None:
    """CJK tokenizes far denser than 4:1; the estimate must not undercount."""

    model = _model(context_window=204800, max_tokens=196608)
    # 9000 Hangul chars ~= 9000 tokens > the 8192 headroom, but only 2250
    # under a naive len/4 charge.
    params = build_params(
        model, _context("안" * 9_000), None, get_compat(model), "short"
    )
    assert _emitted_cap(params) is None
