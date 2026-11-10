"""The parent-death hook rechecks the parent it was spawned by (#611 S1a, Codex r5 finding 13).

``PR_SET_PDEATHSIG`` watches whichever process is the child's parent when the
hook arms it. An aelix that died between ``fork`` and that call has already
handed the child to init or to a subreaper, and the signal is armed against that
one instead: the delegated agent runs on. On main ``fc8f3048`` the hook never
looked at its parent at all — with ``getppid`` and ``_exit`` injected and the
platform patched to linux, its one run read::

    {"probe": "pdeathsig-recheck", "prctl": 1, "getppid": 0, "exit": []}

:func:`~aelix_agents.reaper.pdeathsig_preexec` now binds the spawning process's
pid, read before the fork, and the hook exits the child before ``exec`` when
``getppid()`` is not that pid — compared with the pid, never with 1, which a
subreaper whose pid is not 1 would pass (codex-rs
``utils/pty/src/process_group.rs:25-37``). Today's delegation spawns get it:
the print channel and the RPC channel both pass ``pdeathsig_preexec()``. The
bash tool's own spawns are #611 S2.

The injected cases run on every host (``sys.platform`` is patched, as in
``test_spawn_preexec_platform.py``). The last case runs the real kernel path —
a parent killed while its child sits between ``fork`` and ``exec`` — and so is
Linux only.
"""

from __future__ import annotations

import ctypes
import functools
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from aelix_agents import reaper
from aelix_agents.reaper import pdeathsig, pdeathsig_preexec

_PARENT = 4100
_SUBREAPER = 4242  # a reaper that is not init: comparing with 1 would miss it


