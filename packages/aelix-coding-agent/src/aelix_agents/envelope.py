""":class:`_StreamState` → :class:`SubagentResult` — ADR-0197 §(k).

PURE. No ``asyncio``, no ``os``, no ``subprocess``, no filesystem. Everything
this module needs about the process — the exit code, the stderr tail, the wall
clock — arrives as an argument, so the failure taxonomy, the 50 KiB cap and the
fallback chain can all be pinned without spawning anything.

THE ENVELOPE ALWAYS RETURNS, NEVER RAISES. A delegated child that died, timed
out, was killed, or was declined at the consent dialog still produces a
:class:`SubagentResult`. Divergence from pi, deliberate: pi throws on abort
(``index.ts:413``) and discards every partial the child streamed before it, so
the user loses the work AND the diagnosis. Aelix keeps both.
"""

from __future__ import annotations

import re

from aelix_coding_agent.subagent_contract import (
    SubagentOutcome,
    SubagentResult,
    SubagentUsage,
)

from aelix_agents.stream import _StreamState

# ``AgentProfile.output_cap``'s default (``agents/profile.py:212``), duplicated
# as a module constant so a caller that has no profile (a spawn that died before
# resolution) still has a budget.
DEFAULT_OUTPUT_CAP = 51200

NO_OUTPUT = "(no output)"
"""Terminal rung of the fallback chain. A literal sentinel rather than an empty
string: an empty ``summary`` renders as a blank panel and reads like a bug,
whereas "(no output)" is a fact the model and the user can both act on."""

_TRUNCATION_MARKER = (
    "\n\n[Output truncated: {omitted} bytes omitted. "
    "Full output preserved in tool details.]"
)

# Lines the SIGTERM path emits that are NORMAL and must not be shown as the
# child's failure reason. ``modes/print_mode.py``'s ``_signal_cleanup_and_exit``
# calls ``sys.exit(128 + sig)`` from inside a coroutine, so asyncio prints
# "Task exception was never retrieved" plus a ``SystemExit(143)`` traceback on
# every single cooperative kill. Surfacing that to the model as the reason a
# task was aborted is actively misleading.
#
# ``\s+\w`` is the traceback SOURCE-line pattern (any indented line); combined
# with ``\s+File "`` it removes the frame pairs. Applied ONLY on the
# aborted/timeout outcomes — on a genuine crash the traceback IS the diagnosis.
_STDERR_NOISE_RE = re.compile(
    r'^(Task exception was never retrieved'
    r"|future: <Task"
    r"|Traceback \(most recent call last\)"
    r'|\s+File "'
    r"|\s+\w"
    r"|SystemExit)"
)


def sanitize_stderr(text: str, *, outcome: SubagentOutcome) -> str:
    """The stderr tail as the MODEL should see it.

    Only ``aborted`` / ``timeout`` are filtered — those are the outcomes whose
    stderr is dominated by the shutdown-path noise above. The raw text is
    always preserved separately on ``SubagentResult.details`` (finding B8), so
    filtering here never destroys evidence.
    """

    if outcome not in ("aborted", "timeout"):
        return text.strip()
    kept = [line for line in text.splitlines() if not _STDERR_NOISE_RE.match(line)]
    return "\n".join(kept).strip()


def cap_summary(summary: str, cap: int) -> tuple[str, bool, int]:
    """Apply the output budget. Returns ``(text, truncated, omitted_bytes)``.

    The budget is measured in UTF-8 BYTES, not characters, and the cut is backed
    off to a code-point boundary — a naive character slice lets a CJK or
    emoji-heavy summary carry three to four times the intended payload into the
    parent's context window, which is the whole thing the cap exists to bound.

    The marker is appended AFTER the budget, so the returned string is slightly
    longer than ``cap``. That is intentional: silently eating the marker's own
    bytes would make the truncation invisible at exactly the sizes where it
    matters most.

    Deliberate fix of a pi gap: pi's ``truncateParallelOutput`` has a single
    call site (``index.ts:649``) on the parallel path only, so pi's single-mode
    delegation returns uncapped output.
    """

    raw = summary.encode("utf-8")
    if cap <= 0 or len(raw) <= cap:
        return summary, False, 0
    head = raw[:cap]
    # At most three iterations — a UTF-8 code point is at most 4 bytes.
    while head:
        try:
            kept = head.decode("utf-8")
            break
        except UnicodeDecodeError:
            head = head[:-1]
    else:
        kept = ""
    omitted = len(raw) - len(kept.encode("utf-8"))
    return kept + _TRUNCATION_MARKER.format(omitted=omitted), True, omitted


