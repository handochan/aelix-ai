"""``run_print_mode``'s signal handlers: installed, PUT BACK, and REPORTED (#220).

Two product changes are on trial here, and they only mean anything together
(spec ``.omc/specs/220-design-2026-09-05.md`` §A.3):

(a) The handler block is no longer switched off wholesale on Windows. Until
    #220 the whole of Pi step 1 sat under ``if sys.platform != "win32":``, so a
    ``CTRL_BREAK_EVENT`` — the only stop signal a delegation parent can aim at
    this child on Windows, and the one ``ProcessTree.soft_kill()`` sends
    (ADR-0238) — hit the CRT default and ended the process without
    ``dispose()``. The win32 arm installs a ``SIGBREAK`` handler and restores
    the previous disposition.

(b) ``_signal_cleanup_and_exit`` no longer calls ``sys.exit`` from inside a
    task; it records ``128 + sig`` and ``run_print_mode`` RETURNS it. Without
    (b), (a) is unobservable on Windows: ``TerminateJobObject(job, 1)`` and a
    crashing child both read as exit 1 (#202 handoff §3-12), and the old
    ``sys.exit`` path ALSO produced 1 — MEASURED against a real ``aelix -p``
    child (``.omc/specs/220-progress-2026-09-05.md`` §1: ``SIGTERM -> exit in
    0.172s, returncode=1``, with a ``SystemExit(143)`` traceback replaced by
    ``RuntimeError: Event loop stopped before Future completed`` and a "Task
    exception was never retrieved"). So a cooperative exit could not be told
    from a kill by its exit code at all. The same live run repeated against
    THIS tree gave ``returncode=143`` in 0.126 s (text) / 0.137 s
    (``--mode json``) with "Request aborted" as the whole of stderr — which is
    what the cases below assert in-process, in milliseconds, on every platform.

WHY THIS FILE EXISTS AT ALL. ``run_print_mode`` is a library entry point that
installs PROCESS-GLOBAL dispositions, and nothing in the suite executed the
handler block before #220 — the POSIX arm ran incidentally in every
``tests/cli/test_print_mode.py`` case but no test read a disposition, and the
win32 arm did not exist.

WHAT THIS FILE IS NOT. It is the IN-PROCESS twin of the real-child test in
``tests/agents_ext/test_print_channel_spawn.py`` (spec §D.4), which drives a
genuine ``aelix`` child through ``PrintChannel``'s spawn/attach/reaper and
asserts 143 / 149 as the parent observes it. This file can reach the code path
on every platform in milliseconds; only the child test proves the number
survives ``_async_main`` → ``main_sync`` → the interpreter.

WHY THE GATE IS BUILT HERE rather than borrowed from
``tests/rpc/test_rpc_mode_signal_handlers.py``. That test can read
``signal.getsignal`` "while the mode is still running" because ``run_rpc_mode``
blocks on a stdin ``StreamReader`` it feeds EOF to. ``run_print_mode`` has no
stdin lever and no ``install_signal_handlers`` flag, so the gate is a
``stream_fn`` that parks on an :class:`asyncio.Event` this file owns
(spec §D.3, tests/T8).

INJECTION, NOT ``skipif``. The win32 arm is reached by patching
``sys.platform`` and lending ``signal.SIGBREAK`` a signal number the host
actually has, the way ``tests/cli/test_stdio_encoding_win32.py`` establishes
for this repo. The one thing that cannot be injected is OS DELIVERY of a
signal: ``os.kill(pid, SIGTERM)`` on Windows is ``TerminateProcess`` of the
test runner, so :func:`_deliver` branches on the real platform and calls the
installed handler there instead — the test still runs on both legs, with the
lever the platform allows.
"""

from __future__ import annotations

import asyncio
import gc
import os
import signal
import sys
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.modes.print_mode import run_print_mode

