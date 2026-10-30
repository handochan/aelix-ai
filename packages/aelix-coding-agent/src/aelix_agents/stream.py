"""Child stdout → :class:`_StreamState` — the PURE half of the read path.

ADR-0197 §(k) / ADR-0198. Two independent pieces, both free of ``asyncio``,
``os`` and ``subprocess`` so the byte-level and the field-level behaviour can be
pinned without ever creating a process:

* :class:`LineAssembler` — bytes → complete lines, with a per-line budget.
* :func:`reduce_line` — one JSON line → an accumulated :class:`_StreamState`.

WHY THE ASSEMBLER EXISTS AT ALL (P2 review finding B3). The obvious transport,
``await proc.stdout.readline()``, is unusable. ``create_subprocess_exec``'s
``StreamReader`` defaults to ``limit=65536`` and ``readline()`` RAISES past that
ceiling instead of truncating — and, worse, the oversize bytes stay in the
buffer, so EVERY subsequent ``readline()`` raises too and the terminating
``agent_end`` is lost. Measured against a real child emitting a 200 000-byte
line followed by a valid one::

    ValueError: Separator is not found, and chunk exceed the limit
    ValueError: Separator is found, but chunk is longer than limit

That size is routine, not exotic: a ``message_end`` carrying a single ``read``
of one large source file serialises to ~207 KB, and ``message_end`` is this
module's SOLE source of the summary and the usage numbers. So the pump reads
fixed-size chunks (``reader.read(65536)``) and this class reassembles them —
which is also the only shape in which "drop the oversize line and resync" is
implementable at all.

WHY THE FIELD MAP IS SNAKE_CASE (ADR-0198). ``--mode json`` emits raw kernel
:class:`~aelix_agent_core.types.AgentEvent` dataclasses through
``dataclasses.asdict`` (``modes/print_mode.py:102-111`` → ``rpc/rpc_mode.py``),
so the wire names are the Python attribute names: ``stop_reason``,
``error_message``, ``tool_name``, ``total_tokens``. A line-for-line port of
pi's camelCase parser reads ``None`` for every field, and every failure then
looks like a success with zero usage. ``test_snake_case_fields_read`` is the
regression pin for that trap.

The ONE exception is ``AssistantMessage.usage`` (``aelix_ai/messages.py:127``),
typed ``dict[str, Any]`` and passed through ``asdict`` untransformed: its keys
are whatever the adapter wrote, so it is explicitly OUT of the wire-shape
guarantee and every read here dual-spells, exactly as the kernel itself does at
``session/compaction.py:1038-1045``.
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.utils._child_output import decode_child_output
from aelix_coding_agent.builtin.guardrail import _WRITE_TOOLS
from aelix_coding_agent.subagent_contract import SubagentToolActivity, SubagentToolStatus

# Per-line budget. A line longer than this is DISCARDED and counted, never
# raised on and never buffered — an unbounded line is the one shape in which a
# malfunctioning (or hostile) child can exhaust parent memory. 4 MiB is ~20x the
# largest routine ``message_end`` measured (a ~207 KB whole-file read), so it
# only fires on genuine pathology. The pump's ``StreamReader`` is opened with a
# larger ``limit=`` (8 MiB, ``print_channel.py``) so the reader itself never
# raises before this budget can act.
MAX_LINE_BYTES = 4 * 1024 * 1024

# Event types this reducer consumes. Everything else — ``message_start``,
# ``message_update``, ``turn_end``, the auto-retry pair, the compaction pair —
# is ignored on purpose. ``message_update`` in particular MUST be ignored: its
# ``partial`` repeats the whole assistant message on every delta (measured
# stream-to-result ratios of 329x and 2773x), while the outer
# ``message.content`` stays ``[]`` for the duration, so consuming it costs
# O(n^2) and yields nothing.
#
# ``tool_execution_end`` USED to be on that ignored list and no longer is. It
# carries the only per-tool success/failure signal that ever reaches the parent:
# ``build_result`` derives status from the child's exit code and the stream
# terminator, so before the trail a child whose every tool errored still came
# back ``ok``.
_AGENT_START = "agent_start"
_TURN_START = "turn_start"
_MESSAGE_END = "message_end"
_TOOL_EXECUTION_START = "tool_execution_start"
_TOOL_EXECUTION_END = "tool_execution_end"
_AGENT_END = "agent_end"


class LineAssembler:
    """Reassembles newline-delimited text from arbitrary byte chunks.

    Pure and synchronous: it owns no reader, no loop and no process. The pump
    (``print_channel.py``) supplies chunks and consumes lines::

        for line in assembler.feed(chunk):
            reduce_line(state, line)
        ...
        for line in assembler.flush():   # trailing partial line at EOF
            reduce_line(state, line)

    Decoding is deferred to the line boundary and goes through
    :func:`~aelix_ai.utils._child_output.decode_child_output`, so a multi-byte
    code point split across a chunk boundary survives intact and a genuinely
    malformed byte can never raise into the pump. :meth:`feed` claims NO cut
    end: a line it emits is bounded by the newline the child wrote, not by a
    byte-exact trim (an over-budget line is dropped whole, never cut).
    :meth:`flush` is the carve-out and claims a cut TAIL — its line is the one
    with no terminator, i.e. exactly the bytes a child that died mid-write got
    out. (#239 cross-review: this paragraph used to state the ``feed`` rule for
    both methods, which was false of ``flush``.)
    """

    __slots__ = ("_buf", "_dropped", "_max", "_skipping")

    def __init__(self, *, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self._max = max_line_bytes
        self._buf = bytearray()
        # True while the tail of an already-dropped oversize line is still
        # arriving: everything up to the NEXT newline belongs to that line and
        # must be discarded rather than emitted as a spurious short line.
        self._skipping = False
        self._dropped = 0

    @property
    def dropped_lines(self) -> int:
        """Lines discarded for exceeding :data:`MAX_LINE_BYTES`.

        Rides back to the user on ``SubagentResult.dropped_lines`` so a
        truncated stream is visible rather than silent. The counter is on the
        assembler, not on :class:`_StreamState`, because the drop happens at the
        byte layer — before there is anything a reducer could look at.
        """

        return self._dropped

    def feed(self, chunk: bytes) -> list[str]:
        """Absorb ``chunk`` and return every line it completed.

        Returns a ``list`` rather than a generator on purpose: the mutation of
        the internal buffer and of :attr:`dropped_lines` is a side effect of
        CALLING ``feed``, and a lazily-consumed generator would make those side
        effects depend on whether the caller finished iterating. A partially
        consumed generator would silently lose child output.
        """

        lines: list[str] = []
        if not chunk:
            return lines
        self._buf.extend(chunk)
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            if self._skipping:
                # This was the tail of a line already counted as dropped.
                self._skipping = False
                continue
            if len(raw) > self._max:
                # The whole line arrived inside one chunk and is over budget.
                self._dropped += 1
                continue
            lines.append(decode_child_output(raw))
        if len(self._buf) > self._max:
            # An in-progress line blew the budget before its newline arrived.
            # Drop it NOW (do not keep growing the buffer) and resync at the
            # next newline. ``_skipping`` guards against double-counting the
            # same line across several oversize chunks.
            if not self._skipping:
                self._dropped += 1
                self._skipping = True
            self._buf.clear()
        elif self._skipping:
            # Still resyncing and no newline in sight — the remainder belongs
            # to the dropped line.
            self._buf.clear()
        return lines

    def flush(self) -> list[str]:
        """Emit the trailing partial line at EOF, if any.

        A child that dies mid-line (SIGKILL, a crash) leaves bytes with no
        terminator. They are still worth parsing — the last thing a dying child
        wrote is often the most informative line in the stream.
        """

        if self._skipping:
            self._buf.clear()
            self._skipping = False
            return []
        if not self._buf:
            return []
        raw = bytes(self._buf)
        self._buf.clear()
        if len(raw) > self._max:
            self._dropped += 1
            return []
        # ``ragged_tail=True`` (#239 cross-review): unlike every line ``feed``
        # emits, this one has no terminator — the child died in the middle of
        # writing it, so its last character can be half a character. That is a
        # byte-exact cut and the console code page must not be allowed to spell
        # it.
        return [decode_child_output(raw, ragged_tail=True)]


@dataclass
class _StreamState:
    """Everything the parent learns from the child's stdout.

    Mutable by design — :func:`reduce_line` folds one line at a time into a
    single instance that the pump also hands to the progress callback after
    every line, so the TUI statusline and the tool card stream live.

    Deliberately NOT the contract dataclass: :class:`SubagentUsage` and
    :class:`SubagentResult` are frozen and are the parent's OUTPUT shape.
    :mod:`aelix_agents.envelope` performs the one-way conversion once, at the
    end, where the cap and the fallback chain also live.
    """

    summary: str = ""
    """Text of the LAST text-bearing assistant message. Overwritten, not
    appended: a later assistant turn supersedes an earlier one."""

    last_text: str | None = None
    """:attr:`summary` on one line, bounded — what the delegation panel shows
    as the child's last reply (#132, ADR-0278 D3). Computed ONCE, where
    ``summary`` is set (:func:`_reduce_message_end`), rather than on every
    publish: the runtime publishes after every reduced stdout line and
    ``summary`` can be megabytes. ``None`` until the first text-bearing
    assistant message."""

    stop_reason: str | None = None
    error_message: str | None = None

    final_error: str | None = None
    """The error of the LAST assistant message — and only when that message
    ended with ``stop_reason: "error"`` (#604, ADR-0276 D17).

    :attr:`error_message` is last-NON-EMPTY-wins, so it means "some turn
    errored", never "the run ended in an error": a 429 the harness's auto-retry
    recovered from stays in it for the rest of the run. Reported as the reason a
    child that was later stopped or killed did not finish, that recovered rate
    limit would be the wrong diagnosis. This field is reset on EVERY assistant
    ``message_end``, so it names an error only while the latest message is that
    error. The stale value stays readable in :attr:`error_message` and in the
    envelope's ``details``."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0

    cost_reported: bool = False
    """Did any ``message_end`` carry a ``cost`` of its own? (#199, A.3b)

    The first of the three pieces of evidence behind a recorded ``cost_known``.
    A KEY, not a non-zero value: a provider that wrote ``cost: {"total": 0}``
    for a free turn has answered, exactly as the kernel's own stats read a
    persisted ``cost`` key (``_session_stats._message_cost``). No first-party
    adapter writes one today (they leave pricing to a higher layer), so this is
    ``False`` for almost every real child and the fallback below is what prices
    it."""

    cost_priced: bool = False
    """Did :func:`~aelix_agents.print_channel.apply_cost_fallback` price this
    run from the model registry? The second piece of evidence (#199, A.3b).
    Set by the fallback itself, because its return value is consumed inside the
    channel's envelope builder and ``_run`` only ever sees this object."""

    tokens: int = 0
    """Context LEVEL, not a flow — the last ``total_tokens`` WINS rather than
    summing, because each message reports the whole context, not its own
    increment. Summing would report a number several times the real one."""

    turns: int = 0
    """Assistant messages seen, i.e. completed model turns."""

    current_tool: str | None = None
    provider: str | None = None
    model: str | None = None
    """Last-seen provenance. Recorded (not used) here so the non-pure layer can
    compute a cost fallback via ``aelix_ai.models.calculate_cost`` when the
    adapter emitted no ``cost`` key — openrouter and openai-completions never
    do, so that fallback is the common path, and it needs a model-registry
    lookup, which is disk I/O and therefore illegal in this module."""

    saw_agent_start: bool = False
    saw_agent_end: bool = False
    """``agent_end`` is the child's own terminator. Its absence in a stream
    that reached EOF is how a mid-flight death is distinguished from a clean
    finish that merely produced no text."""

    run_open: bool = False
    """Is the child's LATEST run still open — ``agent_start`` seen, its
    ``agent_end`` not yet? Last event wins, unlike the latched
    :attr:`saw_agent_end`: an auto-retry is a second run (``agent_start`` →
    ``agent_end`` → ``agent_start``), and a stream cut during it must still
    read as unfinished. Consumed by ``child_session.usage_is_complete``; the
    two latched flags keep their existing meanings for the channels."""

    turn_starts: int = 0
    """``turn_start`` events seen. Each :class:`ToolCall` records the value at
    the moment it opened, so a call with no end event can be told apart: in
    the current turn it may still be running, in an earlier one its end will
    never come (#132, ADR-0278 D3, :func:`activity_of`)."""

    recent: deque[ToolCall] = field(default_factory=deque)
    """The child's latest calls, oldest first — the source of
    ``SubagentProgress.activity`` (#132, ADR-0278 D3).

    APART FROM :attr:`tool_trail`, because the trail stops recording at
    :data:`MAX_TRAIL_ENTRIES` and a "recent" read off it would freeze at call
    200. EVERY ``tool_execution_start`` appends here. While the trail has room
    the two share the same :class:`ToolCall` object, and
    :func:`_close_trail_entry` closes the match in both, so the outcome is
    written once and read in two places.

    The latest :data:`ACTIVITY_MAX` calls that are no longer open, by start
    order — PLUS every call still open in the current turn (:func:`_trim_recent`).
    It was a ``deque(maxlen=4)``, and a call that was
    still executing fell out of it as soon as four later calls had started:
    MEASURED (#132 review round 1) with a real ``AgentHarness`` running a slow
    ``grep`` and four ``read`` calls in parallel, the reads finished, the
    window held only them, and the panel said ``thinking`` while the child was
    executing. An open call now leaves the window only when it closes, its turn
    ends or its run ends — and the window is still bounded:
    :data:`_RECENT_MAX` entries in all, which is room for every call a model
    asks for at once in practice (#132 review round 2: at 32 the 33rd parallel
    call was turned away and never appeared, and the panel said ``thinking``
    while it ran)."""

    dropped_lines: int = 0
    """Folded in from :attr:`LineAssembler.dropped_lines` by the pump, so the
    envelope builder has one place to read."""

    trail_overflow: int = 0
    """Calls the child made beyond :data:`MAX_TRAIL_ENTRIES`.

    Counted rather than dropped in silence, so the rendered trail can end with
    an honest "… N more" instead of looking like the whole story."""

    tool_trail: list[ToolCall] = field(default_factory=list)
    """What the child DID, in order — the evidence behind :attr:`summary`.

    ``summary`` is the child's CONCLUSION: the last text-bearing assistant
    message and nothing else. Every tool the child ran, every argument it ran it
    with and every failure it hit were on the wire and discarded here, because
    the reducer read ``tool_execution_start.tool_name`` into
    :attr:`current_tool` — a last-write-wins field that the very next
    ``turn_start`` clears — and ignored ``tool_execution_end`` entirely.

    That mattered most in chain mode, where step k+1 receives step k's
    conclusion with no way to know what ground was already covered, and where a
    child whose every tool FAILED still reports ``ok`` (``build_result``
    derives status from the exit code and the stream terminator, neither of
    which sees a tool error).

    BOUNDED BY COUNT, not just by bytes. :data:`MAX_LINE_BYTES` bounds one line;
    it says nothing about how many of them arrive. An unbounded accumulator here
    would reintroduce the parent-memory-exhaustion shape that budget exists to
    close, on a child that simply runs a lot of tools."""

    file_writes: dict[str, FileWrite] = field(default_factory=dict)
    """Every path a WRITER tool was called on, keyed by the RAW path (#604, D8).

    Collected APART FROM :attr:`tool_trail`, because the trail cannot give a
    path back: its arguments are clipped to :data:`MAX_TRAIL_ARG_CHARS`, it keeps
    only the first :data:`MAX_TRAIL_ENTRIES` calls, and its rendering is cut at a
    byte budget. Insertion-ordered, bounded at :data:`MAX_FILE_PATHS` distinct
    paths; past that :attr:`files_overflow` counts.

    THE KEY IS THE PATH AS THE CHILD WROTE IT, NOT AS IT IS SHOWN (#604 review).
    Keyed by the sanitised spelling, a confirmed write to ``a\\nb.txt`` and an
    unfinished one to ``a b.txt`` — two files — became one entry, listed as
    changed: the unfinished write vanished and the file named did not exist.
    Sanitising is for display only (:func:`path_for_display`); resolving a path
    against the child's directory is ``runtime._run``'s job, because it is the
    one place that knows the directory."""

    write_calls: dict[str, str] = field(default_factory=dict)
    """``tool_call_id`` → path, for a writer call that has not reported its end.

    An end event pops its entry, so what stays is what is in flight — or what
    a child stopped mid-write left behind. Bounded by :data:`MAX_OPEN_WRITE_CALLS`
    against a stream that starts calls and never ends them."""

    files_overflow: int = 0
    """Distinct written paths past :data:`MAX_FILE_PATHS`, counted rather than
    kept so the parent can say "+N more" (see :attr:`file_writes`)."""

    overflow_paths: set[int] = field(default_factory=set, repr=False)
    """Hashes of the paths already counted in :attr:`files_overflow`, so a path
    written twice past the cap counts once. Bounded at
    :data:`MAX_OVERFLOW_TRACKED`; beyond it every overflowing call counts, which
    can only overstate the "+N more" — never hide a file."""


MAX_FILE_PATHS = 64
"""How many DISTINCT written paths one child's result may list (ADR-0276 D8).

A count bound for the same reason :data:`MAX_TRAIL_ENTRIES` is one: a child that
writes ten thousand files must not grow the parent's memory. Past it, paths are
counted (:attr:`_StreamState.files_overflow`), never silently dropped."""

MAX_FILE_PATH_CHARS = 512
"""How much of one written path is SHOWN (:func:`path_for_display`). A longer
string is clipped with ``…`` so it cannot pass for a whole one."""

MAX_FILE_PATH_KEY_CHARS = 4096
"""How much of one written path is KEPT, as the key that tells files apart.

A memory bound, not a display one: a path arrives on a line of up to
:data:`MAX_LINE_BYTES`, and :data:`MAX_FILE_PATHS` of those plus the open calls
would otherwise pin megabytes of a hostile child's strings. 4096 is Linux's
``PATH_MAX`` (macOS's is 1024), so on POSIX no file a writer tool could open
differs from another only past it."""

MAX_OPEN_WRITE_CALLS = 256
"""How many writer calls may be awaiting their end event at once. A real child
runs a handful in parallel; this bounds a stream that only ever starts them."""

MAX_OVERFLOW_TRACKED = 4096
"""How many overflowing paths are remembered to count each one ONCE."""

WRITER_TOOLS = _WRITE_TOOLS
"""The tools whose calls name a file they write: ``edit`` and ``write``, plus the
``create_file`` / ``write_file`` spellings the guardrail already polices.

IMPORTED, not re-spelled (ADR-0276 D8): ``builtin/guardrail.py`` is the one list
of "tools that write a path" product-core keeps, and a second copy here would
drift the day an extension spelling is added there. ``bash`` is not on it and
cannot be — what a shell command writes is not in its arguments."""

_FILE_ARG_KEYS = ("path", "file_path")
"""The argument names a writer tool takes its path under — ``path`` for the
built-ins, ``file_path`` for the edit family (the same pair
``guardrail._path_from_event`` reads)."""


@dataclass
class FileWrite:
    """One path a child's writer tools touched, and what became of it.

    ``changed`` latches: one call that ended without an error is enough, and a
    later failed call on the same path does not undo a write that happened.
    ``open_calls`` counts calls on the path that started and have not reported
    an end — non-zero at the end of the run means a write may have been cut off
    mid-way.
    """

    path: str
    changed: bool = False
    open_calls: int = 0


MAX_TRAIL_ENTRIES = 200
"""How many tool calls one child's trail may record.

A COUNT bound, deliberately, and separate from every byte bound on this path:
:data:`MAX_LINE_BYTES` bounds one line and says nothing about how many arrive,
so a child that runs ten thousand cheap tools would otherwise grow the parent's
memory without ever tripping a size check. Overflow is COUNTED and reported, not
silently discarded — see :attr:`_StreamState.tool_trail`."""

MAX_TRAIL_ARG_CHARS = 120
"""How much of one call's arguments survives into the trail.

Long enough for a path or an ordinary command, short enough that a trail of
:data:`MAX_TRAIL_ENTRIES` cannot dominate the byte budget it has to share with
the summary."""

_ARG_PREFERENCE = ("path", "file_path", "command", "pattern", "query", "url", "cmd")
"""Argument names that ARE the call, when present.

Deliberately a preference list rather than a per-tool table: a table would have
to name every built-in and would go stale the moment an extension ships a tool.
These are the spellings the built-ins already use for their primary argument, so
``read`` renders as its path and ``bash`` as its command, while an unknown tool
falls back to compact ``k=v`` pairs rather than to nothing."""

_CONTROL_CHARS = frozenset(
    chr(c) for c in [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
)
"""C0, DEL and C1. The same set ``consent.contains_control_chars`` refuses.

Stripped rather than refused here, because a trail is diagnostic rather than
structural: a control character in a tool argument must not be able to fail a
delegation that otherwise worked."""


@dataclass
class ToolCall:
    """One tool invocation, as the parent saw it happen.

    Mutable on purpose: ``tool_execution_start`` carries the name and the
    arguments, ``tool_execution_end`` carries the outcome, and the two are
    correlated by ``tool_call_id`` — so the entry is created by the first and
    completed by the second. An entry whose ``failed`` is still ``None`` is a
    call that never reported an end, which is exactly what a child killed
    mid-tool leaves behind and is worth showing as such.
    """

    name: str
    args: str = ""
    failed: bool | None = None
    call_id: str = ""
    turn: int = 0
    """:attr:`_StreamState.turn_starts` when the call opened (#132, D3)."""


ACTIVITY_MAX = 4
"""How many of a child's latest calls ``SubagentProgress.activity`` carries
(#132, ADR-0278 D3). Four because that is what the panel draws at most: a
two-row member shows up to three outcome marks plus the call open now, and a
single delegation lists its last three calls."""

_RECENT_MAX = 256
"""Hard bound on :attr:`_StreamState.recent`: :data:`ACTIVITY_MAX` finished
calls plus the calls still open, which can outnumber them — a model may ask for
any number of parallel calls in one message, and every one is open until its
end event. With the window this full a newer start is not added to it (it
still reaches the trail while the trail has room): the older open
calls keep their places, because "a call that is still running stays on the
panel" is the rule the window exists to keep, and a child's stdout must not
buy unbounded memory here.

256, NOT 32 (#132 review round 2). A call turned away is never added later,
so once the open calls the window holds have ended, one it turned away runs
unseen: MEASURED with 33 parallel calls whose last one was slow, the window
ended as the four reads before it and the panel said ``thinking`` while it
ran. No bounded record can avoid that for every stream — naming an open call
needs its id, and a child can open more calls than any bound and end the ones
kept first — so the bound is set where a model's parallel batch does not reach
it: 252 calls open at once. An entry is a name and an argument of at most
:data:`MAX_TRAIL_ARG_CHARS` each and its call id, and the first
:data:`MAX_TRAIL_ENTRIES` are the trail's own objects."""

LAST_TEXT_MAX_CHARS = 512
"""Bound on :attr:`_StreamState.last_text` — far wider than any panel row, so
the cut a reader sees is always the renderer's, at the terminal's width."""

_LAST_TEXT_WORK_CHARS = LAST_TEXT_MAX_CHARS * 8
"""How much of a reply the one-line collapse looks at. A work bound, like
``panel._MAX_INPUT_CHARS``: a message_end carrying a whole file must not buy a
full-length ``split`` to produce 512 characters."""


def _one_line(text: str) -> str:
    """``text`` with every whitespace run as one space, bounded at
    :data:`LAST_TEXT_MAX_CHARS` with ``…``. Stripped BEFORE the work slice, so
    leading blank lines cannot consume the window (the ``panel._flatten``
    lesson).

    A CUT BY THE WORK BOUND IS MARKED TOO. The slice runs before the collapse,
    so a long run of whitespace can push the rest of the reply past the window:
    MEASURED (#132 review round 1), ``"A" + " " * 5000 + "B"`` collapses to
    ``"A B"`` but came back as ``"A"`` — text lost with nothing to say so. The
    slice still bounds the work; when it dropped anything the line ends in
    ``…``, as a cut by the width bound does.
    """

    stripped = text.strip()
    sliced = len(stripped) > _LAST_TEXT_WORK_CHARS
    flat = " ".join(stripped[:_LAST_TEXT_WORK_CHARS].split())
    if sliced or len(flat) > LAST_TEXT_MAX_CHARS:
        return flat[: LAST_TEXT_MAX_CHARS - 1].rstrip() + "…"
    return flat


def _open_now(state: _StreamState, call: ToolCall) -> bool:
    """Could ``call`` still be executing, as far as the stream can tell? No end
    event yet, opened in the current turn, the run still open. The one fact
    the stream lacks — is the child process live — is :func:`activity_of`'s."""

    return call.failed is None and state.run_open and call.turn == state.turn_starts


def _trim_recent(state: _StreamState) -> None:
    """Keep :attr:`_StreamState.recent` to every call still open plus the
    latest :data:`ACTIVITY_MAX` of the others, by start order (#132 review
    round 1): the oldest call that is finished — or open with an end that will
    never come — goes first, and a call still open keeps its place however many
    calls started after it. Counting the finished ones, rather than the whole
    window, is what keeps the LATEST of them: trimming the window to four as
    each one ended dropped whichever read finished first while the slow call
    held its place, and a later call vanished while earlier ones stayed."""

    recent = state.recent
    done = [call for call in recent if not _open_now(state, call)]
    if len(done) <= ACTIVITY_MAX:
        return
    # By identity: two calls can be equal field for field (``ToolCall`` is a
    # dataclass), and ``deque.remove`` would take the first equal one.
    drop = {id(call) for call in done[: len(done) - ACTIVITY_MAX]}
    kept = [call for call in recent if id(call) not in drop]
    recent.clear()
    recent.extend(kept)


def activity_of(state: _StreamState, *, live: bool) -> tuple[SubagentToolActivity, ...]:
    """Project :attr:`_StreamState.recent` onto the contract (#132, ADR-0278 D3).

    PURE and total. ``live`` is the one fact the stream cannot know — whether
    the CHILD is still running (``RunningChild.state`` not terminal) — so the
    sole producer, ``runtime._publish``, passes it. An open call (no end event
    yet) is ``running`` only while all three hold: it opened in the child's
    current turn, the run is open, and the child is live. Otherwise its end
    event will never come — a later turn began, the run ended, the child was
    stopped — and it is ``no_result``, which is what a stopped child leaves.
    """

    calls: list[tuple[ToolCall, SubagentToolStatus]] = []
    for call in state.recent:
        status: SubagentToolStatus
        if call.failed is True:
            status = "failed"
        elif call.failed is False:
            status = "ok"
        elif live and _open_now(state, call):
            status = "running"
        else:
            status = "no_result"
        calls.append((call, status))
    if len(calls) > ACTIVITY_MAX:
        # AT MOST ACTIVITY_MAX, IN START ORDER, AND EVERY RUNNING CALL FIRST.
        # ``recent`` holds more than four while any of its calls is open
        # (:func:`_trim_recent`); a call that is running must not be the one
        # left out, or the panel says ``thinking`` while the child executes
        # it. The rest of the room goes to the latest of the others.
        running = [k for k, (_, status) in enumerate(calls) if status == "running"]
        if len(running) >= ACTIVITY_MAX:
            keep = set(running[:ACTIVITY_MAX])
        else:
            others = [k for k, (_, status) in enumerate(calls) if status != "running"]
            spare = ACTIVITY_MAX - len(running)
            keep = set(running) | set(others[len(others) - spare :])
        calls = [item for k, item in enumerate(calls) if k in keep]
    return tuple(
        SubagentToolActivity(name=call.name, args=call.args, status=status)
        for call, status in calls
    )


def _strip_control(text: str) -> str:
    """Remove control characters AND collapse the string onto one line.

    BOTH halves are load-bearing, and the second one is the security half.
    A trail is rendered one call per line, so a newline surviving inside an
    argument would let a child that read attacker-controlled content forge
    additional trail entries — inventing tool calls the parent never observed,
    inside a block the fence labels as a faithful record. Collapsing to a single
    line makes the line count structural rather than content-controlled.

    The control-character half is the same hazard ``consent._sanitize_field``
    exists for: these bytes are interpolated into a prompt and, on the
    ``/agents run`` path, rendered to a terminal.
    """

    return "".join(" " if ch in _CONTROL_CHARS else ch for ch in text).strip()


def _render_args(args: Any) -> str:
    """The shortest honest rendering of one call's arguments. NEVER raises.

    Total by construction, like every other extraction in this module: the
    reducer's contract is that a malformed line costs that line and nothing
    more, and ``args`` is model-authored JSON that has already survived a wire.
    """

    if not isinstance(args, dict) or not args:
        return ""
    try:
        for key in _ARG_PREFERENCE:
            value = args.get(key)
            if isinstance(value, str) and value:
                return _clip(_strip_control(value))
        parts = [
            f"{k}={v}"
            for k, v in args.items()
            if isinstance(v, (str, int, float, bool))
        ]
        return _clip(_strip_control(", ".join(parts)))
    except Exception:  # noqa: BLE001 — a trail entry is never worth a failed run
        return ""


def _clip(text: str) -> str:
    if len(text) <= MAX_TRAIL_ARG_CHARS:
        return text
    return text[: MAX_TRAIL_ARG_CHARS - 1] + "…"


def _open_trail_entry(state: _StreamState, tool_name: str, event: dict[str, Any]) -> None:
    """Record a call the child just started. NEVER raises.

    Over-budget calls are COUNTED, not silently forgotten: the entry is dropped
    from the trail but :attr:`_StreamState.trail_overflow` grows, so the
    renderer can say how many it is not showing. A trail that simply stopped
    would read as "the child did this much", which is the failure mode every
    truncation in this codebase is written to avoid.

    The entry is built either way and goes into :attr:`_StreamState.recent`
    (#132): the live panel must keep moving past call 200. Only a window
    already holding :data:`_RECENT_MAX` calls — all but at most four of them
    still open — turns a newer one away, and that one is not added later.
    """

    call_id = event.get("tool_call_id")
    entry = ToolCall(
        name=_strip_control(tool_name)[:MAX_TRAIL_ARG_CHARS],
        args=_render_args(event.get("args")),
        call_id=call_id if isinstance(call_id, str) else "",
        turn=state.turn_starts,
    )
    if len(state.recent) < _RECENT_MAX:
        state.recent.append(entry)
        _trim_recent(state)
    if len(state.tool_trail) >= MAX_TRAIL_ENTRIES:
        state.trail_overflow += 1
        return
    state.tool_trail.append(entry)


def _close_trail_entry(state: _StreamState, event: dict[str, Any]) -> None:
    """Attach an outcome to the call this event ends. NEVER raises.

    Matched on ``tool_call_id``, which both events carry, and searched from the
    END because tools complete near where they started even under the parallel
    executor. An unmatched end event is ignored rather than guessed at: a wrong
    pairing would report one tool's failure against another tool's name, which
    is worse than reporting nothing.

    Both records are searched (#132): :attr:`_StreamState.recent` shares the
    trail's objects while the trail has room, and holds the only copy past
    :data:`MAX_TRAIL_ENTRIES`.
    """

    call_id = event.get("tool_call_id")
    if not isinstance(call_id, str) or not call_id:
        return
    failed = bool(event.get("is_error"))
    for entry in reversed(state.recent):
        if entry.call_id == call_id:
            entry.failed = failed
            _trim_recent(state)
            break
    for entry in reversed(state.tool_trail):
        if entry.call_id == call_id:
            entry.failed = failed
            return


def _written_path(args: Any) -> str | None:
    """The path a writer call names, AS WRITTEN (bounded), or ``None``.

    Raw, because it is the key that tells two files apart (see
    :attr:`_StreamState.file_writes`); only :func:`path_for_display` cleans it.
    A path with nothing to show — empty once its control characters are gone —
    is not recorded, as before: a list entry has to name something.
    """

    if not isinstance(args, dict):
        return None
    for key in _FILE_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            if not _strip_control(value):
                return None
            return value[:MAX_FILE_PATH_KEY_CHARS]
    return None


def path_for_display(path: str) -> str:
    """One written path as a file list SHOWS it: one line, no controls, bounded.

    Through :func:`_strip_control`, which also collapses newlines: a path is
    child-authored and is rendered one per line, so a newline inside one could
    otherwise forge extra entries in a list the parent reads as a record. Clipped
    at :data:`MAX_FILE_PATH_CHARS` with ``…``. Two different paths may display
    alike (``a\\nb.txt``, ``a b.txt``); they stay two entries, because the
    reducer keys them by what the child wrote.
    """

    shown = _strip_control(path)
    if len(shown) > MAX_FILE_PATH_CHARS:
        return shown[: MAX_FILE_PATH_CHARS - 1] + "…"
    return shown


def _open_file_write(state: _StreamState, tool_name: str, event: dict[str, Any]) -> None:
    """Record a writer call the child just started. NEVER raises.

    A path past :data:`MAX_FILE_PATHS` is counted, not kept — once per path as
    far as :data:`MAX_OVERFLOW_TRACKED` reaches. A call with no usable
    ``tool_call_id`` can never be matched to its end, so it stays open and its
    path reads as unconfirmed unless another call on it is confirmed: "we did
    not see it finish" is the truth about it.
    """

    try:
        if tool_name not in WRITER_TOOLS:
            return
        path = _written_path(event.get("args"))
        if path is None:
            return
        entry = state.file_writes.get(path)
        if entry is None:
            if len(state.file_writes) >= MAX_FILE_PATHS:
                key = hash(path)
                if key in state.overflow_paths:
                    return
                if len(state.overflow_paths) < MAX_OVERFLOW_TRACKED:
                    state.overflow_paths.add(key)
                state.files_overflow += 1
                return
            entry = FileWrite(path=path)
            state.file_writes[path] = entry
        entry.open_calls += 1
        call_id = event.get("tool_call_id")
        if isinstance(call_id, str) and call_id and len(state.write_calls) < MAX_OPEN_WRITE_CALLS:
            # The entry's own string, so an open call pins no second copy.
            state.write_calls[call_id] = entry.path
    except Exception:  # noqa: BLE001 — a file list is never worth a failed run
        return


def _close_file_write(state: _StreamState, event: dict[str, Any]) -> None:
    """Settle the writer call this event ends. NEVER raises.

    Matched on ``tool_call_id`` only, like the trail: a wrong pairing would
    report one file as written because another was.
    """

    try:
        call_id = event.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            return
        path = state.write_calls.pop(call_id, None)
        if path is None:
            return
        entry = state.file_writes.get(path)
        if entry is None:
            return
        entry.open_calls = max(entry.open_calls - 1, 0)
        if not event.get("is_error"):
            entry.changed = True
    except Exception:  # noqa: BLE001 — a file list is never worth a failed run
        return


def written_paths(state: _StreamState) -> tuple[list[str], list[str]]:
    """``(changed, unconfirmed)`` as the child wrote them — RAW — first-seen order.

    Raw on purpose: ``runtime._written_files`` resolves each against the
    child's directory and only then cleans it for display
    (:func:`path_for_display`).

    CHANGED BEATS UNCONFIRMED: a path some call wrote successfully was written,
    whatever a later call on it was doing when the child stopped. A path whose
    every call ended in an error is in neither list — nothing was written.
    """

    changed: list[str] = []
    unconfirmed: list[str] = []
    for entry in state.file_writes.values():
        if entry.changed:
            changed.append(entry.path)
        elif entry.open_calls > 0:
            unconfirmed.append(entry.path)
    return changed, unconfirmed


def _usage_field(usage: dict[str, Any] | None, *names: str) -> int:
    """First truthy, int-coercible spelling among ``names`` (0 when none).

    Skip-on-falsy rather than first-key-present, matching the kernel's own
    dual-read at ``session/compaction.py:1038-1045``
    (``usage.get("total_tokens") or usage.get("totalTokens")``): an adapter
    that emits both spellings with one of them zeroed must not report zero.

    ``bool`` is excluded explicitly — it is an ``int`` subclass, and a stray
    ``{"input": True}`` reading as 1 token would be a silent lie.

    NON-FINITE FLOATS ARE SKIPPED, NOT COERCED (P2 review, HIGH #3). CPython's
    ``json`` accepts and emits IEEE specials by default, so
    ``json.loads('{"input": Infinity}')`` is a perfectly ordinary line for this
    reducer to receive — and ``int(inf)`` raises ``OverflowError`` while
    ``int(nan)`` raises ``ValueError``. Either one used to escape
    :func:`reduce_line`, whose contract is that it NEVER raises, and take the
    stdout pump with it. Skipping treats a non-finite token count as the
    nonsense it is and falls through to the next spelling.
    """

    if not isinstance(usage, dict):
        return 0
    for name in names:
        value = usage.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if value:
                return value
            continue
        if isinstance(value, float):
            if not math.isfinite(value):
                continue
            if value:
                return int(value)
            continue
        if isinstance(value, str):
            try:
                parsed = int(value.strip())
            except ValueError:
                continue
            if parsed:
                return parsed
    return 0


def _as_finite_float(value: Any) -> float:
    """``value`` as a finite float, or ``0.0``.

    ``float(x)`` is not total over the values a JSON line can carry: Python
    integers are unbounded, so ``float(10 ** 400)`` raises ``OverflowError``
    (measured — a 400-digit ``usage.cost`` is enough), and ``inf`` / ``nan``
    would poison every downstream sum with a value no renderer can format.
    Both are money numbers a child reported about itself; neither is worth
    failing a delegation over.
    """

    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _usage_cost(usage: dict[str, Any] | None) -> float:
    """The child's own cost number, when the adapter computed one.

    pi's shape is ``usage.cost.total``; a flat numeric ``cost`` is accepted too
    because nothing in the wire contract forbids it (ADR-0198 D2 puts the whole
    ``usage`` sub-dict outside the stability guarantee). Absent → 0.0, and the
    non-pure layer recomputes from ``provider``/``model``.

    Every coercion goes through :func:`_as_finite_float`, which is total —
    ``float(...)`` on its own is not (P2 review, HIGH #3).
    """

    if not isinstance(usage, dict):
        return 0.0
    cost = usage.get("cost")
    if isinstance(cost, dict):
        return _as_finite_float(cost.get("total"))
    return _as_finite_float(cost)


def _usage_reports_cost(usage: dict[str, Any]) -> bool:
    """Is there a cost ANSWER in this ``usage``, zero included? NEVER raises.

    The same two shapes :func:`_usage_cost` reads, but asked about presence: a
    finite number under ``cost.total`` or a flat ``cost``. A value that
    :func:`_as_finite_float` would have discarded (``NaN``, ``Infinity``, a
    400-digit integer, a string) is not an answer, so it cannot make a bill
    look exact.
    """

    cost = usage.get("cost")
    if isinstance(cost, dict):
        cost = cost.get("total")
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        return False
    try:
        return math.isfinite(float(cost))
    except (OverflowError, ValueError):
        return False


def _extract_text(content: Any) -> str:
    """Concatenate the ``type == "text"`` blocks of one message body.

    ``thinking`` blocks are skipped: they are the model's scratchpad, they can
    be enormous, and on some providers they are signed material that must not
    be re-surfaced as an answer. ``toolCall`` blocks are skipped for the same
    reason the summary is not built from them — the parent wants the child's
    conclusion, not its transcript.

    Blocks are joined with ``"\\n"``. The plan specifies WHICH blocks to
    concatenate but not the glue; a bare ``""`` join runs the last word of one
    block into the first word of the next, and multiple text blocks only ever
    appear when something (a thinking block, a tool call) separated them — i.e.
    at a genuine paragraph boundary.

    Divergence from pi, deliberate: pi returns only the FIRST text part
    (``index.ts:170-180``), silently discarding everything a model said after
    its first tool call.
    """

    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _reduce_message_end(state: _StreamState, message: Any) -> None:
    """Fold one ``message_end`` payload into ``state``.

    ``message_end`` is the ONLY source of the summary and of the usage numbers.
    ``agent_end`` carries the entire message array again on a single line and is
    treated purely as a terminator — parsing it would double-count every turn.
    """

    if not isinstance(message, dict):
        return
    if message.get("role") != "assistant":
        return
    state.turns += 1

    # Last-write-wins, but only for a NON-EMPTY value: a later message whose
    # ``stop_reason`` is ``None`` (still streaming, or an adapter that omits it)
    # must not erase the "error" a previous one reported.
    stop_reason = message.get("stop_reason")
    if isinstance(stop_reason, str) and stop_reason:
        state.stop_reason = stop_reason
    error_message = message.get("error_message")
    if isinstance(error_message, str) and error_message:
        state.error_message = error_message
    # RESET ON EVERY ASSISTANT MESSAGE, unlike the two fields above (ADR-0276
    # D17): it answers "did the LATEST message end in an error", so a turn that
    # recovered from an earlier one clears it.
    state.final_error = (
        error_message
        if stop_reason == "error" and isinstance(error_message, str) and error_message
        else None
    )
    provider = message.get("provider")
    if isinstance(provider, str) and provider:
        state.provider = provider
    model = message.get("model")
    if isinstance(model, str) and model:
        state.model = model

    text = _extract_text(message.get("content"))
    if text:
        state.summary = text
        # Once per message, here, and never per publish (#132, D3). A message
        # whose text is only whitespace — a ``"\n\n"`` block before a tool
        # call, which ``_extract_text`` keeps — leaves the last reply as it
        # was: MEASURED (#132 review round 1), it set ``last_text`` back to
        # ``None`` and a child that had already replied read "waiting for the
        # first reply" again.
        line = _one_line(text)
        if line:
            state.last_text = line

    # ``usage`` is ``null`` on an errored message (``messages.py:127`` is
    # Optional and the adapters return ``None`` when every counter is zero —
    # e.g. ``providers/anthropic.py:206-207``), so this must survive absence.
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return
    state.input += _usage_field(usage, "input", "input_tokens", "inputTokens")
    state.output += _usage_field(usage, "output", "output_tokens", "outputTokens")
    state.cache_read += _usage_field(usage, "cache_read", "cacheRead")
    state.cache_write += _usage_field(usage, "cache_write", "cacheWrite")
    total = _usage_field(usage, "total_tokens", "totalTokens")
    if total:
        state.tokens = total
    if _usage_reports_cost(usage):
        state.cost_reported = True
    state.cost += _usage_cost(usage)


def reduce_line(state: _StreamState, line: str) -> _StreamState:
    """Fold one line of child stdout into ``state``; return the same instance.

    NEVER raises. Three separate reasons a line may be garbage, all of which
    must be survivable:

    1. The FIRST line is the session-metadata header — a bare
       ``{"id": ..., "created_at": ...}`` object with **no** ``type`` key
       (``modes/print_mode.py:348-360``). It is emitted best-effort inside its
       own ``try/except``, so neither its presence nor its absence may be
       relied on. Hence ``event.get("type")``, never ``event["type"]``.
    2. Anything in the child can ``print()`` straight into the same stdout —
       an extension, an MCP server, a provider SDK. ``print_mode.py:64-67``
       explicitly declines pi's ``takeOverStdout``, so those bytes are NOT
       redirected. Unparseable lines are skipped in silence.
    3. A dropped oversize line leaves a valid-looking fragment behind
       (:class:`LineAssembler` resyncs at the next newline, so this is rare but
       not impossible).

    "NEVER raises" is a CONTRACT, and it was once false (P2 review, HIGH #3).
    A well-formed line carrying ``"usage": {"input": Infinity}`` — CPython's
    ``json`` emits and accepts IEEE specials by default — reached ``int(inf)``
    and raised ``OverflowError`` out of the stdout pump, out of the pump gather,
    past a ``wait_for`` that catches only ``TimeoutError``, and out of
    ``PrintChannel.run`` with NO reaper ever started; the registry row was
    already popped, so ``stop`` / ``stop_all`` / teardown could not reach the
    surviving session leader either. Every numeric coercion below is therefore
    total (:func:`_usage_field`, :func:`_as_finite_float`), AND
    ``print_channel._pump_stdout`` wraps this call as a second line of defence.
    Both halves are deliberate: one keeps the contract, the other keeps the
    process alive if a future reducer breaks it.
    """

    text = line.strip()
    if not text:
        return state
    try:
        event = json.loads(text)
    except (TypeError, ValueError):
        return state
    if not isinstance(event, dict):
        return state
    return reduce_event(state, event)


def reduce_event(state: _StreamState, event: dict[str, Any]) -> _StreamState:
    """Fold one ALREADY-PARSED event into ``state``; return the same instance.

    The same reducer as :func:`reduce_line` with the framing removed, and it
    exists because the two channels hand their events over in different shapes:
    the print channel reads raw stdout bytes and has a ``str`` per line, while
    :class:`~aelix_coding_agent.rpc.rpc_client.RpcClient` has already called
    ``json.loads`` and hands its listeners a ``dict``.

    WITHOUT THIS SPLIT THE RPC PATH FAILS SILENTLY, which is why it is a
    refactor and not a convenience. ``reduce_line``'s first statement is
    ``line.strip()``, so a dict raises ``AttributeError`` immediately — and
    ``RpcClient`` wraps every listener in ``contextlib.suppress(Exception)``.
    The result would be that EVERY event is dropped, ``state`` stays empty, and
    ``build_result`` reports the delegation as ``ok`` with the summary
    ``"(no output)"``. A wrong answer, delivered confidently, with no error
    anywhere. Re-serialising the dict just to re-parse it would have worked, but
    it is wasteful and it hides the same trap one refactor away.

    No field mapping is needed in either direction: both modes serialise the
    same kernel dataclasses through ``dataclasses.asdict``, so the wire shapes
    are identical (``rpc/rpc_mode.py``'s ``_event_to_dict`` and
    ``modes/print_mode.py``'s namesake).

    Same "NEVER raises" contract as :func:`reduce_line` — see its docstring for
    what that cost to learn.
    """

    kind = event.get("type")
    if kind == _AGENT_START:
        state.saw_agent_start = True
        state.run_open = True
    elif kind == _TURN_START:
        # A new turn begins with nothing executing. Clearing here means a
        # statusline row can never be left showing a tool that finished during
        # the previous turn. ``current_tool`` keeps its old meaning — a
        # finished call still reads as current until this event — and the
        # panel reads ``recent``'s outcomes instead (#132).
        state.current_tool = None
        state.turn_starts += 1
        # The previous turn's open calls will never end now: they may leave
        # the window.
        _trim_recent(state)
    elif kind == _MESSAGE_END:
        _reduce_message_end(state, event.get("message"))
    elif kind == _TOOL_EXECUTION_START:
        # ``tool_name``, NOT pi's ``name`` (kernel ``types.py:195-199``).
        tool_name = event.get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            state.current_tool = tool_name
            _open_trail_entry(state, tool_name, event)
            _open_file_write(state, tool_name, event)
    elif kind == _TOOL_EXECUTION_END:
        # NEWLY CONSUMED, and the reversal is deliberate. This event was ignored
        # because nothing downstream had a use for it; the trail does. It is the
        # ONLY place a per-tool failure is observable — ``build_result`` derives
        # status from the exit code and the stream terminator, so a child whose
        # every tool errored still reports ``ok``.
        _close_trail_entry(state, event)
        _close_file_write(state, event)
    elif kind == _AGENT_END:
        state.saw_agent_end = True
        state.run_open = False
        state.current_tool = None
        _trim_recent(state)
    return state


__all__ = [
    "ACTIVITY_MAX",
    "LAST_TEXT_MAX_CHARS",
    "MAX_FILE_PATHS",
    "MAX_FILE_PATH_CHARS",
    "MAX_FILE_PATH_KEY_CHARS",
    "WRITER_TOOLS",
    "FileWrite",
    "LineAssembler",
    "MAX_LINE_BYTES",
    "MAX_TRAIL_ARG_CHARS",
    "MAX_TRAIL_ENTRIES",
    "ToolCall",
    "_StreamState",
    "activity_of",
    "path_for_display",
    "reduce_event",
    "reduce_line",
    "written_paths",
]
