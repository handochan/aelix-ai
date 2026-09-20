"""#304 live check — drive the real TUI under a pty and read the child's argv.

WHY A PTY. The defect only exists on the door a HUMAN uses: ``/agents run`` is a
slash command, it fires no hook, and the extension's ``ExtensionContext`` is
therefore ``None`` (first command of a fresh TUI) or stale (right after
``/new``). ``-p`` / ``--mode json`` cannot reach that door — they go through the
model's ``agent`` tool, whose ``tool_call`` hook refreshes the context
immediately before the spawn, which is the one case that always worked.

WHAT IS MEASURED. The CHILD PROCESS'S ARGV, read out of ``ps`` while it runs.
That is the security boundary ADR-0197 §(l) defines and the exact thing the
issue is about: ``--model <the parent's id>`` present, or absent.

  uv run --no-sync python .omc/specs/304-pty-live-check.py [first|new]

``first`` types ``/agents run`` as the very first thing; ``new`` types ``/new``
before it. Both must show the parent's ``--model`` on the child.

REAPING. ``os.kill(pid, 0)`` answers for a ZOMBIE too, so "still running" is
read with ``os.waitpid(pid, os.WNOHANG)`` and every wait loop ends in a
process-group kill.
"""

from __future__ import annotations

import contextlib
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

PARENT_MODEL = "anthropic/claude-haiku-4.5"
PROFILE = "general"  # ``model: inherit`` → the profile declares none
TASK = "Reply with the single word OK and stop."
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][B0]|\x1b[=>]|\r")


def _drain(fd: int, out: bytearray, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        out += chunk


def _descendant_argvs(root: int) -> list[str]:
    """Full argv of every live descendant of ``root`` that is an aelix child.

    ``ps -ww`` because the default width TRUNCATES argv, and a truncated command
    line is exactly the evidence this check cannot afford to lose.
    """

    try:
        listing = subprocess.run(
            ["ps", "-ww", "-eo", "pid=,ppid=,args="],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    rows: list[tuple[int, int, str]] = []
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    tree = {root}
    for _ in range(6):  # ps output is not topologically sorted
        for pid, ppid, _args in rows:
            if ppid in tree:
                tree.add(pid)
    return [a for pid, _p, a in rows if pid in tree and "-m aelix_coding_agent" in a]


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "first"
    root = Path(os.environ.get("TMPDIR", "/tmp")) / f"aelix-304-{mode}-{os.getpid()}"
    project, sessions = root / "project", root / "sessions"
    project.mkdir(parents=True, exist_ok=True)
    sessions.mkdir(parents=True, exist_ok=True)
    (project / "note.txt").write_text("hello\n", encoding="utf-8")

    argv = [
        "uv", "run", "--no-sync", "aelix",
        "--agents", "--provider", "openrouter", "--model", PARENT_MODEL,
        "--session-dir", str(sessions),
    ]
    print(f"parent: {' '.join(argv)}\n  cwd={project}\n  mode={mode}\n")

    pid, fd = pty.fork()
    if pid == 0:  # child: the TUI
        os.chdir(project)
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLUMNS"], os.environ["LINES"] = "160", "48"
        os.execvp(argv[0], argv)
        os._exit(127)  # pragma: no cover

    seen = bytearray()
    child_argvs: list[str] = []
    try:
        _drain(fd, seen, 12.0)  # the TUI boots, extensions load, prompt appears
        if mode == "new":
            os.write(fd, b"/new\r")
            _drain(fd, seen, 6.0)
        os.write(fd, f"/agents run {PROFILE} {TASK}\r".encode())
        # Poll for the child while the delegation runs.
        end = time.monotonic() + 90
        while time.monotonic() < end:
            _drain(fd, seen, 1.0)
            found = _descendant_argvs(pid)
            if found:
                child_argvs = found
                break
        _drain(fd, seen, 20.0)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        # ``os.kill(pid, 0)`` answers for a ZOMBIE, so it cannot be the liveness
        # test here — only a reap can be.
        for _ in range(30):
            done, _status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.2)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(fd)

    text = ANSI.sub("", seen.decode("utf-8", "replace"))
    print("=== the child's command line ===")
    if not child_argvs:
        print("  (no child process was observed)")
    for a in child_argvs:
        print(f"  {a}\n")

    ok = any(f"--model {PARENT_MODEL}" in a for a in child_argvs)
    print(f"--model {PARENT_MODEL} on the child's argv: {'YES' if ok else 'NO'}")

    # The DURABLE half, independent of the ``ps`` poll's timing: #199's own
    # records. ``requested_model`` is what the parent asked the child to run.
    print("\n=== aelix.child_session records in the parent session ===")
    import json

    for path in sorted(sessions.rglob("*.jsonl")):
        if path.parent.name.startswith("2026-") or "sub-" in path.name:
            continue  # the child's own file, not the parent's
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "child_session" not in line:
                continue
            try:
                data = json.loads(line).get("data", {})
            except Exception:  # noqa: BLE001
                continue
            print(
                f"  {data.get('phase'):<8} profile={data.get('profile')!r} "
                f"requested_model={data.get('requested_model')!r} "
                f"status={data.get('status')!r} model={data.get('model')!r} "
                f"error={str(data.get('error'))[:140]!r}"
            )

    print("\n=== the last lines the TUI printed ===")
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    for ln in lines[-14:]:
        print(f"  {ln[:200]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