def _select_summary(state: _StreamState, stderr_clean: str, *, ok: bool) -> str:
    """The fallback chain — ADR-0197 §(k), after pi ``index.ts:186-191``.

    ``error_message`` → sanitized stderr tail → extracted summary →
    :data:`NO_OUTPUT`.

    The stderr rung is MANDATORY and is the reason this is a chain at all: a
    child launched with no API key exits 1 having written **zero** stdout bytes
    — not even the session header — so ``state`` is completely empty and stderr
    is the only thing that can explain the failure.

    DEVIATION from the literal chain, and it is load-bearing: the stderr rung is
    gated on ``not ok``. On a SUCCESSFUL run the child's own assistant text is
    authoritative and stderr is diagnostic noise — provider SDK / httpx logging
    and any extension ``print(..., file=sys.stderr)`` land in the same pipe
    (``print_mode.py:23-26`` declines pi's ``takeOverStdout``). Ungated, one
    ``DeprecationWarning`` on a perfectly successful delegation would replace
    the child's answer with a log line. On every FAILURE path the rung fires
    exactly as specified, including the zero-stdout case it exists for.

    THE ``error_message`` RUNG IS GATED THE SAME WAY, AND FOR THE TWIN REASON.
    ``_reduce_message_end`` (``stream.py:552-556``) is last-NON-EMPTY-wins per
    field, so ``state.error_message`` means "SOME turn errored", never "the run
    failed" — the harness's own auto-retry (``harness/core.py:494-495``, default
    ON, 3 attempts) recovers turn 1 on turn 2 and the child answers correctly.
    Ungated, that stale artifact of a retried turn REPLACES the child's real
    answer. Measured against a local endpoint returning 6 consecutive 429s (7
    HTTP calls — the provider SDK absorbs the first 3 and the harness retries
    twice): the envelope came back ``ok=True status='ok'`` carrying
    ``summary="Error code: 429 …"`` while the real answer sat in ``details``,
    and ``render_subagent_result`` renders only ``summary`` — so the PARENT
    MODEL received the error string as the child's work, and in chain mode
    ``ok=True`` kept the chain running and propagated it as ``{previous}``.
    A single 429 does NOT reproduce it; the SDK absorbs that one below the
    message layer and no ``error_message`` is ever emitted.

    On a genuine failure the rung fires exactly as specified: the last turn's
    error sets ``stop_reason`` too (``loop.py:322-325`` states the adapter
    contract), so ``ok`` is already False by the time this is reached.

    On ``aborted`` / ``timeout`` the sanitizer usually reduces the tail to the
    empty string (it is all shutdown noise), so the chain falls through to the
    partial summary the child did manage to stream — which is what §(j)
    promises for those outcomes.
    """

    if not ok and state.error_message:
        return state.error_message
    if not ok and stderr_clean:
        return stderr_clean
    if state.summary:
        return state.summary
    if stderr_clean:
        return stderr_clean
    return NO_OUTPUT


MAX_TRAIL_BYTES = 4096
"""How much rendered trail may travel with a result.

Deliberately far below both the summary cap (51200) and the chain's rendered-step
ceiling (65536): the trail is EVIDENCE accompanying an answer, not a second
answer. Sized so that even a trail at its cap leaves the summary its full
budget — measured, a full-cap summary plus a full-cap trail plus the fence still
fits one occurrence of ``{previous}``, and the chain drops the trail rather than
refusing a step when it would not (see ``chain.render_step``).

Lives HERE and not in ``subagent_contract``: the band gate holds that module's
public constants to an exact set, and a cap is exactly the kind of policy number
the 3-band rule keeps out of product-core."""

_TRAIL_MORE = "… {count} more"

