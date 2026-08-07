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
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

__all__ = ["sandbox_home"]


def sandbox_home(
    monkeypatch: pytest.MonkeyPatch, home: str | os.PathLike[str]
) -> Path:
    """Point every home-directory environment variable at ``home``.

    Returns the sandbox as a :class:`~pathlib.Path` so a caller can keep using
    it (``sandbox_home(monkeypatch, tmp_path / "home") / ".aelix"``).

    The directory is NOT created — callers that need it on disk create it, and
    the several tests that assert on first-run behaviour depend on it being
    absent.
    """

    home_path = Path(home)
    text = str(home_path)

    # POSIX: ``posixpath.expanduser`` reads $HOME.
    monkeypatch.setenv("HOME", text)

    # Windows: ``ntpath.expanduser`` reads %USERPROFILE% first ...
    monkeypatch.setenv("USERPROFILE", text)
    # ... and falls back to %HOMEDRIVE% + %HOMEPATH%. Split with the *native*
    # ``os.path.splitdrive`` so the pair reassembles to ``text`` on whichever
    # platform is running; on POSIX the drive is empty and the pair is inert.
    drive, tail = os.path.splitdrive(text)
    monkeypatch.setenv("HOMEDRIVE", drive)
    monkeypatch.setenv("HOMEPATH", tail)

    # Windows per-user configuration roots, mirroring the real layout under
    # the profile so a leaked write lands in the sandbox rather than in
    # C:\\Users\\runner\\AppData.
    monkeypatch.setenv("APPDATA", str(home_path / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home_path / "AppData" / "Local"))

    return home_path
