"""#612 fix round 2 (F1) — ``set_before_session_start`` runs after the rebind and before the handlers.

The CLI binds its job service there (``cli/entry.py``), so a job an extension
starts from a rebuild's ``session_start`` is the starting session's; the effect is
pinned through the real CLI in ``tests/cli/test_session_start_job_owner_612.py``.
Here, the runtime's order on a swap and on a reload, the failed rebuild's restore
included: rebind, then this callback with the NEW harness and the reason, then
the ``session_start`` handlers, then ``set_after_session_start``'s callback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness
from aelix_agent_core.runtime import RebuildFailedError

from tests.runtime.test_failed_rebuild_663 import _once, _Rig


def _record(rig: _Rig) -> list[tuple[str, Any, str]]:
    order: list[tuple[str, Any, str]] = []
    build = rig.factory

    async def factory(session: Any, **kwargs: Any) -> AgentHarness:
        harness = await build(session, **kwargs)
        runner = harness.extension_runner

        def has_handlers(event: str) -> bool:
            return event == "session_start"

        async def emit(event: Any) -> None:
            order.append(("session_start", harness, str(getattr(event, "reason", ""))))

        runner.has_handlers = has_handlers
        runner.emit = emit
        return harness

    async def rebind(harness: AgentHarness, reason: str) -> None:
        order.append(("rebind", harness, reason))

    async def before(harness: AgentHarness, reason: str) -> None:
        order.append(("before", harness, reason))

    async def after(harness: AgentHarness, reason: str) -> None:
        order.append(("after", harness, reason))

    rig.runtime._create_harness = factory
    rig.runtime.set_rebind_session(rebind)
    rig.runtime.set_before_session_start(before)
    rig.runtime.set_after_session_start(after)
    return order


@pytest.mark.parametrize("how", ["reload", "new_session", "switch_session", "fork"])
async def test_the_callback_runs_between_the_rebind_and_the_handlers(tmp_path: Path, how: str) -> None:
    rig = await _Rig.create(tmp_path)
    old = rig.runtime.harness
    order = _record(rig)
    if how == "reload":
        await rig.runtime.reload()
        reason = "reload"
    elif how == "new_session":
        await rig.runtime.new_session()
        reason = "new"
    elif how == "switch_session":
        await rig.runtime.switch_session(str(rig.other.session_file))
        reason = "resume"
    else:
        [point] = [m for m in await old.get_user_messages_for_forking()]
        await rig.runtime.fork(point.entry_id)
        reason = "fork"
    new = rig.runtime.harness
    assert new is not old
    assert order == [
        ("rebind", new, reason),
        ("before", new, reason),
        ("session_start", new, reason),
        ("after", new, reason),
    ]


async def test_a_restored_harness_is_handed_to_the_callback_too(tmp_path: Path) -> None:
    """A reload whose build failed rebuilds the session; that harness is the one bound, before its handlers."""

    rig = await _Rig.create(tmp_path)
    order = _record(rig)
    rig.fail = _once(RuntimeError("first build fails"))
    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()
    assert caught.value.state == "kept"
    restored = rig.runtime.harness
    assert [(step, harness is restored) for step, harness, _reason in order] == [
        ("rebind", True),
        ("before", True),
        ("session_start", True),
        ("after", True),
    ]
