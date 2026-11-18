"""The command inbox a delegated child serves on its stdin while its one prompt runs (#123, ADR-0306).

A delegated child is a ``--mode json -p`` process (``aelix_agents.print_channel``)
whose task is on its argv and whose stdin used to be ``DEVNULL``. With the hidden
``--command-inbox=1`` flag the parent keeps a pipe to that stdin open instead, and
this module reads it beside print mode's prompt: one JSONL request per line, in
arrival order, each dispatched to the harness print mode already holds
(``runtime_host.harness``), each answered on stdout in the rpc wire's
``response`` shape through print mode's own writer, so the line order on stdout is
the loop's. Completion stays process exit, exactly as before: the inbox has no
``prompt`` and never gates the child's exit.

The protocol is private and versioned (the version rides the flag; a version this
build does not serve ends the launch, ``cli/args.py``). It is aelix-original: pi
has no inbox on a print child (its background subagents are conversations of one
runtime, ``packages/durable``), a divergence ADR-0306 records.

Requests, ``{"type": <command>, "id": <str>, ...}`` — a line whose ``id`` is not a
string is dropped and nothing is run (F8):

* ``steer {message, final?}`` → ``harness.steer(message, final=final)``: read at
  the child's next turn boundary, after the tool calls it is already running. A
  final steer sets the kernel's latch (ADR-0306 §4): the next model call has no
  tools (nor has an automatic retry or an overflow re-run of it), then the run ends.
  ``final`` must be JSON ``true`` or ``false``: anything else is answered
  ``success: false`` and nothing is queued (F8).
* ``follow_up {message}`` → ``harness.follow_up``: read once the current answer
  has no tool calls and no steers left.
* ``set_permission_mode {mode}`` → the child's own ``PermissionPosture.set`` when
  ``mode`` ranks at or below the current one (``aelix_agents.posture``'s order,
  never ``CYCLE_ORDER``); a widen is refused by the child itself, so the
  rpc-channel docstring's reasoning holds on both ends.

Nothing else: no ``prompt`` (one process = one turn + steer), no ``abort`` (a stop
is today's kill, ADR-0291; the cooperative abort rewrites the transcript,
ADR-0286 §6), no ``get_state``.

Replies: ``{"type": "response", "command": <command>, "id": <id>, "success": true,
"data": {...}}`` or ``..., "success": false, "error": "..."}`` — the rpc
``response`` shape, so the parent's stdout pump routes them by ``type`` and the
stream reducer never sees them. A line that is not JSON, not an object or without a
string ``id`` is dropped; one with a string ``id`` that names none of the three
commands, or a command's bad field, is answered ``success: false`` by that id; a
line that is not UTF-8 is dropped whole (:func:`_pump_lines`); nothing on the
inbox is fatal.

EOF is the death detector: a parent that dies, or closes its end, ends the pipe.
The pump then sets :attr:`CommandInbox.ended` and calls ``harness.abort()`` once,
and what ends the run depends on the moment (#123 fix round 1, F2):

* **before the prompt** — print mode checks the flag before each
  ``harness.prompt`` and starts none: no model call. (An abort alone is erased
  when the prompt claims its turn.)
* **in a stream or in a tool** — the abort ends the turn as any abort ends it:
  an ``aborted`` answer, the tool's process stopped.
* **in the drain** (the answer is in; the run waits for its own background jobs,
  ADR-0293) — there is no turn to abort: the flag is part of the drain's
  ``stop``, so the wait ends within its 1 s tick and no notice turn starts.

In each the run goes on to its verdict and exits 1, ``aelix_print_done`` still
written where the run was asked for it, and print mode's dispose stops the jobs
the child started. Print mode ends the pump in its own ``finally`` before the
EOF can fire on a run that finished: :func:`end_inbox` never sets the flag.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aelix_coding_agent.builtin.permission_mode import (
    PermissionMode,
    PermissionPosture,
    authority_rank,
)
from aelix_coding_agent.rpc._jsonl import JsonlLineReader

RESPONSE_TYPE = "response"
"""The ``type`` of every reply; the parent's pump routes on it before the reducer."""


@dataclass
class CommandInbox:
    """What ``cli/entry.py`` opens for a launch that carried ``--command-inbox``."""

    reader: asyncio.StreamReader
    posture: PermissionPosture
    """The ONE posture holder the gate reads (``cli/entry.py`` threads it into the
    permission extension too), so a tighten the parent sends governs the child's
    next approval."""
    pump_thread: threading.Thread | None = None
    """The win32 ``os.read`` daemon thread (``rpc_mode._thread_pumped_stdin_reader``);
    ``None`` on POSIX. Never joined: a blocking read on stdin cannot be interrupted."""
    ended: bool = False
    """Stdin reached EOF, or broke, while the run was going: the parent is gone or
    closed its end. Set by the pump before it aborts the turn and never by
    :func:`end_inbox`; print mode reads it where the abort does not reach
    (``modes/print_mode.py``, ``_inbox_ended``)."""


Writer = Callable[[str], None]
Handler = Callable[[Any, CommandInbox, Mapping[str, Any]], Awaitable[dict[str, Any]]]


def serve_inbox(runtime_host: Any, inbox: CommandInbox | None, *, write: Writer) -> asyncio.Task[None]:
    """Start the pump as a task; a no-op task for ``None`` (print mode calls this unconditionally)."""

    return asyncio.ensure_future(_serve(runtime_host, inbox, write))


async def end_inbox(pump: asyncio.Task[None]) -> None:
    """End the pump without the EOF abort: the run is over, whatever stdin still holds."""

    pump.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await pump


