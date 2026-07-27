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
    result = build_result(
        id="s1",
        profile="scout",
        state=_state(summary="all good", stop_reason="end_turn", turns=2),
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
        state=_state(summary="the answer", stop_reason="end_turn"),
        outcome="ok",
        exit_code=0,
        stderr_tail="DeprecationWarning: ssl.PROTOCOL_TLS is deprecated\n",
    )
    assert result.ok is True
    assert result.summary == "the answer"


def test_no_output_sentinel() -> None:
    """An empty summary renders as a blank panel and reads like a bug."""

    result = build_result(id="s", profile="p", state=_state(), outcome="ok", exit_code=0)
    assert result.summary == NO_OUTPUT
    assert result.ok is True


def test_stderr_only_success_still_surfaces_something() -> None:
    """The last rung before the sentinel fires even on a successful run.

    A child that exited 0 having said nothing on stdout but something on stderr
    is better reported with that text than with "(no output)".
    """

    result = build_result(
        id="s",
        profile="p",
        state=_state(),
        outcome="ok",
        exit_code=0,
        stderr_tail="wrote 3 files",
    )
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

    succeeded = build_result(
        id="s", profile="p", state=_state(summary="fine"), outcome="ok",
        exit_code=0, stderr_tail="chatty log",
    )
    assert succeeded.details == "fine"


def test_details_is_none_when_there_is_nothing_behind_the_summary() -> None:
    result = build_result(id="s", profile="p", state=_state(), outcome="ok", exit_code=0)
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
