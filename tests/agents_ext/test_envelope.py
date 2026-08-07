"""``aelix_agents.envelope`` — :class:`_StreamState` → :class:`SubagentResult`.

ADR-0197 §(k). Pure: every process fact (exit code, stderr tail, wall clock)
arrives as an argument, so the failure taxonomy, the cap and the fallback chain
are pinned without ever creating a process.
"""

from __future__ import annotations

import pytest
from aelix_agents.envelope import (
    DEFAULT_OUTPUT_CAP,
    NO_OUTPUT,
    STREAM_ENDED_EARLY,
    build_result,
    cap_summary,
    declined_result,
    sanitize_stderr,
)
from aelix_agents.stream import _StreamState
from aelix_coding_agent.subagent_contract import SubagentResult

# The exact noise a cooperative SIGTERM produces. ``print_mode.py``'s
# ``_signal_cleanup_and_exit`` calls ``sys.exit(128 + sig)`` from inside a
# coroutine, so asyncio prints this on EVERY successful kill — it is normal, not
# a diagnosis, and surfacing it to the model as the reason a task was aborted is
# actively misleading.
_SIGTERM_NOISE = """Task exception was never retrieved
future: <Task finished name='Task-3' coro=<run() done> exception=SystemExit(143)>
Traceback (most recent call last):
  File "/x/print_mode.py", line 120, in _signal_cleanup_and_exit
    sys.exit(128 + sig)
SystemExit: 143"""


def _state(**kwargs: object) -> _StreamState:
    state = _StreamState()
    for key, value in kwargs.items():
        setattr(state, key, value)
    return state


# --- failure detection ------------------------------------------------------


def test_exit_zero_with_error_stop_reason_is_failure() -> None:
    """NEVER TRUST THE RETURN CODE ALONE (§9.1).

    ``print_mode.py``'s ``stop_reason in ("error","aborted") -> exit_code = 1``
    mapping is guarded by ``if mode == "text"``. A JSON-mode child whose model
    errored — bogus model id, mid-stream auth failure — exits **0** with empty
    stderr while the stream carries ``stop_reason: "error"``. Reading only the
    exit code reports that as a success with an empty summary.
    """

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(stop_reason="error", error_message="model not found"),
        outcome="ok",
        exit_code=0,
    )
    assert result.ok is False
    assert result.status == "error"
    assert result.summary == "model not found"
    assert result.error == "model not found"


def test_nonzero_exit_is_a_failure_even_with_a_clean_stream() -> None:
    """The converse: a clean stream does not launder a non-zero exit."""

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(summary="done", stop_reason="end_turn"),
        outcome="ok",
        exit_code=1,
    )
    assert result.ok is False
    assert result.status == "error"


def test_happy_path_is_ok() -> None:
    # ``saw_agent_end=True`` is not decoration: a run with no ``agent_end`` is a
    # child that stopped MID-TURN, and this test is the definition of one that
    # did not. It was implicit before ``build_result`` learned to read the
    # terminator, which is precisely how a mid-turn death shipped as ``ok``.
    result = build_result(
        id="s1",
        profile="scout",
        state=_state(
            summary="all good", stop_reason="end_turn", turns=2, saw_agent_end=True
        ),
        outcome="ok",
        exit_code=0,
    )
    assert (result.ok, result.status, result.summary) == (True, "ok", "all good")
    assert result.error is None
    assert result.usage.turns == 2


def test_aborted_stop_reason_reports_aborted_not_error() -> None:
    """``stop_reason: "aborted"`` is a distinct outcome, not a generic error."""

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(summary="partial", stop_reason="aborted"),
        outcome="ok",
        exit_code=0,
    )
    assert result.status == "aborted"
    assert result.ok is False


# --- the four quadrants of "did the child actually finish?" -----------------
#
# The two axes are INDEPENDENT and the bugs they pin are each other's mirror:
# an ``error_message`` from a turn the harness RETRIED must not become the
# answer, and a missing terminator must not be laundered into a success by a
# zero exit code. Fixing either half alone is wrong — see the module under test.