#: The signal ``signal.SIGBREAK`` is lent on a host that has no such name.
#: ``SIGUSR1`` on POSIX (nothing in this process uses it); on the windows leg
#: the real ``SIGBREAK`` is already there and the lend is the identity, so the
#: arm runs against the genuine article rather than a stand-in. Same choice and
#: same reasoning as ``tests/rpc/test_rpc_mode_signal_handlers.py:59``.
_BREAK_STAND_IN = getattr(signal, "SIGUSR1", None) or getattr(signal, "SIGBREAK", None)
if _BREAK_STAND_IN is None:  # pragma: no cover — every supported host has one
    raise RuntimeError("no signal on this host can stand in for SIGBREAK")

#: A number that is NOT a signal on any host, parked in ``signal.SIGBREAK`` for
#: the length of one handler call so that forwarding the delivered number and
#: re-reading the module attribute produce different answers. Never sent.
_DECOY = 99

#: How long any wait in this file may take before it is a failure rather than a
#: hang. Every operation under test is sub-millisecond; this is purely so a
#: deleted product line reads as a legible failure in the ``-q`` log.
_DEADLINE = 10.0

#: The separate, much tighter budget for "has the handler been installed yet".
#: Step 1 runs in ``run_print_mode``'s FIRST loop step, before its first
#: ``await``, so there is nothing for this to wait on but the scheduler. It is
#: split out from :data:`_DEADLINE` so that deleting the install arm costs the
#: ``-q`` log seconds rather than a minute: MEASURED at 10 s apiece, the
#: install-arm mutation took 45.45 s to report; at 2 s it is legible.
_INSTALL_DEADLINE = 2.0


def _sentinel(*_: object) -> None:
    """A disposition no other party in this process would ever install."""


@pytest.fixture(autouse=True)
def _restore_dispositions() -> Iterator[None]:
    """Put every signal this file touches back, whatever the test did.

    ``signal.signal`` is PROCESS-GLOBAL and pytest runs the rest of the session
    in the same interpreter, so a win32 case that fails between installing a
    disposition and restoring it would otherwise leave ``SIGTERM`` — or, worse,
    the lent ``SIGUSR1`` — pointing at a dead closure for every later test
    (spec §D.3). Unconditional, not ``try/finally`` inside each case: the cases
    that assert ON the disposition must be able to fail their assertion without
    also poisoning the session.
    """

    names = [signal.SIGTERM, _BREAK_STAND_IN]
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is not None:
        names.append(sighup)
    saved = [(sig, signal.getsignal(sig)) for sig in names]
    try:
        yield
    finally:
        for sig, previous in saved:
            # ``None`` means the disposition was not set from Python and there
            # is nothing ``signal.signal`` could put back.
            if previous is not None:
                signal.signal(sig, previous)


# === the gate ================================================================


class _Gate:
    """The lever ``run_print_mode`` does not give us.

    ``started`` is set once the mock ``stream_fn`` is genuinely inside the turn
    — reading a disposition before that would race the install — and ``open``
    is what lets the turn finish. Nothing here swallows
    :class:`asyncio.CancelledError`: ``dispose()`` aborts the in-flight turn,
    and a ``stream_fn`` that ate the cancellation would park the mode forever
    (that shape is described in ``_signal_cleanup_and_exit``'s docstring — the
    code is still RECORDED, since the assignment is before the first ``await``;
    what never happens is ``run_print_mode`` returning it — and it is what the
    delegation reaper's escalation exists for).
    """

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.open = asyncio.Event()


def _gated_stream(gate: _Gate) -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        gate.started.set()
        await gate.open.wait()
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _new_runtime(gate: _Gate) -> AgentSessionRuntime:
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_gated_stream(gate),
        )
    )

    async def _noop(_s: Any) -> AgentHarness:
        return harness

    return AgentSessionRuntime(
        harness,
        _noop,
        repo=JsonlSessionRepo(fs=LocalFileSystem()),
        fs=LocalFileSystem(),
    )


def _start(gate: _Gate) -> asyncio.Task[int]:
    return asyncio.create_task(
        run_print_mode(
            _new_runtime(gate),
            mode="text",
            messages=[],
            initial_message="go",
        )
    )


