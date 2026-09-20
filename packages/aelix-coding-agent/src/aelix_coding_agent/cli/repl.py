"""Sprint 5b §B.2 — minimal CLI REPL with ``!``/``!!`` bash parser.

Pi parity surface: enough to exercise ``user_bash`` emit + extension command
interception + ``/reload`` dispatch into
:meth:`AgentHarness.reload_resources`. Full TUI / interactive-mode.ts (5528
LOC) is Phase 5c-tui owned (Sprint 6h₁₀b, see ADR-0100).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aelix_agent_core.harness.hooks import (
    UserBashHookEvent,
    UserBashResult,
)
from aelix_agent_core.session.context import create_custom_message
from aelix_ai.utils._child_output import decode_child_output

from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_line,
    truncate_tail,
)
from aelix_coding_agent.tools.bash import (
    BashOperations,
    create_local_bash_operations,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness
    from aelix_agent_core.session.session import Session


#: The ``customType`` the ``!`` record carries. ADR-0242 rule 1.5 keeps the
#: name (it predates the ``aelix.`` namespace); rule 1.1 is what moved it off
#: ``CustomEntry`` — a record that must reach the model is a
#: ``CustomMessageEntry``.
BASH_EXECUTION_TYPE = "bash_execution"

#: The cap on the MODEL-FACING copy of a ``!`` command's output — pi's own
#: numbers for this path. pi runs ``truncateTail(fullOutput)`` with the shared
#: defaults (``core/bash-executor.ts:113``, ``DEFAULT_MAX_LINES`` /
#: ``DEFAULT_MAX_BYTES`` in ``core/tools/truncate.ts:11-12``), this function is
#: a port of that path, and the two sibling bash surfaces here already cap at
#: the same order (the model-facing tool at these exact numbers,
#: ``tools/bash.py:61``; ad-hoc RPC bash tighter at 256 lines / 32KB,
#: ``rpc/rpc_mode.py:605``). Uncapped, one ``!cat server.log`` is re-sent on
#: every later turn of the session and on every ``--continue``: measured before
#: this cap, ``!python3 -c "print('x'*1000000)"`` wrote a 1,000,047-char
#: ``UserMessage`` and a 2,000,455-byte session file.
_MAX_LINES = DEFAULT_MAX_LINES
_MAX_BYTES = DEFAULT_MAX_BYTES

#: The cap on the COMMAND the record echoes, in characters. The output is not
#: the only unbounded thing a ``!`` line can put in the context: the command
#: rides in the same ``content`` and is re-sent with it on every later turn, so
#: ADR-0242 rule 1.5's amended sentence — what a ``CustomMessageEntry`` records
#: is context, so it is bounded at the writer — covers it too. Measured without
#: this cap, ``!#`` followed by 60,000 characters produced a 60,019-byte model
#: message that nothing touched: the same hole as an uncapped output, through a
#: different door.
#:
#: A DIVERGENCE from pi (ADR-0235), which renders ``Ran `${msg.command}` `` with
#: whatever it was handed (``harness/messages.ts:63-64``). Two orders of
#: magnitude below the output's cap because a ``!`` line is one command typed or
#: pasted into a one-line prompt, not a stream — and in CHARACTERS for the same
#: reason: this bounds how long a LINE is, where the output's cap bounds how
#: much of a byte stream fits.
_MAX_COMMAND_CHARS = 1024

#: Where the bytes the cap dropped actually went. They are not lost and they
#: are not in a spill file: :func:`handle_user_bash` returns the FULL output
#: and both call sites print it, so the cut is between the screen and the
#: record, not between the screen and the user.
_FULL_OUTPUT_IS_ON_SCREEN = (
    "The full output was printed in the terminal; only this tail is recorded."
)


def _recorded_command(command: str) -> str:
    """The command as the RECORD carries it — bounded, like the output.

    ``truncate_line`` (``tools/_truncate.py:137``) keeps the HEAD and marks the
    cut in place, which is the right half here: the head of a command line is
    what identifies it.
    """

    return truncate_line(command, _MAX_COMMAND_CHARS)


def _truncation_notice(
    *,
    truncated_by: str,
    last_line_partial: bool,
    partial_line: int,
    partial_bytes: int,
    original_lines: int,
    kept_lines: int,
) -> str:
    """The bracketed line that makes the cut VISIBLE in the record.

    Same shape as the model-facing bash tool's notice
    (``tools/bash.py:779`` ``_format_truncation_notice``) and of pi's
    ``[Output truncated. Full output: …]``, with the one substitution this
    path forces: there is no ``fullOutputPath`` to cite, so the sentence names
    where the rest went instead. Silence here would be the defect #299 is
    about in miniature — a record that ends mid-stream and does not say so
    reads to the model as a command that printed that much and stopped.

    The counts are the WHOLE output's, not the sub-slice ``truncate_tail`` was
    handed: :func:`_cap_for_the_record` splits the trailing blank lines off
    before it truncates, and a notice that reported the helper's own totals
    would undercount every output that ends in one (the first cut of this
    reported a 60,001-byte output as 60,000). ``partial_line`` is which line
    the partial tail came from, which is NOT ``original_lines`` once there are
    blank lines under it.
    """

    if last_line_partial:
        # A single line longer than the byte budget: ``truncate_tail`` keeps
        # its tail, so there is no line RANGE to report, only how much of
        # which line survived.
        return (
            f"\n\n[Showing the last {format_size(partial_bytes)} of line "
            f"{partial_line} ({format_size(_MAX_BYTES)} limit). "
            f"{_FULL_OUTPUT_IS_ON_SCREEN}]"
        )
    start_line = original_lines - kept_lines + 1
    limit = (
        f"{_MAX_LINES} line limit"
        if truncated_by == "lines"
        else f"{format_size(_MAX_BYTES)} limit"
    )
    return (
        f"\n\n[Showing lines {start_line}-{original_lines} of "
        f"{original_lines} ({limit}). {_FULL_OUTPUT_IS_ON_SCREEN}]"
    )


def _split_trailing_terminators(output: str) -> tuple[str, int]:
    """Split a command's output into its content and its trailing line endings.

    A line terminator ENDS a line; it does not begin another. So the FIRST
    terminator of the trailing run belongs to the last line of ``content``, and
    every one after it is a trailing BLANK line — which is content, and content
    that costs almost nothing to keep. :func:`_cap_for_the_record` needs the two
    apart, and this is where they come apart.

    ``\\r\\n`` counts as ONE terminator, so what a Windows child prints and what
    a POSIX child prints produce the same record.
    """

    content = output.rstrip("\r\n")
    return content, len(output[len(content) :].replace("\r\n", "\n"))


@dataclass(frozen=True)
class _Record:
    """The model-facing copy of a ``!`` command's output."""

    #: Never longer than ``_MAX_BYTES`` bytes.
    body: str
    #: The bracketed line naming what was dropped, or ``""`` when nothing was.
    notice: str
    #: The ``details["truncation"]`` payload, or ``None`` when nothing was cut.
    truncation: dict[str, object] | None


