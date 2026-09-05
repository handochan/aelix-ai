"""Pi parity: ``modes/print-mode.ts`` (158 LOC).

Sprint 6h₆ (Phase 5a-ii, ADR-0089, P-389). Lifecycle mirrors Pi:

1. ``registerSignalHandlers`` (SIGTERM / SIGHUP on POSIX, SIGBREAK on
   Windows — #220).
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

**Step 1 diverges from Pi, deliberately (ADR-0235, #220).** Pi registers
``SIGTERM`` on EVERY platform and gates only ``SIGHUP`` off Windows
(``print-mode.ts:50-66``, ``origin/main`` 47236c8:
``const signals = ["SIGTERM"]; if (process.platform !== "win32")
signals.push("SIGHUP")``), and Pi has no ``SIGBREAK`` anywhere
(``git grep -n SIGBREAK origin/main`` → no matches). Aelix does neither.
A win32 ``SIGTERM`` handler would be dead code: the only way a parent can
raise it there is ``os.kill(pid, SIGTERM)``, which CPython implements as
``TerminateProcess`` — the process is gone before any handler runs. The
real Windows equivalent is ``CTRL_BREAK_EVENT``, delivered as ``SIGBREAK``,
which is what ``ProcessTree.soft_kill()`` sends (ADR-0238) — so that is the
twin installed below. Until #220 this whole step was switched off on
Windows, which is what the line-1 summary used to say ("non-Windows").

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
    ``stop_reason`` of ``"error"`` / ``"aborted"`` OR any exception;
    ``128 + sig`` — 143 for SIGTERM, 149 for SIGBREAK — when a signal
    handler installed by step 1 ran, which OVERRIDES the 1, #220).
    """

    loop = asyncio.get_running_loop()

    # === Pi step 1 — signal handlers (POSIX: SIGTERM/SIGHUP; win32: SIGBREAK) ==
    signals_installed: list[int] = []
    # These two are bound BEFORE the platform branch, not inside the win32 arm.
    # The ``finally`` below reads them on the win32 side, and that side is also
    # reached when the arm's ``signal.signal`` raised — a name bound only where
    # the install succeeded is a ``NameError`` at cleanup (win32/W8, posix/P8).
    break_previous: Any = None
    break_installed = False
    # Where the signal path leaves its exit code for :func:`run_print_mode` to
    # return. A dict rather than a ``nonlocal`` because the handler coroutine is
    # a module-level function (it is easier to test that way) — see
    # :func:`_signal_cleanup_and_exit` for why it may not call ``sys.exit``.
    signal_exit: dict[str, int | None] = {"code": None}

    def _handle_signal(sig: int) -> None:
        # Shared by both arms since #220: the win32 arm needs exactly the same
        # cleanup, so keeping this inside the POSIX branch would have meant
        # duplicating it.
        asyncio.ensure_future(_signal_cleanup_and_exit(runtime_host, sig, signal_exit))

    if sys.platform != "win32":
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
    else:
        # The Windows twin (#220, ADR-0238). ``CTRL_BREAK_EVENT`` is the one
        # console event the delegation parent can aim at this child's process
        # group without also terminating itself, and it is what
        # ``ProcessTree.soft_kill()`` sends; CPython delivers it as
        # ``SIGBREAK`` on the main thread. Without a handler here it hits the
        # CRT default and the process ends without ``dispose()`` — the session
        # file is never flushed.
        #
        # ``signal.signal``, not ``loop.add_signal_handler``: the proactor loop
        # raises ``NotImplementedError`` from the latter for every signal
        # (only ``asyncio/unix_events.py`` implements it).
        #
        # The lambda takes ``(sig, frame)`` and forwards the number it was
        # GIVEN rather than re-reading ``signal.SIGBREAK`` at delivery time, so
        # a test that lends ``SIGBREAK`` a stand-in number
        # (``tests/cli/test_print_mode_signal_handlers.py``) gets that number
        # through to ``128 + sig`` instead of the real one (tests/T7). That test
        # MOVES the lent number out from under the handler before delivering,
        # which is what makes the difference observable — with the lend still in
        # place the two spellings are indistinguishable and the mutant survives
        # (#220 review round 2, MUT-4).
        try:
            break_previous = signal.signal(
                signal.SIGBREAK,
                lambda sig, _frame: loop.call_soon_threadsafe(_handle_signal, sig),
            )
            break_installed = True
        except ValueError:
            # Not the main thread. Nothing else can install one either, so the
            # delegation parent's grace elapses into its hard kill.
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
        else:
            # ONE suppressed step, not the two ``rpc_mode`` needs: that function
            # restores POSIX handlers and ``SIGBREAK`` out of one shared
            # ``installed_handlers`` list, so a ``remove_signal_handler`` that
            # raises ``NotImplementedError`` sat between the loop and the
            # restore (#202). Here the removal loop above is already fenced off
            # by ``sys.platform != "win32"``, so nothing can precede this
            # (win32/W8).
            #
            # ``break_previous is not None``, not ``callable(break_previous)``:
            # ``signal.signal`` answers ``SIG_DFL`` — an ``int`` — for a signal
            # nobody had installed a Python handler for, which is the ordinary
            # case, and that is exactly what the old guard skipped in #202.
            # ``None`` is the one answer meaning "not set from Python", i.e.
            # nothing to put back.
            if break_installed and break_previous is not None:
                with contextlib.suppress(ValueError, RuntimeError, TypeError):
                    signal.signal(signal.SIGBREAK, break_previous)
        await _safe_dispose(runtime_host)
        with contextlib.suppress(Exception):
            sys.stdout.flush()

    signal_code = signal_exit["code"]
    if signal_code is not None:
        # A signal reached us and ``_signal_cleanup_and_exit`` recorded
        # ``128 + sig``. This OVERRIDE is required, not cosmetic: step 7 above
        # already set ``exit_code = 1`` for an ``aborted`` stop_reason, which is
        # what a cancelled turn produces (MEASURED,
        # ``.omc/specs/220-progress-2026-09-05.md`` §1: the real child printed
        # "Request aborted" on the SIGTERM path), so without it the delegation
        # parent cannot tell "we killed it" from "the model errored".
        exit_code = signal_code

    return exit_code