def test_recovered_provider_error_does_not_become_the_answer() -> None:
    """QUADRANT: error + RECOVERED. The defect this fix exists for.

    ``_reduce_message_end`` (``stream.py:552-556``) is last-NON-EMPTY-wins per
    field, so ``state.error_message`` survives a turn the harness's own
    auto-retry (``harness/core.py:494-495``, default ON) already recovered from.
    The child then answers correctly on turn 2 and the run is a genuine success:
    ``stop_reason='stop'``, exit 0.

    MEASURED end to end against a local endpoint returning 6 consecutive 429s
    (7 HTTP calls — the provider SDK absorbs the first 3 itself, so a SINGLE 429
    does not reproduce this at all). Before the fix the envelope came back
    ``ok=True status='ok'`` with ``summary="Error code: 429 …"`` and the real
    answer stranded in ``details``. ``render_subagent_result`` renders only
    ``summary``, so the PARENT MODEL received the error string as the child's
    work — and because ``ok`` was True, ``_run_chain`` (``batch.py:391``) did not
    break and propagated it to the next step as ``{previous}``.
    """

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(
            summary="the answer",
            stop_reason="stop",
            error_message="Error code: 429 - rate_limit_exceeded",
            saw_agent_end=True,
        ),
        outcome="ok",
        exit_code=0,
    )
    assert (result.ok, result.status) == (True, "ok")
    assert result.summary == "the answer"
    # The stale artifact must not reappear as a note the model reads either.
    assert result.error is None


def test_unrecovered_provider_error_is_still_the_summary() -> None:
    """QUADRANT: error + UNRECOVERED — the gate must not swallow a real failure.

    Measured with 12 consecutive 429s: auto-retry is exhausted, the adapter sets
    ``stop_reason='error'`` AND ``error_message`` together (``loop.py:322-325``
    states that contract), and the exit code is STILL 0 — which is why
    ``stop_reason``, not the return code, is the load-bearing disjunct.
    """

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(
            stop_reason="error",
            error_message="Error code: 429 - rate_limit_exceeded",
            saw_agent_end=True,
        ),
        outcome="ok",
        exit_code=0,
    )
    assert (result.ok, result.status) == (False, "error")
    assert result.summary == "Error code: 429 - rate_limit_exceeded"
    assert result.error == "Error code: 429 - rate_limit_exceeded"


def test_child_that_died_mid_turn_is_not_a_success() -> None:
    """QUADRANT: mid-turn death with exit 0.

    ``agent_end`` is the child's own terminator (``stream.py:231-235``). A
    JSON-mode child whose harness was torn down emits a good ``message_end`` and
    exits **0** without one; before this fix that returned
    ``ok=True status='ok'`` for a run that never finished.

    THE PARTIAL MUST SURVIVE: a fix that routes the diagnostic through
    ``_select_summary`` instead of ``error`` destroys the only work the child
    did manage to do, which is the pi behaviour this package deliberately
    diverges from (module docstring).
    """

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(summary="partial", stop_reason="end_turn"),
        outcome="ok",
        exit_code=0,
    )
    assert (result.ok, result.status) == (False, "error")
    assert result.summary == "partial"
    assert result.error == STREAM_ENDED_EARLY


def test_missing_terminator_is_not_evidence_once_a_line_was_dropped() -> None:
    """QUADRANT: the TRAP — a SUCCESSFUL child whose terminator was too big.

    ``agent_end`` carries the entire message array on ONE line
    (``stream.py:535-541``), so a child that read a multi-megabyte file emits a
    terminator above ``MAX_LINE_BYTES`` and ``LineAssembler`` drops it — on a run
    that finished perfectly. Measured with a real child: ``saw_agent_end=False,
    dropped_lines=1, exit 0``.

    A bare ``or not state.saw_agent_end`` FAILS that delegation. Once a line has
    been dropped, the terminator's absence is not evidence of anything.
    """

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(summary="the complete answer", stop_reason="end_turn",
                     dropped_lines=1),
        outcome="ok",
        exit_code=0,
    )
    assert (result.ok, result.status) == (True, "ok")
    assert result.summary == "the complete answer"


@pytest.mark.parametrize("outcome", ["timeout", "aborted", "error"])
def test_caller_outcome_is_never_loosened(outcome: str) -> None:
    """The process layer's verdict is a floor: a clean stream cannot undo it."""

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(summary="partial", stop_reason="end_turn"),
        outcome=outcome,  # type: ignore[arg-type]
        exit_code=0,
    )
    assert result.status == outcome
    assert result.ok is False
    assert result.summary == "partial"


