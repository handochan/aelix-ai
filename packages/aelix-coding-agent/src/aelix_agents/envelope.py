""":class:`_StreamState` → :class:`SubagentResult` — ADR-0197 §(k).

PURE. No ``asyncio``, no ``os``, no ``subprocess``, no filesystem. Everything
this module needs about the process — the exit code, the stderr tail, the wall
clock — arrives as an argument, so the failure taxonomy, the 50 KiB cap and the
fallback chain can all be pinned without spawning anything.

THE ENVELOPE ALWAYS RETURNS, NEVER RAISES. A delegated child that died, was
killed or stopped, or was declined at the consent dialog still produces a
:class:`SubagentResult`. Divergence from pi, deliberate: pi throws on abort
(``index.ts:413``) and discards every partial the child streamed before it, so
the user loses the work AND the diagnosis. Aelix keeps both.
"""

from __future__ import annotations

import re
import unicodedata

from aelix_ai.utils.terminal_text import safe_for_terminal
from aelix_coding_agent.subagent_contract import (
    SubagentOutcome,
    SubagentResult,
    SubagentUsage,
)

from aelix_agents.stream import _StreamState, _strip_control

# ``AgentProfile.output_cap``'s default (``agents/profile.py:229``), duplicated
# as a module constant so a caller that has no profile (a spawn that died before
# resolution) still has a budget.
DEFAULT_OUTPUT_CAP = 51200

NO_OUTPUT = "(no output)"
"""Terminal rung of the fallback chain. A literal sentinel rather than an empty
string: an empty ``summary`` renders as a blank panel and reads like a bug,
whereas "(no output)" is a fact the model and the user can both act on."""

_TRUNCATION_MARKER = "\n\n[Output truncated: {omitted} bytes omitted.{where}]"
"""The marker a capped summary ends with — and it says only what is TRUE (#199).

It used to end "Full output preserved in tool details." That was false the
moment the call returned: ``ToolResult.details`` is never persisted
(``loop._to_tool_result_message`` drops it, #168) and nothing in the TUI renders
it for ``agent``, so neither the model nor a human could follow it.

``{where}`` is :data:`_RECORDED_WHERE` when the full text is kept in a delegated
session file the parent allocated (see :attr:`SubagentResult.output_recorded`),
and EMPTY otherwise — no claim, rather than a claim nobody can check. No path is
ever written here: a path would put the user's home directory and name into the
model's context, and the parent's session records already carry it."""

_RECORDED_WHERE = " The full output is recorded in the delegated session."

_MARKER_RE = re.compile(r"\n\n\[Output truncated: (\d+) bytes omitted\.(?: [^\]\n]*)?\]\Z")
"""A marker :func:`cap_summary` wrote, at the very END of a summary.

Read back by :func:`recap_summary` only when the envelope says it was truncated,
so a child whose answer merely ends in similar-looking text is never parsed."""

# Lines a task-borne exception in the child emits that are NORMAL and must not
# be shown as the child's failure reason. Surfacing an asyncio "Task exception
# was never retrieved" report to the model as the reason a task was aborted is
# actively misleading.
#
# UNTIL #220 this was what a cooperative kill itself produced, on every single
# one: ``modes/print_mode.py``'s ``_signal_cleanup_and_exit`` called
# ``sys.exit(128 + sig)`` from inside a coroutine, so asyncio printed that line
# plus a ``SystemExit(143)`` traceback. It no longer does — the handler RECORDS
# ``128 + sig`` and ``run_print_mode`` returns it, and a real child's SIGTERM
# stderr is now "Request aborted" and nothing else (MEASURED,
# ``.omc/specs/220-progress-2026-09-05.md`` §1).
#
# THE FILTER STAYS ANYWAY, as a defensive one, and that is a decision rather
# than an oversight: any OTHER unretrieved task exception in the child
# (``tools/bash.py``'s drain and signal watchers, ``rpc/rpc_mode.py``'s
# background tasks) still produces exactly this shape, and it is applied ONLY on
# the aborted/timeout outcomes — the outcomes where a traceback is not the
# diagnosis. On a genuine crash it is not applied at all, so nothing that IS the
# diagnosis is ever swallowed.
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


_ESCAPE_SEQUENCE = re.compile(
    # CSI — ``ESC [`` or the one-byte C1 CSI, parameter bytes, intermediate
    # bytes, one final byte: colours, cursor moves, clears (``ESC [ 1 ; 35 m``).
    r"(?:\x1b\[|\x9b)[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]"
    # OSC, and the other string sequences (DCS, SOS, PM, APC) — ``ESC ]`` or
    # its C1 byte, a payload on one line, then BEL or ST (``ESC \`` or C1 ST):
    # a window title, a clipboard write, an OSC 8 hyperlink's two halves.
    r"|(?:\x1b[\]PX^_]|[\x90\x98\x9d\x9e\x9f])[^\x07\x1b\x80-\x9f\n]*(?:\x07|\x1b\\|\x9c)"
    # Every other escape: ``ESC``, any intermediate bytes, one final byte —
    # the two-byte ones (``ESC 7``, ``ESC M``, ``ESC c``) and ``tput sgr0``'s
    # ``ESC ( B``. A SPACE is not an intermediate here: an ESC before a space
    # goes alone; one directly before a letter takes it, as a terminal would.
    r"|\x1b[\x21-\x2f]*[\x30-\x7e]"
)
"""A whole terminal escape sequence, removed by :func:`child_prose` (#604 review
round 2). Deleting only the ESC byte left the rest as inert residue, so a
colourised traceback read ``[1;35mValueError [0m: …`` and its reason began
there. A sequence that does not complete (no final byte, an OSC with no
terminator on its line) is not matched whole: its ESC goes, with the byte
after it when the two read as an escape (``\x1b[31`` leaves ``31``)."""