STREAM_ENDED_EARLY = (
    "The delegated agent's output stream ended without its terminator: the "
    "child stopped mid-turn. Anything above is PARTIAL, not a finished answer."
)
"""Why a run with a plausible answer and a zero exit code is reported failed.

Without it the parent model gets ``is_error=True`` plus a partial that reads
like a complete answer and NO explanation, because ``state.error_message`` is
:data:`None` on that path — the child never got far enough to set one."""


def render_tool_trail(state: _StreamState) -> str | None:
    """One line per tool call, bounded and already sanitised. ``None`` if empty.

    ``ok`` / ``FAILED`` / ``(no result)`` rather than a symbol vocabulary of its
    own: ``(no result)`` is a call that started and never reported an end, which
    is what a child killed mid-tool leaves behind and is worth seeing as
    distinct from a success.

    TRUNCATION IS VISIBLE, ALWAYS. A trail that simply stopped would read as
    "that is everything the child did", and a reader — human or model — would
    draw conclusions from work it cannot see. Both truncation paths (the count
    bound in the reducer and the byte bound here) end in the same ``… N more``
    line, following the convention ``panel.format_panel`` already established.

    The arguments arrived through ``_strip_control`` at the reducer, so no entry
    can contain a newline and the line count of this block is therefore
    structural rather than child-controlled.
    """

    if not state.tool_trail:
        return None
    lines: list[str] = []
    used = 0
    hidden = state.trail_overflow
    for index, call in enumerate(state.tool_trail):
        verdict = (
            "(no result)"
            if call.failed is None
            else ("FAILED" if call.failed else "ok")
        )
        line = f"{call.name}({call.args}) {verdict}"
        cost = len(line.encode("utf-8")) + 1
        if used + cost > MAX_TRAIL_BYTES:
            hidden += len(state.tool_trail) - index
            break
        lines.append(line)
        used += cost
    if hidden:
        lines.append(_TRAIL_MORE.format(count=hidden))
    return "\n".join(lines) if lines else None


def _build_details(state: _StreamState, stderr_raw: str, *, ok: bool) -> str | None:
    """The UNCAPPED material behind ``summary`` (finding B8).

    ``summary`` is capped and its truncation marker promises "full output
    preserved in tool details". Without this field that promise is false on the
    ``/agents run`` door, which never builds a ``ToolResult`` at all, and no
    dashboard or Web UI consuming :class:`SubagentResult` could ever show it.

    The stderr half is the RAW text, not the sanitized one: whoever is reading
    details is debugging, and that is exactly when the SIGTERM traceback stops
    being noise.
    """

    parts: list[str] = []
    if state.summary:
        parts.append(state.summary)
    tail = stderr_raw.strip()
    if not ok and tail:
        parts.append(f"--- stderr ---\n{tail}")
    joined = "\n\n".join(parts)
    return joined or None