def test_timeout_carries_the_partial_summary_and_usage() -> None:
    """A timeout returns what the child managed, never an exception (§9.4)."""

    result = build_result(
        id="s1",
        profile="scout",
        state=_state(summary="got halfway", input=100, output=40, turns=1),
        outcome="timeout",
        exit_code=-9,
        elapsed_ms=1500,
    )
    assert result.status == "timeout"
    assert result.summary == "got halfway"
    assert (result.usage.input, result.usage.output, result.usage.turns) == (100, 40, 1)
    assert result.elapsed_ms == 1500


# --- the fallback chain -----------------------------------------------------


def test_fallback_chain_order() -> None:
    """``error_message`` → sanitized stderr → extracted summary → sentinel.

    Each rung is exercised with everything below it also present, so a
    reordering cannot pass.
    """

    both = build_result(
        id="s",
        profile="p",
        state=_state(summary="stream text", error_message="the real reason"),
        outcome="error",
        exit_code=1,
        stderr_tail="stderr says something else",
    )
    assert both.summary == "the real reason"

    no_error_message = build_result(
        id="s",
        profile="p",
        state=_state(summary="stream text"),
        outcome="error",
        exit_code=1,
        stderr_tail="stderr says something else",
    )
    assert no_error_message.summary == "stderr says something else"

    no_stderr = build_result(
        id="s",
        profile="p",
        state=_state(summary="stream text"),
        outcome="error",
        exit_code=1,
        stderr_tail="",
    )
    assert no_stderr.summary == "stream text"

    nothing = build_result(
        id="s", profile="p", state=_state(), outcome="error", exit_code=1
    )
    assert nothing.summary == NO_OUTPUT


def test_empty_stream_uses_stderr() -> None:
    """WHY THE STDERR RUNG IS MANDATORY.

    A child launched with no API key exits 1 having written **zero** stdout
    bytes — not even the session header — so the reduced state is completely
    empty and stderr is the only thing that can explain the failure.
    """

    result = build_result(
        id="s",
        profile="p",
        state=_state(),
        outcome="error",
        exit_code=1,
        stderr_tail="Error: no API key configured for provider 'anthropic'\n",
    )
    assert result.ok is False
    assert "no API key" in result.summary


def test_successful_run_keeps_its_summary_despite_stderr_noise() -> None:
    """DELIBERATE REFINEMENT of the literal chain, and it is load-bearing.

    The stderr rung is gated on failure. Provider SDK / httpx logging and any
    extension ``print(..., file=sys.stderr)`` land in the same pipe —
    ``print_mode.py:23-26`` declines pi's ``takeOverStdout``, so those bytes are
    NOT redirected. Ungated, one ``DeprecationWarning`` on a perfectly
    successful delegation would replace the child's answer with a log line.

    On every failure path the rung still fires (see the tests above), including
    the zero-stdout case it exists for.
    """

    result = build_result(
        id="s",
        profile="p",
        state=_state(
            summary="the answer", stop_reason="end_turn", saw_agent_end=True
        ),
        outcome="ok",
        exit_code=0,
        stderr_tail="DeprecationWarning: ssl.PROTOCOL_TLS is deprecated\n",
    )
    assert result.ok is True
    assert result.summary == "the answer"


def test_no_output_sentinel() -> None:
    """An empty summary renders as a blank panel and reads like a bug."""

    result = build_result(
        id="s",
        profile="p",
        state=_state(saw_agent_end=True),
        outcome="ok",
        exit_code=0,
    )
    assert result.summary == NO_OUTPUT
    assert result.ok is True


def test_stderr_only_success_still_surfaces_something() -> None:
    """The last rung before the sentinel fires even on a successful run.

    A child that exited 0 having said nothing on stdout but something on stderr
    is better reported with that text than with "(no output)".

    ``saw_agent_end=True`` RE-ANCHORS this test rather than repairing it: it
    passed either way, but without the terminator the run is a failure and the
    summary arrives from the ``not ok and stderr_clean`` rung — not the final
    ``if stderr_clean`` rung this test exists to pin. Same text, different code
    path, and the assertion could no longer tell them apart.
    """

    result = build_result(
        id="s",
        profile="p",
        state=_state(saw_agent_end=True),
        outcome="ok",
        exit_code=0,
        stderr_tail="wrote 3 files",
    )
    assert result.ok is True
    assert result.summary == "wrote 3 files"


# --- stderr sanitising ------------------------------------------------------


