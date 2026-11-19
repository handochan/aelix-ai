"""The parent's end of a delegated child's command inbox (#123, ADR-0306).

One :class:`CommandWire` per :class:`~aelix_agents.print_channel.RunningChild`,
built before the spawn is awaited and attached to the child's stdin right after
it. It writes single JSONL requests — ``steer``, ``follow_up`` (and a final
``steer``, the wrap-up), ``set_permission_mode`` — and correlates the child's
``response`` lines, which the stdout pump hands it instead of the stream reducer
(``print_channel._pump_stdout``). The child's side is
``aelix_coding_agent.modes.print_inbox``.

Bounds, each its own (ADR-0306 §2.1): a write — the line plus its drain — gets
:data:`WRITE_SECONDS`; an acknowledgement :data:`ACK_SECONDS`
(``rpc_client.DEFAULT_SEND_TIMEOUT_MS``'s value; a steer was acknowledged in 40
ms mid-tool and 5 ms idle, contract r1 §14.1). ADR-0276's open Limits item 3 — a
blocked stdin write outside every bound — is closed on this path by the write
bound: a line that cannot drain in time fails its request ``unknown`` and marks
the wire broken, and later requests are refused "stop or resume it".

Outcomes are three words and never "not delivered" (Codex M10; ADR-0306 §3.3):
``queued`` — the child answered ``success: true`` (its ``phase`` says whether a
turn is open to read the line); ``refused`` — it answered ``success: false``;
``unknown`` — no answer within the bound, the write failed after bytes may have
left, or the child ended with the request open (``fail_pending``; that reply is
marked ``ended`` and the door names ``action="resume"``). The one case
reported ``not_sent`` is a write that could not begin: no stdin yet, or a pipe
already closing before the first byte. Nothing is retried, so nothing is
duplicated.

Whether a queued line was CONSUMED is read off the child's own stream, not off
the wire: the loop emits a ``user`` message for every drained steer
(``aelix_agent_core.loop``) and the reducer keeps those texts
(``_StreamState.user_texts``). The wrap-up's reading is a latch beside them
(``_StreamState.wrap_up_read``), set by the first such message that carries its
head after the request was recorded; :func:`wrap_up_consumed` reads it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from aelix_agents.stream import USER_TEXT_HEAD_CHARS, _StreamState

WRITE_SECONDS = 5.0
"""How long one request's write and drain may take (ADR-0306 §2.1; closes
ADR-0276 Limits item 3 on this path). Past it the wire is broken."""

ACK_SECONDS = 30.0
"""How long the child has to answer a request (``DEFAULT_SEND_TIMEOUT_MS``'s
value). Past it the request's outcome is ``unknown``: the line may well be
queued and read later, and nothing here can tell."""

RESPONSE_PREFIX = '{"type": "response"'
"""How the child begins every reply (``print_inbox._reply`` writes ``type``
first with ``json.dumps``'s default separators); the one cheap test the pump
makes before the reducer parses a line."""

WRAP_UP_TEXT = (
    "Wrap up now. You have been asked to stop here. Do not call any more tools. In "
    "one or two sentences, say what you did and what is left undone, then end "
    "your reply."
)
"""The final steer's text (ADR-0306 §4.1; Gemini CLI ``local-executor.ts``
``getFinalWarningMessage`` is the reference). A courtesy to the model: the
kernel's ``final`` flag is what makes the next call tool-less and last. It
names nobody: the user's wrap-up and the parent model's send the same words
(fix round 1, VN2); who asked is the envelope's ``reason`` to say."""

NUDGE_TEXT = (
    "You have repeated the same action with the same result four times. Change "
    "approach, or wrap up and report."
)
"""The one stuck nudge (ADR-0306 §5.3), modelled on OpenHands' ``stuck_detector``
message; sent once per row, never a kill."""

INBOX_BROKEN = "the agent's inbox is broken; stop or resume it"
NOT_STARTED = "the agent has not started yet; retry"
ENDED_UNANSWERED = "the agent ended before answering"

Outcome = Literal["queued", "refused", "unknown", "not_sent"]


