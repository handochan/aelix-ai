"""#611 S2d (#760) — ``bash(run_in_background=true)``: background bash as the job service's second kind (ADR-0297).

AT WHAT LEVEL. The REAL ``bash`` tool (``create_bash_tool``) over the real local
backend, the REAL job service bound to a harness with the kernel inbox on and
the ``bash`` kind registered as the CLI registers it, real shells and real
processes. The gate is forced on per test with ``jobs/activation.py``'s seam
(``jobs_active``); the first test pins the tool with it off. The CLI-level
cases build the harness options the CLI builds (``_build_harness_options``)
from a cwd of their own.

Fails on ``71f837eb``: the parameter does not exist and is ignored, so every
background call runs in the foreground — a call that should return at once
blocks for the command (bounded here by :func:`_call`'s 5 s), its output comes
back as the call's result, no job exists, no notice comes, and no host or
backend refuses it. The kind, its paging and its restart reading are new
(``tools/bash_background.py`` is absent). How each one fails on base is in its
docstring where it is not that.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import warnings
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem, Session
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent
from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model
from aelix_ai.tools import ToolExecutionContext, ToolResult
from aelix_ai.utils._process_identity import pid_is_zombie, process_started
from aelix_coding_agent.jobs import activation, evidence
from aelix_coding_agent.jobs.notice import NOTICE_MAX_BYTES, PLACEMENT_LINE_RESERVE
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE, append_fact, fold, make_fact
from aelix_coding_agent.jobs.tool import create_job_stop_tool, create_job_tool
from aelix_coding_agent.modes.print_mode import run_print_mode
from aelix_coding_agent.tools import create_all_tools
from aelix_coding_agent.tools.bash import _build_bash_parameters, create_bash_tool

from tests.jobs.conftest import (  # noqa: F401 — jobs_active: the gate's test seam
    fast_service,
    jobs_active,
    make_harness,
    make_session,
    reopen,
    wait_for,
)

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell syntax and process groups")
WIN32 = pytest.mark.skipif(sys.platform != "win32", reason="windows-latest only (#611 S3's Windows acceptance)")
LINUX = pytest.mark.skipif(sys.platform != "linux", reason="pdeathsig is Linux's")

_ID = re.compile(r"job ([0-9a-f]{16})")
_ENDED = ("done", "failed", "stopped", "lost", "unresolved")


def _bg() -> Any:
    """The module under test, imported late so this file collects on ``71f837eb``."""

    from aelix_coding_agent.tools import bash_background

    return bash_background


@dataclass
class _Rig:
    service: Any
    harness: AgentHarness
    session: Session
    tool: Any
    work: Path

    async def call(self, args: dict[str, Any], timeout: float = 5.0) -> ToolResult:
        return await _call(self.tool, args, timeout)

    async def start(self, command: str, **extra: Any) -> str:
        result = await self.call({"command": command, "run_in_background": True, **extra})
        text = _text(result)
        assert not result.is_error, text
        found = _ID.search(text)
        assert found is not None, f"no job id in {text!r}"
        return found.group(1)

    async def ended(self, job_id: str, timeout: float = 15.0) -> Any:
        await wait_for(lambda: self.service.snapshot(job_id).state in _ENDED, timeout)
        return self.service.snapshot(job_id)

    def notices(self, job_id: str) -> list[Any]:
        return [n for n in self.harness.undelivered_notices() if n.notice_id.startswith(job_id)]


async def _call(tool: Any, args: dict[str, Any], timeout: float = 5.0) -> ToolResult:
    """One ``execute``, bounded: on ``71f837eb`` a background call runs in the foreground."""

    return await asyncio.wait_for(
        tool.execute(dict(args), ToolExecutionContext(tool_call_id="tc-1")), timeout
    )


def _text(result: ToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


@pytest.fixture(autouse=True)
def _own_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Output files go under this test's own temp dir, never the developer's."""

    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture
async def rig(tmp_path: Path, request: pytest.FixtureRequest) -> AsyncIterator[_Rig]:
    request.getfixturevalue("jobs_active")
    made = await _make_rig(tmp_path)
    try:
        yield made
    finally:
        await made.service.shutdown()


async def _make_rig(
    tmp_path: Path, *, unattended: bool = True, options: dict[str, Any] | None = None, **service: Any
) -> _Rig:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    jobs = fast_service(**service)
    with contextlib.suppress(ImportError):  # absent on ``71f837eb``: the call then runs as it did
        _bg().register(jobs)
    activation.install_service(jobs)
    await jobs.bind(harness)
    tool = create_bash_tool(
        str(work),
        {"background_host": lambda: True, "unattended": lambda: unattended, **(options or {})},
    )
    return _Rig(jobs, harness, session, tool, work)


def _alive(pid: int) -> bool:
    """Alive and not a zombie. Never ``os.kill(pid, 0)`` on win32, where it is TerminateProcess."""

    if sys.platform == "win32":
        return process_started(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return pid_is_zombie(pid) is not True


def _kill(*pids: int) -> None:
    for pid in pids:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL if sys.platform != "win32" else signal.SIGTERM)


async def _read_pid(path: Path, timeout: float = 5.0) -> int:
    await wait_for(lambda: path.exists() and path.read_text().strip() != "", timeout)
    return int(path.read_text().split()[0])


def _py(code: str) -> str:
    """A command that runs ``code`` with this interpreter, quoted for a POSIX shell."""

    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


# ── the gate and the host ──────────────────────────────────────────


async def test_the_parameter_is_absent_with_the_gate_off(tmp_path: Path) -> None:
    """G: off, the tool is the tool it was before #611, and the parameter is ignored."""

    tool = create_bash_tool(str(tmp_path), {"background_host": lambda: True, "unattended": lambda: True})
    assert tool.parameters == _build_bash_parameters(600.0, 3600.0)
    assert tool.parameters == create_bash_tool(str(tmp_path)).parameters
    assert tool.description == create_bash_tool(str(tmp_path)).description
    result = await _call(tool, {"command": "echo foreground", "run_in_background": True})
    assert not result.is_error and _text(result).strip() == "foreground"


@POSIX
async def test_a_background_command_returns_its_id_at_once_and_one_notice_comes_back(rig: _Rig) -> None:
    began = time.monotonic()
    job_id = await rig.start("sleep 1; echo finished")
    assert time.monotonic() - began < 0.9, "returned before the command ended"
    assert rig.service.snapshot(job_id).state == "running"
    snap = await rig.ended(job_id)
    assert snap.state == "done" and snap.brief == "exit code 0"
    (notice,) = rig.notices(job_id)
    assert notice.notice_id == f"{job_id}:final"
    assert "finished" in notice.body and "It exited with code 0." in notice.body


@POSIX
@pytest.mark.parametrize(
    ("filler", "invalid"),
    [
        ("x" * 99 + "\n", 0),
        ("x" * 99 + "\n", 3000),
        ("[aelix system notice " * 5 + "\n", 3000),
        ("<background-job-result>\n", 3000),
    ],
    ids=["plain", "invalid-bytes", "forged-header", "forged-tag"],
)
async def test_the_notice_holds_a_tail_and_fits_16_kib(rig: _Rig, filler: str, invalid: int) -> None:
    code = (
        "import sys\n"
        "sys.stdout.write('HEAD-MARK\\n')\n"
        f"sys.stdout.write({filler!r} * 4000)\n"
        "sys.stdout.flush()\n"
        f"sys.stdout.buffer.write(b'\\xff\\xfe' * {invalid})\n"
        "sys.stdout.write('\\nTAIL-MARK\\n')\n"
    )
    job_id = await rig.start(_py(code))
    snap = await rig.ended(job_id)
    assert snap.state == "done"
    (notice,) = rig.notices(job_id)
    size = len(notice.body.encode("utf-8"))
    assert size <= NOTICE_MAX_BYTES - PLACEMENT_LINE_RESERVE, size
    assert "TAIL-MARK" in notice.body and "HEAD-MARK" not in notice.body
    assert "cut here" not in notice.body, "the kind cut it; the frame did not have to"
    path = snap.client_start["output"]
    total = Path(path).stat().st_size
    assert f"Full output: {path} ({total} bytes); job(action=\"result\", id=\"{job_id}\")" in notice.body
    assert f"{total} bytes in all" in notice.body
    assert notice.body.count("</background-job-result>") == 1


@POSIX
async def test_past_the_kept_size_the_rest_is_discarded_with_one_line(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The contract's 1 GiB, at 1000 bytes here: the file stops growing, says so once, and the tail is still the end."""

    monkeypatch.setattr(_bg(), "_KEEP_BYTES", 1000)
    # Flushed a line at a time, so the reader takes many chunks past the limit.
    code = (
        "import sys, time\n"
        "for _ in range(50):\n"
        "    sys.stdout.write('y' * 99 + '\\n'); sys.stdout.flush(); time.sleep(0.005)\n"
        "print('LAST')"
    )
    job_id = await rig.start(_py(code))
    snap = await rig.ended(job_id)
    assert snap.state == "done"
    kept = Path(snap.client_start["output"]).read_bytes()
    assert kept.startswith(b"y" * 99 + b"\n") and kept.count(b"discarded") == 1
    assert len(kept) == 1000 + len(_bg()._DISCARD_LINE)
    (notice,) = rig.notices(job_id)
    assert "LAST" in notice.body
    assert f"Past 1 GiB, {50 * 100 + 5 - 1000} bytes were discarded." in notice.body


@POSIX
async def test_a_file_that_cannot_be_written_is_said_and_the_read_goes_on(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(fd: int, data: Any) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_bg(), "_write_fd", full)
    job_id = await rig.start("echo one; echo two")
    snap = await rig.ended(job_id)
    assert snap.state == "done", "the command still ran to its end"
    (notice,) = rig.notices(job_id)
    assert "two" in notice.body
    assert "Writing the file failed (OSError: [Errno 28] No space left on device)" in notice.body


async def test_an_output_file_that_cannot_be_opened_fails_the_launch_before_a_process_exists(
    rig: _Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[Any] = []
    monkeypatch.setattr(_bg(), "create_output_file", lambda: str(tmp_path / "gone" / "x.log"))
    monkeypatch.setattr(_bg(), "_popen_background", lambda *a, **k: spawned.append(a))
    result = await rig.call({"command": "echo never", "run_in_background": True})
    assert result.is_error and "FileNotFoundError" in _text(result)
    assert spawned == [], "no process was started for a file it could not write"
    (snap,) = rig.service.snapshots()
    assert snap.state == "failed" and rig.harness.undelivered_notices() == []


# ── paging (r3 §5, Codex #13, N12) ─────────────────────────────────


def _pages(path: Path, *, limit: int, ended: bool, offset: int = 0) -> list[Any]:
    pages, at = [], offset
    for _ in range(10_000):
        page = _bg().page_file(str(path), offset=at, limit=limit, ended=ended)
        pages.append(page)
        assert page.next_offset is not None and page.offset == at
        if not page.more:
            return pages
        assert page.next_offset > at, "every page moves forward"
        at = page.next_offset
    raise AssertionError("paging did not end")


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 7, 16])
def test_paging_never_splits_a_multibyte_character(tmp_path: Path, limit: int) -> None:
    text = "a가😀b나é다\n" * 5
    path = tmp_path / "out.log"
    path.write_bytes(text.encode("utf-8"))
    pages = _pages(path, limit=limit, ended=True)
    assert "".join(p.text for p in pages) == text
    assert "�" not in "".join(p.text for p in pages)
    assert pages[-1].next_offset == len(text.encode("utf-8"))