@pytest.mark.parametrize("outcome", ["aborted", "timeout"])
def test_sigterm_traceback_stripped_from_stderr_rung(outcome: str) -> None:
    """Stripped from ``summary``; PRESENT verbatim in ``details`` (finding B8).

    Filtering never destroys evidence — whoever reads ``details`` is debugging,
    and that is exactly when the traceback stops being noise.
    """

    result = build_result(
        id="s",
        profile="p",
        state=_state(),
        outcome=outcome,  # type: ignore[arg-type]
        exit_code=-15,
        stderr_tail=_SIGTERM_NOISE,
    )
    assert "SystemExit" not in result.summary
    assert "Task exception was never retrieved" not in result.summary
    assert result.summary == NO_OUTPUT
    assert result.details is not None
    assert "SystemExit: 143" in result.details


def test_abort_falls_through_to_the_partial_summary() -> None:
    """The whole point of sanitising: shutdown noise must not shadow real work.

    §(j) promises an aborted envelope carries the partial summary. It does,
    because the sanitizer reduces an all-noise stderr tail to the empty string
    and the chain then reaches the stream text.
    """

    result = build_result(
        id="s",
        profile="p",
        state=_state(summary="I read three files and found..."),
        outcome="aborted",
        exit_code=-15,
        stderr_tail=_SIGTERM_NOISE,
    )
    assert result.status == "aborted"
    assert result.summary == "I read three files and found..."


def test_real_errors_are_not_stripped_on_a_crash() -> None:
    """A traceback IS the diagnosis when the child genuinely crashed.

    Filtering applies to ``aborted``/``timeout`` only.
    """

    crash = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: bad"
    result = build_result(
        id="s", profile="p", state=_state(), outcome="error", exit_code=1, stderr_tail=crash
    )
    assert "ValueError: bad" in result.summary


def test_sanitize_stderr_is_a_no_op_off_the_kill_paths() -> None:
    assert sanitize_stderr(_SIGTERM_NOISE, outcome="error") == _SIGTERM_NOISE.strip()
    assert sanitize_stderr(_SIGTERM_NOISE, outcome="aborted") == ""


# --- the cap ----------------------------------------------------------------


def test_under_cap_not_marked() -> None:
    text, truncated, omitted = cap_summary("short", 1000)
    assert (text, truncated, omitted) == ("short", False, 0)


def test_cap_on_utf8_bytes_not_chars() -> None:
    """The budget is BYTES, and the cut backs off to a code-point boundary.

    A naive character slice lets a CJK or emoji-heavy summary carry three to
    four times the intended payload into the parent's context window — the exact
    thing the cap exists to bound. The result must also still be valid text:
    slicing mid-code-point would produce mojibake.
    """

    summary = "日" * 30  # 90 UTF-8 bytes, 30 characters
    text, truncated, omitted = cap_summary(summary, 10)
    assert truncated is True
    kept = text.split("\n\n[Output truncated")[0]
    assert kept == "日" * 3  # 9 bytes: 10 would split a code point
    assert len(kept.encode("utf-8")) <= 10
    assert omitted == 90 - 9
    assert f"{omitted} bytes omitted" in text


def test_cap_uses_profile_output_cap() -> None:
    """``AgentProfile.output_cap`` (``agents/profile.py:212``) is the budget."""

    assert DEFAULT_OUTPUT_CAP == 51200
    big = "a" * 300
    small_cap = build_result(
        id="s", profile="p", state=_state(summary=big), outcome="ok", exit_code=0,
        output_cap=100,
    )
    assert small_cap.truncated is True
    default_cap = build_result(
        id="s", profile="p", state=_state(summary=big), outcome="ok", exit_code=0
    )
    assert default_cap.truncated is False
    assert default_cap.summary == big


def test_zero_or_negative_cap_disables_truncation() -> None:
    """A profile with ``output_cap: 0`` must not truncate everything to nothing."""

    text, truncated, omitted = cap_summary("keep me", 0)
    assert (text, truncated, omitted) == ("keep me", False, 0)


def test_details_is_uncapped_when_summary_truncated() -> None:
    """FINDING B8 — the truncation marker's promise must be TRUE.

    ``summary`` says "full output preserved in tool details". Without this field
    that promise is false on the ``/agents run`` door, which never builds a
    ``ToolResult`` at all, and no dashboard or Web UI consuming
    :class:`SubagentResult` could ever show it.
    """

    big = "b" * 200_000
    result = build_result(
        id="s", profile="p", state=_state(summary=big), outcome="ok", exit_code=0
    )
    assert result.truncated is True
    assert len(result.summary) < len(big)
    assert result.details == big


