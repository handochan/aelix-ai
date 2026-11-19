"""#828 — what ``run_cancellable`` spawns (grep's ``rg``, find's ``fd``) cannot read aelix's stdin.

``run_cancellable`` set no ``stdin``, so its child inherited aelix's fd 0. Since
#123 (ADR-0306 D2) a delegated child's stdin is its parent's command inbox, and
``--mode rpc``'s is the RPC pipe: a child that read stdin took the line meant for
aelix. It now spawns with ``stdin=DEVNULL``, as ``bash.py`` does and as pi's grep
and find tools spawn with ``stdio: ["ignore", "pipe", "pipe"]``.

The shape is #828's: a process whose stdin is a pipe holding one inbox line runs
``run_cancellable``, then reads its own stdin. A Python child that reads stdin
stands in for any reader, so the row runs on every platform; ``rg`` with no path
(the positive control #828 measured) runs where ``rg`` is installed. Fails on
``1c2a4a7c``: the child prints the inbox line and the process is left ``''``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.env_sandbox import child_env

LINE = '{"type":"steer","id":"1","message":"needle line"}\n'

_DRIVER = r"""
import asyncio, json, sys
from aelix_coding_agent.tools._subprocess import run_cancellable
out = asyncio.run(run_cancellable(sys.argv[1:], timeout=60))
left = sys.stdin.read()
print(json.dumps({"child": None if out is None else out[0], "code": None if out is None else out[1], "left": left}))
"""


def _run(tmp_path: Path, argv: list[str]) -> dict[str, object]:
    done = subprocess.run(
        [sys.executable, "-P", "-c", _DRIVER, *argv],
        cwd=tmp_path,
        env=child_env(tmp_path / "home"),
        input=LINE,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_a_child_that_reads_stdin_gets_nothing_and_the_inbox_line_stays(tmp_path: Path) -> None:
    reader = [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"]
    seen = _run(tmp_path, reader)
    assert (str(seen["child"]).strip(), seen["code"], seen["left"]) == ("''", 0, LINE), seen


@pytest.mark.skipif(shutil.which("rg") is None, reason="needs ripgrep on PATH")
def test_a_path_less_rg_reads_no_inbox_line(tmp_path: Path) -> None:
    rg = shutil.which("rg")
    assert rg is not None
    seen = _run(tmp_path, [rg, "--no-config", "needle"])
    # What rg does with a null stdin and no path (its exit code) is rg's; only the line is pinned.
    assert seen["left"] == LINE and "needle" not in str(seen["child"]), seen


# === fix round 1 (Codex review of #828, L13): the extension installer's runner ========

_INSTALLER_DRIVER = r"""
import asyncio, json, sys
from aelix_coding_agent.cli.extension_install import _default_runner
from aelix_coding_agent.rpc.rpc_mode import _open_stdin_reader

async def main():
    reader, _pump = await _open_stdin_reader()  # what --mode rpc and a command inbox open
    # The runner's child shares aelix's stdout; it prints what it read from stdin.
    _default_runner([sys.executable, "-c", "import sys; print('READ', repr(sys.stdin.readline()), flush=True)"])
    left = await asyncio.wait_for(reader.readline(), 30)
    print(json.dumps({"left": left.decode("utf-8", "replace")}), flush=True)

asyncio.run(main())
"""

_POSIX_PIPES = pytest.mark.skipif(sys.platform == "win32", reason="a real child's pipes are POSIX here")


@_POSIX_PIPES
def test_the_installer_runner_reads_no_protocol_line(tmp_path: Path) -> None:
    """An extension calling the installer's Python API under ``--mode rpc`` or a
    command inbox ran pip with aelix's fd 0, so pip's stdin was the protocol pipe.
    Fails on ``1099de2b``: the runner's child prints the inbox line and aelix's
    reader is left with ``''``."""

    done = subprocess.run(
        [sys.executable, "-P", "-c", _INSTALLER_DRIVER],
        cwd=tmp_path,
        env=child_env(tmp_path / "home"),
        input=LINE,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    lines = done.stdout.strip().splitlines()
    assert lines[0] == "READ ''", done.stdout
    assert json.loads(lines[-1]) == {"left": LINE}, done.stdout


def test_a_terminal_launch_keeps_stdin_for_the_installer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before any protocol reader is opened the runner inherits stdin (``None``), so
    ``aelix extension install`` typed at a terminal can still answer pip."""

    from aelix_coding_agent.tools import _subprocess

    monkeypatch.setattr(_subprocess, "_PROTOCOL_STDIN", False)
    assert _subprocess.inherited_stdin() is None
    _subprocess.claim_stdin_for_protocol()
    assert _subprocess.inherited_stdin() == subprocess.DEVNULL
