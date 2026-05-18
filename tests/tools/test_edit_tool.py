"""Sprint 5b §A — edit tool tests."""

from __future__ import annotations

import asyncio

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_edit_tool


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_edit_single_change(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("hello world\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"old_text": "world", "new_text": "aelix"}]},
    )
    assert result.is_error is False
    assert f.read_text() == "hello aelix\n"


async def test_edit_multiple_changes(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("aaa bbb ccc")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {
            "path": "e.txt",
            "edits": [
                {"old_text": "aaa", "new_text": "X"},
                {"old_text": "ccc", "new_text": "Y"},
            ],
        },
    )
    assert result.is_error is False
    assert f.read_text() == "X bbb Y"


async def test_edit_old_text_not_unique(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("dup dup")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"old_text": "dup", "new_text": "x"}]},
    )
    assert result.is_error is True


async def test_edit_old_text_not_found(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("present")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"old_text": "absent", "new_text": "x"}]},
    )
    assert result.is_error is True


async def test_edit_missing_path():
    tool = create_edit_tool("/tmp")
    result = await _exec(tool, {"edits": [{"old_text": "x", "new_text": "y"}]})
    assert result.is_error is True


async def test_edit_missing_edits(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("x")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(tool, {"path": "e.txt"})
    assert result.is_error is True


async def test_edit_no_change_produces_empty_diff(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("same\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "e.txt", "edits": [{"old_text": "same", "new_text": "same"}]},
    )
    assert result.is_error is False


async def test_edit_preserves_crlf_line_endings(tmp_path):
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"line1\r\nline2\r\n")
    tool = create_edit_tool(str(tmp_path))
    result = await _exec(
        tool,
        {"path": "crlf.txt", "edits": [{"old_text": "line1", "new_text": "L1"}]},
    )
    assert result.is_error is False
    assert b"\r\n" in f.read_bytes()


async def test_edit_concurrent_serialised_by_file_lock(tmp_path):
    f = tmp_path / "lock.txt"
    f.write_text("aaa bbb")
    tool = create_edit_tool(str(tmp_path))

    async def edit1():
        return await _exec(
            tool,
            {"path": "lock.txt", "edits": [{"old_text": "aaa", "new_text": "1"}]},
        )

    async def edit2():
        return await _exec(
            tool,
            {"path": "lock.txt", "edits": [{"old_text": "bbb", "new_text": "2"}]},
        )

    r1, r2 = await asyncio.gather(edit1(), edit2())
    assert r1.is_error is False
    assert r2.is_error is False
    # Both edits should apply via the per-file mutation queue.
    assert f.read_text() == "1 2"


async def test_edit_execution_mode_sequential():
    tool = create_edit_tool("/tmp")
    assert tool.execution_mode == "sequential"
