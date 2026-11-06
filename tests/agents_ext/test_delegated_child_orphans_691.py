"""#691 against a REAL child: what its ``bash`` tool started does not outlive a stop.

THE QUESTION. A delegated child is stopped while its ``bash`` call runs. Every door a
human has — Esc and Esc Esc in the TUI, a SIGTERM to the parent (both cancel the
turn), ``SubagentRuntime.stop``, ``stop_all`` at teardown — SIGKILLs the child at once, so
its own ``dispose()`` gets no time to reap the command, and the command leads a session
of its own (``tools/bash.py``), which the child's group kill does not reach. Only the
reaper's descendant walk can name it. Until #691 that walk read ``/proc`` alone, so
on macOS the shell and the command it ran were left with PPID 1, running, after the
parent had reported ``aborted`` and even after the parent had exited. What the
shell backgrounded with ``(cmd &)`` has no parent chain back to the child on any
POSIX host; only the kill of the shell's group, which #691 adds, reaches it, and
only while that shell runs, as ``subshell``'s ``sleep 120`` keeps it running (once
the shell has exited nothing walked leads its group: ADR-0291 limit 2).

The flow is the product's: a real parent session in a bucket, the real runtime and
``PrintChannel``, a real ``-m aelix_coding_agent`` child on a built-in adapter
against a local chat-completions server (the setup of
``test_agent_resume_real_child_610.py``), and a ``bash`` call whose shell writes
its own pid and its command's pid before it waits.

``child``: red on macOS before #691 on all three doors; green on Linux before and
after, where ``/proc`` always named them. ``subshell``: red on both before the group
kill. POSIX only, like every real-child test in this package.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.consent import SpawnGrant
from aelix_agents.print_channel import PrintChannel, build_child_env
from aelix_agents.prompt_file import _pid_is_live
from aelix_agents.runtime import SpawnRecordMeta, SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.agents.profile import AgentProfile
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.subagent_contract import ResolvedProfile

from tests.agents_ext.test_agent_resume_real_child_610 import _chunk, _ModelServer
from tests.agents_ext.test_child_session_records import _parent
from tests.env_sandbox import child_env
from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="real-child spawn semantics are POSIX"
)


class _BashServer(_ModelServer):
    """Every request gets the same ``bash`` call: the command under test."""

    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command

    def turn(self, body: dict[str, Any]) -> list[dict[str, Any]]:  # type: ignore[override]
        call = {
            "index": 0,
            "id": "call_bash_691",
            "type": "function",
            "function": {"name": "bash", "arguments": json.dumps({"command": self.command})},
        }
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return [
            _chunk({"role": "assistant", "content": None, "tool_calls": [call]}),
            _chunk({}, finish="tool_calls", usage=usage),
        ]


def _models_json(agent_dir: Path, url: str) -> None:
    model = {
        "id": "mockm",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 1.0, "output": 2.0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 100000,
        "maxTokens": 4096,
    }
    providers = {
        "mockp": {
            "baseUrl": url,
            "apiKey": "MOCK_KEY",
            "api": "openai-completions",
            "models": [model],
        }
    }
    (agent_dir / "models.json").write_text(json.dumps({"providers": providers}), encoding="utf-8")


def _pids(path: Path) -> list[int] | None:
    """The shell's pid and its command's, once the file is whole."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.endswith("\n"):
        return None
    parts = text.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return [int(p) for p in parts]


_COMMANDS = {
    # ``$$`` is the shell the tool started, the session leader; ``$!`` is what it
    # runs. Written whole (temp file, then a rename) so a half line is never read.
    "child": "sleep 120 & echo $$ $! > {f}.tmp && mv {f}.tmp {f}; wait",
    # ``(cmd &)``: the subshell backgrounds ``cmd`` and exits, so ``cmd`` has PPID 1
    # and no walk names it. It stays in the shell's group, which the shell leads,
    # and goes with that group's kill. ``$$`` in a subshell is still the shell's.
    "subshell": "(sleep 120 & echo $$ $! > {f}.tmp && mv {f}.tmp {f}); sleep 120",
}


