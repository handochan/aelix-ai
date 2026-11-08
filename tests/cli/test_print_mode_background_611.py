"""``-p`` waits for tracked jobs and gives the model what they leave (#722, ADR-0293 §drain).

Fails on main ``a385c64a``: ``run_print_mode`` has no drain, so it returns while
a job still runs and its result never reaches the model (opencode #48361's
shape). Naive: waiting only while the last answer succeeded (Codex r1 #11), or
waiting for every job including those of kinds that do not block ``-p``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime import AgentSessionRuntime
from aelix_agent_core.session import JsonlSessionRepo, LocalFileSystem
from aelix_ai.streaming import Model
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.contract import KindCapabilities
from aelix_coding_agent.modes.print_mode import run_print_mode

from tests.jobs import _sleeper
from tests.jobs.conftest import (
    RecordingModel,
    _reap_sleepers,  # noqa: F401 — autouse: reap the children
    fast_service,
    make_session,
    reopen,
)


@pytest.fixture(autouse=True)
def _active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(activation, "ACTIVE", True)
    _sleeper.Sleeper.instances.clear()


def _runtime(harness: AgentHarness) -> AgentSessionRuntime:
    async def factory(_s: Any) -> AgentHarness:
        return harness

    return AgentSessionRuntime(harness, factory, repo=JsonlSessionRepo(fs=LocalFileSystem()), fs=LocalFileSystem())


def _harness(session: Any, model: RecordingModel) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"), stream_fn=model, session=session, notice_inbox=True
        )
    )


async def test_waits_for_tracked_jobs_only(tmp_path: Path) -> None:
    model = RecordingModel("seen")
    harness = _harness(await make_session(tmp_path), model)
    service = fast_service()
    _sleeper.register(service)

    class Untracked(_sleeper.SleeperAdapter):
        name = "test:untracked"

        def __init__(self) -> None:
            super().__init__()
            self.capabilities = KindCapabilities(blocks_print_exit=False)

    service.register_kind(Untracked(), _sleeper.Sleeper)
    await service.bind(harness)
    tracked = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.4})
    untracked = await service.start("test:untracked", title="forever", params={"seconds": 60})

    code = await run_print_mode(_runtime(harness), mode="text", messages=[], initial_message="go", jobs=service)

    assert code == 0
    assert service.snapshot(tracked).state == "done"  # type: ignore[union-attr]
    assert any("sleeper said" in t for t in model.contexts[-1]), "its notice reached the model"
    assert service.snapshot(untracked).state == "running", "not waited for"  # type: ignore[union-attr]
    await service.owner_ended(service.owner, "owner_end")  # what the bridge does at the dispose
    assert service.snapshot(untracked).state == "stopped"  # type: ignore[union-attr]
    await service.shutdown()


async def test_waits_after_a_failed_answer(tmp_path: Path) -> None:
    model = RecordingModel("late")
    model.fail_next = [True, False]
    harness = _harness(await make_session(tmp_path), model)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.3})

    code = await run_print_mode(_runtime(harness), mode="text", messages=[], initial_message="go", jobs=service)

    assert code == 1
    assert service.snapshot(job_id).state == "done"  # type: ignore[union-attr]
    assert len(model.contexts) == 2
    await service.shutdown()


async def test_continue_drains_restored_notices(tmp_path: Path) -> None:
    model = RecordingModel("seen")
    first = _harness(await make_session(tmp_path), model)
    service = fast_service()
    _sleeper.register(service)
    await service.bind(first)
    await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    while first.pending_notices == 0:
        import asyncio

        await asyncio.sleep(0.02)
    await service.shutdown()

    # ``-p --continue`` with no prompt, in a new process: the restored notice is drained.
    model2 = RecordingModel("got it")
    second = _harness(await reopen(tmp_path), model2)
    service2 = fast_service()
    _sleeper.register(service2)
    await service2.bind(second)
    code = await run_print_mode(_runtime(second), mode="text", messages=[], initial_message=None, jobs=service2)
    assert code == 0
    assert len(model2.contexts) == 1 and any("sleeper said" in t for t in model2.contexts[0])
    assert not await second.unanswered_notice_at_leaf()
    await service2.shutdown()


async def test_without_jobs_nothing_differs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The gate off: no service is passed, and a run is #410's run."""

    model = RecordingModel("plain")
    harness = _harness(await make_session(tmp_path), model)
    code = await run_print_mode(_runtime(harness), mode="text", messages=[], initial_message="go")
    assert code == 0 and capsys.readouterr().out == "plain\n"
    assert len(model.contexts) == 1


async def test_the_drain_takes_a_notice_that_arrives_while_it_yields_before_leaving(tmp_path: Path) -> None:
    """The drain's last pass yields once and leaves only if nothing appeared
    meanwhile (ADR-0293 §11). A notice posted in exactly that yield gets its
    notice turn; without the re-check after the yield (verify V12) the drain
    returns with it pending and ``-p`` exits without giving it to the model."""

    import asyncio

    from aelix_agent_core.harness.inbox import Notice
    from aelix_coding_agent.modes.print_mode import _drain_background, _FirstFailure

    model = RecordingModel("seen")
    harness = _harness(await make_session(tmp_path), model)

    class _Jobs:
        def __init__(self) -> None:
            self.asked = 0

        def unsettled(self) -> bool:
            self.asked += 1
            if self.asked == 1:  # a job's notice lands during the drain's yield
                asyncio.get_running_loop().call_soon(
                    harness.post_notice,
                    Notice(notice_id="late", custom_type="t", body="LATE-RESULT"),
                )
            return False

    await asyncio.wait_for(
        _drain_background(_runtime(harness), _Jobs(), _FirstFailure([]), stop=lambda: False), 5
    )
    assert harness.pending_notices == 0
    assert model.contexts == [["LATE-RESULT"]]
