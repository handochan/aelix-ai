"""The ``"replay": true`` cases on one real ``python -m aelix_coding_agent --mode rpc`` child (#623, ADR-0302 D8).

The wire is the contract, and an in-process capture cannot fail the way a pipe
can (ADR-0198 D7). The child is rig ``child``: the files ``rigs.seed_files``
writes, a scratch HOME, agent dir, settings path and cwd (as the real-child bed
``tests/rpc/test_rpc_real_child.py`` sets them), launched as aelix-acp launches
it (``--mode rpc --offline --no-extensions``) plus ``-e`` the fixture
extension. It boots once. Every replay case sets what it reads with its own
``before``, so the cases run in a seeded random order and then in a second
order on the same child; the seed is printed.

The locked-session case is a second launch, ``--session <the first child's own
session file>``, which a real process holds: it must exit non-zero with the
text in its stderr.
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.env_sandbox import child_env
from tests.rpc.control_v1 import rigs
from tests.rpc.control_v1.rigs import rig_env  # noqa: F401  (the fixture, by name)

pytestmark = pytest.mark.usefixtures("rig_env")

BOOT_S = 120.0
STOP_S = 15.0
REPLAY = [
    (fixture, case)
    for fixture in rigs.load_fixtures()
    for case in fixture["cases"]
    if case.get("replay") is True and "launch" not in case
]
LAUNCH = [
    (fixture, case)
    for fixture in rigs.load_fixtures()
    for case in fixture["cases"]
    if case.get("replay") is True and "launch" in case
]


def _argv(*extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "aelix_coding_agent",
        "--mode",
        "rpc",
        "--offline",
        "--no-extensions",
        "-e",
        str(rigs.EXTENSION_FILE),
        *extra,
    ]


def _env(seed: rigs.Seed) -> dict[str, str]:
    return child_env(
        seed.home,
        PYTHONUNBUFFERED="1",
        AELIX_CODING_AGENT_DIR=str(seed.agent_dir),
        AELIX_SETTINGS_PATH=str(seed.settings_path),
        NO_COLOR="1",
    )


class ChildWire:
    """The real child's pipes behind the in-memory ``Wire``'s interface (``send``, ``response``, ``record``)."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.records: list[dict[str, Any]] = []
        self.stderr = bytearray()
        self._changed = asyncio.Event()
        self._tasks = [asyncio.create_task(self._pump()), asyncio.create_task(self._pump_stderr())]

    async def _pump(self) -> None:
        assert self.proc.stdout is not None
        buffer = b""
        while chunk := await self.proc.stdout.read(65536):
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    self.records.append(json.loads(line))
            self._changed.set()

    async def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(65536):
            self.stderr.extend(chunk)

    def send(self, *payloads: dict[str, Any], eof: bool = False) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(b"".join((json.dumps(p) + "\n").encode() for p in payloads))
        if eof:
            self.proc.stdin.close()

    async def record(self, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
        while True:
            self._changed.clear()
            for record in self.records:
                if predicate(record):
                    return record
            if self.proc.returncode is not None:
                raise AssertionError(
                    f"the child exited ({self.proc.returncode}):\n{self.text_stderr()}"
                )
            await self._changed.wait()

    async def response(self, request_id: str) -> dict[str, Any]:
        return await self.record(
            lambda r: r.get("type") == "response" and r.get("id") == request_id
        )

    def text_stderr(self) -> str:
        return bytes(self.stderr).decode("utf-8", "replace")[-4000:]

    async def stop(self) -> None:
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), STOP_S)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


async def _replay(
    wire: ChildWire,
    values: dict[str, Any],
    order: list[tuple[dict[str, Any], dict[str, Any]]],
    tag: str,
) -> None:
    for number, (fixture, case) in enumerate(order):
        prefix = f"{tag}{number}-"
        try:
            result = await asyncio.wait_for(
                rigs.run_case(fixture, case, wire, values, prefix=prefix), BOOT_S
            )
            rigs.assert_case(fixture, case, result, prefix=prefix)
        except BaseException as exc:
            raise AssertionError(
                f"order {tag} step {number}: {fixture['_file']} {case['name']!r}: {exc}\n"
                f"stderr:\n{wire.text_stderr()}"
            ) from exc


async def test_replay_cases_on_one_real_child(tmp_path: Path) -> None:
    assert REPLAY and LAUNCH, "no replay case found"
    seed = rigs.seed_files(tmp_path / "child")
    proc = await asyncio.create_subprocess_exec(
        *_argv(),
        cwd=str(seed.cwd),
        env=_env(seed),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    wire = ChildWire(proc)
    try:
        wire.send({"type": "get_state", "id": "state"})
        state = await asyncio.wait_for(wire.response("state"), BOOT_S)
        assert state["success"] is True, wire.text_stderr()
        values = {
            "python": sys.executable,
            "cwd": str(seed.cwd),
            "root": str(seed.root),
            "mcp_script": str(seed.mcp_script),
            "locked_session": state["data"]["sessionFile"],
        }
        assert Path(values["locked_session"]).is_file(), state
        seed_value = random.SystemRandom().randrange(1 << 30)
        print(f"replay order seed: {seed_value}")
        first = REPLAY[:]
        random.Random(seed_value).shuffle(first)
        second = first[::-1]
        await _replay(wire, values, first, "a")
        await _replay(wire, values, second, "b")
        for fixture, case in LAUNCH:
            await _launch(seed, fixture, case, values)
    finally:
        await wire.stop()


async def _launch(
    seed: rigs.Seed, fixture: dict[str, Any], case: dict[str, Any], values: dict[str, Any]
) -> None:
    args = [str(rigs.resolve(arg, values)) for arg in case["launch"]["args"]]
    proc = await asyncio.create_subprocess_exec(
        *_argv(*args),
        cwd=str(seed.cwd),
        env=_env(seed),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(b""), BOOT_S)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    stderr = err.decode("utf-8", "replace")
    label = f"{fixture['_file']} {case['name']!r}"
    assert proc.returncode not in (0, None), f"{label}: exited {proc.returncode}\n{stderr}"
    assert not rigs.schema_rejects(case["stderr"], stderr), f"{label}: stderr was {stderr!r}"
    assert out.strip() == b"", f"{label}: wrote to stdout: {out[:500]!r}"
