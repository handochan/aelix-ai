"""#611 S3c PR c0 (#777): ``tests/jobs/gated_cli.py`` runs the real CLI with background jobs on.

A gated ``-p`` run against a local chat-completions SSE server: the parent model
asks ``agent(action="spawn")``, a real delegated child (``python -P -m
aelix_coding_agent``) answers, ``-p`` waits for the job's notice and exits 0. The
server records every request, so what each process was offered is read off the
wire: the parent's ``agent`` tool offers ``spawn`` and it holds ``job`` only
with the gate on, and the child's ``bash`` offers ``run_in_background`` only with
its own gate on, which shows the launcher reached the child too
(``gated_site/sitecustomize.py``); a parent started without the launcher gives
its child no such offer, the negative control. The server is the shape
of ``tests/agents_ext/test_agent_resume_real_child_610.py``'s.

Green on ``52bc607a`` once the launcher exists: it is the prerequisite itself.
POSIX only, as every test that runs a service-owned real child is
(``tests/agents_ext/test_agent_background_611.py``'s ``REAL_CHILD``): on win32
such a child runs the job's real Job-object evidence legs, which #777 PR c1
measures first.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.env_sandbox import child_env

REAL_CHILD = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "a service-owned child on win32 runs the job's real Job-object evidence legs, which "
        "ADR-0293 proves against an injected api only until #611 S3's Windows acceptance"
    ),
)

LAUNCHER = Path(__file__).resolve().parent / "gated_cli.py"
CHILD_ANSWER = "the gated child answered"
PROFILE = "prober"

Turn = Callable[[dict[str, Any], int], dict[str, Any]]
"""A parent turn: its request body and its index among the parent's requests -> the delta to stream."""


class ModelServer:
    """A chat-completions endpoint that streams one scripted turn per request, and keeps each body.

    A request whose first user message is a delegated task (``Task: …``) is the
    child's and is answered with ``child_answer``; every other one is the
    parent's and is answered by ``parent_turn``.
    """

    def __init__(self, parent_turn: Turn, *, child_answer: str = CHILD_ANSWER) -> None:
        self.parent: list[dict[str, Any]] = []
        self.child: list[dict[str, Any]] = []
        self._parent_turn = parent_turn
        self._child_answer = child_answer
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:  # noqa: N802 — http.server's spelling
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length))
                delta = server.answer(body)
                usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                finish = "tool_calls" if delta.get("tool_calls") else "stop"
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for chunk in (_chunk(delta), _chunk({}, finish=finish, usage=usage)):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._lock = threading.Lock()

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if text_of(first_user(body)).startswith("Task: "):
                self.child.append(body)
                return {"role": "assistant", "content": self._child_answer}
            self.parent.append(body)
            return self._parent_turn(body, len(self.parent) - 1)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._http.server_address[1]}/v1"

    def __enter__(self) -> ModelServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._http.shutdown()
        self._http.server_close()


def _chunk(delta: dict[str, Any], *, finish: str | None = None, usage: Any = None) -> dict[str, Any]:
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


def call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    """A delta that calls one tool."""

    fn = {"name": name, "arguments": json.dumps(arguments)}
    return {"role": "assistant", "content": None, "tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": fn}]}


def say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def first_user(body: dict[str, Any]) -> dict[str, Any]:
    users = [m for m in body.get("messages", []) if m.get("role") == "user"]
    return users[0] if users else {}


def text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def all_text(body: dict[str, Any]) -> str:
    return "\n".join(text_of(m) for m in body.get("messages", []))