def _cap_for_the_record(output: str) -> _Record:
    """Bound the recorded copy of ``output`` at ``_MAX_LINES``/``_MAX_BYTES``.

    ``truncate_tail`` alone cannot do this, and the ``removesuffix("\\n")`` the
    first cut of #299 reached for is not enough either. The helper splits on
    ``"\\n"`` raw, so a trailing newline leaves an empty final element that IS a
    line: it costs 0 bytes, so it is the one line that fits, and the long line
    above it is dropped whole. Stripping one ``"\\n"`` hides that for the single
    commonest case and for nothing else — CROSS-REVIEWED and MEASURED on the
    first cut, the recorded body for a ``!`` output of:

    * ``"x"*60000 + "\\n"`` — 51,200 ``x``, right;
    * ``"x"*60000 + "\\n\\n"`` — EMPTY, recorded as ``(no output)``;
    * ``"x"*60000 + "\\r\\n"`` — 51,199 ``x`` and a ``\\r``, a byte short of the
      cap, which is what made the windows CI legs red where the POSIX ones
      were green;
    * ``"x"*60000 + "\\r\\n\\r\\n"`` — a lone ``\\r``;
    * ``"x"*51200 + "\\n"`` — 51,201 bytes and NO notice: not a cap at all.

    The first four are one bug: a trailing terminator is not a line, and the
    run of them is not one character. This strips the WHOLE run and counts it,
    so a blank final line and a CRLF stop being special cases. ``print()`` +
    ``print()`` is an ordinary command, and it was the one that lost 60KB of
    output behind the word ``(no output)``.

    The blank lines are then kept, because they are the END of the output and
    the end is the half ``truncate_tail`` keeps. They are paid for out of both
    budgets before the content is — one byte each — so an output that is
    nothing but newlines still records its last ``_MAX_LINES`` of them.

    The fifth is the other half of the same normalisation and the reason the
    caps are decided against the ORIGINAL bytes: strip first and ask afterwards
    whether the stripped text fits, and a record one byte over the cap answers
    yes. ``truncate_tail`` asks the same question the same way
    (``truncate.ts:172-177``: ``totalBytes`` is the whole content's, trailing
    newline included). What is not restored is the terminator itself: it is put
    back only when the output fits whole, because a record already at the cap
    has no room for a line ending that carries nothing.

    The helper's own bug is #309 — it has five callers (bash/read/grep/find/ls)
    and pinned ``tests/pi_parity/`` tests, so it is its own issue and its own
    commit. pi is immune to the first row (``splitLinesForCounting`` pops that
    element, ``truncate.ts:47-56``) and not to the others.
    """

    content, terminators = _split_trailing_terminators(output)
    blanks = max(terminators - 1, 0)
    original_lines = content.count("\n") + 1 + blanks
    original_bytes = len(output.encode())

    kept_blanks = min(blanks, _MAX_LINES - 1)
    capped, info = truncate_tail(
        content,
        max_lines=_MAX_LINES - kept_blanks,
        max_bytes=_MAX_BYTES - kept_blanks,
    )
    if not info.truncated and kept_blanks == blanks:
        # Nothing of the CONTENT was dropped. Either the whole output fits, and
        # the record is it verbatim — terminator, blank lines and all — or the
        # only thing over the cap is that terminator, which is a line ending
        # and not a line, so it goes without a notice: there is nothing to
        # announce, and "[Showing lines 1-1 of 1]" over a complete output would
        # be the same kind of false record this issue is about.
        if original_bytes <= _MAX_BYTES:
            return _Record(output, "", None)
        return _Record(capped + "\n" * kept_blanks, "", None)

    body = capped + "\n" * kept_blanks
    kept_lines = info.kept_lines + kept_blanks
    # ``truncated_by`` is ``None`` exactly when the helper did not cut and the
    # blank tail alone blew the LINE budget.
    truncated_by = info.truncated_by or "lines"
    return _Record(
        body,
        _truncation_notice(
            truncated_by=truncated_by,
            last_line_partial=info.last_line_partial,
            # The partial line is the last line of ``content``, which is the
            # helper's own total; the blank lines below it are not it.
            partial_line=info.original_lines,
            partial_bytes=info.kept_bytes,
            original_lines=original_lines,
            kept_lines=kept_lines,
        ),
        {
            "truncated_by": truncated_by,
            "original_lines": original_lines,
            "kept_lines": kept_lines,
            "original_bytes": original_bytes,
            "kept_bytes": len(body.encode()),
        },
    )


