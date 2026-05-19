"""Pi parity: ``packages/coding-agent/src/modes/rpc/rpc-mode.ts`` (492 LOC).

RPC mode entry point + per-command handlers + signal-aware shutdown +
event pipe. Headless operation behind a JSONL stdin/stdout wire.

Protocol (Pi parity):

- Commands arrive as JSONL on stdin; ``parse_rpc_command`` dispatches.
- Responses are emitted as JSONL on stdout (correlated by optional ``id``).
- Session events flow out via :meth:`AgentHarness.subscribe` → stdout
  WITHOUT transformation (Pi ``session.subscribe((event) => output(event))``).
- ``contextlib.redirect_stdout(sys.stderr)`` hijacks any stray ``print()`` so
  the JSONL stream stays uncorrupted (Pi ``takeOverStdout()``).
- SIGTERM / SIGHUP trigger graceful shutdown (Pi
  ``rpc-mode.ts:registerSignalHandlers``).

Sprint 6d ships **9 supported handlers** wired to the existing
:class:`AgentHarness` surface and **20 deferred handlers** as
:class:`RpcErrorResponse` stubs per Pi parity (P-107 + ADR-0058 closure).
The Pi RpcCommand variant count is **29** (W4 M2 / P-121 — the original
spec text said "28" but the fixture's ``rpc_command_types`` list is the
authoritative wire surface).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import signal
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aelix_coding_agent.rpc._jsonl import (
    JsonlLineReader,
    serialize_json_line,
)
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommand,
    RpcCommandBash,
    RpcCommandCompact,
    RpcCommandGetMessages,
    RpcCommandGetState,
    RpcCommandNewSession,
    RpcCommandPrompt,
    RpcCommandSetSessionName,
    RpcCommandSetThinkingLevel,
    RpcErrorResponse,
    RpcResponse,
    RpcSessionState,
    RpcSuccessResponse,
    parse_rpc_command,
)

if TYPE_CHECKING:
    from aelix_agent_core.harness.core import AgentHarness


# === Deferred command allowlist (P-107 + ADR-0058) ============================
#
# 20 commands Aelix's current AgentHarness can't satisfy directly. Every
# entry returns :class:`RpcErrorResponse` with the owning ADR string in
# the error message — the wire shape matches Pi's
# ``{success: false, error: "..."}`` envelope. The closure pin asserts
# 9 supported + 20 deferred = 29 total (= Pi RpcCommand variant count).

DEFERRED_COMMANDS: dict[str, str] = {
    "steer": "ADR-0058 — Sprint 6f harness command paths",
    "follow_up": "ADR-0058 — Sprint 6f harness command paths",
    "set_model": "ADR-0058 — Sprint 6e ModelRegistry",
    "cycle_model": "ADR-0058 — Sprint 6e ModelRegistry",
    "get_available_models": "ADR-0058 — Sprint 6e ModelRegistry",
    "cycle_thinking_level": "ADR-0058 — Sprint 6f",
    "set_steering_mode": "ADR-0058 — Sprint 6f",
    "set_follow_up_mode": "ADR-0058 — Sprint 6f",
    "set_auto_compaction": "ADR-0058 — Sprint 6f",
    "set_auto_retry": "ADR-0058 — Sprint 6f",
    "abort_retry": "ADR-0058 — Sprint 6f",
    "abort_bash": "ADR-0058 — Sprint 6f bash cancellation token",
    "get_session_stats": "ADR-0058 — Sprint 6f session inspection",
    "export_html": "ADR-0058 — Sprint 6f session inspection",
    "switch_session": "ADR-0058 — Sprint 6f session tree navigation",
    "fork": "ADR-0058 — Sprint 6f session tree navigation",
    "clone": "ADR-0058 — Sprint 6f session tree navigation",
    "get_fork_messages": "ADR-0058 — Sprint 6f session tree navigation",
    "get_last_assistant_text": "ADR-0058 — Sprint 6f session tree navigation",
    "get_commands": "ADR-0058 — Sprint 6e extension/skill/template aggregation",
}


# Supported command discriminator → handler name mapping. The closure pin
# (``tests/pi_parity/test_phase_4_4_strict_superset.py``) asserts the
# union of ``SUPPORTED_COMMANDS`` and ``DEFERRED_COMMANDS`` equals the
# 29-element Pi ``RpcCommand`` discriminator set (W4 M2 / P-121).
SUPPORTED_COMMANDS: frozenset[str] = frozenset(
    {
        "prompt",
        "abort",
        "new_session",
        "get_state",
        "get_messages",
        "compact",
        "bash",
        "set_thinking_level",
        "set_session_name",
    }
)


# === Helpers ===================================================================


def _dataclass_to_dict(value: Any) -> Any:
    """Convert dataclass-tree values to plain dicts for JSON serialization.

    Pi emits ``AgentSessionEvent`` objects directly (TS plain objects); the
    Aelix equivalent is a dataclass tree. ``dataclasses.asdict`` produces
    the Pi-shape ``{type, ...}`` dict the wire expects.
    """

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Serialize a harness session event to a Pi-shape dict for stdout.

    Pi parity: ``rpc-mode.ts:86-87`` — events flow out without
    transformation (``session.subscribe((event) => output(event))``).
    """

    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        return dataclasses.asdict(event)
    if isinstance(event, dict):
        return event
    # Fallback for non-dataclass listeners — rely on JSON-serializability.
    return {"event": repr(event)}