def _without_format_characters(text: str) -> str:
    """``text`` without any Unicode FORMAT character (category ``Cf``).

    All of them, not a chosen few: the shared terminal strip removes the BiDi
    controls, the zero-width space and the byte-order mark, but a word joiner
    (U+2060), a zero-width joiner (U+200D), a soft hyphen (U+00AD) or a
    left-to-right mark (U+200E) inside ``[aelix system report`` or a fence tag
    hid it from the match that neutralises it, while the model reads past them
    (#604 review round 2). The invisible tag characters (U+E0000 block) go too.

    🔴 ZWJ INCLUDED, against ``aelix_ai.utils.terminal_text``'s choice to keep
    it, and that is a decision for these paths only: an emoji sequence such as
    ``👩‍💻`` falls apart into its parts. That is the accepted cosmetic loss of a
    FAILURE report the model reads, where a forgery the model cannot see is the
    cost on the other side; the panels and every success keep their ZWJ.
    """

    if text.isascii():
        return text
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def child_prose(text: str) -> str:
    """Child-authored text as the #604 surfaces hand it to the model.

    The unfinished template (ADR-0276 D11), the unfinished member block (D12)
    and the cancel report (D14) carry a child's partial answer, its error and
    its tool arguments, and the report does so in a USER-role message (#604
    review: an ANSI clear-screen, a CR and a forged report header all reached
    the next prompt verbatim). In order:

    1. every whole escape sequence goes (:data:`_ESCAPE_SEQUENCE`), so no
       inert ``[2J`` or ``[35m`` is left behind;
    2. CR and CRLF become a newline, so a CR cannot overwrite a line on a
       terminal that later shows the text;
    3. the shared ``aelix_ai`` terminal strip — the one the batch panel and the
       ``/agents run`` panel use — deletes what is left of C0 (newline and tab
       kept), DEL, C1, the BiDi controls and the line and paragraph separators;
    4. every Unicode format character goes (:func:`_without_format_characters`).

    Idempotent: nothing it returns is removed by a second pass. Success results
    and refusals do not pass through here: they render byte for byte as before
    (D18).
    """

    text = _ESCAPE_SEQUENCE.sub("", text)
    text = safe_for_terminal(
        text.replace("\r\n", "\n").replace("\r", "\n"), keep_newline=True, keep_tab=True
    )
    return _without_format_characters(text)


def child_line(text: str) -> str:
    """:func:`child_prose` folded onto ONE line, for a heading, a reason, a path or
    a session id — anything whose own line count must not be the child's to
    choose (:func:`~aelix_agents.stream._strip_control`)."""

    return _strip_control(child_prose(text))


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


def _cut(summary: str, cap: int) -> tuple[str, int]:
    """``summary`` cut to ``cap`` UTF-8 bytes on a code-point boundary.

    Returns ``(kept, omitted_bytes)``; ``omitted_bytes == 0`` means nothing was
    cut (``cap <= 0`` disables the cap, as it always has).

    ``surrogatepass`` on every encode and decode, so it never raises. A lone
    surrogate is a legal ``str`` that a child's JSON can carry (``"\\ud83d"``),
    and so can an ``OSError`` naming an undecodable path; a bare
    ``encode("utf-8")`` raised ``UnicodeEncodeError`` out of the envelope
    builder, and out of the batch renderer once an error note went through
    here (#199). Such a code point counts as the three bytes it takes.
    """

    raw = summary.encode("utf-8", "surrogatepass")
    if cap <= 0 or len(raw) <= cap:
        return summary, 0
    head = raw[:cap]
    # At most three iterations — a UTF-8 code point is at most 4 bytes.
    while head:
        try:
            kept = head.decode("utf-8", "surrogatepass")
            break
        except UnicodeDecodeError:
            head = head[:-1]
    else:
        kept = ""
    return kept, len(raw) - len(kept.encode("utf-8", "surrogatepass"))


def _marker(omitted: int, *, recorded: bool) -> str:
    return _TRUNCATION_MARKER.format(
        omitted=omitted, where=_RECORDED_WHERE if recorded else ""
    )


def cap_summary(
    summary: str, cap: int, *, recorded: bool = False
) -> tuple[str, bool, int]:
    """Apply the output budget. Returns ``(text, truncated, omitted_bytes)``.

    The budget is measured in UTF-8 BYTES, not characters, and the cut is backed
    off to a code-point boundary — a naive character slice lets a CJK or
    emoji-heavy summary carry three to four times the intended payload into the
    parent's context window, which is the whole thing the cap exists to bound.

    The marker is appended AFTER the budget, so the returned string is slightly
    longer than ``cap``. That is intentional: silently eating the marker's own
    bytes would make the truncation invisible at exactly the sizes where it
    matters most.

    ``recorded`` decides whether the marker may say where the rest is — see
    :data:`_TRUNCATION_MARKER`. It defaults to the claim-free marker, which is
    true of every caller.

    Deliberate fix of a pi gap: pi's ``truncateParallelOutput`` has a single
    call site (``index.ts:649``) on the parallel path only, so pi's single-mode
    delegation returns uncapped output.
    """

    kept, omitted = _cut(summary, cap)
    if not omitted:
        return summary, False, 0
    return kept + _marker(omitted, recorded=recorded), True, omitted