async def _wait_for_install(previous: object) -> object:
    """Poll ``getsignal`` until the handler under test replaced ``previous``.

    Read WHILE the mode is still running: the ``finally`` puts the previous
    disposition back by design, so this is the only moment the install is
    visible. Returns what was found so the caller can assert on it (a returned
    ``previous`` means the install never happened).
    """

    installed: object = previous
    for _ in range(int(_INSTALL_DEADLINE / 0.01)):
        installed = signal.getsignal(_BREAK_STAND_IN)
        if installed is not previous:
            return installed
        await asyncio.sleep(0.01)
    return installed


async def _finish(task: asyncio.Task[int], gate: _Gate) -> int:
    gate.open.set()
    return await asyncio.wait_for(task, _DEADLINE)


async def _settle() -> None:
    """Let the ``_signal_cleanup_and_exit`` task finish before the loop closes.

    ``run_print_mode`` returns as soon as ``dispose()`` has aborted the turn,
    which is BEFORE that task's own ``dispose()`` returns (see the ordering
    note in ``_signal_cleanup_and_exit``'s docstring). Leaving it pending would
    trade the defect this file tests for a "Task was destroyed but it is
    pending!" on the way out.
    """

    me = asyncio.current_task()
    for _ in range(int(_DEADLINE / 0.01)):
        others = [t for t in asyncio.all_tasks() if t is not me]
        if not others:
            return
        await asyncio.wait(others, timeout=0.01)


# === (a) the win32 arm: installed, and put back ==============================


async def test_the_win32_arm_installs_a_sigbreak_handler_and_restores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows twin of the POSIX SIGTERM handler, reached by injection.

    Both halves are asserted, and the install half is what makes the restore
    half mean anything: a mutation that deleted the whole ``else`` arm would
    leave the sentinel in place the entire time and pass a restore-only test
    for free.

    NO SELF-SIGNAL HERE. Delivery is
    :func:`test_a_delivered_signal_makes_run_print_mode_return_128_plus_sig`'s
    job; raising the stand-in in this case would be a different risk on each
    leg (on the windows leg ``os.kill(pid, SIGBREAK)`` is a
    ``TerminateProcess`` of the test runner), and the block under test is
    reached whether or not anything is ever delivered to it.

    THE THIRD INJECTION — ``remove_signal_handler`` raising
    ``NotImplementedError``, which is what the proactor loop does for EVERY
    signal (only ``asyncio/unix_events.py`` implements it) — is carried over
    from ``tests/rpc/test_rpc_mode_signal_handlers.py`` and currently guards
    NOTHING, deliberately. MEASURED by reading the product: ``print_mode``'s
    removal loop and its SIGBREAK restore sit in the two arms of the SAME
    ``if sys.platform != "win32":``, so no ``remove_signal_handler`` can
    precede the restore and there is nothing for its exception to swallow
    (spec §B.3, win32/W8) — unlike ``rpc_mode``, where one loop restores both
    kinds out of a shared list and a single ``try`` around both steps was
    exactly the #202 defect. It stays because that structural difference is
    the assumption the restore rests on, and this injection is what would fail
    loudly if ``print_mode``'s cleanup were ever merged into ``rpc_mode``'s
    shape.
    """

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(signal, "SIGBREAK", _BREAK_STAND_IN, raising=False)

    loop = asyncio.get_running_loop()
    real_remove = loop.remove_signal_handler

    def _remove_signal_handler(sig: int) -> bool:
        if sig == _BREAK_STAND_IN:
            raise NotImplementedError
        return real_remove(sig)

    monkeypatch.setattr(loop, "remove_signal_handler", _remove_signal_handler)

    signal.signal(_BREAK_STAND_IN, _sentinel)
    gate = _Gate()
    task = _start(gate)
    try:
        installed = await _wait_for_install(_sentinel)
        assert installed is not _sentinel, (
            "the win32 arm never installed a SIGBREAK handler — on Windows a "
            "CTRL_BREAK_EVENT from the delegation parent would hit the CRT "
            "default and end this process without dispose()"
        )
    finally:
        await _finish(task, gate)
        await _settle()

    assert signal.getsignal(_BREAK_STAND_IN) is _sentinel, (
        "run_print_mode kept its SIGBREAK disposition after returning; "
        f"getsignal now answers {signal.getsignal(_BREAK_STAND_IN)!r}. This is "
        "a library entry point — the TUI and the delegation channel call it "
        "in-process, so a handler it forgets to put back changes what the "
        "NEXT console event does to the whole process"
    )


async def test_a_default_sigbreak_disposition_survives_the_win32_arm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SIG_DFL`` is the ordinary case, and the one a ``callable`` guard skips.

    ``signal.signal`` answers ``Handlers.SIG_DFL`` — an ``int``, not a callable
    — for a signal nobody installed a Python handler for, which is precisely
    the state a freshly spawned ``aelix --mode json`` child is in. #202 found
    the pre-existing guard was ``callable(previous)``, so the restore was
    skipped in exactly the runs that needed it; the guard here is
    ``break_previous is not None``.

    Unlike the POSIX twin below, this case is NOT green either way: on win32
    there is no ``loop.remove_signal_handler`` to reset the disposition on the
    way out (it raises ``NotImplementedError`` on the proactor loop and, in
    ``print_mode``, is not even reached), so the explicit
    ``signal.signal(SIGBREAK, break_previous)`` is the ONLY thing that puts
    ``SIG_DFL`` back. Weakening the guard to ``callable(break_previous)``
    turns this red.
    """

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(signal, "SIGBREAK", _BREAK_STAND_IN, raising=False)

    signal.signal(_BREAK_STAND_IN, signal.SIG_DFL)
    gate = _Gate()
    task = _start(gate)
    try:
        installed = await _wait_for_install(signal.SIG_DFL)
        assert installed is not signal.SIG_DFL, (
            "the win32 arm never installed a SIGBREAK handler over SIG_DFL"
        )
    finally:
        await _finish(task, gate)
        await _settle()

    assert signal.getsignal(_BREAK_STAND_IN) is signal.SIG_DFL, (
        "SIGBREAK was left with a disposition the caller never asked for: "
        f"{signal.getsignal(_BREAK_STAND_IN)!r} — the `previous is not None` "
        "guard is what makes the SIG_DFL case restore at all"
    )


