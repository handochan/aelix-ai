"""Exercise idle capturing modals in a 120×40 terminal with the real CLI."""

from __future__ import annotations

import argparse
import os
import runpy
import shlex
import signal
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.env_sandbox import child_env
from tests.jobs.test_gated_cli_611 import ModelServer, Scratch, call, say

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX PTY and TUI signals")

_CAPTURE = (
    Path(__file__).resolve().parents[2] / ".agents/skills/aelix-live-check/scripts/tui_capture.py"
)
_MODALS = [
    ("/model", "Select Model"),
    ("/settings", "Settings — select to change"),
    ("/skill", "── Skills ──"),
    ("/tools", "── Tools ──"),
    ("/agents", "── Agents ──"),
    ("/statusline", "Status line — toggle footer segments"),
    ("/login", "Add a provider"),
]
_EXIT_BUDGET = 10.0
_STARTUP_BUDGET = 60.0
_STEP_BUDGET = 30.0


@contextmanager
def _terminal(
    scratch: Scratch, *extra_args: str
) -> Iterator[tuple[subprocess.Popen[bytes], Any, Any]]:
    if sys.platform == "win32":
        pytest.skip("POSIX PTY")
    capture = argparse.Namespace(**runpy.run_path(str(_CAPTURE)))
    env = child_env(
        scratch.home,
        AELIX_CODING_AGENT_DIR=str(scratch.agent_dir),
        MOCK_KEY="local-test-key",
        TERM="xterm-256color",
        COLUMNS="120",
        LINES="40",
    )
    argv = [
        sys.executable,
        "-P",
        "-m",
        "aelix_coding_agent",
        "--offline",
        "--agents",
        "--provider",
        "mockp",
        "--model",
        "mockm",
        *extra_args,
    ]
    master, slave = os.openpty()
    try:
        try:
            capture.fcntl.ioctl(
                slave, capture.termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0)
            )
            process = subprocess.Popen(
                argv,
                cwd=scratch.cwd,
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
            )
        finally:
            os.close(slave)
        session = capture.Session(master, 120, 40)
        try:
            yield process, session, capture.run_steps
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=_EXIT_BUDGET)
            (scratch.cwd / "raw.bin").write_bytes(session.raw)
    finally:
        os.close(master)


def _show(session: Any, steps: Any, command: str, marker: str) -> None:
    assert not (
        problems := steps(
            session, [f"type:{command}", "key:enter", f"until:{marker}"], _STEP_BUDGET
        )
    ), "\n".join([*problems, *session.screen.display])


def _shutdown(
    process: subprocess.Popen[bytes],
    session: Any,
    scratch: Scratch,
    sig: signal.Signals,
    marker: str,
) -> None:
    rows = [row.rstrip() for row in session.screen.display]
    (scratch.cwd / "screen.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    for row in rows:
        if marker in row:
            print(f"120×40 {sig.name} | {row.strip()}")
    assert process.poll() is None, "CLI exited before the signal"
    assert any(marker in row for row in rows), f"{marker!r} missing before {sig.name}"
    started = time.monotonic()
    process.send_signal(sig)
    while (elapsed := time.monotonic() - started) < _EXIT_BUDGET:
        if (code := process.poll()) is not None:
            print(f"{sig.name} exit={code} after {elapsed:.3f}s")
            assert code == 0
            with pytest.raises(ProcessLookupError):
                os.killpg(process.pid, 0)
            return
        session.pump(0.05)
        if session.closed:
            time.sleep(0.01)
    pytest.fail(f"CLI did not exit within {_EXIT_BUDGET:g}s of {sig.name} with {marker!r} mounted")


@pytest.mark.parametrize("command,marker", _MODALS)
@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGHUP"])
def test_idle_modal_dismisses_on_signal(
    tmp_path: Path, command: str, marker: str, signal_name: str
) -> None:
    scratch = Scratch(tmp_path)
    model = ModelServer(lambda _body, _index: say("unused"))
    scratch.point_at(model)
    with _terminal(scratch) as (process, session, steps), model:
        assert session.pump(_STARTUP_BUDGET, until="Type your message")
        _show(session, steps, command, marker)
        _shutdown(process, session, scratch, signal.Signals[signal_name], marker)


def _job_command(pid_file: Path, stop_file: Path) -> str:
    # The job writes its own PID rather than guessing a Bash wrapper's descendant.
    script = """\
import os, sys, time
from pathlib import Path
pid_file, stop_file = map(Path, sys.argv[1:])
pid_file.write_text(str(os.getpid()))
deadline = time.monotonic() + 60
while not stop_file.exists() and time.monotonic() < deadline:
    time.sleep(0.05)
"""
    return f"exec {shlex.join([sys.executable, '-I', '-c', script, str(pid_file), str(stop_file)])}"


def test_sigterm_with_model_open_stops_background_job(tmp_path: Path) -> None:
    scratch = Scratch(tmp_path)
    pid_file = scratch.cwd / "job.pid"
    stop_file = scratch.cwd / "stop-job"

    def turn(_body: dict[str, Any], index: int) -> dict[str, Any]:
        if index == 0:
            return call(
                "bash", {"command": _job_command(pid_file, stop_file), "run_in_background": True}
            )
        return say("background job submitted")

    model = ModelServer(turn)
    scratch.point_at(model)
    try:
        with _terminal(scratch, "--permission-mode", "yolo") as (process, session, steps), model:
            assert session.pump(_STARTUP_BUDGET, until="Type your message")
            _show(session, steps, "Start the probe job", "background job submitted")
            _show(session, steps, "/model", "Select Model")
            ready_deadline = time.monotonic() + 10.0
            while not session.closed and time.monotonic() < ready_deadline:
                if pid_file.exists() and (pid := pid_file.read_text(encoding="utf-8")).isdigit():
                    job_pid = int(pid)
                    break
                session.pump(0.05)
            else:
                pytest.fail("Background job did not publish its PID before shutdown")
            os.kill(job_pid, 0)
            print(f"background job pid={job_pid} alive with /model open")
            _shutdown(process, session, scratch, signal.SIGTERM, "Select Model")
            with pytest.raises(ProcessLookupError):
                os.kill(job_pid, 0)
            print(f"background job pid={job_pid} absent after shutdown")
    finally:
        stop_file.touch()  # Let our probe exit even if the CLI had to be killed.
