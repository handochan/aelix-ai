"""Windows-asserting tests for the pid-only process-tree kill (#105, #202).

The POSIX kill body is not merely wrong on Windows, it raises: ``os.killpg`` is
undefined there and ``signal.SIGKILL`` does not exist, so the abort (Esc) and
timeout handlers would have died with ``AttributeError`` inside the very cleanup
they exist to perform.

These RUN on Linux by injecting ``platform="win32"``. The POSIX cases below run
on WINDOWS by the mirror trick: the win32 case deletes ``os.killpg``,
``os.getpgid`` and ``signal.SIGKILL`` to reproduce a Windows interpreter here,
and the POSIX cases lend those same three names back, because a Windows
interpreter genuinely does not have them and the arm cannot even be *called*
without them — the attribute lookups raise before any of the three runs.

Lending them asserts the arm's SHAPE, which is all these cases ever asserted
about it. It claims nothing about that arm being available on Windows: it is
not reachable there at all. ``platform`` defaults to ``sys.platform`` and the
one production caller left does not pass one, so the early return takes every
real Windows call to ``taskkill``.

No ``skipif`` in either direction, per ``tests/cli/test_stdio_encoding_win32.py``:
a case that only runs on one runner is not a regression guard. The first form of
this file was POSIX-bound anyway, through ``monkeypatch.setattr(os, "getpgid")``
alone — which raises when the attribute is absent — and that cost four of the
remaining ``AttributeError`` failures on the first ``windows-latest`` leg.

WHAT #202 CHANGED HERE. The body moved to ``aelix_ai.utils._process_tree``, so
the old ``monkeypatch.setattr(_process_tree.sys, ...)`` had nothing to patch —
the platform is now driven through ``sys`` itself, which is the same module
object the implementation reads. ``argv[0]`` is resolved from ``%SystemRoot%``
(Pi ``7af2d27d``), so the argv cases run both environment arms. And every win32
case asserts ``os.kill`` was NOT called: on Windows ``os.kill`` is
``TerminateProcess``, the root-only kill this whole area exists to stop using —
revision 1 of #202's design still had a TerminateProcess leg and it is gone.

WHAT #222 CHANGED HERE, AND WHY THE FILE STAYS. #202 left
``aelix_coding_agent/tools/_process_tree.py`` behind as a re-export shim so the
two tool spawn sites kept their import path; #222 gave both of them a
``ProcessTree`` instead, the shim's last reason to exist went with them, and it
is deleted — hence the import above now names the primitive directly. The two
cases that asserted the shim was wired to those sites
(``test_bash_kill_group_delegates`` / ``test_subprocess_helper_delegates``) went
with it: they asserted an identity between two module attributes, which is not
something a site can be wrong about any more, and the site-level assertions that
replace them are ``tests/tools/test_bash_tool_containment.py`` and
``tests/tools/test_subprocess_helper.py``'s tree cases. What is asserted below
is the pid-only entry point itself, which #222 did NOT delete: it is the
degradation in ``rpc_client.stop()`` for a client whose ``attach`` raised out of
``start``, and it is still the shape that would crash with ``AttributeError`` on
Windows if the POSIX body were reached there.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

import pytest
from aelix_ai.utils._process_tree import kill_process_tree

# The value only has to be the SAME one production and these assertions see.
# On POSIX this is ``signal.SIGKILL`` itself and lending it back is a no-op; on
# Windows it is the number POSIX uses, and nothing here does anything with it
# but compare it.
SIGKILL = getattr(signal, "SIGKILL", 9)

#: ``SystemRoot`` present and absent. B.5 defaults to ``C:\\Windows`` when the
#: variable is missing, which is what a stripped or non-standard image gives.
SYSTEM_ROOTS = ["D:\\WinDir", None]


@pytest.fixture(autouse=True)
def terminations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Records ``os.kill`` — which on Windows IS ``TerminateProcess``."""

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append((pid, sig)))
    return calls