# === (a) the POSIX arm =======================================================


async def test_the_posix_sigterm_disposition_survives_the_call() -> None:
    """The POSIX arm leaves ``SIGTERM`` as it found it — for the case that matters.

    THE CASE THAT MATTERS is ``SIG_DFL``: that is what a freshly spawned print
    child has, and what ``run_print_mode`` must not leave changed for whatever
    runs after it in-process (the TUI and the delegation channel both call it
    that way).

    STATED LIMIT, MEASURED, not hidden: a caller who had its OWN Python handler
    on ``SIGTERM`` does NOT get it back. ``print_mode``'s cleanup is
    ``loop.remove_signal_handler(sig)`` alone, and asyncio's removal resets the
    disposition to ``SIG_DFL`` (``asyncio/unix_events.py``) rather than to
    whatever was there before — unlike ``rpc_mode``, which records ``previous``
    and re-installs it. Measured on this box with a sentinel installed before
    the call: ``getsignal(SIGTERM)`` answered ``0`` (``SIG_DFL``) afterwards.
    Asserting that behaviour would pin a wart and block its fix, so it is
    recorded here instead; asserting the ``SIG_DFL`` case pins the state the
    product actually depends on and is green under either implementation.

    MUTATION: deleting the ``finally``'s ``loop.remove_signal_handler`` loop
    turns this red — the disposition is then asyncio's ``_sighandler_noop``,
    left pointing into a dead loop.

    ON THE WINDOWS LEG this case runs and passes trivially: the POSIX arm is
    not taken, ``add_signal_handler`` is never called, and the disposition is
    untouched. That is honest coverage of the platform's behaviour, not a claim
    that the windows leg exercises the removal.
    """

    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    gate = _Gate()
    gate.open.set()
    exit_code = await asyncio.wait_for(_start(gate), _DEADLINE)
    await _settle()

    assert exit_code == 0
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL, (
        "run_print_mode left SIGTERM pointing at "
        f"{signal.getsignal(signal.SIGTERM)!r} — on POSIX that is asyncio's "
        "_sighandler_noop bound to a loop that no longer exists, so the next "
        "SIGTERM in this process is delivered into nothing"
    )


