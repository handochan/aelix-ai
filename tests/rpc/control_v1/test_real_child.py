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
import stat
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


async def _launch_transcript_child(seed: rigs.Seed, *extra: str, umask: int = -1) -> ChildWire:
    env = {**_env(seed), "AELIX_655_FIXTURE_PROVIDER": "1"}
    args = _argv("--provider", "fixture-transcript", "--model", "local", "--tools", "fixture_echo")
    proc = await asyncio.create_subprocess_exec(
        *args, *extra, cwd=str(seed.cwd), env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024**2,
        umask=umask,
    )
    return ChildWire(proc)


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX umask")
async def test_real_child_write_only_session_receipts_are_storage_only(tmp_path: Path) -> None:
    seed = rigs.seed_files(tmp_path)
    child = await _launch_transcript_child(seed, "--session-dir", str(seed.sessions), umask=0o477)
    try:
        child.send({"id": "startup", "type": "get_state"})
        startup = await asyncio.wait_for(child.response("startup"), BOOT_S)
        path = Path(startup["data"]["sessionFile"])
        assert stat.S_IMODE(path.stat().st_mode) == 0o200
        child.send({"type": "prompt", "id": "plain", "message": "ordinary content"})
        await asyncio.wait_for(child.response("plain"), BOOT_S)
        await asyncio.wait_for(child.record(lambda row: row["type"] == "agent_end"), BOOT_S)
        ends = [row for row in child.records if row["type"] == "message_end"]
        assert [row["message"]["role"] for row in ends] == ["user", "assistant"]
        refused = await rigs.request(child, "read", "transcript/list", {})
        assert refused["error"] == "Transcript unavailable"
        assert [row["transcript"]["status"] for row in ends] == ["storageOnly"] * 2
    finally:
        await child.stop()


async def test_real_child_transcript_preserves_cli_rows_after_restart(tmp_path: Path) -> None:
    seed = rigs.seed_files(tmp_path / "transcript")
    fixture, case = next(
        (fixture, case) for fixture, case in rigs.in_process_cases()
        if fixture.get("method") == "transcript/list"
        and case["name"] == "tool result ends with stored coordinates"
    )
    first = await _launch_transcript_child(seed)
    try:
        first.send({"id": "startup", "type": "get_state"})
        startup = await asyncio.wait_for(first.response("startup"), BOOT_S)
        assert startup["success"], startup
        path = startup["data"]["sessionFile"]
        # The real process begins with an empty transcript. The fixture's
        # in-process rig begins with a seed user row.
        result = await asyncio.wait_for(rigs.run_case(fixture, case, first, {}), BOOT_S)
        assert result.response["success"]
        original = result.response["data"]
        assert original["totalMessages"] == 4
        assert [row["message"]["role"] for row in original["entries"]] == [
            "user", "assistant", "toolResult", "assistant"
        ]
        receipt, = [frame["transcript"] for frame in result.frames]
        assert receipt["status"] == "stored" and receipt["sequence"] == 3
        assert receipt["entryId"] == original["entries"][2]["entryId"]
        short = await rigs.request(first, "short", "transcript/list", {"limit": 1})
        cursor = short["data"]["nextCursor"]
        assert cursor
        first.send(eof=True)
        await asyncio.wait_for(first.proc.wait(), STOP_S)
        assert first.proc.returncode == 0, first.text_stderr()
    finally:
        await first.stop()

    second = await _launch_transcript_child(seed, "--session", path)
    try:
        restored = await rigs.request(second, "restored", "transcript/list", {})
        assert restored["success"], second.text_stderr()
        page = restored["data"]
        assert page["bindingId"] != original["bindingId"]
        assert page["nativeSessionId"] == original["nativeSessionId"]
        assert page["leafId"] == original["leafId"]
        assert page["entries"] == original["entries"]
        stale = await rigs.request(second, "foreign", "transcript/list", {"cursor": cursor})
        assert stale["success"] is False and stale["error"] == "Stale transcript cursor"
    finally:
        await second.stop()


