"""``Process.wait()`` resolving at the child's EXIT, on any interpreter (#192).

CPython gh-119710 (shipped in 3.13.15 and 3.14.7; 3.14.5 and 3.14.6 do not have
it) changed what ``asyncio.subprocess.Process.wait()`` waits for. Up to 3.13.14
``BaseSubprocessTransport`` woke ``wait()`` only once every pipe to the child was
disconnected - so a grandchild holding the child's stdout kept it pending. From
3.13.15 and 3.14.7 ``_process_exited`` wakes it as soon as the child itself
exits.

Two teardown ladders read "``wait()`` resolved inside the grace" as "the tree is
gone" and skipped their hard rung, which leaked the tree on 3.13.15+ (review
round 5: CI's ubuntu py3.13 leg, CPython 3.13.16). Developer machines and the
3.11/3.12 legs still run the OLD semantics, so a case that pins the fix must be
able to ask for the new one wherever it runs: :func:`resolve_wait_at_exit` adds
exactly gh-119710's wake-up to ``_process_exited`` for one test. On an
interpreter that already has it, the wrapper finds ``_exit_waiters`` already
``None`` and does nothing.
"""

from __future__ import annotations

from asyncio import base_subprocess
from typing import Any

import pytest


def resolve_wait_at_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give this test CPython 3.13.15's ``Process.wait()``: resolved at exit."""

    cls: Any = base_subprocess.BaseSubprocessTransport
    real = cls._process_exited

    def _process_exited(self: Any, returncode: int) -> None:
        real(self, returncode)
        waiters = self._exit_waiters
        if waiters:
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_result(returncode)
            # Not ``None`` as gh-119710 leaves it: the pre-gh-119710
            # ``_call_connection_lost`` still iterates this list when the pipes
            # close, and must find nothing left to wake.
            self._exit_waiters = []

    monkeypatch.setattr(cls, "_process_exited", _process_exited)
