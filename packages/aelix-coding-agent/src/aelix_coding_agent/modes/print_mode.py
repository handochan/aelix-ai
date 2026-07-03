"""Pi parity: ``modes/print-mode.ts`` (158 LOC).

Sprint 6h₆ (Phase 5a-ii, ADR-0089, P-389). Lifecycle mirrors Pi:

1. ``registerSignalHandlers`` (SIGTERM / SIGHUP — non-Windows).
2. ``setRebindSession`` so JSON-mode subscribe survives session swaps.
3. JSON-mode header emit (session metadata).
4. Initial rebind (binds subscribe to the live harness).
5. Initial-message prompt (if non-:data:`None`).
6. Residual-messages loop (``parsed.messages`` after :func:`build_initial_message`'s ``.shift()``).
7. Text-mode terminal printout (only TextContent blocks of the last
   assistant message; if ``stop_reason`` is ``"error"`` / ``"aborted"``
   emit the error message to stderr and return exit-code 1).
8. Cleanup (unsubscribe + signal handler removal + harness dispose).

JSON output is line-delimited JSON, one event per line. Header (when
present) is emitted first; subsequent lines are session events from
:meth:`AgentHarness.subscribe`.

Pi citation: ``modes/print-mode.ts:1-158`` at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.

**Out-of-scope** (deferred): ``takeOverStdout`` (Pi redirects stdout so
tool ``console.log`` cannot corrupt the JSON stream) — Aelix builtins
emit through harness events, not raw stdout, so the JSONL stream
stays clean without the redirect (ADR-0089 §"Carry-forward").
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import signal
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from aelix_ai.messages import AssistantMessage, TextContent

if TYPE_CHECKING:
    from aelix_agent_core.runtime.agent_session_runtime import (
        AgentSessionRuntime,
    )


def _write_raw_stdout(text: str) -> None:
    """Pi parity: ``writeRawStdout``.

    Writes verbatim to :attr:`sys.stdout` and flushes immediately so
    JSON events arrive line-by-line to the consumer.
    """

    sys.stdout.write(text)
    sys.stdout.flush()


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Convert a harness event to a Pi-shape dict for JSON emit.

    Reuses the Sprint 6d :mod:`rpc.rpc_mode` serializer so the wire
    shape stays consistent across ``rpc`` and ``json`` modes.
    """

    from aelix_coding_agent.rpc.rpc_mode import _dataclass_to_dict

    return _dataclass_to_dict(event)


def _metadata_to_dict(metadata: Any) -> dict[str, Any]:
    """Convert session metadata to a dict for the JSON header emit.

    Pi emits the storage's metadata directly (TS plain object). Aelix
    metadata is a dataclass — :func:`dataclasses.asdict` produces a
    Pi-shape dict.
    """

    if metadata is None:
        return {}
    if dataclasses.is_dataclass(metadata) and not isinstance(metadata, type):
        return dataclasses.asdict(metadata)
    if isinstance(metadata, dict):
        return dict(metadata)
    return {"metadata": repr(metadata)}


async def _safe_dispose(runtime_host: AgentSessionRuntime) -> None:
    """Dispose helper — swallows errors so cleanup never raises.

    Pi parity: ``modes/print-mode.ts`` ``finally`` clause never throws.
    """

    with contextlib.suppress(Exception):
        await runtime_host.dispose()


