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

    On ``aborted`` / ``timeout`` the sanitizer usually reduces the tail to the
    empty string (it is all shutdown noise), so the chain falls through to the
    partial summary the child did manage to stream — which is what §(j)
    promises for those outcomes.
    """

    if state.error_message:
        return state.error_message
    if not ok and stderr_clean:
        return stderr_clean
    if state.summary:
        return state.summary
    if stderr_clean:
        return stderr_clean
    return NO_OUTPUT


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

    failed = (
        outcome != "ok"
        or (exit_code is not None and exit_code != 0)
        or state.stop_reason in ("error", "aborted")
    )
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
        error=error if error is not None else (state.error_message if not ok else None),
        exit_code=exit_code,
        stop_reason=state.stop_reason,
        elapsed_ms=elapsed_ms,
        dropped_tools=dropped_tools,
        details=_build_details(state, stderr_tail, ok=ok),
        dropped_lines=state.dropped_lines,
        permission_mode=permission_mode,
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
    "DEFAULT_OUTPUT_CAP",
    "NO_OUTPUT",
    "build_result",
    "cap_summary",
    "declined_result",
    "sanitize_stderr",
]
