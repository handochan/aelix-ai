"""#610 against a REAL child: a stopped tool call reaches the resumed child's model as
"No result provided" — an error, never a success.

THE QUESTION (design D14, test 7). A child stopped while a tool runs leaves its
file ending in ``assistant(toolCall)`` with no result and no abort marker — the
harness keeps the abort off the session write path. Resuming it on that file
must not hand its model a call that silently succeeded. aelix does not repair
on load; the provider transform (``_transform_messages``) answers the orphaned
call with ``ToolResultMessage("No result provided", is_error=True)`` in the next
request. This pins that on the wire.

WHY A BUILT-IN ADAPTER AND A MOCK SERVER, NOT THE ``-e`` STUB PROVIDER. An
adapter an extension registers gets the raw messages and bypasses the
transform (ADR-0277 limit 3), so the stub of ``test_child_realized_posture``
would show a request without the synthesised result and prove nothing. Here
the child runs ``openai-completions`` against a local chat-completions SSE
server, which records every request body it is sent — the measured shape of
the #610 survey probe: ``tool(call_sleep_1, "No result provided")`` and then
the new user message.

The flow is the product's: a real parent session in a bucket, the real runtime
and ``PrintChannel``, a cancel while the child's ``bash`` call runs (Esc), then
``find_resume_target`` and ``resume_granted`` on the same child file.

POSIX only, like every real-child test in this package.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.child_session import ResumeTarget
from aelix_agents.consent import SpawnGrant
from aelix_agents.print_channel import PrintChannel, build_child_env
from aelix_agents.runtime import (
    RESUME_PREAMBLE,
    SpawnRecordMeta,
    SubagentHost,
    _SubagentRuntimeImpl,
)
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.agents_ext.test_child_session_records import _parent
from tests.env_sandbox import child_env
from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="real-child spawn semantics are POSIX"
)

_CALL_ID = "call_sleep_1"
_RESUMED_ANSWER = "resumed and checked the earlier call"


class _ModelServer:
    """A chat-completions endpoint that streams one scripted turn per request.

    A request whose last user message is a RESUME (it begins with the resume
    preamble) gets a plain answer; any other gets a ``bash`` call that sleeps,
    so the first run is still inside its tool when it is stopped. Every request
    body is kept, in order.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802 — http.server's spelling
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length))
                server.requests.append(body)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for chunk in server.turn(body):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._http.server_address[1]}/v1"

    def __enter__(self) -> _ModelServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._http.shutdown()
        self._http.server_close()

    @staticmethod
    def turn(body: dict[str, Any]) -> list[dict[str, Any]]:
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if _text(_last_user(body)).startswith(f"Task: {RESUME_PREAMBLE}"):
            delta: dict[str, Any] = {"role": "assistant", "content": _RESUMED_ANSWER}
            finish = "stop"
        else:
            call = {
                "index": 0,
                "id": _CALL_ID,
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": "sleep 30"})},
            }
            delta = {"role": "assistant", "content": None, "tool_calls": [call]}
            finish = "tool_calls"
        return [_chunk(delta), _chunk({}, finish=finish, usage=usage)]


