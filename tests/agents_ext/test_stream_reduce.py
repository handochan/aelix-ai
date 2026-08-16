"""``aelix_agents.stream`` — the pure read path (ADR-0197 §(k), ADR-0198).

Two subjects, both pure:

* :class:`LineAssembler` — the chunked transport that exists because
  ``StreamReader.readline()`` is unusable at real ``message_end`` sizes
  (finding B3). The 200 000-byte pin below is the exact size that raises
  ``ValueError: Separator is not found, and chunk exceed the limit`` on
  ``readline()`` and leaves the stream permanently unrecoverable.
* :func:`reduce_line` — the field map. Its most valuable tests are the ones
  that prove a pi-shaped camelCase parser reads NOTHING, because that failure
  mode is invisible: every field silently becomes ``None`` and every failed run
  looks like a success with zero usage.
"""

from __future__ import annotations

import json

import pytest
from aelix_agents.stream import (
    MAX_LINE_BYTES,
    LineAssembler,
    _StreamState,
    reduce_line,
)


def _assistant(
    *,
    content: list[dict[str, object]] | None = None,
    stop_reason: str | None = "end_turn",
    error_message: str | None = None,
    usage: dict[str, object] | None = None,
    provider: str | None = "anthropic",
    model: str | None = "claude-x",
) -> dict[str, object]:
    """A ``message_end`` payload in the shape ``dataclasses.asdict`` produces.

    Mirrors ``aelix_ai/messages.py``'s :class:`AssistantMessage` field-for-field
    (snake_case, ``role`` last) so a drift in the kernel dataclass shows up here
    rather than in production.
    """

    return {
        "type": "message_end",
        "message": {
            "content": content if content is not None else [],
            "stop_reason": stop_reason,
            "error_message": error_message,
            "usage": usage,
            "timestamp": 1.0,
            "api": None,
            "provider": provider,
            "model": model,
            "response_id": None,
            "role": "assistant",
        },
    }


def _feed(state: _StreamState, *events: object) -> _StreamState:
    for event in events:
        reduce_line(state, json.dumps(event) if not isinstance(event, str) else event)
    return state


# --- reduce_line: robustness ------------------------------------------------


def test_typeless_header_line_does_not_raise() -> None:
    """The FIRST line has no ``type`` key at all.

    ``modes/print_mode.py:173-185`` emits the session metadata dict verbatim
    before any event. ``event["type"]`` would ``KeyError`` on line one of every
    single delegation.
    """

    state = _StreamState()
    reduce_line(state, json.dumps({"id": "abc", "created_at": 123.0}))
    assert state.summary == ""
    assert state.turns == 0


def test_absent_header_is_fine() -> None:
    """The header is emitted inside its own ``try/except`` and may never appear.

    Neither its presence nor its absence may be relied on, so a stream that
    starts straight at ``agent_start`` must reduce identically.
    """

    state = _StreamState()
    _feed(
        state,
        {"type": "agent_start"},
        _assistant(content=[{"type": "text", "text": "hi"}]),
        {"type": "agent_end", "messages": []},
    )
    assert state.saw_agent_start is True
    assert state.saw_agent_end is True
    assert state.summary == "hi"


def test_unparseable_line_skipped() -> None:
    """Anything in the child can ``print()`` into the same stdout.

    ``print_mode.py:23-26`` explicitly declines pi's ``takeOverStdout``, so an
    extension's or an MCP server's stray print lands in the event stream. It
    must be skipped silently, not raised on and not counted.
    """

    state = _StreamState()
    for junk in ("not json at all", "{broken", "[1,2,3]", '"a bare string"', "42", ""):
        reduce_line(state, junk)
    assert state.turns == 0
    assert state.summary == ""


def test_message_update_ignored() -> None:
    """``message_update`` repeats the WHOLE message on every delta.

    Measured stream-to-result ratios of 329x and 2773x, while the outer
    ``message.content`` stays ``[]`` for the duration. Consuming it is O(n^2)
    and yields nothing; a reducer that counted it would also multiply ``turns``
    and every usage number by the delta count.
    """

    state = _StreamState()
    update = {
        "type": "message_update",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "partial"}],
            "usage": {"input": 999, "output": 999},
            "stop_reason": "end_turn",
        },
        "assistant_message_event": {"type": "text_delta"},
    }
    _feed(state, update, update, update)
    assert state.turns == 0
    assert state.summary == ""
    assert state.input == 0


