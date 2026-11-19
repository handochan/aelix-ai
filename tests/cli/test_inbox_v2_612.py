"""#612 (ADR-0308, contract r5 §7.1–§7.2) — a real delegated child on inbox version 2 asks its parent.

A ``--mode json -p --command-inbox=2`` child (``AELIX_SUBAGENT_DEPTH=1``, so its
gate's headless verdict is ``block``) under ``--permission-mode default``, on a
scripted chat-completions server whose first answer is a ``bash`` (or ``write``)
call. Its gate reaches branch (h) and asks the parent: one
``{"type": "approval_request", …}`` line on stdout, after the call's
``tool_execution_start``; this test is the parent and answers on the child's
stdin. What the child's model read is the tool message of its second request.

Every case but the ``=1`` guard fails on ``567753c0``: the child refused
``--command-inbox=2`` at launch ("names a command inbox version this aelix does
not serve") and made no model request. The guard (``=1``: no request is ever
written, the call is blocked headless) passes there.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.builtin.permission import _HEADLESS_BLOCK_REASON
from aelix_coding_agent.builtin.permission_mode import PLAN_BLOCKED
from aelix_coding_agent.cli.args import COMMAND_INBOX_FLAG
from aelix_coding_agent.modes.print_inbox import APPROVAL_REQUEST_MAX_BYTES

from tests.cli.test_command_inbox_123 import (
    _child_setup,
    _ModelServer,
    _text_turn,
    _texts,
    _tool_turn,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="a real child's pipes are POSIX here")

Answer = Callable[[dict[str, Any], "_Child"], Awaitable[None]]


class _Child:
    """The parent's end: the child's stdin, every stdout line it wrote, its approval requests."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.lines: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.sent = 0

    async def send(self, command: str, **fields: Any) -> str:
        assert self.proc.stdin is not None
        self.sent += 1
        request_id = f"p-{self.sent}"
        self.proc.stdin.write(json.dumps({"type": command, "id": request_id, **fields}).encode() + b"\n")
        await self.proc.stdin.drain()
        return request_id

    async def reply(self, request_id: str, timeout: float = 30) -> dict[str, Any]:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            for line in self.lines:
                if line.get("type") == "response" and line.get("id") == request_id:
                    return line
            await asyncio.sleep(0.02)
        raise AssertionError(f"no reply to {request_id}")


async def _run(
    tmp_path: Path,
    answer: Answer | None,
    *,
    script: Callable[[int], Any] | None = None,
    inbox: str = "2",
    mode: str = "default",
) -> tuple[_Child, _ModelServer, Path]:
    def default_script(n: int) -> Any:
        if n == 1:
            return _tool_turn("bash", {"command": "echo approved > out.txt"})
        return _text_turn("done")

    with _ModelServer(script=script or default_script) as server:
        cwd, env = _child_setup(tmp_path, server)
        env["AELIX_SUBAGENT_DEPTH"] = "1"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-P", "-m", "aelix_coding_agent",
            "--mode", "json", "-p", f"{COMMAND_INBOX_FLAG}={inbox}",
            "--model", "mockm", "--provider", "mockp", "--no-session", "--no-extensions",
            "--permission-mode", mode, "--no-agents", "Task: write the word approved to out.txt",
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        child = _Child(proc)
        assert proc.stdout is not None and proc.stderr is not None
        errors = asyncio.ensure_future(proc.stderr.read())
        try:
            while True:
                raw = await asyncio.wait_for(proc.stdout.readline(), 60)
                if not raw:
                    break
                line = json.loads(raw)
                child.lines.append(line)
                if line.get("type") == "approval_request":
                    child.requests.append(line)
                    if answer is not None:
                        asyncio.ensure_future(answer(line, child))
            await asyncio.wait_for(proc.wait(), 30)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            if proc.stdin is not None:
                proc.stdin.close()
        child.stderr = (await errors).decode("utf-8", errors="replace")  # type: ignore[attr-defined]
    return child, server, cwd


def _tool_result(server: _ModelServer) -> str:
    """What the child's model read for its one call: the tool message of its second request."""

    assert len(server.requests) >= 2, f"{len(server.requests)} model requests"
    (tool,) = [t for t in _texts(server.requests[1]) if t.startswith("tool:")]
    return tool[len("tool:") :]


def _decide(decision: str, reason: str | None = None) -> Answer:
    async def answer(request: dict[str, Any], child: _Child) -> None:
        fields: dict[str, Any] = {"request": request["id"], "decision": decision}
        if reason is not None:
            fields["reason"] = reason
        await child.send("approval", **fields)

    return answer


async def test_yes_runs_the_call_and_the_request_follows_its_start(tmp_path: Path) -> None:
    child, server, cwd = await _run(tmp_path, _decide("yes"))
    assert child.proc.returncode == 0, child.stderr  # type: ignore[attr-defined]
    (request,) = child.requests
    assert request["id"] == "ap-1" and request["toolName"] == "bash" and request["kind"] == "bash"
    assert request["args"] == {"command": "echo approved > out.txt"}
    assert request["toolCallId"] == "call_1"
    assert set(request) == {"type", "id", "toolCallId", "toolName", "kind", "args", "yesLabel", "redirectLabel"}
    types = [line.get("type") for line in child.lines]
    assert types.index("tool_execution_start") < types.index("approval_request")
    replies = [line for line in child.lines if line.get("type") == "response"]
    assert replies == [{"type": "response", "command": "approval", "id": "p-1", "success": True, "data": {"taken": True}}]
    assert (cwd / "out.txt").read_text(encoding="utf-8") == "approved\n"