def test_details_carries_raw_stderr_on_failure_only() -> None:
    failed = build_result(
        id="s", profile="p", state=_state(summary="partial"), outcome="error",
        exit_code=1, stderr_tail="raw diagnostic",
    )
    assert failed.details is not None
    assert "raw diagnostic" in failed.details
    assert "partial" in failed.details

    # The subject is "on failure ONLY", so this half must genuinely SUCCEED.
    # Without the terminator it is a failure, ``_build_details`` appends the
    # stderr half, and the test would be asserting the failure branch twice.
    succeeded = build_result(
        id="s", profile="p", state=_state(summary="fine", saw_agent_end=True),
        outcome="ok", exit_code=0, stderr_tail="chatty log",
    )
    assert succeeded.ok is True
    assert succeeded.details == "fine"


def test_details_is_none_when_there_is_nothing_behind_the_summary() -> None:
    # RE-ANCHORED, not repaired: both branches yield ``None``, so this passed
    # either way — and silently stopped exercising the success branch it names.
    result = build_result(
        id="s",
        profile="p",
        state=_state(saw_agent_end=True),
        outcome="ok",
        exit_code=0,
    )
    assert result.ok is True
    assert result.details is None


# --- the rest of the envelope ----------------------------------------------


@pytest.mark.parametrize(
    "outcome,exit_code", [("ok", 0), ("error", 1), ("timeout", -9), ("aborted", -15)]
)
def test_permission_mode_recorded_on_every_envelope(
    outcome: str, exit_code: int
) -> None:
    """The authority the child ran under is part of the audit story.

    ``/agents run``'s panel, a P4 dashboard and the ADR's story all need to show
    what was granted — including when a human widened it at the consent dialog.
    """

    result = build_result(
        id="s",
        profile="p",
        state=_state(summary="x"),
        outcome=outcome,  # type: ignore[arg-type]
        exit_code=exit_code,
        permission_mode="auto-accept-edits",
    )
    assert result.permission_mode == "auto-accept-edits"


def test_declined_envelope_shape() -> None:
    """A decline is an OUTCOME, not an error — and never a silent spawn.

    No process was created, so there is no exit code, no usage and no stderr.
    Reporting it through the same channel as everything else is what lets
    ``/agents run``, the ``agent`` tool and any dashboard render it with no
    special case.
    """

    result = declined_result(id="s1", profile="scout", permission_mode="plan")
    assert isinstance(result, SubagentResult)
    assert result.status == "declined"
    assert result.ok is False
    assert result.exit_code is None
    assert result.usage.turns == 0
    assert result.usage.cost == 0.0
    assert result.usage.input == 0
    assert result.summary.strip() != ""
    assert result.permission_mode == "plan"
    assert result.details is None
    assert result.truncated is False


def test_declined_envelope_accepts_a_custom_reason() -> None:
    result = declined_result(id="s", profile="p", reason="Project agent declined.")
    assert result.summary == "Project agent declined."


def test_usage_is_carried_across_verbatim() -> None:
    state = _state(
        input=11, output=22, cache_read=33, cache_write=44, cost=1.5, tokens=99, turns=3
    )
    result = build_result(id="s", profile="p", state=state, outcome="ok", exit_code=0)
    assert result.usage.input == 11
    assert result.usage.output == 22
    assert result.usage.cache_read == 33
    assert result.usage.cache_write == 44
    assert result.usage.cost == 1.5
    assert result.usage.tokens == 99
    assert result.usage.turns == 3


def test_dropped_lines_and_dropped_tools_ride_back() -> None:
    """Both are silent data loss unless they are reported."""

    result = build_result(
        id="s",
        profile="p",
        state=_state(summary="x", dropped_lines=2),
        outcome="ok",
        exit_code=0,
        dropped_tools=("agent", "write"),
    )
    assert result.dropped_lines == 2
    assert result.dropped_tools == ("agent", "write")