def test_non_assistant_message_end_is_not_a_turn() -> None:
    """Only assistant messages count as turns.

    ``message_end`` also fires for user and ``toolResult`` messages; counting
    them would inflate ``turns`` by roughly the tool-call count.
    """

    state = _StreamState()
    _feed(
        state,
        {"type": "message_end", "message": {"role": "user", "content": []}},
        {
            "type": "message_end",
            "message": {"role": "toolResult", "content": [], "tool_call_id": "t1"},
        },
    )
    assert state.turns == 0


# --- reduce_line: summary extraction ---------------------------------------


def test_summary_is_last_textbearing_assistant() -> None:
    """A later assistant turn supersedes an earlier one — but only if it speaks.

    A tool-calling turn carries no text; overwriting the summary with its empty
    body would throw away the child's actual answer.
    """

    state = _StreamState()
    _feed(
        state,
        _assistant(content=[{"type": "text", "text": "first"}]),
        _assistant(
            content=[{"type": "toolCall", "tool_name": "read", "input": {}}],
            stop_reason="toolUse",
        ),
        _assistant(content=[{"type": "text", "text": "final"}]),
    )
    assert state.summary == "final"
    assert state.turns == 3


def test_summary_survives_a_trailing_toolcall_only_turn() -> None:
    """The text-bearing turn wins even when it is not the last one."""

    state = _StreamState()
    _feed(
        state,
        _assistant(content=[{"type": "text", "text": "the answer"}]),
        _assistant(
            content=[{"type": "toolCall", "tool_name": "write", "input": {}}],
            stop_reason="toolUse",
        ),
    )
    assert state.summary == "the answer"


def test_summary_concatenates_multiple_text_blocks() -> None:
    """Deliberate divergence from pi, which returns only the FIRST text part.

    pi (``index.ts:170-180``) silently discards everything a model said after
    its first tool call. Multiple text blocks only ever appear when something
    separated them, so they are joined on a newline rather than glued.
    """

    state = _StreamState()
    _feed(
        state,
        _assistant(
            content=[
                {"type": "text", "text": "alpha"},
                {"type": "toolCall", "tool_name": "read", "input": {}},
                {"type": "text", "text": "beta"},
            ]
        ),
    )
    assert state.summary == "alpha\nbeta"


def test_thinking_blocks_excluded() -> None:
    """``thinking`` is scratchpad, and on some providers it is SIGNED material.

    It must never be surfaced as the child's answer.
    """

    state = _StreamState()
    _feed(
        state,
        _assistant(
            content=[
                {"type": "thinking", "thinking": "secret chain of thought"},
                {"type": "text", "text": "public answer"},
            ]
        ),
    )
    assert state.summary == "public answer"
    assert "secret" not in state.summary


def test_malformed_content_blocks_are_skipped() -> None:
    """A non-list ``content``, or non-dict members, must not raise."""

    state = _StreamState()
    _feed(
        state,
        {"type": "message_end", "message": {"role": "assistant", "content": "oops"}},
        {
            "type": "message_end",
            "message": {"role": "assistant", "content": [None, 5, {"type": "text"}]},
        },
    )
    assert state.summary == ""
    assert state.turns == 2


# --- reduce_line: the field map --------------------------------------------


def test_snake_case_fields_read() -> None:
    """THE PI-PORT TRAP.

    ``--mode json`` emits raw kernel dataclasses through ``dataclasses.asdict``
    (``print_mode.py:59-68``), so the wire names are the Python attribute names.
    A camelCase fixture — pi's spelling — must populate NOTHING at the message
    level, because a parser that reads pi's names produces a stream in which
    every failure looks like a success with an empty summary.

    ``usage`` is deliberately excluded from this assertion: it is the one
    sub-dict that IS dual-read (ADR-0198 D2), and its own test is below.
    """

    snake = _StreamState()
    _feed(
        snake,
        _assistant(
            content=[{"type": "text", "text": "ok"}],
            stop_reason="end_turn",
            error_message=None,
        ),
        {"type": "tool_execution_start", "tool_call_id": "t", "tool_name": "read"},
    )
    assert snake.stop_reason == "end_turn"
    assert snake.current_tool == "read"

    camel = _StreamState()
    _feed(
        camel,
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "stopReason": "error",
                "errorMessage": "boom",
            },
        },
        {"type": "tool_execution_start", "toolCallId": "t", "name": "read"},
    )
    assert camel.stop_reason is None
    assert camel.error_message is None
    assert camel.current_tool is None