@pytest.mark.parametrize(
    ("decision", "reason", "read"),
    [
        ("no", None, "Denied by the user."),
        ("cancel", None, "Denied by the user (cancelled)."),
        ("no", "the parent is in plan mode", "Not approved: the parent is in plan mode."),
        ("cancel", "no answer from the host within 300 s", "Not approved: no answer from the host within 300 s."),
        ("redirect", None, "Denied by the user (unexpected redirect)."),
    ],
)
async def test_a_refusal_blocks_the_call_with_its_reason(
    tmp_path: Path, decision: str, reason: str | None, read: str
) -> None:
    child, server, cwd = await _run(tmp_path, _decide(decision, reason))
    assert child.proc.returncode == 0
    assert _tool_result(server) == read
    assert not (cwd / "out.txt").exists()


async def test_yes_for_this_agent_is_the_childs_own_grant_and_the_next_call_is_not_asked(tmp_path: Path) -> None:
    def script(n: int) -> Any:
        if n in (1, 2):
            return _tool_turn("bash", {"command": "echo approved >> out.txt"}, call_id=f"call_{n}")
        return _text_turn("done")

    child, _server, cwd = await _run(tmp_path, _decide("yes_session"), script=script)
    assert len(child.requests) == 1, "the second call matched the child's own session grant"
    assert (cwd / "out.txt").read_text(encoding="utf-8") == "approved\napproved\n"


async def test_the_inbox_serves_a_steer_and_refuses_a_late_answer_while_a_request_is_open(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    async def answer(request: dict[str, Any], child: _Child) -> None:
        seen["steer"] = await child.reply(await child.send("steer", message="NOTE"))
        seen["late"] = await child.reply(await child.send("approval", request="ap-999", decision="yes"))
        await child.send("approval", request=request["id"], decision="yes")

    child, _server, cwd = await _run(tmp_path, answer)
    assert seen["steer"]["success"] is True and seen["steer"]["data"] == {"phase": "streaming"}
    assert seen["late"] == {
        "type": "response", "command": "approval", "id": "p-2", "success": False,
        "error": "no approval 'ap-999' is pending",
    }
    assert (cwd / "out.txt").exists()


async def test_a_tighten_to_plan_while_it_waits_blocks_the_call_after_a_yes(tmp_path: Path) -> None:
    """The child re-reads its posture after the answer (§7.2): ``plan`` wins over the yes."""

    async def answer(request: dict[str, Any], child: _Child) -> None:
        tightened = await child.reply(await child.send("set_permission_mode", mode="plan"))
        assert tightened["data"] == {"mode": "plan"}
        await child.send("approval", request=request["id"], decision="yes_session")

    child, server, cwd = await _run(tmp_path, answer)
    assert _tool_result(server).startswith(PLAN_BLOCKED)
    assert not (cwd / "out.txt").exists()


async def test_the_inboxs_eof_while_a_request_is_open_ends_the_run_with_nothing_run(tmp_path: Path) -> None:
    async def answer(_request: dict[str, Any], child: _Child) -> None:
        assert child.proc.stdin is not None
        child.proc.stdin.close()

    child, server, cwd = await _run(tmp_path, answer)
    assert child.proc.returncode == 1
    assert len(server.requests) == 1
    assert not (cwd / "out.txt").exists()


async def test_a_change_too_large_to_show_is_blocked_unsent(tmp_path: Path) -> None:
    big = "x" * (APPROVAL_REQUEST_MAX_BYTES + 10)

    def script(n: int) -> Any:
        return _tool_turn("write", {"path": "big.txt", "content": big}) if n == 1 else _text_turn("done")

    child, server, cwd = await _run(tmp_path, _decide("yes"), script=script)
    assert child.requests == []
    read = _tool_result(server)
    assert read.startswith("This change is too large to show for approval (") and read.endswith(
        " bytes; the limit is 1 MiB)."
    )
    assert not (cwd / "big.txt").exists()


async def test_a_version_1_child_never_asks_and_is_blocked_headless(tmp_path: Path) -> None:
    """Guard (passes on the base): ``-p``/json parents launch ``=1``, whose children keep today's verdict."""

    child, server, cwd = await _run(tmp_path, _decide("yes"), inbox="1")
    assert child.requests == []
    assert _tool_result(server) == _HEADLESS_BLOCK_REASON
    assert not (cwd / "out.txt").exists()


def test_the_request_bound_stays_under_the_parents_line_bound() -> None:
    """A request the parent's reader dropped silently would hold the child at its gate."""

    from aelix_agents.stream import MAX_LINE_BYTES

    assert APPROVAL_REQUEST_MAX_BYTES < MAX_LINE_BYTES


def test_the_child_parses_version_2_and_still_refuses_any_other() -> None:
    from aelix_coding_agent.cli.args import parse_args

    parsed = parse_args([f"{COMMAND_INBOX_FLAG}=2", "-p", "x"])
    assert parsed.command_inbox == "2" and parsed.diagnostics == []
    refused = parse_args([f"{COMMAND_INBOX_FLAG}=3", "-p", "x"])
    assert refused.command_inbox is None
    assert refused.diagnostics[0]["message"].endswith("it serves 1 and 2")


async def test_a_version_1_inbox_answers_approval_as_an_unknown_command() -> None:
    from aelix_coding_agent.builtin.permission_mode import PermissionPosture
    from aelix_coding_agent.modes import print_inbox

    written: list[str] = []
    inbox = print_inbox.CommandInbox(reader=asyncio.StreamReader(), posture=PermissionPosture())
    line = json.dumps({"type": "approval", "id": "p-1", "request": "ap-1", "decision": "yes"})
    await print_inbox._dispatch(None, inbox, line, written.append)
    assert json.loads(written[0]) == {
        "type": "response", "command": "approval", "id": "p-1", "success": False,
        "error": "unknown inbox command 'approval'",
    }