# === Supported handlers (Pi parity per P-107) =================================


async def _handle_prompt(
    harness: AgentHarness,
    cmd: RpcCommandPrompt,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:237-260`` (prompt handler).

    Pi fires the prompt asynchronously and emits the response only after
    ``preflightResult(true)``. Aelix has no preflight callback yet — we
    schedule the prompt as a fire-and-forget task and acknowledge
    immediately (Pi's behavior when preflight succeeds synchronously).
    Errors during the run are emitted later via the event stream.
    """

    async def _run() -> None:
        # Errors are observable via the AgentEvent stream and stderr; the
        # response was already emitted (Pi parity: ``rpc-mode.ts:237-260``).
        # W4 m2 / P-119: do NOT silently suppress — log the exception to
        # stderr so the operator sees failures and ``wait_for_idle``
        # callers don't hang on a dropped error.
        try:
            await harness.prompt(cmd.message, source="rpc")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[rpc] prompt task failed: {exc!r}",
                file=sys.stderr,
                flush=True,
            )
            # NOTE: Pi parity (`rpc-mode.ts:379-401`) also emits a synthetic
            # terminal event so the client's `wait_for_idle` listener
            # unblocks. The Aelix harness does not yet expose a public
            # event-emit method to feed an `agent_end` event from outside;
            # Sprint 6f wires that bridge per ADR-0058 carry-forward.

    task = asyncio.create_task(_run())
    # Pin the task on the harness so disposal awaits completion. W4 m3:
    # `AgentHarness` always defines `_pending_tasks` (constructor seeds
    # the set at line ~590); the prior `hasattr` guard was dead. The
    # private-attr read is intentional — Sprint 6f gates a public
    # `track_task()` helper per ADR-0058 carry-forward.
    harness._pending_tasks.add(task)
    task.add_done_callback(harness._pending_tasks.discard)
    return RpcSuccessResponse(id=cmd.id, command="prompt")


async def _handle_abort(
    harness: AgentHarness,
    cmd: Any,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:272-275`` (abort handler)."""

    await harness.abort()
    return RpcSuccessResponse(id=cmd.id, command="abort")


async def _handle_new_session(
    harness: AgentHarness,
    cmd: RpcCommandNewSession,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:277-282`` (new_session handler).

    Aelix harness has no ``runtimeHost.newSession`` equivalent yet — the
    fork/clone Sprint 6f deliverable owns that. Sprint 6d ships the
    minimal cancel-aware shape: harness.abort() clears in-flight queues
    and the response reports ``cancelled: false`` because no extension
    veto path exists yet.

    P-117: ``parent_session`` lineage tracking requires session-tree
    navigation (fork/switch/clone) which is deferred to Sprint 6f per
    ADR-0058. Reject the request explicitly rather than silently dropping
    the field, so callers see a parity-shaped error envelope.
    """

    if cmd.parent_session is not None:
        return RpcErrorResponse(
            id=cmd.id,
            command="new_session",
            error=(
                "parent_session lineage tracking deferred to Sprint 6f "
                "(ADR-0058)"
            ),
        )
    if not harness.is_idle:
        await harness.abort()
        with contextlib.suppress(Exception):
            await harness.wait_for_idle()
    # Pi shape: ``{cancelled: bool}``. Aelix never cancels in this
    # Sprint 6d path; the deferred Sprint 6f work adds the runtimeHost
    # veto callback.
    return RpcSuccessResponse(
        id=cmd.id,
        command="new_session",
        data={"cancelled": False},
    )


async def _handle_get_state(
    harness: AgentHarness,
    cmd: RpcCommandGetState,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:288-302`` (get_state handler).

    Inspects the harness for the 12 :class:`RpcSessionState` fields.

    P-118: this handler reads only public properties on ``AgentHarness``;
    no ``_``-prefixed attribute access. The closure pin asserts the
    invariant so future drift trips mechanically.
    """

    state = harness.state

    model_dict: dict[str, Any] | None = None
    if state.model is not None:
        # Pi emits the Model record verbatim; ``dataclasses.asdict`` gives
        # the Pi-shape camelCase-free dict (Aelix Model uses snake_case
        # internally — see ADR-0035).
        model_dict = _dataclass_to_dict(state.model)

    # P-116: ``is_streaming`` covers every non-idle phase (turn + tool
    # execution + compaction) so RPC clients see the harness as busy
    # whenever it is not idle. ``is_compacting`` is the strict subset.
    is_streaming = harness.phase != "idle"

    rpc_state = RpcSessionState(
        session_id=state.session_id or "",
        thinking_level=state.thinking_level,
        is_streaming=is_streaming,
        is_compacting=harness.phase == "compaction",
        steering_mode=harness.steering_mode,
        follow_up_mode=harness.follow_up_mode,
        message_count=len(state.messages),
        pending_message_count=harness.pending_message_count,
        # W4 m1 — Pi parity carry-forward: hardcoded ``True`` until
        # Sprint 6f wires AutoCompaction state through the harness. See
        # ``DEFERRED_COMMANDS["set_auto_compaction"]`` for the owning
        # ADR-0058 entry.
        auto_compaction_enabled=True,
        model=model_dict,
        session_file=harness.session_file,
        session_name=harness.session_name,
    )
    return RpcSuccessResponse(
        id=cmd.id, command="get_state", data=rpc_state.to_json()
    )


async def _handle_get_messages(
    harness: AgentHarness,
    cmd: RpcCommandGetMessages,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:447-449`` (get_messages handler)."""

    messages = [_dataclass_to_dict(m) for m in harness.messages]
    return RpcSuccessResponse(
        id=cmd.id, command="get_messages", data={"messages": messages}
    )


async def _handle_compact(
    harness: AgentHarness,
    cmd: RpcCommandCompact,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:358-361`` (compact handler)."""

    try:
        result = await harness.compact(cmd.custom_instructions)
    except Exception as exc:  # noqa: BLE001
        return RpcErrorResponse(
            id=cmd.id,
            command="compact",
            error=str(exc),
        )
    return RpcSuccessResponse(
        id=cmd.id,
        command="compact",
        data=_dataclass_to_dict(result),
    )


async def _handle_bash(
    harness: AgentHarness,
    cmd: RpcCommandBash,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:387-389`` (bash handler).

    Sprint 6d invokes the builtin bash tool's underlying operations
    directly (Pi ``session.executeBash`` ultimately funnels into the
    same shell exec). We sidestep the harness's tool-dispatch path
    because bash here is an ad-hoc command from the RPC client, not a
    model-issued tool call.
    """

    from aelix_coding_agent.tools._truncate import truncate_tail
    from aelix_coding_agent.tools.bash import create_local_bash_operations

    ops = create_local_bash_operations()
    cwd = harness._options.cwd or "."
    chunks: list[bytes] = []
    exit_result = await ops.exec(
        cmd.command,
        cwd,
        on_data=chunks.append,
        signal=None,
    )
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    body, info = truncate_tail(raw, max_lines=256, max_bytes=32 * 1024)
    # P-115 BLOCKING: Pi ``BashResult`` shape (Pi
    # ``coding-agent/core/bash-executor.ts:29-40``):
    #   ``{output: string, exitCode: number | undefined,
    #     cancelled: boolean, truncated: boolean,
    #     fullOutputPath?: string}``
    # Sprint 6d has no bash cancellation signal yet (see
    # ``DEFERRED_COMMANDS["abort_bash"]`` → ADR-0058 Sprint 6f), so
    # ``cancelled`` is always False. ``fullOutputPath`` is omitted when
    # the local executor produced no spill file.
    data: dict[str, Any] = {
        "output": body,
        "exitCode": exit_result.exit_code,
        "cancelled": False,
        "truncated": bool(info.truncated) if info else False,
    }
    full_path = getattr(info, "full_output_path", None) if info else None
    if full_path is not None:
        data["fullOutputPath"] = str(full_path)
    return RpcSuccessResponse(id=cmd.id, command="bash", data=data)


async def _handle_set_thinking_level(
    harness: AgentHarness,
    cmd: RpcCommandSetThinkingLevel,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:317-319`` (set_thinking_level handler)."""

    await harness.set_thinking_level(cmd.level)
    return RpcSuccessResponse(id=cmd.id, command="set_thinking_level")


async def _handle_set_session_name(
    harness: AgentHarness,
    cmd: RpcCommandSetSessionName,
) -> RpcResponse:
    """Pi parity: ``rpc-mode.ts:432-440`` (set_session_name handler)."""

    name = cmd.name.strip()
    if not name:
        return RpcErrorResponse(
            id=cmd.id,
            command="set_session_name",
            error="Session name cannot be empty",
        )
    if harness._session is None:
        return RpcErrorResponse(
            id=cmd.id,
            command="set_session_name",
            error="set_session_name() requires options.session to be attached",
        )
    await harness._session.append_session_name(name)
    harness._cached_session_name = name
    return RpcSuccessResponse(id=cmd.id, command="set_session_name")


# === Deferred handler factory =================================================


def _make_deferred_handler(
    cmd_type: str, owner_adr: str
) -> Callable[[Any, Any], Awaitable[RpcResponse]]:
    """Return a handler that emits an :class:`RpcErrorResponse` stub.

    Closure pin asserts every key in :data:`DEFERRED_COMMANDS` routes
    through this factory and produces ``success: false``.
    """

    async def _handler(_harness: Any, cmd: Any) -> RpcResponse:
        return RpcErrorResponse(
            id=getattr(cmd, "id", None),
            command=cmd_type,
            error=f"{cmd_type} not implemented in Sprint 6d ({owner_adr})",
        )

    return _handler


# Per-discriminator dispatch table. Constructed once at import time.
_SUPPORTED_HANDLERS: dict[
    str, Callable[[Any, Any], Awaitable[RpcResponse]]
] = {
    "prompt": _handle_prompt,
    "abort": _handle_abort,
    "new_session": _handle_new_session,
    "get_state": _handle_get_state,
    "get_messages": _handle_get_messages,
    "compact": _handle_compact,
    "bash": _handle_bash,
    "set_thinking_level": _handle_set_thinking_level,
    "set_session_name": _handle_set_session_name,
}


def build_dispatch_table() -> dict[
    str, Callable[[Any, Any], Awaitable[RpcResponse]]
]:
    """Build the full 29-command dispatch table (9 supported + 20 deferred).

    Returned fresh on every call so tests can introspect without leaking
    state. The supported handlers are real callables; the deferred ones
    are :func:`_make_deferred_handler` closures.
    """

    table: dict[str, Callable[[Any, Any], Awaitable[RpcResponse]]] = dict(
        _SUPPORTED_HANDLERS
    )
    for cmd_type, adr in DEFERRED_COMMANDS.items():
        table[cmd_type] = _make_deferred_handler(cmd_type, adr)
    return table


# === Entry point ==============================================================


async def _handle_command(
    harness: AgentHarness,
    payload: dict[str, Any],
    dispatch: dict[str, Callable[[Any, Any], Awaitable[RpcResponse]]],
) -> RpcResponse:
    """Parse + dispatch a single command payload.

    Errors during parse return an :class:`RpcErrorResponse` with
    ``command="parse"`` (Pi parity, ``rpc-mode.ts:464-470``).
    """

    try:
        cmd: RpcCommand = parse_rpc_command(payload)
    except (ValueError, TypeError) as exc:
        # P-120: Pi parity (`rpc-mode.ts:464-470`) — every parse failure
        # (unknown type, missing required field, type/value error) emits
        # ``command="parse"``. The previous behavior echoed the user's
        # claimed ``type`` which broke clients dispatching on ``parse``.
        return RpcErrorResponse(
            id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            command="parse",
            error=f"Failed to parse command: {exc}",
        )
    handler = dispatch.get(cmd.type)
    if handler is None:
        return RpcErrorResponse(
            id=cmd.id,
            command=cmd.type,
            error=f"Unknown command: {cmd.type}",
        )
    try:
        return await handler(harness, cmd)
    except Exception as exc:  # noqa: BLE001 — wire-back to client as error
        return RpcErrorResponse(
            id=cmd.id,
            command=cmd.type,
            error=str(exc),
        )


async def run_rpc_mode(
    harness: AgentHarness,
    *,
    stdin: asyncio.StreamReader | None = None,
    stdout_write: Callable[[bytes], None] | None = None,
    install_signal_handlers: bool = True,
) -> None:
    """Pi parity: ``rpc-mode.ts:runRpcMode`` (lines 50-492).

    Hijacks ``sys.stdout`` (redirects to ``sys.stderr`` so tool ``print``
    output stays out of the JSONL stream — Pi ``takeOverStdout``), attaches
    a JSONL line reader to stdin, subscribes to harness session events,
    and dispatches commands until EOF or a shutdown signal.

    Args:
        harness: Active :class:`AgentHarness` whose surface backs the 9
            supported commands.
        stdin: Optional :class:`asyncio.StreamReader`. When ``None`` the
            entry connects to ``sys.stdin.buffer`` via
            :func:`asyncio.connect_read_pipe`.
        stdout_write: Optional sink for outgoing JSONL records. When
            ``None`` writes are buffered through the real ``sys.stdout``
            file descriptor (bypassing the redirect).
        install_signal_handlers: Set False in tests to skip the
            SIGTERM/SIGHUP wiring (the test harness uses asyncio cancel
            instead).
    """

    # Pi takeOverStdout — pre-redirect the real stdout fd to stderr so
    # any stray ``print()`` from tools/extensions doesn't corrupt the
    # JSONL frame. Tests inject ``stdout_write`` to bypass.
    stdout_redirect_cm: contextlib.AbstractContextManager[Any] = (
        contextlib.redirect_stdout(sys.stderr)
    )
    if stdout_write is None:
        # Capture the real stdout descriptor BEFORE redirect, then route
        # all RPC writes through it.
        real_stdout_fd = sys.__stdout__
        if real_stdout_fd is None:
            real_stdout_fd = sys.stdout  # fallback for unusual environments

        def _write(data: bytes) -> None:
            try:
                real_stdout_fd.buffer.write(data)
                real_stdout_fd.buffer.flush()
            except (AttributeError, ValueError, OSError):
                # ``__stdout__`` may not expose ``.buffer`` in unusual
                # environments (e.g. captured by pytest); fall back to
                # the text stream.
                real_stdout_fd.write(data.decode("utf-8", errors="replace"))
                real_stdout_fd.flush()

        stdout_write = _write

    write_sink: Callable[[bytes], None] = stdout_write

    def _output(obj: Any) -> None:
        write_sink(serialize_json_line(obj).encode("utf-8"))

    # Connect stdin if not supplied.
    own_stdin = stdin is None
    if stdin is None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        stdin = reader

    dispatch = build_dispatch_table()
    shutdown_event = asyncio.Event()

    # === Event pipe ===========================================================
    # Pi parity: session.subscribe((event) => output(event)). The Aelix
    # harness exposes ``subscribe`` with the same signature.

    def _on_agent_event(event: Any) -> None:
        try:
            payload = _event_to_dict(event)
            # Best-effort JSON-serializable check.
            try:
                json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                payload = {"type": getattr(event, "type", "unknown")}
            _output(payload)
        except Exception:  # noqa: BLE001
            # Pi swallows listener errors; matches.
            pass

    unsubscribe = harness.subscribe(_on_agent_event)

    # === Signal handlers ======================================================
    # Pi parity: SIGTERM always; SIGHUP on non-Windows.

    installed_handlers: list[tuple[int, Any]] = []
    if install_signal_handlers:
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                previous = signal.getsignal(sig)
                loop.add_signal_handler(sig, shutdown_event.set)
                installed_handlers.append((sig, previous))
            except (NotImplementedError, RuntimeError, ValueError):
                # Windows / non-main-thread paths fall through silently;
                # asyncio.run() in the main thread handles SIGINT via
                # KeyboardInterrupt regardless.
                pass

    # === Stdin reader =========================================================

    pending_command_tasks: set[asyncio.Task[Any]] = set()

    def _on_line(line: str) -> None:
        if not line.strip():
            return
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            _output(
                RpcErrorResponse(
                    command="parse",
                    error=f"Failed to parse command: {exc}",
                ).to_json()
            )
            return
        if not isinstance(payload, dict):
            _output(
                RpcErrorResponse(
                    command="parse",
                    error="Command payload must be a JSON object",
                ).to_json()
            )
            return
        # Route extension UI responses to a no-op for Sprint 6d (bridge
        # deferred to Sprint 6f per ADR-0058). The wire shape is recognised
        # but not yet correlated.
        if payload.get("type") == "extension_ui_response":
            return

        async def _dispatch() -> None:
            response = await _handle_command(harness, payload, dispatch)
            _output(response.to_json())

        task = asyncio.create_task(_dispatch())
        pending_command_tasks.add(task)
        task.add_done_callback(pending_command_tasks.discard)

    reader_obj = JsonlLineReader(_on_line)
    stdin_eof = asyncio.Event()

    async def _pump_stdin() -> None:
        try:
            while True:
                chunk = await stdin.read(4096)
                if not chunk:
                    reader_obj.end()
                    stdin_eof.set()
                    return
                reader_obj.feed(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            stdin_eof.set()

    pump_task = asyncio.create_task(_pump_stdin())

    # === Main loop ============================================================
    try:
        with stdout_redirect_cm:
            await asyncio.wait(
                [
                    asyncio.create_task(shutdown_event.wait()),
                    asyncio.create_task(stdin_eof.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Drain any in-flight command dispatches before tearing down.
            if pending_command_tasks:
                await asyncio.gather(*pending_command_tasks, return_exceptions=True)
    finally:
        pump_task.cancel()
        with contextlib.suppress(BaseException):
            await pump_task
        unsubscribe()
        # Restore signal handlers.
        if install_signal_handlers:
            for sig, previous in installed_handlers:
                try:
                    loop = asyncio.get_running_loop()
                    loop.remove_signal_handler(sig)
                    if callable(previous):
                        signal.signal(sig, previous)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
        # Pi parity: ``await runtimeHost.dispose()``.
        with contextlib.suppress(Exception):
            await harness.dispose()
        if own_stdin:
            # asyncio's pipe transport is closed when the reader hits EOF;
            # nothing to actively pause on the Python side.
            pass


__all__ = [
    "DEFERRED_COMMANDS",
    "SUPPORTED_COMMANDS",
    "build_dispatch_table",
    "run_rpc_mode",
]
