"""A directory a job registers is the service's: removed only after ``helper_cleanup`` and the facts (#722, Codex r5 #7).

The generic half of the cleanup-obligation interface (the contract's §2.3
``register_directory``); delegation's prompt directory moving onto it is #611
S1c, and the launch ledger that lives there is S2. Fails on main ``a385c64a``:
no job service. Naive: a runner (or its channel) removing its own directory when
``run`` returns — Codex's probe 3 found the ledger gone before the finalizer
read it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.jobs.contract import ClientOutcome, HelperCleanup
from aelix_coding_agent.jobs.records import fold

from tests.jobs import _sleeper
from tests.jobs.conftest import fast_service, make_harness, make_session, wait_for

pytestmark = pytest.mark.usefixtures("jobs_active")


async def test_service_owned_directory_survives_run_and_stop_until_helper_cleanup(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    owned = tmp_path / "owned"

    class WithDir(_sleeper.Sleeper):
        async def run(self, ctx: Any) -> ClientOutcome:
            owned.mkdir()
            (owned / "ledger").write_text("launched")
            ctx.register_directory(str(owned))
            return await super().run(ctx)

        async def settle(self) -> None:
            seen["at_settle"] = owned.exists()
            await super().settle()

        async def helper_cleanup(self, receipt: Any) -> HelperCleanup:
            seen["at_helper_cleanup"] = (owned / "ledger").read_text()
            return HelperCleanup("confirmed")

    service = fast_service()
    service.register_kind(_sleeper.SleeperAdapter(), WithDir)
    session = await make_session(tmp_path)
    await service.bind(make_harness(session))
    job_id = await service.start(_sleeper.KIND, title="nap", params={"seconds": 30})
    await service.stop(job_id)
    await wait_for(lambda: service.snapshot(job_id).state == "stopped")  # type: ignore[union-attr]
    assert seen == {"at_settle": True, "at_helper_cleanup": "launched"}
    assert not owned.exists(), "removed once the facts are written"
    job = fold(await session.get_entries())[job_id]
    assert job.cleanup is not None and job.cleanup["helpers"] == "confirmed"
    assert job.outcome is not None
    await service.shutdown()
