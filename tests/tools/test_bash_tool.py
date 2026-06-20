"""Sprint 5b §A — bash tool tests."""

from __future__ import annotations

from pathlib import Path

from aelix_ai.tools import ToolExecutionContext
from aelix_coding_agent.tools import create_bash_tool
from aelix_coding_agent.tools._truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES
from aelix_coding_agent.tools.bash import (
    BashToolDetails,
    ExecExitResult,
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


async def test_bash_nonzero_exit_appends_status(tmp_path):
    """Pi parity ``appendStatus`` — status appended after body w/ blank line."""

    tool = create_bash_tool(str(tmp_path))
    # ``echo`` emits a trailing newline, which is part of the captured body
    # (pi keeps it in ``snapshot.content``); appendStatus then adds ``\n\n``.
    result = await _exec(tool, {"command": "echo before; exit 3"})
    text = result.content[0].text
    assert text == "before\n\n\nCommand exited with code 3"


async def test_bash_nonzero_exit_status_only_when_empty_body(tmp_path):
    """Pi parity ``appendStatus`` empty-body branch — bare status line."""

    tool = create_bash_tool(str(tmp_path))
    result = await _exec(tool, {"command": "exit 2"})
    assert result.content[0].text == "Command exited with code 2"


async def test_bash_success_empty_body_is_no_output(tmp_path):
    """Pi parity ``formatOutput`` success ``emptyText = "(no output)"``."""

    tool = create_bash_tool(str(tmp_path))
    result = await _exec(tool, {"command": "true"})
    assert result.is_error is False
    assert result.content[0].text == "(no output)"


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


async def test_bash_defaults_are_2000_lines_50kb(tmp_path):
    """Pi parity caps: ``DEFAULT_MAX_LINES`` (2000) / ``DEFAULT_MAX_BYTES`` (50KB)."""

    assert DEFAULT_MAX_LINES == 2000
    assert DEFAULT_MAX_BYTES == 50 * 1024
    # 1500 lines / well under 50KB must NOT truncate at the raised defaults.
    tool = create_bash_tool(str(tmp_path))
    result = await _exec(tool, {"command": "seq 1 1500"})
    assert result.details.truncation.truncated is False
    assert result.details.full_output_path is None


async def test_bash_truncation_lines_notice_and_tempfile(tmp_path):
    """Pi parity ``formatOutput`` lines-branch notice + temp-file persistence."""

    tool = create_bash_tool(str(tmp_path), {"max_lines": 5})
    # ``printf`` with no trailing newline → exactly 100 lines (deterministic).
    result = await _exec(tool, {"command": "printf '%s\\n' $(seq 1 99); printf '100'"})
    text = result.content[0].text
    path = result.details.full_output_path
    assert path is not None
    assert (
        f"\n\n[Showing lines 96-100 of 100. Full output: {path}]" in text
    )
    # Full untruncated output is saved to <tmpdir>/pi-bash-<hex>.log.
    p = Path(path)
    assert p.name.startswith("pi-bash-")
    assert p.suffix == ".log"
    saved = p.read_text()
    assert saved.splitlines()[0] == "1"
    assert saved.splitlines()[99] == "100"


async def test_bash_truncation_bytes_partial_line_notice(tmp_path):
    """Pi parity ``lastLinePartial`` branch — single long line cut by bytes."""

    tool = create_bash_tool(str(tmp_path), {"max_bytes": 16})
    result = await _exec(tool, {"command": "printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"})
    text = result.content[0].text
    path = result.details.full_output_path
    assert path is not None
    assert "[Showing last " in text
    assert " of line " in text
    assert f"Full output: {path}]" in text


async def test_bash_tempfile_only_when_truncated(tmp_path):
    """Pi parity: temp file is created only when output is truncated."""

    tool = create_bash_tool(str(tmp_path))
    result = await _exec(tool, {"command": "echo small"})
    assert result.details.truncation.truncated is False
    assert result.details.full_output_path is None


async def test_bash_timeout_status(tmp_path):
    """Pi parity ``Command timed out after {timeout} seconds`` (exit_code None)."""

    class _TimeoutOps:
        async def exec(self, command, cwd, *, on_data, signal=None, timeout=None, env=None):
            return ExecExitResult(exit_code=None)

    tool = create_bash_tool(str(tmp_path), {"operations": _TimeoutOps()})
    result = await _exec(tool, {"command": "sleep 100", "timeout": 5})
    assert result.is_error is True
    assert result.content[0].text == "Command timed out after 5 seconds"


async def test_bash_abort_status(tmp_path):
    """Pi parity ``Command aborted`` — killed/None exit with no timeout set."""

    class _AbortOps:
        async def exec(self, command, cwd, *, on_data, signal=None, timeout=None, env=None):
            return ExecExitResult(exit_code=None)

    tool = create_bash_tool(str(tmp_path), {"operations": _AbortOps()})
    result = await _exec(tool, {"command": "sleep 100"})
    assert result.is_error is True
    assert result.content[0].text == "Command aborted"


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


async def test_bash_partial_line_notice_reports_last_line_bytes(tmp_path):
    # Pi parity ``getLastLineBytes()``: when a SINGLE final line exceeds the
    # byte cap, the "(line is X)" notice reports that LAST line's byte size —
    # NOT the whole-output total (which would wrongly add the preceding lines).
    tool = create_bash_tool(str(tmp_path), {"max_bytes": 100, "max_lines": 999})
    # 2 short leading lines + a 300-byte final line; only the last overflows.
    cmd = r"printf 'AAAA\nBBBB\n'; printf 'C%.0s' $(seq 1 300)"
    result = await _exec(tool, {"command": cmd}, cwd=str(tmp_path))
    text = result.content[0].text
    assert "of line 3" in text  # 3rd line is the partial one
    assert "(line is 300B)" in text  # last line only, not 310B (whole output)
    assert isinstance(result.details, BashToolDetails)
    assert result.details.truncation.last_line_partial is True
    assert result.details.full_output_path is not None


# --- P0 #3 HEAVY (ADR-0139): commandPrefix / spawnHook / shellPath ---


async def test_bash_command_prefix_prepended(tmp_path):
    """Pi parity ``bash.ts:284``: ``commandPrefix`` is prepended as
    ``${prefix}\n${command}`` so prefix shell setup is in scope."""

    tool = create_bash_tool(str(tmp_path), {"command_prefix": "PIPREFIX=hello"})
    result = await _exec(tool, {"command": "echo $PIPREFIX"}, cwd=str(tmp_path))
    assert result.is_error is False
    assert "hello" in result.content[0].text


async def test_bash_spawn_hook_rewrites_command(tmp_path):
    """Pi parity ``resolveSpawnContext``: ``spawn_hook`` can rewrite the
    command before it is spawned."""

    from aelix_coding_agent.tools.bash import BashSpawnContext

    def hook(ctx: BashSpawnContext) -> BashSpawnContext:
        ctx.command = "echo rewritten"
        return ctx

    tool = create_bash_tool(str(tmp_path), {"spawn_hook": hook})
    result = await _exec(tool, {"command": "echo original"}, cwd=str(tmp_path))
    assert "rewritten" in result.content[0].text
    assert "original" not in result.content[0].text


async def test_bash_spawn_hook_injects_env(tmp_path):
    """Pi parity: ``spawn_hook`` may mutate ``ctx.env`` in place; the spawned
    command sees the injected variable."""

    from aelix_coding_agent.tools.bash import BashSpawnContext

    def hook(ctx: BashSpawnContext) -> BashSpawnContext:
        ctx.env["INJECTED_BY_HOOK"] = "yes"
        return ctx

    tool = create_bash_tool(str(tmp_path), {"spawn_hook": hook})
    result = await _exec(
        tool, {"command": "echo $INJECTED_BY_HOOK"}, cwd=str(tmp_path)
    )
    assert "yes" in result.content[0].text


async def test_bash_spawn_hook_receives_shell_env_path(tmp_path):
    """The base spawn context env carries the bin dir on PATH (getShellEnv)."""

    from aelix_coding_agent.cli.config import get_bin_dir
    from aelix_coding_agent.tools.bash import BashSpawnContext

    seen = {}

    def hook(ctx: BashSpawnContext) -> BashSpawnContext:
        seen["path"] = ctx.env.get("PATH", "")
        return ctx

    tool = create_bash_tool(str(tmp_path), {"spawn_hook": hook})
    await _exec(tool, {"command": "true"}, cwd=str(tmp_path))
    assert get_bin_dir() in seen["path"].split(":")


async def test_bash_shell_path_missing_raises(tmp_path):
    """Pi parity ``getShellConfig``: a missing custom shell path raises with
    Pi's ``Custom shell path not found`` message."""

    import pytest

    tool = create_bash_tool(
        str(tmp_path), {"shell_path": "/nonexistent/shell/bin/zzz"}
    )
    with pytest.raises(ValueError, match="Custom shell path not found"):
        await _exec(tool, {"command": "echo hi"}, cwd=str(tmp_path))


async def test_bash_shell_path_valid_used(tmp_path):
    """A valid explicit shell path is honored."""

    import shutil

    sh = shutil.which("bash") or "/bin/bash"
    if not Path(sh).exists():
        import pytest

        pytest.skip("no bash available")
    tool = create_bash_tool(str(tmp_path), {"shell_path": sh})
    result = await _exec(tool, {"command": "echo viashell"}, cwd=str(tmp_path))
    assert "viashell" in result.content[0].text
