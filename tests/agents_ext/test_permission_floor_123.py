"""#123 (ADR-0306 §6), W5: a tighter parent posture reaches a running child.

``PermissionPosture`` notifies its listeners on every mutation — ``set`` (an
accepted RPC ``permissions/set``) and ``cycle`` (shift+tab), which
used to assign ``mode`` directly (Codex H6) — and the agents extension's one
listener tightens every live row of every runtime it holds, a spawned job's
included. A loosening notifies and tightens nothing.

Fails on ``a1606f58``: ``PermissionPosture`` has no ``listeners``; nothing reaches a
running child when the parent's posture changes (contract r2 §6.1).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.builtin.permission_mode import (
    CYCLE_ORDER,
    PermissionMode,
    PermissionPosture,
)

from tests.agents_ext.test_agent_background_611 import _rig, jobs_on  # noqa: F401 — the fixture
from tests.agents_ext.test_intervention_doors_123 import _applied, _channel
from tests.agents_ext.test_tool_and_security import _text
from tests.jobs.conftest import wait_for


def test_set_and_cycle_both_reach_a_listener_with_previous_and_current() -> None:
    posture = PermissionPosture(mode=PermissionMode.AUTO_ACCEPT)
    seen: list[tuple[PermissionMode, PermissionMode]] = []
    posture.listeners.append(lambda a, b: seen.append((a, b)))
    assert posture.cycle() is PermissionMode.PLAN, "the UX rotation, unchanged"
    posture.set(PermissionMode.YOLO)
    posture.set(PermissionMode.YOLO)
    assert seen == [
        (PermissionMode.AUTO_ACCEPT, PermissionMode.PLAN),
        (PermissionMode.PLAN, PermissionMode.YOLO),
        (PermissionMode.YOLO, PermissionMode.YOLO),
    ]
    off = PermissionPosture(mode="nope")  # type: ignore[arg-type]
    off.listeners.append(lambda a, b: seen.append((a, b)))
    assert off.cycle() is CYCLE_ORDER[0] and seen[-1][1] is CYCLE_ORDER[0], "the fail-safe restart notifies too"


def test_the_posture_hook_mark_is_not_a_constructor_parameter() -> None:
    """Fix round 1 VN3: ``_posture_hooked`` is the extension's own mark, not something a caller passes.

    On ``8d7ee999`` it was a dataclass field with a default, so ``AgentsExtension(_posture_hooked=True)``
    built an extension that never subscribed to its posture.
    """

    import inspect

    from aelix_agents.extension import AgentsExtension

    assert "_posture_hooked" not in inspect.signature(AgentsExtension).parameters
    assert AgentsExtension()._posture_hooked is False


def test_a_listener_that_raises_drops_nothing() -> None:
    posture = PermissionPosture()
    seen: list[PermissionMode] = []

    def bad(_a: PermissionMode, _b: PermissionMode) -> None:
        raise RuntimeError("x")

    posture.listeners.extend([bad, lambda _a, b: seen.append(b)])
    posture.set(PermissionMode.PLAN)
    assert posture.get() is PermissionMode.PLAN and seen == [PermissionMode.PLAN]


@pytest.mark.skipif(sys.platform == "win32", reason="the stub child's pipes are POSIX here")
@pytest.mark.usefixtures("jobs_on")
async def test_a_shift_tab_tightens_a_spawned_child_and_a_loosening_does_not(tmp_path: Path) -> None:
    record = tmp_path / "applied.txt"
    rig = await _rig(
        tmp_path, channel=_channel(AELIX_JOBS_PROTOCOL="1", STUB_APPLIED=str(record)), posture=PermissionMode.YOLO
    )
    posture: Any = rig.ext.posture
    assert posture is not None and rig.ext._on_posture_change in posture.listeners
    rig.ext._hook_posture()  # what a second build does again: subscribes nothing twice
    assert posture.listeners.count(rig.ext._on_posture_change) == 1
    await rig.prompt_started()
    spawned = await rig.call({"action": "spawn", "profile": "scout", "task": "count files"})
    assert not spawned.is_error, _text(spawned)
    (job_id,) = [s.job_id for s in rig.jobs()]
    await wait_for(lambda: (rig.service.snapshot(job_id).progress or {}).get("last_text") == "halfway")  # type: ignore[union-attr]
    entry = rig.ext._delegation_jobs.info[job_id]  # type: ignore[union-attr]
    runner = entry.runner()
    assert runner is not None
    (row,) = runner._runtime._children.values()
    assert row.granted_mode is PermissionMode.YOLO
    try:
        # shift+tab: YOLO → AUTO (the cycle's next), which ranks BELOW yolo. What
        # the CHILD applied is read (F9), not the row the parent wrote before the
        # acknowledgement: ``auto`` is ``auto-accept-edits`` for a child (F1).
        posture.cycle()
        await wait_for(lambda: _applied(record) == ["launch yolo", "auto-accept-edits"])
        assert row.granted_mode is PermissionMode.AUTO_ACCEPT and row.stopped is False
        posture.set(PermissionMode.YOLO)  # a loosening: notified, nothing sent
        await wait_for(lambda: True)
        assert row.granted_mode is PermissionMode.AUTO_ACCEPT
        posture.set(PermissionMode.PLAN)
        await wait_for(lambda: _applied(record) == ["launch yolo", "auto-accept-edits", "plan"])
        assert row.granted_mode is PermissionMode.PLAN
        assert row.stopped is False and row.abort_reason is None
    finally:
        await rig.service.control(job_id, "steer", {"text": "FINISH"})
        await wait_for(lambda: rig.service.snapshot(job_id).state in ("done", "failed", "stopped"), timeout=30)  # type: ignore[union-attr]
        await rig.service.shutdown()
    assert _applied(record) == ["launch yolo", "auto-accept-edits", "plan"], "the loosening sent nothing"