async def run_print_mode(
    runtime_host: AgentSessionRuntime,
    *,
    mode: Literal["text", "json"],
    messages: list[str],
    initial_message: str | None,
    initial_images: list[Any] | None = None,
) -> int:
    """Pi parity: ``runPrintMode`` (``modes/print-mode.ts``).

    Returns the process exit code (0 on success; 1 on assistant
    ``stop_reason`` of ``"error"`` / ``"aborted"`` OR any exception).
    """

    loop = asyncio.get_running_loop()

    # === Pi step 1 — signal handlers (non-Windows) ============================
    signals_installed: list[int] = []
    if sys.platform != "win32":
        def _handle_signal(sig: int) -> None:
            # Schedule async cleanup + sys.exit(128+sig) on the loop.
            asyncio.ensure_future(_signal_cleanup_and_exit(runtime_host, sig))

        for sig_name in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, _handle_signal, sig)
                signals_installed.append(sig)
            except (NotImplementedError, RuntimeError):
                # Some loops (e.g., uvloop in subprocess test harnesses)
                # may reject signal installation; treat as best-effort.
                pass

    # === Pi steps 2-3 — rebind closure + subscribe ============================
    unsubscribe_holder: dict[str, Callable[[], None] | None] = {"u": None}
    # Issue #57: set by the JSON event emitter when the stdout consumer
    # vanishes (BrokenPipeError). Subscribers must not raise — harness event
    # dispatch swallows listener errors (pi parity) — so the emitter records
    # the death here and the main coroutine surfaces it after the prompt
    # loop, exiting 141 via main_sync's guard instead of running to a
    # useless exit 0 while every event write fails.
    stdout_dead: dict[str, bool] = {"v": False}

    async def _rebind(new_harness: Any, reason: str = "resume") -> None:
        # Issue #24 — ``reason`` (``new``/``resume``/``fork``/``reload``) is part
        # of the widened rebind-callback contract; headless print/json mode only
        # re-subscribes the event emitter, identical for every reason.
        # Drop any prior subscription bound to the OLD harness.
        prior = unsubscribe_holder["u"]
        if prior is not None:
            with contextlib.suppress(Exception):
                prior()
            unsubscribe_holder["u"] = None

        # In JSON mode, attach the line-delimited event emitter to the
        # NEW harness so events keep flowing across session swaps.
        if mode == "json":
            def _emit(event: Any) -> None:
                if stdout_dead["v"]:
                    return  # consumer gone — stop writing to a dead pipe
                try:
                    _write_raw_stdout(json.dumps(_event_to_dict(event)) + "\n")
                except BrokenPipeError:
                    stdout_dead["v"] = True  # Issue #57 — surfaced post-loop
                except Exception:  # noqa: BLE001 — pi swallows listener errors
                    pass

            unsubscribe_holder["u"] = new_harness.subscribe(_emit)

    runtime_host.set_rebind_session(_rebind)

    exit_code = 0
    try:
        # === Pi step 4 — JSON-mode header emit ================================
        if mode == "json":
            session = runtime_host.harness.session
            if session is not None:
                try:
                    metadata = await session.get_metadata()
                    header = _metadata_to_dict(metadata)
                    if header:
                        _write_raw_stdout(json.dumps(header) + "\n")
                except BrokenPipeError:
                    raise  # Issue #57 — dead consumer must reach main_sync
                except Exception:  # noqa: BLE001 — header emit is best-effort
                    pass

        # === Pi step 5 — initial rebind ======================================
        await _rebind(runtime_host.harness)

        # === Pi step 6 — initial message =====================================
        if initial_message is not None:
            await runtime_host.harness.prompt(
                initial_message,
                images=initial_images,
            )

        # === Pi step 7 — residual messages loop ==============================
        for message in messages:
            if stdout_dead["v"]:
                # Issue #57 (review MEDIUM): the consumer died during an
                # earlier turn — don't burn full agent turns (LLM calls,
                # tools) emitting into a dead pipe; the post-loop raise
                # surfaces the EPIPE now.
                break
            await runtime_host.harness.prompt(message)

        # Issue #57: a consumer that vanished mid-run (JSON mode) was recorded
        # by ``_emit``; surface it now so the process exits 141 instead of 0.
        if stdout_dead["v"]:
            raise BrokenPipeError(
                "stdout consumer went away during JSON event emit"
            )

        # === Pi step 8 — text-mode terminal printout =========================
        if mode == "text":
            state_messages = list(runtime_host.harness.state.messages)
            if state_messages:
                last = state_messages[-1]
                if isinstance(last, AssistantMessage):
                    stop_reason = last.stop_reason
                    if stop_reason in ("error", "aborted"):
                        error_message = (
                            last.error_message or f"Request {stop_reason}"
                        )
                        print(error_message, file=sys.stderr)
                        exit_code = 1
                    else:
                        for block in last.content:
                            if isinstance(block, TextContent):
                                _write_raw_stdout(f"{block.text}\n")

    except BrokenPipeError:
        # Issue #57: stdout consumer vanished — propagate to main_sync's
        # top-level guard (quiet exit 141). The old broad catch masked this
        # as exit 1 with a dirty buffer, which then crashed the interpreter's
        # shutdown flush ("Exception ignored in ... BrokenPipeError", exit 120).
        raise
    except Exception as exc:  # noqa: BLE001 — surface any failure to caller
        print(str(exc), file=sys.stderr)
        exit_code = 1

    finally:
        # === Pi step 9 — cleanup =============================================
        unsub = unsubscribe_holder["u"]
        if unsub is not None:
            with contextlib.suppress(Exception):
                unsub()
        if sys.platform != "win32":
            for sig in signals_installed:
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)
        await _safe_dispose(runtime_host)
        with contextlib.suppress(Exception):
            sys.stdout.flush()

    return exit_code


async def _signal_cleanup_and_exit(
    runtime_host: AgentSessionRuntime, sig: int
) -> None:
    """Pi parity: signal handler cleanup path.

    Disposes the runtime then exits with code ``128 + sig`` (POSIX
    convention).
    """

    await _safe_dispose(runtime_host)
    sys.exit(128 + sig)


__all__ = ["run_print_mode"]
