#!/usr/bin/env python3
"""Drive the REAL Aelix TUI over a pty against a REAL model, and read the screen back.

Some TUI defects are only observable on a live terminal against a live provider.
The one this script was extracted from (#170) is the shape: a live WINDOW is
acquired and never released, so the failure is not "the wrong bytes were
computed" — it is "bytes nobody computed again are still on the glass". A
sink-level assertion cannot see that, because the defect is the ABSENCE of a
later write. Headless tests cover the event sequence; this covers the glass.

Usage (from the repo root, inside the dev environment)::

    python scripts/diag_tui_live.py --model deepseek/deepseek-r1
    python scripts/diag_tui_live.py --model deepseek/deepseek-r1 --interrupt 25

``--interrupt N`` sends Ctrl+C N seconds after the prompt is submitted, which is
how the abort paths are exercised. Pick N from the model, not from habit: a fast
model finishes reasoning in under a second and the interrupt lands in the answer
instead, which looks like a pass.

Reads provider credentials from the environment and, if present, ``.env``.

**The detector has a positive control, and that is not decoration.** Counting an
escape sequence that never matches looks exactly like counting an event that
never happened, and "nothing to report" is the reading that quietly excuses a
gap. The original probe matched ``\\x1b[2;3m`` while Rich was emitting
``\\x1b[0;2;3m``; several runs that really were inside the window were written
off as proving nothing, and the wrong conclusion ("this window is unreachable on
a live provider") went into an ADR before the pattern was checked. So: a zero
count is a HARD FAILURE here, never a quiet zero.
"""

from __future__ import annotations

# POSIX-only diagnostic: it drives a real pty, which Windows has no equivalent
# of. These three imports ARE the runtime guard — on Windows ``fcntl`` does not
# exist and ``pty`` fails importing ``termios``, so the script dies here with an
# ImportError and never reaches ``main``. A ``sys.platform`` bail-out below them
# would be unreachable, so the honest form is the import wall plus the
# ``# pyright: ignore[reportAttributeAccessIssue]`` markers at the use sites:
# those are about the CHECKER — on a windows host pyright analyses this file as
# Windows and loses ``pty.openpty`` / ``fcntl.ioctl`` / ``termios.TIOCSWINSZ``.
import argparse
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time

try:
    import pyte
except ImportError:  # pragma: no cover - dev-only tool
    sys.exit("pyte is required: it ships in the dev group (`uv sync`).")

#: Rich emits a style run either bare or reset-prefixed depending on what
#: preceded it, so match both rather than the one that happened to show up first.
DIM_ITALIC = re.compile(r"\x1b\[[0-9;]*?2;3m")

DEFAULT_PROMPT = (
    "Without using any tools, work out from first principles how many distinct "
    "ways there are to tile a 4x10 rectangle with 1x2 dominoes. Show your "
    "reasoning in full."
)


def _load_dotenv(env: dict[str, str], explicit: str | None, repo: str) -> str | None:
    """Seed provider credentials from a ``.env``, and say which one was used.

    Searched in order: ``--env-file``, ``$PWD/.env``, ``<repo>/.env``. The repo
    here is the checkout the script lives in, which for a git WORKTREE is not the
    checkout holding the (untracked) ``.env`` — assuming otherwise produced
    "No API key for provider" against a perfectly good build.
    """

    for path in (explicit, os.path.join(os.getcwd(), ".env"), os.path.join(repo, ".env")):
        if not path or not os.path.exists(path):
            continue
        with open(path) as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env.setdefault(key, value)
        return path
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, help="provider model id, e.g. deepseek/deepseek-r1")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--cols", type=int, default=90)
    ap.add_argument("--rows", type=int, default=24)
    ap.add_argument("--thinking", default="high")
    ap.add_argument(
        "--interrupt",
        type=float,
        default=None,
        metavar="SECONDS",
        help="send Ctrl+C this many seconds after submitting (default: let the turn finish)",
    )
    ap.add_argument("--settle", type=float, default=6.0, help="seconds to wait for startup")
    ap.add_argument("--budget", type=float, default=120.0, help="max seconds for a full turn")
    ap.add_argument("--dump", metavar="PATH", help="write the raw byte stream here")
    ap.add_argument("--env-file", metavar="PATH", help="dotenv with provider credentials")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = ":".join(
        os.path.join(repo, "packages", pkg, "src")
        for pkg in ("aelix-agent-core", "aelix-coding-agent", "aelix-ai", "aelix-server")
    )
    env["TERM"] = "xterm-256color"
    env.pop("COLUMNS", None)  # let the ioctl decide, the way a real terminal does
    env_file = _load_dotenv(env, args.env_file, repo)

    master, slave = pty.openpty()  # pyright: ignore[reportAttributeAccessIssue]
    fcntl.ioctl(  # pyright: ignore[reportAttributeAccessIssue]
        slave,
        termios.TIOCSWINSZ,  # pyright: ignore[reportAttributeAccessIssue]
        struct.pack("HHHH", args.rows, args.cols, 0, 0),
    )
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "aelix_coding_agent",
            "--model", args.model,
            "--thinking", args.thinking,
        ],
        stdin=slave, stdout=slave, stderr=slave, env=env, cwd=repo, close_fds=True,
    )
    os.close(slave)

    raw = bytearray()

    def pump(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([master], [], [], 0.01)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return
            if not chunk:
                return
            raw.extend(chunk)

    try:
        pump(args.settle)
        os.write(master, args.prompt.encode() + b"\r")
        submitted = len(raw)
        if args.interrupt is not None:
            pump(args.interrupt)
            interrupted_at = len(raw)
            os.write(master, b"\x03")  # Ctrl+C aborts a running turn (chrome.py `c-c`)
            pump(6.0)
        else:
            interrupted_at = None
            pump(args.budget)
    finally:
        try:
            os.write(master, b"\x03")
            time.sleep(0.3)
            os.write(master, b"\x04")
        except OSError:
            pass
        time.sleep(0.5)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.close(master)

    if args.dump:
        with open(args.dump, "wb") as fh:
            fh.write(bytes(raw))

    text = raw.decode("utf-8", "replace")
    repaints = len(DIM_ITALIC.findall(text))

    screen = pyte.Screen(args.cols, args.rows)
    pyte.Stream(screen).feed(text)

    print(f"=== model={args.model}  {args.cols}x{args.rows}  {len(raw)} bytes ===")
    print(f"credentials: {env_file or 'environment only'}")
    print(f"reasoning repaints (dim-italic runs): {repaints}")
    if interrupted_at is not None:
        in_flight = len(DIM_ITALIC.findall(raw[submitted:interrupted_at].decode("utf-8", "replace")))
        print(f"reasoning repaints before the Ctrl+C: {in_flight}")
        if not in_flight:
            print(
                "  !! the interrupt landed OUTSIDE the reasoning window — this run says\n"
                "     nothing about the abort-during-reasoning path. Raise --interrupt,\n"
                "     or use a model that reasons for longer."
            )
    print("--- final painted screen ---")
    for row_no, row in enumerate(screen.display):
        if row.strip():
            print(f"{row_no:2} |{row.rstrip()}")

    if not repaints:
        # Positive control. See the module docstring: a zero here is far more
        # likely to be a broken pattern than a silent renderer, and reading it as
        # "the feature did not run" is how a wrong negative gets written down.
        print(
            "\nFAIL: the dim-italic detector matched nothing in the whole stream.\n"
            "Do NOT read this as 'reasoning never rendered'. Check the pattern against\n"
            "the raw bytes first (`--dump /tmp/raw.bin`), then the renderer.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