def _chunk(
    delta: dict[str, Any], *, finish: str | None = None, usage: Any = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "mockm",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def _last_user(body: dict[str, Any]) -> dict[str, Any]:
    users = [m for m in body.get("messages", []) if m.get("role") == "user"]
    return users[-1] if users else {}


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _entries(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


def _has_the_dangling_call(path: Path) -> bool:
    """The child's file holds the assistant turn that asked for ``bash``."""

    for entry in _entries(path):
        message = entry.get("message") if entry.get("type") == "message" else None
        if isinstance(message, dict) and message.get("role") == "assistant":
            for block in message.get("content") or []:
                if block.get("type") == "toolCall" and block.get("tool_call_id") == _CALL_ID:
                    return True
    return False


async def test_a_resumed_child_sees_its_stopped_call_as_no_result_never_as_success(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    agent_dir = home / "agent"
    cwd = tmp_path / "repo"
    for directory in (home / ".config", agent_dir, cwd):
        directory.mkdir(parents=True, exist_ok=True)

    with _ModelServer() as server:
        (agent_dir / "models.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "mockp": {
                            "baseUrl": server.url,
                            "apiKey": "MOCK_KEY",
                            "api": "openai-completions",
                            "models": [
                                {
                                    "id": "mockm",
                                    "reasoning": False,
                                    "input": ["text"],
                                    "cost": {
                                        "input": 1.0,
                                        "output": 2.0,
                                        "cacheRead": 0,
                                        "cacheWrite": 0,
                                    },
                                    "contextWindow": 100000,
                                    "maxTokens": 4096,
                                }
                            ],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        env = child_env(
            home,
            XDG_CONFIG_HOME=str(home / ".config"),
            AELIX_CODING_AGENT_DIR=str(agent_dir),
            PI_OFFLINE="1",
            MOCK_KEY="not-a-real-key",
        )
        session, _repo, parent_path = await _parent(tmp_path, cwd=cwd)
        runtime = _SubagentRuntimeImpl(
            host=SubagentHost(
                cwd=lambda: str(cwd),
                session=lambda: session,
                active_tools=lambda: ["bash", "read"],
            ),
            channel=PrintChannel(grace=1.0, env_builder=lambda p: build_child_env(p, base=env)),
        )
        profile = AgentProfile(
            name="prober",
            description="Runs one command.",
            body="You are a prober.",
            file_path=str(tmp_path / "prober.md"),
            scope="user",
            model="mockm",
            provider="mockp",
        )
        resolved = ResolvedProfile(
            name=profile.name, profile=profile, source_path=profile.file_path, scope=profile.scope
        )
        # ``bash`` has to RUN for the call to be in flight when it is stopped.
        grant = SpawnGrant(
            profile=profile.name,
            source_path=profile.file_path,
            scope=profile.scope,
            mode=PermissionMode.YOLO,
            widened=False,
            consented=True,
        )
        try:
            first = asyncio.ensure_future(
                runtime.spawn_granted(
                    grant,
                    resolved,
                    "run the slow check",
                    record=SpawnRecordMeta(tool_call_id="tc-1", mode="single", index=0),
                )
            )
            await wait_until(
                lambda: any(row.session_id for row in runtime._children.values()),
                timeout=60,
                what="the first run's child file is allocated",
            )
            row = next(iter(runtime._children.values()))
            child_id = str(row.session_id)
            child_path = parent_path.with_name("p") / f"{row.id}.jsonl"
            await wait_until(
                lambda: _has_the_dangling_call(child_path),
                timeout=90,
                what="the child's bash call is in its file",
            )
            # Esc: the call is cancelled while the child's ``bash`` runs.
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first

            target = await runtime.find_resume_target(child_id)
            assert isinstance(target, ResumeTarget), target
            assert target.last_status == "stopped"
            # The killed child's writer lock goes with its process.
            await wait_until(lambda: runtime.resume_refusal(target) is None, timeout=30)
            before = len(server.requests)

            result = await asyncio.wait_for(
                runtime.resume_granted(
                    grant,
                    resolved,
                    target,
                    "report what the check found",
                    record=SpawnRecordMeta(tool_call_id="tc-2", mode="single", index=0),
                ),
                120,
            )
        finally:
            await runtime.stop_all()

    assert result.ok is True, f"{result.summary}\n{result.details or ''}"
    assert result.summary == _RESUMED_ANSWER
    assert result.session_id == child_id

    resumed = server.requests[before:]
    assert len(resumed) == 1, [_text(_last_user(r)) for r in resumed]
    messages = resumed[0]["messages"]
    roles = [m["role"] for m in messages if m["role"] != "system"]
    assert roles == ["user", "assistant", "tool", "user"], roles
    asked = next(m for m in messages if m["role"] == "assistant")
    assert [c["id"] for c in asked["tool_calls"]] == [_CALL_ID]
    answered = next(m for m in messages if m["role"] == "tool")
    assert answered["tool_call_id"] == _CALL_ID
    assert _text(answered) == "No result provided", "never a success the call did not have"
    assert _text(_last_user(resumed[0])).startswith(f"Task: {RESUME_PREAMBLE}")
    assert _text(_last_user(resumed[0])).endswith("Next instruction:\nreport what the check found")

    # The synthesised result is the provider's, not the file's: nothing in the
    # child's transcript claims the call returned.
    results = [
        e for e in _entries(child_path)
        if e.get("type") == "message" and e["message"].get("role") == "toolResult"
    ]
    assert results == []
