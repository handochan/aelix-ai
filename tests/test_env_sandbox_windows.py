"""Windows-asserting tests for :mod:`tests.env_sandbox` (#109 / #103 pre-req).

These do not skip on POSIX and they do not merely mock a platform string.
``ntpath`` *is* CPython's Windows path implementation and imports fine on
Linux, so calling ``ntpath.expanduser`` here exercises the exact code a
``windows-latest`` runner would execute. That makes the central claim —
"setting ``HOME`` does not sandbox a Windows runner" — a measured fact in CI
rather than an assertion about a machine we do not have.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import subprocess
from pathlib import Path

import pytest

from tests.env_sandbox import _CHILD_BOOT_KEYS, child_env, sandbox_home


def test_home_alone_does_not_sandbox_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this helper exists for: HOME is invisible to Windows.

    Pinning it as a test keeps someone from "simplifying" ``sandbox_home``
    back down to a single ``setenv("HOME", ...)``.
    """

    sandbox = tmp_path / "home"
    real_profile = tmp_path / "REAL-USER-PROFILE"
    monkeypatch.setenv("USERPROFILE", str(real_profile))
    monkeypatch.setenv("HOME", str(sandbox))

    # POSIX honours the sandbox ...
    assert posixpath.expanduser("~") == str(sandbox)
    # ... Windows ignores it entirely and resolves the real profile.
    assert ntpath.expanduser("~") == str(real_profile)


def test_sandbox_home_covers_windows_expanduser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the helper, BOTH platform implementations resolve the sandbox."""

    sandbox = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "REAL-USER-PROFILE"))

    returned = sandbox_home(monkeypatch, sandbox)

    assert returned == sandbox
    assert posixpath.expanduser("~") == str(sandbox)
    assert ntpath.expanduser("~") == str(sandbox)
    # The form production code actually uses (``expand_tilde_path``,
    # ``~/.aelix/agent``) stays inside the sandbox on Windows too.
    assert ntpath.expanduser("~/.aelix/agent").startswith(str(sandbox))
    assert os.path.expanduser("~") == str(sandbox)


def test_sandbox_home_covers_homedrive_homepath_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second arm of ntpath's chain is sandboxed as well.

    ``ntpath.expanduser`` falls through to ``%HOMEDRIVE%`` + ``%HOMEPATH%``
    when ``%USERPROFILE%`` is absent. A runner where the profile var is unset
    must still land in the sandbox, not on the real drive.
    """

    sandbox = tmp_path / "home"
    sandbox_home(monkeypatch, sandbox)
    monkeypatch.delenv("USERPROFILE")

    assert ntpath.expanduser("~") == str(sandbox)


def test_sandbox_home_sets_windows_config_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """%APPDATA% / %LOCALAPPDATA% land under the sandbox, not the real profile.

    ``cli/extension_install.py`` reads ``APPDATA`` when it enumerates pip
    config candidates, so an unsandboxed value is a second write path into the
    runner's profile.
    """

    sandbox = tmp_path / "home"
    sandbox_home(monkeypatch, sandbox)

    assert Path(os.environ["APPDATA"]) == sandbox / "AppData" / "Roaming"
    assert Path(os.environ["LOCALAPPDATA"]) == sandbox / "AppData" / "Local"


def test_no_test_sets_home_outside_the_helper() -> None:
    """Guard: HOME may only be sandboxed through :func:`sandbox_home`.

    Without this, the next test to reach for ``monkeypatch.setenv("HOME", ...)``
    silently re-opens the leak on Windows and nothing on Linux would notice.
    """

    root = Path(__file__).resolve().parent
    proc = subprocess.run(
        ["git", "grep", "-n", r'setenv("HOME"', "--", "tests/"],
        cwd=root.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    offenders = [
        line
        for line in proc.stdout.splitlines()
        # The helper itself and this guard's own pattern are the exceptions.
        if not re.match(r"tests/(env_sandbox|test_env_sandbox_windows)\.py:", line)
    ]
    assert offenders == [], (
        "these tests sandbox HOME directly, which does nothing on Windows; "
        "route them through tests.env_sandbox.sandbox_home:\n"
        + "\n".join(offenders)
    )


def test_child_env_copies_boot_keys_only_when_the_parent_has_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #209 part: SystemRoot crosses when present, and nothing is planted
    as ``""`` when it is not."""

    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.PY")
    monkeypatch.delenv("windir", raising=False)
    monkeypatch.setenv("TMP", "")

    env = child_env(tmp_path / "home")

    assert env["SystemRoot"] == r"C:\Windows"
    assert env["PATHEXT"] == ".COM;.EXE;.PY"
    assert "windir" not in env
    assert "TMP" not in env
    # (``HOMEDRIVE`` is legitimately "" on POSIX — that is ``home_env``'s
    # native ``splitdrive``; the boot keys are the ones that must never be.)
    assert not [k for k in _CHILD_BOOT_KEYS if env.get(k) == ""]
    # The home half is the same set ``sandbox_home`` patches.
    assert env["USERPROFILE"] == env["HOME"] == str(tmp_path / "home")


def test_child_env_extra_wins_and_leaks_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-cross")
    monkeypatch.setenv("PYTHONPATH", "")

    env = child_env(tmp_path / "home", PATH="/pinned", PI_OFFLINE="1")

    assert env["PATH"] == "/pinned"
    assert env["PI_OFFLINE"] == "1"
    assert "OPENAI_API_KEY" not in env
    assert "PYTHONPATH" not in env


def test_no_test_hand_builds_a_child_home() -> None:
    """Guard for the dict channel, as the test above guards the monkeypatch one.

    A real-child ``env=`` dict written with ``"HOME": ...`` sandboxes nothing on
    Windows and, before #209, carried no ``%SystemRoot%`` either. Build it with
    :func:`child_env`.
    """

    root = Path(__file__).resolve().parent
    proc = subprocess.run(
        # ``"HOME": str(<tmp>)`` is the shape every real-child dict used; a
        # string-literal HOME is a pure-function input (pip config candidates)
        # and never reaches a process.
        ["git", "grep", "-n", r'"HOME": str(', "--", "tests/"],
        cwd=root.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    offenders = [
        line
        for line in proc.stdout.splitlines()
        if not re.match(r"tests/(env_sandbox|test_env_sandbox_windows)\.py:", line)
    ]
    assert offenders == [], (
        "these tests hand-build a child HOME, which sandboxes nothing on Windows; "
        "route them through tests.env_sandbox.child_env:\n" + "\n".join(offenders)
    )