def test_stop_reason_and_error_message_last_write_wins() -> None:
    """Last non-empty value wins; ``None`` never erases a recorded failure."""

    state = _StreamState()
    _feed(
        state,
        _assistant(stop_reason="toolUse"),
        _assistant(stop_reason="error", error_message="the model exploded"),
        _assistant(stop_reason=None, error_message=None),
    )
    assert state.stop_reason == "error"
    assert state.error_message == "the model exploded"


def test_current_tool_from_tool_execution_start() -> None:
    """``tool_name``, not pi's ``name`` (kernel ``types.py:195-199``)."""

    state = _StreamState()
    _feed(
        state,
        {"type": "tool_execution_start", "tool_call_id": "1", "tool_name": "bash"},
    )
    assert state.current_tool == "bash"


def test_turn_start_and_agent_end_clear_current_tool() -> None:
    """A statusline row must never be left showing a finished tool.

    ``tool_execution_end`` is deliberately not consumed (it is one of the two
    event types that carry a whole ``ToolResult`` payload), so the clear rides
    on the next ``turn_start`` and on the terminator.
    """

    state = _StreamState()
    _feed(
        state,
        {"type": "tool_execution_start", "tool_call_id": "1", "tool_name": "bash"},
        {"type": "turn_start"},
    )
    assert state.current_tool is None

    _feed(
        state,
        {"type": "tool_execution_start", "tool_call_id": "2", "tool_name": "grep"},
        {"type": "agent_end", "messages": []},
    )
    assert state.current_tool is None
    assert state.saw_agent_end is True


def test_agent_end_is_a_terminator_only() -> None:
    """``agent_end`` re-sends the ENTIRE message array on one line.

    Reducing it would double-count every turn and every token in the run.
    """

    state = _StreamState()
    _feed(state, _assistant(content=[{"type": "text", "text": "hi"}]))
    before = (state.turns, state.summary)
    _feed(
        state,
        {
            "type": "agent_end",
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hi"}],
                    "usage": {"input": 100},
                }
            ],
        },
    )
    assert (state.turns, state.summary) == before
    assert state.input == 0


def test_provenance_recorded_for_the_cost_fallback() -> None:
    """``provider``/``model`` ride along so the impure layer can price the run.

    openrouter and openai-completions emit no ``cost`` key at all, so the
    registry-lookup fallback is the COMMON path — and a registry lookup is disk
    I/O, illegal in this module.
    """

    state = _StreamState()
    _feed(state, _assistant(provider="openrouter", model="qwen/qwen3"))
    assert state.provider == "openrouter"
    assert state.model == "qwen/qwen3"


# --- reduce_line: usage -----------------------------------------------------


def test_usage_sums_with_missing_keys() -> None:
    """Flows SUM across messages; a partial usage dict contributes what it has."""

    state = _StreamState()
    _feed(
        state,
        _assistant(usage={"input": 10, "output": 5}),
        _assistant(usage={"input": 7, "cache_read": 3}),
    )
    assert (state.input, state.output, state.cache_read, state.cache_write) == (
        17,
        5,
        3,
        0,
    )


def test_usage_null_on_errored_message() -> None:
    """``usage`` is ``None`` on an errored message and must not zero the totals.

    ``providers/anthropic.py:155-156`` returns ``None`` outright when every
    counter is zero, so this is the normal shape of a failed turn.
    """

    state = _StreamState()
    _feed(
        state,
        _assistant(usage={"input": 10, "output": 5, "total_tokens": 15}),
        _assistant(usage=None, stop_reason="error", error_message="429"),
    )
    assert (state.input, state.output, state.tokens) == (10, 5, 15)
    assert state.turns == 2