@dataclass(frozen=True)
class Reply:
    """One request's outcome, as the runtime doors report it."""

    outcome: Outcome
    data: Mapping[str, Any] | None = None
    error: str | None = None
    ended: bool = False
    """The child's END answered, not the child: the request was open when the
    wire closed (``unknown`` — a line read and not acknowledged before the exit
    is possible) or was made after it (``refused`` — nothing was written). The
    row is past ``send`` either way; the door's words name ``action="resume"``
    (``runtime.Delivery.of``, fix round 1 F3)."""

    @property
    def phase(self) -> str | None:
        """``streaming`` or ``idle`` on a queued reply (the child's word), else ``None``."""

        phase = self.data.get("phase") if self.data is not None else None
        return phase if isinstance(phase, str) else None


@dataclass(frozen=True)
class WrapUp:
    """A wrap-up asked of one row (ADR-0306 §4.3): who asked, what was sent, when."""

    origin: str
    text: str
    requested_at: float

    @property
    def head(self) -> str:
        return self.text[:USER_TEXT_HEAD_CHARS]


class CommandWire:
    """Request ids, the two bounds, the correlator, the close-out. One per row."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._count = 0
        self._writer: Any = None
        self._pending: dict[str, asyncio.Future[Reply]] = {}
        self.broken = False
        """A write that failed or could not drain in time (ADR-0306 §3.6)."""
        self.closed = False

    def attach(self, writer: Any) -> None:
        """The child's stdin, once there is a process."""

        self._writer = writer

    @property
    def attached(self) -> bool:
        return self._writer is not None

    @property
    def pending(self) -> int:
        return len(self._pending)

    # ── the correlator ──────────────────────────────────────────────

    def take(self, line: str) -> bool:
        """``True`` when ``line`` is a ``response`` (taken here); ``False`` for a stream event."""

        if not line.startswith(RESPONSE_PREFIX):
            return False
        try:
            payload = json.loads(line)
        except ValueError:
            return False
        if not isinstance(payload, dict) or payload.get("type") != "response":
            return False
        request_id = payload.get("id")
        future = self._pending.pop(request_id, None) if isinstance(request_id, str) else None
        if future is None or future.done():
            return True  # answered after its bound, or nobody asked: not a stream event either way
        if payload.get("success") is True:
            data = payload.get("data")
            future.set_result(Reply("queued", data=data if isinstance(data, dict) else {}))
        else:
            error = payload.get("error")
            future.set_result(Reply("refused", error=error if isinstance(error, str) else "refused"))
        return True

    # ── requests ────────────────────────────────────────────────────

    async def request(
        self, command: str, payload: Mapping[str, Any], *, timeout: float | None = None
    ) -> Reply:
        """Write one request line and wait for its answer, under both bounds.

        ``timeout`` defaults to :data:`ACK_SECONDS` as it stands when called."""

        if timeout is None:
            timeout = ACK_SECONDS
        if self.closed:
            return Reply("refused", error=ENDED_UNANSWERED, ended=True)
        if self.broken:
            return Reply("refused", error=INBOX_BROKEN)
        writer = self._writer
        if writer is None:
            return Reply("not_sent", error=NOT_STARTED)
        transport = getattr(writer, "transport", None)
        if transport is not None and transport.is_closing():
            # Nothing left: the pipe was gone before the first byte.
            return Reply("not_sent", error="the agent's inbox is closed")
        self._count += 1
        request_id = f"{self._prefix}-{self._count}"
        line = json.dumps({"type": command, "id": request_id, **payload}) + "\n"
        future: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            try:
                writer.write(line.encode("utf-8"))
                await _drained(writer)
            except TimeoutError:
                self._pending.pop(request_id, None)
                self.broken = True
                return Reply("unknown", error=f"the agent's inbox did not take the line within {WRITE_SECONDS:g} s")
            except (OSError, RuntimeError, ValueError) as exc:
                # ``BrokenPipeError``/``ConnectionResetError`` are ``OSError``; a closed
                # transport raises ``RuntimeError``. Bytes may have left: unknown.
                self._pending.pop(request_id, None)
                self.broken = True
                return Reply("unknown", error=f"the write to the agent's inbox failed: {type(exc).__name__}")
            done, _ = await asyncio.wait({future}, timeout=timeout)
        except asyncio.CancelledError:
            # #612 (contract r5 §3.4; Codex r4-2 9): the caller stopped waiting — a
            # host's request a session change settled. Its correlation entry goes
            # with it, so a late answer is an unknown id; the line, whose write
            # had begun, may still be read.
            self._pending.pop(request_id, None)
            raise
        if not done:
            self._pending.pop(request_id, None)
            return Reply("unknown", error=f"the agent did not answer within {timeout:g} s")
        return future.result()

    # ── close-out ───────────────────────────────────────────────────

    def fail_pending(self, reason: str = ENDED_UNANSWERED) -> None:
        """Every request still open ends ``unknown`` with ``reason``, marked ``ended``.

        The one caller is :meth:`close`, at the child's end."""

        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_result(Reply("unknown", error=reason, ended=True))

    def close(self) -> None:
        """Fail what is open and close the write end. Idempotent, never raises."""

        self.closed = True
        self.fail_pending()
        writer, self._writer = self._writer, None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()


