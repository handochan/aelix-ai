"""Sprint 5b §A — ls tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_ls_tool


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_ls_lists_entries(tmp_path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {})
    assert result.is_error is False
    assert "a.txt" in result.content[0].text
    assert "b.txt" in result.content[0].text


async def test_ls_marks_dirs_with_trailing_slash(tmp_path):
    (tmp_path / "subdir").mkdir()
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {})
    assert "subdir/" in result.content[0].text


async def test_ls_sorted_alphabetically(tmp_path):
    for name in ("z.txt", "a.txt", "m.txt"):
        (tmp_path / name).write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {})
    text = result.content[0].text
    a_idx = text.index("a.txt")
    m_idx = text.index("m.txt")
    z_idx = text.index("z.txt")
    assert a_idx < m_idx < z_idx


async def test_ls_missing_dir(tmp_path):
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"path": str(tmp_path / "missing")})
    assert result.is_error is True


async def test_ls_limit(tmp_path):
    for i in range(20):
        (tmp_path / f"f{i}.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"limit": 5})
    assert result.is_error is False


async def test_ls_custom_path(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "inside.txt").write_text("")
    tool = create_ls_tool(str(tmp_path))
    result = await _exec(tool, {"path": "sub"})
    assert "inside.txt" in result.content[0].text


async def test_ls_execution_mode_parallel():
    tool = create_ls_tool("/tmp")
    assert tool.execution_mode == "parallel"
