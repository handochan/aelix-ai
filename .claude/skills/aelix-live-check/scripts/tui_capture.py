"""Drive the real aelix TUI in a pty, isolated in a scratch directory, and save what it painted.

POSIX only (``os.forkpty``): on Windows it exits with a message, and the
POSIX-only calls sit behind ``sys.platform`` checks so that
``pyright --pythonplatform Windows`` passes over it clean
(``tests/test_agent_skills.py``). Run it from the repository root with the dev
environment, so ``sys.executable`` is the checkout's interpreter and ``pyte`` is
installed::

    uv run python .agents/skills/aelix-live-check/scripts/tui_capture.py \
        --scratch /tmp/live-123 --step wait:6 --step type:/skill --step key:enter \
        --step wait:2 --step key:escape --step type:/quit --step key:enter \
        --expect "Skills" -- --offline

Everything after ``--`` is passed to ``python -m aelix_coding_agent``.

ISOLATION. The child gets a scratch ``HOME`` (``USERPROFILE`` too), a scratch
``AELIX_CODING_AGENT_DIR`` and a scratch cwd, all under ``--scratch``. Its
environment is built from scratch rather than copied from yours, so a provider key
exported in your shell does not reach it. ``--key NAME`` copies one variable by
name, from your environment or from ``--dotenv FILE``, without printing it.
``--cwd`` runs it somewhere else (for example the checkout, to see what aelix does
there). Then do not send a tool-using prompt, and do not pass ``yolo`` or ``auto``.

STEPS, run in order:
  ``wait:SECONDS``  read output for that long
  ``type:TEXT``     type the text (no Enter)
  ``key:NAME``      one of enter, escape, tab, up, down, left, right, backspace,
                    ctrl-c, ctrl-d
  ``until:TEXT``    read until TEXT is on the screen (gives up after --budget)

POSITIVE CONTROL. ``--expect TEXT`` (repeatable) must be on the final screen, and
``--count REGEX`` (repeatable) must match the raw byte stream at least once.
Either failing is exit 1. A zero count usually means the pattern is wrong, not
that nothing happened (``scripts/diag_tui_live.py`` has the story).

OUTPUT, under ``--scratch``: ``raw.bin`` (every byte the child wrote) and
``screen.txt`` (the final screen, one row per line, ANSI stripped). pyte rows are
always exactly COLS cells wide, so "no row wider than the terminal" proves
nothing. Measure width in-process with ``rich.cells.cell_len`` instead.

ENDING. Finish with a quit step (``type:/quit`` then ``key:enter``), after closing
any panel. If the child is still running when the steps end, this script sends it
Ctrl+D, then SIGTERM, then SIGKILL, and only to the one process it started. At an
idle prompt Ctrl+D quits. With the ``/skill`` panel open, aelix outlived SIGTERM by
20 s in 3 of 3 runs (measured 2026-11-09), so such a run ends on SIGKILL and its
exit status reads -9.
"""

from __future__ import annotations

import argparse
import os
import re
import select
import signal
import struct
import sys
import time
from pathlib import Path

try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover - Windows has no pty
    sys.exit("tui_capture.py needs a POSIX pty (macOS or Linux).")

try:
    import pyte
except ImportError:  # pragma: no cover - dev-only tool
    sys.exit("pyte is required: it ships in the dev dependency group (`uv sync`).")

KEYS = {
    "enter": b"\r",
    "escape": b"\x1b",
    "tab": b"\t",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "backspace": b"\x7f",
    "ctrl-c": b"\x03",
    "ctrl-d": b"\x04",
}

CPR_REQUEST = b"\x1b[6n"  # prompt_toolkit asks where the cursor is, and warns if nobody answers

PASSED_THROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT")


def read_dotenv(path: Path, name: str) -> str | None:
    """The value of one variable in a dotenv file, or None. Nothing else is read out."""

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            return value.strip().strip("'\"")
    return None


def child_env(args: argparse.Namespace, scratch: Path) -> dict[str, str]:
    home = scratch / "home"
    agent_dir = scratch / "agent"
    home.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    env = {k: os.environ[k] for k in PASSED_THROUGH if k in os.environ}
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        AELIX_CODING_AGENT_DIR=str(agent_dir),
        TERM="xterm-256color",
        COLUMNS=str(args.cols),
        LINES=str(args.rows),
    )
    for name in args.key:
        value = os.environ.get(name)
        if value is None and args.dotenv:
            value = read_dotenv(Path(args.dotenv), name)
        if value is None:
            sys.exit(f"--key {name}: not in the environment or the dotenv file")
        env[name] = value
    return env


