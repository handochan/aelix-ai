"""#777 (#611 S3c) PR c1, L6: a job that swallows every cancel, through ``/reload``'s bound and its release.

The kind is ``tests/jobs/fixtures/hold_kind_ext.py``, the ``-e`` extension the
lead's pty run loads through the launcher; here it is loaded from that file and
run in-process with the real job service, its bridge extension (``aelix-jobs``)
and a harness, its run released by an ``asyncio.Event`` or by the file it names.
Its run owns no process: it waits for its release and catches ``CancelledError``
in a loop until then (contract r2 §6 L6, r4 errata E5).

``/reload`` (``session_shutdown("reload")``, which the bridge turns into the
owner's end) stops the session's jobs and waits the service's cleanup bound:
the job ignores the stop, the service's escalation cancels it and it swallows
the cancel, so ``/reload`` returns at the bound with the job
``unresolved(reload_timeout)``, its slot held and its task still the service's;
the footer reads ``⚙ 1 unresolved``. Released, it honours the cancel it
swallowed and ends ``stopped``, its outcome and its record in the session it was
started in, its slot back. A run that only shields a sleep cannot make the
state (Codex r1 #10): the control case ends ``stopped`` inside the bound.

A guard of today's behaviour: green on ``b6100968`` once the fixture exists.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.runtime.agent_session_runtime import _emit_session_shutdown_event
from aelix_ai.streaming import Model
from aelix_coding_agent.extensions.api import _ExtensionRuntime
from aelix_coding_agent.extensions.loader import load_extension_from_factory, load_extensions
from aelix_coding_agent.jobs import activation
from aelix_coding_agent.jobs.contract import ClientOutcome
from aelix_coding_agent.jobs.extension import JobsExtension
from aelix_coding_agent.jobs.indicator import indicator_text
from aelix_coding_agent.jobs.records import JOB_FACT_TYPE, fold

from tests.jobs.conftest import RecordingModel, fast_service, make_session, wait_settled

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hold_kind_ext.py"
BOUND = 0.5
"""The service's cleanup bound here; the product's is ``CLEANUP_BOUND`` (10 s), the lead's pty run's."""

pytestmark = pytest.mark.usefixtures("jobs_active")


def _fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hold_kind_ext_under_test", FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def service() -> AsyncIterator[Any]:
    svc = fast_service(cleanup_bound=BOUND, stop_grace=0.05)
    activation.install_service(svc)
    yield svc
    await svc.shutdown()


async def _build(
    tmp_path: Path, service: Any, setup: Callable[[Any], None]
) -> tuple[AgentHarness, Any]:
    runtime = _ExtensionRuntime()
    bridge = await load_extension_from_factory(
        JobsExtension(service), name="aelix-jobs", runtime=runtime
    )
    hold = await load_extension_from_factory(setup, name="hold-ext", runtime=runtime)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p"),
            stream_fn=RecordingModel(),
            session=await make_session(tmp_path),
            extensions=[bridge, hold],
            runtime=runtime,
            notice_inbox=True,
        )
    )
    await harness.bootstrap()
    await service.bind(harness)
    return harness, hold


async def test_the_extension_file_loads_and_registers_its_command_and_kind(
    tmp_path: Path, service: Any
) -> None:
    """What ``-e tests/jobs/fixtures/hold_kind_ext.py`` does, through the loader's path branch."""

    result = await load_extensions([str(FIXTURE)], cwd=tmp_path)
    assert not result.errors, result.errors
    (extension,) = result.extensions
    assert "hold" in extension.commands
    assert service.registry.get("test:hold") is not None


@pytest.mark.parametrize("released_by", ["event", "file"])
async def test_reload_leaves_it_unresolved_and_its_release_ends_it_stopped(
    tmp_path: Path, service: Any, released_by: str
) -> None:
    module = _fixture()
    event = asyncio.Event()
    marker = tmp_path / "release"

    def release(params: Any) -> Callable[[], Awaitable[None]]:
        return event.wait if released_by == "event" else module.file_release(params)

    harness, hold = await _build(tmp_path, service, module.extension(release))
    said = await hold.commands["hold"].handler(str(marker), None)
    (snap,) = service.snapshots()
    job_id = snap.job_id
    assert job_id in said and snap.state == "running"
    (runner,) = module.HoldRunner.instances
    held = service._jobs[job_id]

    loop = asyncio.get_running_loop()
    begun = loop.time()
    await _emit_session_shutdown_event(harness.extension_runner, "reload")
    took = loop.time() - begun
    assert BOUND - 0.05 <= took < BOUND + 2.0, (
        f"/reload returned after {took:.2f} s, not at the bound"
    )
    snap = service.snapshot(job_id)
    assert snap is not None and (snap.state, snap.unresolved_reason) == (
        "unresolved",
        "reload_timeout",
    )
    assert indicator_text(service.snapshots()) == "⚙ 1 unresolved"
    assert service.admission.count("test:hold") == 1, "its slot is held"
    assert held.task is not None and not held.task.done(), "its run is still the service's"
    assert runner.stops == ["reload"] and runner.cancels >= 1, (runner.stops, runner.cancels)

    since = loop.time()
    if released_by == "event":
        event.set()
    else:
        marker.write_text("go", encoding="utf-8")
    await wait_settled(service, (job_id,), since=since, state="stopped", slots=("test:hold", 0))
    assert runner.released
    folded = fold(await held.session.get_entries())[job_id]
    assert folded.outcome is not None and folded.outcome["status"] == "stopped", folded.outcome
    entries = await held.session.get_entries()
    tagged = [
        e.custom_type for e in entries if e.type == "custom" and e.data.get("job_id") == job_id
    ]
    assert "test.hold" in tagged and tagged[-1] == JOB_FACT_TYPE, tagged


async def test_a_run_that_only_shields_a_sleep_ends_stopped_inside_the_bound(
    tmp_path: Path, service: Any
) -> None:
    """The control (Codex r1 #10): without the loop that swallows each cancel there is no held state."""

    module = _fixture()

    class Shielded(module.HoldRunner):  # type: ignore[name-defined,misc]
        async def run(self, ctx: Any) -> ClientOutcome:
            ctx.launched()
            await asyncio.shield(asyncio.sleep(30))
            return ClientOutcome("done", "slept")

    def setup(aelix: Any) -> None:
        aelix.jobs.register_kind(
            module.HoldAdapter(), lambda _params: Shielded(asyncio.Event().wait)
        )

    harness, _hold = await _build(tmp_path, service, setup)
    job_id = await service.start("test:hold", title="shielded")
    loop = asyncio.get_running_loop()
    begun = loop.time()
    await _emit_session_shutdown_event(harness.extension_runner, "reload")
    assert loop.time() - begun < BOUND, "it ended before the bound"
    snap = service.snapshot(job_id)
    assert snap is not None and snap.state == "stopped"
    assert service.admission.count("test:hold") == 0