def recap_summary(
    summary: str, cap: int, *, truncated: bool, recorded: bool = False
) -> tuple[str, bool, int]:
    """Cap an envelope's summary AGAIN, to a smaller budget (#199, A.9 (c)).

    The batch renderer gives every member an even share of one per-call budget
    (``aggregate.BATCH_OUTPUT_BUDGET_BYTES``), after the channel has already
    capped each summary at the profile's ``output_cap``. Capping the capped text
    with :func:`cap_summary` alone would cut the first marker off and report
    only the bytes cut the SECOND time, understating what the model is missing.
    So a marker this module wrote is stripped first — only when ``truncated``
    (the envelope's own flag) says there is one — and the two counts are added.

    Returns ``(text, truncated, omitted_bytes)`` with the same meaning as
    :func:`cap_summary`; a summary that already fits is returned untouched,
    marker and all.
    """

    body, prior = _split_marker(summary, truncated=truncated)
    kept, omitted = _cut(body, cap)
    if not omitted:
        return summary, prior > 0, prior
    total = prior + omitted
    return kept + _marker(total, recorded=recorded), True, total


def _split_marker(summary: str, *, truncated: bool) -> tuple[str, int]:
    """``summary`` without the marker :func:`cap_summary` ended it with, and that
    marker's count — or ``(summary, 0)``. Read only when ``truncated`` (the
    envelope's own flag) says a marker is there."""

    if truncated:
        match = _MARKER_RE.search(summary)
        if match is not None:
            return summary[: match.start()], int(match.group(1))
    return summary, 0


def error_repeats_summary(summary: str, error: str, *, truncated: bool) -> bool:
    """Would an ``Error:`` note only repeat what ``summary`` already says?

    Yes when the error is inside the summary — the rule both renderers always
    had — and ALSO when the summary is the error cut short. A failed child that
    wrote no text of its own has its final error as its summary
    (:func:`_select_summary`'s second rung since #604; its first before),
    capped at ``output_cap``; an error longer than the cap is then never "in"
    the summary, so it came back in full as a note after the cut it began.
    Measured before this: one 60,000-byte error message made a single
    delegation return 111,336 bytes and an eight-member batch 546,832, past
    every cap either had (#199 review). Nothing but the reducer's 4 MiB line
    limit bounds such an error.

    Only a TRUNCATED summary can be a cut: an uncut one that merely begins an
    error (a stderr ``Error`` beside ``Error: the details``) still gets its
    note.
    """

    if error in summary:
        return True
    if not truncated:
        return False
    body, _omitted = _split_marker(summary, truncated=True)
    return bool(body) and error.startswith(body)


def summary_is_the_error(summary: str, error: str | None, *, truncated: bool) -> bool:
    """Is ``summary`` the run's error itself, rather than text the child wrote?

    True when the fallback chain had nothing of the child's own to select and
    reached the final error (:func:`_select_summary`'s second rung on a failure)
    — the whole error, or the error cut at ``output_cap``. Stricter than
    :func:`error_repeats_summary` on purpose: a partial answer that merely
    QUOTES its error ("the build failed: X") is the child's text and must still
    be shown as its partial result (ADR-0276 D11).
    """

    if not error or not summary:
        return False
    if summary == error:
        return True
    if not truncated:
        return False
    body, _omitted = _split_marker(summary, truncated=True)
    return bool(body) and error.startswith(body)


def says_nothing(summary: str, *, truncated: bool) -> bool:
    """Is ``summary`` empty once cleaned (:func:`child_prose`), its truncation
    marker aside? (#604 review round 2)

    A partial answer made only of control or format characters — or of blank
    lines — rendered as an empty ``Partial result:`` in D11 and D12, because
    "it wrote no text" was decided on the raw, non-empty value. Both renderers
    ask this instead, on what the model would actually read.
    """

    body, _omitted = _split_marker(summary, truncated=truncated)
    return not child_prose(body).strip()