class _Exited(Exception):
    """Stands in for ``os._exit``, which would end the test process."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Linux:
    """Linux as the hook sees it: a recording ``prctl``, ``signal`` and ``_exit``.

    ``events`` holds the hook's calls in order: ``("signal", signum, handler)``
    for each disposition it sets, ``("prctl", *args)`` for each arming. The test
    process's own SIGTERM handler is never touched: ``getsignal`` answers
    ``handler`` and ``signal`` only records.
    """

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.handler: Any = lambda *_a: None
        self.cdll: list[tuple[Any, ...]] = []

    def prctl(self, *args: Any) -> int:
        self.events.append(("prctl", *args))
        return 0

    @property
    def armed(self) -> list[tuple[Any, ...]]:
        return [e[1:] for e in self.events if e[0] == "prctl"]


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> _Linux:
    host = _Linux()

    class _FakeLibc:
        prctl = staticmethod(host.prctl)

    def _cdll(*args: Any, **kwargs: Any) -> Any:
        host.cdll.append((args, kwargs))
        return _FakeLibc()

    def _exit(code: int) -> None:
        raise _Exited(code)

    monkeypatch.setattr(reaper.sys, "platform", "linux", raising=True)
    monkeypatch.setattr(ctypes, "CDLL", _cdll)
    monkeypatch.setattr(reaper.os, "_exit", _exit)
    monkeypatch.setattr(reaper.signal, "getsignal", lambda _signum: host.handler)
    monkeypatch.setattr(
        reaper.signal, "signal", lambda signum, handler: host.events.append(("signal", signum, handler))
    )
    return host


@pytest.mark.parametrize("now", [1, _SUBREAPER], ids=["init", "subreaper-not-pid-1"])
def test_recheck_compares_the_parent_captured_before_fork(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch, now: int
) -> None:
    monkeypatch.setattr(reaper.os, "getppid", lambda: now)

    with pytest.raises(_Exited) as exited:
        pdeathsig(_PARENT, linux.prctl)

    assert exited.value.code == 128 + signal.SIGTERM
    assert linux.armed and linux.armed[0][:2] == (1, signal.SIGTERM), "armed before the recheck"


def test_the_original_parent_still_there_lets_exec_go_on(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reaper.os, "getppid", lambda: _PARENT)

    pdeathsig(_PARENT, linux.prctl)

    assert len(linux.armed) == 1


def test_the_recheck_runs_even_when_arming_failed(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refused(*_a: Any) -> int:
        raise OSError("prctl refused (injected)")

    monkeypatch.setattr(reaper.os, "getppid", lambda: 1)

    with pytest.raises(_Exited):
        pdeathsig(_PARENT, _refused)


def test_without_a_parent_pid_the_hook_only_arms(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reaper.os, "getppid", lambda: 1)

    pdeathsig(None, linux.prctl)

    assert len(linux.armed) == 1


def test_off_linux_the_hook_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _exit(code: int) -> None:
        raise _Exited(code)

    monkeypatch.setattr(reaper.sys, "platform", "darwin", raising=True)
    monkeypatch.setattr(reaper.os, "_exit", _exit)
    monkeypatch.setattr(reaper.os, "getppid", lambda: 1)

    pdeathsig(_PARENT)


def test_the_spawn_hook_binds_the_spawning_process_pid(linux: _Linux) -> None:
    hook = pdeathsig_preexec()

    assert isinstance(hook, functools.partial)
    assert hook.func is pdeathsig and not hook.keywords
    assert hook.args[0] == os.getpid() and len(hook.args) == 2


# === nothing is loaded after the fork (Codex review of #721, finding 7) ===========


def test_the_hook_loads_no_library_after_the_fork(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch
) -> None:
    """libc is loaded and ``prctl`` looked up by ``pdeathsig_preexec`` — in the
    spawning process — and the hook only calls it. On ``ed76ea76`` the hook built
    ``ctypes.CDLL("libc.so.6")`` itself, between ``fork`` and ``exec``, where a
    lock another parent thread held at the fork is never released (Codex's
    ``preexec_probe.py``: the middle process blocked in ``Popen``, no exec)."""

    monkeypatch.setattr(reaper.os, "getppid", os.getpid)
    hook = pdeathsig_preexec()
    assert hook is not None
    assert len(linux.cdll) == 1, "resolved once, before the fork"

    hook()

    assert len(linux.cdll) == 1, "the hook loaded a library"
    assert linux.armed == [(1, signal.SIGTERM, 0, 0, 0)]


def test_a_bare_hook_has_nothing_to_arm_and_loads_nothing(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reaper.os, "getppid", lambda: _PARENT)

    pdeathsig(_PARENT)

    assert linux.cdll == [] and linux.armed == []


# === SIGTERM is back to its default before arming (Codex review of #721, finding 6) ==


def test_an_inherited_sigterm_handler_is_reset_before_arming(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reaper.os, "getppid", lambda: _PARENT)

    pdeathsig(_PARENT, linux.prctl)

    assert [e[0] for e in linux.events] == ["signal", "prctl"]
    assert linux.events[0] == ("signal", signal.SIGTERM, signal.SIG_DFL)


@pytest.mark.parametrize("handler", [signal.SIG_IGN, signal.SIG_DFL], ids=["ignored", "default"])
def test_a_sigterm_that_is_not_handled_is_left_as_exec_would_leave_it(
    linux: _Linux, monkeypatch: pytest.MonkeyPatch, handler: Any
) -> None:
    monkeypatch.setattr(reaper.os, "getppid", lambda: _PARENT)
    linux.handler = handler

    pdeathsig(_PARENT, linux.prctl)

    assert [e[0] for e in linux.events] == ["prctl"]


_HANDLED = textwrap.dedent(
    """
    import asyncio, os, signal, subprocess, sys
    sys.path[:0] = {path!r}
    from aelix_agents import reaper
    from aelix_agents.reaper import pdeathsig_preexec

    marker = sys.argv[1]
    loop = asyncio.new_event_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: None)  # as the TUI and print mode do
    hook = pdeathsig_preexec()

    def after_the_recheck():
        reaper.sys.platform = "linux"  # the hook's Linux body, on any POSIX host
        hook()
        # The parent-death signal, landing after the recheck passed.
        os.kill(os.getpid(), signal.SIGTERM)

    child = subprocess.Popen(
        ["/bin/sh", "-c", 'touch "$0"', marker], preexec_fn=after_the_recheck
    )
    print(child.wait(), flush=True)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="preexec_fn is POSIX")
