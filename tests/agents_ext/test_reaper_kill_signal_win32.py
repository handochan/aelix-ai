"""The escalation leg must not name ``SIGKILL`` on Windows (#103).

``kill_tree`` reached ``os.kill(pid, signal.SIGKILL)`` unguarded, and that name
does not exist off POSIX. ``AttributeError`` is no subclass of ``OSError``, so
the surrounding ``contextlib.suppress(ProcessLookupError, PermissionError,
OSError)`` did not catch it either: it escaped the handler whose entire job is
to make a child stop existing. Measured on the first ``windows-latest`` run, in
``kill_tree``'s descendant loop: ``AttributeError: module 'signal' has no
attribute 'SIGKILL'``.

No ``skipif``: the branch is a ``sys.platform`` read and the crash is a missing
attribute, so patching one and deleting the other reproduces Windows exactly on
POSIX — the injection ``tests/session/test_session_file_permissions.py`` and
``tests/agents_ext/test_spawn_preexec_platform.py`` already use, for the reason
``tests/cli/test_stdio_encoding_win32.py`` states: a case that only runs on the
runner we do not have is not a regression guard.

What these cases do NOT claim is that the reaper is correct on Windows. It is
not, and no signal choice here could make it so — see :func:`_kill_signal`'s
docstring on the first leg already being an uncatchable ``TerminateProcess``
and on the grandchildren orphaned before this code is reached (#202). These pin
one thing: the escalation runs to completion instead of dying inside itself.
"""

from __future__ import annotations

import signal

import pytest
from aelix_agents import reaper
from aelix_agents.reaper import WalkEntry, kill_tree, reap
from aelix_ai.utils import _process_identity

# The POSIX cases run on the windows leg too, where this name does not exist —
# so they lend it, exactly as ``tests/tools/test_process_tree_win32.py`` does.
# The value only has to be the one both production and the assertion see: on
# POSIX it IS ``signal.SIGKILL`` and lending it back is a no-op.
SIGKILL = getattr(signal, "SIGKILL", 9)

STAMP = 1000.0
#: The walk's entry for pid 99, with the start time it read (#611 S2a).
WALKED = WalkEntry(99, STAMP, 4321)


@pytest.fixture(autouse=True)
def _pid_99_is_still_the_process_walked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pid 99 still reads the walk's start time, and no pidfd is opened (#611 S2a).

    A walked pid is signalled only while its start time is the walk's; 99 is
    fake, so the reader is pinned for it and Linux is made to compare start
    times as the other hosts do.
    """

    real = _process_identity.process_started
    monkeypatch.setattr(
        _process_identity, "process_started", lambda pid: STAMP if pid == 99 else real(pid)
    )
    monkeypatch.delattr(reaper.os, "pidfd_open", raising=False)


class _Live:
    """A child that has NOT exited, so ``_signal_child`` really signals it."""

    returncode = None
    pid = 4321

    def kill(self) -> None:  # pragma: no cover — reached only by a regression
        raise AssertionError("kill_tree must never reap behind the loop's back")

    async def wait(self) -> int:
        return 15


def _simulate_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the module see Windows: ``sys.platform`` win32, no ``SIGKILL``.

    Both halves are needed. The platform alone would exercise the branch
    without reproducing the crash; deleting the attribute alone would prove
    nothing about which platform decides.

    ``raising=False`` on the delete says "ensure it is absent", not "assert it
    was present". On the windows leg it already is absent, and a strict delete
    would fail this file there — on the one runner it is written about.
    """

    monkeypatch.setattr(reaper.sys, "platform", "win32", raising=True)
    monkeypatch.delattr(reaper.signal, "SIGKILL", raising=False)


def _record_signals(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Spy on ``os.kill`` — the pids and signals, in order."""

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(reaper.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    return sent


def test_kill_tree_on_windows_does_not_die_on_the_missing_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole tree is still signalled, deepest first, and nothing raises.

    ``SIGTERM`` is not a downgrade here: on Windows ``os.kill`` is
    ``TerminateProcess(handle, sig)`` for every value that is not a console
    control event, which the target cannot catch, block or handle.
    """

    sent = _record_signals(monkeypatch)
    _simulate_win32(monkeypatch)

    kill_tree(_Live(), [WALKED])

    assert sent == [(99, signal.SIGTERM), (4321, signal.SIGTERM)]


async def test_reap_escalates_on_windows_instead_of_dying_in_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path that broke is ``reap`` → ``kill_tree``, not ``kill_tree`` alone.

    ``eager_kill=True`` is the second-Ctrl+C leg: no grace, straight to the
    escalation, which is where the ``AttributeError`` landed in production and
    where it cost the exit status of a child nobody was left to wait for.
    """

    sent = _record_signals(monkeypatch)
    _simulate_win32(monkeypatch)

    assert await reap(_Live(), eager_kill=True, descendants=[WALKED]) == 15

    # The cooperative leg first, then the escalation over the same tree.
    assert sent == [
        (4321, signal.SIGTERM),
        (99, signal.SIGTERM),
        (4321, signal.SIGTERM),
    ]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_posix_still_escalates_with_sigkill(
    platform: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Platform-scoped, not a blanket downgrade to SIGTERM.

    A fix written as an unconditional ``SIGTERM`` passes the Windows case above
    while turning ``reap``'s second leg into a repeat of its first on the
    platform that HAS SIGKILL — i.e. leaving alive exactly the SIGTERM-ignoring
    child the escalation exists for. This is the case that catches that.
    """

    monkeypatch.setattr(reaper.sys, "platform", platform, raising=True)
    monkeypatch.setattr(reaper.signal, "SIGKILL", SIGKILL, raising=False)
    sent = _record_signals(monkeypatch)

    kill_tree(_Live(), [WALKED])

    assert sent == [(99, SIGKILL), (4321, SIGKILL)]
