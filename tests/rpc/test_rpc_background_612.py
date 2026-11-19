"""#612 (owner Q4, 2026-11-18; contract r5 §6.3) — background children on an RPC host.

An idle ``permissions/set`` fires the agents extension's posture listener, which
tightens every live runtime's rows, every background job's runtime included
(``AgentsExtension._live_runtimes``). Before #612 an RPC host had no background
child for it to reach (ADR-0306 D8's measured RPC rows); since Q4 opened ``spawn``
there, a running background child is tightened — asynchronously: the response
can arrive before the child applied it.

Fails on ``567753c0`` through the CLI only (an RPC launch registered no ``agent``
job kind, ``tests/cli/test_job_tools_builtin_779.py``'s RPC row); this test drives
the plane the CLI builds over the extension the CLI builds, with the kind
registered, and pins that the listener reaches the job's runtime from an RPC
``permissions/set``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.builtin.permission_mode import PermissionMode
from aelix_coding_agent.rpc.control import RpcControlPlane

from tests.agents_ext._detach_rig import reap_children  # noqa: F401 — the fixture
from tests.agents_ext.test_agent_background_611 import _rig, jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_intervention_doors_123 import _applied, _channel
from tests.jobs.conftest import wait_for

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="the stub children's pipes are POSIX here"),
    pytest.mark.usefixtures("jobs_on", "reap_children"),
]


async def test_an_idle_permissions_set_plan_tightens_a_running_background_child(tmp_path: Path) -> None:
    applied = tmp_path / "applied.txt"
    rig = await _rig(
        tmp_path,
        channel=_channel(STUB_APPLIED=str(applied), AELIX_JOBS_PROTOCOL="1"),
        posture=PermissionMode.AUTO_ACCEPT,
    )
    try:
        await rig.prompt_started()
        spawned = await rig.call({"action": "spawn", "profile": "scout", "task": "count files"})
        assert not spawned.is_error, spawned.content[0].text  # type: ignore[union-attr]
        (job_id,) = [s.job_id for s in rig.jobs()]
        await wait_for(
            lambda: (rig.service.snapshot(job_id).progress or {}).get("last_text") == "halfway"  # type: ignore[union-attr]
        )
        assert _applied(applied) == ["launch auto-accept-edits"]
        assert rig.ext.posture is not None
        plane = RpcControlPlane(
            SimpleNamespace(harness=rig.harness), permission_posture=rig.ext.posture
        )
        answer: dict[str, Any] = await plane.handle("permissions/set", {"mode": "plan"})
        assert answer == {"mode": "plan"}
        await wait_for(lambda: _applied(applied) == ["launch auto-accept-edits", "plan"])
        snap = rig.service.snapshot(job_id)
        assert snap is not None and snap.state == "running", "tightened, not stopped"
    finally:
        await rig.service.shutdown()