def test_a_sigterm_after_the_recheck_ends_a_child_whose_parent_handles_sigterm(
    tmp_path: Path,
) -> None:
    """Codex's ``postcheck_probe.py`` as a case: the spawning process handles
    SIGTERM the way the product does, and the SIGTERM arrives after the hook
    returned. On ``ed76ea76`` the inherited handler took it and ``exec`` ran:
    exit 0 and the marker written. Now the child ends with -15 before ``exec``."""

    marker = tmp_path / "execed"
    middle = subprocess.run(
        [sys.executable, "-c", _HANDLED.format(path=sys.path), str(marker)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert middle.returncode == 0, middle.stderr
    assert middle.stdout.strip() == str(-signal.SIGTERM)
    assert not marker.exists(), "the command ran after a SIGTERM the hook let through"


_MIDDLE = textwrap.dedent(
    """
    import os, sys, time, subprocess
    sys.path[:0] = {path!r}
    from aelix_agents.reaper import pdeathsig_preexec

    forked, marker = sys.argv[1], sys.argv[2]
    me = os.getpid()
    hook = pdeathsig_preexec()

    def between_fork_and_exec():
        # The child, before it arms: say so, then wait until the parent is gone,
        # so the arming happens after the death, as the race has it.
        open(forked, "w").close()
        deadline = time.monotonic() + 20
        while os.getppid() == me and time.monotonic() < deadline:
            time.sleep(0.01)
        hook()

    subprocess.Popen(
        ["/bin/sh", "-c", 'touch "$0"; sleep 5', marker], preexec_fn=between_fork_and_exec
    )
    """
)


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux")
def test_a_parent_killed_between_fork_and_exec_leaves_no_running_child(tmp_path: Path) -> None:
    """The real race, made deterministic: the middle process forks a child whose
    pre-exec step waits for the middle to be SIGKILLed, then runs the hook the
    delegation spawn passes. With the recheck the child never reaches ``exec``,
    so the command it would have run never writes its marker."""

    forked, marker = tmp_path / "forked", tmp_path / "marker"
    middle = subprocess.Popen(
        [sys.executable, "-c", _MIDDLE.format(path=sys.path), str(forked), str(marker)]
    )
    try:
        deadline = time.monotonic() + 20
        while not forked.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert forked.exists(), "the child never reached its pre-exec step"
        middle.kill()
        middle.wait(timeout=20)
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not marker.exists(), "the child ran its command under another parent"
    finally:
        if middle.poll() is None:
            middle.kill()
            middle.wait()


_DIES_AFTER_THE_RECHECK = textwrap.dedent(
    """
    import asyncio, os, signal, subprocess, sys, time
    sys.path[:0] = {path!r}
    from aelix_agents.reaper import pdeathsig_preexec

    armed, marker = sys.argv[1], sys.argv[2]
    me = os.getpid()
    loop = asyncio.new_event_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: None)  # as the TUI and print mode do
    hook = pdeathsig_preexec()

    def armed_then_parent_dies():
        hook()  # armed against this process, and the recheck passed
        open(armed, "w").close()
        deadline = time.monotonic() + 20
        while os.getppid() == me and time.monotonic() < deadline:
            time.sleep(0.01)

    subprocess.Popen(
        ["/bin/sh", "-c", 'touch "$0"; sleep 5', marker], preexec_fn=armed_then_parent_dies
    )
    time.sleep(30)
    """
)


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_PDEATHSIG is Linux")
def test_a_parent_killed_after_the_recheck_still_ends_the_child(tmp_path: Path) -> None:
    """The real kernel path of Codex's finding 6: the hook armed and rechecked,
    and then the parent — one that handles SIGTERM, as the product does — is
    SIGKILLed before ``exec``. The kernel's SIGTERM has to end the child. On
    ``ed76ea76`` the inherited handler took it and the command ran."""

    armed, marker = tmp_path / "armed", tmp_path / "marker"
    middle = subprocess.Popen(
        [sys.executable, "-c", _DIES_AFTER_THE_RECHECK.format(path=sys.path), str(armed), str(marker)]
    )
    try:
        deadline = time.monotonic() + 20
        while not armed.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert armed.exists(), "the child never armed"
        middle.kill()
        middle.wait(timeout=20)
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not marker.exists(), "the parent-death signal was swallowed and exec ran"
    finally:
        if middle.poll() is None:
            middle.kill()
            middle.wait()


def test_the_hook_lives_in_aelix_ai_and_reaper_reexports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """#611 S2a: the hook moved to ``aelix_ai.utils._parent_death``, unchanged.

    A spawn site in ``aelix_coding_agent`` (S2d's background ``bash``) can take
    it there; product-core may not import ``aelix_agents`` (the band rule,
    ``tests/agents/test_p2_band_boundaries.py``). ``reaper`` re-exports both
    names, so its callers and the cases above reach the same functions, and
    the preexec built through ``reaper`` binds the moved hook and this
    process's pid. The new module imports nothing from ``aelix_agents``.
    Fails on ``3cab5d16`` (no such module), and with the re-export dropped.
    """

    from aelix_ai.utils import _parent_death

    assert reaper.pdeathsig is _parent_death.pdeathsig
    assert reaper.pdeathsig_preexec is _parent_death.pdeathsig_preexec
    monkeypatch.setattr(reaper.sys, "platform", "linux", raising=True)
    hook = reaper.pdeathsig_preexec()
    assert isinstance(hook, functools.partial)
    assert hook.func is _parent_death.pdeathsig
    assert hook.args[0] == os.getpid()
    source = Path(_parent_death.__file__).read_text(encoding="utf-8")
    assert "aelix_agents" not in "".join(
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    )
