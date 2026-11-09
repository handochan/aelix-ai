"""Kinds by name and version across re-registrations (#722, Codex r3 #10).

Fails on main ``a385c64a``: no registry. Naive: the notice is rendered by the
adapter the job started with, so a ``/reload`` that fixed the adapter still
renders the old text; a record whose kind is not registered is guessed at
(``lost``) instead of kept ``unresolved`` until the kind returns.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.records import append_fact, make_fact

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, reopen, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


async def test_old_generation_notice_rendered_by_current_adapter_or_stored_body(
    tmp_path: Path,
) -> None:
    service = fast_service()
    _sleeper.register(service, tag="[gen1] ")
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.3})
    _sleeper.register(service, tag="[gen2] ")  # a /reload registers the kind again
    await wait_for(lambda: harness.pending_notices == 1)
    (notice,) = harness.undelivered_notices()
    assert "[gen2] sleeper said" in notice.body
    await service.shutdown()

    # A restart with no adapter at all: the body stored with the outcome is posted.
    stored = fast_service()
    h2 = make_harness(await reopen(tmp_path))
    await stored.bind(h2)
    (again,) = h2.undelivered_notices()
    assert again.body == notice.body
    assert stored.snapshot(job_id).state == "done"  # type: ignore[union-attr]


async def test_missing_adapter_is_unresolved_and_reevaluated_on_registration(
    tmp_path: Path,
) -> None:
    session = await make_session(tmp_path)
    await append_fact(
        session,
        make_fact(
            "start",
            "j",
            kind=_sleeper.KIND,
            kind_version=2,
            owner="s",
            mode="background",
            notify=True,
            title="nap",
            created=1.0,
            settings_key="sleeper",
        ),
    )
    await session.append_custom_entry("test.sleeper", {"job_id": "j", "status": "done"})
    service = fast_service()
    harness = make_harness(await reopen(tmp_path))
    await service.bind(harness)
    assert service.snapshot("j").unresolved_reason == "adapter_missing"  # type: ignore[union-attr]

    _sleeper.register(service, version=1)
    await asyncio.sleep(0.05)
    assert service.snapshot("j").unresolved_reason == "adapter_too_old"  # type: ignore[union-attr]
    # Codex #11: one restored "unresolved" notice — the model is told — and
    # nothing the kind would say about a record it cannot read is made up.
    (notice,) = harness.undelivered_notices()
    assert notice.notice_id == "j:unresolved" and "(adapter_missing)" in notice.body
    # Nothing rechecks it but a registration of its kind: no "keeps checking" (#611 S1c).
    assert "keeps checking" not in notice.body and "it is not watching this job" in notice.body
    assert "sleeper said" not in notice.body
    assert service.admission.count(_sleeper.KIND) == 1, "held until it is settled"

    _sleeper.register(service, version=2)
    await wait_for(lambda: service.snapshot("j").state == "done")  # type: ignore[union-attr]
    assert harness.pending_notices == 2
    assert service.admission.count(_sleeper.KIND) == 0


async def test_a_job_keeps_the_runner_it_started_with(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.2})
    first = _sleeper.Sleeper.instances[-1]

    class Other(_sleeper.Sleeper):
        pass

    service.register_kind(_sleeper.SleeperAdapter(), Other)
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    assert "settle" in first.calls
    assert all(type(r) is _sleeper.Sleeper for r in _sleeper.Sleeper.instances)
    await service.shutdown()


def test_a_kind_needs_a_name_and_a_version() -> None:
    service = fast_service()
    with pytest.raises(ValueError):
        service.register_kind(_sleeper.SleeperAdapter(version=0), _sleeper.Sleeper)
    bad: Any = _sleeper.SleeperAdapter()
    bad.name = ""
    with pytest.raises(ValueError):
        service.register_kind(bad, _sleeper.Sleeper)
