"""``-p`` keeps the first failure of one user prompt and the notice turns after it (#722, ADR-0268 amended).

Owner, 2026-11-07: "``-p`` keeps the first failure's exit code, but only within
one user prompt and the notice turns it caused. Across several user messages
ADR-0268 is unchanged." The contract's row 11 test
``test_first_user_failure_then_user_success_exits_1`` is therefore replaced by
:func:`test_first_user_failure_then_user_success_exits_0`.

Fails on main ``a385c64a``: ``run_print_mode`` takes no ``jobs`` and runs no
notice turn, so the notice cases cannot happen there. Naive: judging the last
answer only (ADR-0268 as it was) exits 0 for a failed answer followed by a
successful notice turn; a latch across every prompt of the run exits 1 for a
failure the user's next message already answered.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.streaming import Model
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.modes import print_mode
from aelix_coding_agent.modes.print_mode import run_print_mode

from tests.jobs import _sleeper
from tests.jobs.conftest import (
    RecordingModel,
    _reap_sleepers,  # noqa: F401 — autouse: reap the children
    fast_service,
    make_session,
)


@pytest.fixture(autouse=True)
def _active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)
    _sleeper.Sleeper.instances.clear()


async def _setup(tmp_path: Path, model: RecordingModel, *, seconds: float | None = 0.2) -> tuple[AgentSessionRuntime, Any]:
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions

    session = await make_session(tmp_path)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"), stream_fn=model, session=session, notice_inbox=True
        )
    )

    async def factory(_s: Any) -> AgentHarness:
        return harness

    runtime = AgentSessionRuntime(harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    if seconds is not None:
        await service.start(_sleeper.KIND, title="nap", params={"seconds": seconds})
    return runtime, service


async def test_first_user_failure_then_user_success_exits_0(tmp_path: Path) -> None:
    model = RecordingModel("fine")
    model.fail_next = [True, False]
    runtime, service = await _setup(tmp_path, model, seconds=None)
    code = await run_print_mode(runtime, mode="text", messages=["second"], initial_message="first", jobs=service)
    assert code == 0, "ADR-0268 unchanged across user messages"
    await service.shutdown()


async def test_notice_failure_after_success_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = RecordingModel("fine")
    model.fail_next = [False, True]
    runtime, service = await _setup(tmp_path, model)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go", jobs=service)
    assert code == 1
    assert len(model.contexts) == 2 and any("sleeper said" in t for t in model.contexts[1])
    assert "boom" in capsys.readouterr().err
    await service.shutdown()


async def test_successful_notice_after_failure_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = RecordingModel("the notice turn's answer")
    model.fail_next = [True, False]
    runtime, service = await _setup(tmp_path, model)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go", jobs=service)
    out = capsys.readouterr()
    assert code == 1
    assert "boom" in out.err
    assert "the notice turn's answer" in out.out, "the last successful answer is still printed"
    await service.shutdown()


async def test_json_mode_latches_too_and_writes_nothing_extra(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    model = RecordingModel("fine")
    model.fail_next = [True, False]
    runtime, service = await _setup(tmp_path, model)
    code = await run_print_mode(runtime, mode="json", messages=[], initial_message="go", jobs=service)
    assert code == 1
    assert capsys.readouterr().err == ""
    await service.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM handler is POSIX (the win32 twin is SIGBREAK)")
async def test_signal_and_epipe_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = RecordingModel("fine")
    runtime, service = await _setup(tmp_path, model, seconds=30)
    loop = asyncio.get_running_loop()
    loop.call_later(0.3, os.kill, os.getpid(), signal.SIGTERM)
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go", jobs=service)
    assert code == 128 + signal.SIGTERM, "the signal's code, not the drain's or the verdict's"
    await service.shutdown()
    runner = _sleeper.Sleeper.instances[-1]
    assert runner.proc is not None and runner.proc.returncode is not None

    (tmp_path / "b").mkdir()
    runtime2, service2 = await _setup(tmp_path / "b", RecordingModel("fine"))

    def dead(_text: str) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(print_mode, "_write_raw_stdout", dead)
    with pytest.raises(BrokenPipeError):
        await run_print_mode(runtime2, mode="json", messages=[], initial_message="go", jobs=service2)
    await service2.shutdown()


# === #722 fix round 1 =========================================================


async def test_a_failed_notice_turn_then_a_successful_one_exits_1(tmp_path: Path) -> None:
    """Verify V13: two notice turns after one user prompt; the first fails, the
    second answers. The latch keeps the first failure (owner, 2026-11-07).

    A drain that does not latch its notice turns judges only the last answer
    and exits 0 here.
    """

    model = RecordingModel("fine")
    model.fail_next = [False, True, False]
    runtime, service = await _setup(tmp_path, model, seconds=0.05)
    await service.start(_sleeper.KIND, title="later nap", params={"seconds": 0.6})
    code = await run_print_mode(runtime, mode="text", messages=[], initial_message="go", jobs=service)
    assert len(model.contexts) == 3, "the prompt and two notice turns"
    assert code == 1
    await service.shutdown()


async def test_an_interrupt_during_the_drain_stops_the_jobs_and_flushes_without_a_turn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify S6, Codex #19: ``asyncio.run`` turns a Ctrl+C into a cancel of the
    main task. The drain stops every job, writes what the model was not given,
    starts no turn for it, and lets the interrupt end the run.

    Fails on ``a80e81fa``: the job was stopped by the dispose but its notice
    stayed pending and was written nowhere.
    """

    from tests.jobs.conftest import wait_for

    for mode in ("text", "json"):
        (tmp_path / mode).mkdir()
        model = RecordingModel("fine")
        runtime, service = await _setup(tmp_path / mode, model, seconds=30)
        run = asyncio.ensure_future(
            run_print_mode(runtime, mode=mode, messages=[], initial_message="go", jobs=service)
        )
        await wait_for(lambda: len(model.contexts) == 1)  # noqa: B023 — awaited in this iteration
        await asyncio.sleep(0.1)  # in the drain, waiting for the job
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        (snap,) = service.snapshots()
        assert snap.state == "stopped"
        assert len(model.contexts) == 1, "no turn for the flushed notice"
        out = capsys.readouterr()
        if mode == "text":
            assert "interrupted — 1 background result was not given to the model" in out.err
            assert 'sleeper "nap" stopped' in out.err
        else:
            events = [json.loads(line) for line in out.out.splitlines() if line.startswith("{")]
            (flushed,) = [e for e in events if e.get("type") == "background_notice_undelivered"]
            assert flushed["notice_id"] == f"{snap.job_id}:final"
            assert "[aelix system notice" in flushed["body"]
        await service.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="a real SIGINT to a child; POSIX")
