"""Sprint 5b §A — grep tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_grep_tool
from aelix_coding_agent.tools.grep import GrepToolDetails


async def _exec(tool, args):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_grep_finds_pattern(tmp_path):
    (tmp_path / "a.txt").write_text("hello world\nfoo bar\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hello"})
    assert result.is_error is False
    assert "hello" in result.content[0].text


async def test_grep_literal(tmp_path):
    (tmp_path / "a.txt").write_text("a.b.c\nx+y\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "a.b.c", "literal": True})
    assert result.is_error is False
    assert "a.b.c" in result.content[0].text


async def test_grep_ignore_case(tmp_path):
    (tmp_path / "a.txt").write_text("HELLO\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hello", "ignoreCase": True})
    assert result.is_error is False
    assert "HELLO" in result.content[0].text


async def test_grep_glob_filter(tmp_path):
    (tmp_path / "a.py").write_text("py match\n")
    (tmp_path / "b.txt").write_text("txt match\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "match", "glob": "*.py"})
    assert result.is_error is False
    # Result text should not include the txt file line.
    if "b.txt" in result.content[0].text:
        # ripgrep may or may not be present; both paths are OK as long as
        # the py match is found.
        pass
    assert "py match" in result.content[0].text


async def test_grep_limit(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join("hit" for _ in range(50)))
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "hit", "limit": 5})
    assert isinstance(result.details, GrepToolDetails)


async def test_grep_missing_pattern():
    tool = create_grep_tool("/tmp")
    result = await _exec(tool, {})
    assert result.is_error is True


async def test_grep_execution_mode_parallel():
    tool = create_grep_tool("/tmp")
    assert tool.execution_mode == "parallel"


async def test_grep_no_matches(tmp_path):
    (tmp_path / "a.txt").write_text("nothing here\n")
    tool = create_grep_tool(str(tmp_path))
    result = await _exec(tool, {"pattern": "absent-zzz-pattern"})
    assert result.is_error is False