def bash_execution_to_text(
    command: str,
    output: str,
    *,
    exit_code: int | None = None,
    notice: str = "",
) -> str:
    """Render a ``!`` execution the way the model reads it.

    Pi ``bashExecutionToText`` (``harness/messages.ts:63-79``), including the
    order it appends in: output block, then status, then truncation notice.

    ``output`` is the ALREADY-CAPPED body and ``notice`` the line describing
    what that cut dropped — :func:`_cap_for_the_record` produces both, and the
    arithmetic behind them is why they arrive made rather than as a
    ``TruncationInfo`` to re-derive here. ``command`` is capped HERE, by
    :func:`_recorded_command`, so no caller can put an unbounded one in the
    record.

    ``exit_code`` is ``BashOperations.exec``'s (``tools/bash.py:210``, which
    returns an :class:`ExecExitResult`, not just the byte stream) or the one
    an intercepting extension's ``result`` carries. Without it ``!test -f
    missing`` and ``!test -f present`` both reach the model as
    "Ran `…`\\n(no output)" — a record that cannot tell failure from success,
    which is the same class of defect as one that carries no output at all.
    Reported only when non-zero and known, exactly as pi does: ``None`` means
    killed, and pi's ``exitCode ?? undefined`` prints nothing for it either.

    pi's remaining field, ``cancelled``, has no source on this path and is not
    faked: ``handle_user_bash`` passes ``signal=None``, so a ``!`` command
    cannot be aborted and the branch would be unreachable. The model-facing
    bash tool and the RPC handler are the two paths that do supply a signal.
    """

    text = f"Ran `{_recorded_command(command)}`\n"
    if output:
        text += f"```\n{output}\n```"
    else:
        text += "(no output)"
    if exit_code is not None and exit_code != 0:
        text += f"\n\nCommand exited with code {exit_code}"
    return text + notice