def test_explicit_error_overrides_the_stream() -> None:
    """A spawn that never started has no stream at all to explain itself."""

    result = build_result(
        id="s",
        profile="p",
        state=_state(),
        outcome="error",
        exit_code=None,
        error="No such file or directory: 'python-does-not-exist'",
    )
    assert result.error is not None
    assert "No such file" in result.error
    assert result.exit_code is None
    assert result.ok is False


def test_stop_reason_and_elapsed_ride_back() -> None:
    result = build_result(
        id="s",
        profile="p",
        state=_state(summary="x", stop_reason="end_turn"),
        outcome="ok",
        exit_code=0,
        elapsed_ms=4242,
    )
    assert result.stop_reason == "end_turn"
    assert result.elapsed_ms == 4242


def test_result_is_frozen() -> None:
    """The envelope is the parent's OUTPUT shape and must not be edited in place."""

    result = build_result(id="s", profile="p", state=_state(), outcome="ok", exit_code=0)
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is a dataclass detail
        result.ok = False  # type: ignore[misc]


# === Which model the child actually ran (A-5 half 2) ==========================


def test_the_envelope_records_the_child_s_model_and_provider() -> None:
    """The substitution was invisible, which is what made it dangerous.

    A profile that declares no ``model:`` emits no ``--model``, so the child runs
    the PERSISTED default rather than the parent's run-scope model — a different
    model at a different price. Nothing in the envelope, the footer or the
    ``/agents run`` grid named it, so the only way to notice was the bill.

    The reducer already fills these from every ``message_end``; this pins that
    they survive into the contract object callers actually read.
    """

    state = _StreamState()
    state.summary = "done"
    state.saw_agent_end = True
    state.provider = "openrouter"
    state.model = "deepseek/deepseek-v4-flash"

    result = build_result(id="s1", profile="explorer", state=state, exit_code=0)

    assert result.ok
    assert result.model == "deepseek/deepseek-v4-flash"
    assert result.provider == "openrouter"


def test_the_footer_names_the_model_only_when_there_is_one() -> None:
    """Gated, because the single-mode footer is pinned byte-for-byte elsewhere.

    A stub-driven result carries no model and its footer must not grow a stray
    separator; a real delegation must show what it ran on.
    """

    from aelix_agents.tool import _usage_line

    state = _StreamState()
    state.summary = "done"
    state.saw_agent_end = True
    silent = build_result(id="s1", profile="explorer", state=state, exit_code=0)
    # The exact single-mode shape, so a stray separator cannot creep in.
    assert _usage_line(silent) == "[agent explorer · ok · 0.0s]"

    state.provider = "openrouter"
    state.model = "deepseek/deepseek-v4-flash"
    named = build_result(id="s1", profile="explorer", state=state, exit_code=0)

    assert "deepseek/deepseek-v4-flash" in _usage_line(named)
    assert "deepseek/deepseek-v4-flash" not in _usage_line(silent)


# === the footer's width, measured against the renderer that imposes it ========
#
# LIVE QA, main @ 9ed0dde. A real delegation's card read
#
#   [agent explorer · ok · yolo · 1 turns · 3533 in / 4 out · $0.0036 · claude-…
#
# at EXACTLY 76 cells, identically at 120 and at 200 terminal columns — so this
# was never a narrow-terminal problem and widening the window never fixed it.
# ``tui/render.py``'s ``_truncate_lines`` caps every card line at a FIXED
# ``max_line_width = 76`` and appends ``…``; the model was simply the last field,
# and last is what a hard cut takes.


def _finished(**kwargs: object) -> SubagentResult:
    """A result shaped like a real one: a model, a posture, and every number."""

    from aelix_coding_agent.subagent_contract import SubagentUsage

    base: dict[str, object] = {
        "id": "s1",
        "profile": "explorer",
        "ok": True,
        "status": "ok",
        "summary": "done",
        "permission_mode": "yolo",
        "usage": SubagentUsage(input=3533, output=4, turns=1, cost=0.0036),
        "model": "claude-sonnet-4-5-20250929",
        "provider": "anthropic",
        "elapsed_ms": 3200,
    }
    base.update(kwargs)
    return SubagentResult(**base)  # type: ignore[arg-type]


def _card_line(result: SubagentResult) -> str:
    """The footer AS DRAWN — through product-core's real card renderer.

    Asserting against the renderer rather than against ``USAGE_LINE_MAX_CHARS``
    is the point: the budget is only correct while it agrees with the number
    ``render.py`` actually applies, and a copy of a constant cannot notice when
    the original moves.
    """

    from aelix_agents.tool import _usage_line
    from aelix_coding_agent.tui.render import _truncate_lines

    lines, hidden = _truncate_lines(_usage_line(result), max_lines=12)
    assert hidden == 0, "the footer must stay ONE card row"
    return lines[0]


