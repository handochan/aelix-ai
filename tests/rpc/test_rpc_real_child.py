"""The real-child ``--mode rpc`` bed.

Every other test in ``tests/rpc/`` either drives ``run_rpc_mode`` in-process
against a fake harness, or subclasses ``RpcClient`` to replace ``_build_argv``
with an inline ``python -c`` echo stub. So **nothing** exercised the actual
``python -m aelix_coding_agent --mode rpc`` entry point, and the mode advertised
in ``README.md:50`` / ``docs/guides/getting-started.md:74`` was dead on arrival:
``cli/entry.py`` passed ``runtime_host=`` *and* ``repo=``/``fs=`` into
``run_rpc_mode``, the exact pair it forbids (ADR-0079, P-324), so the process
raised ``RuntimeError`` before reading a byte of stdin.

This module is the bed that closes that hole. It spawns a genuine child, pumps
both pipes concurrently (ADR-0198 D6's deadlock rule) and speaks real JSONL.

**Why one test rather than four.** A cold child costs ~25 s, of which ~18.6 s is
import time alone (measured: ``python -X importtime -c "import
aelix_coding_agent.cli.entry"``). ``asyncio_mode = "auto"`` gives function-scoped
event loops, and a subprocess is bound to the loop that created it, so a
module-scoped child would outlive its loop. Paying one boot and asserting the
sequence against it is both faster and closer to what a long-lived channel does.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.env_sandbox import child_env

# A full CLI boot (settings, model registry, extension scan) is an order of
# magnitude slower than the in-process tests beside it.
_BOOT_TIMEOUT_S = 120.0
_STOP_TIMEOUT_S = 15.0


def _child_env(tmp_path: Path) -> dict[str, str]:
    """Hermetic env: the child must not read or write the developer's ~/.aelix.

    ``AELIX_CODING_AGENT_DIR`` / ``AELIX_SETTINGS_PATH`` are the seams the rest
    of the suite isolates through (``cli/config.py:36``).
    """

    return child_env(
        tmp_path / "home",
        PYTHONUNBUFFERED="1",
        AELIX_CODING_AGENT_DIR=str(tmp_path / "agent"),
        AELIX_SETTINGS_PATH=str(tmp_path / "settings.json"),
        NO_COLOR="1",
    )


class RpcChild:
    """A live ``--mode rpc`` child with both pipes pumped concurrently.

    Responses are correlated by ``id``; bare agent events (which carry no id --
    pi's documented design, ``rpc.md:746``) are collected separately.
    """

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._responses: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self._stderr = bytearray()
        self._arrived = asyncio.Event()
        self._buf = ""
        self._tasks: list[asyncio.Task[None]] = []

    @classmethod
    async def start(cls, tmp_path: Path) -> RpcChild:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "aelix_coding_agent",
            "--mode",
            "rpc",
            # Keep the child off the marketplace catalog fetch and out of the
            # ambient extension set: a failure here should be a protocol
            # failure, not a slow network round trip.
            "--no-extensions",
            cwd=str(tmp_path),
            env=_child_env(tmp_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        child = cls(proc)
        child._tasks = [
            asyncio.create_task(child._pump_stdout()),
            asyncio.create_task(child._pump_stderr()),
        ]
        return child

    async def stop(self) -> int | None:
        """Terminate the server. An rpc child never exits on its own."""

        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), _STOP_TIMEOUT_S)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        for task in self._tasks:
            task.cancel()
        return self._proc.returncode

    async def _pump_stdout(self) -> None:
        # Chunked reads, never readline(): ADR-0198 D6. Reassembling the
        # newline framing here is deliberate -- framing is part of the contract.
        assert self._proc.stdout is not None
        while chunk := await self._proc.stdout.read(4096):
            self._buf += chunk.decode("utf-8", "replace")
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if not (line := line.strip()):
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:  # pragma: no cover - diagnostic
                    continue
                if msg.get("type") == "response":
                    self._responses[str(msg.get("id"))] = msg
                else:
                    self.events.append(msg)
                self._arrived.set()

    async def _pump_stderr(self) -> None:
        assert self._proc.stderr is not None
        while chunk := await self._proc.stderr.read(65536):
            self._stderr.extend(chunk)

    async def send_raw(self, payload: bytes) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(payload)
        await self._proc.stdin.drain()

    async def request(self, req_id: str, **command: Any) -> dict[str, Any]:
        """Send a command and wait for the response carrying its ``id``."""

        await self.send_raw((json.dumps({"id": req_id, **command}) + "\n").encode())

        async def _wait() -> dict[str, Any]:
            while req_id not in self._responses:
                self._arrived.clear()
                await self._arrived.wait()
            return self._responses[req_id]

        try:
            return await asyncio.wait_for(_wait(), _BOOT_TIMEOUT_S)
        except TimeoutError:  # pragma: no cover
            pytest.fail(
                f"no response for id={req_id!r} within {_BOOT_TIMEOUT_S}s.\n"
                f"returncode={self._proc.returncode}\n"
                f"stderr:\n{self.stderr()}"
            )

    def stderr(self) -> str:
        return bytes(self._stderr).decode("utf-8", "replace")


async def test_rpc_child_boots_and_serves_a_command_sequence(tmp_path: Path) -> None:
    """A real ``--mode rpc`` child boots, and serves many correlated commands.

    Phase 1 is the regression guard for the ``cli/entry.py`` fix: before it, the
    child died during boot with ``RuntimeError: repo and fs must not be supplied
    when runtime_host is explicit`` (``rpc/rpc_mode.py:2068-2071``) and this first
    request never got an answer.
    """

    child = await RpcChild.start(tmp_path)
    try:
        # -- phase 1: it boots at all, and answers ------------------------
        first = await child.request("r1", type="get_state")
        assert first["type"] == "response", child.stderr()
        assert first["command"] == "get_state"
        assert first["success"] is True, child.stderr()
        assert first["id"] == "r1"
        # ``data`` is the camelCase state envelope (rpc_types.py ``_camel``).
        # Pin a key the shape guarantees rather than the model, which depends
        # on whatever the isolated config resolves to.
        assert first["data"]["isStreaming"] is False

        # -- phase 2: many commands on ONE process ------------------------
        # The precondition for a long-lived channel. No test anywhere sent two
        # commands to one live child before this one.
        second = await child.request("r2", type="get_state")
        third = await child.request("r3", type="get_commands")
        assert [r["id"] for r in (second, third)] == ["r2", "r3"]
        assert second["success"] is True
        assert third["success"] is True and third["command"] == "get_commands"

        # -- phase 3: failure is in-band and correlated, not fatal --------
        bad = await child.request("r4", type="no_such_command_at_all")
        assert bad["type"] == "response"
        assert bad["success"] is False
        assert bad["id"] == "r4"
        # pi's error payload is free text, not a code space (rpc-types.ts:206).
        # Pinned so that adding a structured error later is a deliberate,
        # visible break rather than a silent one.
        assert isinstance(bad["error"], str) and bad["error"]

        # -- phase 4: a malformed line must not kill the server -----------
        await child.send_raw(b"{ this is not json\n")
        after = await child.request("r5", type="get_state")
        assert after["success"] is True, (
            "a malformed stdin line killed the server:\n" + child.stderr()
        )
        assert after["id"] == "r5"
    finally:
        await child.stop()