async def handle_user_bash(
    harness: AgentHarness,
    command: str,
    *,
    exclude_from_context: bool,
    cwd: str,
) -> str:
    """Emit ``user_bash``; execute via injected ops or local default.

    Pi parity (``interactive-mode.ts:5403-5466``): emit lets extensions
    intercept; ``result``-bearing reducer return short-circuits execution;
    otherwise an injected ``operations`` (or the local default) runs.
    Returns the captured stdout/stderr buffer.

    **The output reaches the model unless ``exclude_from_context``** (#299).
    That takes two writes, because a harness WITH a session and one without
    build a turn from different lists:

    * the session gets a ``CustomMessageEntry`` (ADR-0242 rule 1.1) —
      ``build_session_context`` turns it back into a ``UserMessage``, and that
      is what the next ``prompt()`` sends, this turn and on every later
      resume, because ``AgentHarness._run`` derives the turn's messages from
      ``session.build_context()`` whenever a session is attached
      (``harness/core.py:4333-4336``, pinned by
      ``tests/test_state_messages_derived.py``);
    * ``harness.messages`` gets the same ``UserMessage`` now, which is what
      carries the output on the ``--no-session`` path — there ``_state.messages``
      IS the turn's list, and nothing else would hold the output at all. With a
      session attached it keeps ``_state.messages`` in step with what the
      session will send, which is what the harness's own context-window and
      cost estimates read.

    Until #299 this wrote a ``CustomEntry`` instead, which rule 1.6 has
    readers skip, and nothing was appended live — so ``!cmd`` and ``!!cmd``
    were the same command from the model's side, and this docstring's promise
    was false in both tiers.

    **The recorded copy is capped** — the output at
    ``_MAX_LINES``/``_MAX_BYTES`` (:func:`_cap_for_the_record`) and the command
    at ``_MAX_COMMAND_CHARS`` (:func:`_recorded_command`). The returned output,
    which both call sites print, is not. The user asked to run a command and
    gets all of it on screen; the model and the session file carry a bounded
    record, because they carry it again on every later turn.

    ``!!cmd`` writes neither, which is where this diverges from pi
    (ADR-0235): pi records a ``bashExecution`` message with
    ``excludeFromContext: true`` so its transcript can redraw it, and drops it
    in ``convertToLlm``. Aelix has one entry type for both tiers, so "excluded"
    is "not recorded" — the same thing the model sees, one less thing the
    transcript does.

    The in-memory append is a plain ``list.append`` on the harness's live
    message list, NOT :meth:`AgentHarness.append_message`: during a turn that
    method queues a ``PendingMessageWrite`` whose flush writes a SECOND
    session entry, and the record written here would be duplicated on replay.
    Both call sites (``run_repl`` and the TUI input loop) read a line only
    between turns, so pi's ``_pendingBashMessages`` deferral has no reachable
    trigger here; a future caller that runs this mid-stream would need it, for
    the ordering reason pi documents.
    """

    event_result = await harness.hooks.emit(
        UserBashHookEvent(
            command=command,
            exclude_from_context=exclude_from_context,
            cwd=cwd,
        )
    )
    operations: BashOperations | None = None
    fully_handled = False
    output = ""
    # pi's ``BashResult`` carries ``exitCode`` alongside ``output``
    # (``core/bash-executor.ts:29-40``); the Aelix ``BashResult`` Protocol is a
    # stub, so an intercepting extension's is read the same duck-typed way
    # ``output`` already is, and stays ``None`` when it supplies neither.
    exit_code: int | None = None
    if isinstance(event_result, UserBashResult):
        operations = event_result.operations  # type: ignore[assignment]
        if event_result.result is not None:
            fully_handled = True
            output = getattr(event_result.result, "output", "") or ""
            exit_code = getattr(event_result.result, "exit_code", None)
    if not fully_handled:
        ops: BashOperations = operations or create_local_bash_operations()
        chunks: list[bytes] = []
        exit_result = await ops.exec(command, cwd, on_data=chunks.append, signal=None)
        exit_code = exit_result.exit_code
        # ``ragged_tail=True`` (#239 cross-review): no signal and no timeout
        # here, so the only cut is #232's success-path exit drain —
        # ``_drain_after_the_exit`` ends the read at a READ boundary while a
        # backgrounded helper may still be writing. Rarer than the abort path,
        # same repair. The head is append-only and so is not claimed.
        output = decode_child_output(b"".join(chunks), ragged_tail=True)
    if exclude_from_context:
        return output
    # The OUTPUT's cap is applied here and not inside
    # ``bash_execution_to_text`` so the metadata survives into ``details``
    # (the command's is applied there, where nothing can route around it), and
    # to the RECORDED copy only — ``output`` is returned whole below. What it
    # has to get right, and what the first cut of #299 did not, is in
    # :func:`_cap_for_the_record`.
    record = _cap_for_the_record(output)
    text = bash_execution_to_text(
        command, record.body, exit_code=exit_code, notice=record.notice
    )
    # NOT the output: ``content`` above already carries it, and nothing reads
    # this key back — ``bash_execution`` has no registered renderer, so
    # ``tui/render.py:1481-1487`` draws the ``[bash_execution]`` label and
    # ``content``. Storing it twice cost a 2,000,455-byte session file for
    # 1,000,001 bytes of output before #299's fix pass. What stays is what a
    # future renderer could not recover from ``content``: the command, the
    # exit code, and the size of what the cap dropped. The command is stored
    # capped for the same reason it is rendered capped — this dict is part of
    # the entry, and the entry is bounded.
    details: dict[str, object] = {
        "command": _recorded_command(command),
        "exit_code": exit_code,
    }
    if record.truncation is not None:
        details["truncation"] = record.truncation
    # Sprint 6h₅d §E (P-384 / MINOR-3): read through
    # :attr:`AgentHarness.session` and narrow once locally.
    session = harness.session
    timestamp = _iso_now()
    if session is not None:
        # Suppressed as it always was: an unwritable session must not take the
        # REPL down over a ``!`` line. What that costs, exactly: with a session
        # attached the turn's messages come from ``build_context()``, so a
        # failed write costs THIS turn as well as the next resume — the live
        # append below keeps the output in ``_state.messages``, which
        # ``_run`` then does not read (``harness/core.py:4333-4336``). Measured
        # with ``append_custom_message_entry`` raising:
        # ``in _state.messages=True reached provider=False``.
        with contextlib.suppress(Exception):
            timestamp = await _append_bash_entry(session, text, details)
    # The live tier, built by the SAME helper ``build_session_context`` uses on
    # the entry above, so what a ``--no-session`` run sends and what a
    # ``--continue`` sends are the same text rather than two renderings free to
    # drift apart. NOT suppressed: nothing here can fail on a real harness, and
    # swallowing it would silently restore the defect #299 is about for every
    # session-less run.
    harness.messages.append(
        create_custom_message(BASH_EXECUTION_TYPE, text, True, details, timestamp)
    )
    return output


