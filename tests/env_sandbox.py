"""Cross-platform home-directory sandboxing for tests.

``monkeypatch.setenv("HOME", tmp_path)`` sandboxes nothing on Windows.
``ntpath.expanduser`` — which backs ``os.path.expanduser`` and therefore
``Path.home()`` on a Windows runner — consults ``%USERPROFILE%`` first and
``%HOMEDRIVE%`` + ``%HOMEPATH%`` second, and **never reads ``HOME``**
(CPython ``Lib/ntpath.py``, the ``if 'USERPROFILE' in os.environ`` chain).

The consequence is not a skipped test but a destructive one: every test that
sandboxes itself by setting ``HOME`` alone resolves ``~`` to the *runner's real
profile* on a ``windows-latest`` job, so production code under test writes
``~/.aelix/agent`` — settings, ``auth.json``, session JSONL — into the actual
user profile instead of ``tmp_path``. That is why this helper is a stated
prerequisite for adding a Windows CI leg (#109 / #103): without it a Windows
leg would not merely be red, it would corrupt the machine it runs on.

:func:`sandbox_home` sets every variable each platform's ``expanduser``
consults, plus the ``%APPDATA%`` / ``%LOCALAPPDATA%`` pair Windows uses for
per-user configuration (read by ``cli/extension_install.py`` when it enumerates
pip config candidates), so a single call sandboxes the same test on POSIX and
on Windows.

:func:`child_env` is the same idea for a REAL child interpreter, where the
autouse guards in ``tests/conftest.py`` cannot reach and the test hands the
child a hand-built ``env=`` dict instead. Two things go wrong when that dict is
written with only POSIX in mind (#209): the child has no ``%SystemRoot%``, so
``import asyncio`` → ``windows_events`` → ``import _overlapped`` dies in
Winsock initialisation with ``OSError: [WinError 10106]`` before the test's
own assertion is reached; and it has ``HOME`` but no ``%USERPROFILE%``, so a
child that *did* boot would resolve ``~`` to the runner's real profile — the
destructive case above, one process boundary further out.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

__all__ = ["child_env", "home_env", "sandbox_home"]


def home_env(home: str | os.PathLike[str]) -> dict[str, str]:
    """Every home-directory variable each platform's ``expanduser`` consults.

    The dict form of :func:`sandbox_home`, for callers that build an ``env=``
    for a child process rather than patching their own.
    """

    home_path = Path(home)
    text = str(home_path)
    # Split with the *native* ``os.path.splitdrive`` so HOMEDRIVE + HOMEPATH
    # reassembles to ``text`` on whichever platform is running; on POSIX the
    # drive is empty and the pair is inert.
    drive, tail = os.path.splitdrive(text)
    return {
        # POSIX: ``posixpath.expanduser`` reads $HOME.
        "HOME": text,
        # Windows: ``ntpath.expanduser`` reads %USERPROFILE% first ...
        "USERPROFILE": text,
        # ... and falls back to %HOMEDRIVE% + %HOMEPATH%.
        "HOMEDRIVE": drive,
        "HOMEPATH": tail,
        # Windows per-user configuration roots, mirroring the real layout under
        # the profile so a leaked write lands in the sandbox rather than in
        # C:\\Users\\runner\\AppData.
        "APPDATA": str(home_path / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home_path / "AppData" / "Local"),
    }


def sandbox_home(monkeypatch: pytest.MonkeyPatch, home: str | os.PathLike[str]) -> Path:
    """Point every home-directory environment variable at ``home``.

    Returns the sandbox as a :class:`~pathlib.Path` so a caller can keep using
    it (``sandbox_home(monkeypatch, tmp_path / "home") / ".aelix"``).

    The directory is NOT created — callers that need it on disk create it, and
    the several tests that assert on first-run behaviour depend on it being
    absent.
    """

    for key, value in home_env(home).items():
        monkeypatch.setenv(key, value)
    return Path(home)


# What a Windows child needs from the parent to boot at all. Each is copied
# only when the parent has it, so on POSIX the loop is a no-op and no test
# needs a platform guard. ``SystemRoot`` is the one #209 measured (WinError
# 10106 without it); the rest are the conventional companions — ``TEMP``/``TMP``
# for ``tempfile``, ``PATHEXT`` because 3.11's ``shutil.which`` has no default
# for it, ``ComSpec`` for anything that shells out. Deliberately *not*
# ``os.environ.get(key, "")``: an empty ``%SystemRoot%`` is not the same as an
# absent one to CreateProcess, and an empty string would be planted on POSIX.
_WINDOWS_BOOT_KEYS = (
    "SystemRoot",
    "SystemDrive",
    "windir",
    "ComSpec",
    "PATHEXT",
    "TEMP",
    "TMP",
)


def child_env(home: str | os.PathLike[str], **extra: str) -> dict[str, str]:
    """A hermetic ``env=`` for a REAL child interpreter.

    Carries the parent's ``PATH`` (and ``PYTHONPATH`` when set), the full
    :func:`home_env` for ``home``, and whatever the platform needs to boot a
    Python child (``_WINDOWS_BOOT_KEYS``). Nothing else crosses — no API keys,
    no developer ``~`` — so the child's hermeticity is stated rather than
    inherited. ``extra`` is applied last and wins, so a test can override
    ``PATH`` or add its own seams (``AELIX_CODING_AGENT_DIR``, ``PI_OFFLINE``).
    """

    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if pythonpath := os.environ.get("PYTHONPATH"):
        env["PYTHONPATH"] = pythonpath
    env.update(home_env(home))
    for key in _WINDOWS_BOOT_KEYS:
        if value := os.environ.get(key):
            env[key] = value
    env.update(extra)
    return env