def test_usage_dual_spelling_read() -> None:
    """FINDING I8 — ``usage`` is ``dict[str, Any]`` and adapters own the keys.

    ``aelix_ai/messages.py:127`` types it ``dict[str, Any]`` and it rides
    through ``asdict`` untransformed, so it is explicitly OUT of the wire-shape
    guarantee. The kernel itself dual-reads at
    ``session/compaction.py:1036-1043``; a camelCase usage dict must populate
    the SAME fields as the snake_case one.
    """

    snake = _StreamState()
    _feed(
        snake,
        _assistant(
            usage={
                "input": 11,
                "output": 22,
                "cache_read": 33,
                "cache_write": 44,
                "total_tokens": 55,
            }
        ),
    )
    camel = _StreamState()
    _feed(
        camel,
        _assistant(
            usage={
                "inputTokens": 11,
                "outputTokens": 22,
                "cacheRead": 33,
                "cacheWrite": 44,
                "totalTokens": 55,
            }
        ),
    )
    for field in ("input", "output", "cache_read", "cache_write", "tokens"):
        assert getattr(snake, field) == getattr(camel, field)
    assert snake.tokens == 55


def test_usage_zero_in_one_spelling_falls_through_to_the_other() -> None:
    """Skip-on-falsy, matching the kernel's ``a or b`` dual-read exactly.

    An adapter that emits BOTH spellings with one zeroed (several do — see
    ``providers/openai_completions.py:976-983``) must not report zero.
    """

    state = _StreamState()
    _feed(state, _assistant(usage={"total_tokens": 0, "totalTokens": 90}))
    assert state.tokens == 90


def test_usage_booleans_are_not_token_counts() -> None:
    """``bool`` is an ``int`` subclass; ``{"input": True}`` must read as 0."""

    state = _StreamState()
    _feed(state, _assistant(usage={"input": True, "output": False}))
    assert (state.input, state.output) == (0, 0)


def test_tokens_overwrites_not_sums() -> None:
    """``tokens`` is a context LEVEL, not a flow.

    Every message reports the whole context, so summing reports a number
    several times the truth — and that number drives the ``/context`` meter and
    any compaction decision downstream.
    """

    state = _StreamState()
    _feed(
        state,
        _assistant(usage={"total_tokens": 1000}),
        _assistant(usage={"total_tokens": 1500}),
        _assistant(usage={"total_tokens": 1800}),
    )
    assert state.tokens == 1800


def test_cost_read_from_nested_total_and_summed() -> None:
    """pi's shape is ``usage.cost.total``; a flat number is tolerated too."""

    state = _StreamState()
    _feed(
        state,
        _assistant(usage={"cost": {"total": 0.25, "input": 0.1}}),
        _assistant(usage={"cost": 0.75}),
        _assistant(usage={"cost": None}),
    )
    assert state.cost == 1.0


# --- reduce_line: "NEVER raises" is a contract (P2 review, HIGH #3) ----------
#
# CPython's ``json`` emits AND accepts IEEE specials by default —
# ``json.dumps({"input": float("inf")})`` is ``{"input": Infinity}`` and
# ``json.loads('{"a": Infinity}')`` is ``{'a': inf}`` — and Python integers are
# unbounded, so a JSON line can carry a 400-digit number. ``int(inf)`` raises
# ``OverflowError``, ``int(nan)`` raises ``ValueError``, and
# ``float(10 ** 400)`` raises ``OverflowError``.
#
# Each of those escaped ``reduce_line``, then ``_pump_stdout``, then the pump
# gather, then a ``wait_for`` that catches only ``TimeoutError`` — leaving
# ``PrintChannel.run`` with NO reaper ever started while ``runtime._run``'s
# ``finally`` had already popped the registry row. Reproduced with a real
# spawned child: ``run() RAISED OverflowError`` / ``child='S (sleeping)'`` — an
# un-reapable session leader holding the parent's API keys.


@pytest.mark.parametrize(
    "usage",
    [
        {"input": float("inf")},
        {"input": float("-inf")},
        {"input": float("nan")},
        {"output": float("inf")},
        {"total_tokens": float("nan")},
        {"cache_read": float("inf")},
    ],
)
def test_non_finite_token_counts_do_not_raise(usage: dict[str, object]) -> None:
    """A non-finite token count is nonsense, and nonsense is skipped."""

    state = _StreamState()
    _feed(state, _assistant(usage=usage))
    assert state.input == 0
    assert state.output == 0
    assert state.tokens == 0
    assert state.cache_read == 0