def _select_summary(state: _StreamState, stderr_clean: str, *, ok: bool) -> str:
    """The fallback chain — ADR-0197 §(k), reordered by ADR-0276 D9 (#604).

    On a FAILURE or a stop: the partial answer (``state.summary``) → the final
    error (``state.final_error``) → :data:`NO_OUTPUT`. On a SUCCESS: the answer →
    the stderr tail → :data:`NO_OUTPUT`, as it always was.

    THE PARTIAL ANSWER LEADS ON A FAILURE NOW, and that is the #604 change. The
    chain used to put the error and then ANY surviving stderr line ahead of it,
    so the work a stopped child had done was replaced by its diagnosis: a real
    child's SIGTERM stderr is "Request aborted" (measured at #220 — the
    sanitizer below keeps it, because it is not a traceback), so every child the
    parent stopped came back as "Request aborted" with its partial answer only
    in ``details``, which the model never reads. The diagnosis is not lost by
    moving it down: ``build_result`` resolves it into ``error`` (ADR-0276 D17),
    and both renderers print that as its own note.

    AND THE STDERR IS NO SUMMARY OF A FAILURE AT ALL (#604 review). As the
    chain's third rung it was presented as the child's partial result: a child
    with no API key read ``Partial result: No API key found…`` with ``Error:
    exited with code 1`` under it, and a child stopped before it wrote anything
    read ``Partial result: Request aborted``. The stderr is the diagnosis, so
    it is resolved into ``error`` — and from there into the reason — while the
    summary says the child wrote nothing.

    THE ERROR RUNG IS THE FINAL ERROR, NOT ``error_message``. ``_reduce_message_end``
    is last-NON-EMPTY-wins per field, so ``state.error_message`` means "SOME turn
    errored" — the harness's auto-retry recovers turn 1 on turn 2, and the 429 it
    recovered from stays in that field for the rest of the run. Read here, it
    became the stated reason a run that later failed for something else did not
    finish. ``state.final_error`` is reset on every assistant message and names
    an error only while the LATEST message is one (D17).

    The stderr is still MANDATORY on a failure, just not here: a child launched
    with no API key exits 1 having written **zero** stdout bytes — not even the
    session header — so ``state`` is completely empty and stderr is the only
    thing that can explain the failure. :func:`_resolve_error` carries it.

    ON A SUCCESS THE STDERR RUNG COMES AFTER THE ANSWER, and that is load-bearing:
    there the child's own assistant text is authoritative and stderr is
    diagnostic noise — provider SDK / httpx logging and any extension
    ``print(..., file=sys.stderr)`` land in the same pipe (``print_mode.py:64-67``
    declines pi's ``takeOverStdout``). One ``DeprecationWarning`` on a perfectly
    successful delegation must not replace the child's answer with a log line.
    For the twin reason no error is read on a success at all: measured against a
    local endpoint returning 6 consecutive 429s, a recovered run used to come
    back ``ok=True`` with ``summary="Error code: 429 …"`` and the real answer
    stranded in ``details``.

    ``stderr_clean`` is read on a success only.
    """

    if not ok:
        if state.summary:
            return state.summary
        if state.final_error:
            return state.final_error
        return NO_OUTPUT
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

LINGERED_NOTE = "the agent finished but did not exit within 30 s; it was stopped"
"""What a run reaped after a COMPLETE stream says about itself (#604, ADR-0276 D4).

``build_result(lingered=True)`` means the channel saw both pipes close and then
waited ``print_channel.POST_EOF_EXIT_BOUND_SECONDS`` for an exit that never
came, and reaped the child. When the stream had already reached its terminator
with a stop reason that is neither ``error`` nor ``aborted``, the answer is
whole: the reap was only cleanup, so the run stays ``ok`` and carries this as
:attr:`SubagentResult.note` instead of being failed on the signal's exit code.

The "30 s" is the bound's own value, spelled out because this module is PURE and
cannot import the channel that owns it (the channel imports this module);
``test_the_lingered_texts_name_the_channels_own_bound`` holds the two together."""

LINGERED_REASON = "the agent closed its output but did not exit within 30 s"
"""Why a lingering run that had NOT finished its stream is a failure (ADR-0276 D4).

The other half of :data:`LINGERED_NOTE`: no terminator, or a stop reason of
``error`` / ``aborted``, means the closed pipes said nothing about the answer
being whole, so the run is ``error``. It is the lingering row of the reason
table (D10) and is carried as :attr:`SubagentResult.reason`; ``error`` holds
whatever diagnosis the run itself left (D17)."""

REASON_STOPPED = "stopped by the session (stop, /new, /resume, /fork or quit)"
"""Why an ``aborted`` run the CALLER proposed did not finish (ADR-0276 D10).

Both channels propose ``outcome="aborted"`` only when ``row.stopped`` is set,
i.e. ``stop`` or ``stop_all`` killed the child — and ``stop_all`` runs at
``session_shutdown``, which ``/new``, ``/resume``, ``/fork`` and quit all emit."""

REASON_CANCELLED = "the turn was cancelled"
"""Why a run whose turn was cancelled did not finish (ADR-0276 D10, D14).

NEUTRAL ABOUT WHO CANCELLED IT (#604 review). Esc and Ctrl+C are the common
door, but an RPC ``abort``, an embedder's ``harness.abort()`` and the TUI's
SIGTERM / SIGHUP handler (D16) cancel the turn the same way, and this frame
cannot tell them apart — so it names none of them.

Never produced by :func:`build_result` on its own: a cancel builds no envelope
through a channel. ``runtime._run`` passes it as ``reason=`` when it builds the
one envelope a cancelled spawn gets, for the report the next turn carries."""

REASON_OWN_ABORT = "the agent's own run was aborted"
"""Why a run is ``aborted`` when nobody on the parent's side stopped it: the
child's own stream ended with ``stop_reason: "aborted"`` (ADR-0276 D10). Not
"the turn was cancelled" — that is the parent's Esc, which this is not."""

REASON_UNSPECIFIED = "its run failed without an error message"
"""The reason of last resort, for a failure that left nothing to quote — no
error of its own, no stderr, a clean exit code, and a terminator. Rare, but a
started failure is always rendered with a reason line, and an empty one would
read like a renderer bug."""

