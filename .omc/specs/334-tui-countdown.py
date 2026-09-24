#!/usr/bin/env python3
"""#334 — where does a line typed during the TUI's retry countdown go?

Committed as ``.omc/specs/334-tui-countdown.py`` (the #334 lane's
``tui_countdown_raw.py``); the TUI check in ADR-0023's #334 amendment is its
output. ``RAW_OUT=<file>`` also saves the raw pty bytes.

Adapted from ``scripts/diag_retry_abort_live.py``: the REAL TUI (``python -m
aelix_coding_agent``) in a pty, talking to a local provider on 127.0.0.1 through
a temporary ``models.json``. Attempt 1 ends retryably (an SSE body with no
``finish_reason``, which the openai SDK does not retry and aelix does); attempt
2+ answers normally and the provider RECORDS each request body. The harness's
own backoff is the real one (2 s for attempt 1).

Once the "Retrying (1/3) in …" widget is on the glass, the driver types
``BRAVO-LINE`` + Enter. Then it reads back from two places:

  THE WIRE  — which request body carries BRAVO-LINE (attempt 2 = the re-run of
              prompt #1, so the line was steered into it), and how many requests
              there were (a third one = the line started a turn of its own).
  THE GLASS — the pyte-replayed final screen (the steer echo, the reply).

Usage (from the tree, its interpreter)::

    cd <tree> && uv run --no-sync python .omc/specs/334-tui-countdown.py --repo <tree> [--src DIR]

``--src`` puts a scratch copy of aelix-agent-core's src first on PYTHONPATH.
"""

from __future__ import annotations

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

import pyte


def _chunk(content: str, finish: str | None) -> bytes:
    body = {
        "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "m",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n".encode()


class Provider:
    def __init__(self) -> None:
        self.port = 0
        self.bodies: list[dict] = []
        self._ready = threading.Event()

    async def _handle(self, reader, writer) -> None:  # type: ignore[no-untyped-def]
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            n = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    n = int(line.split(b":")[1])
            raw = await reader.readexactly(n) if n else b""
            try:
                self.bodies.append(json.loads(raw))
            except Exception:  # noqa: BLE001
                self.bodies.append({"raw": raw[:200].decode("utf-8", "replace")})
            me = len(self.bodies)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )

            def send(payload: bytes) -> None:
                writer.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")

            if me == 1:
                send(_chunk("thinking", None))
            else:
                send(_chunk(f"reply-{me}", None))
                send(_chunk("", "stop"))
                send(b"data: [DONE]\n\n")
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except Exception:  # noqa: BLE001
            pass
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
    with open(os.path.join(agent_dir, "models.json"), "w", encoding="utf-8") as fh:
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
                                "id": "held-model", "reasoning": False, "input": ["text"],
                                "cost": {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0},
                                "contextWindow": 128000, "maxTokens": 4096,
                            }
                        ],
                    }
                }
            },
            fh,
        )


def user_texts(body: dict) -> list[str]:
    out = []
    for m in body.get("messages", []) or []:
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--src", default=None)
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=30)
    args = ap.parse_args()

    provider = Provider()
    provider.start()
    agent_dir = tempfile.mkdtemp(prefix="aelix-334-tui-")
    cwd = tempfile.mkdtemp(prefix="aelix-334-tui-cwd-")
    write_models_json(agent_dir, provider.port)
    env = dict(os.environ)
    paths = [os.path.join(args.repo, "packages", p, "src")
             for p in ("aelix-agent-core", "aelix-coding-agent", "aelix-ai", "aelix-server")]
    if args.src:
        paths.insert(0, args.src)
    env["PYTHONPATH"] = ":".join(paths)
    env["TERM"] = "xterm-256color"
    env["AELIX_CODING_AGENT_DIR"] = agent_dir
    env["RETRYPROBE_KEY"] = "sk-local-probe"
    env["AELIX_OFFLINE"] = "1"
    env.pop("COLUMNS", None)

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", args.rows, args.cols, 0, 0))
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import aelix_agent_core.harness.core as c, sys; sys.stderr.write('CORE '+c.__file__+'\\n'); "
         "import runpy; runpy.run_module('aelix_coding_agent', run_name='__main__')",
         "--model", "retryprobe/held-model"],
        stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd, close_fds=True,
    )
    os.close(slave)
    raw = bytearray()
    screen = pyte.Screen(args.cols, args.rows)
    stream = pyte.ByteStream(screen)

    def pump(seconds: float, until=None) -> bool:  # type: ignore[no-untyped-def]
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if until is not None and until():
                return True
            ready, _, _ = select.select([master], [], [], 0.01)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                return False
            if not chunk:
                return False
            raw.extend(chunk)
            stream.feed(chunk)
        return until() if until is not None else True

    def on_glass(text: str) -> bool:
        return any(text in row for row in screen.display)

    typed_during = False
    try:
        pump(8.0)
        os.write(master, b"Reply with exactly: ALPHA\r")
        typed_during = pump(20.0, until=lambda: on_glass("Retrying (1/"))
        glass_at_type = [r.rstrip() for r in screen.display if r.strip()]
        requests_at_type = len(provider.bodies)
        if typed_during:
            os.write(master, b"BRAVO-LINE\r")
        pump(10.0, until=lambda: len(provider.bodies) >= 2 and on_glass("reply-2"))
        pump(2.0)
    finally:
        with contextlib.suppress(OSError):
            os.write(master, b"\x03")
            time.sleep(0.3)
            os.write(master, b"\x04")
        time.sleep(0.5)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        os.close(master)

    if os.environ.get("RAW_OUT"):
        with open(os.environ["RAW_OUT"], "wb") as fh:
            fh.write(bytes(raw))
    core_line = next((ln for ln in raw.decode("utf-8", "replace").splitlines() if ln.startswith("CORE ")), "CORE ?")
    print(core_line)
    print("countdown on the glass when typed :", typed_during, f"(requests served then: {requests_at_type})")
    for row in glass_at_type:
        if "Retrying" in row:
            print("  glass then:", row)
    print("requests served                   :", len(provider.bodies))
    for i, body in enumerate(provider.bodies, 1):
        print(f"  request {i} user texts           :", user_texts(body))
    print("--- final painted screen (before teardown) ---")
    for i, row in enumerate(screen.display):
        if row.strip():
            print(f"{i:2} |{row.rstrip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