@pytest.mark.parametrize("shape", sorted(_COMMANDS))
@pytest.mark.parametrize("door", ["cancel", "stop", "stop_all"])
async def test_a_stopped_delegation_leaves_no_process_its_bash_started(
    tmp_path: Path, door: str, shape: str
) -> None:
    """``cancel`` is Esc / Esc Esc / a SIGTERM to the parent; ``stop`` is
    ``SubagentRuntime.stop``; ``stop_all`` is the session teardown (quit, ``/new``).

    ``child`` is a command the shell waits for, which the walk names; ``subshell``
    is ``(sleep 120 &)``, which only the kill of the shell's group reaches —
    left with PPID 1 on Linux and macOS until #691's group kill."""

    home = tmp_path / "home"
    agent_dir = home / "agent"
    cwd = tmp_path / "repo"
    for directory in (home / ".config", agent_dir, cwd):
        directory.mkdir(parents=True, exist_ok=True)
    pid_file = tmp_path / "bash.pids"
    command = _COMMANDS[shape].format(f=pid_file)

    pids: list[int] | None = None
    with _BashServer(command) as server:
        _models_json(agent_dir, server.url)
        env = child_env(
            home,
            XDG_CONFIG_HOME=str(home / ".config"),
            AELIX_CODING_AGENT_DIR=str(agent_dir),
            PI_OFFLINE="1",
            MOCK_KEY="not-a-real-key",
        )
        session, _repo, _parent_path = await _parent(tmp_path, cwd=cwd)
        runtime = _SubagentRuntimeImpl(
            host=SubagentHost(
                cwd=lambda: str(cwd),
                session=lambda: session,
                active_tools=lambda: ["bash", "read"],
            ),
            channel=PrintChannel(grace=1.0, env_builder=lambda p: build_child_env(p, base=env)),
        )
        profile = AgentProfile(
            name="runner",
            description="Runs one command.",
            body="You are a runner.",
            file_path=str(tmp_path / "runner.md"),
            scope="user",
            model="mockm",
            provider="mockp",
        )
        resolved = ResolvedProfile(
            name=profile.name, profile=profile, source_path=profile.file_path, scope=profile.scope
        )
        grant = SpawnGrant(
            profile=profile.name,
            source_path=profile.file_path,
            scope=profile.scope,
            mode=PermissionMode.YOLO,
            widened=False,
            consented=True,
        )
        delegation = asyncio.ensure_future(
            runtime.spawn_granted(
                grant,
                resolved,
                "run the command",
                record=SpawnRecordMeta(tool_call_id="tc-691", mode="single", index=0),
            )
        )
        try:
            await wait_until(
                lambda: _pids(pid_file) is not None,
                timeout=90,
                what="the child's bash call to start its command",
            )
            pids = _pids(pid_file)
            assert pids is not None
            assert all(_pid_is_live(pid) for pid in pids), pids
            (row,) = runtime._children.values()

            if door == "cancel":
                delegation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await delegation
            elif door == "stop":
                await runtime.stop(row.id)
                result = await asyncio.wait_for(delegation, 30)
                assert result.status == "aborted", result
            else:
                await runtime.stop_all()
                result = await asyncio.wait_for(delegation, 30)
                assert result.status == "aborted", result

            # SIGKILLed on the line of the child's kill, so they are gone within
            # moments; the bound is anti-hang. A survivor is reparented and
            # reaped by init, so liveness is a fair read of it.
            await wait_until(
                lambda: not any(_pid_is_live(pid) for pid in pids),
                timeout=10,
                what=f"the shell and its command {pids} to die with the child",
            )
        finally:
            delegation.cancel()
            with contextlib.suppress(BaseException):
                await delegation
            await runtime.stop_all()
            pids = pids or _pids(pid_file) or []
            for pid in pids:
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
            if pids:
                # The shell leads its group, and a failed ``subshell`` run leaves
                # the shell's own ``sleep`` there too.
                with contextlib.suppress(OSError):
                    os.killpg(pids[0], signal.SIGKILL)