# === (b) the exit code the parent can read ===================================


def _record_loop_exceptions() -> list[dict[str, Any]]:
    """Capture what asyncio would otherwise print, so it can be ASSERTED on.

    ``Task exception was never retrieved`` reaches the default handler from
    ``Task.__del__``, i.e. at collection time — which is why the callers
    ``gc.collect()`` before reading this list.
    """

    seen: list[dict[str, Any]] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: seen.append(context))
    return seen


def _assert_no_signal_noise(seen: list[dict[str, Any]]) -> None:
    gc.collect()
    messages = [str(context.get("message", "")) for context in seen]
    exceptions = [repr(context.get("exception")) for context in seen]
    assert not any("never retrieved" in message for message in messages), (
        "asyncio reported an unretrieved task exception — this is the #220 "
        f"shape returning: {messages} / {exceptions}"
    )
    assert not any("SystemExit" in text for text in exceptions), (
        "a SystemExit escaped into a task again; _signal_cleanup_and_exit must "
        f"RECORD 128+sig, never raise it: {exceptions}"
    )


def _arm() -> int:
    """Put ``_sentinel`` on the signal this platform's arm will take over.

    Called BEFORE the mode starts, for two reasons. It is the readiness lever
    — "the disposition is no longer the sentinel" is how :func:`_deliver` knows
    the install happened — and on POSIX it is a safety belt: if the product
    installed nothing, ``_sentinel`` absorbs the ``SIGTERM`` and the test fails
    instead of the default action taking the whole pytest session with it.
    Installing it AFTER the mode started would instead CLOBBER asyncio's
    ``_sighandler_noop`` and break delivery (measured while writing this file:
    the poll then never saw an install).
    """

    sig = signal.SIGBREAK if sys.platform == "win32" else signal.SIGTERM
    signal.signal(sig, _sentinel)
    return int(sig)


async def _deliver(sig: int) -> int:
    """Deliver ``sig`` to this process and return the number delivered.

    Branching on the REAL platform, because delivery is the one thing
    injection cannot fake. On POSIX the OS carries a genuine ``SIGTERM``
    through ``loop.add_signal_handler``'s wakeup fd, which is exactly what the
    delegation reaper's leg 1 sends. On Windows there is no equivalent lever —
    ``os.kill(pid, SIGTERM)`` there is ``TerminateProcess`` of the test runner,
    and a real ``CTRL_BREAK_EVENT`` would hit the whole console group — so the
    installed handler is invoked directly, which still runs the product's
    ``loop.call_soon_threadsafe`` → ``_handle_signal`` chain.
    """

    handler: Any = _sentinel
    for _ in range(int(_INSTALL_DEADLINE / 0.01)):
        handler = signal.getsignal(sig)
        if handler is not _sentinel:
            break
        await asyncio.sleep(0.01)
    assert handler is not _sentinel, (
        f"run_print_mode never installed a handler for signal {sig}; refusing "
        "to raise one, because on POSIX the default action would take the test "
        "session with it"
    )
    if sys.platform == "win32":
        assert callable(handler), f"nothing to deliver to: {handler!r}"
        handler(sig, None)
        return sig
    os.kill(os.getpid(), sig)
    return sig


