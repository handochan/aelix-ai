"""bash tool — Pi parity ``coding-agent/src/core/tools/bash.ts``.

Sequential execution_mode. ``BashOperations`` is the swap surface for
remote execution (e.g. SSH) — local default via
:func:`create_local_bash_operations`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal as _signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._truncate import (
    TruncationInfo,
    truncate_tail,
)

# Pi parity defaults: ``DEFAULT_MAX_LINES = 256``, ``DEFAULT_MAX_BYTES = 32_768``.
_DEFAULT_MAX_LINES = 256
_DEFAULT_MAX_BYTES = 32 * 1024


def _resolve_shell(env: dict[str, str]) -> str:
    """Pi parity ``getShellConfig()`` resolution chain (``utils/shell.ts``).

    W4 MAJOR-4 fix: Pi tries /bin/bash, then bash on PATH, then falls back
    to sh. We additionally honor ``$SHELL`` first so user-configured shells
    (zsh, fish-via-bash, etc.) win when the env exports one — matches Pi's
    spirit of "respect the user's shell" while preserving the bash-first
    invariant that Pi's bash.ts and shellrc handling assume.
    """

    shell = env.get("SHELL")
    if shell:
        return shell
    if Path("/bin/bash").exists():
        return "/bin/bash"
    bash_on_path = shutil.which("bash")
    if bash_on_path:
        return bash_on_path
    return "/bin/sh"


@dataclass(frozen=True)
class ExecExitResult:
    """Pi parity ``ExecExitResult`` (``bash.ts:30-32``)."""

    exit_code: int | None  # None when killed


@dataclass(frozen=True)
class BashToolDetails:
    """Pi parity ``BashToolDetails``."""

    exit_code: int | None
    truncation: TruncationInfo
    full_output_path: str | None = None


class BashOperations(Protocol):
    """Pi parity ``BashOperations`` Protocol — swap surface for SSH/remote."""

    async def exec(
        self,
        command: str,
        cwd: str,
        *,
        on_data: Callable[[bytes], None],
        signal: Any | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecExitResult: ...


class _LocalBashOperations:
    """Local default — subprocess.Popen w/ session group kill on abort."""

    async def exec(
        self,
        command: str,
        cwd: str,
        *,
        on_data: Callable[[bytes], None],
        signal: Any | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecExitResult:
        env_dict = dict(os.environ)
        if env is not None:
            env_dict.update(env)
        # Pi parity: ``getShellConfig()`` — $SHELL → /bin/bash → bash-on-PATH
        # → /bin/sh. See ``_resolve_shell`` for the full chain.
        shell = _resolve_shell(env_dict)
        try:
            proc = subprocess.Popen(  # noqa: S603
                [shell, "-c", command],
                cwd=cwd,
                env=env_dict,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            on_data(f"[bash] failed to spawn: {exc}\n".encode())
            return ExecExitResult(exit_code=127)

        async def _drain() -> None:
            assert proc.stdout is not None
            while True:
                chunk = await asyncio.to_thread(proc.stdout.read, 4096)
                if not chunk:
                    break
                on_data(chunk)

        async def _wait() -> int | None:
            try:
                return await asyncio.to_thread(proc.wait, timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc.pid)
                return None

        drain_task = asyncio.create_task(_drain())
        try:
            exit_code = await _wait()
        finally:
            await drain_task
        return ExecExitResult(exit_code=exit_code)


def _kill_group(pid: int) -> None:
    """Send SIGKILL to the process group (Pi parity detached spawn cleanup)."""

    try:
        os.killpg(os.getpgid(pid), _signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def create_local_bash_operations() -> BashOperations:
    """Pi parity ``createLocalBashOperations()``."""

    return _LocalBashOperations()


# Pi parity: ``createBashToolDefinition`` (``bash.ts``) parameter schema +
# per-field descriptions. The top-level description states Aelix's ACTUAL caps
# (256 lines / 32KB, no temp-file recovery); Pi's 2000/50KB cap + temp-file
# save are a P0 #3 behavior gap tracked for follow-up.
_BASH_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Bash command to execute",
        },
        "timeout": {
            "type": "number",
            "description": "Timeout in seconds (optional, no default timeout)",
        },
    },
    "required": ["command"],
}


def create_bash_tool(
    cwd: str, options: dict | None = None
) -> AgentTool:
    """Pi parity ``createBashToolDefinition`` (``bash.ts:264-440``)."""

    opts = options or {}
    operations: BashOperations = opts.get("operations") or create_local_bash_operations()
    max_lines: int = int(opts.get("max_lines", _DEFAULT_MAX_LINES))
    max_bytes: int = int(opts.get("max_bytes", _DEFAULT_MAX_BYTES))

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        command = args.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(
                content=[TextContent(text="bash: missing 'command'")],
                is_error=True,
            )
        timeout_arg = args.get("timeout")
        timeout: float | None = float(timeout_arg) if timeout_arg else None
        if not Path(cwd).is_dir():
            return ToolResult(
                content=[TextContent(text=f"bash: cwd {cwd!r} is not a directory")],
                is_error=True,
            )
        chunks: list[bytes] = []
        exit_result = await operations.exec(
            command,
            cwd,
            on_data=chunks.append,
            signal=getattr(ctx, "signal", None),
            timeout=timeout,
        )
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        body, info = truncate_tail(
            raw, max_lines=max_lines, max_bytes=max_bytes
        )
        details = BashToolDetails(
            exit_code=exit_result.exit_code,
            truncation=info,
        )
        return ToolResult(
            content=[TextContent(text=body)],
            details=details,
            is_error=(exit_result.exit_code is None or exit_result.exit_code != 0),
        )

    return AgentTool(
        name="bash",
        description=(
            "Execute a bash command in the current working directory. Returns "
            "combined stdout and stderr. Output is truncated to the last 256 "
            "lines or 32KB (whichever is hit first). Optionally provide a "
            "timeout in seconds."
        ),
        parameters=_BASH_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="sequential",
    )


__all__ = [
    "BashOperations",
    "BashToolDetails",
    "ExecExitResult",
    "create_bash_tool",
    "create_local_bash_operations",
]
