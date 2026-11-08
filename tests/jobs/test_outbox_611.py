"""A notice is posted only after its outcome is confirmed in the file (#722, Codex r1 #5).

Fails on main ``a385c64a``: no job service. Naive: post the notice and write
the outcome "best effort"; after a crash the model has read a result the file
never recorded, and a restart has nothing to reconcile it against.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.session.storage import SessionError
from aelix_coding_agent.jobs.records import fold

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


async def test_failed_outcome_append_is_retried_and_notice_not_posted_until_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aelix_coding_agent.jobs import service as service_module

    monkeypatch.setattr(service_module, "_OUTBOX_BACKOFF", (0.05, 0.05, 0.05, 0.05, 0.05))
    service = fast_service()
    _sleeper.register(service)
    session = await make_session(tmp_path)
    harness = make_harness(session)
    await service.bind(harness)
    real = session.place_custom_entry
    failures = {"left": 3}

    async def flaky(entry_id: str, custom_type: str, data: Any = None, **kwargs: Any) -> Any:
        if isinstance(data, dict) and data.get("fact") == "outcome" and failures["left"] > 0:
            failures["left"] -= 1
            raise SessionError("storage", "disk full")
        return await real(entry_id, custom_type, data, **kwargs)

    session.place_custom_entry = flaky  # type: ignore[method-assign]
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    assert harness.pending_notices == 0, "not posted before the outcome is written"
    assert service.unsettled()
    await wait_for(lambda: harness.pending_notices == 1)
    assert failures["left"] == 0
    job = fold(await session.get_entries())[job_id]
    assert job.outcome is not None and job.outcome["notice"]["id"] == f"{job_id}:final"
    assert not service.unsettled()
    await service.shutdown()


async def test_an_owed_outcome_keeps_p_waiting_even_without_a_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kind that leaves no notice still owes its outcome: ``-p`` waits until it is written."""

    from aelix_coding_agent.jobs import service as service_module

    monkeypatch.setattr(service_module, "_OUTBOX_BACKOFF", (0.05, 0.05, 0.05, 0.05, 0.05))
    service = fast_service()
    _sleeper.register(service, notify=False)
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))
    real = session.place_custom_entry
    failures = {"left": 2}

    async def flaky(entry_id: str, custom_type: str, data: Any = None, **kwargs: Any) -> Any:
        if isinstance(data, dict) and data.get("fact") == "outcome" and failures["left"] > 0:
            failures["left"] -= 1
            raise SessionError("storage", "disk full")
        return await real(entry_id, custom_type, data, **kwargs)

    session.place_custom_entry = flaky  # type: ignore[method-assign]
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    assert service.unsettled(), "its outcome is still owed"
    await wait_for(lambda: not service.unsettled())
    assert fold(await session.get_entries())[job_id].outcome is not None
    await service.shutdown()
