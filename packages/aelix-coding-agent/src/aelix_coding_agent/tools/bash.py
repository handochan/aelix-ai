"""bash tool — Pi parity ``coding-agent/src/core/tools/bash.ts``.

Sequential execution_mode. ``BashOperations`` is the swap surface for
remote execution (e.g. SSH) — local default via
:func:`create_local_bash_operations`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import shutil
import signal as _signal
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aelix_agent_core.types import AgentTool
from aelix_ai.messages import TextContent
from aelix_ai.tools import ToolExecutionContext, ToolResult

from aelix_coding_agent.tools._truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationInfo,
    format_size,
    truncate_tail,
)
from aelix_coding_agent.util.shell_env import get_shell_env

# Pi parity defaults (``OutputAccumulator``/``truncate.ts``): 2000 lines / 50KB.
_DEFAULT_MAX_LINES = DEFAULT_MAX_LINES
_DEFAULT_MAX_BYTES = DEFAULT_MAX_BYTES

# Pi parity ``OutputAccumulator({ tempFilePrefix: "pi-bash" })`` →
# ``<tmpdir>/pi-bash-<hex>.log`` where ``<hex> = randomBytes(8).toString("hex")``.
_TEMP_FILE_PREFIX = "pi-bash"


def _resolve_shell(env: dict[str, str], shell_path: str | None = None) -> str:
    """Pi parity ``getShellConfig()`` resolution chain (``utils/shell.ts``).

    Resolution order:

    1. An explicit ``shell_path`` (Pi parity ``getShellConfig(customShellPath)``
       — validated with :meth:`Path.exists`; raises ``ValueError`` with Pi's
       ``Custom shell path not found: {path}`` message when absent).
    2. ``$SHELL`` (Aelix-additive — documented ``$SHELL``-first divergence so
       user-configured shells win when the env exports one).
    3. ``/bin/bash`` → ``bash`` on ``PATH`` → ``/bin/sh`` (Pi's Unix chain).
    """

    if shell_path:
        if Path(shell_path).exists():
            return shell_path
        raise ValueError(f"Custom shell path not found: {shell_path}")
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
    """Local default — subprocess.Popen w/ session group kill on abort.

    Pi parity ``createLocalBashOperations({ shellPath })`` — an explicit
    ``shell_path`` (from settings) is validated and used in preference to the
    resolution chain.
    """

    def __init__(self, shell_path: str | None = None) -> None:
        self._shell_path = shell_path

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
        # Pi parity ``env ?? getShellEnv()`` — when the caller supplies an env
        # (the bash tool always does, via the spawn-context) use it verbatim;
        # otherwise fall back to the shell env (process env + bin dir on PATH)
        # so a bare ``operations.exec`` still resolves auto-downloaded tools.
        env_dict = dict(env) if env is not None else get_shell_env()
        # Pi parity: ``getShellConfig(shellPath)`` — explicit shell path →
        # $SHELL → /bin/bash → bash-on-PATH → /bin/sh. See ``_resolve_shell``.
        shell = _resolve_shell(env_dict, self._shell_path)
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

        # Track whether the abort signal (not timeout) triggered a kill so we
        # can return exit_code=None parity with the timeout-kill path.
        _signal_aborted = False

        async def _watch_signal() -> None:
            """Kill the process group when the abort signal fires."""
            nonlocal _signal_aborted
            assert signal is not None  # watcher only started when signal is set
            await signal.wait()
            _signal_aborted = True
            _kill_group(proc.pid)

        drain_task = asyncio.create_task(_drain())
        watcher_task: asyncio.Task[None] | None = None
        if signal is not None and hasattr(signal, "wait"):
            watcher_task = asyncio.create_task(_watch_signal())
        try:
            try:
                exit_code = await _wait()
            except asyncio.CancelledError:
                # Esc-path: the harness cancelled our turn task.  Kill
                # the child group so it does not become an orphan, then
                # re-raise after the finally drain so the caller sees the
                # cancellation.
                _kill_group(proc.pid)
                exit_code = None
                raise
        finally:
            if watcher_task is not None:
                watcher_task.cancel()
                # Swallow the watcher's own cancellation (and any teardown
                # error) — the outer turn cancellation is captured and
                # re-raised at the ``except asyncio.CancelledError`` above.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watcher_task
            await drain_task
        # Pi parity: signal-kill and timeout-kill both report exit_code=None.
        if _signal_aborted:
            exit_code = None
        return ExecExitResult(exit_code=exit_code)


def _kill_group(pid: int) -> None:
    """Send SIGKILL to the process group (Pi parity detached spawn cleanup)."""

    try:
        os.killpg(os.getpgid(pid), _signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return


def create_local_bash_operations(
    shell_path: str | None = None,
) -> BashOperations:
    """Pi parity ``createLocalBashOperations(options?: { shellPath })``."""

    return _LocalBashOperations(shell_path)


@dataclass
class BashSpawnContext:
    """Pi parity ``BashSpawnContext`` (``bash.ts:129-133``).

    The ``(command, cwd, env)`` triple a :data:`BashSpawnHook` may rewrite
    before the command is spawned. Mutable (non-frozen) so a hook can adjust
    ``env`` in place and return the same instance, mirroring Pi's mutable
    object ergonomics.
    """

    command: str
    cwd: str
    env: dict[str, str]


# Pi parity ``BashSpawnHook`` (``bash.ts:135``): ``(context) => context``.
BashSpawnHook = Callable[[BashSpawnContext], BashSpawnContext]


def _resolve_spawn_context(
    command: str, cwd: str, spawn_hook: BashSpawnHook | None
) -> BashSpawnContext:
    """Pi parity ``resolveSpawnContext`` (``bash.ts:137-140``).

    Builds the base context with a fresh :func:`get_shell_env` env (Pi
    ``{ ...getShellEnv() }``) and applies ``spawn_hook`` when provided.
    """

    base = BashSpawnContext(command=command, cwd=cwd, env=get_shell_env())
    return spawn_hook(base) if spawn_hook else base


def _write_full_output(raw: str) -> str:
    """Pi parity ``OutputAccumulator.ensureTempFile`` — persist the FULL,
    untruncated raw output to ``<tmpdir>/pi-bash-<hex>.log``.

    ``<hex>`` is :func:`secrets.token_hex(8)` (16 lowercase hex chars), matching
    pi's ``randomBytes(8).toString("hex")``. Called only when truncated.
    """

    hex_id = secrets.token_hex(8)
    path = Path(tempfile.gettempdir()) / f"{_TEMP_FILE_PREFIX}-{hex_id}.log"
    path.write_text(raw, encoding="utf-8")
    return str(path)


def _format_truncation_notice(
    info: TruncationInfo,
    *,
    full_output_path: str,
    max_bytes: int,
    last_line_bytes: int,
) -> str:
    """Pi parity ``formatOutput`` notice (``bash.ts``) — the bracketed
    ``[Showing …. Full output: <path>]`` line appended to truncated output.

    Maps aelix :class:`TruncationInfo` onto pi's ``TruncationResult`` fields:
    ``totalLines = original_lines``, ``outputLines = kept_lines``,
    ``outputBytes = kept_bytes``. The partial-line branch (pi's ``lastLinePartial``
    tail edge case, when a single line exceeds the byte cap) reports the FULL
    byte size of that last line via ``last_line_bytes`` — pi's
    ``getLastLineBytes()`` — NOT the whole-output byte total.
    """

    total_lines = info.original_lines
    output_lines = info.kept_lines
    start_line = total_lines - output_lines + 1
    end_line = total_lines
    if info.last_line_partial:
        return (
            f"\n\n[Showing last {format_size(info.kept_bytes)} of line {end_line} "
            f"(line is {format_size(last_line_bytes)}). "
            f"Full output: {full_output_path}]"
        )
    if info.truncated_by == "lines":
        return (
            f"\n\n[Showing lines {start_line}-{end_line} of {total_lines}. "
            f"Full output: {full_output_path}]"
        )
    return (
        f"\n\n[Showing lines {start_line}-{end_line} of {total_lines} "
        f"({format_size(max_bytes)} limit). Full output: {full_output_path}]"
    )


def _append_status(text: str, status: str) -> str:
    """Pi parity ``appendStatus`` (``bash.ts``):
    ``${text ? `${text}\\n\\n` : ""}${status}``."""

    return f"{text}\n\n{status}" if text else status


# Pi parity: ``createBashToolDefinition`` (``bash.ts``) parameter schema +
# per-field descriptions. Output is truncated at the pi-parity caps (2000 lines
# / 50KB via DEFAULT_MAX_LINES/DEFAULT_MAX_BYTES) and the full untruncated output
# is persisted to a temp file when truncated (see ``_write_full_output`` +
# ``_format_truncation_notice``).
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
    # Pi parity ``BashToolOptions`` — ``operations`` / ``shellPath`` /
    # ``commandPrefix`` / ``spawnHook``.
    operations: BashOperations = opts.get(
        "operations"
    ) or create_local_bash_operations(opts.get("shell_path"))
    command_prefix: str | None = opts.get("command_prefix")
    spawn_hook: BashSpawnHook | None = opts.get("spawn_hook")
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
        # Pi parity ``bash.ts:284-285``: prepend ``commandPrefix`` (separated by
        # a newline), then resolve the spawn context (base env = getShellEnv,
        # optionally rewritten by ``spawnHook``).
        resolved_command = (
            f"{command_prefix}\n{command}" if command_prefix else command
        )
        spawn_context = _resolve_spawn_context(resolved_command, cwd, spawn_hook)
        if not Path(spawn_context.cwd).is_dir():
            return ToolResult(
                content=[
                    TextContent(
                        text=f"bash: cwd {spawn_context.cwd!r} is not a directory"
                    )
                ],
                is_error=True,
            )
        chunks: list[bytes] = []
        exit_result = await operations.exec(
            spawn_context.command,
            spawn_context.cwd,
            on_data=chunks.append,
            signal=getattr(ctx, "signal", None),
            timeout=timeout,
            env=spawn_context.env,
        )
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        body, info = truncate_tail(
            raw, max_lines=max_lines, max_bytes=max_bytes
        )

        # Pi parity ``OutputAccumulator.snapshot({ persistIfTruncated: true })`` —
        # write the FULL untruncated raw output to a temp file when truncated and
        # append the ``[Showing …. Full output: <path>]`` notice (formatOutput).
        full_output_path: str | None = None
        if info.truncated:
            full_output_path = _write_full_output(raw)
            # Pi parity ``getLastLineBytes()`` — the FULL byte length of the
            # final raw line (used only by the partial-line notice branch).
            last_line_bytes = len(raw.rsplit("\n", 1)[-1].encode("utf-8"))
            body += _format_truncation_notice(
                info,
                full_output_path=full_output_path,
                max_bytes=max_bytes,
                last_line_bytes=last_line_bytes,
            )

        details = BashToolDetails(
            exit_code=exit_result.exit_code,
            truncation=info,
            full_output_path=full_output_path,
        )

        exit_code = exit_result.exit_code
        is_error = exit_code is None or exit_code != 0
        if not is_error:
            # Pi parity ``formatOutput`` success path: ``emptyText = "(no output)"``.
            text = body or "(no output)"
            return ToolResult(
                content=[TextContent(text=text)],
                details=details,
                is_error=False,
            )

        # Pi parity error paths throw ``appendStatus(text, status)`` where the
        # catch-path ``formatOutput`` uses an empty ``emptyText`` (so an empty
        # body yields the bare status line). ``exit_code is None`` maps to the
        # timeout status when a timeout was set (``_wait`` kills the group on
        # ``TimeoutExpired``), else to the abort status.
        if exit_code is None:
            if timeout is not None:
                # Pi renders the verbatim user-supplied seconds (string split);
                # drop a trailing ``.0`` so an integer ``5`` stays ``5``.
                timeout_secs = int(timeout) if timeout == int(timeout) else timeout
                status = f"Command timed out after {timeout_secs} seconds"
            else:
                status = "Command aborted"
        else:
            status = f"Command exited with code {exit_code}"
        return ToolResult(
            content=[TextContent(text=_append_status(body, status))],
            details=details,
            is_error=True,
        )

    return AgentTool(
        name="bash",
        description=(
            "Execute a bash command in the current working directory. Returns "
            "stdout and stderr. Output is truncated to last 2000 lines or 50KB "
            "(whichever is hit first). If truncated, full output is saved to a "
            "temp file. Optionally provide a timeout in seconds."
        ),
        parameters=_BASH_PARAMETERS_SCHEMA,
        execute=execute,
        execution_mode="sequential",
    )


__all__ = [
    "BashOperations",
    "BashSpawnContext",
    "BashSpawnHook",
    "BashToolDetails",
    "ExecExitResult",
    "create_bash_tool",
    "create_local_bash_operations",
]
