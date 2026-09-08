"""#249 — the footer context meter's MID-TURN figure, read off ``message_end``.

``get_session_stats`` cannot serve a mid-turn read: the harness only extends
``_state.messages`` once the loop has returned (``harness/core.py:4598``), so
the per-round-trip refreshes the TUI already ran all estimated over an
unchanged list and repainted the PRE-turn number. The fresh figure is carried
by the ``message_end`` payload itself, and
:func:`~aelix_coding_agent.tui.shell._live_context_usage` is the five lines
that read it.

These are the pure unit tests for that helper — validity rule, agreement with
the harness estimator's own anchor, and the rendered label. The integration
tests that drive it through a live ``run_tui`` live at the end of
``tests/tui/test_run_tui_smoke.py``, beside the rest of the meter's tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.session.compaction import (
    _valid_assistant_usage,
    calculate_context_tokens,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_coding_agent.tui.shell import _format_context_label, _live_context_usage

_MODEL = SimpleNamespace(id="anthropic/claude-x", context_window=200_000)

#: The usage shape the adapters report: input + output + cache_read = 96_900.
_USAGE = {"input_tokens": 84_000, "output_tokens": 900, "cache_read": 12_000}


def _assistant(**kw: Any) -> AssistantMessage:
    base: dict[str, Any] = {
        "content": [TextContent(text="done")],
        "stop_reason": "end_turn",
        "usage": dict(_USAGE),
    }
    base.update(kw)
    return AssistantMessage(**base)


# --- T1: only a SCORING assistant message moves the meter ----------------------
#
# Each row states what a mutation costs, because that is the whole reason the
# rule is duplicated in the TUI rather than approximated there.

_ROWS: list[tuple[str, Any, Any, bool]] = [
    # (name, message, model, expected-to-produce-a-usage)
    ("assistant with usage", _assistant(), _MODEL, True),
    ("aborted assistant", _assistant(stop_reason="aborted"), _MODEL, False),
    ("errored assistant", _assistant(stop_reason="error"), _MODEL, False),
    ("assistant with no usage", _assistant(usage=None), _MODEL, False),
    (
        "assistant with all-zero usage",
        _assistant(usage={"input_tokens": 0, "output_tokens": 0}),
        _MODEL,
        False,
    ),
    ("a user message", UserMessage(content=[TextContent(text="hi")]), _MODEL, False),
    # The row that pins the ``isinstance`` line. ``UserMessage`` /
    # ``ToolResultMessage`` carry no ``usage`` field at all
    # (``messages.py:127`` is the module's only one), so the user-message row
    # above stays green with the type check deleted. This shape — a non-assistant
    # object that DOES carry usage — is reachable through a ``message_end``
    # reducer replacement (``harness/hooks.py:313``) or an RPC-decoded message,
    # and repainting from it would show a number the estimator will never anchor
    # on.
    (
        "a duck-typed non-assistant carrying usage",
        SimpleNamespace(role="user", stop_reason=None, usage=dict(_USAGE)),
        _MODEL,
        False,
    ),
]


@pytest.mark.parametrize(
    ("message", "model", "expected"),
    [pytest.param(m, mo, e, id=name) for name, m, mo, e in _ROWS],
)
def test_live_context_usage_accepts_only_a_scoring_assistant_message(
    message: Any, model: Any, expected: bool
) -> None:
    usage = _live_context_usage(message, model)
    assert (usage is not None) is expected
    if usage is not None:
        assert usage.tokens == 96_900
        assert usage.context_window == 200_000


def test_live_context_usage_needs_a_model_with_a_positive_window() -> None:
    """The same short-circuit ``_get_context_usage_safe`` opens with.

    A double without a ``current_model`` therefore paints NOTHING rather than
    dividing by zero — which is also why every smoke test below sets one.
    """

    assert _live_context_usage(_assistant(), None) is None
    assert _live_context_usage(_assistant(), SimpleNamespace(context_window=0)) is None
    assert _live_context_usage(_assistant(), SimpleNamespace(id="no-window")) is None


# --- T2: the same rule the estimator anchors on --------------------------------


@pytest.mark.parametrize(
    ("message", "model", "expected"),
    [pytest.param(m, mo, e, id=name) for name, m, mo, e in _ROWS],
)
def test_live_usage_agrees_with_the_estimator_anchor(
    message: Any, model: Any, expected: bool
) -> None:
    """``estimate_context_tokens`` = this term + a heuristic for the trailing
    messages, so the two must accept the same messages and count them the same
    way. If they drift the meter jumps at every turn boundary — #249's exact
    complaint, arriving by the other door.

    ``_valid_assistant_usage`` (``session/compaction.py:1102``, pi #5526) is
    private, so it is imported HERE and not in the product code; this test is
    what keeps the five duplicated lines honest.
    """

    anchor = _valid_assistant_usage(message)
    live = _live_context_usage(message, model)
    assert (live is not None) == (anchor is not None)
    if live is not None:
        assert anchor is not None
        assert live.tokens == calculate_context_tokens(anchor)


# --- T3: the label the footer actually shows -----------------------------------


def test_the_live_usage_renders_through_the_one_formatter() -> None:
    """84000 + 900 + 12000 = 96900 over a 200K window, through
    ``_format_context_label`` — not a second formatter, so the mid-turn segment
    is byte-identical in shape to the turn-end one."""

    usage = _live_context_usage(_assistant(), _MODEL)
    assert usage is not None
    assert usage.percent == pytest.approx(48.45)
    assert _format_context_label(usage) == "◔ 48% · 96.9K/200K"


def test_a_million_token_window_changes_both_halves_of_the_label() -> None:
    """What ``/model`` to a bigger model must do without a new turn (§A5)."""

    usage = _live_context_usage(_assistant(), SimpleNamespace(context_window=1_000_000))
    assert usage is not None
    assert usage.tokens == 96_900
    assert _format_context_label(usage) == "◔ 10% · 96.9K/1M"