async def _serve(runtime_host: Any, inbox: CommandInbox | None, write: Writer) -> None:
    if inbox is None:
        return
    dispatches: set[asyncio.Task[None]] = set()

    def on_line(line: str) -> None:
        # One task per line, started in arrival order: a handler that awaits
        # (``steer`` emits ``queue_update``) must not hold the next line back.
        task = asyncio.ensure_future(_dispatch(runtime_host, inbox, line, write))
        dispatches.add(task)
        task.add_done_callback(dispatches.discard)

    try:
        # A broken stdin is an EOF, not a crash.
        with contextlib.suppress(Exception):
            await _pump_lines(inbox.reader, JsonlLineReader(on_line))
        # EOF while the run is still going: the parent is gone. Recorded first —
        # print mode reads the flag before each prompt, in its drain and with its
        # verdict, where the abort does not reach — then the turn is aborted. Once.
        inbox.ended = True
        with contextlib.suppress(Exception):
            await runtime_host.harness.abort()
    finally:
        for task in list(dispatches):
            task.cancel()


async def _pump_lines(stream: asyncio.StreamReader, lines: JsonlLineReader) -> None:
    """Read ``stream`` to EOF and hand ``lines`` one decoded line at a time.

    The framing stays the rpc reader's (LF only, a trailing CR stripped, the
    residue at EOF is a line). The decode is this inbox's own (fix round 1 F7):
    the bytes are split on LF first and each line is decoded by itself, so a line
    that is not UTF-8 is dropped and nothing else is. ``rpc._jsonl.pump_jsonl_lines``
    decodes the whole stream strictly and raises on such a byte, which here ended
    the turn as if stdin had closed. LF is never a byte of a multi-byte UTF-8
    sequence, so the split cannot cut a character.
    """

    tail = b""
    while True:
        chunk = await stream.read(4096)
        *whole, tail = (tail + chunk).split(b"\n")
        for raw in whole:
            _feed_line(lines, raw + b"\n")
        if not chunk:
            _feed_line(lines, tail)
            lines.end()
            return


def _feed_line(lines: JsonlLineReader, raw: bytes) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return  # not UTF-8: this line is dropped, never fatal
    lines.feed(text)


async def _dispatch(runtime_host: Any, inbox: CommandInbox, line: str, write: Writer) -> None:
    try:
        payload = json.loads(line)
    except ValueError:
        return  # not JSON: dropped, never fatal
    if not isinstance(payload, dict):
        return
    request_id = payload.get("id")
    if not isinstance(request_id, str):
        # F8 — the parent's wire always sends a string id (``command_wire``) and
        # routes a reply by it: a command without one is not the parent's, and
        # a reply with ``"id": null`` would reach nobody. Dropped, nothing run.
        return
    command = payload.get("type")
    handler = _HANDLERS.get(command) if isinstance(command, str) else None
    if handler is None:
        _reply(write, command, request_id, error=f"unknown inbox command {command!r}")
        return
    try:
        data = await handler(runtime_host, inbox, payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a refusal is the reply
        _reply(write, command, request_id, error=str(exc) or type(exc).__name__)
        return
    _reply(write, command, request_id, data=data)


def _reply(
    write: Writer,
    command: Any,
    request_id: str,
    *,
    data: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    body: dict[str, Any] = {"type": RESPONSE_TYPE, "command": command, "id": request_id}
    if error is None:
        body["success"] = True
        body["data"] = data if data is not None else {}
    else:
        body["success"] = False
        body["error"] = error
    # A dead stdout is print mode's to notice (its event emitter records it);
    # a reply that cannot be written is not a reason to end the turn.
    with contextlib.suppress(Exception):
        write(json.dumps(body) + "\n")


def _message_of(payload: Mapping[str, Any]) -> str:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("'message' must be a non-empty string")
    return message


def _phase(harness: Any) -> str:
    """``streaming`` while a turn is open to read the line, else ``idle`` (contract r2 §3.2)."""

    return "streaming" if getattr(harness, "phase", None) == "turn" else "idle"


async def _steer(runtime_host: Any, _inbox: CommandInbox, payload: Mapping[str, Any]) -> dict[str, Any]:
    harness = runtime_host.harness
    final = payload.get("final", False)
    if not isinstance(final, bool):
        # F8 — ``bool("false")`` is true: a final that is not JSON ``true`` or
        # ``false`` is refused whole, never read as either.
        raise ValueError("'final' must be a JSON boolean")
    await harness.steer(_message_of(payload), final=final)
    return {"phase": _phase(harness)}


async def _follow_up(runtime_host: Any, _inbox: CommandInbox, payload: Mapping[str, Any]) -> dict[str, Any]:
    harness = runtime_host.harness
    await harness.follow_up(_message_of(payload))
    return {"phase": _phase(harness)}


async def _set_permission_mode(
    _runtime_host: Any, inbox: CommandInbox, payload: Mapping[str, Any]
) -> dict[str, Any]:
    # The authority ladder is the one ``aelix_agents.posture`` clamps by
    # (ADR-0199 §3.9), declared beside the enum since #123 so product-core
    # needs no import of the extension (``test_p2_import_direction``).
    raw = payload.get("mode")
    try:
        wanted = PermissionMode(raw)
    except ValueError:
        raise ValueError(f"{raw!r} is not a permission mode") from None
    current = inbox.posture.get()
    if authority_rank(wanted) > authority_rank(current):
        raise ValueError("a delegated child's posture only tightens")
    inbox.posture.set(wanted)
    return {"mode": inbox.posture.get().value}


_HANDLERS: dict[str, Handler] = {
    "steer": _steer,
    "follow_up": _follow_up,
    "set_permission_mode": _set_permission_mode,
}


__all__ = ["RESPONSE_TYPE", "CommandInbox", "end_inbox", "serve_inbox"]
