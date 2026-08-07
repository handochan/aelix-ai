"""Windows-asserting tests for process-tree termination (#105).

The POSIX kill body is not merely wrong on Windows, it raises: ``os.killpg``
is undefined there and ``signal.SIGKILL`` does not exist, so the abort (Esc)
and timeout handlers would have died with ``AttributeError`` inside the very
cleanup they exist to perform.

These RUN on Linux by injecting ``platform="win32"``.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

import pytest
from aelix_coding_agent.tools import _process_tree
from aelix_coding_agent.tools._process_tree import kill_process_tree

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

    monkeypatch.setattr(os, "getpgid", lambda pid: pid + 1000)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("POSIX must not shell out")
    )

    kill_process_tree(4321, platform="linux")

    assert calls == [(5321, signal.SIGKILL)]


@pytest.mark.parametrize("exc", [ProcessLookupError, PermissionError])
def test_posix_swallows_an_already_dead_child(
    monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise exc()

    monkeypatch.setattr(os, "getpgid", boom)

    kill_process_tree(4321, platform="linux")  # must not raise


def test_default_platform_is_sys_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers pass no platform; the default must follow the real host."""

    seen: list[int] = []
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "killpg", lambda pgid, _sig: seen.append(pgid))

    kill_process_tree(77)

    assert seen == [77]  # this box is POSIX


# === the two call sites are wired to it =====================================


def test_bash_kill_group_delegates() -> None:
    from aelix_coding_agent.tools import bash as bash_mod

    assert bash_mod.kill_process_tree is _process_tree.kill_process_tree


def test_subprocess_helper_delegates() -> None:
    from aelix_coding_agent.tools import _subprocess as sub_mod

    assert sub_mod.kill_process_tree is _process_tree.kill_process_tree
