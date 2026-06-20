"""Lane C — cooperative-abort responsiveness tests.

Verify that the leaf I/O in read/write/edit/ls is dispatched via
``asyncio.to_thread`` so the event loop stays alive and a concurrent
coroutine can make progress while a (simulated) blocking file operation
is in flight.

Design: monkeypatch the sync leaf functions (e.g. ``Path.read_bytes``,
``Path.write_bytes``, ``Path.iterdir``) to call ``time.sleep`` before
returning.  Because the production code wraps these in
``asyncio.to_thread``, the sleep runs on a thread pool worker and the
event loop remains free.  A concurrent counter task should therefore
increment at least once.  If the code reverted to a direct sync call,
the sleep would freeze the event loop and the counter would stay at 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import (
    create_edit_tool,
    create_ls_tool,
    create_read_tool,
    create_write_tool,
)

_SLEEP_S = 0.05  # 50 ms — fast enough for CI, long enough to prove yielding


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(tool_call_id="t")


# ---------------------------------------------------------------------------
# Helper: run tool + counter concurrently; assert counter advanced
# ---------------------------------------------------------------------------


async def _assert_loop_yields_during(coro) -> None:
    """Run *coro* concurrently with an incrementing task; assert > 0 ticks."""

    ticks: list[int] = [0]

    async def _counter() -> None:
        while True:
            await asyncio.sleep(0)  # yield each iteration
            ticks[0] += 1

    task = asyncio.create_task(_counter())
    try:
        await coro
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert ticks[0] > 0, (
        "event loop was frozen during blocking I/O — to_thread not used"
    )


# ---------------------------------------------------------------------------
# Tests — patch the sync leaf at the Path level so time.sleep runs in thread
# ---------------------------------------------------------------------------


async def test_read_tool_yields_to_event_loop(tmp_path, monkeypatch):
    """read: Path.read_bytes wrapped in to_thread → loop stays alive."""
    f = tmp_path / "f.txt"
    f.write_text("hello\n")
    real_read_bytes = Path.read_bytes

    def _slow_read_bytes(self):
        time.sleep(_SLEEP_S)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _slow_read_bytes)
    tool = create_read_tool(str(tmp_path))

    await _assert_loop_yields_during(tool.execute({"path": "f.txt"}, _ctx()))


async def test_write_tool_yields_to_event_loop(tmp_path, monkeypatch):
    """write: Path.write_bytes wrapped in to_thread → loop stays alive."""
    real_write_bytes = Path.write_bytes

    def _slow_write_bytes(self, data):
        time.sleep(_SLEEP_S)
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _slow_write_bytes)
    tool = create_write_tool(str(tmp_path))

    await _assert_loop_yields_during(
        tool.execute({"path": "f.txt", "content": "hi"}, _ctx())
    )


async def test_edit_tool_yields_to_event_loop(tmp_path, monkeypatch):
    """edit: Path.read_bytes + Path.write_bytes in to_thread → loop alive."""
    f = tmp_path / "f.txt"
    f.write_text("old text\n")
    real_read_bytes = Path.read_bytes
    real_write_bytes = Path.write_bytes

    def _slow_read_bytes(self):
        time.sleep(_SLEEP_S)
        return real_read_bytes(self)

    def _slow_write_bytes(self, data):
        time.sleep(_SLEEP_S)
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "read_bytes", _slow_read_bytes)
    monkeypatch.setattr(Path, "write_bytes", _slow_write_bytes)
    tool = create_edit_tool(str(tmp_path))

    await _assert_loop_yields_during(
        tool.execute(
            {
                "path": "f.txt",
                "edits": [{"oldText": "old text", "newText": "new text"}],
            },
            _ctx(),
        )
    )


async def test_ls_tool_yields_to_event_loop(tmp_path, monkeypatch):
    """ls: Path.iterdir wrapped in to_thread → loop stays alive."""
    (tmp_path / "a.txt").write_text("")
    real_iterdir = Path.iterdir

    def _slow_iterdir(self):
        time.sleep(_SLEEP_S)
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _slow_iterdir)
    tool = create_ls_tool(str(tmp_path))

    await _assert_loop_yields_during(tool.execute({}, _ctx()))
