"""Who gets a notice (#722, Codex r3 #14).

Fails on main ``a385c64a``: no job service. Naive: a notice for every ended job,
so a failed launch the tool already reported, and a kind that asked for no
notices, each leave one — and a restart posts one for a ``notify: false`` job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aelix_coding_agent.jobs.records import append_fact, make_fact

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, reopen, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


async def test_launch_failure_posts_no_notice(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    with pytest.raises(Exception, match="no such program"):
        await service.start(_sleeper.KIND, title="nap", params={"launch": "fail"})
    assert harness.pending_notices == 0 and harness.undelivered_notices() == []
    assert not service.unsettled()
    await service.shutdown()


async def test_a_kind_without_notices_leaves_none(tmp_path: Path) -> None:
    service = fast_service()
    _sleeper.register(service, notify=False)
    harness = make_harness(await make_session(tmp_path))
    await service.bind(harness)
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 0.01})
    await wait_for(lambda: service.snapshot(job_id).state == "done")  # type: ignore[union-attr]
    assert harness.pending_notices == 0
    assert not service.unsettled()
    await service.shutdown()


async def test_notify_false_kind_creates_no_notice_on_restore(tmp_path: Path) -> None:
    session = await make_session(tmp_path)
    await append_fact(
        session,
        make_fact(
            "start",
            "j1",
            kind=_sleeper.KIND,
            kind_version=1,
            owner="s",
            mode="background",
            notify=False,
            title="quiet",
            created=1.0,
            settings_key="sleeper",
        ),
    )
    service = fast_service()
    _sleeper.register(service, notify=False)
    harness = make_harness(await reopen(tmp_path))
    await service.bind(harness)
    snap = service.snapshot("j1")
    assert snap is not None and snap.state in ("lost", "unresolved")
    assert harness.pending_notices == 0
    await service.shutdown()