def _lend_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the interpreter the ``signal.SIGKILL`` the POSIX arm reads."""

    monkeypatch.setattr(signal, "SIGKILL", SIGKILL, raising=False)


def _expected_argv(system_root: str | None, pid: int) -> list[str]:
    """The argv B.5 resolves, for whichever ``SystemRoot`` the case set."""

    root = system_root if system_root is not None else r"C:\Windows"
    return [os.path.join(root, "System32", "taskkill.exe"), "/T", "/F", "/PID", str(pid)]


def _set_system_root(monkeypatch: pytest.MonkeyPatch, system_root: str | None) -> None:
    if system_root is None:
        monkeypatch.delenv("SYSTEMROOT", raising=False)
    else:
        monkeypatch.setenv("SYSTEMROOT", system_root)


# === the win32 arm ==========================================================


@pytest.mark.parametrize("system_root", SYSTEM_ROOTS)
def test_win32_kills_the_tree_with_taskkill(
    system_root: str | None,
    monkeypatch: pytest.MonkeyPatch,
    terminations: list[tuple[int, int]],
) -> None:
    recorded: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        recorded.append(list(argv))
        assert kwargs.get("check") is False  # an already-dead PID is success
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    _set_system_root(monkeypatch, system_root)

    kill_process_tree(4321, platform="win32")

    # ``/T`` is load-bearing: without it only the direct child dies. Windows
    # ignores ``start_new_session``, so there is no process group to target and
    # descendants would be orphaned. ``argv[0]`` is resolved rather than bare
    # because System32 is not always on PATH (Pi #6596).
    assert recorded == [_expected_argv(system_root, 4321)]
    assert terminations == []


def test_win32_never_touches_killpg_or_sigkill(
    monkeypatch: pytest.MonkeyPatch, terminations: list[tuple[int, int]]
) -> None:
    """The actual crash: both names are absent on Windows.

    Deleting them here reproduces a Windows interpreter closely enough to prove
    the win32 path does not reference either.
    """

    monkeypatch.delattr(os, "killpg", raising=False)
    monkeypatch.delattr(os, "getpgid", raising=False)
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)

    kill_process_tree(4321, platform="win32")  # must not raise

    assert terminations == []


def test_win32_retries_the_bare_name_and_survives_a_missing_taskkill(
    monkeypatch: pytest.MonkeyPatch, terminations: list[tuple[int, int]]
) -> None:
    """A wrong ``SystemRoot`` degrades to today's behaviour, not to silence.

    Resolving ``argv[0]`` fixes Pi #6596 but introduces a way to be wrong that
    the bare name never had, so ``FileNotFoundError`` on the resolved path
    retries ``taskkill`` off ``PATH``. When that is missing too — a stripped
    image — the abort path must still not break.
    """

    attempted: list[str] = []

    def missing(argv: list[str], **_k: Any) -> Any:
        attempted.append(argv[0])
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(subprocess, "run", missing)
    _set_system_root(monkeypatch, "D:\\WinDir")

    kill_process_tree(4321, platform="win32")  # must not raise

    assert attempted == [os.path.join("D:\\WinDir", "System32", "taskkill.exe"), "taskkill"]
    assert terminations == []


def test_win32_survives_a_taskkill_that_never_returns(
    monkeypatch: pytest.MonkeyPatch, terminations: list[tuple[int, int]]
) -> None:
    """A hung ``taskkill.exe`` costs a bounded wait, not the process.

    The escalation is synchronous and its win32 callers are coroutines
    (``RpcClient.stop``, the hook timeout ladder, the hook CANCELLATION path —
    the Esc path, where latency is most visible), so an un-timed spawn would
    block the event loop for as long as the shell-out hangs (review
    win-leg/F6). ``TimeoutExpired`` is a ``subprocess.SubprocessError`` and NOT
    an ``OSError``, so the existing suppression did not cover it and it is
    caught by name; this pins both halves.
    """

    def hung(argv: list[str], **kwargs: Any) -> Any:
        assert kwargs.get("timeout") == 5
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(subprocess, "run", hung)
    _set_system_root(monkeypatch, "D:\\WinDir")

    kill_process_tree(4321, platform="win32")  # must not raise

    assert terminations == []


# === POSIX ==================================================================


def test_posix_kills_the_process_group_of_a_child_that_leads_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary shape: the spawn asked for a session of its own.

    Every caller this function ever had spawns through
    ``containment_spawn_kwargs(new_session=True)`` or the literal
    ``start_new_session=True`` it expands to on POSIX, so the child leads the
    group and ``getpgid(pid) == pid``. Since #222 the caller reaching here is
    ``rpc_client.stop()``'s degradation rather than the two tool sites, but the
    spawn shape — and therefore this assertion — is the same one.
    """

    calls: list[tuple[int, int]] = []

    _lend_sigkill(monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False)
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("POSIX must not shell out")
    )

    kill_process_tree(4321, platform="linux")

    assert calls == [(4321, SIGKILL)]


