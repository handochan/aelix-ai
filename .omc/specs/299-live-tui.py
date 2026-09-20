#!/usr/bin/env python
"""#299 live driver — type ``!echo MARKER`` into the real TUI, then ask the
real model what it printed.

There is no TTY in the agent session, so the TUI is driven through
``os.forkpty``. ``--no-tools`` is not decoration: with the bash tool available
the model can answer by re-running the command, and the run would prove
nothing about the session context. The only way it can name the marker is if
the ``!`` output reached its messages.

Reaping is ``os.waitpid(pid, os.WNOHANG)``; ``os.kill(pid, 0)`` succeeds on a
zombie and would report a finished child as still running.

Run (``AELIX_299_SESSION_DIR`` is what makes the second line resume the first)::

    uv run --no-sync python .omc/specs/299-live-tui.py              # ! reaches the model
    uv run --no-sync python .omc/specs/299-live-tui.py --continue   # ...and after a resume
    uv run --no-sync python .omc/specs/299-live-tui.py --transient  # !! still does not
    uv run --no-sync python .omc/specs/299-live-tui.py --big        # capped, and it SAYS so
    uv run --no-sync python .omc/specs/299-live-tui.py --blank      # …ending in a blank line
    … --continue --dump   # + the whole screen, to watch the replay draw the record

``--big`` is the half a live run of ``echo`` cannot reach. The record is capped
(2000 lines / 50KB), so what the model gets back is the TAIL plus a bracketed
notice — and the point of the notice is that the model can tell it was cut
rather than reading the tail as the whole output. Both halves are asked in one
turn: the marker is the last line, so naming it proves the tail survived, and
the notice question proves the cut is visible.

``--blank`` is ``--big`` with one more ``print()`` at the end — an ordinary
thing for a command to do, and the shape the first cut of the cap lost
ENTIRELY. A trailing blank line is an empty element that costs 0 bytes, so
``truncate_tail`` kept it and dropped the real output: the model was told
``(no output)`` for 300KB the user had just watched go past. It asks ``--big``'s
two questions and the passing answer is the same.
"""

from __future__ import annotations

import contextlib
import os
import pty
import re
import select
import shutil
import signal
import struct
import sys
import tempfile
import termios
import time
from fcntl import ioctl

MARKER = "MARKER-299-LIVE"
QUESTION = (
    "Without running anything, what exact string did the echo command in this "
    "conversation print? If you cannot see any command output, reply NO-OUTPUT-VISIBLE."
)
#: ``--big``: 300KB on one line, then the marker on the last. The cap keeps the
#: tail, so a model that can name the marker read the TRUNCATED record.
BIG_COMMAND = f"python3 -c \"print('z'*300000); print('{MARKER}')\""
#: ``--blank``: the same, plus the trailing blank line that used to take the
#: whole record with it.
BLANK_COMMAND = f"python3 -c \"print('z'*300000); print('{MARKER}'); print()\""
BIG_QUESTION = (
    "Without running anything, answer both in two lines. (1) What exact string "
    "was the LAST line the command in this conversation printed? If you cannot "
    "see any command output at all, say NO-OUTPUT-VISIBLE. (2) Does the record "
    "of that command say that its output was shortened or truncated? Answer "
    "TRUNCATION-NOTED or NO-TRUNCATION-NOTE."
)
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][B0]")


def strip(text: str) -> str:
    return ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