def build_result(
    *,
    id: str,
    profile: str,
    state: _StreamState,
    outcome: SubagentOutcome = "ok",
    exit_code: int | None = None,
    stderr_tail: str = "",
    elapsed_ms: int = 0,
    output_cap: int = DEFAULT_OUTPUT_CAP,
    permission_mode: str | None = None,
    dropped_tools: tuple[str, ...] = (),
    error: str | None = None,
) -> SubagentResult:
    """Fold a finished (or abandoned) child run into its envelope.

    ``outcome`` is the caller's PROPOSAL — what the process layer observed
    happen to the process. It may be tightened here but never loosened: a run
    the spawner believed succeeded is still reported as an error when the
    stream says so.

    NEVER TRUST THE RETURN CODE ALONE (§9.1). ``print_mode.py``'s
    ``stop_reason in ("error", "aborted") → exit_code = 1`` mapping is guarded
    by ``if mode == "text"``, so a JSON-mode child whose model errored — a bogus
    model id, an auth failure mid-stream — exits **0** with empty stderr while
    the event stream carries ``stop_reason: "error"``. Reading only the exit
    code reports that as a success with an empty summary.
    """

    # ``agent_end`` is the child's own terminator (``stream.py:231-235``). Its
    # absence in a stream that reached EOF is a child that stopped MID-TURN, and
    # the exit code cannot see it: measured against a real child that emits a
    # good ``message_end`` and then exits 0 without a terminator, this returned
    # ``ok=True status='ok' summary='the complete answer'`` for a run that never
    # finished.
    #
    # GUARDED BY ``dropped_lines == 0``, and the guard is the whole reason the
    # disjunct is safe. ``agent_end`` carries the entire message array on ONE
    # line (``stream.py:535-541``), so a child that read a large file emits a
    # terminator above ``MAX_LINE_BYTES`` and ``LineAssembler`` drops it — on a
    # run that finished perfectly. Measured: ``saw_agent_end=False,
    # dropped_lines=1, exit 0, summary='the complete answer'``. A bare
    # ``or not state.saw_agent_end`` FAILS that delegation. Once a line has been
    # dropped we cannot know whether the terminator was among them, so its
    # absence is not evidence and the run is judged on the exit code and the
    # stream alone.
    no_terminator = not state.saw_agent_end and state.dropped_lines == 0
    process_failed = (
        outcome != "ok"
        or (exit_code is not None and exit_code != 0)
        or state.stop_reason in ("error", "aborted")
    )
    failed = process_failed or no_terminator
    if outcome != "ok":
        status: SubagentOutcome = outcome
    elif state.stop_reason == "aborted":
        status = "aborted"
    elif failed:
        status = "error"
    else:
        status = "ok"
    ok = not failed

    stderr_clean = sanitize_stderr(stderr_tail, outcome=status)
    summary, truncated, _omitted = cap_summary(
        _select_summary(state, stderr_clean, ok=ok), output_cap
    )

    resolved_error = error
    if resolved_error is None and not ok:
        # SAFE BY CONSTRUCTION: ``_select_summary`` reads
        # ``state.error_message``, never this argument, so nothing computed here
        # can displace the child's partial answer — that displacement is exactly
        # the defect the ``not ok and`` gate above fixes. Both renderers print
        # ``error`` as a SEPARATE note gated on ``result.error not in body``
        # (``tool.render_subagent_result``, ``aggregate._member_block``).
        #
        # The sentinel fires only when the missing terminator is the SOLE
        # reason: on a timeout or an abort the status already says what
        # happened, and a second sentence about the stream would be true and
        # redundant.
        resolved_error = state.error_message or (
            STREAM_ENDED_EARLY if no_terminator and not process_failed else None
        )
    return SubagentResult(
        id=id,
        profile=profile,
        ok=ok,
        status=status,
        summary=summary,
        truncated=truncated,
        usage=SubagentUsage(
            input=state.input,
            output=state.output,
            cache_read=state.cache_read,
            cache_write=state.cache_write,
            cost=state.cost,
            tokens=state.tokens,
            turns=state.turns,
        ),
        error=resolved_error,
        exit_code=exit_code,
        stop_reason=state.stop_reason,
        elapsed_ms=elapsed_ms,
        dropped_tools=dropped_tools,
        details=_build_details(state, stderr_tail, ok=ok),
        dropped_lines=state.dropped_lines,
        permission_mode=permission_mode,
        tool_trail=render_tool_trail(state),
    )


def declined_result(
    *,
    id: str,
    profile: str,
    permission_mode: str | None = None,
    reason: str | None = None,
) -> SubagentResult:
    """The envelope for a spawn a human refused — ADR-0197 §(i).

    A decline is an OUTCOME, not an error: no process was ever created, so
    there is no exit code, no usage and no stderr. It is reported through the
    same channel as every other outcome so that ``/agents run``, the ``agent``
    tool and any future dashboard all render it without a special case, and so
    that a refusal can never be mistaken for a silent spawn.

    ``permission_mode`` still rides along — the authority that WOULD have been
    granted is part of the audit story even when it was not taken.
    """

    return SubagentResult(
        id=id,
        profile=profile,
        ok=False,
        status="declined",
        summary=reason or "Delegation declined by the user; no agent was started.",
        usage=SubagentUsage(),
        exit_code=None,
        permission_mode=permission_mode,
    )


__all__ = [
    "MAX_TRAIL_BYTES",
    "STREAM_ENDED_EARLY",
    "render_tool_trail",
    "DEFAULT_OUTPUT_CAP",
    "NO_OUTPUT",
    "build_result",
    "cap_summary",
    "declined_result",
    "sanitize_stderr",
]
