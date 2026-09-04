"""``run_rpc_mode``'s signal handlers: installed, and PUT BACK (#202).

This is the child half of #202's shutdown story and it had no test at all on
any platform. Every in-process ``run_rpc_mode(...)`` in the suite passes
``install_signal_handlers=False``, so neither the win32 ``SIGBREAK`` install nor
the rewritten restore loop was ever executed by a test; reverting the restore to
its pre-#202 shape left 319/319 green (review win-leg/F5, MUT-2). The only
out-of-process coverage — ``tests/agents/test_rpc_sprint_pins.py``'s real
``--mode rpc`` child — proves DELIVERY and says nothing about restoration.

WHY RESTORATION IS WORTH A TEST. ``run_rpc_mode`` is a library entry point: the
TUI and the delegation channel both call it in-process, so a handler it forgets
to put back outlives the call and changes what the NEXT ``SIGTERM`` does to the
whole process. The pre-#202 guard was ``callable(previous)``, and
``signal.getsignal`` answers ``SIG_DFL`` — an ``int``, not a callable — for
every signal nobody had installed a Python handler for, which is the ordinary
case. So the restore was skipped in exactly the runs that needed it.

WHAT EACH ARM IS WORTH, PER LEG, stated rather than implied:

- The ``SIGTERM`` arms run for real on POSIX, where ``loop.add_signal_handler``
  succeeds and the ``finally`` has something to restore. On win32
  ``add_signal_handler`` raises ``NotImplementedError``, so nothing is recorded
  and nothing is restored — the disposition is untouched and the assertions hold
  trivially there. That is honest coverage of the platform's behaviour, not a
  claim that the windows leg exercises the restore.
- The ``SIGBREAK`` arm is the win32 one, reached by INJECTION rather than a
  ``skipif``: ``sys.platform`` is patched and ``signal.SIGBREAK`` is lent a
  signal number the host actually has. The POSIX ``SIGTERM``/``SIGHUP`` loop
  still runs underneath the patched platform, which is fine — the subject is the
  block below it.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode

#: The signal ``signal.SIGBREAK`` is lent on a host that has no such name.
#: ``SIGUSR1`` on POSIX (nothing in this process uses it); on the windows leg
#: the real ``SIGBREAK`` is already there and the lend is the identity, so the
#: arm runs against the genuine article rather than a stand-in.
_BREAK_STAND_IN = getattr(signal, "SIGUSR1", None) or getattr(signal, "SIGBREAK", None)
if _BREAK_STAND_IN is None:  # pragma: no cover — every supported host has one
    raise RuntimeError("no signal on this host can stand in for SIGBREAK")


def _quiet_stream_fn() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _build_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_quiet_stream_fn(),
        )
    )


def _sentinel(*_: object) -> None:
    """A disposition no other party in this process would ever install."""


async def _run_to_stdin_eof() -> None:
    """Drive ``run_rpc_mode`` with signal handlers ON, to an immediate EOF.

    EOF is one of the two things the main loop races (the other is the shutdown
    event), so an already-closed stdin makes the call return as soon as the
    handlers are installed — which is all these cases need.
    """

    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    await run_rpc_mode(
        _build_harness(),
        stdin=stdin,
        stdout_write=lambda _data: None,
        install_signal_handlers=True,
    )


async def test_a_python_sigterm_handler_survives_the_call() -> None:
    """The handler the caller had is the handler the caller gets back."""

    previous = signal.signal(signal.SIGTERM, _sentinel)
    try:
        await _run_to_stdin_eof()
        assert signal.getsignal(signal.SIGTERM) is _sentinel, (
            "run_rpc_mode kept its own SIGTERM disposition after returning; "
            f"getsignal now answers {signal.getsignal(signal.SIGTERM)!r}"
        )
    finally:
        signal.signal(signal.SIGTERM, previous)


async def test_a_default_sigterm_disposition_survives_the_call() -> None:
    """``SIG_DFL`` is the ordinary case, and the one the old guard skipped.

    ``signal.getsignal`` answers ``Handlers.SIG_DFL`` — an ``int`` — for a
    signal nobody installed a Python handler for, so ``callable(previous)`` was
    ``False`` and the restore never ran. The corrected guard is
    ``previous is not None``.

    On POSIX the OBSERVABLE outcome is the same either way, because
    ``loop.remove_signal_handler`` already resets the disposition to ``SIG_DFL``
    before the restore is even attempted. So this case pins the resulting STATE,
    not the mechanism; the mechanism it protects is the win32 path, where
    ``remove_signal_handler`` raises before doing anything and the explicit
    ``signal.signal(sig, previous)`` is the only thing that puts the
    disposition back.
    """

    previous = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        await _run_to_stdin_eof()
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL, (
            "SIGTERM was left with a disposition the caller never asked for: "
            f"{signal.getsignal(signal.SIGTERM)!r}"
        )
    finally:
        signal.signal(signal.SIGTERM, previous)


async def test_the_win32_arm_installs_a_sigbreak_handler_and_restores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows twin of the SIGTERM handler, reached by injection.

    ``RpcClient.stop()`` sends ``CTRL_BREAK_EVENT`` on win32 because
    ``terminate()`` there is an uncatchable ``TerminateProcess``; CPython
    delivers that event as ``SIGBREAK``, and this block is what turns it into a
    graceful shutdown instead of the OS default handler ending the process.
    Without it #202's whole cooperative claim on Windows is prose.

    Both halves are asserted, and the install half is what makes the restore
    half mean anything: a mutation that deleted the block entirely would leave
    the sentinel in place the whole time and pass a restore-only test for free.

    NO SELF-SIGNAL. Delivery is the sprint-pin test's job, against a real child.
    Raising the stand-in here would be a different risk on each leg (on win32
    ``os.kill(pid, SIGBREAK)`` is a ``TerminateProcess`` of the test runner),
    and the block under test is reached whether or not anything is ever
    delivered to it.

    THE THIRD INJECTION is ``remove_signal_handler`` raising
    ``NotImplementedError``, which is what the proactor loop does for EVERY
    signal (only ``asyncio/unix_events.py`` implements it; the base in
    ``asyncio/events.py`` raises). Without it this case cannot see the defect
    #202 fixed: the restore used to be ONE ``try`` around both steps, so the
    removal's exception swallowed the restore with it — and on POSIX that is
    invisible, because ``remove_signal_handler`` succeeds AND resets the
    disposition to ``SIG_DFL`` on its way. It is injected for the stand-in
    signal ONLY, so the loop's real ``SIGTERM``/``SIGHUP`` bookkeeping is left
    exactly as asyncio expects to find it.
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

    previous = signal.signal(_BREAK_STAND_IN, _sentinel)
    stdin = asyncio.StreamReader()
    task = asyncio.create_task(
        run_rpc_mode(
            _build_harness(),
            stdin=stdin,
            stdout_write=lambda _data: None,
            install_signal_handlers=True,
        )
    )
    try:
        installed: object = _sentinel
        for _ in range(500):
            installed = signal.getsignal(_BREAK_STAND_IN)
            if installed is not _sentinel:
                break
            await asyncio.sleep(0.01)
        # Read while the mode is still running: after it returns the handler is
        # gone again by design, so this is the only moment the install is
        # visible.
        assert installed is not _sentinel, (
            "the win32 arm never installed a SIGBREAK handler — the child half "
            "of #202's cooperative Windows shutdown is not wired"
        )
    finally:
        stdin.feed_eof()
        await asyncio.wait_for(task, timeout=10.0)

    try:
        assert signal.getsignal(_BREAK_STAND_IN) is _sentinel, (
            "run_rpc_mode kept its SIGBREAK disposition after returning; "
            f"getsignal now answers {signal.getsignal(_BREAK_STAND_IN)!r}"
        )
    finally:
        signal.signal(_BREAK_STAND_IN, previous)