class Tui:
    def __init__(self, argv: list[str], cwd: str) -> None:
        self.buf = ""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            os.chdir(cwd)
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLUMNS"] = "120"
            os.environ["LINES"] = "40"
            os.execvp(argv[0], argv)
        ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    def pump(self, seconds: float) -> str:
        """Read for ``seconds``, appending to the buffer; return what arrived."""

        got = ""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            got += chunk.decode("utf-8", "replace")
        self.buf += got
        return got

    def wait_for(self, needle: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if needle in strip(self.buf):
                return True
            self.pump(0.3)
        return needle in strip(self.buf)

    def send(self, line: str) -> None:
        os.write(self.fd, line.encode())

    def close(self) -> int | None:
        with contextlib.suppress(OSError):
            os.kill(self.pid, signal.SIGTERM)
        end = time.monotonic() + 5
        while time.monotonic() < end:
            reaped, status = os.waitpid(self.pid, os.WNOHANG)
            if reaped == self.pid:
                with contextlib.suppress(OSError):
                    os.close(self.fd)
                return status
            self.pump(0.2)
        with contextlib.suppress(OSError):
            os.kill(self.pid, signal.SIGKILL)
        os.waitpid(self.pid, 0)
        return None


def main() -> int:
    resume = "--continue" in sys.argv[1:]
    # ``--transient`` drives the OTHER half: ``!!`` must stay invisible to the
    # model. A fix that only proves ``!`` arrives has broken what it fixed.
    transient = "--transient" in sys.argv[1:]
    # ``--big`` drives the cap: the record is the TAIL plus a notice, and the
    # model has to be able to see both. ``--blank`` is the same command with a
    # trailing blank line, which is where the first cut of the cap recorded
    # ``(no output)`` for the whole 300KB.
    blank = "--blank" in sys.argv[1:]
    big = blank or "--big" in sys.argv[1:]
    question = BIG_QUESTION if big else QUESTION
    session_dir = os.environ.get("AELIX_299_SESSION_DIR") or tempfile.mkdtemp(
        prefix="aelix-299-"
    )
    cwd = os.environ.get("AELIX_299_CWD") or os.getcwd()
    argv = [
        "uv",
        "run",
        "--no-sync",
        "aelix",
        "--provider",
        "anthropic",
        "--model",
        "claude-haiku-4-5",
        "--no-tools",
        "--no-extensions",
        "--session-dir",
        session_dir,
    ]
    if resume:
        argv.append("--continue")

    print(f"[driver] session-dir={session_dir} continue={resume}")
    tui = Tui(argv, cwd)
    try:
        tui.pump(12.0)  # startup: credentials notice, chrome, first paint
        if not resume:
            prefix = "!!" if transient else "!"
            command = (
                BLANK_COMMAND if blank else BIG_COMMAND if big else f"echo {MARKER}"
            )
            tui.send(f"{prefix}{command}\r")
            # 300KB through a pty takes longer to paint than one echo line.
            if not tui.wait_for(MARKER, 120 if big else 60):
                print("[driver] FAILED: the TUI never echoed the marker")
                return 2
            tui.pump(4.0 if big else 2.0)
        before = len(tui.buf)
        tui.send(question + "\r")
        # The reply is done when the model has said one of the two things, or
        # when the model went quiet for a while.
        end = time.monotonic() + 120
        while time.monotonic() < end:
            tail = strip(tui.buf[before:])
            if MARKER in tail or "NO-OUTPUT-VISIBLE" in tail:
                tui.pump(3.0)
                break
            tui.pump(1.0)
        answer = strip(tui.buf[before:])
        tui.send("/quit\r")
        tui.pump(3.0)
    finally:
        status = tui.close()

    print(f"[driver] child status={status}")
    print("[driver] ===== model's answer, verbatim =====")
    print(answer.strip())
    print("[driver] ===== end =====")
    if "--dump" in sys.argv[1:]:
        print("[driver] ===== whole screen buffer =====")
        print(strip(tui.buf))
        print("[driver] ===== end buffer =====")
    saw = MARKER in answer
    print(f"[driver] marker reached the model: {saw}")
    print(f"[driver] session files: {sorted(p for p in _walk(session_dir))}")
    for path in _walk(session_dir):
        print(f"[driver] session bytes: {os.path.getsize(path)} {path}")
    if transient:
        print(f"[driver] verdict: {'BROKEN — !! leaked' if saw else 'OK — !! excluded'}")
        return 1 if saw else 0
    if big:
        noted = "TRUNCATION-NOTED" in answer
        print(f"[driver] truncation visible to the model: {noted}")
        print(
            "[driver] verdict: "
            + ("OK — tail kept and the cut is stated" if saw and noted else "BROKEN")
        )
        return 0 if (saw and noted) else 1
    return 0 if saw else 1


def _walk(root: str) -> list[str]:
    out: list[str] = []
    for base, _dirs, files in os.walk(root):
        out.extend(os.path.join(base, f) for f in files)
    return out


if __name__ == "__main__":
    assert shutil.which("uv"), "uv is required"
    sys.exit(main())