MAX_REASON_CHARS = 300
"""The longest :attr:`SubagentResult.reason` (ADR-0276 D10). One line in a
heading, so a 64 KiB stderr dump cannot become one."""

MAX_ERROR_NOTE_BYTES = 4096
"""How much of ``error`` the single-mode ``Error:`` note may carry (ADR-0276 D17).

An RPC child that dies mid-turn raises ``RpcServerExited`` carrying its whole
stderr snapshot — up to ``print_channel.STDERR_RING_BYTES`` (64 KiB) — and that
became ``error`` verbatim, printed uncapped beside a summary. The batch renderer
already capped its note (a share of the member budget); this is the single-mode
twin. Below the cap nothing changes, which is every refusal and decline."""

_SIGNAL_NAMES = {
    1: "SIGHUP",
    2: "SIGINT",
    3: "SIGQUIT",
    6: "SIGABRT",
    9: "SIGKILL",
    11: "SIGSEGV",
    13: "SIGPIPE",
    14: "SIGALRM",
    15: "SIGTERM",
}
"""The POSIX signals whose numbers are the same on Linux and macOS.

A TABLE, not ``signal.Signals(n).name``: Windows has no ``SIGKILL`` (``Signals(9)``
raises there), and this module must read the same everywhere. A negative exit
code only ever comes from a POSIX child anyway; anything off the table is named
by its number."""


def describe_exit(code: int) -> str:
    """A process exit status, in words: ``exited with code 1`` or ``killed by
    signal SIGKILL`` (asyncio reports a signal death as ``-signum``).

    The rung of :func:`build_result`'s error chain that diagnoses a death the
    stream never saw — an OOM kill leaves exit ``-9``, no stderr and no error of
    its own, and before #604 such a run came back ``error`` with nothing at all
    to say why (design critique 3)."""

    if code < 0:
        number = -code
        name = _SIGNAL_NAMES.get(number)
        return f"killed by signal {name}" if name else f"killed by signal {number}"
    return f"exited with code {code}"


_TRAIL_MORE_RE = re.compile(r"… (\d+) more")
"""The last line :func:`render_tool_trail` writes when it hid calls."""


def split_trail(trail: str | None) -> tuple[list[str], int]:
    """A rendered trail as ``(entry lines, calls it did not show)``.

    The line count is STRUCTURAL, which is what makes this safe to parse: every
    entry's arguments went through ``_strip_control`` at the reducer, so no entry
    contains a newline, and every entry ends in its verdict, so none can be the
    ``… N more`` line :func:`render_tool_trail` ends a cut trail with.
    """

    if not trail:
        return [], 0
    lines = trail.split("\n")
    match = _TRAIL_MORE_RE.fullmatch(lines[-1])
    if match is not None:
        return lines[:-1], int(match.group(1))
    return lines, 0


def count_trail_calls(trail: str | None) -> int:
    """How many tool calls a rendered trail stands for, shown or not."""

    entries, hidden = split_trail(trail)
    return len(entries) + hidden


def _first_line(lines: list[str]) -> str:
    """The first non-blank of ``lines``, de-fanged, at most :data:`MAX_REASON_CHARS`."""

    for raw in lines:
        line = _strip_control(raw)
        if line:
            if len(line) > MAX_REASON_CHARS:
                return line[: MAX_REASON_CHARS - 1] + "…"
            return line
    return ""


_TRACEBACK_HEAD = "Traceback (most recent call last):"

_GROUP_TRACEBACK_HEAD = "+ Exception Group Traceback (most recent call last):"
"""The header of a top-level exception group's traceback (Python 3.11+; an
``asyncio.TaskGroup`` failure is one). Its frames and its own exception line
follow under a ``|`` margin in the header's ``+`` column, and its
sub-exceptions after a ``+-+---- 1 ----`` rule, each one level deeper — where a
nested ``Traceback`` header carries the margin and is not a top-level one."""

_MARGIN = re.compile(r"[ \t]*\|")


def _group_frame(after: list[str]) -> list[str]:
    """The lines under a top-level exception group's header, out of their ``|``
    margin: its frames, still indented, and its own exception line, not — up
    to the rule that opens its sub-exceptions.

    The margin is read off the line after the header rather than the header's
    own indentation, which a stripped stderr tail has already lost.
    """

    first = _MARGIN.match(after[0]) if after else None
    if first is None:
        return []
    margin = first.group(0)
    lines: list[str] = []
    for line in after:
        if not line.startswith(margin):
            break
        rest = line[len(margin) :]
        lines.append(rest[1:] if rest.startswith(" ") else rest)
    return lines