async def _drained(writer: Any) -> None:
    """``writer.drain()`` within :data:`WRITE_SECONDS`; :class:`TimeoutError` past it.

    ``asyncio.wait``, not ``asyncio.wait_for`` (#612): on Python 3.11 ``wait_for``
    returns the drain's result when the caller's cancel lands in the turn the drain
    completed (CPython gh-86296, fixed in 3.12), so a request a session change
    settled went on to wait for its answer (measured,
    ``.omc/probes/612/impl/A/wait-for-311.txt``). ``wait`` never keeps a cancel:
    the drain is cancelled with the caller, and on the bound.
    """

    drain = asyncio.ensure_future(writer.drain())
    try:
        done, _ = await asyncio.wait({drain}, timeout=WRITE_SECONDS)
    except asyncio.CancelledError:
        drain.cancel()
        raise
    if not done:
        drain.cancel()
        raise TimeoutError
    drain.result()


def close_wire(wire: Any) -> None:
    """``CommandWire.close`` for a row that may have no wire (a stub channel's)."""

    if wire is not None:
        with contextlib.suppress(Exception):
            wire.close()


def wrap_up_consumed(wrap_up: WrapUp | None, state: _StreamState) -> bool:
    """Did the child's model read the wrap-up? The loop emitted its ``user`` message.

    The reducer's latch, not a search of the kept texts (fix round 1, F4): a
    message with the same words before the request does not count, and the
    notes drained with the final do not push its reading out of a window.
    """

    return wrap_up is not None and state.wrap_up_read and state.wrap_up_head == wrap_up.head


def new_wrap_up(origin: str, *, text: str = WRAP_UP_TEXT) -> WrapUp:
    return WrapUp(origin=origin, text=text, requested_at=time.monotonic())


class InterventionNotSent(asyncio.CancelledError):
    """A door's cancel that landed before its line's write began (#612 fix round 1, V-F1).

    Still a :class:`asyncio.CancelledError`, so a caller that propagates a cancel
    goes on doing so. ``not_sent`` is what a host reads (``SubagentControls.wrap_up``):
    product core does not import this module. ``runtime.wrap_up`` raises it when a
    cancel lands while an open approval request's ``cancel`` waits for its
    acknowledgement, ahead of the final steer (ADR-0308), once it has taken the
    wrap-up back.
    """

    not_sent = True


__all__ = [
    "InterventionNotSent",
    "ACK_SECONDS",
    "ENDED_UNANSWERED",
    "INBOX_BROKEN",
    "NOT_STARTED",
    "NUDGE_TEXT",
    "RESPONSE_PREFIX",
    "WRAP_UP_TEXT",
    "WRITE_SECONDS",
    "CommandWire",
    "Reply",
    "WrapUp",
    "close_wire",
    "new_wrap_up",
    "wrap_up_consumed",
]