def test_a_non_finite_spelling_falls_through_to_a_usable_one() -> None:
    """Skipped, not fatal AND not terminal — the dual-read keeps looking."""

    state = _StreamState()
    _feed(state, _assistant(usage={"input": float("nan"), "inputTokens": 42}))
    assert state.input == 42


@pytest.mark.parametrize(
    "cost",
    [
        10**400,
        {"total": 10**400},
        float("inf"),
        {"total": float("inf")},
        float("nan"),
        {"total": float("nan")},
    ],
)
def test_unrepresentable_costs_do_not_raise(cost: object) -> None:
    """``float(...)`` is not total over what a JSON line can carry."""

    state = _StreamState()
    _feed(state, _assistant(usage={"cost": cost}))
    assert state.cost == 0.0


def test_a_huge_but_representable_cost_still_reads() -> None:
    """The guard rejects the unrepresentable, not the merely large."""

    state = _StreamState()
    _feed(state, _assistant(usage={"cost": {"total": 10**30}}))
    assert state.cost == float(10**30)


def test_a_poisoned_line_does_not_stop_the_stream() -> None:
    """The terminating ``agent_end`` — and the summary — must still land.

    This is the property that actually mattered: the poisoned ``message_end``
    used to take the whole pump with it, so everything AFTER it was lost.
    """

    state = _StreamState()
    _feed(
        state,
        {"type": "agent_start"},
        _assistant(
            content=[{"type": "text", "text": "poisoned"}],
            usage={"input": float("inf")},
        ),
        _assistant(
            content=[{"type": "text", "text": "the answer"}],
            usage={"input": 7, "output": 3},
        ),
        {"type": "agent_end"},
    )
    assert state.summary == "the answer"
    assert state.input == 7
    assert state.saw_agent_end is True


# --- LineAssembler ----------------------------------------------------------


def test_line_assembler_splits_across_chunk_boundaries() -> None:
    """The whole reason the assembler exists: the pump reads FIXED-SIZE chunks.

    A line is split at an arbitrary byte offset, so the assembler must hold the
    tail across ``feed`` calls and emit only complete lines.
    """

    payload = b'{"type":"agent_start"}\n{"type":"turn_start"}\n{"type":"agent_end"}\n'
    assembler = LineAssembler()
    out: list[str] = []
    for i in range(0, len(payload), 7):
        out.extend(assembler.feed(payload[i : i + 7]))
    assert out == [
        '{"type":"agent_start"}',
        '{"type":"turn_start"}',
        '{"type":"agent_end"}',
    ]
    assert assembler.flush() == []
    assert assembler.dropped_lines == 0


def test_line_assembler_handles_a_200kb_line() -> None:
    """FINDING B3, THE PIN.

    200 000 bytes is the exact size at which ``StreamReader.readline()`` raises
    ``ValueError: Separator is not found, and chunk exceed the limit`` — and the
    NEXT ``readline()`` raises ``Separator is found, but chunk is longer than
    limit``, i.e. the stream never recovers and the terminating ``agent_end`` is
    lost forever. It is not an exotic size: a ``message_end`` carrying one
    ``read`` of a large source file serialises to ~207 KB, and ``message_end``
    is the SOLE source of the summary and the usage numbers.

    Under the assembler the line is well inside the 4 MiB budget, so it must
    survive intact, be reducible, and leave the following line parseable.
    """

    big_text = "x" * 200_000
    line = json.dumps(_assistant(content=[{"type": "text", "text": big_text}]))
    assert len(line.encode("utf-8")) > 200_000
    payload = line.encode("utf-8") + b"\n" + b'{"type":"agent_end","messages":[]}\n'

    assembler = LineAssembler()
    state = _StreamState()
    emitted = 0
    for i in range(0, len(payload), 65536):
        for out in assembler.feed(payload[i : i + 65536]):
            emitted += 1
            reduce_line(state, out)
    assert emitted == 2
    assert assembler.dropped_lines == 0
    assert state.summary == big_text
    assert state.saw_agent_end is True