@pytest.mark.parametrize("shape", ["duplicate", "dangling", "forward", "cycle-sibling"])
async def test_cli_opens_legacy_damage_and_only_transcript_refuses_the_selection(
    tmp_path: Path, shape: str,
) -> None:
    seed = rigs.seed_files(tmp_path / "damage")
    history = tmp_path / "history.jsonl"
    stamp = "2026-11-20T00:00:00Z"

    def message(entry_id: str, parent: str | None, text: str) -> dict[str, Any]:
        return {
            "type": "message", "id": entry_id, "parentId": parent, "timestamp": stamp,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        }

    first, last = message("u", None, "first"), message("v", "u", "last")
    if shape == "duplicate":
        rows = [first, message("side", "u", "old"), message("side", "u", "new"), last]
        selected = "v"
    elif shape == "forward":
        rows = [last, first, {
            "type": "leaf", "id": "pick", "parentId": None, "targetId": "v", "timestamp": stamp,
        }]
        selected = "v"
    else:
        parent = "missing" if shape == "dangling" else "bad"
        rows = [first, message("bad", parent, "off-branch"), last]
        selected = "bad"
    header = {
        "type": "session", "version": 3, "id": "legacy", "cwd": str(seed.cwd), "timestamp": stamp,
    }
    content = b"".join((json.dumps(row) + "\n").encode() for row in [header, *rows])
    history.write_bytes(content)
    child = await _launch_transcript_child(seed, "--session", str(history))
    try:
        child.send({"id": "classic", "type": "get_messages"})
        classic = await asyncio.wait_for(child.response("classic"), BOOT_S)
        assert classic["success"], classic
        assert [row["content"][0]["text"] for row in classic["data"]["messages"]] == ["first", "last"]
        refusal = await rigs.request(child, "selected", "transcript/list", {"leafId": selected})
        assert refusal["id"] == "selected" and refusal["error"] == "invalidTree"
        if selected == "bad":
            active = await rigs.request(child, "active", "transcript/list", {})
            assert [row["entryId"] for row in active["data"]["entries"]] == ["u", "v"]
        assert history.read_bytes() == content
    finally:
        await child.stop()


async def test_half_close_during_a_held_turn_keeps_the_classic_no_session_output(
    tmp_path: Path,
) -> None:
    seed = rigs.seed_files(tmp_path / "half-close")
    for flags, expected in [(("--no-session",), []), ((), ["turn_end", "agent_end"])]:
        child = await _launch_transcript_child(seed, *flags)
        try:
            child.send({"type": "prompt", "id": "held", "message": "fixture wait"})
            await asyncio.wait_for(child.response("held"), BOOT_S)
            await asyncio.wait_for(
                child.record(lambda frame: frame["type"] == "message_update"), BOOT_S,
            )
            before = len(child.records)
            child.send(eof=True)
            await asyncio.wait_for(child.proc.wait(), STOP_S)
            await asyncio.gather(*child._tasks)
            assert child.proc.returncode == 0, child.text_stderr()
            assert [frame["type"] for frame in child.records[before:]] == expected
        finally:
            await child.stop()


async def test_an_unencodable_header_keeps_classic_events_and_hides_transcript(
    tmp_path: Path,
) -> None:
    seed = rigs.seed_files(tmp_path / "header")
    history = tmp_path / "header.jsonl"
    header = {
        "type": "session", "version": 3, "id": "legacy" + chr(0xD800),
        "cwd": str(seed.cwd), "timestamp": "2026-11-20T00:00:00Z",
    }
    history.write_bytes((json.dumps(header) + "\n").encode())
    child = await _launch_transcript_child(seed, "--session", str(history))
    try:
        capability = await rigs.request(child, "caps", "capabilities", {})
        assert "transcript/list" not in capability["data"]["methods"]
        child.send({"type": "prompt", "id": "plain", "message": "ordinary content"})
        await asyncio.wait_for(child.response("plain"), BOOT_S)
        await asyncio.wait_for(child.record(lambda row: row["type"] == "agent_end"), BOOT_S)
        messages = [row for row in child.records if row["type"] == "message_end"]
        assert [row["message"]["role"] for row in messages] == ["user", "assistant"]
        assert all("transcript" not in row for row in messages)
        refused = await rigs.request(child, "read", "transcript/list", {"leafId": None})
        assert refused["id"] == "read" and refused["error"] == "Transcript unavailable on this binding"
    finally:
        await child.stop()


@pytest.mark.parametrize(("reply_size", "advertised"), [(1_048_576, True), (1_048_577, False)])
async def test_discovery_checks_the_worst_case_empty_reply_size(
    tmp_path: Path, reply_size: int, advertised: bool,
) -> None:
    from aelix_coding_agent.rpc.rpc_mode import _reply_bytes
    from aelix_coding_agent.rpc.rpc_types import RpcSuccessResponse

    seed = rigs.seed_files(tmp_path / "discovery")
    history = tmp_path / "discovery.jsonl"
    widest_id = "\x00" * 170 + "xx"  # 1,024 ASCII-escaped JSON bytes, including quotes.
    data = {
        "bindingId": "a" * 32, "nativeSessionId": "", "leafId": None, "totalMessages": 0,
        "entries": [], "nextCursor": None, "recovered": True,
    }
    empty = RpcSuccessResponse(id=widest_id, command="aelix_control", data=data)
    native_id = "x" * (reply_size - len(_reply_bytes(empty)))
    data["nativeSessionId"] = native_id
    assert len(_reply_bytes(empty)) == reply_size
    header = {
        "type": "session", "version": 3, "id": native_id,
        "cwd": str(seed.cwd), "timestamp": "2026-11-20T00:00:00Z",
    }
    history.write_bytes((json.dumps(header) + "\n").encode())
    child = await _launch_transcript_child(seed, "--session", str(history))
    try:
        capability = await rigs.request(child, "caps", "capabilities", {})
        assert ("transcript/list" in capability["data"]["methods"]) is advertised
    finally:
        await child.stop()