def test_the_result_card_shows_the_whole_model() -> None:
    """The regression, pinned where it was measured: on screen, not in the string.

    ``…`` anywhere in the drawn line means the renderer cut something; the model
    must be present in full and must not be what pays for the width.
    """

    drawn = _card_line(_finished())

    assert "claude-sonnet-4-5-20250929" in drawn
    assert "…" not in drawn
    assert drawn.startswith("[agent explorer · claude-sonnet-4-5-20250929 · ok")


def test_a_wider_terminal_is_not_the_fix_and_is_not_needed() -> None:
    """The card cap does not grow with the terminal — that is why the live QA saw
    the identical cut at 120 and at 200 columns. The footer therefore has to fit
    the fixed width, and this asserts it does rather than asserting it is short.
    """

    from aelix_agents.tool import USAGE_LINE_MAX_CHARS, _usage_line

    assert len(_usage_line(_finished())) <= USAGE_LINE_MAX_CHARS
    assert "…" not in _card_line(_finished())


def test_the_fields_that_pay_for_the_width_are_chosen_not_sliced() -> None:
    """WHAT IS GIVEN UP, stated. At 76 cells a real footer cannot hold every
    field, so the low-value ones are dropped WHOLE — never cut mid-token, and
    never the identity, the outcome or the posture.

    ``turns`` and the raw token split go first; the cost and the elapsed time —
    what a reader scans a finished delegation for — survive.
    """

    from aelix_agents.tool import _usage_line

    line = _usage_line(_finished())

    assert line == (
        "[agent explorer · claude-sonnet-4-5-20250929 · ok · yolo · $0.0036 · 3.2s]"
    )
    assert "turns" not in line
    assert "in / " not in line


def test_a_footer_that_fits_is_untouched() -> None:
    """The common shape — no model resolved, modest numbers — is byte-identical to
    what shipped. Nothing is dropped while there is room, and the model term is
    still omitted rather than rendered empty."""

    from aelix_agents.tool import _usage_line
    from aelix_coding_agent.subagent_contract import SubagentUsage

    assert _usage_line(
        _finished(
            profile="scout",
            model=None,
            provider=None,
            permission_mode="plan",
            usage=SubagentUsage(input=900, output=120, turns=2, cost=0.0031),
            elapsed_ms=12_400,
        )
    ) == "[agent scout · ok · plan · 2 turns · 900 in / 120 out · $0.0031 · 12.4s]"


def test_a_hostile_model_cannot_corrupt_the_result_card() -> None:
    """``model`` is child-authored and this string is joined into the tool
    result's TEXT, which ``tui/render.py`` SPLITS ON NEWLINES into card rows. A
    raw ``\\n`` therefore added rows and a raw ESC rode into them — so the term is
    flattened, control-stripped and bounded here, at the source, exactly as the
    panel does it. ``profile`` gets the same treatment: it is a filename stem and
    POSIX permits a newline in one.
    """

    from aelix_agents.tool import USAGE_LINE_MAX_CHARS, _usage_line

    hostile = "gpt\n\x1b[31m FAKE\x9b2J\n" + "z" * 3000
    line = _usage_line(_finished(model=hostile))

    assert "\n" not in line
    assert "\x1b" not in line
    assert "\x9b" not in line  # C1: the one-byte CSI
    assert "FAKE" in line, "flattening must not silently delete the visible text"
    # Bounded at the source, so a 3 000-character model does not even reach the
    # renderer's own truncation: it is one ordinary card row, drawn whole.
    assert len(line) <= USAGE_LINE_MAX_CHARS
    assert _card_line(_finished(model=hostile)) == line

    # The PROFILE is the same hole from the other side. A profile long enough to
    # crowd out the model is a filename someone wrote, not something a child can
    # choose, so it is bounded but not budgeted — the renderer's own ``…`` is the
    # last word there. What must NEVER happen either way is a second row.
    for value in (hostile, "scout\nrm -rf /"):
        assert "\n" not in _usage_line(_finished(profile=value))
        _card_line(_finished(profile=value))  # asserts hidden == 0