def _reason_line(text: str) -> str:
    """The one line of an error that says why the run failed (ADR-0276 D10).

    Read on the CLEANED text (:func:`child_prose`), so a colourised traceback's
    escape sequences neither hide its header nor begin its reason (#604 review
    round 2: ``[35mTraceback…``).

    Its first non-blank line — except in a Python TRACEBACK, whose first line is
    the header ``Traceback (most recent call last):`` and says nothing (#604
    review: a child that crashed on an uncaught exception was reported as "did
    not finish: Traceback (most recent call last):"). There the reason is the
    exception line: the first unindented line after the LAST header, so a
    chained exception ("During handling of the above exception…") names the
    one that ended the run, and log lines before the traceback are skipped.
    With no such line (a tail cut mid-traceback), the last non-blank line.

    An EXCEPTION GROUP's header (:data:`_GROUP_TRACEBACK_HEAD`) counts as a
    header too, and its lines are read out of their ``|`` margin: the reason is
    the group's own exception line (``ExceptionGroup: unhandled errors in a
    TaskGroup (1 sub-exception)``), not ``+ Exception Group Traceback…`` and not
    a sub-exception — the group is what ended the run, as the last exception of
    a chain is, and the sub-exceptions stay in the error note.
    """

    lines = child_prose(text).split("\n")
    heads = [
        index
        for index, line in enumerate(lines)
        if line.strip() in (_TRACEBACK_HEAD, _GROUP_TRACEBACK_HEAD)
    ]
    if not heads:
        return _first_line(lines)
    after = lines[heads[-1] + 1 :]
    if lines[heads[-1]].strip() == _GROUP_TRACEBACK_HEAD:
        after = _group_frame(after)
    exception = next(
        (line for line in after if line.strip() and not line[:1].isspace()), None
    )
    if exception is None:
        exception = next((line for line in reversed(after) if line.strip()), "")
    return _first_line([exception]) or _first_line(lines)


