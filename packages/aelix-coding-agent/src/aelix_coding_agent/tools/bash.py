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

# Issue #11 — Aelix-additive default + max bash timeout (pi has NEITHER, by
# design — pi assumes a capable model that always supplies ``timeout`` and an
# interactive Esc). Aelix serves modest local models too: one that OMITS
# ``timeout`` would otherwise hang the agent loop forever. So a default is
# armed ONLY when the model omits ``timeout`` (or passes ≤0); an EXPLICIT value
# is always honored, clamped to the max cap. Both are overridable per-tool via
# ``options`` (wired from env vars in ``entry.py``). Setting either to 0
# disables that knob: ``default_timeout=0`` restores pi's unbounded behavior;
# ``max_timeout=0`` lifts the cap so a model can request an arbitrarily long
# window (full CI, hour-plus compiles).
_DEFAULT_TIMEOUT = 600.0  # 10 min — generous enough for most builds/installs/tests
_MAX_TIMEOUT = 3600.0  # 1 hour hard cap on an explicit model-supplied value


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
    # Issue #11 — distinguishes a TIMEOUT-kill from an ABORT/signal-kill (both
    # yield ``exit_code=None``). Pi's ``ExecExitResult`` carries only
    # ``exit_code`` because pi has no default timeout — a ``None`` exit was
    # unambiguously an abort. Once a default timeout is ALWAYS armed (so the
    # model omitting ``timeout`` no longer means "unbounded"), the status
    # formatter can no longer infer timeout-vs-abort from "was a timeout set?";
    # this flag is the authoritative signal. Defaults ``False`` so existing
    # custom :class:`BashOperations` impls keep working (their kills read as
    # aborts unless they opt in).
    timed_out: bool = False


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

        # Track whether the timeout (not the abort signal) triggered the kill,
        # so the bash tool can label the result correctly (issue #11).
        _timed_out = False

        async def _wait() -> int | None:
            nonlocal _timed_out
            try:
                return await asyncio.to_thread(proc.wait, timeout)
            except subprocess.TimeoutExpired:
                _timed_out = True
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
        # Issue #11: a signal-abort takes precedence over a timeout label (if
        # both somehow fired, the user's abort is the operative cause).
        return ExecExitResult(
            exit_code=exit_code, timed_out=_timed_out and not _signal_aborted
        )


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


def _fmt_secs(value: float) -> str:
    """Render a seconds value without a trailing ``.0`` (``600.0`` → ``600``)."""

    return str(int(value)) if value == int(value) else str(value)


def _resolve_timeout_knob(value: Any, fallback: float) -> float:
    """Resolve a configured timeout knob (issue #11).

    ``None`` (unset) → the module ``fallback``; a non-positive or non-numeric
    value → ``0.0`` (the knob is DISABLED — see :data:`_DEFAULT_TIMEOUT` /
    :data:`_MAX_TIMEOUT` docs for what disabling each means).
    """

    if value is None:
        return fallback
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return fallback
    return resolved if resolved > 0 else 0.0


def _resolve_call_timeout(
    timeout_arg: Any, default_timeout: float, max_timeout: float
) -> tuple[float | None, bool]:
    """Pick the effective timeout for one bash call (issue #11).

    Returns ``(timeout, was_clamped)``. An explicit positive ``timeout_arg`` is
    honored, clamped to ``max_timeout`` when that cap is enabled (``was_clamped``
    is then ``True`` so the status message can be honest rather than telling the
    model to "retry with a larger timeout" up to a value it already exceeded).
    Otherwise the ``default_timeout`` safety net applies; when the default is
    disabled (0) the command runs unbounded (pi behavior).
    """

    try:
        requested = float(timeout_arg) if timeout_arg is not None else None
    except (TypeError, ValueError):
        requested = None
    if requested is not None and requested > 0:
        if max_timeout > 0 and requested > max_timeout:
            return max_timeout, True
        return requested, False
    return (default_timeout if default_timeout > 0 else None), False


# Pi parity: ``createBashToolDefinition`` (``bash.ts``) parameter schema +
# per-field descriptions. Output is truncated at the pi-parity caps (2000 lines
# / 50KB via DEFAULT_MAX_LINES/DEFAULT_MAX_BYTES) and the full untruncated output
# is persisted to a temp file when truncated (see ``_write_full_output`` +
# ``_format_truncation_notice``). The ``timeout`` description is built per-tool
# (issue #11) so it states the resolved default + cap.
def _build_bash_parameters(default_timeout: float, max_timeout: float) -> dict[str, Any]:
    if default_timeout > 0:
        cap = f", capped at {_fmt_secs(max_timeout)}s" if max_timeout > 0 else ""
        timeout_desc = (
            f"Timeout in seconds. When omitted, defaults to "
            f"{_fmt_secs(default_timeout)}s{cap}. Pass a larger value for "
            "long-running commands (builds, installs, test suites)."
        )
    else:
        timeout_desc = "Timeout in seconds (optional, no default timeout)."
    return {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Bash command to execute",
            },
            "timeout": {
                "type": "number",
                "description": timeout_desc,
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
    # Issue #11 — resolve the per-tool default/max timeout knobs (0 disables).
    default_timeout = _resolve_timeout_knob(
        opts.get("default_timeout"), _DEFAULT_TIMEOUT
    )
    max_timeout = _resolve_timeout_knob(opts.get("max_timeout"), _MAX_TIMEOUT)
    parameters = _build_bash_parameters(default_timeout, max_timeout)

    async def execute(
        args: dict[str, Any], ctx: ToolExecutionContext
    ) -> ToolResult:
        command = args.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(
                content=[TextContent(text="bash: missing 'command'")],
                is_error=True,
            )
        # Issue #11: arm the default timeout when the model omits ``timeout``
        # (or passes ≤0); honor an explicit value, clamped to the max cap.
        timeout, timeout_clamped = _resolve_call_timeout(
            args.get("timeout"), default_timeout, max_timeout
        )
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
        # body yields the bare status line). ``exit_code is None`` is a kill;
        # issue #11 uses the authoritative ``timed_out`` flag (NOT "was a
        # timeout set?", which is now always true) to label timeout vs abort,
        # and appends actionable retry guidance so a model whose long command
        # was cut can re-run it with a larger ``timeout``.
        if exit_code is None:
            if exit_result.timed_out and timeout is not None:
                if timeout_clamped:
                    # The model asked for MORE than the cap; "retry larger" would
                    # be non-actionable. Tell it the cap was applied + how to lift.
                    retry = (
                        f" The requested timeout exceeded the "
                        f"{_fmt_secs(max_timeout)}s cap; raise "
                        "AELIX_BASH_MAX_TIMEOUT to allow longer."
                    )
                elif max_timeout > 0:
                    retry = (
                        " If this command needs longer, retry with a larger "
                        f"'timeout' (up to {_fmt_secs(max_timeout)} seconds)."
                    )
                else:
                    retry = (
                        " If this command needs longer, retry with a larger "
                        "'timeout'."
                    )
                status = (
                    f"Command timed out after {_fmt_secs(timeout)} seconds.{retry}"
                )
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
            "temp file. Provide a timeout in seconds for long-running commands "
            "(see the timeout parameter)."
        ),
        parameters=parameters,
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
