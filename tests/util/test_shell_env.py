"""P0 #3 HEAVY (ADR-0139) — ``shell_env.get_shell_env`` + ``config.get_bin_dir``.

Pi citation: ``utils/shell.ts:108-120`` (``getShellEnv``) +
``config.ts:483-485`` (``getBinDir``) at SHA
``734e08edf82ff315bc3d96472a6ebfa69a1d8016``.
"""

from __future__ import annotations

import os

from aelix_coding_agent.cli.config import get_agent_dir, get_bin_dir
from aelix_coding_agent.util.shell_env import get_shell_env


def test_get_bin_dir_under_agent_dir():
    assert get_bin_dir() == os.path.join(get_agent_dir(), "bin")


def test_get_shell_env_prepends_bin_dir(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = get_shell_env()
    entries = env["PATH"].split(os.pathsep)
    assert entries[0] == get_bin_dir()
    assert "/usr/bin" in entries and "/bin" in entries


def test_get_shell_env_idempotent(monkeypatch):
    monkeypatch.setenv("PATH", f"{get_bin_dir()}:/usr/bin")
    env = get_shell_env()
    # Already present → not duplicated.
    assert env["PATH"].split(os.pathsep).count(get_bin_dir()) == 1


def test_get_shell_env_preserves_other_vars(monkeypatch):
    monkeypatch.setenv("MY_CUSTOM_VAR", "hello-aelix")
    env = get_shell_env()
    assert env["MY_CUSTOM_VAR"] == "hello-aelix"
