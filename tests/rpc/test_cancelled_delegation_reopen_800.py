"""#800 against the product: a real ``--mode rpc`` parent cancels a delegation and is relaunched.

Contract r5 §12.3 test 10 (the shape of ``.omc/probes/612/design-r3/p800/p800_cancel_probe.py``).
A scripted chat-completions server; a real ``--mode rpc`` parent whose model calls ``agent``
once; the parent spawns a real delegated child (``--mode json -p``) that says what it found
and runs ``bash sleep 30``. While the child is in that tool the host sends ``abort``. The
cancelled call's result is the report (ADR-0307 D1), so ``get_messages`` returns it, and a
parent relaunched with ``--session <file>`` returns it again: it is in the session file.

Fails on 6dd682cd: the cancelled call's result is the loop's cut text ("Cancelled: the user
stopped the turn …"), in the live process and after the relaunch; the report sat on the
in-memory next-turn queue, which the relaunch does not have.

POSIX only: the parent's and the child's pipes and the child's process group are POSIX here
(``tests/cli/test_command_inbox_123.py``'s real-child tests are marked the same way).
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.aggregate import CANCEL_REPORT_HEAD

from tests.env_sandbox import child_env

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="real child pipes are POSIX here")

_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
_FOUND = "CHILD FOUND THE BUG IN parser.py LINE 42"


def _chunk(delta: dict[str, Any], *, finish: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "mockm",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if finish is not None:
        body["usage"] = _USAGE
    return body


def _call(name: str, arguments: dict[str, Any], call_id: str, text: str | None = None) -> list[Any]:
    call = {"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    return [
        _chunk({"role": "assistant", "content": text}),
        _chunk({"tool_calls": [call]}),
        _chunk({}, finish="tool_calls"),
    ]


def _say(text: str) -> list[Any]:
    return [_chunk({"role": "assistant", "content": text}), _chunk({}, finish="stop")]


class _Server:
    """Parent requests (they offer ``agent``) delegate once; a child's first request
    says what it found and runs ``bash sleep 30``."""

    def __init__(self) -> None:
        self.parent = 0
        self.child = 0
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802 — http.server's spelling
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
                turn = server.answer(body)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                try:
                    for chunk in turn:
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._http.daemon_threads = True
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    def answer(self, body: dict[str, Any]) -> list[Any]:
        names = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        if "agent" in names:
            self.parent += 1
            if self.parent == 1:
                return _call("agent", {"profile": "general-purpose", "task": "TASK ALPHA"}, "p1")
            return _say("PARENT NEXT ANSWER")
        self.child += 1
        if not any(m.get("role") == "tool" for m in body.get("messages", [])):
            return _call("bash", {"command": "sleep 30"}, "c1", text=_FOUND)
        return _say("CHILD DONE")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._http.server_address[1]}/v1"

    def __enter__(self) -> _Server:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._http.shutdown()
        self._http.server_close()


class _Rpc:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.lines: list[dict[str, Any]] = []
        self.stderr = bytearray()
        self._buf = b""
        self._pumps = [asyncio.ensure_future(self._out()), asyncio.ensure_future(self._err())]

    async def _out(self) -> None:
        assert self.proc.stdout is not None
        while chunk := await self.proc.stdout.read(65536):
            self._buf += chunk
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                if raw.strip():
                    try:
                        self.lines.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue

    async def _err(self) -> None:
        assert self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(65536):
            self.stderr.extend(chunk)

    async def send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def wait_for(self, pred: Any, timeout: float, what: str) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for line in list(self.lines):
                if pred(line):
                    return line
            assert self.proc.returncode is None, f"the parent exited: {self.stderr.decode('utf-8', 'replace')[-2000:]}"
            await asyncio.sleep(0.05)
        raise AssertionError(f"no {what} in {timeout} s; stderr: {self.stderr.decode('utf-8', 'replace')[-2000:]}")

    async def call(self, obj: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
        await self.send(obj)
        return await self.wait_for(
            lambda o: o.get("type") == "response" and o.get("id") == obj["id"], timeout, f"response {obj['id']}"
        )

    async def close(self) -> int | None:
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), 60)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()
        await asyncio.gather(*self._pumps, return_exceptions=True)
        return self.proc.returncode


def _agent_results(response: dict[str, Any]) -> list[str]:
    out = []
    for message in (response.get("data") or {}).get("messages", []):
        if message.get("role") != "toolResult" or message.get("tool_name") != "agent":
            continue
        content = message.get("content")
        if isinstance(content, list):
            out.append("".join(part.get("text", "") for part in content if isinstance(part, dict)))
        else:
            out.append(str(content))
    return out


async def _launch(argv: list[str], cwd: Path, env: dict[str, str]) -> _Rpc:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-P", "-m", "aelix_coding_agent", *argv,
        cwd=str(cwd), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    return _Rpc(proc)


async def test_a_cancelled_delegations_report_survives_a_relaunch(tmp_path: Path) -> None:
    home, agent_dir, cwd = tmp_path / "home", tmp_path / "home" / "agent", tmp_path / "repo"
    for directory in (home / ".config", agent_dir, cwd):
        directory.mkdir(parents=True, exist_ok=True)
    argv = ["--mode", "rpc", "--model", "mockm", "--provider", "mockp", "--agents", "--permission-mode", "yolo"]
    with _Server() as server:
        (agent_dir / "models.json").write_text(
            json.dumps({"providers": {"mockp": {
                "baseUrl": server.url, "apiKey": "MOCK_KEY", "api": "openai-completions",
                "models": [{"id": "mockm", "reasoning": False, "input": ["text"],
                            "cost": {"input": 1.0, "output": 2.0, "cacheRead": 0, "cacheWrite": 0},
                            "contextWindow": 100000, "maxTokens": 4096}]}}}),
            encoding="utf-8",
        )
        env = child_env(
            home,
            XDG_CONFIG_HOME=str(home / ".config"),
            AELIX_CODING_AGENT_DIR=str(agent_dir),
            PI_OFFLINE="1",
            MOCK_KEY="not-a-real-key",
            PYTHONUNBUFFERED="1",
        )
        rpc = await _launch(argv, cwd, env)
        try:
            await rpc.call({"type": "prompt", "id": "p1", "message": "delegate the task"})
            started = time.monotonic()
            while server.child < 1:
                assert time.monotonic() - started < 90, "the child made no request"
                assert rpc.proc.returncode is None, rpc.stderr.decode("utf-8", "replace")[-2000:]
                await asyncio.sleep(0.05)
            await asyncio.sleep(1.5)  # the child is in its ``bash sleep 30``
            await rpc.call({"type": "abort", "id": "ab"})
            await rpc.wait_for(lambda o: o.get("type") == "agent_end", 60, "agent_end after the abort")
            messages = await rpc.call({"type": "get_messages", "id": "m1"})
            live = _agent_results(messages)
            state = await rpc.call({"type": "get_state", "id": "s1"})
            session_file = (state.get("data") or {}).get("sessionFile")
        finally:
            await rpc.close()
        assert session_file, state
        relaunched = await _launch([*argv, "--session", session_file], cwd, env)
        try:
            reopened = _agent_results(await relaunched.call({"type": "get_messages", "id": "m2"}))
        finally:
            await relaunched.close()

    assert len(live) == 1, json.dumps(messages)[:3000]
    assert live[0].startswith(CANCEL_REPORT_HEAD), live[0][:300]
    assert _FOUND in live[0]
    assert reopened == live, "the relaunched parent reads the same result from the session file"
    assert server.parent == 1, "no turn was started on the user's behalf"
