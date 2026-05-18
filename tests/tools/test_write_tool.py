"""Sprint 5b §A — write tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_write_tool


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_write_new_file(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "new.txt", "content": "hello"})
    assert result.is_error is False
    assert (tmp_path / "new.txt").read_text() == "hello"


async def test_write_creates_parent_dirs(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "a/b/c.txt", "content": "deep"})
    assert result.is_error is False
    assert (tmp_path / "a" / "b" / "c.txt").read_text() == "deep"


async def test_write_overwrites_existing(tmp_path):
    f = tmp_path / "w.txt"
    f.write_text("old")
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "w.txt", "content": "new"})
    assert result.is_error is False
    assert f.read_text() == "new"


async def test_write_missing_path():
    tool = create_write_tool("/tmp")
    result = await _exec(tool, {"content": "x"})
    assert result.is_error is True


async def test_write_missing_content(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "x.txt"})
    assert result.is_error is True


async def test_write_byte_count_reported(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "b.txt", "content": "abc"})
    assert "3" in result.content[0].text


async def test_write_utf8_round_trip(tmp_path):
    tool = create_write_tool(str(tmp_path))
    result = await _exec(tool, {"path": "u.txt", "content": "héllo αβ"})
    assert result.is_error is False
    assert (tmp_path / "u.txt").read_text(encoding="utf-8") == "héllo αβ"


async def test_write_execution_mode_sequential():
    tool = create_write_tool("/tmp")
    assert tool.execution_mode == "sequential"
