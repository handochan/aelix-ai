"""The aelix TUI with background jobs on, its keys read from stdin: a test launcher (#777 PR c2).

``python tests/jobs/gated_tui_child.py <aelix arguments>`` is
``tests/jobs/gated_cli.py <aelix arguments>`` — the gate on in this process and
in its delegated children, and the ``AELIX_TEST_ANNOUNCE`` announcement — on the
real CLI's TUI path, with the chrome's input a prompt_toolkit pipe input that
this process's stdin feeds and its output ``DummyOutput``. A test writes keys
to its stdin (``go\\r`` submits a prompt, ``\\x02`` is Ctrl+B) and kills it.

Why a process of its own (design proposal, contract r4 errata E4): moving a
running call into a job (Ctrl+B, ADR-0298) happens only on the TUI host, so a
``-p`` run cannot do it; a pty needs ``os.forkpty``, which is POSIX only; and an
in-process TUI would make the test process the aelix parent that the case kills.

Two things are swapped and nothing else. The CLI picks the TUI only when stdin
is a terminal (``cli/entry.py``, ``resolve_app_mode``), so ``sys.stdin`` is
wrapped to say it is one; nothing on the TUI path reads it otherwise, since the
chrome builds its ``Application`` from the app session's input and output when
it is handed none (``tui/chrome.py``). Test-only, as the launcher is.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any


class _Terminal:
    """``sys.stdin`` as the CLI's mode resolution reads it: a terminal. Everything else is the stream's own."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def isatty(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _forward(fd: int, pipe: Any) -> None:
    """This process's stdin, as it arrives, into the pipe input; ends at EOF."""

    while True:
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            return
        if not chunk:
            return
        pipe.send_bytes(chunk)


def _launcher() -> ModuleType:
    """``gated_cli.py`` beside this file, imported by its path (the repository is not on ``sys.path``)."""

    spec = importlib.util.spec_from_file_location(
        "gated_cli", Path(__file__).resolve().parent / "gated_cli.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    launcher = _launcher()
    launcher.gate()
    launcher.announce("parent")

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    fd = sys.stdin.fileno()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        threading.Thread(
            target=_forward, args=(fd, pipe), name="stdin-to-pipe", daemon=True
        ).start()
        sys.stdin = _Terminal(sys.stdin)

        from aelix_coding_agent.cli.entry import main_sync

        main_sync()


if __name__ == "__main__":
    main()