def tools(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The tools a request offered, by name, as their JSON schemas."""

    out: dict[str, dict[str, Any]] = {}
    for tool in body.get("tools") or []:
        fn = tool.get("function") or {}
        out[str(fn.get("name"))] = fn.get("parameters") or {}
    return out


def offers_background_bash(body: dict[str, Any]) -> bool:
    """Whether the request's ``bash`` offers ``run_in_background``, which it does only while
    ``is_active()`` (``tools/bash.py`` ``_background_offer``). A child is granted built-in
    tools only (``print_channel.narrow_tools``), so ``job`` never reaches its request and
    this is how a child's gate reads on the wire."""

    schema = tools(body).get("bash") or {}
    return "run_in_background" in (schema.get("properties") or {})


def agent_actions(body: dict[str, Any]) -> list[str]:
    schema = tools(body).get("agent") or {}
    return list(((schema.get("properties") or {}).get("action") or {}).get("enum") or [])


class Scratch:
    """A scratch HOME, agent dir and cwd, with ``mockp/mockm`` pointed at a server and one profile."""

    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.agent_dir = self.home / "agent"
        self.cwd = root / "cwd"
        for directory in (self.home / ".config", self.agent_dir / "agents", self.cwd):
            directory.mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "agents" / f"{PROFILE}.md").write_text(
            f"---\nname: {PROFILE}\ndescription: Answers once.\nmodel: mockm\nprovider: mockp\n---\n\n"
            "You are the prober.\n",
            encoding="utf-8",
        )

    def point_at(self, server: ModelServer) -> None:
        model = {
            "id": "mockm",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 1.0, "output": 2.0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 100000,
            "maxTokens": 4096,
        }
        provider = {"baseUrl": server.url, "apiKey": "MOCK_KEY", "api": "openai-completions", "models": [model]}
        (self.agent_dir / "models.json").write_text(json.dumps({"providers": {"mockp": provider}}), encoding="utf-8")

    def settings(self, value: dict[str, Any]) -> None:
        """The GLOBAL settings file (``<agent dir>/settings.json``)."""

        (self.agent_dir / "settings.json").write_text(json.dumps(value), encoding="utf-8")

    def run(
        self, *args: str, launcher: Path | None = LAUNCHER, timeout: float = 120.0
    ) -> subprocess.CompletedProcess[str]:
        """``gated_cli.py --agents --provider mockp --model mockm <args>`` in the scratch;
        ``launcher=None`` runs ``python -P -m aelix_coding_agent`` instead, the gate off."""

        env = child_env(
            self.home,
            XDG_CONFIG_HOME=str(self.home / ".config"),
            AELIX_CODING_AGENT_DIR=str(self.agent_dir),
            PI_OFFLINE="1",
            MOCK_KEY="not-a-real-key",
            AELIX_MAX_RETRIES="0",
        )
        entry = [str(launcher)] if launcher is not None else ["-P", "-m", "aelix_coding_agent"]
        argv = [sys.executable, *entry, "--agents", "--provider", "mockp", "--model", "mockm", *args]
        return subprocess.run(
            argv,
            cwd=self.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )


def spawn_then_wait(body: dict[str, Any], index: int) -> dict[str, Any]:
    """The parent: spawn one agent in the background, then answer plainly."""

    if index == 0:
        return call("agent", {"action": "spawn", "profile": PROFILE, "task": "say the answer"})
    return say(f"parent turn {index}")


@REAL_CHILD
def test_a_gated_print_run_spawns_one_background_agent_and_exits_0(tmp_path: Path) -> None:
    scratch = Scratch(tmp_path)
    with ModelServer(spawn_then_wait) as server:
        scratch.point_at(server)
        done = scratch.run("-p", "go")

    assert done.returncode == 0, done.stderr
    assert len(server.child) == 1, "one delegated child ran"
    first = server.parent[0]
    assert "spawn" in agent_actions(first), "the parent's agent tool offers spawn: the gate is on"
    assert {"job", "job_stop"} <= set(tools(first)), sorted(tools(first))
    started = all_text(server.parent[1])
    assert "background" in started and CHILD_ANSWER not in started, "spawn returned before the answer"
    last = all_text(server.parent[-1])
    assert "<background-job-result" in last and CHILD_ANSWER in last, "-p waited for the job's notice"
    assert offers_background_bash(server.child[0]), "the delegated child runs with the gate on too"


def run_then_answer(body: dict[str, Any], index: int) -> dict[str, Any]:
    """The parent: run one agent in the foreground, then answer plainly."""

    if index == 0:
        return call("agent", {"profile": PROFILE, "task": "say the answer"})
    return say(f"parent turn {index}")


@REAL_CHILD
def test_without_the_launcher_neither_process_is_gated(tmp_path: Path) -> None:
    """The negative control for the wire readings above: the product's own entry point."""

    scratch = Scratch(tmp_path)
    with ModelServer(run_then_answer) as server:
        scratch.point_at(server)
        done = scratch.run("-p", "go", launcher=None)

    assert done.returncode == 0, done.stderr
    assert len(server.child) == 1
    first = server.parent[0]
    assert "spawn" not in agent_actions(first) and "job" not in tools(first)
    assert not offers_background_bash(first) and not offers_background_bash(server.child[0])
    assert CHILD_ANSWER in all_text(server.parent[1]), "the foreground run returned its answer"