async def _signal_cleanup_and_exit(
    runtime_host: AgentSessionRuntime, sig: int, holder: dict[str, int | None]
) -> None:
    """RECORD ``128 + sig``, then dispose — never ``sys.exit`` from a task.

    MEASURED (``.omc/specs/220-progress-2026-09-05.md`` §1, two live runs of a
    real ``aelix -p`` child, both ``text`` and ``json`` modes): the old
    ``sys.exit(128 + sig)`` here raised ``SystemExit`` inside an
    ``ensure_future``d task, which escapes the loop mid-step;
    ``asyncio.run``'s ``Runner.close()`` then raised
    ``RuntimeError: Event loop stopped before Future completed`` and REPLACED
    it, so the child exited **1** with two tracebacks and a "Task exception was
    never retrieved" — not 143. Pi's ``process.exit(143)``
    (``print-mode.ts:60``, ``origin/main`` 47236c8) has no event loop to tear
    down, so the literal port was wrong in Python.

    Recording the code lets :func:`run_print_mode` return it through
    ``_async_main`` → ``main_sync``, which is the only way a Windows caller can
    tell a cooperative ``SIGBREAK`` exit (149) from ``TerminateJobObject(job,
    1)`` — both read as exit 1 today (#202 handoff §3-12).

    A child whose work ``dispose()`` cannot unblock never returns and so never
    reports the code. The code itself IS recorded in that case — the assignment
    below is before the first ``await`` — but ``run_print_mode`` is what returns
    it, and a turn ``dispose()`` cannot abort keeps it parked: a ``stream_fn``
    that swallows ``CancelledError`` never exits on SIGTERM at all, because
    ``await _safe_dispose`` never returns. (Reasoned from this function's
    control flow, and pinned by
    ``tests/cli/test_print_mode_signal_handlers.py``'s gated ``stream_fn``; the
    pre-implementation refutation round's probe for it was not archived.) That
    is what the delegation reaper's grace and escalation are for, on both
    platforms, and #220 does not change it.

    RECORD FIRST, DISPOSE SECOND — this is a deliberate departure from the
    draft's ``dispose(); holder["code"] = …`` order, and it is the only
    ordering that works. MEASURED on this box, by an in-process SIGTERM to a
    gated ``run_print_mode``; the in-tree twin of that probe is
    ``tests/cli/test_print_mode_signal_handlers.py::
    test_a_delivered_signal_makes_run_print_mode_return_128_plus_sig``, which
    goes red under the draft's order. With the assignment after the ``await``,
    ``run_print_mode`` returned **1**, not 143. ``dispose()`` is what ABORTS
    the in-flight turn, so the moment it starts, ``run_print_mode`` unparks
    from ``harness.prompt(...)``, runs step 7 (which sets ``exit_code = 1`` for
    the ``aborted`` stop_reason), runs its ``finally`` and returns — all while
    this coroutine is still inside ``dispose``. Assigning before the first
    ``await`` closes that window deterministically: ``ensure_future`` runs this
    body up to its first suspension point in one loop step, and the only thing
    that can unpark ``run_print_mode`` is the dispose that follows it.

    RESULT, MEASURED after this change (#220 §H.6(B), darwin 25.4.0, a real
    ``aelix -p`` child against ``openrouter anthropic/claude-haiku-4.5``
    running ``sleep 120`` through the bash tool, SIGTERM to the ROOT only —
    the delegation reaper's leg 1): ``returncode=143`` in 0.126 s in text mode
    and 0.137 s in ``--mode json``, the bash-tool grandchild gone within 1 s,
    and stderr carrying "Request aborted" and nothing else — no ``Traceback``,
    no ``SystemExit``, no "Task exception was never retrieved". 149 on Windows
    is still **unmeasured**; the windows CI leg is where it gets a number.
    """

    holder["code"] = 128 + sig
    await _safe_dispose(runtime_host)


__all__ = ["run_print_mode"]
