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
``dataclasses.asdict`` (``modes/print_mode.py:59-68`` → ``rpc/rpc_mode.py``),
so the wire names are the Python attribute names: ``stop_reason``,
``error_message``, ``tool_name``, ``total_tokens``. A line-for-line port of
pi's camelCase parser reads ``None`` for every field, and every failure then
looks like a success with zero usage. ``test_snake_case_fields_read`` is the
regression pin for that trap.

The ONE exception is ``AssistantMessage.usage`` (``aelix_ai/messages.py:127``),
typed ``dict[str, Any]`` and passed through ``asdict`` untransformed: its keys
are whatever the adapter wrote, so it is explicitly OUT of the wire-shape
guarantee and every read here dual-spells, exactly as the kernel itself does at
``session/compaction.py:1036-1043``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

# Per-line budget. A line longer than this is DISCARDED and counted, never
# raised on and never buffered — an unbounded line is the one shape in which a
# malfunctioning (or hostile) child can exhaust parent memory. 4 MiB is ~20x the
# largest routine ``message_end`` measured (a ~207 KB whole-file read), so it
# only fires on genuine pathology. The pump's ``StreamReader`` is opened with a
# larger ``limit=`` (8 MiB, ``print_channel.py``) so the reader itself never
# raises before this budget can act.
MAX_LINE_BYTES = 4 * 1024 * 1024

# Event types this reducer consumes. Everything else — ``message_start``,
# ``message_update``, ``tool_execution_end``, ``turn_end``, the auto-retry
# pair — is ignored on purpose. ``message_update`` in particular MUST be
# ignored: its ``partial`` repeats the whole assistant message on every delta
# (measured stream-to-result ratios of 329x and 2773x), while the outer
# ``message.content`` stays ``[]`` for the duration, so consuming it costs
# O(n^2) and yields nothing.
_AGENT_START = "agent_start"
_TURN_START = "turn_start"
_MESSAGE_END = "message_end"
_TOOL_EXECUTION_START = "tool_execution_start"
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

    Decoding is deferred to the line boundary and uses ``errors="replace"``, so
    a multi-byte code point split across a chunk boundary survives intact and a
    genuinely malformed byte can never raise into the pump.
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
            lines.append(raw.decode("utf-8", errors="replace"))
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
        return [raw.decode("utf-8", errors="replace")]


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

    stop_reason: str | None = None
    error_message: str | None = None

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0

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

    dropped_lines: int = 0
    """Folded in from :attr:`LineAssembler.dropped_lines` by the pump, so the
    envelope builder has one place to read."""


def _usage_field(usage: dict[str, Any] | None, *names: str) -> int:
    """First truthy, int-coercible spelling among ``names`` (0 when none).

    Skip-on-falsy rather than first-key-present, matching the kernel's own
    dual-read at ``session/compaction.py:1036-1043``
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
    provider = message.get("provider")
    if isinstance(provider, str) and provider:
        state.provider = provider
    model = message.get("model")
    if isinstance(model, str) and model:
        state.model = model

    text = _extract_text(message.get("content"))
    if text:
        state.summary = text

    # ``usage`` is ``null`` on an errored message (``messages.py:127`` is
    # Optional and the adapters return ``None`` when every counter is zero —
    # e.g. ``providers/anthropic.py:150-151``), so this must survive absence.
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
    state.cost += _usage_cost(usage)


def reduce_line(state: _StreamState, line: str) -> _StreamState:
    """Fold one line of child stdout into ``state``; return the same instance.

    NEVER raises. Three separate reasons a line may be garbage, all of which
    must be survivable:

    1. The FIRST line is the session-metadata header — a bare
       ``{"id": ..., "created_at": ...}`` object with **no** ``type`` key
       (``modes/print_mode.py:173-185``). It is emitted best-effort inside its
       own ``try/except``, so neither its presence nor its absence may be
       relied on. Hence ``event.get("type")``, never ``event["type"]``.
    2. Anything in the child can ``print()`` straight into the same stdout —
       an extension, an MCP server, a provider SDK. ``print_mode.py:23-26``
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

    kind = event.get("type")
    if kind == _AGENT_START:
        state.saw_agent_start = True
    elif kind == _TURN_START:
        # A new turn begins with nothing executing. Clearing here means a
        # statusline row can never be left showing a tool that finished during
        # the previous turn (``tool_execution_end`` is deliberately not
        # consumed — see the module docstring).
        state.current_tool = None
    elif kind == _MESSAGE_END:
        _reduce_message_end(state, event.get("message"))
    elif kind == _TOOL_EXECUTION_START:
        # ``tool_name``, NOT pi's ``name`` (kernel ``types.py:195-199``).
        tool_name = event.get("tool_name")
        if isinstance(tool_name, str) and tool_name:
            state.current_tool = tool_name
    elif kind == _AGENT_END:
        state.saw_agent_end = True
        state.current_tool = None
    return state


__all__ = [
    "LineAssembler",
    "MAX_LINE_BYTES",
    "_StreamState",
    "reduce_line",
]
