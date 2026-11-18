"""EOF on the command inbox ends the child, in every moment of its run (#123 fix round 1, F2).

``8d7ee999`` aborted the turn at EOF and nothing else: a child that had its own
background job went on waiting for it in ``-p``'s drain, then ran a notice turn
with tools for a parent that was gone (measured on a real child: killed at
2.65 s, exited at 7.91 s, two model requests after the kill, one of them a
``write``). Here EOF is recorded on the inbox, the drain stops on it, no prompt
and no notice turn starts after it, the dispose stops the child's own jobs and
the run exits 1.

The in-process tests run on every platform; the three against a real child and a
scripted chat-completions server are POSIX (a real child's pipes).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.streaming import Model
from aelix_coding_agent.builtin.permission_mode import PermissionMode, PermissionPosture
from aelix_coding_agent.cli.args import COMMAND_INBOX_FLAG, COMMAND_INBOX_VERSION
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.modes import print_mode
from aelix_coding_agent.modes.print_inbox import CommandInbox
from aelix_coding_agent.modes.print_mode import _drain_background, _FirstFailure, run_print_mode

from tests.cli.test_command_inbox_123 import (
    _child_setup,
    _ModelServer,
    _Run,
    _text_turn,
    _tool_turn,
    posix_only,
)
from tests.jobs import _sleeper
from tests.jobs.conftest import (
    RecordingModel,
    _reap_sleepers,  # noqa: F401 — autouse: reap the sleeper jobs
    fast_service,
    make_session,
    wait_for,
)

#: How long the child's own background job would run if nothing stopped it, and
#: how long a child may take to be gone once its inbox is at EOF. The fixed child
#: is gone in about a second (the drain's 1 s wait, then the dispose); the
#: unfixed one waited the whole job out.
_JOB_SECONDS = 40
_EXIT_BOUND = 20.0


# === in process ====================================================================


async def test_eof_during_the_drain_ends_the_run_and_starts_no_notice_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run's answer is in and it waits for its own tracked job: EOF ends the wait.

    Fails on ``8d7ee999``: the drain's ``stop`` knew stdout and signals only, so
    the run sat in it until the job ended (a timeout here).
    """

    monkeypatch.setattr(activation, "ACTIVE", True)
    monkeypatch.setenv("AELIX_JOBS_PROTOCOL", "1")  # an owned run writes aelix_print_done
    _sleeper.Sleeper.instances.clear()
    model = RecordingModel("seen")
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=model,
            session=await make_session(tmp_path),
            notice_inbox=True,
        )
    )

    async def factory(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    job = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    reader = asyncio.StreamReader()
    inbox = CommandInbox(reader=reader, posture=PermissionPosture(mode=PermissionMode("yolo")))
    lines: list[str] = []
    monkeypatch.setattr(print_mode, "_write_raw_stdout", lines.append)
    task = asyncio.ensure_future(
        run_print_mode(runtime, mode="json", messages=[], initial_message="go", jobs=service, inbox=inbox)
    )
    try:
        await wait_for(lambda: any('"agent_end"' in line for line in lines))  # the turn is over: the drain is next
        await asyncio.sleep(0.1)
        assert not task.done(), "the run waits for its tracked job"
        reader.feed_eof()
        done, _ = await asyncio.wait({task}, timeout=5)
        assert done, "the run was still draining 5 s after its inbox reached EOF"
        assert task.result() == 1, "an inbox that ended before the run did is a failed run"
        assert len(model.contexts) == 1, "no notice turn after the EOF"
        assert '"aelix_print_done"' in lines[-1], lines[-1]
        assert service.snapshot(job).state == "running", "stopping it is the dispose's, not the drain's"  # type: ignore[union-attr]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        await service.owner_ended(service.owner, "owner_end")
        await service.shutdown()


async def test_the_drain_starts_no_notice_turn_when_the_stop_came_true_while_it_read_the_leaf() -> None:
    """The one await between the drain's loop check and its notice turn.

    Fails on ``8d7ee999``: the stop was read at the top of the pass only, so an
    EOF that landed while the leaf was read still got its model call.
    """

    ended = {"v": False}

    class _Harness:
        pending_notices = 0

        async def unanswered_notice_at_leaf(self) -> bool:
            ended["v"] = True  # the inbox reaches EOF during this read
            return True

        async def prompt_notices(self) -> Any:
            raise AssertionError("a notice turn was started after the stop came true")

    await asyncio.wait_for(
        _drain_background(
            SimpleNamespace(harness=_Harness()),  # type: ignore[arg-type]
            SimpleNamespace(),
            _FirstFailure([]),
            stop=lambda: ended["v"],
        ),
        5,
    )


async def test_an_inbox_already_at_eof_starts_no_model_call_and_the_run_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard (finding F2: not reproduced without ``asyncio.eager_task_factory``): a
    run whose inbox is at EOF before its first prompt makes no model request."""

    run = _Run(monkeypatch)
    run.reader.feed_eof()
    run.start()
    assert await run.finish() == 1
    assert run.provider.seen == []
    assert run.types[-1] == "aelix_print_done"


async def test_a_run_whose_inbox_has_ended_starts_neither_its_prompt_nor_a_residual_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The by-construction half: the flag alone, with the reader left open so no
    abort is in play. ``8d7ee999`` has no flag to read (there both prompts run)."""

    run = _Run(monkeypatch)
    run.release.set()  # a prompt that did start would run to its end, not hang
    run.inbox.ended = True  # what the pump records at EOF
    run.task = asyncio.ensure_future(
        print_mode.run_print_mode(
            run.runtime, mode="json", messages=["SECOND"], initial_message="TASK", inbox=run.inbox
        )
    )
    assert await run.finish() == 1
    assert run.provider.seen == []
    assert run.types[-1] == "aelix_print_done"


# === a real child ==================================================================


def _alive(pid: int) -> bool:
    """Whether ``pid`` still runs. A zombie does not: under a pid 1 that reaps
    nothing (a container) an orphan stays in the table after it ended."""

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True  # no /proc (macOS): the signal-0 answer stands


def _kill(pid: int | None) -> None:
    """Clean-up for a process this test started and may have left behind."""

    if pid is None or not _alive(pid):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _job_script(pid_file: Path) -> Any:
    """Request 1 starts a background ``bash`` job that writes its pid and runs
    ``_JOB_SECONDS``; request 2 answers; a request after that is the notice turn
    the fixed child never makes."""

    command = (
        f"echo $$ > {shlex.quote(str(pid_file))}; "
        f"for i in $(seq 1 {_JOB_SECONDS * 10}); do sleep 0.1; done"
    )

    def script(n: int) -> list[dict[str, Any]]:
        if n == 1:
            return _tool_turn("bash", {"command": command, "run_in_background": True})
        return _text_turn("WAITING" if n == 2 else f"AFTER {n}")

    return script


def _argv(task: str) -> list[str]:
    return [
        sys.executable, "-P", "-m", "aelix_coding_agent",
        "--mode", "json", "-p", f"{COMMAND_INBOX_FLAG}={COMMAND_INBOX_VERSION}",
        "--model", "mockm", "--provider", "mockp", "--no-session", "--no-extensions",
        "--permission-mode", "yolo", "--no-agents", task,
    ]  # fmt: skip


async def _until(predicate: Any, what: str, limit: float = 60.0) -> None:
    started = time.monotonic()
    while not predicate():
        assert time.monotonic() - started < limit, what
        await asyncio.sleep(0.05)


@posix_only
async def test_a_real_child_whose_inbox_closes_while_it_drains_exits_and_stops_its_own_job(
    tmp_path: Path,
) -> None:
    """Fails on ``8d7ee999``: the child waited for its job (the bound here), then
    asked the model again with its tools."""

    pid_file = tmp_path / "job.pid"
    with _ModelServer(script=_job_script(pid_file)) as server:
        cwd, env = _child_setup(tmp_path, server)
        proc = await asyncio.create_subprocess_exec(
            *_argv("Task: start the job, then say WAITING"),
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        out: list[bytes] = []
        turn_over = asyncio.Event()

        async def read_out() -> None:
            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                out.append(line)
                if json.loads(line).get("type") == "agent_end":
                    turn_over.set()

        reads = asyncio.gather(read_out(), proc.stderr.read())
        try:
            await asyncio.wait_for(turn_over.wait(), 60)
            await _until(lambda: _read_pid(pid_file) is not None, "the job never wrote its pid", 10)
            await asyncio.sleep(0.5)  # the drain is in its wait
            assert proc.returncode is None, "the child waits for its own job"
            requests_at_eof = len(server.requests)
            proc.stdin.close()
            closed = time.monotonic()
            done, _ = await asyncio.wait({asyncio.ensure_future(proc.wait())}, timeout=_EXIT_BOUND)
            assert done, f"the child was still running {_EXIT_BOUND:.0f} s after its inbox closed"
            took = time.monotonic() - closed
            _, err = await asyncio.wait_for(reads, 10)
            job_pid = _read_pid(pid_file)
            assert job_pid is not None
            await _until(lambda: not _alive(job_pid), "the child's own job outlived it", 5)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            _kill(_read_pid(pid_file))
        stderr = err.decode("utf-8", errors="replace")
    assert requests_at_eof == 2, [len(r.get("messages", [])) for r in server.requests]
    assert len(server.requests) == requests_at_eof, "a model request after the EOF"
    assert proc.returncode == 1, stderr
    assert took < _EXIT_BOUND
    assert "Traceback" not in stderr, stderr


@posix_only
async def test_a_real_child_whose_inbox_is_closed_before_its_prompt_makes_no_model_request(
    tmp_path: Path,
) -> None:
    """Guard: measured on ``8d7ee999`` too (exit 1, 0 requests; the abort cut the
    turn before its request). The check before each prompt keeps it by construction."""

    with _ModelServer(script=lambda n: _text_turn(f"PONG {n}")) as server:
        cwd, env = _child_setup(tmp_path, server)
        proc = await asyncio.create_subprocess_exec(
            *_argv("Task: say PONG"),
            cwd=str(cwd), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        proc.stdin.close()
        try:
            _, err = await asyncio.wait_for(asyncio.gather(proc.stdout.read(), proc.stderr.read()), 60)
            await asyncio.wait_for(proc.wait(), 10)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    stderr = err.decode("utf-8", errors="replace")
    assert proc.returncode == 1, stderr
    assert server.requests == []
    assert "Traceback" not in stderr, stderr


_PARENT = """
import json, subprocess, sys, time
spec = json.load(open(sys.argv[1], encoding="utf-8"))
child = subprocess.Popen(
    spec["argv"], cwd=spec["cwd"], env=spec["env"], stdin=subprocess.PIPE,
    stdout=open(spec["stdout"], "wb"), stderr=open(spec["stderr"], "wb"), start_new_session=True,
)
print(child.pid, flush=True)
time.sleep(600)
"""


@posix_only
def test_a_child_whose_parent_is_sigkilled_while_it_drains_exits_with_no_model_call_after_the_kill(
    tmp_path: Path,
) -> None:
    """Contract r2 §12: "a SIGKILLed parent → the child aborts on EOF and exits".

    The parent here is a process that holds the child's stdin pipe and nothing
    else; killing it closes the pipe's only write end. Fails on ``8d7ee999``: the
    orphan waited for its job, then made a model request with its tools.
    """

    pid_file = tmp_path / "job.pid"
    spec_file = tmp_path / "spec.json"
    child_pid: int | None = None
    with _ModelServer(script=_job_script(pid_file)) as server:
        cwd, env = _child_setup(tmp_path, server)
        spec_file.write_bytes(
            json.dumps(
                {
                    "argv": _argv("Task: start the job, then say WAITING"),
                    "cwd": str(cwd),
                    "env": env,
                    "stdout": str(tmp_path / "child.out"),
                    "stderr": str(tmp_path / "child.err"),
                }
            ).encode("utf-8")
        )
        parent = subprocess.Popen(  # noqa: S603 — this interpreter, a script of this file
            [sys.executable, "-c", _PARENT, str(spec_file)], stdout=subprocess.PIPE, env={"PATH": env["PATH"]}
        )
        try:
            assert parent.stdout is not None
            child_pid = int(parent.stdout.readline())
            started = time.monotonic()
            while len(server.requests) < 2 or _read_pid(pid_file) is None:
                assert _alive(child_pid), "the child ended before its second request"
                assert time.monotonic() - started < 60, "the child never reached its drain"
                time.sleep(0.05)
            time.sleep(1.0)  # the second answer is in and the drain is in its wait
            assert _alive(child_pid), "the child waits for its own job"
            requests_at_kill = len(server.requests)
            parent.kill()
            parent.wait()
            killed = time.monotonic()
            while _alive(child_pid) and time.monotonic() - killed < _EXIT_BOUND:
                time.sleep(0.05)
            orphan_alive = _alive(child_pid)
            job_pid = _read_pid(pid_file)
            job_deadline = time.monotonic() + 5
            while job_pid is not None and _alive(job_pid) and time.monotonic() < job_deadline:
                time.sleep(0.05)
            job_alive = job_pid is not None and _alive(job_pid)
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            _kill(child_pid)
            _kill(_read_pid(pid_file))
        stderr = (tmp_path / "child.err").read_bytes().decode("utf-8", errors="replace")
    assert not orphan_alive, f"the child was still running {_EXIT_BOUND:.0f} s after its parent was killed"
    assert requests_at_kill == 2
    assert len(server.requests) == requests_at_kill, "a model request after the parent was killed"
    assert not job_alive, "the child's own job outlived it"
    assert "Traceback" not in stderr, stderr