def test_paging_over_invalid_bytes_advances_by_bytes(tmp_path: Path) -> None:
    raw = b"ab\xff\xfecd\xc3(ef"
    path = tmp_path / "out.log"
    path.write_bytes(raw)
    pages = _pages(path, limit=3, ended=True)
    assert "".join(p.text for p in pages) == raw.decode("utf-8", errors="replace")
    assert [p.next_offset for p in pages] == [3, 6, 9, 10]


def test_a_truncated_character_before_a_whole_one_consumes_only_itself(tmp_path: Path) -> None:
    """``가``'s first two bytes, then ``一``: at limit 1 the broken one is one U+FFFD of two bytes."""

    path = tmp_path / "out.log"
    path.write_bytes("가".encode()[:2] + "一".encode())
    pages = _pages(path, limit=1, ended=True)
    assert [(p.text, p.next_offset) for p in pages] == [("\ufffd", 2), ("一", 5)]


def test_paging_from_a_nonzero_offset_advances_by_bytes(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes("가나다라".encode())
    page = _bg().page_file(str(path), offset=3, limit=4, ended=True)
    assert (page.text, page.next_offset, page.more) == ("나", 6, True)
    page = _bg().page_file(str(path), offset=page.next_offset, limit=6, ended=True)
    assert (page.text, page.next_offset, page.more) == ("다라", 12, False)


def test_a_limit_smaller_than_a_code_point_still_progresses(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes("😀가".encode())
    page = _bg().page_file(str(path), offset=0, limit=1, ended=False)
    assert (page.text, page.next_offset, page.more) == ("😀", 4, True)
    page = _bg().page_file(str(path), offset=4, limit=2, ended=False)
    assert (page.text, page.next_offset, page.more) == ("가", 7, False)


def test_a_truncated_final_sequence_is_consumed_once_the_job_ended(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"ok" + "가".encode()[:2])
    page = _bg().page_file(str(path), offset=0, limit=16, ended=True)
    assert (page.text, page.next_offset, page.more) == ("ok�", 4, False)
    page = _bg().page_file(str(path), offset=2, limit=1, ended=True)
    assert (page.text, page.next_offset, page.more) == ("�", 4, False)


def test_a_running_jobs_incomplete_tail_is_not_more(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"ok" + "가".encode()[:2])
    page = _bg().page_file(str(path), offset=0, limit=16, ended=False)
    assert (page.text, page.next_offset, page.more) == ("ok", 2, False)
    page = _bg().page_file(str(path), offset=2, limit=16, ended=False)
    assert (page.text, page.next_offset, page.more) == ("", 2, False)
    # The writer finishes the character: the same offset now reads it.
    path.write_bytes(b"ok" + "가".encode())
    page = _bg().page_file(str(path), offset=2, limit=16, ended=False)
    assert (page.text, page.next_offset, page.more) == ("가", 5, False)


def test_its_output_decodes_with_replace_on_cp1252(tmp_path: Path) -> None:
    """A cp1252 child's bytes (``café`` is ``caf\\xe9``) read as U+FFFD, never an error (portable half of the win32 I case)."""

    path = tmp_path / "out.log"
    path.write_bytes("café crème\r\n".encode("cp1252"))
    page = _bg().page_file(str(path), offset=0, limit=64, ended=True)
    assert page.text == "caf� cr�me\r\n" and page.next_offset == 12


@POSIX
async def test_job_result_pages_a_real_jobs_file_by_bytes(rig: _Rig) -> None:
    job_id = await rig.start(_py("import sys; sys.stdout.write('가' * 6000)"))
    await rig.ended(job_id)
    result = await create_job_tool(rig.service).execute({"action": "result", "id": job_id})
    body, _, hint = _text(result).rpartition("\n")
    assert body == "가" * 5461
    assert hint == f'… more: job(action="result", id="{job_id}", offset={5461 * 3})'
    result = await create_job_tool(rig.service).execute({"action": "result", "id": job_id, "offset": 5461 * 3})
    assert _text(result) == "가" * (6000 - 5461)


# ── where it may run (Codex #14) ───────────────────────────────────


@pytest.fixture
def own_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """#733: a cwd with no project resources for the CLI-level cases."""

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


async def _cli_bash(mode: str) -> Any:
    from aelix_agent_core.session.memory_storage import MemorySessionStorage
    from aelix_coding_agent.cli.args import Args
    from aelix_coding_agent.cli.entry import _build_harness_options

    options = await _build_harness_options(Args(), Session(MemorySessionStorage()), app_mode=mode)
    (bash,) = [t for t in options.tools if t.name == "bash"]
    return bash


@pytest.mark.usefixtures("own_cwd", "own_agent_dir", "jobs_active")
async def test_rpc_and_acp_never_advertise_or_accept_it(tmp_path: Path) -> None:
    """The CLI's own options: RPC (and ACP over it) never offers it; the TUI, -p and json do."""

    jobs = fast_service()
    _bg().register(jobs)
    activation.install_service(jobs)
    try:
        rpc = await _cli_bash("rpc")
        assert "run_in_background" not in rpc.parameters["properties"]
        result = await _call(rpc, {"command": "echo ran", "run_in_background": True})
        assert result.is_error and _text(result) == _bg().REFUSED_HERE
        assert jobs.snapshots() == []
        for mode in ("interactive", "print", "json"):
            offered = await _cli_bash(mode)
            described = offered.parameters["properties"]["run_in_background"]["description"]
            # The TUI is attended (no default limit); -p and json are not (owner OQ-B).
            assert ("stopped after 600s" in described) is (mode != "interactive"), mode
    finally:
        await jobs.shutdown()


async def test_a_direct_factory_call_without_the_host_capability_refuses(rig: _Rig) -> None:
    for tool in (create_bash_tool(str(rig.work)), create_all_tools(str(rig.work))["bash"]):
        assert "run_in_background" not in tool.parameters["properties"]
        result = await _call(tool, {"command": "echo ran", "run_in_background": True})
        assert result.is_error and _text(result) == _bg().REFUSED_HERE
    assert rig.service.snapshots() == []


async def test_a_host_that_says_no_at_the_call_refuses(rig: _Rig) -> None:
    """Checked again on each call: a host whose answer turned refuses what its schema listed."""

    answer = {"yes": True}
    tool = create_bash_tool(str(rig.work), {"background_host": lambda: answer["yes"]})
    assert "run_in_background" in tool.parameters["properties"]
    answer["yes"] = False
    result = await _call(tool, {"command": "echo ran", "run_in_background": True})
    assert result.is_error and _text(result) == _bg().REFUSED_HERE
    assert rig.service.snapshots() == []


@pytest.mark.parametrize("answer", ["raises", "no"])
async def test_a_host_that_cannot_answer_offers_nothing(rig: _Rig, answer: str) -> None:
    def host() -> bool:
        if answer == "raises":
            raise RuntimeError("no host")
        return False

    tool = create_bash_tool(str(rig.work), {"background_host": host})
    assert "run_in_background" not in tool.parameters["properties"]


@POSIX
async def test_a_tool_not_told_its_host_is_attended_gets_the_unattended_default(rig: _Rig) -> None:
    """A missing ``unattended`` option means unattended (r3 §10): the default limit applies."""

    tool = create_bash_tool(str(rig.work), {"background_host": lambda: True, "default_timeout": 1.0})
    result = await _call(tool, {"command": "sleep 30", "run_in_background": True})
    found = _ID.search(_text(result))
    assert found is not None, _text(result)
    assert rig.service.snapshot(found.group(1)).client_start["timeout"] == 1.0


async def test_the_cli_registers_the_kind_while_jobs_are_on(rig: _Rig) -> None:
    from aelix_coding_agent.cli.entry import _with_bash_kind

    jobs = fast_service()
    assert _with_bash_kind(jobs) is jobs
    registration = jobs.registry.get("bash")
    assert registration is not None and isinstance(registration.adapter, _bg().BashKindAdapter)
    assert registration.adapter.capabilities.normal_exit == "release"
    assert registration.adapter.capabilities.default_bound == 4


@POSIX
def test_the_output_directory_and_file_are_this_users_alone(_own_tempdir: Path) -> None:
    path = Path(_bg().create_output_file())
    assert path.parent == _bg().output_dir() == _own_tempdir / path.parent.name
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600 and path.read_bytes() == b""
    path.parent.chmod(0o755)
    with pytest.raises(OSError, match="open to other users"):
        _bg().output_dir()
    path.parent.chmod(0o700)
    moved = path.parent.with_name(path.parent.name + "-real")
    path.parent.rename(moved)
    moved.with_name(path.parent.name).symlink_to(moved)
    with pytest.raises(OSError, match="not a directory"):
        _bg().output_dir()


async def test_an_exit_record_of_no_known_status_is_not_a_recovery() -> None:
    adapter = _bg().BashKindAdapter()
    record = {"custom_type": _bg().RECORD_TYPE, "phase": "exit", "status": "weird", "exit_code": 0}
    assert await adapter.recover({}, [record]) is None
    assert await adapter.recover({}, []) is None
    done = await adapter.recover({}, [{**record, "status": "done"}])
    assert done is not None and (done.status, done.payload["exit_code"]) == ("done", 0)


def test_a_restart_decodes_an_ended_command_from_its_outcome_and_exit_record() -> None:
    record = {"custom_type": _bg().RECORD_TYPE, "phase": "exit", "status": "stopped", "exit_code": -15, "stop_reason": "user", "bytes": 12}
    outcome = _bg().BashKindAdapter().decode({"fact": {"status": "stopped", "brief": "b"}, "records": [record]})
    assert (outcome.status, outcome.brief) == ("stopped", "b")
    assert dict(outcome.payload) == {"exit_code": -15, "stop_reason": "user", "bytes": 12}
    unconfirmed = _bg().BashKindAdapter().decode({"fact": {"status": "done"}, "records": [{**record, "output_unconfirmed": True}]})
    assert unconfirmed.payload["output_unconfirmed"] is True
    assert _bg().BashKindAdapter().decode({"fact": {"status": "lost"}, "records": []}).status == "failed"


def test_the_tail_drops_a_character_its_cut_split() -> None:
    raw = "가나".encode()[1:]  # the cut took the first byte of 가
    assert _bg()._decode_tail(raw, cut=True) == "나"
    assert _bg()._decode_tail(b"ok\xff", cut=True) == "ok\ufffd"
    # Uncut, every byte is the command's own (Codex 8).
    assert _bg()._decode_tail(b"\x80\x81\x82OK", cut=False) == "\ufffd\ufffd\ufffdOK"


class _RemoteOps:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def exec(self, command: str, cwd: str, **_: Any) -> Any:
        from aelix_coding_agent.tools.bash import ExecExitResult

        self.calls.append(command)
        return ExecExitResult(exit_code=0)


async def test_a_remote_backend_refuses_it(rig: _Rig) -> None:
    remote = _RemoteOps()
    tool = create_bash_tool(str(rig.work), {"operations": remote, "background_host": lambda: True})
    assert "run_in_background" not in tool.parameters["properties"]
    result = await _call(tool, {"command": "echo ran", "run_in_background": True})
    assert result.is_error and _text(result) == _bg().REFUSED_HERE
    assert remote.calls == [] and rig.service.snapshots() == []


@POSIX
async def test_a_trailing_ampersand_is_refused(rig: _Rig) -> None:
    result = await rig.call({"command": "sleep 3 &", "run_in_background": True})
    assert result.is_error and _text(result) == _bg().TRAILING_AMPERSAND
    assert rig.service.snapshots() == []


@pytest.mark.parametrize(
    ("command", "refused"),
    [
        ("sleep 3 &", True),
        ("sleep 3&", True),
        ("sleep 3 &  ", True),
        ("a && b", False),
        ("a || b &&", False),
        ("echo 'a &'", False),
        ('echo "a &"', False),
        ("echo a \\&", False),
        ("echo a \\\\&", True),
        ("a & # trailing comment", True),
        ("a # not this &", False),
        ("(a &)", False),
        ("a |&", False),
        ("echo 'unclosed &", False),
        ("echo $(sleep 1 &)", False),
        ("`true` &", True),
        ("echo a#b &", True),
        ('echo "a \\" &', False),
        ('echo "a \\\\" &', True),
        # Codex 4: a line continuation after the '&', and quotes nested in a substitution.
        ("sleep 3 &\\\n", True),
        ("sleep 3 & \\\n  ", True),
        ('echo "$(printf \'"\')" &', True),
        ("x=$(echo ')') &", True),
        ("echo $(( 1 + 2 )) &", True),
        ("echo `echo '&'`", False),
        ('echo "$(echo "&")"', False),
        # verify M8, M9: a backslash in single quotes is literal; a comment after ';'.
        ("echo 'a\\' &", True),
        ("a;# x &", False),
        # The shell drops a backslash the input ends on.
        ("a &\\", True),
        # A group inside a substitution keeps the substitution open.
        ("echo $( (x) ; y &", False),
        # A redirection's '&' is not a background; an escaped last character is a word.
        ("echo x >&", False),
        ("sleep 1 & \\x", False),
        # verify F14 (N11): a quote or a substitution that opens after the '&' begins a word.
        ('sleep 5 & "$HOME/bin/client"', False),
        ("server & $(make client)", False),
        ("a & 'b'", False),
        # verify F16 (N10): a comment inside $( ) hides a quote until its newline.
        ("echo $(a # '\n) &", True),
    ],
)
def test_what_counts_as_a_trailing_ampersand(command: str, refused: bool) -> None:
    assert _bg().ends_in_ampersand(command) is refused


# ── control ────────────────────────────────────────────────────────


@POSIX
async def test_job_stop_stops_the_command_and_its_group(rig: _Rig) -> None:
    job_id = await rig.start("echo $$ > root; sleep 30 & echo $! > member; wait")
    root, member = await _read_pid(rig.work / "root"), await _read_pid(rig.work / "member")
    try:
        result = await create_job_stop_tool(rig.service).execute({"id": job_id})
        assert _text(result) == f"{job_id}: stopped"
        snap = rig.service.snapshot(job_id)
        assert snap.state == "stopped" and snap.brief == "stopped by you (job_stop)"
        assert not _alive(root) and not _alive(member)
        (notice,) = rig.notices(job_id)
        assert "It was stopped by you (job_stop)." in notice.body
    finally:
        _kill(root, member)


@POSIX
@pytest.mark.parametrize(("reason", "said"), [("owner_end", "when its session ended"), ("reload", "by /reload")])
async def test_owner_end_and_reload_stop_it(rig: _Rig, reason: str, said: str) -> None:
    job_id = await rig.start("echo $$ > root; sleep 30")
    root = await _read_pid(rig.work / "root")
    try:
        await rig.service.owner_ended(rig.service.owner, reason)
        snap = rig.service.snapshot(job_id)
        assert snap.state == "stopped" and said in snap.brief
        assert not _alive(root)
    finally:
        _kill(root)


@POSIX
async def test_a_released_helper_survives_a_normal_exit_top_level(rig: _Rig) -> None:
    """#232 parity: the command's own helper outlives its normal exit, as a top-level foreground call's does."""

    job_id = await rig.start("sleep 30 > /dev/null 2>&1 & echo $! > helper; echo started")
    helper = await _read_pid(rig.work / "helper")
    try:
        snap = await rig.ended(job_id)
        assert snap.state == "done"
        assert _alive(helper), "released, not stopped"
        cleanup = fold(await rig.session.get_entries())[job_id].cleanup
        assert cleanup is not None and cleanup["helpers"] == "none"
        assert cleanup.get("reason") == "released (#232)"
    finally:
        _kill(helper)


@POSIX
async def test_the_fifth_is_refused_by_admission(rig: _Rig) -> None:
    ids = [await rig.start("sleep 30") for _ in range(4)]
    result = await rig.call({"command": "sleep 30", "run_in_background": True})
    assert result.is_error
    assert "4 background commands already run" in _text(result)
    assert "backgroundJobs.limits.bash" in _text(result)
    assert len(rig.service.snapshots()) == 4
    # The refused call left no file behind.
    assert sorted(p.name for p in _bg().output_dir().iterdir()) == sorted(
        Path(s.client_start["output"]).name for s in rig.service.snapshots()
    )
    await rig.service.owner_ended(rig.service.owner, "owner_end")
    assert all(rig.service.snapshot(i).state == "stopped" for i in ids)


# ── -p waits (owner, 2026-11-07) ───────────────────────────────────


def _scripted(first: AssistantMessage, seen: list[list[str]]) -> Any:
    """The model: ``first``, then "noted"; ``seen`` gets each context's texts (``RecordingModel``'s reading)."""

    async def stream(model: Any, context: Any, options: Any = None) -> Any:
        seen.append(
            [
                block.text
                for message in context.messages
                for block in (getattr(message, "content", []) or [])
                if isinstance(block, TextContent)
            ]
        )
        reply = first if len(seen) == 1 else AssistantMessage(content=[TextContent(text="noted")], stop_reason="end_turn")
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=reply)

    return stream


@POSIX
@pytest.mark.usefixtures("jobs_active")
async def test_print_waits_for_a_background_command(tmp_path: Path) -> None:
    rig = await _make_rig(tmp_path)
    seen: list[list[str]] = []
    call = AssistantMessage(
        content=[
            ToolCallContent(
                tool_call_id="call-1",
                tool_name="bash",
                input={"command": "sleep 1; echo printed-late", "run_in_background": True},
            )
        ],
        stop_reason="tool_use",
    )
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_scripted(call, seen),
            session=rig.session,
            tools=[rig.tool],
            notice_inbox=True,
        )
    )
    await rig.service.bind(harness)

    async def factory(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())
    began = time.monotonic()
    code = await asyncio.wait_for(
        run_print_mode(runtime, mode="text", messages=[], initial_message="go", jobs=rig.service), 20
    )
    assert code == 0
    assert time.monotonic() - began >= 1.0, "-p waited for the command"
    (snap,) = rig.service.snapshots()
    assert snap.state == "done"
    assert len(seen) == 3, "the call, its result, then the notice turn"
    assert not any("printed-late" in t for t in seen[1]), "not the call's own result"
    assert any("printed-late" in t and "background-job-result" in t for t in seen[2])
    await rig.service.shutdown()