def _iso_now() -> str:
    """The session store's timestamp format (``session.py:_iso_now``)."""

    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


async def _append_bash_entry(
    session: Session, text: str, details: dict[str, object]
) -> str:
    """Write the record and hand back ITS timestamp.

    Reading the timestamp back off the stored entry (rather than minting a
    second one here) keeps the live message and the replayed one identical:
    ``create_custom_message`` puts it on the ``UserMessage``, so two clocks
    would make the same turn render one time live and another after a resume.
    Falls back to a fresh stamp when the entry cannot be read back.
    """

    entry_id = await session.append_custom_message_entry(
        custom_type=BASH_EXECUTION_TYPE,
        content=text,
        display=True,
        details=details,
    )
    entry = await session.get_entry(entry_id)
    return getattr(entry, "timestamp", None) or _iso_now()


async def run_repl(harness: AgentHarness, *, cwd: str) -> None:
    """Minimal stdin → AgentHarness REPL.

    Recognised tokens:

    - ``!<cmd>`` — emit ``user_bash``; the output is printed in full, and a
      capped copy of it is recorded and reaches the model, this turn and on
      every later resume (#299)
    - ``!!<cmd>`` — emit ``user_bash``, print the output, record nothing
      (transient: the model never sees it, live or on replay)
    - ``/reload`` — call :meth:`AgentHarness.reload_resources`
    - ``/quit`` / ``/exit`` — exit the REPL
    - anything else — :meth:`AgentHarness.prompt` (triggers ``input`` emit)
    """

    await harness.bootstrap()
    while True:
        try:
            line = await asyncio.to_thread(input, "» ")
        except EOFError:
            return
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in ("/quit", "/exit"):
            return
        if stripped == "/reload":
            await harness.reload_resources()
            continue
        if line.startswith("!!"):
            cmd = line[2:].strip()
            if cmd:
                out = await handle_user_bash(
                    harness, cmd, exclude_from_context=True, cwd=cwd
                )
                if out:
                    print(out, end="" if out.endswith("\n") else "\n")
            continue
        if line.startswith("!"):
            cmd = line[1:].strip()
            if cmd:
                out = await handle_user_bash(
                    harness, cmd, exclude_from_context=False, cwd=cwd
                )
                if out:
                    print(out, end="" if out.endswith("\n") else "\n")
            continue
        await harness.prompt(line)


__all__ = ["handle_user_bash", "run_repl"]