def test_a_real_ctrl_c_in_the_drain_exits_as_an_interrupt_after_the_flush(tmp_path: Path) -> None:
    """The same with a real SIGINT to a real process: the exit is the interrupt's
    (killed by SIGINT, as without jobs), the line is on stderr, no sleeper is left."""

    import subprocess
    import textwrap

    root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        f"""
        import asyncio, os, signal, sys
        sys.path.insert(0, {str(root)!r})
        from aelix_coding_agent.jobs import activation
        from aelix_coding_agent.modes.print_mode import run_print_mode
        from tests.cli.test_print_verdict_611 import _setup
        from tests.jobs import _sleeper
        from tests.jobs.conftest import RecordingModel
        from pathlib import Path

        async def main():
            activation.ACTIVE = True
            runtime, service = await _setup(Path({str(tmp_path)!r}), RecordingModel("fine"), seconds=30)
            asyncio.get_running_loop().call_later(0.5, os.kill, os.getpid(), signal.SIGINT)
            try:
                await run_print_mode(runtime, mode="text", messages=[], initial_message="go", jobs=service)
            finally:
                pids = [r.proc.pid for r in _sleeper.Sleeper.instances if r.proc is not None]
                print("SLEEPERS", pids, file=sys.stderr, flush=True)
                await service.shutdown()

        asyncio.run(main())
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == -signal.SIGINT, (done.returncode, done.stderr[-2000:])
    assert "interrupted — 1 background result was not given to the model" in done.stderr
    pids = [int(p) for p in done.stderr.split("SLEEPERS [", 1)[1].split("]", 1)[0].split(",") if p.strip()]
    from aelix_ai.utils._process_identity import pid_is_live

    assert pids and all(pid_is_live(pid) is False for pid in pids), "no sleeper is left"