def error_says_only_the_reason(error: str | None, reason: str | None) -> bool:
    """Would an ``Error:`` note only repeat the reason line above it? (#604 review)

    True when the error is ONE line whose de-fanged text is the reason, uncut —
    a one-line provider 400, say, which became both the heading's reason and
    the note under it, so the card printed the same line twice. An error with
    more to say (more lines, or longer than the reason's cap) keeps its note.

    Compared as both are shown: the error cleaned the way the note prints it
    (:func:`child_prose`), as the reason was cleaned when it was taken from it.
    """

    if not error or not reason:
        return False
    lines = [line for line in child_prose(error).split("\n") if line.strip()]
    return len(lines) == 1 and _strip_control(lines[0]) == reason


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

    ``summary`` is capped, and without this field no dashboard or Web UI
    consuming :class:`SubagentResult` could ever show what the cap removed. It
    lives only as long as the call: ``ToolResult.details`` is never persisted
    (#168), which is why the truncation marker points at the delegated session
    file instead (:data:`_TRUNCATION_MARKER`).

    The stderr half is the RAW text, not the sanitized one: whoever is reading
    details is debugging, and that is exactly when the SIGTERM traceback stops
    being noise.

    On a failure the stream's last error message is kept here too (ADR-0276
    D17). When it is STALE — a 429 the harness recovered from before the run
    failed for another reason — this is the only place it goes: it is evidence
    about the run, not the reason the run did not finish. When it is the final
    error it may also be the summary or ``error``, or neither: a caller's own
    ``error`` (a dead RPC child's exit) outranks it in the resolved chain, and it
    must not vanish because of that.
    """

    parts: list[str] = []
    if state.summary:
        parts.append(state.summary)
    tail = stderr_raw.strip()
    if not ok and tail:
        parts.append(f"--- stderr ---\n{tail}")
    if not ok and state.error_message:
        parts.append(f"--- the stream's last error message ---\n{state.error_message}")
    joined = "\n\n".join(parts)
    return joined or None


def _resolve_error(
    *,
    error: str | None,
    ok: bool,
    status: SubagentOutcome,
    state: _StreamState,
    stderr_clean: str,
    exit_code: int | None,
    lingered: bool,
    no_terminator: bool,
) -> str | None:
    """The diagnosis a failed run carries in ``error`` (ADR-0276 D17).

    The caller's ``error`` first, verbatim — and on a run that failed without
    being aborted, the sanitized stderr after it under a ``stderr:`` line, when
    the stderr says something the caller's error does not already contain
    (#604 review round 2). A channel's own error is about the CHANNEL — the
    print channel's last line of defence reports the exception that broke it,
    RpcChannel a stdin that closed — and since the summary of a failure is
    never the stderr (D9), the child's own diagnosis (``No API key found…``)
    was left in ``details``, which the model never reads. An error that
    already quotes the stderr (``RpcServerExited``'s ``Stderr: …``) gains
    nothing; a blank one (an exception with no message) gives way to the
    stderr. The reason is still the CALLER's error's line when it has one
    (:func:`build_result`). The caller's error otherwise stands as it came —
    a spawn that failed has no stream, and none of the rungs below is true of
    it.

    Otherwise, on a run that is not ``ok``, the first of these that has a value:

    1. the final error (:attr:`_StreamState.final_error`) — never a stale one;
    2. the sanitized stderr tail — the diagnosis of a child that wrote nothing
       else, such as one with no API key (the summary of a failure is never the
       stderr, :func:`_select_summary`, so nothing prints it twice);
    3. the exit status in words (:func:`describe_exit`) — the only trace an OOM
       kill leaves;
    4. :data:`STREAM_ENDED_EARLY` when the terminator is missing.

    AN ABORT STOPS AFTER RUNG 1. Its reason line already says who stopped it
    (D10), its stderr is the shutdown of a process the parent killed, and its
    exit code is the parent's own signal — none of it is a diagnosis, and the
    sentinel about the stream would be true and redundant (the status says it).
    A LINGERED run skips rung 3 for the same reason: that exit code is the
    reaper's (D4).

    Moving the stderr out of the summary (D9) is what makes this chain
    necessary rather than a refinement: without rungs 2-3 a stderr-only
    diagnosis or an OOM kill beside a partial answer would reach the model
    nowhere at all (design critique 3).
    """

    if error is not None:
        if not ok and status != "aborted" and stderr_clean and stderr_clean not in error:
            return f"{error}\n\nstderr:\n{stderr_clean}" if error.strip() else stderr_clean
        return error
    if ok:
        return None
    if state.final_error:
        return state.final_error
    if status == "aborted":
        return None
    if stderr_clean:
        return stderr_clean
    if not lingered and exit_code is not None and exit_code != 0:
        return describe_exit(exit_code)
    if no_terminator:
        return STREAM_ENDED_EARLY
    return None


def _reason_for(
    *,
    ok: bool,
    status: SubagentOutcome,
    outcome: SubagentOutcome,
    lingered_unfinished: bool,
    resolved_error: str | None,
) -> str | None:
    """One line saying why the run did not finish (ADR-0276 D10). ``None`` if ok.

    The order follows the reason table: the lingering sentence first (it forces
    ``error`` even over the child's own ``aborted``), then who stopped an
    ``aborted`` run — the CALLER proposing ``aborted`` means ``row.stopped``,
    i.e. ``stop`` / ``stop_all``; the child's own stop reason means it aborted
    itself — then the line of the resolved error that says why
    (:func:`_reason_line`: the first, or a traceback's exception line).
    """

    if ok:
        return None
    if lingered_unfinished:
        return LINGERED_REASON
    if status == "aborted":
        return REASON_STOPPED if outcome == "aborted" else REASON_OWN_ABORT
    if resolved_error:
        line = _reason_line(resolved_error)
        if line:
            return line
    return REASON_UNSPECIFIED


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
    session_recorded: bool = False,
    lingered: bool = False,
    reason: str | None = None,
) -> SubagentResult:
    """Fold a finished (or abandoned) child run into its envelope.

    ``outcome`` is the caller's PROPOSAL — what the process layer observed
    happen to the process. It may be tightened here but never loosened: a run
    the spawner believed succeeded is still reported as an error when the
    stream says so.

    ``reason`` overrides the reason this function would write for a run that
    is not ``ok`` (ADR-0276 D10). One caller passes it: ``runtime._run``
    building the envelope of a cancelled spawn (:data:`REASON_CANCELLED`), the
    one stop this function cannot tell apart from ``stop_all`` by its outcome.
    On an ``ok`` run it is ignored — a success has no reason.

    ``lingered`` is the channel's statement that the child closed both pipes
    and then did not exit within ``print_channel.POST_EOF_EXIT_BOUND_SECONDS``,
    so it was reaped and ``exit_code`` is the REAPER's, not the child's own
    (#604, ADR-0276 D4). It is the one case in which a non-zero exit code does
    not fail the run: a stream that reached its terminator with a stop reason
    other than ``error`` / ``aborted`` is a finished answer, and it stays ``ok``
    with :data:`LINGERED_NOTE`. Anything less is ``error``, its reason
    :data:`LINGERED_REASON`. Read only beside ``outcome="ok"``: a stop that
    landed during the wait is the caller's ``aborted``, and that wins.

    ``session_recorded`` is the channel's statement that the child ran with a
    session file its parent allocated (``SpawnPlan.session_path``, #199). It is
    necessary but not sufficient for :attr:`SubagentResult.output_recorded`:
    the summary must also have come from the child's own stream, because a
    stderr tail or a spawn error is not in that file — a child that never got
    as far as its first turn left only the header and the origin record there.

    NEVER TRUST THE RETURN CODE ALONE (§9.1). Until #410 ``print_mode.py``'s
    ``stop_reason in ("error", "aborted") → exit_code = 1`` mapping was guarded
    by ``if mode == "text"``, so a JSON-mode child whose model errored — a bogus
    model id, an auth failure mid-stream — exited **0** with empty stderr while
    the event stream carried ``stop_reason: "error"``. It exits 1 now
    (ADR-0268), still with empty stderr, but the stream stays the evidence read
    here: a child from an older release still exits 0, a run that ended on a
    dropped terminator says nothing about it in the code, and on Windows a
    ``TerminateJobObject(job, 1)`` hard kill exits 1 exactly as a failed answer
    now does. (A kill on POSIX stays visible: ``SIGTERM`` gives 143, ``SIGHUP``
    129, ``SIGKILL`` -9.)
    """

    # ``agent_end`` is the child's own terminator (``stream.py:276-280``). Its
    # absence in a stream that reached EOF is a child that stopped MID-TURN, and
    # the exit code cannot see it: measured against a real child that emits a
    # good ``message_end`` and then exits 0 without a terminator, this returned
    # ``ok=True status='ok' summary='the complete answer'`` for a run that never
    # finished.
    #
    # GUARDED BY ``dropped_lines == 0``, and the guard is the whole reason the
    # disjunct is safe. ``agent_end`` carries the entire message array on ONE
    # line (``stream.py:821-827``), so a child that read a large file emits a
    # terminator above ``MAX_LINE_BYTES`` and ``LineAssembler`` drops it — on a
    # run that finished perfectly. Measured: ``saw_agent_end=False,
    # dropped_lines=1, exit 0, summary='the complete answer'``. A bare
    # ``or not state.saw_agent_end`` FAILS that delegation. Once a line has been
    # dropped we cannot know whether the terminator was among them, so its
    # absence is not evidence and the run is judged on the exit code and the
    # stream alone.
    no_terminator = not state.saw_agent_end and state.dropped_lines == 0
    # THE LINGERING SPLIT (#604, ADR-0276 D4). Both halves need the TERMINATOR
    # itself, not merely "nothing was missing": a terminator dropped as an
    # oversize line (``dropped_lines``) proves nothing about a child that then
    # failed to exit, so it is judged as unfinished.
    lingering = lingered and outcome == "ok"
    finished_then_lingered = (
        lingering and state.saw_agent_end and state.stop_reason not in ("error", "aborted")
    )
    lingered_unfinished = lingering and not finished_then_lingered
    process_failed = (
        outcome != "ok"
        # The reaper's signal is not the child's verdict on a finished stream.
        or (exit_code is not None and exit_code != 0 and not finished_then_lingered)
        or state.stop_reason in ("error", "aborted")
        or lingered_unfinished
    )
    failed = process_failed or no_terminator
    if outcome != "ok":
        status: SubagentOutcome = outcome
    elif lingered_unfinished:
        # ``error`` even over the child's own ``aborted`` stop reason: what the
        # parent observed is a child that would not exit, and that is the fact
        # :data:`LINGERED_REASON` states.
        status = "error"
    elif state.stop_reason == "aborted":
        status = "aborted"
    elif failed:
        status = "error"
    else:
        status = "ok"
    ok = not failed

    stderr_clean = sanitize_stderr(stderr_tail, outcome=status)
    selected = _select_summary(state, stderr_clean, ok=ok)
    # FROM THE CHILD'S OWN STREAM, i.e. text its harness wrote into its session
    # file: its answer, or its own final error. Compared by value because
    # ``_select_summary`` returns one of these objects or a stderr/sentinel
    # string; a stderr tail that happens to equal the answer is that answer.
    from_stream = bool(selected) and selected in (state.summary, state.final_error)
    recorded = session_recorded and from_stream
    summary, truncated, _omitted = cap_summary(selected, output_cap, recorded=recorded)

    # SAFE BY CONSTRUCTION: ``_select_summary`` never reads this, so nothing
    # resolved here can displace the child's partial answer. Both renderers
    # print ``error`` as a SEPARATE note unless it only repeats the summary
    # (:func:`error_repeats_summary`). See :func:`_resolve_error` for the chain.
    resolved_error = _resolve_error(
        error=error,
        ok=ok,
        status=status,
        state=state,
        stderr_clean=stderr_clean,
        exit_code=exit_code,
        lingered=lingered,
        no_terminator=no_terminator,
    )
    why = (reason or None) if not ok else None
    if why is None:
        why = _reason_for(
            ok=ok,
            status=status,
            outcome=outcome,
            lingered_unfinished=lingered_unfinished,
            # The CALLER's error, when it gave one: a stderr appended after it
            # (:func:`_resolve_error`) is evidence, and a traceback in it must
            # not take the reason over from the error the channel reported.
            resolved_error=error if error and error.strip() else resolved_error,
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
        # What the child ACTUALLY ran on, read off its own ``message_end``.
        #
        # CORRECTED: this comment used to say that a profile with no ``model:``
        # takes the persisted default rather than the parent's run-scope model.
        # That stopped being true when ``resolver.child_model_flags`` began
        # inheriting ``parent_model_flags`` for a profile that declares NEITHER
        # ``model`` nor ``provider`` — such a child is now launched with the
        # parent's ``--model``. The persisted default is reached only when the
        # parent has no resolved model of its own either.
        #
        # The field is still worth having and still reads off the child, because
        # what we ASK for and what answers can differ: a provider substitution, a
        # profile naming a model that resolves elsewhere, or an alias resolved at
        # the far end. This is the only value that survives that.
        model=state.model,
        provider=state.provider,
        output_recorded=recorded,
        note=LINGERED_NOTE if finished_then_lingered else None,
        # ``started``, the session and the files are ``_run``'s to fill: this
        # module is pure and knows neither whether a process existed nor the
        # directories a path is relative to.
        reason=why,
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
    "LINGERED_NOTE",
    "LINGERED_REASON",
    "MAX_ERROR_NOTE_BYTES",
    "MAX_REASON_CHARS",
    "MAX_TRAIL_BYTES",
    "REASON_CANCELLED",
    "REASON_OWN_ABORT",
    "REASON_STOPPED",
    "REASON_UNSPECIFIED",
    "STREAM_ENDED_EARLY",
    "render_tool_trail",
    "DEFAULT_OUTPUT_CAP",
    "NO_OUTPUT",
    "build_result",
    "cap_summary",
    "child_line",
    "child_prose",
    "count_trail_calls",
    "declined_result",
    "describe_exit",
    "error_repeats_summary",
    "error_says_only_the_reason",
    "recap_summary",
    "sanitize_stderr",
    "says_nothing",
    "split_trail",
    "summary_is_the_error",
]
