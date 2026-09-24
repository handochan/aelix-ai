"""Type Ctrl+C into a pseudo-terminal that runs a command; count how many end it.

Committed with #334 (``.omc/specs/334-pty-ctrl-c.py``, with the parking
extension ``.omc/specs/334-park-ext.py``) for its ``-p`` Ctrl+C check: a
``-p`` run whose ``input`` or ``before_agent_start`` handler parks must still
end on one Ctrl+C. From a scratch cwd, per hook::

    PARK_EVENT=input PARK_MARK=/tmp/m uv run --no-sync --project <tree> \
      python <tree>/.omc/specs/334-pty-ctrl-c.py --ready-file /tmp/m -- \
      <tree>/.venv/bin/aelix -e <tree>/.omc/specs/334-park-ext.py -p hello

usage: pty_ctrl_c.py --ready-file PATH | --ready-text TEXT  [--gap S] [--max N] -- CMD...

The command runs as the session leader of a fresh pty (``pty.fork``), so it and
everything it spawns in its process group form the terminal's foreground
process group. Writing ``\\x03`` to the master is what a keyboard Ctrl+C is: the
line discipline (ISIG on, as a fresh pty has it) sends SIGINT to that whole
group. Nothing here sends a signal itself.

After the ready condition, one ``\\x03`` is typed; the driver then waits up to
``--gap`` seconds for the child to exit and types the next one only if it has
not, up to ``--max``; if it is still alive after that, SIGKILL to the group.
Prints per Ctrl+C whether the command ended, the exit status, the elapsed time
from the FIRST Ctrl+C, and the tail of what the pty showed.
"""

from __future__ import annotations

import argparse
import os
import pty
import select
import signal
import sys
import termios
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ready-file")
    ap.add_argument("--ready-text")
    ap.add_argument("--gap", type=float, default=10.0)
    ap.add_argument("--max", type=int, default=4)
    ap.add_argument("--ready-timeout", type=float, default=60.0)
    ap.add_argument("--tail", type=int, default=12)
    ap.add_argument("--save", help="write the whole pty transcript here")
    ap.add_argument("--send-line", help="type this line into the pty after 2 s (an RPC command)")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if a.ready_file and os.path.exists(a.ready_file):
        os.remove(a.ready_file)

    pid, fd = pty.fork()
    if pid == 0:  # child: session leader, pty is the controlling terminal
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.execvp(cmd[0], cmd)

    attrs = termios.tcgetattr(fd)
    isig = bool(attrs[3] & termios.ISIG)
    print(f"pty child pid={pid}; ISIG on the pty: {isig}; VINTR={attrs[6][termios.VINTR]!r}")
    buf = bytearray()

    def pump(timeout: float) -> None:
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            r, _, _ = select.select([fd], [], [], min(left, 0.05))
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                buf.extend(data)

    def reaped() -> int | None:
        try:
            wpid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return -999
        if wpid == 0:
            return None
        return status

    t_start = time.monotonic()
    if a.send_line:
        pump(2.0)
        os.write(fd, a.send_line.encode() + b"\n")
    while True:
        pump(0.1)
        ready = (a.ready_file and os.path.exists(a.ready_file)) or (
            a.ready_text and a.ready_text.encode() in buf
        )
        if ready:
            break
        st = reaped()
        if st is not None:
            print(f"child ended BEFORE ready: status {st}")
            print(buf.decode(errors="replace")[-2000:])
            return 2
        if time.monotonic() - t_start > a.ready_timeout:
            print("never ready; killing")
            os.killpg(pid, signal.SIGKILL)
            print(buf.decode(errors="replace")[-2000:])
            return 2
    print(f"ready after {time.monotonic() - t_start:.1f} s")
    pump(0.3)  # let the process settle in its await

    t0 = time.monotonic()
    status = None
    for n in range(1, a.max + 1):
        os.write(fd, b"\x03")
        end = time.monotonic() + a.gap
        while time.monotonic() < end:
            pump(0.1)
            status = reaped()
            if status is not None:
                break
        if status is not None:
            how = (
                f"exit code {os.waitstatus_to_exitcode(status)}"
                if status != -999
                else "already reaped"
            )
            print(f"Ctrl+C #{n}: ENDED ({how}) {time.monotonic() - t0:.2f} s after Ctrl+C #1")
            break
        print(f"Ctrl+C #{n}: still running {a.gap:.0f} s later")
    else:
        os.killpg(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        print(f"still running after {a.max} Ctrl+C: SIGKILL")
    pump(0.5)
    lines = buf.decode(errors="replace").replace("\r", "").splitlines()
    if a.save:
        with open(a.save, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"--- pty output, last {a.tail} lines:")
    for ln in lines[-a.tail :]:
        print("   ", ln[:220])
    return 0


if __name__ == "__main__":
    sys.exit(main())