async def test_a_delivered_signal_makes_run_print_mode_return_128_plus_sig() -> None:
    """The whole point of §A.3(b): the code comes back, it does not exit.

    THIS TEST FAILS ON ``main``. There ``_signal_cleanup_and_exit`` calls
    ``sys.exit(128 + sig)`` inside an ``ensure_future``d task; ``SystemExit``
    is re-raised by ``Task.__step`` out through the loop, so ``run_print_mode``
    never returns at all — the loop stops mid-step. Against a real child that
    read as ``returncode=1`` plus two tracebacks
    (``.omc/specs/220-progress-2026-09-05.md`` §1), which is the SAME number
    ``TerminateJobObject(job, 1)`` produces, so "we asked it to stop" and "we
    killed it" were indistinguishable.

    THE OVERRIDE IS PART OF THE SUBJECT. Step 7 sets ``exit_code = 1`` for the
    ``aborted`` stop_reason, and ``dispose()`` is what aborts the turn, so
    every signal-driven run passes through that 1. Deleting the
    ``if signal_code is not None:`` override in ``run_print_mode`` leaves this
    asserting ``1 == 143``.

    143 ON POSIX, 149 ON THE WINDOWS LEG — computed from the number actually
    delivered rather than hard-coded, because :func:`_deliver` uses the lever
    each platform allows and ``SIGBREAK`` is 21.
    """

    seen = _record_loop_exceptions()
    armed = _arm()
    gate = _Gate()
    task = _start(gate)
    await asyncio.wait_for(gate.started.wait(), _DEADLINE)

    delivered = await _deliver(armed)
    exit_code = await asyncio.wait_for(task, _DEADLINE)
    await _settle()

    assert exit_code == 128 + delivered, (
        f"run_print_mode returned {exit_code}, not {128 + delivered}. 1 is the "
        "tell that the signal path was swallowed by step 7's aborted-stop_reason "
        "exit code; anything else means the code never reached the return"
    )
    _assert_no_signal_noise(seen)


async def test_the_injected_win32_sigbreak_path_reports_128_plus_sig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The win32 exit-code path, run on every leg.

    :func:`test_a_delivered_signal_makes_run_print_mode_return_128_plus_sig`
    exercises the win32 arm only ON Windows. This case injects the platform so
    the ``SIGBREAK`` → ``call_soon_threadsafe`` → ``_handle_signal`` →
    ``_signal_cleanup_and_exit`` chain — the one that has to produce 149 on the
    windows CI leg — is executed by the Linux and Darwin legs too, where it
    produces ``128 + SIGUSR1``.

    Calling the installed handler is not a shortcut around the OS: the handler
    ``signal.signal`` holds IS the object CPython would invoke on delivery, and
    everything downstream of it is the product's.
    """

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(signal, "SIGBREAK", _BREAK_STAND_IN, raising=False)

    seen = _record_loop_exceptions()
    signal.signal(_BREAK_STAND_IN, _sentinel)
    gate = _Gate()
    task = _start(gate)
    await asyncio.wait_for(gate.started.wait(), _DEADLINE)

    handler = await _wait_for_install(_sentinel)
    assert callable(handler), f"nothing to deliver to — the win32 arm installed {handler!r}"
    # THE LEND MOVES OUT FROM UNDER THE HANDLER FOR THE DURATION OF THE CALL.
    # With ``signal.SIGBREAK`` still equal to the number being delivered, a
    # handler that forwarded its ``sig`` argument and one that re-read
    # ``signal.SIGBREAK`` at delivery time are INDISTINGUISHABLE, and the
    # product comment claiming the forwarding matters (``print_mode.py``, the
    # win32 install) is pinned by nothing — measured: swapping the lambda to
    # re-read the module attribute left the whole suite green (#220 review round
    # 2, MUT-4). ``_DECOY`` is not a signal number anyone sends; it is only ever
    # read, and it is put back on the very next line so ``run_print_mode``'s
    # ``finally`` restores the disposition of the signal it actually installed
    # on. The window between the two is synchronous.
    monkeypatch.setattr(signal, "SIGBREAK", _DECOY, raising=False)
    handler(_BREAK_STAND_IN, None)
    monkeypatch.setattr(signal, "SIGBREAK", _BREAK_STAND_IN, raising=False)

    exit_code = await asyncio.wait_for(task, _DEADLINE)
    await _settle()

    assert exit_code == 128 + int(_BREAK_STAND_IN), (
        f"the win32 signal path returned {exit_code}, not "
        f"{128 + int(_BREAK_STAND_IN)} (149 on the windows leg, where the lend "
        "is the identity and SIGBREAK is 21). The lambda forwards the number it "
        f"was GIVEN precisely so a lent signal reports its own code; "
        f"{128 + _DECOY} here would mean it re-read signal.SIGBREAK instead"
    )
    _assert_no_signal_noise(seen)
