"""Sprint 5b §A — bash tool tests."""

from __future__ import annotations

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_bash_tool
from aelix_coding_agent.tools.bash import (
    BashToolDetails,
    create_local_bash_operations,
)


async def _exec(tool, args, cwd="/tmp"):
    return await tool.execute(args, ToolExecutionContext(tool_call_id="t1"))


async def test_bash_runs_simple_command(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await _exec(tool, {"command": "echo hi"}, cwd=str(tmp_path))
    assert result.is_error is False
    assert "hi" in result.content[0].text
    assert isinstance(result.details, BashToolDetails)
    assert result.details.exit_code == 0


async def test_bash_missing_command():
    tool = create_bash_tool("/tmp")
    result = await _exec(tool, {})
    assert result.is_error is True


async def test_bash_empty_command():
    tool = create_bash_tool("/tmp")
    result = await _exec(tool, {"command": "   "})
    assert result.is_error is True


async def test_bash_cwd_is_not_dir(tmp_path):
    fake = tmp_path / "missing"
    tool = create_bash_tool(str(fake))
    result = await _exec(tool, {"command": "ls"})
    assert result.is_error is True


async def test_bash_nonzero_exit_marks_error(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await _exec(tool, {"command": "exit 1"})
    assert result.is_error is True
    assert result.details.exit_code == 1


async def test_bash_stdout_captured(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    result = await _exec(tool, {"command": "echo aelix-bash"})
    assert "aelix-bash" in result.content[0].text


async def test_bash_stderr_captured(tmp_path):
    tool = create_bash_tool(str(tmp_path))
    # bash sends stderr through subprocess.STDOUT capture path.
    result = await _exec(tool, {"command": "ls /this-path-must-not-exist-x"})
    assert result.is_error is True


async def test_bash_truncation_lines(tmp_path):
    tool = create_bash_tool(str(tmp_path), {"max_lines": 5})
    result = await _exec(tool, {"command": "seq 1 100"})
    assert result.details.truncation.truncated is True


async def test_bash_truncation_bytes(tmp_path):
    tool = create_bash_tool(str(tmp_path), {"max_bytes": 16})
    result = await _exec(tool, {"command": "echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})
    assert result.details.truncation.truncated is True


async def test_bash_operations_swap(tmp_path):
    """Pi parity: callers may inject a custom :class:`BashOperations`."""

    class _StubOps:
        async def exec(self, command, cwd, *, on_data, signal=None, timeout=None, env=None):
            on_data(b"stub-out\n")
            from aelix_coding_agent.tools.bash import ExecExitResult
            return ExecExitResult(exit_code=0)

    tool = create_bash_tool(str(tmp_path), {"operations": _StubOps()})
    result = await _exec(tool, {"command": "echo ignored"})
    assert "stub-out" in result.content[0].text
    assert result.details.exit_code == 0


async def test_bash_execution_mode_sequential():
    tool = create_bash_tool("/tmp")
    assert tool.execution_mode == "sequential"


async def test_bash_local_operations_factory():
    ops = create_local_bash_operations()
    assert ops is not None
