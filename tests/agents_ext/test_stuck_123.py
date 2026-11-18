"""#123 (ADR-0306 §5): stuck detection marks, nudges once, and never kills.

A child that makes four identical calls with identical results is ``stuck:
repeat`` on its progress snapshot; the parent writes ONE nudge into its inbox; a
second trigger keeps the mark and sends nothing; the child is never killed for
it. ``stuck_of`` itself and the fingerprints are pinned in
``test_command_wire_123.py``; the ``quiet`` flag's guard is
``tests/jobs/test_monitoring_611.py::test_quiet_flag_never_kills_or_notifies``,
green on the base and after.

Fails on ``a1606f58``: ``SubagentProgress`` has no ``stuck`` and nothing is ever
written to a child's stdin.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.command_wire import NUDGE_TEXT
from aelix_agents.print_channel import PrintChannel
from aelix_agents.runtime import SpawnRecordMeta, SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.builtin.permission_mode import PermissionMode

from tests.agents_ext.test_child_session_records import _grant, _parent, _resolved
from tests.agents_ext.test_print_channel_spawn import _stub
from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stub child's pipes are POSIX here")

_REPEATING = _stub(
    """
    import select
    start()
    def same(n):
        emit({"type": "tool_execution_start", "tool_name": "grep", "tool_call_id": "c%d" % n, "args": {"q": "x"}})
        emit({"type": "tool_execution_end", "tool_call_id": "c%d" % n, "is_error": False,
              "result": {"content": [{"type": "text", "text": "nothing"}]}})
    got = []
    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([0], [], [], 0.05)
            if ready:
                line = sys.stdin.readline()
                if not line:
                    return
                req = json.loads(line)
                got.append(req["message"])
                emit({"type": "response", "command": req["type"], "id": req["id"], "success": True, "data": {"phase": "streaming"}})
    for n in range(1, 5):
        same(n)
    drain(1.5)
    for n in range(5, 9):
        same(n)
    drain(1.0)
    say("nudges=%d first=%r" % (len(got), got[0] if got else None))
    done()
    """
)


async def test_four_identical_calls_mark_stuck_and_nudge_exactly_once(tmp_path: Path) -> None:
    base = {k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH", "HOME", "TMPDIR")}
    session, _repo, _path = await _parent(tmp_path)
    marks: list[Any] = []
    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path / "p"), session=lambda: session, on_progress=marks.append),
        channel=PrintChannel(
            grace=1.0,
            argv_builder=lambda *_a, **_k: [sys.executable, "-c", _REPEATING],
            env_builder=lambda *_a, **_k: dict(base),
        ),
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(
            _grant(PermissionMode.YOLO), _resolved(), "go", record=SpawnRecordMeta(tool_call_id="tc-1", mode="single", index=0)
        )
    )
    await wait_until(lambda: any(m.stuck for m in marks) or task.done(), what="the stuck mark")
    assert not task.done(), task.result()
    (row,) = runtime._children.values()
    assert row.nudged is True
    result = await asyncio.wait_for(task, 30)
    assert result.ok is True, result.summary
    assert result.summary == f"nudges=1 first={NUDGE_TEXT!r}", "one nudge, the second trigger sent nothing"
    stuck_marks = [m.stuck for m in marks if m.stuck]
    assert set(stuck_marks) == {"repeat"}
    assert marks[-1].state == "done", "never killed for it"