# ── restart (Codex #12) ────────────────────────────────────────────


def _start_fact(job_id: str, output: str) -> dict[str, Any]:
    return make_fact(
        "start",
        job_id,
        kind="bash",
        kind_version=1,
        owner="s",
        mode="background",
        notify=True,
        title="sleep 30",
        created=1.0,
        settings_key="bash",
        client={"v": 1, "output": output, "command_head": "sleep 30", "cwd": "/w", "timeout": None},
    )


def _process_fact(job_id: str, pid: int, started: float | None) -> dict[str, Any]:
    return make_fact(
        "process", job_id, seq=1, evidence={"pid": pid, "started": started, "host": evidence.this_host()}
    )


async def _restart(tmp_path: Path, *records: tuple[str, dict[str, Any]]) -> tuple[Any, AgentHarness]:
    session = await make_session(tmp_path)
    for custom_type, data in records:
        if custom_type == JOB_FACT_TYPE:
            await append_fact(session, data)
        else:
            from aelix_coding_agent.jobs.records import append_record

            await append_record(session, custom_type, data)
    jobs = fast_service()
    with contextlib.suppress(ImportError):  # absent on ``71f837eb``: the kind's jobs read adapter_missing
        _bg().register(jobs)
    harness = make_harness(await reopen(tmp_path))
    await jobs.bind(harness)
    return jobs, harness


@pytest.mark.usefixtures("jobs_active")
async def test_a_restart_before_the_exit_record_names_the_output_file(tmp_path: Path) -> None:
    output = tmp_path / "kept.log"
    output.write_bytes("가나다 from the last run\n".encode())
    gone = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    started = process_started(gone.pid)
    gone.wait()
    jobs, harness = await _restart(
        tmp_path,
        (JOB_FACT_TYPE, _start_fact("j", str(output))),
        (JOB_FACT_TYPE, _process_fact("j", gone.pid, started)),
    )
    snap = jobs.snapshot("j")
    assert snap is not None and snap.state == "lost"
    (notice,) = harness.undelivered_notices()
    assert f"Its output, as far as it got, is in {output}." in notice.body
    # The restored snapshot reads the recorded path, not one derived again.
    page = jobs.output("j", offset=0, limit=64)
    assert page.text == "가나다 from the last run\n"  # type: ignore[union-attr]
    await jobs.shutdown()


