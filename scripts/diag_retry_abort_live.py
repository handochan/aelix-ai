#!/usr/bin/env python3
"""Drive the REAL Aelix TUI over a pty and interrupt it during an auto-retry request.

#147's precondition cannot be produced by a real provider on demand — the issue's
own repro built it, with a proxy that fails attempt 1 and then HOLDS the retry
connection. This script does the same, against the real ``python -m
aelix_coding_agent``: a local provider on 127.0.0.1, reached through a temporary
``models.json``, so every layer below the fake body is production code (model
registry, harness retry loop, openai SDK, httpx, prompt-toolkit, Rich).

Two facts are read back, from two different places, because they fail
differently:

  THE GLASS  — pyte replays the child's escape stream; the final screen must
               carry the abort notice, not a live ``Working…``.
  THE WIRE   — the local provider reports whether aelix's socket actually went
               away. A REPL that frees up while the connection lingers is
               exactly the residual #147 was left open on, and only the server
               can tell those apart.

Usage (repo root, dev environment)::

    python scripts/diag_retry_abort_live.py                 # interrupt the retry
    python scripts/diag_retry_abort_live.py --no-interrupt  # POSITIVE CONTROL

``--no-interrupt`` is not decoration. It must end with the turn still stuck and
the connection still open; if it does not, the rig never built the precondition
and a pass in the other mode means nothing. A prior #147 live rig passed against
the BROKEN build because the ``openai`` SDK's own ``DEFAULT_MAX_RETRIES = 2``
absorbed the 5xx it was firing, so aelix's retry loop never engaged and the
"retry" being interrupted did not exist. The trigger here is one the SDK does NOT
retry: a 200 whose SSE body simply ends with no ``finish_reason``, which is what
``_RETRYABLE_ERROR_PATTERN`` matches on aelix's side.

WHAT A PASS HERE DOES **NOT** MEAN. Measured: this script passes identically on
main @ ``7815cc6``, i.e. on the build WITHOUT the causal interrupt handover that
ADR-0225 adds. That is expected and is stated here so nobody later reads a green
run as covering it. On an idle machine the old timer-driven handover beats the
retry request to the wire by about 2 ms, so the window it leaves is not reachable
from outside the process; it only opens under event-loop pressure, where it was
measured at 336 ms (150 ms/frame) and 1237 ms (400 ms/frame). What this script
DOES establish is the fact #147 was left open on — that an abort during an
in-flight retry reaches the transport and the provider's connection really goes.
The handover itself is pinned by the sabotage-verified tests in
``tests/tui/test_run_tui_smoke.py``.
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
import asyncio
import contextlib
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

try:
    import pyte
except ImportError:  # pragma: no cover - dev-only tool
    sys.exit("pyte is required: it ships in the dev group (`uv sync`).")

_CHUNK = (
    'data: {"id":"c","object":"chat.completion.chunk","created":0,"model":"m",'
    '"choices":[{"index":0,"delta":{"content":"thinking"},"finish_reason":null}]}\n\n'
)


class HoldingProvider:
    """Attempt 1 ends retryably; attempt 2+ is held open until the peer leaves."""

    def __init__(self) -> None:
        self.port = 0
        self.requests = 0
        self.retry_in_flight = threading.Event()
        self.peer_gone = threading.Event()
        self._ready = threading.Event()

    async def _handle(self, reader, writer) -> None:  # type: ignore[no-untyped-def]
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            n = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":")[1])
            if n:
                await reader.readexactly(n)
            self.requests += 1
            me = self.requests
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            payload = _CHUNK.encode()
            writer.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            await writer.drain()
            if me == 1:
                # Retryable on aelix's side, NOT retried by the openai SDK.
                writer.write(b"0\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            self.retry_in_flight.set()
            if await reader.read(1) == b"":
                self.peer_gone.set()
        except Exception:  # noqa: BLE001 — a reset is the peer leaving too
            self.peer_gone.set()
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    def _serve(self) -> None:
        async def run() -> None:
            server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            async with server:
                await server.serve_forever()

        asyncio.run(run())

    def start(self) -> None:
        threading.Thread(target=self._serve, daemon=True).start()
        if not self._ready.wait(timeout=10):
            raise SystemExit("the local provider never bound a port")


def write_models_json(agent_dir: str, port: int) -> None:
    path = os.path.join(agent_dir, "models.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "providers": {
                    "retryprobe": {
                        "name": "Retry Probe",
                        "api": "openai-completions",
                        "baseUrl": f"http://127.0.0.1:{port}/v1",
                        "apiKey": "RETRYPROBE_KEY",
                        "models": [
                            {
                                "id": "held-model",
                                "reasoning": False,
                                "input": ["text"],
                                "cost": {
                                    "input": 0.0, "output": 0.0,
                                    "cacheRead": 0.0, "cacheWrite": 0.0,
                                },
                                "contextWindow": 128000,
                                "maxTokens": 4096,
                            }
                        ],
                    }
                }
            },
            fh,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="say hello")
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=24)
    ap.add_argument("--settle", type=float, default=8.0)
    ap.add_argument(
        "--no-interrupt", action="store_true",
        help="POSITIVE CONTROL: never send Ctrl+C; the turn must stay stuck",
    )
    ap.add_argument("--hold", type=float, default=12.0,
                    help="seconds to observe after the retry request goes out")
    ap.add_argument("--dump", metavar="PATH")
    args = ap.parse_args()

    provider = HoldingProvider()
    provider.start()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    agent_dir = tempfile.mkdtemp(prefix="aelix-retry-probe-")
    write_models_json(agent_dir, provider.port)

    env = dict(os.environ)
    env["PYTHONPATH"] = ":".join(
        os.path.join(repo, "packages", pkg, "src")
        for pkg in ("aelix-agent-core", "aelix-coding-agent", "aelix-ai", "aelix-server")
    )
    env["TERM"] = "xterm-256color"
    env["AELIX_CODING_AGENT_DIR"] = agent_dir
    env["RETRYPROBE_KEY"] = "sk-local-probe"
    env["AELIX_OFFLINE"] = "1"  # no catalog fetch; the probe provider is enough
    env.pop("COLUMNS", None)

    master, slave = pty.openpty()  # pyright: ignore[reportAttributeAccessIssue]
    fcntl.ioctl(  # pyright: ignore[reportAttributeAccessIssue]
        slave,
        termios.TIOCSWINSZ,  # pyright: ignore[reportAttributeAccessIssue]
        struct.pack("HHHH", args.rows, args.cols, 0, 0),
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "aelix_coding_agent", "--model", "retryprobe/held-model"],
        stdin=slave, stdout=slave, stderr=slave, env=env, cwd=repo, close_fds=True,
    )
    os.close(slave)

    raw = bytearray()

    def pump(seconds: float, until=None) -> None:  # type: ignore[no-untyped-def]
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if until is not None and until():
                return
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

    interrupted = False
    # Snapshotted BEFORE the teardown below. The teardown sends Ctrl+C, Ctrl+D
    # and finally terminates the child — all of which close the socket, so
    # reading ``peer_gone`` or the screen afterwards measures the teardown and
    # reports every run as a teardown, control included. That is exactly how the
    # first version of this script declared its own positive control broken.
    observed = {"peer_gone": False, "bytes": 0}
    try:
        pump(args.settle)
        os.write(master, args.prompt.encode() + b"\r")
        # Wait for the RETRY request specifically — attempt 1 must have failed
        # and attempt 2 must be on the wire before the interrupt means anything.
        pump(60.0, until=provider.retry_in_flight.is_set)
        reached = provider.retry_in_flight.is_set()
        if reached and not args.no_interrupt:
            pump(1.5)  # let the chrome settle into the in-flight state
            os.write(master, b"\x03")  # Ctrl+C — chrome.py binds `c-c` to abort
            interrupted = True
        pump(args.hold, until=(provider.peer_gone.is_set if interrupted else None))
        if interrupted:
            pump(2.0)
        observed["peer_gone"] = provider.peer_gone.is_set()
        observed["bytes"] = len(raw)
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

    # Replay only the bytes seen INSIDE the observation window, for the same
    # reason ``observed`` is snapshotted: the teardown repaints the screen.
    text = raw[: observed["bytes"] or len(raw)].decode("utf-8", "replace")
    screen = pyte.Screen(args.cols, args.rows)
    pyte.Stream(screen).feed(text)
    display = list(screen.display)
    joined = "\n".join(display)

    mode = "POSITIVE CONTROL (no interrupt)" if args.no_interrupt else "interrupt the retry"
    print(f"=== {mode}  {args.cols}x{args.rows}  {len(raw)} bytes ===")
    print(f"provider requests served      : {provider.requests}")
    print(f"retry request reached the wire: {provider.retry_in_flight.is_set()}")
    print(f"aelix's socket went away      : {observed['peer_gone']}  (observed before teardown)")
    print("--- final painted screen ---")
    for i, row in enumerate(display):
        if row.strip():
            print(f"{i:2} |{row.rstrip()}")

    if not provider.retry_in_flight.is_set():
        print(
            "\n  !! THE RIG NEVER BUILT THE PRECONDITION. The retry request never\n"
            "     reached the wire, so nothing here says anything about #147.\n"
            "     Do NOT read this as a pass. Check that attempt 1 was classified\n"
            f"     retryable (requests served: {provider.requests})."
        )
        return 2

    aborted = "Operation aborted" in joined
    working = "Working…" in joined
    if args.no_interrupt:
        ok = not observed["peer_gone"] and not aborted
        print(
            f"\n  control: connection still open = {not observed['peer_gone']}, "
            f"no abort notice = {not aborted}, spinner up = {working}"
            f" -> {'OK' if ok else 'RIG BROKEN'}"
        )
        return 0 if ok else 1

    ok = aborted and observed["peer_gone"]
    print(f"\n  abort notice on the glass = {aborted}")
    print(f"  provider socket torn down = {observed['peer_gone']}")
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
