"""Local live PTY probe: truncate, recover via bash, hold the next model response."""

import asyncio
import contextlib
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import tempfile
import termios
import threading
import time
from pathlib import Path

import pyte

REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / ".omc/specs/issue-197-evidence"
ARTIFACTS.mkdir(exist_ok=True)
ready = threading.Event()
retry = threading.Event()
release_recovery = threading.Event()
continued = threading.Event()
release_final = threading.Event()
served = []
port = []


def chunk(delta, finish=None):
    return (
        "data: "
        + json.dumps(
            {
                "id": "probe",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "retry-model",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )
        + "\n\n"
    ).encode()


async def handle(reader, writer):
    try:
        header = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in header.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        request = json.loads(await reader.readexactly(length))
        served.append(request)
        attempt = len(served)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )

        async def send(payload):
            writer.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
            await writer.drain()

        if attempt == 1:
            await send(chunk({"content": "FIRST_ATTEMPT_TRUNCATED"}))
        elif attempt == 2:
            retry.set()
            while not release_recovery.is_set():
                await asyncio.sleep(0.02)
            await send(chunk({
                "tool_calls": [{
                    "index": 0,
                    "id": "recovered-bash",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": "sleep 2; printf RECOVERED_TOOL_OK"}),
                    },
                }],
            }, "tool_calls"))
            await send(b"data: [DONE]\n\n")
        else:
            continued.set()
            await send(chunk({"content": "RECOVERED_TURN_STILL_RUNNING"}))
            while not release_final.is_set():
                await asyncio.sleep(0.02)
            await send(chunk({}, "stop"))
            await send(b"data: [DONE]\n\n")
        writer.write(b"0\r\n\r\n")
        await writer.drain()
    finally:
        writer.close()


def serve():
    async def run():
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port.append(server.sockets[0].getsockname()[1])
        ready.set()
        async with server:
            await server.serve_forever()
    asyncio.run(run())


threading.Thread(target=serve, daemon=True).start()
assert ready.wait(10)
agent = Path(tempfile.mkdtemp(prefix="aelix-197-probe-"))
(agent / "models.json").write_text(json.dumps({"providers": {"retryprobe": {
    "api": "openai-completions",
    "baseUrl": f"http://127.0.0.1:{port[0]}/v1",
    "apiKey": "RETRYPROBE_KEY",
    "models": [{"id": "retry-model", "reasoning": False, "input": ["text"],
                "contextWindow": 128000, "maxTokens": 4096,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}],
}}}))
env = dict(os.environ, TERM="xterm-256color", AELIX_CODING_AGENT_DIR=str(agent),
           RETRYPROBE_KEY="local-test-only", AELIX_OFFLINE="1")
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 110, 0, 0))
process = subprocess.Popen(
    ["uv", "run", "aelix", "--provider", "retryprobe", "--model", "retry-model",
     "--no-session", "--no-extensions", "--no-skills", "--no-context-files",
     "--offline", "--tools", "bash", "--permission-mode", "yolo"],
    cwd=REPO, stdin=slave, stdout=slave, stderr=slave, env=env,
)
os.close(slave)
raw = bytearray()


def screen():
    grid = pyte.Screen(110, 30)
    pyte.Stream(grid).feed(raw.decode("utf-8", "replace"))
    return "\n".join(grid.display)


def pump(seconds, condition=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition and condition():
            return
        if not select.select([master], [], [], 0.02)[0]:
            continue
        try:
            data = os.read(master, 65536)
        except OSError:
            return
        raw.extend(data)
        # Real terminal reply to prompt-toolkit's cursor-position query.
        if b"\x1b[6n" in data:
            os.write(master, b"\x1b[1;1R")


try:
    pump(10, lambda: "Press Enter" in screen() or "❯" in screen())
    os.write(master, b"Exercise recovery and the bash tool\r")
    pump(40, retry.is_set)
    pump(2)
    before = screen()
    (ARTIFACTS / "before-recovery.txt").write_text(before)
    assert retry.is_set(), f"No retry request; served {len(served)}\n{before}"
    assert "Retrying" in before, before
    release_recovery.set()
    pump(30, continued.is_set)
    pump(3)
    recovered = screen()
    (ARTIFACTS / "after-recovery-active.txt").write_text(recovered)
    assert continued.is_set(), f"No next request; served {len(served)}\n{recovered}"
    # Check BEFORE ending the held stream or quitting the TUI.
    print("requests served:", len(served))
    print("retry visible before recovered response:", "Retrying" in before)
    print("retry visible while next response remains active:", "Retrying" in recovered)
    print("retry succeeded notice in transcript while active:", "Retry succeeded" in raw.decode("utf-8", "replace"))
    print(recovered)
    assert "Retrying" not in recovered, recovered
    assert "Retry succeeded" in raw.decode("utf-8", "replace"), recovered
    assert "Working" in recovered, recovered
    assert "RECOVERED_TOOL_OK" in json.dumps(served[-1]), "Bash result did not reach next model call"
    release_final.set()
    pump(3)
    (ARTIFACTS / "finished.txt").write_text(screen())
finally:
    release_recovery.set()
    release_final.set()
    (ARTIFACTS / "terminal.raw").write_bytes(raw)
    with contextlib.suppress(OSError):
        os.write(master, b"\x03")
        pump(0.5)
        os.write(master, b"\x04")
        pump(1)
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(5)
    if process.poll() is None:
        process.kill()
    os.close(master)