@POSIX
async def test_the_exit_record_tells_stopped_from_killed(rig: _Rig, tmp_path: Path) -> None:
    stopped = await rig.start("sleep 30")
    await create_job_stop_tool(rig.service).execute({"id": stopped})
    killed = await rig.start("echo $$ > root; sleep 30")
    root = await _read_pid(rig.work / "root")
    os.kill(root, signal.SIGKILL)
    await rig.ended(killed)
    records = {
        e.data["job_id"]: e.data
        for e in await rig.session.get_entries()
        if getattr(e, "custom_type", None) == _bg().RECORD_TYPE
    }
    assert (records[stopped]["status"], records[stopped]["stop_reason"]) == ("stopped", "model")
    assert (records[killed]["status"], records[killed]["stop_reason"], records[killed]["exit_code"]) == (
        "failed",
        None,
        -9,
    )
    (notice,) = rig.notices(killed)
    assert "It was killed by signal 9, not by aelix." in notice.body
    # A restart that finds the exit record and no outcome reads how each ended.
    adapter = _bg().BashKindAdapter()
    for job_id, status in ((stopped, "stopped"), (killed, "failed")):
        recovered = await adapter.recover({}, [{"custom_type": _bg().RECORD_TYPE, **records[job_id]}])
        assert recovered is not None and recovered.status == status


@POSIX
@pytest.mark.usefixtures("jobs_active")
async def test_restart_reports_a_live_command_unresolved_and_never_kills_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = subprocess.Popen(["/bin/sh", "-c", "sleep 30"], start_new_session=True)  # noqa: S603
    try:
        signalled: list[Any] = []
        real_kill, real_killpg = os.kill, os.killpg

        def no_kill(pid: int, sig: int) -> None:
            if sig != 0:
                signalled.append((pid, sig))
                raise AssertionError("a restart signalled")
            real_kill(pid, sig)

        def no_killpg(pid: int, sig: int) -> None:
            if sig != 0:
                signalled.append((pid, sig))
                raise AssertionError("a restart signalled")
            real_killpg(pid, sig)

        monkeypatch.setattr(os, "kill", no_kill)
        monkeypatch.setattr(os, "killpg", no_killpg)
        jobs, harness = await _restart(
            tmp_path,
            (JOB_FACT_TYPE, _start_fact("j", str(tmp_path / "out.log"))),
            (JOB_FACT_TYPE, _process_fact("j", live.pid, process_started(live.pid))),
        )
        snap = jobs.snapshot("j")
        assert snap is not None and snap.state == "unresolved"
        (notice,) = harness.undelivered_notices()
        assert notice.notice_id == "j:unresolved"
        assert f"Its output, as far as it got, is in {tmp_path / 'out.log'}." in notice.body
        await jobs.tick()
        assert live.poll() is None and signalled == []
        await jobs.shutdown()
    finally:
        monkeypatch.undo()
        live.kill()
        live.wait()


# ── the launch ledger (S2c, ADR-0296) ──────────────────────────────


@POSIX
async def test_in_an_armed_agent_its_spawn_is_in_the_ledger(
    rig: _Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.tools import launch_ledger as ll

    path = tmp_path / "run.launch-ledger"
    assert ll.create(str(path), run="sub-test")
    monkeypatch.setattr(ll, "_set_subreaper", lambda: None)
    assert ll.arm_from_environment({ll.LEDGER_ENV: str(path)}) is not None
    try:
        job_id = await rig.start("echo $$ > root; sleep 30")
        root = await _read_pid(rig.work / "root")
        lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        launches = [r for r in lines if r.get("t") == "launch"]
        assert [r["pid"] for r in launches] == [root] and launches[0]["pgid"] == root
        assert any(r.get("t") == "intent" and r["n"] == launches[0]["n"] for r in lines)
        await create_job_stop_tool(rig.service).execute({"id": job_id})
    finally:
        ll.disarm()


# ── the time limit (owner OQ-B, 2026-11-10) ─────────────────────────


@POSIX
@pytest.mark.usefixtures("jobs_active")
async def test_an_unattended_background_command_gets_the_bash_default_and_ends_stopped(tmp_path: Path) -> None:
    rig = await _make_rig(tmp_path, unattended=True, options={"default_timeout": 1.0})
    try:
        job_id = await rig.start("sleep 30")
        assert rig.service.snapshot(job_id).client_start["timeout"] == 1.0
        snap = await rig.ended(job_id)
        assert snap.state == "stopped"
        assert snap.brief == "stopped after reaching its background time limit"
    finally:
        await rig.service.shutdown()


@POSIX
@pytest.mark.usefixtures("jobs_active")
@pytest.mark.parametrize(("asked", "applied"), [(1.0, 1.0), (100.0, 1.5)])
async def test_an_explicit_background_timeout_is_honoured_up_to_the_max(
    tmp_path: Path, asked: float, applied: float
) -> None:
    """In the TUI (no default there) an explicit ``timeout`` bypasses the foreground's 600 s default and is enforced."""

    rig = await _make_rig(tmp_path, unattended=False, options={"max_timeout": 1.5})
    try:
        job_id = await rig.start("sleep 30", timeout=asked)
        assert rig.service.snapshot(job_id).client_start["timeout"] == applied
        snap = await rig.ended(job_id)
        assert snap.state == "stopped" and "time limit" in snap.brief
    finally:
        await rig.service.shutdown()


@POSIX
@pytest.mark.usefixtures("jobs_active")
async def test_the_tui_applies_no_default(tmp_path: Path) -> None:
    rig = await _make_rig(tmp_path, unattended=False, options={"default_timeout": 0.5})
    try:
        job_id = await rig.start("sleep 30")
        assert rig.service.snapshot(job_id).client_start["timeout"] is None
        await asyncio.sleep(1.5)
        assert rig.service.snapshot(job_id).state == "running"
        assert "unless you pass a timeout" in rig.tool.parameters["properties"]["run_in_background"]["description"]
    finally:
        await rig.service.shutdown()
    assert _bg().resolve_limit(None, default_timeout=600, max_timeout=3600, unattended=False) == (None, False)
    assert _bg().resolve_limit(None, default_timeout=600, max_timeout=3600, unattended=True) == (600, False)
    assert _bg().resolve_limit(None, default_timeout=0, max_timeout=3600, unattended=True) == (None, False)
    assert _bg().resolve_limit(7200, default_timeout=600, max_timeout=0, unattended=True) == (7200, False)


@POSIX
@pytest.mark.usefixtures("jobs_active")
async def test_the_time_limit_notice_names_its_reason(tmp_path: Path) -> None:
    rig = await _make_rig(tmp_path, unattended=True, options={"default_timeout": 1.0})
    try:
        job_id = await rig.start("echo before; sleep 30")
        await rig.ended(job_id)
        (notice,) = rig.notices(job_id)
        assert "It was stopped after reaching its background time limit (1s)." in notice.body
        assert "before" in notice.body
        records = fold(await rig.session.get_entries())[job_id]
        assert records.outcome is not None and records.outcome["status"] == "stopped"
    finally:
        await rig.service.shutdown()


# ── Linux: pdeathsig on the shell only (owner OQ-C) ─────────────────

_STANDIN_AELIX = """
import asyncio, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd())
scratch = Path(sys.argv[1])
tempfile.tempdir = str(scratch / "tmp")
from aelix_coding_agent.jobs import activation
activation.ACTIVE = True
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import bash_background as bg
from aelix_coding_agent.tools.bash import create_bash_tool
from tests.jobs.conftest import fast_service, make_harness, make_session

async def main():
    jobs = fast_service()
    bg.register(jobs)
    activation.install_service(jobs)
    await jobs.bind(make_harness(await make_session(scratch)))
    tool = create_bash_tool(str(scratch / "work"), {"background_host": lambda: True})
    result = await tool.execute(
        {"command": "echo $$ > shell; sleep 60 & echo $! > child; wait", "run_in_background": True},
        ToolExecutionContext(tool_call_id="t"),
    )
    print("started" if not result.is_error else "refused", flush=True)
    await asyncio.sleep(60)

asyncio.run(main())
"""


@LINUX
async def test_its_root_gets_pdeathsig_with_the_original_parent_recheck(tmp_path: Path) -> None:
    """Through the real tool, service and runner (verify F1): a SIGKILLed aelix takes its shell, not the shell's child."""

    import functools

    from aelix_ai.utils._parent_death import pdeathsig

    hook = _bg()._spawn_kwargs(suspended=False)["preexec_fn"]
    assert isinstance(hook, functools.partial) and hook.func is pdeathsig
    assert hook.args[0] == os.getpid(), "the recheck compares with this aelix, read before the fork"

    for folder in ("tmp", "work"):
        (tmp_path / folder).mkdir(exist_ok=True)
    script = tmp_path / "standin_aelix.py"
    script.write_text(_STANDIN_AELIX)
    standin = subprocess.Popen(  # noqa: S603
        [sys.executable, str(script), str(tmp_path)],
        stdout=subprocess.PIPE,
        start_new_session=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert standin.stdout is not None
    assert standin.stdout.readline().strip() == b"started"
    shell = await _read_pid(tmp_path / "work" / "shell")
    child = await _read_pid(tmp_path / "work" / "child")
    try:
        assert _alive(shell) and _alive(child)
        standin.kill()  # SIGKILL: aelix's own cleanup never runs
        standin.wait()
        await wait_for(lambda: not _alive(shell), 5.0)
        # The command's own child is not the shell: it outlives aelix (ADR-0297's limit).
        assert _alive(child)
    finally:
        _kill(shell, child)


# ── helpers at a normal exit, win32's Job (r2 §5, #10) ───────────────


class _Tree:
    def __init__(self, *, job: bool, preserves: bool) -> None:
        self.job = 1 if job else None
        self.preserves = preserves
        self.calls: list[str] = []

    def preserve_descendants(self) -> bool:
        self.calls.append("preserve")
        return self.preserves

    def close(self) -> None:
        self.calls.append("close")


def _runner_with(tree: _Tree, stopped_by: str | None = None, *, aborted: bool = False) -> Any:
    runner = _bg().BashJobRunner.__new__(_bg().BashJobRunner)
    runner._tree = tree
    runner._stopped_by = stopped_by
    runner._aborted = aborted
    runner._record = None
    return runner


async def test_a_normal_exit_releases_through_preserve_then_close() -> None:
    tree = _Tree(job=True, preserves=True)
    answer = await _runner_with(tree).helper_cleanup({"confirmed": True, "policy": "release"})
    assert (answer.state, answer.reason) == ("none", "released (#232)")
    assert tree.calls == ["preserve", "close"]


async def test_a_failed_preserve_is_held_for_aelixs_lifetime_and_reads_unverified() -> None:
    tree = _Tree(job=True, preserves=False)
    answer = await _runner_with(tree).helper_cleanup({"confirmed": True, "policy": "release"})
    assert answer.state == "unverified"
    assert answer.reason == "Windows could not release its helpers; they end when aelix exits"
    assert tree.calls == ["preserve"], "never closed: closing a kill-on-close Job would end them now"
    assert tree in _bg()._HELD_FOR_LIFE
    _bg()._HELD_FOR_LIFE.remove(tree)


@pytest.mark.parametrize(
    ("stopped_by", "policy"),
    # The service latched a stop it asked for; a time limit is the runner's own, so the
    # service still reads the job's normal-exit policy, "release".
    [("model", "stop"), ("time_limit", "release")],
)
async def test_a_stopped_command_closes_its_tree_without_preserving(stopped_by: str, policy: str) -> None:
    tree = _Tree(job=True, preserves=True)
    answer = await _runner_with(tree, stopped_by).helper_cleanup({"confirmed": True, "policy": policy})
    assert answer.state == "none" and tree.calls == ["close"]
    tree = _Tree(job=True, preserves=True)
    unconfirmed = await _runner_with(tree, stopped_by).helper_cleanup({"confirmed": False, "policy": policy})
    assert unconfirmed.state == "unverified" and tree.calls == []


# ── #760 CI: what a settled job keeps, and every win32 handle a job opens ──


@POSIX
async def test_a_settled_job_keeps_no_reader_sink_or_tree(rig: _Rig) -> None:
    """#760 CI: the service keeps a settled job for the process's life, so the job lets go of every
    object that holds a lock or a thread — on win32 CPython 3.12 and older each lock is a kernel
    semaphore (7 per job measured on windows-latest), on 3.13 and later the reader thread kept its
    handle (1 per job).

    Fails on ``66e9eca5``: the runner keeps its reader (the thread, its stream, its pin lock), its
    sink and its tree, and the record keeps the tree.
    """

    import gc
    import weakref

    job_id = await rig.start("sleep 0.5; echo short")
    held = rig.service._jobs[job_id]
    runner = held.runner
    await wait_for(lambda: runner._reader is not None and runner._tree is not None, 5.0)
    refs = {
        "reader": weakref.ref(runner._reader),
        "stream": weakref.ref(runner._reader._stream),
        "sink": weakref.ref(runner._sink),
        "tree": weakref.ref(runner._tree),
    }
    snap = await rig.ended(job_id)
    assert snap.state == "done" and snap.payload["bytes"] == 6 and "short" in snap.payload["tail"]

    def gone() -> bool:
        gc.collect()
        return all(ref() is None for ref in refs.values())

    await wait_for(gone, 5.0)
    assert (runner._reader, runner._sink, runner._tree, held.processes[0].tree) == (None, None, None, None)


class _LedgerKernel:
    """An injected win32 kernel (the ``_Win32Api`` family) that counts every handle it hands out.

    ``create_job``, ``duplicate`` and ``open_member`` open one; ``close_handle``
    and ``close_process`` close one. The processes are this host's own: the
    Job's members are the assigned root and what lives in its process group, and
    a terminate kills them for real.
    """

    def __init__(self) -> None:
        self.next = 1000
        self.open: dict[int, str] = {}
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.misuse: list[str] = []
        self.members: dict[int, set[int]] = {}
        self.pid_of: dict[int, int] = {}

    def _new(self, what: str, pid: int | None = None) -> int:
        self.next += 1
        self.open[self.next] = what
        self.opened.append(what)
        if pid is not None:
            self.pid_of[self.next] = pid
        return self.next

    def _close(self, handle: int) -> None:
        what = self.open.pop(handle, None)
        if what is None:
            self.misuse.append(f"close of {handle}")
        else:
            self.closed.append(what)

    @staticmethod
    def _alive(pid: int) -> bool:
        return _alive(pid)

    def create_job(self, *, kill_on_close: bool) -> int:
        job = self._new("job")
        self.members[job] = set()
        return job

    def assign(self, job: int, pid: int, handle: int | None) -> None:
        self.members[job].add(pid)

    def terminate_job(self, job: int) -> None:
        _kill(*self.job_member_pids(job))

    def close_handle(self, job: int) -> None:
        self._close(job)

    def ctrl_break(self, pid: int) -> None:
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGTERM)

    def taskkill(self, pid: int) -> None:
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGKILL)

    def job_member_pids(self, job: int) -> list[int]:
        from aelix_ai.utils._process_walk import group_members

        roots = self.members.get(job, set())
        found = set(roots) | {p for root in roots for p in (group_members(root) or [])}
        self.members[job] = roots | found
        return sorted(p for p in found if self._alive(p))

    def active_count(self, job: int) -> int:
        return len(self.job_member_pids(job))

    def open_member(self, pid: int) -> int:
        return self._new("member", pid)

    def in_job(self, handle: int, job: int) -> bool:
        return self.pid_of.get(handle) in self.members.get(job, set())

    def terminate_process(self, handle: int) -> None:
        _kill(self.pid_of[handle])

    def wait_handle(self, handle: int, timeout_ms: int) -> bool:
        end = time.monotonic() + timeout_ms / 1000
        while self._alive(self.pid_of[handle]) and time.monotonic() < end:
            time.sleep(0.01)
        return not self._alive(self.pid_of[handle])

    def close_process(self, handle: int) -> None:
        self._close(handle)

    def limit_flags(self, job: int) -> int:
        return 0x2000

    def set_limit_flags(self, job: int, flags: int) -> None:
        pass

    def can_resume(self) -> bool:
        return True

    def resume(self, pid: int, handle: int | None) -> None:
        pass

    def duplicate(self, handle: int) -> int:
        return self._new("suspended launch's own copy", self.pid_of.get(handle))