class Session:
    def __init__(self, fd: int, cols: int, rows: int) -> None:
        self.fd = fd
        self.raw = bytearray()
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.closed = False

    def pump(self, seconds: float, until: str | None = None) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.closed:
            if until is not None and until in "\n".join(self.screen.display):
                return True
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                self.closed = True
                break
            self.raw.extend(chunk)
            self.stream.feed(chunk)
            if CPR_REQUEST in chunk:  # answer the way a real terminal does
                cursor = self.screen.cursor
                self.send(f"\x1b[{cursor.y + 1};{cursor.x + 1}R".encode())
        return until is not None and until in "\n".join(self.screen.display)

    def send(self, data: bytes) -> None:
        if not self.closed:
            os.write(self.fd, data)


def run_steps(session: Session, steps: list[str], budget: float) -> list[str]:
    problems: list[str] = []
    for step in steps:
        kind, _, value = step.partition(":")
        if kind == "wait":
            session.pump(float(value))
        elif kind == "type":
            session.send(value.encode("utf-8"))
            session.pump(0.2)
        elif kind == "key":
            if value not in KEYS:
                sys.exit(f"unknown key {value!r}; known: {', '.join(KEYS)}")
            session.send(KEYS[value])
            session.pump(0.3)
        elif kind == "until":
            if not session.pump(budget, until=value):
                problems.append(f"until:{value} never appeared within {budget:.0f} s")
        else:
            sys.exit(f"unknown step {step!r}")
    return problems


def stop_child(pid: int, session: Session) -> int:
    """End the one process this script started. Never by name."""

    if sys.platform == "win32":  # pragma: no cover - main() never gets here on Windows
        raise RuntimeError("POSIX only")
    for data, grace in ((KEYS["ctrl-d"], 3.0), (None, 5.0)):
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return status
        if data is not None:
            session.send(data)
        else:
            os.kill(pid, signal.SIGTERM)
        session.pump(grace)
    done, status = os.waitpid(pid, os.WNOHANG)
    if not done:
        os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
    return status


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--scratch", required=True, help="scratch dir for HOME, agent dir, cwd and output"
    )
    ap.add_argument("--cwd", help="run the child here instead of <scratch>/cwd")
    ap.add_argument("--cols", type=int, default=120)
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument(
        "--step", action="append", default=[], help="wait:S | type:TEXT | key:NAME | until:TEXT"
    )
    ap.add_argument("--budget", type=float, default=60.0, help="seconds an until: step may take")
    ap.add_argument(
        "--key", action="append", default=[], help="copy this variable to the child by name"
    )
    ap.add_argument("--dotenv", help="dotenv file to read --key values from")
    ap.add_argument("--expect", action="append", default=[], help="text the final screen must hold")
    ap.add_argument("--count", action="append", default=[], help="regex the raw bytes must match")
    ap.add_argument("aelix_args", nargs="*", help="arguments for aelix, after --")
    args = ap.parse_args()
    if sys.platform == "win32":  # pragma: no cover - the fcntl import already exited
        sys.exit("tui_capture.py needs a POSIX pty (macOS or Linux).")

    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    cwd = Path(args.cwd).resolve() if args.cwd else scratch / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    env = child_env(args, scratch)
    argv = [sys.executable, "-m", "aelix_coding_agent", *args.aelix_args]

    pid, fd = os.forkpty()
    if pid == 0:  # the child: size the pty before aelix asks for it
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", args.rows, args.cols, 0, 0))
            os.chdir(cwd)
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)

    session = Session(fd, args.cols, args.rows)
    started = time.monotonic()
    try:
        problems = run_steps(session, args.step, args.budget)
    finally:
        status = stop_child(pid, session)
        os.close(fd)

    (scratch / "raw.bin").write_bytes(bytes(session.raw))
    rows = [row.rstrip() for row in session.screen.display]
    (scratch / "screen.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    text = "\n".join(rows)
    raw_text = session.raw.decode("utf-8", "replace")
    for want in args.expect:
        if want not in text:
            problems.append(f"expected text not on the final screen: {want!r}")
    for pattern in args.count:
        hits = len(re.findall(pattern, raw_text))
        print(f"count {pattern!r}: {hits}")
        if not hits:
            problems.append(f"positive control matched nothing: {pattern!r}")

    print(
        f"=== {args.cols}x{args.rows}, {len(session.raw)} bytes, {time.monotonic() - started:.1f} s, "
        f"exit status {os.waitstatus_to_exitcode(status)} ==="
    )
    for number, row in enumerate(rows):
        if row:
            print(f"{number:2} |{row}")
    print(f"saved {scratch / 'raw.bin'} and {scratch / 'screen.txt'}")
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