def test_posix_never_killpgs_a_group_the_pid_does_not_lead(
    monkeypatch: pytest.MonkeyPatch, terminations: list[tuple[int, int]]
) -> None:
    """The safety case, on the entry point that had no ``attach`` to carry it.

    A pid that leads no group of its own is in the CALLER's group — this
    interpreter and every sibling it spawned. Until #202's review this function
    ``killpg``'d it anyway: a driver that ``setpgid(0, 0)``'d itself and called
    ``kill_process_tree`` on an uncontained child was measured exiting ``-9``,
    i.e. SIGKILLing itself with its own cleanup (review posix2/F5). The kill now
    degrades to the root, which is the same rule ``ProcessTree.attach`` enforces
    through ``contained`` and ADR-0238 states as a Decision.
    """

    _lend_sigkill(monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1000, raising=False)
    monkeypatch.setattr(
        os, "killpg", lambda *_a: pytest.fail("that group is ours, not the child's"), raising=False
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("POSIX must not shell out")
    )

    kill_process_tree(4321, platform="linux")

    assert terminations == [(4321, SIGKILL)]


def test_posix_a_zombie_leader_falls_back_to_the_pid_as_pgid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Darwin raises ``ProcessLookupError`` for a zombie leader (#202, measured).

    Its group is alive and holding descendants, so giving up here would refuse
    the kill in exactly the case the group kill exists for. The caller spawned
    with a session of its own, so the number is the group the spawn asked for.

    What used to stand here was a contrast between the two tool callers —
    ``bash.py`` calling before any reap, where the zombie really does pin the
    number, and ``_subprocess.py::run_cancellable`` calling after asyncio's
    watcher already reaped the child (measured, #202 review posix2/F1). Since
    #222 neither of them reaches this function: both hold a ``ProcessTree``
    whose pgid was captured at the spawn. The one caller left,
    ``rpc_client.stop()``'s degradation, is an asyncio spawn site too, so what
    bounds the fallback here is the group itself — pinned while any member
    lives, ``ESRCH`` when it is empty.
    """

    calls: list[tuple[int, int]] = []

    def gone(_pid: int) -> int:
        raise ProcessLookupError

    _lend_sigkill(monkeypatch)
    monkeypatch.setattr(os, "getpgid", gone, raising=False)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False)

    kill_process_tree(4321, platform="linux")

    assert calls == [(4321, SIGKILL)]


def test_posix_swallows_a_pgid_it_may_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PermissionError`` is somebody else's process, not ours to kill.

    Also pins the short-circuit: a pgid that cannot be resolved must not reach a
    kill. ``os.killpg`` has to exist for the arm to be callable at all — Python
    resolves the callable before it evaluates the argument that raises — so a
    Windows runner needs it lent even here.
    """

    def boom(*_a: Any, **_k: Any) -> None:
        raise PermissionError()

    _lend_sigkill(monkeypatch)
    monkeypatch.setattr(os, "getpgid", boom, raising=False)
    monkeypatch.setattr(os, "killpg", lambda *_a: pytest.fail("no pgid, no kill"), raising=False)

    kill_process_tree(4321, platform="linux")  # must not raise


@pytest.mark.parametrize("system_root", SYSTEM_ROOTS)
def test_default_platform_is_sys_platform(
    system_root: str | None,
    monkeypatch: pytest.MonkeyPatch,
    terminations: list[tuple[int, int]],
) -> None:
    """Callers pass no platform; the default must follow the host, both ways.

    The first form asserted ``seen == [77]  # this box is POSIX``, which encodes
    the author's runner into the assertion rather than the behaviour: on
    ``windows-latest`` it was right about the wrong thing. Driving ``sys.platform``
    asks what the test's name claims, on every runner. Since #202 the body lives
    in ``aelix_ai.utils._process_tree`` and this module re-exports it, so the
    object to patch is ``sys`` itself — the same module both files read.
    """

    killed: list[int] = []
    shelled: list[list[str]] = []
    _lend_sigkill(monkeypatch)
    _set_system_root(monkeypatch, system_root)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(os, "killpg", lambda pgid, _sig: killed.append(pgid), raising=False)
    monkeypatch.setattr(subprocess, "run", lambda argv, **_k: shelled.append(list(argv)))

    monkeypatch.setattr(sys, "platform", "linux")
    kill_process_tree(77)

    assert (killed, shelled) == ([77], [])

    monkeypatch.setattr(sys, "platform", "win32")
    kill_process_tree(78)

    assert killed == [77]  # the second call must not have reached killpg
    assert shelled == [_expected_argv(system_root, 78)]
    assert terminations == []