class _LedgerHandle:
    """``Popen._handle`` as the ledger counts it: opened at the spawn, closed by ``Close``."""

    def __init__(self, ledger: _LedgerKernel, pid: int) -> None:
        self.value = ledger._new("Popen process handle", pid)
        self.ledger = ledger
        self.closed = False

    def __int__(self) -> int:
        return self.value

    def Close(self) -> None:  # noqa: N802 — subprocess.Handle's own name
        if not self.closed:
            self.closed = True
            self.ledger._close(self.value)


@POSIX
@pytest.mark.parametrize("ending", ["normal exit", "job_stop"])
async def test_every_win32_handle_a_job_opens_is_closed_when_it_settles(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    """#760 CI, the kernel side: the job's win32 arm on an injected kernel, across five jobs.

    Per job: the Popen process handle (spawn → reap), the suspended launch's own
    copy and the Job (attach → the tree's close), and on a stop the member handles
    its evidence opens (collect → confirm or close). Every one is closed once
    the job settles, and none is closed twice. A guard: green on ``66e9eca5``
    too, where fix round 1 had closed the Popen handle.
    """

    from aelix_ai.utils._process_tree import ProcessTree

    ledger = _LedgerKernel()
    real_attach = ProcessTree.attach
    real_popen = _bg()._popen_background

    def popen(*args: Any, **kwargs: Any) -> Any:
        proc = real_popen(*args, **kwargs)
        proc._handle = _LedgerHandle(ledger, proc.pid)  # type: ignore[attr-defined]
        return proc

    def attach(pid: int, **kwargs: Any) -> ProcessTree:
        return real_attach(
            pid,
            kill_on_close=kwargs["kill_on_close"],
            handle=kwargs["handle"],
            stop_latched=kwargs["stop_latched"],
            suspended=True,
            platform="win32",
            api=ledger,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(_bg(), "_popen_background", popen)
    monkeypatch.setattr(_bg().ProcessTree, "attach", staticmethod(attach))
    for _ in range(5):
        if ending == "normal exit":
            job_id = await rig.start("echo short")
        else:
            job_id = await rig.start("sleep 30 & echo $! > member; wait")
            await _read_pid(rig.work / "member")
            await create_job_stop_tool(rig.service).execute({"id": job_id})
            (rig.work / "member").unlink()
        snap = await rig.ended(job_id)
        assert snap.state == ("done" if ending == "normal exit" else "stopped")
        assert ledger.open == {}, f"left open after a job: {sorted(ledger.open.values())}"
    assert ledger.misuse == []
    assert ledger.opened.count("job") == ledger.closed.count("job") == 5
    assert ledger.opened.count("Popen process handle") == ledger.closed.count("Popen process handle") == 5
    assert ledger.opened.count("suspended launch's own copy") == 5
    if ending == "job_stop":
        assert ledger.opened.count("member") >= 5, "the stop's evidence opened member handles"


# ── windows-latest only (I): the real Job ─────────────────────────────


def _win_python(script: Path) -> str:
    """A command line that runs ``script`` with this interpreter under the shell bash resolves on win32."""

    from aelix_ai.utils._shell import POWERSHELL_NAMES, shell_basename
    from aelix_coding_agent.tools.bash import _resolve_shell
    from aelix_coding_agent.util.shell_env import get_shell_env

    shell = _resolve_shell(get_shell_env())
    line = f'"{sys.executable}" "{script}"'
    return f"& {line}" if shell_basename(shell.path) in POWERSHELL_NAMES else line


_WIN_HELPER = """
import subprocess, sys
helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(helper.pid))
print("started", flush=True)
"""


@WIN32
async def test_a_normal_exit_preserves_its_helpers(rig: _Rig, tmp_path: Path) -> None:
    script = tmp_path / "helper.py"
    script.write_text(_WIN_HELPER.replace("sys.argv[1]", repr(str(rig.work / "helper"))))
    job_id = await rig.start(_win_python(script))
    helper = await _read_pid(rig.work / "helper", 15.0)
    try:
        snap = await rig.ended(job_id, 30.0)
        assert snap.state == "done"
        cleanup = fold(await rig.session.get_entries())[job_id].cleanup
        assert cleanup is not None and (cleanup["helpers"], cleanup.get("reason")) == ("none", "released (#232)")
        await asyncio.sleep(1.0)
        assert _alive(helper), "preserve_descendants released it; closing the Job did not end it"
    finally:
        _kill(helper)


@WIN32
async def test_a_failed_preserve_holds_and_reads_unverified(rig: _Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_ai.utils._process_tree import ProcessTree

    monkeypatch.setattr(ProcessTree, "preserve_descendants", lambda self: False)
    script = tmp_path / "helper.py"
    script.write_text(_WIN_HELPER.replace("sys.argv[1]", repr(str(rig.work / "helper"))))
    job_id = await rig.start(_win_python(script))
    helper = await _read_pid(rig.work / "helper", 15.0)
    try:
        snap = await rig.ended(job_id, 30.0)
        assert snap.state == "done"
        cleanup = fold(await rig.session.get_entries())[job_id].cleanup
        assert cleanup is not None and cleanup["helpers"] == "unverified"
        assert _alive(helper), "held, not killed: the Job stays open until aelix exits"
        (notice,) = rig.notices(job_id)
        assert "they end when aelix exits" in notice.body
    finally:
        _kill(helper)
        held = list(_bg()._HELD_FOR_LIFE)
        _bg()._HELD_FOR_LIFE.clear()
        for tree in held:
            tree.close()


_WIN_FAKE_AELIX = """
import os, sys, time
from aelix_ai.utils._process_tree import ProcessTree, _retained_handle
from aelix_coding_agent.tools.bash import _resolve_shell
from aelix_coding_agent.tools.bash_background import _popen_background
from aelix_coding_agent.util.shell_env import get_shell_env
suspended = ProcessTree.suspend_flags() != 0
command = sys.argv[1]
proc = _popen_background(_resolve_shell(get_shell_env()), command, sys.argv[2], get_shell_env(), suspended=suspended)
tree = ProcessTree.attach(proc.pid, handle=_retained_handle(proc), kill_on_close=True, suspended=suspended)
print(proc.pid, flush=True)
time.sleep(60)
"""


@WIN32
async def test_aelix_dying_kills_it_through_kill_on_close(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    script = tmp_path / "fake_aelix.py"
    script.write_text(_WIN_FAKE_AELIX)
    command = _win_python(sleeper) + f' "{tmp_path / "grandchild"}"'
    fake = subprocess.Popen([sys.executable, str(script), command, str(tmp_path)], stdout=subprocess.PIPE)  # noqa: S603
    assert fake.stdout is not None
    shell = int(fake.stdout.readline())
    grandchild = await _read_pid(tmp_path / "grandchild", 15.0)
    try:
        fake.kill()  # TerminateProcess: the Job handle closes with aelix
        fake.wait()
        await wait_for(lambda: not _alive(shell) and not _alive(grandchild), 10.0)
    finally:
        _kill(shell, grandchild)


# ── fix round 1 (#760): launch failures, starvation, ownership, restart, wiring ──


def _spy_spawns(monkeypatch: pytest.MonkeyPatch) -> list[subprocess.Popen[bytes]]:
    """Every background shell the runner spawns, through ``_popen_background`` itself."""

    spawned: list[subprocess.Popen[bytes]] = []
    real = _bg()._popen_background

    def spy(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc = real(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(_bg(), "_popen_background", spy)
    return spawned


def _fail_at(point: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_coding_agent.jobs import service as service_mod

    boom = RuntimeError("can't start new thread")
    if point == "reader-start":
        monkeypatch.setattr(_bg()._PipeReader, "start", lambda self: (_ for _ in ()).throw(boom))
    elif point == "reader-init":
        monkeypatch.setattr(_bg(), "_PipeReader", lambda *a, **k: (_ for _ in ()).throw(boom))
    elif point == "waiter":
        monkeypatch.setattr(_bg().BashJobRunner, "_start_waiter", lambda *a: (_ for _ in ()).throw(boom))
    elif point == "register":
        monkeypatch.setattr(service_mod._Context, "register_process", lambda *a, **k: (_ for _ in ()).throw(boom))
    elif point == "attach":
        monkeypatch.setattr(_bg().ProcessTree, "attach", lambda *a, **k: (_ for _ in ()).throw(boom))


@POSIX
@pytest.mark.parametrize("point", ["reader-start", "reader-init", "waiter", "register", "attach"])
async def test_a_launch_that_fails_after_its_spawn_leaves_no_command_and_no_slot(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    """Codex 1: the command is stopped and reaped, its file closed and its slot back before "not started" is said."""

    spawned = _spy_spawns(monkeypatch)
    _fail_at(point, monkeypatch)
    before = _open_fds()
    result = await rig.call({"command": "sleep 30", "run_in_background": True}, timeout=15.0)
    assert _open_fds() <= before, "its pipe and its output file are closed"
    assert result.is_error and "was not started" in _text(result) and "RuntimeError" in _text(result)
    (proc,) = spawned
    assert proc.poll() is not None or not _alive(proc.pid), "no command left running"
    (snap,) = rig.service.snapshots()
    assert snap.state == "failed", snap
    assert rig.service.admission.count("bash") == 0, "its slot went back"
    assert not Path(snap.client_start["output"]).exists()
    assert rig.harness.undelivered_notices() == []
    assert rig.service.unsettled() is False


def _open_fds() -> set[int]:
    return {int(name) for name in os.listdir("/dev/fd") if name.isdigit()}


@POSIX
async def test_a_nul_in_the_command_is_refused_before_anything_is_allocated(rig: _Rig) -> None:
    before = _open_fds()
    for _ in range(3):
        result = await rig.call({"command": "printf x\x00y", "run_in_background": True})
        assert result.is_error and _text(result) == _bg().NUL_REFUSED
    assert rig.service.snapshots() == [] and not any(_bg().output_dir().iterdir())
    assert _open_fds() <= before


@POSIX
async def test_every_launch_failure_closes_the_output_file(rig: _Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex 2: a ``Popen`` that raises something other than ``OSError`` (here its NUL ``ValueError``) leaks nothing."""

    def refuse(*a: Any, **k: Any) -> Any:
        raise ValueError("embedded null byte")

    monkeypatch.setattr(_bg(), "_popen_background", refuse)
    before = _open_fds()
    for _ in range(3):
        result = await rig.call({"command": "echo never", "run_in_background": True})
        assert result.is_error and "ValueError: embedded null byte" in _text(result)
    assert _open_fds() <= before, "no output descriptor left open"
    assert rig.service.admission.count("bash") == 0


@POSIX
async def test_a_starved_reader_says_its_output_may_be_incomplete_and_the_file_still_gets_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Codex 3: past the drain's cap with no proof, the notice and the result say so, and nothing is dropped."""

    import threading

    request.getfixturevalue("jobs_active")
    rig = await _make_rig(tmp_path, exit_grace=45)
    release = threading.Event()
    real = _bg()._PipeReader.run

    def held(self: Any) -> None:
        release.wait(10)
        real(self)

    monkeypatch.setattr(_bg()._PipeReader, "run", held)
    try:
        job_id = await rig.start("printf OWN-OUTPUT-BEFORE-EXIT")
        snap = await rig.ended(job_id)
        assert snap.state == "done" and snap.payload["output_unconfirmed"] is True
        (notice,) = rig.notices(job_id)
        assert "(no output)" not in notice.body
        assert "Its output may be incomplete" in notice.body
        released_at = time.monotonic()
        release.set()
        path = Path(snap.client_start["output"])
        await wait_for(lambda: path.read_bytes() == b"OWN-OUTPUT-BEFORE-EXIT", 5.0)
        # verify F15 (N39): the late capture ends at the reader's EOF, not at its 30 s bound
        # (measured on the wall clock: a capture that spun on would hold the loop past it).
        runner = rig.service._jobs[job_id].runner
        await wait_for(lambda: runner._late is not None and runner._late.done(), 3.0)
        assert time.monotonic() - released_at < 3.0
        page = rig.service.output(job_id, offset=0, limit=1024)
        assert page.text.startswith("OWN-OUTPUT-BEFORE-EXIT\n[Its output may be incomplete")
        record = [
            e.data for e in await rig.session.get_entries() if getattr(e, "custom_type", None) == _bg().RECORD_TYPE
        ]
        assert record[0]["output_unconfirmed"] is True
    finally:
        release.set()
        await rig.service.shutdown()


@POSIX
async def test_a_reader_late_by_less_than_the_cap_is_still_a_complete_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Held 1 s, inside the drain's 2 s: the drain waits for its proof instead of ending at the idle mark."""

    import threading

    request.getfixturevalue("jobs_active")
    rig = await _make_rig(tmp_path, exit_grace=45)
    release = threading.Event()
    real = _bg()._PipeReader.run
    monkeypatch.setattr(_bg()._PipeReader, "run", lambda self: (release.wait(1.0), real(self))[1])
    try:
        job_id = await rig.start("printf on-time")
        snap = await rig.ended(job_id)
        assert snap.payload["output_unconfirmed"] is False and snap.payload["bytes"] == 7
        (notice,) = rig.notices(job_id)
        assert "on-time" in notice.body and "may be incomplete" not in notice.body
    finally:
        await rig.service.shutdown()


@POSIX
async def test_a_launch_failing_after_the_command_forked_stops_its_whole_group(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 1 with a member already in the group: the job's evidence legs take it, not only the root."""

    work = rig.work

    def late_failure(self: Any) -> None:
        deadline = time.monotonic() + 5.0
        while not (work / "member").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(_bg()._PipeReader, "start", late_failure)
    result = await rig.call({"command": "sleep 30 & echo $! > member; wait", "run_in_background": True}, timeout=15.0)
    assert result.is_error and "was not started" in _text(result)
    member = int((work / "member").read_text())
    await wait_for(lambda: not _alive(member), 5.0)
    assert rig.service.admission.count("bash") == 0


@POSIX
def test_another_users_output_directory_is_refused(_own_tempdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _bg().output_dir()
    monkeypatch.setattr(os, "getuid", lambda: folder.stat().st_uid + 1)
    with pytest.raises(OSError, match="belongs to another user"):
        _bg().output_dir()


def test_after_a_failed_write_the_file_stays_a_prefix_with_no_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"")
    calls = {"n": 0}
    real = _bg()._write_fd

    def flaky(fd: int, data: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(28, "No space left on device")
        return real(fd, data)

    monkeypatch.setattr(_bg(), "_write_fd", flaky)
    sink = _bg()._Sink(str(path))
    for chunk in (b"one ", b"two ", b"three"):
        sink.write(chunk)
    sink.close()
    assert path.read_bytes() == b"one " and sink.total == 13 and sink.write_error is not None


def test_with_the_gate_off_building_bash_imports_nothing_of_the_background_module(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from aelix_coding_agent.tools.bash import create_bash_tool\n"
        f"create_bash_tool({str(tmp_path)!r}, {{'background_host': lambda: True}})\n"
        "print('aelix_coding_agent.tools.bash_background' in sys.modules)\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, check=True, timeout=60
    ).stdout.decode("utf-8", errors="replace")
    assert out.strip() == "False"


@POSIX
async def test_a_complete_output_is_never_called_incomplete(rig: _Rig) -> None:
    job_id = await rig.start("printf done-here")
    snap = await rig.ended(job_id)
    assert snap.payload["output_unconfirmed"] is False
    (notice,) = rig.notices(job_id)
    assert "may be incomplete" not in notice.body and "done-here" in notice.body
    job_id = await rig.start("true")
    await rig.ended(job_id)
    (notice,) = rig.notices(job_id)
    assert "(no output)" in notice.body


@POSIX
async def test_a_helper_holding_the_pipe_is_no_reason_to_call_the_output_incomplete(rig: _Rig) -> None:
    """The drain ends on the reader's proof (#232's idle rule), not on EOF, and that is a complete read."""

    job_id = await rig.start("sleep 30 & echo $! > helper; echo started")
    helper = await _read_pid(rig.work / "helper")
    try:
        snap = await rig.ended(job_id)
        assert snap.payload["output_unconfirmed"] is False
        (notice,) = rig.notices(job_id)
        assert "started" in notice.body and "may be incomplete" not in notice.body
    finally:
        _kill(helper)


@POSIX
async def test_backgrounded_work_the_scan_cannot_see_is_reported_released(rig: _Rig) -> None:
    """Codex 4's fallback: ``eval`` hides the '&' from any scan; the exit reports what it left running."""

    job_id = await rig.start("eval 'sleep 30 > /dev/null 2>&1 &'; echo $! > helper")
    helper = await _read_pid(rig.work / "helper")
    try:
        snap = await rig.ended(job_id)
        assert snap.state == "done" and snap.payload["left_running"] is True
        (notice,) = rig.notices(job_id)
        assert "left processes running in its process group" in notice.body and "(#232)" in notice.body
        assert _alive(helper), "released, as #232 releases a foreground command's"
    finally:
        _kill(helper)
    job_id = await rig.start("echo alone")
    await rig.ended(job_id)
    (notice,) = rig.notices(job_id)
    assert "left processes running" not in notice.body


@pytest.mark.usefixtures("jobs_active")
async def test_a_restart_after_the_exit_record_keeps_the_output_file_notice(tmp_path: Path) -> None:
    """Codex 5: the crash window between the exit record and the outcome."""

    output = tmp_path / "kept.log"
    output.write_bytes(b"preserved output\n")
    gone = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    started = process_started(gone.pid)
    gone.wait()
    start = _start_fact("j", str(output))
    start["client"]["timeout"] = 3.0
    jobs, harness = await _restart(
        tmp_path,
        (JOB_FACT_TYPE, start),
        (JOB_FACT_TYPE, _process_fact("j", gone.pid, started)),
        (_bg().RECORD_TYPE, {"v": 1, "phase": "exit", "job_id": "j", "status": "stopped", "exit_code": -15, "stop_reason": "time_limit", "bytes": 17}),
    )
    assert jobs.snapshot("j").state == "stopped"  # type: ignore[union-attr]
    (notice,) = harness.undelivered_notices()
    assert "It was stopped after reaching its background time limit (3s)." in notice.body
    assert f'Full output: {output} (17 bytes); job(action="result", id="j") reads it in pages.' in notice.body
    assert "$ sleep 30" in notice.body
    await jobs.shutdown()


@POSIX
async def test_a_failed_suspended_launch_hands_its_tree_to_the_runner_which_closes_it(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 6, portable: the tree a SuspendedLaunchError carries is the runner's, closed and never preserved."""

    from aelix_ai.utils._process_tree import ProcessTree, SuspendedLaunchError

    trees: list[ProcessTree] = []
    real_attach = ProcessTree.attach
    preserved: list[bool] = []
    real_job = ProcessTree.job

    def attach_then_fail(pid: int, **kwargs: Any) -> ProcessTree:
        tree = real_attach(pid)
        trees.append(tree)
        monkeypatch.setattr(tree, "preserve_descendants", lambda: preserved.append(True) or True)
        raise SuspendedLaunchError("not resumed: injected; ending it failed", tree=tree)

    # As on win32, the tree holds a Job: an aborted launch must still never be preserved.
    monkeypatch.setattr(
        ProcessTree, "job", property(lambda self: 1 if self in trees and not self.closed else real_job.fget(self))  # type: ignore[attr-defined]
    )
    monkeypatch.setattr(_bg().ProcessTree, "attach", staticmethod(attach_then_fail))
    spawned = _spy_spawns(monkeypatch)
    result = await rig.call({"command": "sleep 30", "run_in_background": True}, timeout=15.0)
    assert result.is_error and "not resumed: injected" in _text(result)
    (tree,) = trees
    (proc,) = spawned
    assert tree.closed and preserved == []
    assert proc.poll() is not None
    (snap,) = rig.service.snapshots()
    assert snap.state == "failed" and rig.service.admission.count("bash") == 0


class _FakeHandle:
    closed = False

    def Close(self) -> None:  # noqa: N802 — subprocess.Handle's own name
        self.closed = True


@POSIX
async def test_a_reaped_command_lets_go_of_its_popen_and_closes_its_process_handle(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex 7, portable: a win32 ``Popen`` keeps its process handle until closed; after the reap nothing holds it."""

    handles: list[_FakeHandle] = []
    real = _bg()._popen_background

    def with_handle(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        proc = real(*args, **kwargs)
        handles.append(_FakeHandle())
        proc._handle = handles[-1]  # type: ignore[attr-defined]
        return proc

    monkeypatch.setattr(_bg(), "_popen_background", with_handle)
    job_id = await rig.start("echo output")
    await rig.ended(job_id)
    assert handles[0].closed, "the original process handle is closed after the reap"
    held = rig.service._jobs[job_id]
    assert held.processes[0].proc.returncode == 0 and held.processes[0].proc._popen is None
    assert held.runner._proc is None


@POSIX
async def test_a_short_invalid_output_keeps_its_leading_bytes(rig: _Rig) -> None:
    """Codex 8: five bytes ``80 81 82 4f 4b``, never cut, read whole."""

    job_id = await rig.start("printf '\\200\\201\\202OK'")
    await rig.ended(job_id)
    (notice,) = rig.notices(job_id)
    assert "\n���OK\n" in notice.body + "\n"


@POSIX
async def test_the_background_shell_is_spawned_by_its_own_popen_with_its_group_recorded(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify F1 (M77, M27): through the real tool, service and runner, ``_spawn_shell`` hands the
    background spawn to ``_popen_background`` (pdeathsig, a suspended spawn), and the group is the evidence."""

    spawned = _spy_spawns(monkeypatch)
    attached: list[dict[str, Any]] = []
    real_attach = _bg().ProcessTree.attach

    def attach(pid: int, **kwargs: Any) -> Any:
        attached.append(kwargs)
        return real_attach(pid, **kwargs)

    monkeypatch.setattr(_bg().ProcessTree, "attach", staticmethod(attach))
    job_id = await rig.start("echo $$ > root; sleep 30")
    root = await _read_pid(rig.work / "root")
    assert os.getsid(root) == root, "a session of its own, no controlling terminal"
    (kwargs,) = attached
    assert kwargs["kill_on_close"] is True and kwargs["stop_latched"]() is False
    await create_job_stop_tool(rig.service).execute({"id": job_id})
    assert kwargs["stop_latched"]() is True, "the latch a suspended attach reads is the job's own"
    await rig.ended(job_id)
    assert [p.pid for p in spawned] == [root]
    facts = fold(await rig.session.get_entries())[job_id]
    (process,) = facts.process_list()
    assert process["evidence"]["pid"] == root and process["evidence"]["pgid"] == root


@pytest.mark.usefixtures("jobs_active")
async def test_restored_note_none_adds_no_text(tmp_path: Path) -> None:
    """verify F3 (M86): a kind answering ``None`` adds nothing at all, not the word."""

    class _Silent(_bg().BashKindAdapter):  # type: ignore[misc,name-defined]
        def restored_note(self, facts: Any) -> str | None:
            return None

    gone = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    started = process_started(gone.pid)
    gone.wait()
    session = await make_session(tmp_path)
    for fact in (_start_fact("j", "/abs/out.log"), _process_fact("j", gone.pid, started)):
        await append_fact(session, fact)
    jobs = fast_service()
    jobs.register_kind(_Silent(), lambda params: None)  # type: ignore[arg-type,return-value]
    harness = make_harness(await reopen(tmp_path))
    await jobs.bind(harness)
    (notice,) = harness.undelivered_notices()
    assert "None" not in notice.body and "/abs/out.log" not in notice.body
    await jobs.shutdown()


@POSIX
async def test_the_job_end_retires_its_group_in_an_armed_agents_ledger(
    rig: _Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify F5 (M34): the job's end calls ``after_call``, which retires the group it left empty."""

    from aelix_coding_agent.tools import launch_ledger as ll

    path = tmp_path / "run.launch-ledger"
    assert ll.create(str(path), run="sub-test")
    monkeypatch.setattr(ll, "_set_subreaper", lambda: None)
    writer = ll.arm_from_environment({ll.LEDGER_ENV: str(path)})
    assert writer is not None
    writer.stop_tick()  # only the job's own end may retire it here
    monkeypatch.setattr(ll.LaunchWriter, "_start_tick", lambda self: None)
    try:
        spawned = _spy_spawns(monkeypatch)
        job_id = await rig.start("echo done")
        await rig.ended(job_id)
        lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        (launch,) = [r for r in lines if r.get("t") == "launch"]
        assert launch["pid"] == spawned[0].pid, "spawned by the background popen in the armed path (M78)"
        assert {"t": "retired", "n": launch["n"]}.items() <= next(
            r for r in lines if r.get("t") == "retired"
        ).items()
    finally:
        ll.disarm()


@POSIX
async def test_a_launch_whose_record_cannot_be_made_stops_its_whole_group(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify F16 (N22): with no record to run the legs on, the tree's hard kill takes the group."""

    from aelix_coding_agent.jobs import service as service_mod

    work = rig.work

    def refuse(*a: Any, **k: Any) -> Any:
        deadline = time.monotonic() + 5.0
        while not (work / "member").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        raise RuntimeError("register refused")

    monkeypatch.setattr(service_mod._Context, "register_process", refuse)
    result = await rig.call({"command": "sleep 30 & echo $! > member; wait", "run_in_background": True}, timeout=15.0)
    assert result.is_error and "register refused" in _text(result)
    member = int((work / "member").read_text())
    await wait_for(lambda: not _alive(member), 5.0)


def test_the_incomplete_caveat_is_on_the_last_page_only(tmp_path: Path) -> None:
    """verify F16 (N49): the caveat follows the output it qualifies, once; earlier pages are the file's bytes."""

    from aelix_coding_agent.jobs.contract import JobSnapshot

    path = tmp_path / "out.log"
    path.write_bytes(b"0123456789" * 3)
    snap = JobSnapshot(
        job_id="j", kind="bash", label="command", title="t", state="done", owner="s", created=0.0,
        elapsed=1.0, last_activity_ago=None, quiet=False, result_waiting=False, notice_state="none",
        payload={"output_unconfirmed": True}, client_start={"output": str(path)},
    )
    adapter = _bg().BashKindAdapter()
    pages = [adapter.output(snap, offset=at, limit=10) for at in (0, 10, 20)]
    assert [p.text for p in pages[:2]] == ["0123456789", "0123456789"]  # type: ignore[union-attr]
    assert pages[2].text.startswith("0123456789\n[Its output may be incomplete")  # type: ignore[union-attr]


def test_a_cut_tail_drops_the_character_the_cut_split(tmp_path: Path) -> None:
    """verify F16 (N52): the sink's tail of a longer output starts on a whole character."""

    path = tmp_path / "out.log"
    path.write_bytes(b"")
    sink = _bg()._Sink(str(path))
    sink.write("가".encode() * 3000)  # 9000 bytes; the last 8192 start 2 bytes into a character
    text = _bg()._sink_tail(sink)
    sink.close()
    assert text == "가" * (8192 // 3)


_ZOMBIE_GROUP = """
import asyncio, ctypes, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd())
scratch = Path(sys.argv[1])
tempfile.tempdir = str(scratch / "tmp")
# An armed agent's shape on Linux: aelix is a child subreaper (ADR-0296), and reaps nothing it did not spawn.
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
from aelix_coding_agent.jobs import activation
activation.ACTIVE = True
from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import bash_background as bg
from aelix_coding_agent.tools.bash import create_bash_tool
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

async def main():
    jobs = fast_service(exit_grace=10)
    bg.register(jobs)
    activation.install_service(jobs)
    await jobs.bind(make_harness(await make_session(scratch)))
    tool = create_bash_tool(str(scratch / "work"), {"background_host": lambda: True})
    result = await tool.execute(
        {"command": "(sleep 0.1 >/dev/null 2>&1 &); sleep 0.6; echo done", "run_in_background": True},
        ToolExecutionContext(tool_call_id="t"),
    )
    job_id = "".join(b.text for b in result.content).split("job ")[1].split(";")[0]
    await wait_for(lambda: jobs.snapshot(job_id).state in ("done", "failed", "stopped"), 15)
    snap = jobs.snapshot(job_id)
    zombies = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if fields[0] == "Z" and int(fields[1]) == os.getpid():
                zombies.append(int(entry.name))
    print(json.dumps({"state": snap.state, "left_running": snap.payload.get("left_running"), "zombies": zombies}))
    await jobs.shutdown()

asyncio.run(main())
"""


@LINUX
async def test_a_zombie_left_in_the_group_is_not_reported_running(tmp_path: Path) -> None:
    """verify F13 (N42): under a subreaper an exited helper stays a zombie in the command's group; that is not "left running"."""

    for folder in ("tmp", "work"):
        (tmp_path / folder).mkdir(exist_ok=True)
    script = tmp_path / "zombie_group.py"
    script.write_text(_ZOMBIE_GROUP)
    out = subprocess.run(  # noqa: S603
        [sys.executable, str(script), str(tmp_path)],
        capture_output=True,
        timeout=60,
        cwd=Path(__file__).resolve().parents[2],
    )
    seen = json.loads(out.stdout.decode("utf-8", errors="replace").strip().splitlines()[-1])
    assert seen["zombies"], f"the shape needs an adopted zombie: {seen}"
    assert seen["state"] == "done" and seen["left_running"] is False, seen


# ── verify F7: the survivors on reachable lines ─────────────────────


def test_the_parameter_text_with_no_default_says_no_limit() -> None:
    text = _bg().parameter(True, 0)["description"]
    assert "unless you pass a timeout" in text and "stopped after" not in text  # M5


@pytest.mark.parametrize("asked", [0, -5, "soon", None])
def test_a_zero_negative_or_unreadable_timeout_is_no_timeout(asked: Any) -> None:
    # M12, M15: as the foreground resolver reads them, an omitted one.
    assert _bg().resolve_limit(asked, default_timeout=600, max_timeout=3600, unattended=True) == (600, False)
    assert _bg().resolve_limit(asked, default_timeout=600, max_timeout=3600, unattended=False) == (None, False)


def test_the_tail_is_the_last_eight_kib(tmp_path: Path) -> None:
    path = tmp_path / "out.log"
    path.write_bytes(b"")
    sink = _bg()._Sink(str(path))
    sink.write(b"a" * 10_000 + b"END")
    assert len(sink.tail()) == 8 * 1024 and sink.tail().endswith(b"END")  # M25
    sink.close()


async def test_a_stop_latched_before_the_spawn_starts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aelix_ai.utils._shell import ShellConfig

    spawned = _spy_spawns(monkeypatch)

    class _Ctx:
        stop_latched = True
        failed: list[str] = []

        def launch_failed(self, why: str) -> None:
            self.failed.append(why)

    out = tmp_path / "x.log"
    out.write_bytes(b"")
    launch = _bg().Launch("echo x", ShellConfig("/bin/sh"), str(tmp_path), {}, str(out), None, "echo x")
    ctx = _Ctx()
    outcome = await _bg().BashJobRunner(launch).run(ctx)
    assert outcome.status == "stopped" and ctx.failed == ["it was stopped before it started"]  # M29
    assert spawned == []


def test_the_first_stop_reason_stands() -> None:
    from aelix_ai.utils._shell import ShellConfig

    runner = _bg().BashJobRunner(_bg().Launch("x", ShellConfig("/bin/sh"), "/", {}, "/x", None, "x"))
    runner.request_stop("model")
    runner.request_stop("shutdown")
    assert runner._stopped_by == "model"  # M38


def test_the_rows_name_the_output_file() -> None:
    from aelix_coding_agent.jobs.contract import JobSnapshot

    snap = JobSnapshot(
        job_id="j", kind="bash", label="command", title="sleep 9", state="running", owner="s",
        created=0.0, elapsed=1.0, last_activity_ago=0.0, quiet=False, result_waiting=False,
        notice_state="none", progress={"bytes": 12}, client_start={"output": "/t/x.log"},
    )
    assert _bg().BashKindAdapter().render_rows(snap, 80) == ["command · sleep 9", "  12 bytes → /t/x.log"]  # M51


@POSIX
async def test_a_running_jobs_page_through_the_service_leaves_its_incomplete_tail(rig: _Rig) -> None:
    job_id = await rig.start("printf 'ok\\352\\260'; sleep 30")
    path = Path(rig.service.snapshot(job_id).client_start["output"])  # type: ignore[union-attr]
    await wait_for(lambda: path.stat().st_size == 4, 5.0)
    page = rig.service.output(job_id, offset=0, limit=64)
    assert (page.text, page.next_offset, page.more) == ("ok", 2, False)  # M52
    await create_job_stop_tool(rig.service).execute({"id": job_id})


@POSIX
async def test_the_answer_names_the_limit_and_a_clamped_one_says_so(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    request.getfixturevalue("jobs_active")
    rig = await _make_rig(tmp_path, unattended=False, options={"max_timeout": 5.0})
    try:
        text = _text(await rig.call({"command": "sleep 30", "run_in_background": True, "timeout": 2}))
        assert "It is stopped after 2s if it has not ended." in text and "cap" not in text  # M70
        text = _text(await rig.call({"command": "sleep 30", "run_in_background": True, "timeout": 100}))
        assert "It is stopped after 5s if it has not ended." in text
        assert "The requested timeout exceeded the 5s cap; raise AELIX_BASH_MAX_TIMEOUT to allow longer." in text  # F9
    finally:
        await rig.service.shutdown()


async def test_a_cwd_that_is_not_a_directory_is_refused(rig: _Rig, tmp_path: Path) -> None:
    tool = create_bash_tool(str(tmp_path / "missing"), {"background_host": lambda: True})
    result = await _call(tool, {"command": "echo x", "run_in_background": True})
    assert result.is_error and "is not a directory" in _text(result)  # M74
    assert rig.service.snapshots() == []


async def test_with_the_gate_on_and_no_service_it_is_refused(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    request.getfixturevalue("jobs_active")
    activation.install_service(None)
    tool = create_bash_tool(str(tmp_path), {"background_host": lambda: True})
    result = await _call(tool, {"command": "echo x", "run_in_background": True})
    assert result.is_error and _text(result) == _bg().REFUSED_HERE  # M80


@POSIX
async def test_the_title_and_dollar_line_are_the_models_command_not_a_prefix(rig: _Rig) -> None:
    """verify F8: an embedder's ``command_prefix`` runs, but the job is named by the model's command."""

    tool = create_bash_tool(
        str(rig.work), {"background_host": lambda: True, "command_prefix": "set -e  # prefix-line"}
    )
    result = await _call(tool, {"command": "echo mine", "run_in_background": True})
    job_id = _ID.search(_text(result)).group(1)  # type: ignore[union-attr]
    await rig.ended(job_id)
    assert rig.service.snapshot(job_id).title == "echo mine"  # type: ignore[union-attr]
    (notice,) = rig.notices(job_id)
    assert "$ echo mine" in notice.body and "prefix-line" not in notice.body


@WIN32
async def test_repeated_short_jobs_hold_no_process_handles(rig: _Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex 7 on the real kernel: twenty short jobs leave this process's handle count where it was.

    It also says, with ``warnings.warn`` (the one channel a ``-q`` log keeps),
    how the count moves through each job: right after the spawn, the reader's
    start, the reap, the tree's close and the job's settle, each the mean over
    the twenty jobs of the count then minus the count before that job began.
    """

    import ctypes
    import gc
    from ctypes import wintypes

    # A private, typed kernel32: untyped, the pseudo-handle GetCurrentProcess
    # returns (-1) reached GetProcessHandleCount as a 32-bit int and the call
    # failed (#766's first CI run, every windows leg). The shared
    # ``ctypes.windll.kernel32`` stays untouched for the product's own calls.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    count = wintypes.DWORD()

    def handles() -> int:
        ok = kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count))
        assert ok, f"GetProcessHandleCount failed: error {ctypes.get_last_error()}"  # type: ignore[attr-defined]
        return int(count.value)

    phases: dict[str, list[int]] = {"spawn": [], "reader start": [], "reap": [], "tree close": [], "job settle": []}
    origin = {"count": 0}

    def after(name: str, real: Any) -> Any:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            out = real(*args, **kwargs)
            phases[name].append(handles() - origin["count"])
            return out

        return wrapped

    from aelix_ai.utils._process_tree import ProcessTree

    monkeypatch.setattr(_bg(), "_popen_background", after("spawn", _bg()._popen_background))
    monkeypatch.setattr(_bg().BashJobRunner, "_start_reading", after("reader start", _bg().BashJobRunner._start_reading))
    monkeypatch.setattr(_bg().BashJobRunner, "_settle_root", after("reap", _bg().BashJobRunner._settle_root))
    monkeypatch.setattr(ProcessTree, "close", after("tree close", ProcessTree.close))

    for _ in range(3):  # warm up whatever one-time handles a first job opens
        await rig.ended(await rig.start("echo warm"), 30.0)
    gc.collect()
    for values in phases.values():
        values.clear()
    before = handles()
    for _ in range(20):
        origin["count"] = handles()
        await rig.ended(await rig.start("echo short"), 30.0)
        phases["job settle"].append(handles() - origin["count"])
    gc.collect()
    grew = handles() - before
    means = ", ".join(
        f"{name} {sum(values) / len(values):+.1f}" if values else f"{name} n/a" for name, values in phases.items()
    )
    warnings.warn(f"#760 handle count per job: {means}; after 20 short jobs and a gc: {grew:+d}", stacklevel=1)
    assert grew < 10, f"{grew} handles more after 20 short jobs"


@pytest.fixture(autouse=True)
def _no_leftover_service() -> Iterator[None]:
    yield
    activation.install_service(None)
