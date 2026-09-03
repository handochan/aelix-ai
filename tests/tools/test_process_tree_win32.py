"""Windows-asserting tests for process-tree termination (#105).

The POSIX kill body is not merely wrong on Windows, it raises: ``os.killpg``
is undefined there and ``signal.SIGKILL`` does not exist, so the abort (Esc)
and timeout handlers would have died with ``AttributeError`` inside the very
cleanup they exist to perform.

These RUN on Linux by injecting ``platform="win32"``. The POSIX cases below run
on WINDOWS by the mirror trick: the win32 case deletes ``os.killpg``,
``os.getpgid`` and ``signal.SIGKILL`` to reproduce a Windows interpreter here,
and the POSIX cases lend those same three names back, because a Windows
interpreter genuinely does not have them and the arm cannot even be *called*
without them — the attribute lookups raise before any of the three runs.

Lending them asserts the arm's SHAPE, which is all these cases ever asserted
about it. It claims nothing about that arm being available on Windows: it is
not reachable there at all. ``platform`` defaults to ``sys.platform`` and
neither production caller (``bash.py``, ``_subprocess.py``) passes one, so the
early return takes every real Windows call to ``taskkill``.

No ``skipif`` in either direction, per ``tests/cli/test_stdio_encoding_win32.py``:
a case that only runs on one runner is not a regression guard. The first form of
this file was POSIX-bound anyway, through ``monkeypatch.setattr(os, "getpgid")``
alone — which raises when the attribute is absent — and that cost four of the
remaining ``AttributeError`` failures on the first ``windows-latest`` leg.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

import pytest
from aelix_coding_agent.tools import _process_tree
from aelix_coding_agent.tools._process_tree import kill_process_tree

# The value only has to be the SAME one production and these assertions see.
# On POSIX this is ``signal.SIGKILL`` itself and lending it back is a no-op; on
# Windows it is the number POSIX uses, and nothing here does anything with it
# but compare it.
SIGKILL = getattr(signal, "SIGKILL", 9)


def _lend_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the interpreter the ``signal.SIGKILL`` the POSIX arm reads."""

    monkeypatch.setattr(signal, "SIGKILL", SIGKILL, raising=False)


# === the win32 arm ==========================================================


def test_win32_kills_the_tree_with_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        recorded.append(list(argv))
        assert kwargs.get("check") is False  # an already-dead PID is success
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    kill_process_tree(4321, platform="win32")

    # ``/T`` is load-bearing: without it only the direct child dies. Windows
    # ignores ``start_new_session``, so there is no process group to target and
    # descendants would be orphaned.
    assert recorded == [["taskkill", "/T", "/F", "/PID", "4321"]]


def test_win32_never_touches_killpg_or_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual crash: both names are absent on Windows.

    Deleting them here reproduces a Windows interpreter closely enough to prove
    the win32 path does not reference either.
    """

    monkeypatch.delattr(os, "killpg", raising=False)
    monkeypatch.delattr(os, "getpgid", raising=False)
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: None)

    kill_process_tree(4321, platform="win32")  # must not raise


def test_win32_survives_a_missing_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stripped image without taskkill must not break the abort path."""

    def boom(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("taskkill")

    monkeypatch.setattr(subprocess, "run", boom)

    kill_process_tree(4321, platform="win32")  # must not raise


# === POSIX is unchanged =====================================================


def test_posix_still_kills_the_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int]] = []

    _lend_sigkill(monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1000, raising=False)
    monkeypatch.setattr(
        os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("POSIX must not shell out")
    )

    kill_process_tree(4321, platform="linux")

    assert calls == [(5321, SIGKILL)]


@pytest.mark.parametrize("exc", [ProcessLookupError, PermissionError])
def test_posix_swallows_an_already_dead_child(
    monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise exc()

    _lend_sigkill(monkeypatch)
    monkeypatch.setattr(os, "getpgid", boom, raising=False)
    # Also pins the short-circuit: a pgid that cannot be resolved must not
    # reach a kill. ``os.killpg`` has to exist for the arm to be callable at
    # all — Python resolves the callable before it evaluates the argument that
    # raises — so a Windows runner needs it lent even here.
    monkeypatch.setattr(
        os, "killpg", lambda *_a: pytest.fail("no pgid, no kill"), raising=False
    )

    kill_process_tree(4321, platform="linux")  # must not raise


def test_default_platform_is_sys_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers pass no platform; the default must follow the host, both ways.

    The first form asserted ``seen == [77]  # this box is POSIX``, which encodes
    the author's runner into the assertion rather than the behaviour: on
    ``windows-latest`` it was right about the wrong thing. Driving the module's
    own ``sys.platform`` asks what the test's name claims, on every runner.
    """

    killed: list[int] = []
    shelled: list[list[str]] = []
    _lend_sigkill(monkeypatch)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(
        os, "killpg", lambda pgid, _sig: killed.append(pgid), raising=False
    )
    monkeypatch.setattr(subprocess, "run", lambda argv, **_k: shelled.append(list(argv)))

    monkeypatch.setattr(_process_tree.sys, "platform", "linux", raising=True)
    kill_process_tree(77)

    assert (killed, shelled) == ([77], [])

    monkeypatch.setattr(_process_tree.sys, "platform", "win32", raising=True)
    kill_process_tree(78)

    assert killed == [77]  # the second call must not have reached killpg
    assert shelled == [["taskkill", "/T", "/F", "/PID", "78"]]


# === the two call sites are wired to it =====================================


def test_bash_kill_group_delegates() -> None:
    from aelix_coding_agent.tools import bash as bash_mod

    assert bash_mod.kill_process_tree is _process_tree.kill_process_tree


def test_subprocess_helper_delegates() -> None:
    from aelix_coding_agent.tools import _subprocess as sub_mod

    assert sub_mod.kill_process_tree is _process_tree.kill_process_tree