def test_line_assembler_drops_oversize_and_resyncs() -> None:
    """Over-budget lines are DROPPED and counted — never raised on, never kept.

    An unbounded line is the one shape in which a malfunctioning or hostile
    child can exhaust parent memory, and the terminating ``agent_end`` must
    still arrive afterwards. The budget is injected here rather than allocating
    4 MiB of test data.
    """

    assembler = LineAssembler(max_line_bytes=64)
    state = _StreamState()
    out: list[str] = []
    out.extend(assembler.feed(b'{"type":"agent_start"}\n'))
    for _ in range(10):
        out.extend(assembler.feed(b"y" * 100))
    out.extend(assembler.feed(b'\n{"type":"agent_end","messages":[]}\n'))
    for line in out:
        reduce_line(state, line)

    assert assembler.dropped_lines == 1
    assert out == ['{"type":"agent_start"}', '{"type":"agent_end","messages":[]}']
    assert state.saw_agent_start is True
    assert state.saw_agent_end is True


def test_line_assembler_drops_a_whole_oversize_line_inside_one_chunk() -> None:
    """The budget also applies when the line arrives complete, in one chunk."""

    assembler = LineAssembler(max_line_bytes=16)
    out = assembler.feed(b"short\n" + b"z" * 100 + b"\nalso-short\n")
    assert out == ["short", "also-short"]
    assert assembler.dropped_lines == 1


def test_line_assembler_counts_one_drop_per_line_not_per_chunk() -> None:
    """A single oversize line spread over many chunks counts ONCE."""

    assembler = LineAssembler(max_line_bytes=32)
    for _ in range(50):
        assert assembler.feed(b"q" * 64) == []
    assembler.feed(b"\nok\n")
    assert assembler.dropped_lines == 1


def test_trailing_partial_line_flushed_at_eof() -> None:
    """A child killed mid-line leaves bytes with no terminator.

    The last thing a dying child wrote is often the most informative line in
    the stream, so it is parsed rather than discarded.
    """

    assembler = LineAssembler()
    assert assembler.feed(b'{"type":"agent_start"}\n{"type":"turn_') == [
        '{"type":"agent_start"}'
    ]
    assert assembler.feed(b'start"}') == []
    assert assembler.flush() == ['{"type":"turn_start"}']
    assert assembler.flush() == []


def test_flush_discards_an_oversize_partial() -> None:
    """An unterminated over-budget tail is counted, not emitted."""

    assembler = LineAssembler(max_line_bytes=16)
    assembler.feed(b"w" * 100)
    assert assembler.flush() == []
    assert assembler.dropped_lines == 1


def test_line_assembler_survives_split_multibyte_codepoints() -> None:
    """Decoding is deferred to the LINE boundary, not the chunk boundary.

    Splitting a 3-byte code point across two ``feed`` calls would otherwise
    yield two replacement characters and corrupt the JSON.
    """

    line = json.dumps({"type": "message_end", "message": {"role": "user"}})
    raw = ("日本語テキスト" + line).encode("utf-8") + b"\n"
    assembler = LineAssembler()
    out: list[str] = []
    for i in range(0, len(raw), 1):
        out.extend(assembler.feed(raw[i : i + 1]))
    assert out == ["日本語テキスト" + line]


def test_line_assembler_never_raises_on_invalid_utf8() -> None:
    """Malformed bytes become replacement chars; ``reduce_line`` then skips them."""

    assembler = LineAssembler()
    out = assembler.feed(b"\xff\xfe garbage\n" + b'{"type":"agent_end","messages":[]}\n')
    assert len(out) == 2
    state = _StreamState()
    for line in out:
        reduce_line(state, line)
    assert state.saw_agent_end is True


def test_empty_feed_is_a_noop() -> None:
    """EOF is signalled by an empty chunk; it must not disturb the buffer."""

    assembler = LineAssembler()
    assembler.feed(b"partial")
    assert assembler.feed(b"") == []
    assert assembler.flush() == ["partial"]


def test_max_line_bytes_default_is_the_documented_budget() -> None:
    """4 MiB — ~20x the largest routine ``message_end`` measured (~207 KB)."""

    assert MAX_LINE_BYTES == 4 * 1024 * 1024
    assert LineAssembler().dropped_lines == 0
